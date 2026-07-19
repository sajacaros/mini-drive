"""권한 인지 검색 (Permission-aware Retrieval, PRD 3.7.3, Phase 7-2).

RAG 는 접근 제어를 기본 제공하지 않는다 — 벡터 DB 에 데이터가 있으면 권한 없는 사용자의
질문에도 답이 나갈 수 있다. 이를 **2단계 필터**로 차단한다.

  ① 사전 필터(coarse): 후보 파일 집합 = `내 소유 파일 ∪ 내 활성 그룹에 부여된 서브트리`
     (read 이상). 이 집합 조건을 pgvector 검색 SQL 에 **한 쿼리로** 결합해, 후보 밖 청크는
     스캔 자체가 되지 않게 한다(recursive CTE — permissions.py 상속 판정과 동일 철학).
  ② 사후 검증(exact): top-k 결과의 파일별로 기존 단일 관문 `get_access_level` 을 재검증한다
     (상속 재정의·expires_at 만료·회수 직후 세대 캐시 무효화까지 정확 반영). 탈락 청크는 폐기.

검색은 **항상 요청 사용자 본인 자격**으로 수행한다. role 을 보지 않으므로 admin 도 예외가 없다
(get_access_level 이 소유/그룹 권한만 본다). 사용자 간 공유 캐시는 만들지 않는다(권한 우회 방지).
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import bindparam, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.logging import get_logger
from app.models import File
from app.models.user import User
from app.services.groups import get_user_group_ids
from app.services.permissions import _get_wiki_root_folder_id, get_access_level

_log = get_logger("app.retrieval")

# 사후 검증 탈락(상속 재정의·만료·회수)을 흡수하도록 coarse 단계는 최종 k 보다 넉넉히 뽑는다.
_OVERSAMPLE = 4
_OVERSAMPLE_MIN_MARGIN = 10


@dataclass(frozen=True)
class RetrievedChunk:
    """검색 통과 청크 하나. distance 는 코사인 거리(작을수록 유사)."""

    chunk_id: int
    file_id: int
    file_name: str
    version: int
    content: str
    distance: float


# 스페이스 범위 제한(선택). 챗 세션이 위키 스페이스로 범위 지정되면 후보를 그 스페이스 파일
# 집합(루트 서브트리 + 등록 소스 서브트리)으로 **추가 교집합**한다(PRD 3.7.5). 권한 사전 필터는
# 그대로 유지되므로 범위 지정이 권한을 넓히지 않는다(교집합일 뿐).
_SPACE_RESTRICT = "AND c.file_id IN :space_ids"


def _candidate_search_sql(scoped: bool) -> Any:
    """① 사전 필터 + pgvector 검색을 한 쿼리로 결합한다(scoped 면 스페이스 교집합 추가).

    - granted: 내 활성 그룹에 부여된(미만료) grant 대상 파일 + inherit_to_children 서브트리.
      grant 지점 파일은 항상 후보, 하위는 grant 가 상속될 때만(inherit=TRUE) 재귀로 포함한다.
    - wiki_pages: 위키 루트 서브트리(전사 위키 페이지). 시스템 사용자 소유라 owned/granted 에 안
      잡히지만 **모든 로그인 사용자**가 읽을 수 있어야 하므로(wiki-v2 D4 특례, get_access_level 과
      일관) 후보에 포함한다. wiki_root 가 NULL(부트스트랩 전)이면 시드가 없어 빈 집합이다.
    - candidates: 내 소유 파일 ∪ granted ∪ wiki_pages. 후보 밖 청크는 스캔되지 않는다.
    - qvec 는 텍스트 벡터 리터럴로 넘겨 CAST(:qvec AS vector) 로 캐스팅한다(타입 등록 불요).
    """
    return text(
        f"""
        WITH RECURSIVE granted AS (
            SELECT p.file_id AS id, p.inherit_to_children AS inherit
            FROM file_group_permissions p
            WHERE p.group_id IN :group_ids
              AND (p.expires_at IS NULL OR p.expires_at > :now)
            UNION
            SELECT f.id, g.inherit
            FROM files f
            JOIN granted g ON f.parent_folder_id = g.id
            WHERE g.inherit = TRUE
        ),
        wiki_pages AS (
            SELECT id FROM files WHERE id = :wiki_root
            UNION
            SELECT f.id FROM files f JOIN wiki_pages w ON f.parent_folder_id = w.id
        ),
        candidates AS (
            SELECT id FROM files WHERE user_id = :user_id
            UNION
            SELECT id FROM granted
            UNION
            SELECT id FROM wiki_pages
        )
        SELECT c.id AS chunk_id, c.file_id AS file_id, f.name AS file_name,
               c.version AS version, c.content AS content,
               (c.embedding <=> CAST(:qvec AS vector)) AS distance
        FROM file_chunks c
        JOIN files f ON f.id = c.file_id
        WHERE f.is_deleted = FALSE
          AND c.embedding IS NOT NULL
          AND c.file_id IN (SELECT id FROM candidates)
          {_SPACE_RESTRICT if scoped else ""}
        ORDER BY c.embedding <=> CAST(:qvec AS vector)
        LIMIT :limit
        """
    ).bindparams(
        bindparam("group_ids", expanding=True),
        *([bindparam("space_ids", expanding=True)] if scoped else []),
    )


_CANDIDATE_SEARCH_SQL = _candidate_search_sql(scoped=False)
_CANDIDATE_SEARCH_SQL_SCOPED = _candidate_search_sql(scoped=True)


def prioritize_wiki(
    chunks: list[RetrievedChunk], wiki_page_ids: set[int], k: int
) -> list[RetrievedChunk]:
    """위키 페이지 청크를 우선 배치한다(PRD 3.7.5 하이브리드 — 위키 우선 + 원문 보강).

    k 의 절반을 위키 페이지 청크에 우선 할당하고, 나머지는 원문 청크로 채운다. 부족분은 남은
    위키 청크로 보충한다. 각 그룹은 이미 거리순이며 최종 결과도 거리순으로 정렬해 반환한다.
    wiki_page_ids 가 비면 단순히 상위 k 를 반환한다(범위 지정 없음).
    """
    if not wiki_page_ids or k <= 0:
        return chunks[:k]
    wiki = [c for c in chunks if c.file_id in wiki_page_ids]
    rest = [c for c in chunks if c.file_id not in wiki_page_ids]
    half = k // 2
    picked = wiki[:half]
    picked += rest[: k - len(picked)]
    if len(picked) < k:
        picked += wiki[half : half + (k - len(picked))]
    return sorted(picked, key=lambda c: c.distance)[:k]


def _vector_literal(vec: list[float]) -> str:
    """pgvector 텍스트 리터럴 `[v0,v1,...]` 로 직렬화한다(CAST(... AS vector) 대상)."""
    return "[" + ",".join(repr(float(x)) for x in vec) + "]"


async def _coarse_search(
    session: AsyncSession,
    user: User,
    query_embedding: list[float],
    limit: int,
    space_ids: set[int] | None = None,
) -> list[RetrievedChunk]:
    """사전 필터(후보 집합) 를 결합한 pgvector 검색. 후보 밖 청크는 스캔되지 않는다.

    space_ids 가 주어지면 후보를 그 스페이스 파일 집합으로 추가 교집합한다(범위는 권한을 넓히지
    않는다). 빈 집합이면 후보가 없으므로 즉시 [] 를 반환한다.
    """
    group_ids = await get_user_group_ids(session, user.id)
    # 위키 루트(전사 위키 페이지 서브트리)를 후보에 포함하기 위한 시드. 부트스트랩 전이면 None.
    wiki_root = await _get_wiki_root_folder_id(session)
    params: dict[str, Any] = {
        "user_id": user.id,
        "group_ids": group_ids or [-1],
        "now": datetime.now(UTC),
        "qvec": _vector_literal(query_embedding),
        "limit": limit,
        "wiki_root": wiki_root,
    }
    if space_ids is not None:
        if not space_ids:
            return []
        params["space_ids"] = list(space_ids)
        sql = _CANDIDATE_SEARCH_SQL_SCOPED
    else:
        sql = _CANDIDATE_SEARCH_SQL
    # expanding bindparam 은 빈 리스트도 안전히 처리한다(거짓 술어 → granted 시드 없음).
    result = await session.execute(sql, params)
    return [
        RetrievedChunk(
            chunk_id=r.chunk_id,
            file_id=r.file_id,
            file_name=r.file_name,
            version=r.version,
            content=r.content,
            distance=float(r.distance),
        )
        for r in result
    ]


async def _has_read_access(session: AsyncSession, user: User, file: File) -> bool:
    """사후 검증 — 소유자거나 그룹 권한이 read 이상이면 True (get_access_level 재검증).

    role 을 보지 않으므로 admin 도 타인 파일에 접근하지 못한다(PRD 3.7.3). 모든 그룹 권한
    수준(read/write/manage)은 read 를 충족하므로 level is not None 이면 통과다.
    """
    if file.user_id == user.id:
        return True
    level = await get_access_level(session, user, file)
    return level is not None


async def retrieve(
    session: AsyncSession,
    user: User,
    query_embedding: list[float],
    *,
    k: int,
    space_ids: set[int] | None = None,
    wiki_page_ids: set[int] | None = None,
) -> list[RetrievedChunk]:
    """권한 인지 검색 — 사전 필터 검색 후 파일별 사후 검증을 거친 청크 상위 k 를 반환한다.

    항상 요청 사용자(user) 자격으로 검색한다(admin 예외 없음). space_ids 가 주어지면 후보를 그
    스페이스 파일 집합으로 교집합하고(범위 제한), wiki_page_ids 로 위키 페이지 청크를 우선
    배치한다(PRD 3.7.5). 반환 순서는 코사인 거리 오름차순(유사도 내림차순).
    """
    if k <= 0:
        return []
    coarse_limit = max(k * _OVERSAMPLE, k + _OVERSAMPLE_MIN_MARGIN)
    coarse = await _coarse_search(
        session, user, query_embedding, coarse_limit, space_ids
    )

    # 파일별 접근 판정을 캐시해 같은 파일의 여러 청크에 중복 판정을 피한다. 위키 우선 배치를
    # 위해 상위 후보 전체를 사후 검증한다(coarse_limit 은 작아 비용이 제한적).
    verdict: dict[int, bool] = {}
    passed: list[RetrievedChunk] = []
    for chunk in coarse:
        allowed = verdict.get(chunk.file_id)
        if allowed is None:
            file = await session.get(File, chunk.file_id)
            allowed = file is not None and await _has_read_access(session, user, file)
            verdict[chunk.file_id] = allowed
        if allowed:
            passed.append(chunk)

    result = prioritize_wiki(passed, wiki_page_ids or set(), k)
    dropped = len(coarse) - len(passed)
    if dropped:
        _log.info(
            "retrieval_post_verify_dropped",
            user_id=user.id,
            coarse=len(coarse),
            passed=len(passed),
            dropped=dropped,
        )
    return result
