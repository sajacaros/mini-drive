"""챗봇 권한 인지 검색 통합 검증 (실제 postgres[pgvector]+redis+minio 필요, pytest 미수집).

Phase 7 완료 조건의 raw-chunk(위키 이전) 버전 + 세션 CRUD/소유자 격리/SSE 스트림:
  ① A그룹 폴더 문서 인덱싱 후 B그룹 사용자 질문 → 검색·인용 불가(사전 필터가 후보에서 제외)
  ② 권한 부여 후 접근 → 회수 직후 동일 질문 → 즉시 제외(grant 삭제 + 세대 캐시 무효화)
  ⑤ admin 계정 질문 → 타인 파일 내용 미노출(role 무시, 소유/그룹만)
  ③ 소스 버전 갱신 → 재인덱싱 후 새 내용으로 검색(citation version=2, 새 스니펫)
  + 세션 생성/목록/상세(citations)/삭제, 소유자 격리(타인 세션 404), SSE token→citations→done 수신.

인덱싱은 워커 큐를 거치지 않고 서비스 함수를 직접 호출한다(EMBEDDING_PROVIDER=fake). 챗 생성은
외부 호출 없는 결정적 fake 프로바이더(CHAT_PROVIDER=fake)를 쓴다. rate limit 은 비활성화한다.

env: DATABASE_URL / REDIS_URL / MINIO_ENDPOINT / MINIO_BUCKET, EMBEDDING_PROVIDER=fake,
     CHAT_PROVIDER=fake, INDEXING_ENABLED=false, RATE_LIMIT_ENABLED=false.
     `python -m tests.integration_chat`.
"""

from __future__ import annotations

import asyncio
import json
import sys

import httpx
from httpx import ASGITransport

import app.models  # noqa: F401 - 전체 모델을 metadata 에 등록
from app.core.config import settings
from app.core.database import Base, SessionFactory, engine
from app.core.redis import redis_client
from app.main import app
from app.services import indexing as indexing_service
from app.services.embeddings import get_embedding_provider
from app.services.storage import storage_service
from tests._bootstrap import register_active, setup_admin
from tests._dbreset import stamp_alembic_head

ALICE = {"email": "alice@example.com", "password": "Passw0rd!", "display_name": "Alice"}
BOB = {"email": "bob@example.com", "password": "Passw0rd!", "display_name": "Bob"}

