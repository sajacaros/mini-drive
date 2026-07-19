"""LLM 위키 통합 검증 (실제 postgres[pgvector]+redis+minio 필요, pytest 미수집, Phase 7-3).

시나리오(권한 경계 = 컴파일 경계 + Phase 7 완료 조건):
  - 그룹 스코프 스페이스 생성 → 루트 폴더에 그룹 read 자동 부여 확인
  - 비권한 파일 소스 등록 403(그룹이 read 불가) → 그룹 read 부여 후 정상 등록
  - wiki_ingest 직접 호출 → 페이지 드라이브 파일 생성 + index.md/log.md 부기 + 페이지 청크 임베딩
  - ① B그룹 사용자가 스페이스 접근 404 / 세션 space_id 지정 404 / 전역 검색 미포함(그룹 격리)
  - ⑤ admin 도 타인(그룹 A) 위키 페이지 내용 미노출
  - ④ 인용 대상(페이지) 은 소유자만 preview 통과(ensure_file_access)
  - 답변 승격(promote) → 위키 페이지 생성
  - Lint → 소스 버전 갱신 stale 리포트

인덱싱/컴파일은 워커 큐를 거치지 않고 서비스 함수를 직접 호출한다(EMBEDDING_PROVIDER=fake,
CHAT_PROVIDER=fake — 결정적). rate limit 은 비활성화한다.

env: DATABASE_URL / REDIS_URL / MINIO_ENDPOINT / MINIO_BUCKET, EMBEDDING_PROVIDER=fake,
     CHAT_PROVIDER=fake, INDEXING_ENABLED=false, RATE_LIMIT_ENABLED=false.
     `python -m tests.integration_wiki`.
"""

from __future__ import annotations

import asyncio
import json
import sys

import httpx
from httpx import ASGITransport
from sqlalchemy import select

import app.models  # noqa: F401 - 전체 모델을 metadata 에 등록
from app.core.config import settings
from app.core.database import Base, SessionFactory, engine
from app.core.redis import redis_client
from app.main import app
from app.models import FileChunk
from app.services import files as files_service
from app.services import indexing as indexing_service
from app.services import wiki as wiki_service
from app.services.chat_llm import get_chat_provider
from app.services.embeddings import get_embedding_provider
from app.services.storage import storage_service
from tests._bootstrap import register_active, setup_admin
from tests._dbreset import stamp_alembic_head

ALICE = {"email": "alice@example.com", "password": "Passw0rd!", "display_name": "Alice"}
BOB = {"email": "bob@example.com", "password": "Passw0rd!", "display_name": "Bob"}

DOC = "\n".join(
    f"문단 {i}: 사내 보안 정책 문서. 비밀번호 규정과 접근 통제 원칙을 다룬다." for i in range(80)
)
DOC_V2 = "\n".join(
    f"문단 {i}: 갱신된 보안 정책. 2단계 인증 의무화를 추가했다." for i in range(80)
)


def _ok(msg: str) -> None:
    print(f"  [PASS] {msg}")


async def _reset() -> None:
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
        await conn.run_sync(Base.metadata.create_all)
        await conn.run_sync(stamp_alembic_head)
    await redis_client.flushdb()
    if not storage_service._client.bucket_exists(storage_service.bucket):
        storage_service._client.make_bucket(storage_service.bucket)
    leftover = [
        o.object_name
        for o in storage_service._client.list_objects(
            storage_service.bucket, recursive=True
        )
    ]
    storage_service.delete_many(leftover)


async def _ingest(space_id: int, file_id: int) -> int:
    """워커 wiki_ingest 와 동일 경로(ingest_source)를 fake 챗 프로바이더로 직접 호출한다."""
    provider = get_chat_provider(settings)
    async with SessionFactory() as s:
        summary = await wiki_service.ingest_source(
            s, storage_service, provider, settings, space_id, file_id
        )
    return summary.compiled


async def _index(file_id: int) -> int:
    provider = get_embedding_provider(settings)
    async with SessionFactory() as s:
        return await indexing_service.index_target(
            s, storage_service, provider, file_id, settings
        )


async def _find_page_id(root_id: int, name: str) -> int | None:
    async with SessionFactory() as s:
        row = await files_service.find_active_child(s, root_id, name)
        return row.id if row else None


