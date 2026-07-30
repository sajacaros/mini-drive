"""위키 라우터 (spec/wiki-index.md) — 문서 카탈로그(목록·절 트리)와 질의.

파일 단위 토글(`/api/files/{id}/wiki`)은 경로가 `/api/files/*` 라 files 라우터에 둔다 —
권한 부여 엔드포인트와 같은 이유다(routes/permissions.py 주석 참조). 이 라우터는
`/api/wiki/*` 만 담당한다.
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, HTTPException, Query

from app.api.deps import CurrentUser, DbSession
from app.core.logging import get_logger
from app.schemas.wiki import (
    WikiAskRequest,
    WikiAskResponse,
    WikiCatalogNode,
    WikiCatalogResponse,
    WikiCitation,
    WikiDocumentItem,
    WikiDocumentListResponse,
)
from app.services import wiki as wiki_service
from app.services import wiki_query
from app.services.storage import get_storage
from app.services.wiki_llm import WikiLLMError, WikiLLMRequestError

router = APIRouter()
log = get_logger(__name__)


@router.get("/documents", response_model=WikiDocumentListResponse)
async def list_documents(
    user: CurrentUser,
    session: DbSession,
    page: Annotated[int, Query(ge=1)] = 1,
    size: Annotated[int, Query(ge=1, le=100)] = 50,
    q: Annotated[str | None, Query(max_length=200)] = None,
) -> WikiDocumentListResponse:
    """전사 위키의 문서 목록 — 사람마다 다르지 않다.

    인덱싱된 문서는 전 직원 공개라는 불변식(services/wiki.py)이 있으므로 문서별 권한 판정을
    하지 않는다. 로그인은 여전히 필요하다.

    `q` 는 문서명 부분 일치다. 전사 위키는 수백 건으로 커지므로(실측 467건) 이름순 페이지를
    넘겨 찾는 것이 사실상 불가능하다 — 방금 켠 내 문서가 색인됐는지 확인하는 것이 이 화면의
    주 용도인데 그게 안 되면 화면이 제 일을 못 한다.
    """
    items, total = await wiki_service.list_documents(session, page, size, query=q)
    return WikiDocumentListResponse(
        items=[
            WikiDocumentItem(
                file_id=i.file_id,
                name=i.name,
                owner_display_name=i.owner_display_name,
                location=i.location,
                status=i.status,
                version=i.version,
                indexed_at=i.indexed_at.isoformat() if i.indexed_at else None,
                node_count=i.node_count,
            )
            for i in items
        ],
        total=total,
    )


@router.get("/documents/{file_id}", response_model=WikiCatalogResponse)
async def get_catalog(
    file_id: int, user: CurrentUser, session: DbSession
) -> WikiCatalogResponse:
    """문서 한 건의 절 트리(카탈로그).

    목록과 같은 규칙이다 — 전사 위키이므로 권한 판정 없이 열고, 없는 문서만 404 다.
    """
    try:
        catalog = await wiki_service.get_catalog(session, file_id)
    except wiki_service.WikiServiceError as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.detail) from exc
    return WikiCatalogResponse(
        file_id=catalog.file_id,
        name=catalog.name,
        owner_display_name=catalog.owner_display_name,
        location=catalog.location,
        status=catalog.status,
        version=catalog.version,
        indexed_at=catalog.indexed_at.isoformat() if catalog.indexed_at else None,
        node_count=catalog.node_count,
        nodes=[_catalog_node(n) for n in catalog.nodes],
    )


def _catalog_node(node: wiki_service.CatalogNode) -> WikiCatalogNode:
    """서비스 dataclass → 응답 스키마 (재귀)."""
    return WikiCatalogNode(
        node_id=node.node_id,
        title=node.title,
        line_num=node.line_num,
        summary=node.summary,
        nodes=[_catalog_node(c) for c in node.nodes],
    )


@router.post("/ask", response_model=WikiAskResponse)
async def ask(
    payload: WikiAskRequest, user: CurrentUser, session: DbSession
) -> WikiAskResponse:
    """인덱싱된 문서에 질의한다 (spec/wiki-index.md).

    검색 대상은 전사 위키에 올라간 문서 전체다(인덱싱 = 전사 공개). 문서가 많으면 질문과
    관련된 노드부터 예산만큼만 모델에 올린다 — 전부 넣으면 컨텍스트를 넘어 질의가 통째로
    실패한다(spec 「후보 선별이 없으면 문서가 늘자마자 통째로 실패한다」).
    """
    question = payload.question.strip()
    if not question:
        raise HTTPException(status_code=422, detail="질문이 비어 있습니다.")
    try:
        result = await wiki_query.ask(session, get_storage(), question)
    except WikiLLMRequestError as exc:
        # 모델이 **우리 요청**을 거부했다. 서버 탓으로 말하면 원인을 못 찾는다 — 실제로 이
        # 자리의 "연결할 수 없습니다"가 프롬프트 컨텍스트 초과를 가리고 있었다(2026-07-30).
        log.error("wiki_ask_bad_request", user_id=user.id, error=str(exc))
        raise HTTPException(
            status_code=500,
            detail="질의를 구성하지 못했습니다. 관리자에게 문의해 주세요.",
        ) from exc
    except WikiLLMError as exc:
        # 원인 문자열에는 모델 서버의 **주소·포트와 상태 코드**가 들어 있다(httpx 예외를 그대로
        # 감싼다). 그대로 detail 에 실으면 브라우저까지 내려가 내부 엔드포인트가 공개된다 —
        # 진단에 필요한 쪽은 로그이고, 사용자에게 필요한 것은 "지금 내가 뭘 하면 되는가"다.
        log.warning("wiki_ask_llm_failed", user_id=user.id, error=str(exc))
        raise HTTPException(
            status_code=503,
            detail="위키 모델 서버에 연결할 수 없습니다. 잠시 후 다시 시도해 주세요.",
        ) from exc
    return WikiAskResponse(
        answer=result.answer,
        citations=[
            WikiCitation(
                file_id=c.file_id,
                file_name=c.file_name,
                node_id=c.node_id,
                node_title=c.node_title,
                line_num=c.line_num,
            )
            for c in result.citations
        ],
        searched_documents=result.searched_documents,
        examined_documents=result.examined_documents,
    )