# 청크가 여러 개 나오도록 chunk_size(1000) 를 넘기고, 버전별로 뚜렷이 구분되는 내용을 담는다.
DOC_V1 = "\n".join(
    f"버전1 문단 {i}: 분기 매출 보고서 초안. 신제품 라인의 성장세를 다룬다." for i in range(80)
)
DOC_V2 = "\n".join(
    f"버전2 문단 {i}: 완전히 교체된 내용. 물류 비용 절감 계획을 다룬다." for i in range(80)
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


async def _index(file_id: int) -> int:
    """워커 태스크와 동일한 경로(index_target)를 fake 프로바이더로 직접 호출한다."""
    provider = get_embedding_provider(settings)
    async with SessionFactory() as s:
        return await indexing_service.index_target(
            s, storage_service, provider, file_id, settings
        )


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


async def _ask(
    c: httpx.AsyncClient, h: dict[str, str], session_id: int, question: str
) -> tuple[list[str], list[dict], dict]:
    """SSE 로 질문을 보내고 (토큰들, citations, done 페이로드) 를 반환한다."""
    tokens: list[str] = []
    citations: list[dict] = []
    done: dict = {}
    cur_event: str | None = None
    async with c.stream(
        "POST",
        f"/api/chat/sessions/{session_id}/messages",
        headers=h,
        json={"content": question},
    ) as r:
        assert r.status_code == 200, (r.status_code, await r.aread())
        assert r.headers.get("content-type", "").startswith("text/event-stream")
        assert r.headers.get("x-accel-buffering") == "no"
        async for line in r.aiter_lines():
            if line.startswith("event: "):
                cur_event = line[len("event: ") :]
            elif line.startswith("data: "):
                payload = json.loads(line[len("data: ") :])
                if cur_event == "token":
                    tokens.append(payload["value"])
                elif cur_event == "citations":
                    citations = payload
                elif cur_event == "done":
                    done = payload
                elif cur_event == "error":
                    raise AssertionError(f"SSE error: {payload}")
    return tokens, citations, done


def _cited_file_ids(citations: list[dict]) -> set[int]:
    return {ci["file_id"] for ci in citations}


async def _new_session(c: httpx.AsyncClient, h: dict[str, str], title: str = "") -> int:
    r = await c.post("/api/chat/sessions", headers=h, json={"title": title})
    assert r.status_code == 201, r.text
    return r.json()["id"]


async def scenario() -> None:
    assert settings.embedding_provider == "fake", "EMBEDDING_PROVIDER=fake 로 실행하세요."
    assert settings.chat_provider == "fake", "CHAT_PROVIDER=fake 로 실행하세요."
    await _reset()

    transport = ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test", timeout=60) as c:
        admin_h, code = await setup_admin(c)
        alice_h, alice_id = await register_active(c, code, ALICE)
        bob_h, bob_id = await register_active(c, code, BOB)
        _ok("셋업 + alice/bob 코드 가입")

        # 그룹 A(alice), 그룹 B(bob) 구성.
        r = await c.post("/api/groups", headers=alice_h, json={"name": "A팀"})
        assert r.status_code == 201, r.text
        group_a = r.json()["id"]
        r = await c.post("/api/groups", headers=alice_h, json={"name": "B팀"})
        assert r.status_code == 201, r.text
        group_b = r.json()["id"]
        r = await c.post(
            f"/api/groups/{group_b}/members",
            headers=alice_h,
            json={"user_id": bob_id, "role": "member"},
        )
        assert r.status_code == 201, r.text
        _ok("그룹 A(alice) / 그룹 B(bob member) 구성")

        # alice 가 폴더 + 문서 업로드, 그룹 A read(inherit) 부여, 인덱싱.
        r = await c.post("/api/files", headers=alice_h, json={"name": "보고서함"})
        assert r.status_code == 201, r.text
        folder_id = r.json()["id"]
        doc_id = await _upload(c, alice_h, "report.txt", DOC_V1.encode(), parent_id=folder_id)
        r = await c.post(
            f"/api/files/{folder_id}/permissions",
            headers=alice_h,
            json={"group_id": group_a, "permission": "read", "inherit_to_children": True},
        )
        assert r.status_code == 201, r.text
        assert await _index(doc_id) == 1
        _ok("폴더+문서 업로드 → 그룹 A read 부여 → 인덱싱")

        QUESTION = "분기 매출 보고서 내용 알려줘"

        # alice(소유자) 질문 → 문서 인용됨.
        s_alice = await _new_session(c, alice_h)
        tokens, citations, done = await _ask(c, alice_h, s_alice, QUESTION)
        assert tokens, "토큰 스트림을 받아야 한다"
        assert doc_id in _cited_file_ids(citations), citations
        assert done.get("message_id"), done
        assert any("버전1" in ci["snippet"] for ci in citations), citations
        # citations 에 file_name 포함(프론트 인용 칩이 추가 조회 없이 파일명 표시).
        assert all(ci.get("file_name") for ci in citations), citations
        assert any(ci["file_name"] == "report.txt" for ci in citations), citations
        _ok("① alice(소유자) 질문 → report.txt 인용(SSE token/citations/done 수신)")

        # ① B그룹 bob 질문 → 인용 불가(사전 필터가 후보에서 제외).
        s_bob = await _new_session(c, bob_h)
        _, citations_bob, _ = await _ask(c, bob_h, s_bob, QUESTION)
        assert doc_id not in _cited_file_ids(citations_bob), citations_bob
        assert citations_bob == [], citations_bob
        _ok("① B그룹 bob 질문 → 타 그룹 문서 검색·인용 불가")

        # ⑤ admin 질문 → 타인 파일 미노출(role 무시).
        s_admin = await _new_session(c, admin_h)
        _, citations_admin, _ = await _ask(c, admin_h, s_admin, QUESTION)
        assert doc_id not in _cited_file_ids(citations_admin), citations_admin
        _ok("⑤ admin 질문 → 타인 파일 내용 미노출")

        # ② 그룹 B read 부여 → bob 인용 가능.
        r = await c.post(
            f"/api/files/{folder_id}/permissions",
            headers=alice_h,
            json={"group_id": group_b, "permission": "read", "inherit_to_children": True},
        )
        assert r.status_code == 201, r.text
        _, citations_bob2, _ = await _ask(c, bob_h, s_bob, QUESTION)
        assert doc_id in _cited_file_ids(citations_bob2), citations_bob2
        _ok("② 그룹 B read 부여 → bob 인용 가능")

        # ② 회수 직후 동일 질문 → 즉시 제외.
        r = await c.request(
            "DELETE", f"/api/files/{folder_id}/permissions/{group_b}", headers=alice_h
        )
        assert r.status_code == 204, r.text
        _, citations_bob3, _ = await _ask(c, bob_h, s_bob, QUESTION)
        assert doc_id not in _cited_file_ids(citations_bob3), citations_bob3
        _ok("② 권한 회수 직후 동일 질문 → 즉시 제외")

        # ③ 소스 버전 갱신 → 재인덱싱 → 새 내용으로 검색.
        r = await c.post(
            f"/api/files/{doc_id}/upload",
            headers=alice_h,
            data={"base_version": "1"},
            files={"file": ("report.txt", DOC_V2.encode(), "text/plain")},
        )
        assert r.status_code == 201 and r.json()["current_version"] == 2, r.text
        await _index(doc_id)
        _, citations_v2, _ = await _ask(c, alice_h, s_alice, "물류 비용 절감 계획 알려줘")
        assert doc_id in _cited_file_ids(citations_v2), citations_v2
        assert all(ci["version"] == 2 for ci in citations_v2 if ci["file_id"] == doc_id)
        assert any("버전2" in ci["snippet"] for ci in citations_v2), citations_v2
        _ok("③ 버전 갱신 → 재인덱싱 → 새 내용(버전2, version=2)으로 검색")

        # 세션 목록/상세(citations 포함)/소유자 격리/삭제.
        r = await c.get("/api/chat/sessions", headers=alice_h)
        assert r.status_code == 200 and any(s["id"] == s_alice for s in r.json()), r.text
        r = await c.get(f"/api/chat/sessions/{s_alice}", headers=alice_h)
        assert r.status_code == 200, r.text
        detail = r.json()
        roles = [m["role"] for m in detail["messages"]]
        assert roles.count("user") >= 1 and roles.count("assistant") >= 1, roles
        assert any(m["citations"] for m in detail["messages"] if m["role"] == "assistant")
        assert detail["title"], "첫 질문으로 제목이 자동 설정돼야 한다"
        _ok("세션 목록/상세(citations 포함)/자동 제목")

        # 소유자 격리 — bob 은 alice 세션 접근/삭제 불가(404).
        r = await c.get(f"/api/chat/sessions/{s_alice}", headers=bob_h)
        assert r.status_code == 404, r.text
        r = await c.request("DELETE", f"/api/chat/sessions/{s_alice}", headers=bob_h)
        assert r.status_code == 404, r.text
        _ok("소유자 격리 — 타인 세션 조회/삭제 404")

        # 삭제 — 소유자.
        r = await c.request("DELETE", f"/api/chat/sessions/{s_bob}", headers=bob_h)
        assert r.status_code == 204, r.text
        r = await c.get(f"/api/chat/sessions/{s_bob}", headers=bob_h)
        assert r.status_code == 404, r.text
        _ok("세션 삭제(소유자)")

    await engine.dispose()
    await redis_client.aclose()
    print("\n챗봇 통합 시나리오 전체 통과.")


def main() -> int:
    try:
        asyncio.run(scenario())
    except AssertionError as exc:
        print(f"\n[FAIL] {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