async def _chunks(file_id: int) -> list[FileChunk]:
    async with SessionFactory() as s:
        rows = (
            await s.execute(
                select(FileChunk).where(FileChunk.file_id == file_id)
            )
        ).scalars().all()
        return list(rows)


async def _upload(
    c: httpx.AsyncClient, h: dict[str, str], name: str, body: bytes, parent_id: int | None = None
) -> int:
    data = {"parent_id": str(parent_id)} if parent_id is not None else None
    r = await c.post(
        "/api/files/upload",
        headers=h,
        data=data,
        files={"file": (name, body, "text/plain")},
    )
    assert r.status_code == 201, r.text
    return r.json()["id"]


async def _new_session(
    c: httpx.AsyncClient, h: dict[str, str], space_id: int | None = None
) -> httpx.Response:
    body: dict = {}
    if space_id is not None:
        body["space_id"] = space_id
    return await c.post("/api/chat/sessions", headers=h, json=body)


async def _ask(
    c: httpx.AsyncClient, h: dict[str, str], session_id: int, question: str
) -> tuple[list[dict], dict]:
    citations: list[dict] = []
    done: dict = {}
    cur: str | None = None
    async with c.stream(
        "POST",
        f"/api/chat/sessions/{session_id}/messages",
        headers=h,
        json={"content": question},
    ) as r:
        assert r.status_code == 200, (r.status_code, await r.aread())
        async for line in r.aiter_lines():
            if line.startswith("event: "):
                cur = line[len("event: ") :]
            elif line.startswith("data: "):
                payload = json.loads(line[len("data: ") :])
                if cur == "citations":
                    citations = payload
                elif cur == "done":
                    done = payload
                elif cur == "error":
                    raise AssertionError(f"SSE error: {payload}")
    return citations, done


def _cited_ids(citations: list[dict]) -> set[int]:
    return {c["file_id"] for c in citations}


async def scenario() -> None:
    assert settings.embedding_provider == "fake", "EMBEDDING_PROVIDER=fake 로 실행하세요."
    assert settings.chat_provider == "fake", "CHAT_PROVIDER=fake 로 실행하세요."
    await _reset()

    transport = ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test", timeout=60) as c:
        admin_h, code = await setup_admin(c)
        alice_h, _alice_id = await register_active(c, code, ALICE)
        bob_h, bob_id = await register_active(c, code, BOB)
        _ok("셋업 + alice/bob 코드 가입")

        # 그룹 A(alice owner) / 그룹 B(alice owner, bob member).
        r = await c.post("/api/groups", headers=alice_h, json={"name": "A팀"})
        group_a = r.json()["id"]
        r = await c.post("/api/groups", headers=alice_h, json={"name": "B팀"})
        group_b = r.json()["id"]
        r = await c.post(
            f"/api/groups/{group_b}/members",
            headers=alice_h,
            json={"user_id": bob_id, "role": "member"},
        )
        assert r.status_code == 201, r.text
        _ok("그룹 A(alice) / 그룹 B(alice, bob member)")

        # 그룹 A 스코프 스페이스 생성 → 루트 폴더에 그룹 A read(inherit) 자동 부여 확인.
        r = await c.post(
            "/api/wiki/spaces",
            headers=alice_h,
            json={"name": "보안위키", "scope": "group", "group_id": group_a},
        )
        assert r.status_code == 201, r.text
        space_a = r.json()["id"]
        root_a = r.json()["root_folder_id"]
        r = await c.get(f"/api/files/{root_a}/permissions", headers=alice_h)
        assert r.status_code == 200, r.text
        grants = r.json()["direct"]
        assert any(
            g["group_id"] == group_a and g["permission"] == "read" and g["inherit_to_children"]
            for g in grants
        ), grants
        _ok("그룹 스코프 스페이스 생성 → 루트 폴더에 그룹 A read(inherit) 자동 부여")

        # bob(그룹 A 비멤버)은 그룹 admin 이 아니므로 그룹 A 스페이스 생성도 불가(403).
        r = await c.post(
            "/api/wiki/spaces",
            headers=bob_h,
            json={"name": "침입시도", "scope": "group", "group_id": group_a},
        )
        assert r.status_code == 403, r.text
        _ok("비멤버 그룹 스페이스 생성 거부(403)")

        # 비권한 파일 소스 등록 403 — alice 개인 파일(그룹 A 미공유)은 그룹이 read 불가.
        private_id = await _upload(c, alice_h, "private.txt", DOC.encode())
        r = await c.post(
            f"/api/wiki/spaces/{space_a}/sources",
            headers=alice_h,
            json={"file_id": private_id, "recursive": False},
        )
        assert r.status_code == 403, ("권한 경계=컴파일 경계", r.text)
        _ok("① 비권한 파일 소스 등록 403(그룹 read 불가 — 권한 경계=컴파일 경계)")

        # 정상 등록 — 폴더를 그룹 A 에 read 공유 후 그 하위 문서를 소스로.
        r = await c.post("/api/files", headers=alice_h, json={"name": "정책함"})
        folder_id = r.json()["id"]
        doc_id = await _upload(c, alice_h, "policy.txt", DOC.encode(), parent_id=folder_id)
        r = await c.post(
            f"/api/files/{folder_id}/permissions",
            headers=alice_h,
            json={"group_id": group_a, "permission": "read", "inherit_to_children": True},
        )
        assert r.status_code == 201, r.text
        r = await c.post(
            f"/api/wiki/spaces/{space_a}/sources",
            headers=alice_h,
            json={"file_id": doc_id, "recursive": False},
        )
        assert r.status_code == 201 and r.json()["file_id"] == doc_id, r.text
        _ok("그룹 read 부여 후 정상 소스 등록(201)")

        # wiki_ingest 직접 호출 → 페이지 생성 + index/log 부기 + 페이지 청크 임베딩.
        compiled = await _ingest(space_a, doc_id)
        assert compiled == 1, compiled
        page_id = await _find_page_id(root_a, "policy.md")
        assert page_id is not None, "policy.md 페이지가 생성돼야 한다"
        # index.md / log.md 부기 확인(상세 API).
        r = await c.get(f"/api/wiki/spaces/{space_a}", headers=alice_h)
        detail = r.json()
        assert any("policy.md" in e for e in detail["index_entries"]), detail["index_entries"]
        assert any("ingest" in ln for ln in detail["recent_log"]), detail["recent_log"]
        assert detail["sources"][0]["status"] == "indexed", detail["sources"]
        # 페이지 청크 임베딩(위키 페이지도 검색 대상).
        await _index(page_id)
        page_chunks = await _chunks(page_id)
        assert page_chunks and all(len(ch.embedding) == 4096 for ch in page_chunks)
        _ok("wiki_ingest → policy.md 생성 + index/log 부기 + 페이지 청크 임베딩")

        QUESTION = "사내 보안 정책의 접근 통제 원칙 알려줘"

        # alice 스페이스 세션 → 위키 페이지/소스 인용(범위 검색).
        r = await _new_session(c, alice_h, space_id=space_a)
        assert r.status_code == 201, r.text
        s_alice = r.json()["id"]
        citations, done = await _ask(c, alice_h, s_alice, QUESTION)
        assert citations, "스페이스 범위 검색이 인용을 내야 한다"
        assert done.get("message_id"), done
        cited = _cited_ids(citations)
        assert page_id in cited or doc_id in cited, cited
        _ok("alice 스페이스 세션 → 범위 검색 인용")

        # ④ 인용 대상 페이지는 소유자만 preview 통과(ensure_file_access).
        r = await c.get(f"/api/files/{page_id}/preview", headers=alice_h)
        assert r.status_code == 200, r.text
        r = await c.get(f"/api/files/{page_id}/preview", headers=bob_h)
        assert r.status_code == 404, ("④ 비권한자는 인용 링크를 열 수 없다", r.text)
        _ok("④ 인용 대상(위키 페이지) 은 소유자만 preview 통과(ensure_file_access)")

        # ① 그룹 격리 — bob(그룹 A 비멤버)은 스페이스 접근/세션 space_id 지정 404.
        r = await c.get(f"/api/wiki/spaces/{space_a}", headers=bob_h)
        assert r.status_code == 404, r.text
        r = await _new_session(c, bob_h, space_id=space_a)
        assert r.status_code == 404, ("세션 space_id 지정도 접근 검증", r.text)
        # 전역 세션에서도 그룹 A 위키 페이지/소스 미검색.
        r = await _new_session(c, bob_h)
        s_bob = r.json()["id"]
        citations_bob, _ = await _ask(c, bob_h, s_bob, QUESTION)
        assert page_id not in _cited_ids(citations_bob), citations_bob
        assert doc_id not in _cited_ids(citations_bob), citations_bob
        _ok("① 그룹 격리 — bob 스페이스 접근 404 + 세션 space_id 404 + 전역 검색 미포함")

        # ⑤ admin 도 타인 위키 페이지/소스 미노출.
        r = await _new_session(c, admin_h)
        s_admin = r.json()["id"]
        citations_admin, _ = await _ask(c, admin_h, s_admin, QUESTION)
        assert page_id not in _cited_ids(citations_admin), citations_admin
        assert doc_id not in _cited_ids(citations_admin), citations_admin
        _ok("⑤ admin 질문 → 타인 위키 페이지/소스 미노출")

        # 답변 승격(promote) → 위키 페이지 생성.
        assert done.get("message_id"), done
        r = await c.post(
            f"/api/chat/messages/{done['message_id']}/promote",
            headers=alice_h,
            json={"space_id": space_a, "title": "승격된 답변"},
        )
        assert r.status_code == 200, r.text
        promoted_id = r.json()["file_id"]
        promoted_page = await _find_page_id(root_a, "승격된 답변.md")
        assert promoted_page == promoted_id, (promoted_page, promoted_id)
        # bob 은 타인 세션 메시지를 승격할 수 없다(404).
        r = await c.post(
            f"/api/chat/messages/{done['message_id']}/promote",
            headers=bob_h,
            json={"space_id": space_a, "title": "탈취"},
        )
        assert r.status_code == 404, r.text
        _ok("답변 승격 → 위키 페이지 생성(타인 메시지 승격 404)")

        # Lint — 소스 버전 갱신 후 stale 리포트.
        r = await c.post(
            f"/api/files/{doc_id}/upload",
            headers=alice_h,
            data={"base_version": "1"},
            files={"file": ("policy.txt", DOC_V2.encode(), "text/plain")},
        )
        assert r.status_code == 201 and r.json()["current_version"] == 2, r.text
        r = await c.post(f"/api/wiki/spaces/{space_a}/lint", headers=alice_h)
        assert r.status_code == 200, r.text
        lint = r.json()
        assert any("stale" in msg for msg in lint["reports"]), lint
        _ok("Lint → 소스 버전 갱신 stale 리포트")

        # 잡 이력 조회(스페이스 접근자만) + bob 은 404.
        r = await c.get(f"/api/wiki/spaces/{space_a}/jobs", headers=alice_h)
        assert r.status_code == 200 and any(j["kind"] == "ingest" for j in r.json()), r.text
        r = await c.get(f"/api/wiki/spaces/{space_a}/jobs", headers=bob_h)
        assert r.status_code == 404, r.text
        _ok("잡 이력 조회(스페이스 접근자만, bob 404)")

        # 스페이스 목록 — alice 는 그룹 A 스페이스 보임, bob 은 안 보임.
        r = await c.get("/api/wiki/spaces", headers=alice_h)
        assert any(s["id"] == space_a for s in r.json()), r.text
        r = await c.get("/api/wiki/spaces", headers=bob_h)
        assert all(s["id"] != space_a for s in r.json()), r.text
        _ok("스페이스 접근 목록 — 소속 그룹만 노출")

        # 스페이스 삭제 → wiki_* CASCADE, 페이지 폴더는 드라이브에 잔존.
        r = await c.request("DELETE", f"/api/wiki/spaces/{space_a}", headers=alice_h)
        assert r.status_code == 204, r.text
        r = await c.get(f"/api/wiki/spaces/{space_a}", headers=alice_h)
        assert r.status_code == 404, r.text
        assert await _find_page_id(root_a, "policy.md") is not None, "페이지 폴더는 잔존"
        _ok("스페이스 삭제 → wiki_* CASCADE, 위키 페이지 폴더 잔존")

    await engine.dispose()
    await redis_client.aclose()
    print("\n위키 통합 시나리오 전체 통과.")


def main() -> int:
    try:
        asyncio.run(scenario())
    except AssertionError as exc:
        print(f"\n[FAIL] {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
