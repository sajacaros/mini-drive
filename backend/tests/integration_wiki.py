"""전사 단일 위키 통합 검증 (실제 postgres[pgvector]+redis+minio 필요, pytest 미수집, Phase 7-4).

위키 v2 재설계(wiki-v2) 시나리오 — "출판 동의(체크) = 전사 출판":
  - 부트스트랩 멱등성(동시 체크 레이스) → Wiki 폴더 1개.
  - 비소유자 체크 403 / 소유자 체크 201 → wiki_sources·잡 생성.
  - wiki_ingest 직접 호출 → 페이지 드라이브 파일 생성 + index.md/log.md 부기 + 페이지 청크 임베딩.
  - 권한 특례(D4): carol(비권한자)·admin 이 위키 페이지 preview 200 / **원본 파일은 미노출(404)**.
  - 폴더 체크 시 타인 소유 파일 제외(D2) — bob 폴더에 carol 이 만든 파일은 컴파일 제외.
  - 챗 wiki_scope: 위키 페이지는 모두 검색 / 원본 스니펫은 권한자만.
  - 체크 해제(D5) → log.md 기록, 페이지 잔존. 비소유자 해제 403.
  - Lint → 소스 버전 갱신 stale 리포트(로그인 사용자 누구나).

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
from app.models import File, FileChunk, User
from app.services import files as files_service
from app.services import indexing as indexing_service
from app.services import permissions as permissions_service
from app.services import wiki as wiki_service
from app.services.chat_llm import get_chat_provider
from app.services.embeddings import get_embedding_provider
from app.services.storage import storage_service
from app.services.wiki import WIKI_SYSTEM_EMAIL, WIKI_FOLDER_NAME
from tests._bootstrap import register_active, setup_admin
from tests._dbreset import stamp_alembic_head

BOB = {"email": "bob@example.com", "password": "Passw0rd!", "display_name": "Bob"}
CAROL = {"email": "carol@example.com", "password": "Passw0rd!", "display_name": "Carol"}

DOC = "\n".join(
    f"문단 {i}: 사내 보안 정책 문서. 비밀번호 규정과 접근 통제 원칙을 다룬다." for i in range(80)
)
DOC_V2 = "\n".join(
    f"문단 {i}: 갱신된 보안 정책. 2단계 인증 의무화를 추가했다." for i in range(80)
)
CAROL_DOC = "\n".join(
    f"문단 {i}: carol 이 만든 별개 문서. 위키 컴파일에서 제외돼야 한다." for i in range(40)
)
QUESTION = "사내 보안 정책의 접근 통제 원칙 알려줘"


def _ok(msg: str) -> None:
    print(f"  [PASS] {msg}")


async def _reset() -> None:
    permissions_service.reset_wiki_root_cache()
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


async def _ingest(file_id: int) -> int:
    """워커 wiki_ingest 와 동일 경로(ingest_source)를 fake 챗 프로바이더로 직접 호출한다."""
    provider = get_chat_provider(settings)
    async with SessionFactory() as s:
        summary = await wiki_service.ingest_source(
            s, storage_service, provider, settings, file_id
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
            await s.execute(select(FileChunk).where(FileChunk.file_id == file_id))
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
    c: httpx.AsyncClient, h: dict[str, str], wiki_scope: bool = False
) -> httpx.Response:
    return await c.post(
        "/api/chat/sessions", headers=h, json={"wiki_scope": wiki_scope}
    )


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


async def _count_wiki_folders() -> int:
    """시스템 사용자 root 하위의 활성 `Wiki` 폴더 개수(부트스트랩 멱등성 검증)."""
    async with SessionFactory() as s:
        sys_user = (
            await s.execute(select(User).where(User.email == WIKI_SYSTEM_EMAIL))
        ).scalar_one_or_none()
        if sys_user is None:
            return 0
        root = (
            await s.execute(
                select(File).where(
                    File.user_id == sys_user.id,
                    File.parent_folder_id.is_(None),
                    File.is_folder.is_(True),
                )
            )
        ).scalar_one()
        rows = (
            await s.execute(
                select(File.id).where(
                    File.parent_folder_id == root.id,
                    File.name == WIKI_FOLDER_NAME,
                    File.is_deleted.is_(False),
                )
            )
        ).scalars().all()
        return len(rows)


async def scenario() -> None:
    assert settings.embedding_provider == "fake", "EMBEDDING_PROVIDER=fake 로 실행하세요."
    assert settings.chat_provider == "fake", "CHAT_PROVIDER=fake 로 실행하세요."
    await _reset()

    # 부트스트랩 멱등성(동시 체크 레이스) — 어떤 API 도 부트스트랩하기 전에 먼저 검증한다.
    async def _bs() -> tuple[int, int]:
        async with SessionFactory() as s:
            return await wiki_service.bootstrap_wiki(s, storage_service)

    r1, r2 = await asyncio.gather(_bs(), _bs())
    assert r1 == r2, ("동시 부트스트랩은 동일 (root, system_user) 를 내야 한다", r1, r2)
    assert await _count_wiki_folders() == 1, "Wiki 폴더는 정확히 1개여야 한다(레이스 방어)"
    _ok("부트스트랩 멱등성 — 동시 체크 레이스에도 Wiki 폴더 1개")

    transport = ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test", timeout=60) as c:
        admin_h, code = await setup_admin(c)
        bob_h, bob_id = await register_active(c, code, BOB)
        carol_h, carol_id = await register_active(c, code, CAROL)
        _ok("셋업 + bob/carol 코드 가입")

        # 위키 루트 폴더 id — 개요 API 로 확인(로그인 사용자 누구나).
        r = await c.get("/api/wiki", headers=carol_h)
        assert r.status_code == 200, r.text
        root_id = r.json()["root_folder_id"]

        # ① 비소유자 체크 403 / 소유자 체크 201 → wiki_sources·잡 생성.
        priv_id = await _upload(c, bob_h, "bobprivate.txt", DOC.encode())
        r = await c.post("/api/wiki/sources", headers=carol_h, json={"file_id": priv_id})
        assert r.status_code == 403, ("비소유자는 남의 파일을 출판할 수 없다", r.text)
        r = await c.post("/api/wiki/sources", headers=admin_h, json={"file_id": priv_id})
        assert r.status_code == 403, ("admin 도 타인 콘텐츠 출판 불가(D1)", r.text)
        r = await c.post("/api/wiki/sources", headers=bob_h, json={"file_id": priv_id})
        assert r.status_code == 201 and r.json()["file_id"] == priv_id, r.text
        assert r.json()["added_by"] == bob_id, r.json()
        r = await c.get("/api/wiki/jobs", headers=carol_h)
        assert any(j["kind"] == "ingest" and j["file_id"] == priv_id for j in r.json()), r.text
        _ok("① 비소유자 체크 403(admin 포함) / 소유자 체크 201 → 소스·잡 생성")

        # 파일 목록/단건 응답의 wiki_shared 파생 필드.
        r = await c.get(f"/api/files/{priv_id}", headers=bob_h)
        assert r.json()["wiki_shared"] is True, r.json()
        _ok("wiki_shared 파생 필드 — 체크된 파일 True")

        # ② wiki_ingest → 페이지 생성 + index/log 부기 + 페이지 청크 임베딩.
        assert await _ingest(priv_id) == 1
        page_id = await _find_page_id(root_id, "bobprivate.md")
        assert page_id is not None, "bobprivate.md 페이지가 생성돼야 한다"
        r = await c.get("/api/wiki", headers=bob_h)
        ov = r.json()
        assert any("bobprivate.md" in e for e in ov["index_entries"]), ov["index_entries"]
        assert any("ingest" in ln for ln in ov["recent_log"]), ov["recent_log"]
        assert ov["sources"][0]["status"] == "indexed", ov["sources"]
        await _index(page_id)
        page_chunks = await _chunks(page_id)
        assert page_chunks and all(len(ch.embedding) == 4096 for ch in page_chunks)
        _ok("② wiki_ingest → 페이지 생성 + index/log 부기 + 페이지 청크 임베딩")

        # ③ 권한 특례(D4) — carol/admin 위키 페이지 preview 200 / 원본 파일 미노출(404).
        r = await c.get(f"/api/files/{page_id}/preview", headers=carol_h)
        assert r.status_code == 200, ("비권한자도 위키 페이지는 읽는다(특례)", r.text)
        r = await c.get(f"/api/files/{page_id}/preview", headers=admin_h)
        assert r.status_code == 200, ("admin 도 로그인 사용자로서 위키 페이지 읽기", r.text)
        r = await c.get(f"/api/files/{priv_id}/preview", headers=carol_h)
        assert r.status_code == 404, ("원본은 특례 대상 아님(권한 유지)", r.text)
        r = await c.get(f"/api/files/{priv_id}/preview", headers=admin_h)
        assert r.status_code == 404, ("admin 도 원본 내용 접근 불가", r.text)
        _ok("③ 특례 READ — 비권한자·admin 위키 페이지 200 / 원본 404 유지")

        # ④ 폴더 체크 시 타인 소유 파일 제외(D2).
        r = await c.post("/api/groups", headers=bob_h, json={"name": "정책팀"})
        group_id = r.json()["id"]
        r = await c.post(
            f"/api/groups/{group_id}/members",
            headers=bob_h,
            json={"user_id": carol_id, "role": "member"},
        )
        assert r.status_code == 201, r.text
        r = await c.post("/api/files", headers=bob_h, json={"name": "정책함"})
        folder_id = r.json()["id"]
        bobfolder_id = await _upload(c, bob_h, "bobfolder.txt", DOC.encode(), parent_id=folder_id)
        r = await c.post(
            f"/api/files/{folder_id}/permissions",
            headers=bob_h,
            json={"group_id": group_id, "permission": "write", "inherit_to_children": True},
        )
        assert r.status_code == 201, r.text
        # carol 이 bob 폴더에 자기 소유 파일을 만든다(그룹 write) — 컴파일 제외 대상.
        carolfolder_id = await _upload(
            c, carol_h, "carolfolder.txt", CAROL_DOC.encode(), parent_id=folder_id
        )
        r = await c.post("/api/wiki/sources", headers=bob_h, json={"file_id": folder_id})
        assert r.status_code == 201, r.text
        compiled = await _ingest(folder_id)
        assert compiled == 1, ("bob 소유 파일 1개만 컴파일", compiled)
        assert await _find_page_id(root_id, "bobfolder.md") is not None
        assert await _find_page_id(root_id, "carolfolder.md") is None, "타인 파일은 제외"
        _ok("④ 폴더 체크 — bob 소유 파일만 컴파일, carol 파일 제외(D2)")

        # ⑤ 챗 wiki_scope — 위키 페이지는 모두 검색 / 원본 스니펫은 권한자만.
        folder_page_id = await _find_page_id(root_id, "bobfolder.md")
        await _index(priv_id)
        await _index(folder_page_id)
        # bob(권한자) 위키 범위 세션 — 인용을 낸다.
        r = await _new_session(c, bob_h, wiki_scope=True)
        assert r.status_code == 201 and r.json()["wiki_scope"] is True, r.text
        s_bob = r.json()["id"]
        cites_bob, done_bob = await _ask(c, bob_h, s_bob, QUESTION)
        assert cites_bob, "위키 범위 검색이 인용을 내야 한다"
        assert done_bob.get("message_id"), done_bob
        # carol(비권한자) 위키 범위 세션 — 위키 페이지는 인용, 원본 bobprivate 는 제외.
        r = await _new_session(c, carol_h, wiki_scope=True)
        s_carol = r.json()["id"]
        cites_carol, _ = await _ask(c, carol_h, s_carol, QUESTION)
        carol_ids = _cited_ids(cites_carol)
        assert page_id in carol_ids or folder_page_id in carol_ids, (
            "비권한자도 위키 페이지는 검색된다", carol_ids
        )
        # priv_id 는 carol 이 접근 권한이 없는 원본(그룹 공유 없음) — 위키 범위여도 스니펫 미노출.
        assert priv_id not in carol_ids, ("원본 스니펫은 권한자만", carol_ids)
        _ok("⑤ 챗 wiki_scope — 위키 페이지는 모두 검색 / 미권한 원본은 미노출")

        # ⑥ 체크 해제(D5) — log.md 기록, 페이지 잔존. 비소유자 해제 403.
        r = await c.request("DELETE", f"/api/wiki/sources/{priv_id}", headers=carol_h)
        assert r.status_code == 403, ("비소유자 해제 거부", r.text)
        r = await c.request("DELETE", f"/api/wiki/sources/{priv_id}", headers=bob_h)
        assert r.status_code == 204, r.text
        assert await _find_page_id(root_id, "bobprivate.md") is not None, "페이지 잔존(D5)"
        r = await c.get("/api/wiki", headers=bob_h)
        assert any("source-removed" in ln for ln in r.json()["recent_log"]), r.json()["recent_log"]
        assert all(s["file_id"] != priv_id for s in r.json()["sources"]), "소스 목록에서 제거"
        _ok("⑥ 체크 해제 — 소유자만, log 기록, 페이지 잔존")

        # ⑦ Lint — 소스(폴더) 버전 갱신 후 stale 리포트(로그인 사용자 누구나).
        r = await c.post(
            f"/api/files/{bobfolder_id}/upload",
            headers=bob_h,
            data={"base_version": "1"},
            files={"file": ("bobfolder.txt", DOC_V2.encode(), "text/plain")},
        )
        assert r.status_code == 201 and r.json()["current_version"] == 2, r.text
        # 폴더 소스는 last_ingested_version 이 폴더 버전이라 파일 stale 를 직접 못 보므로, 파일도
        # 개별 소스로 등록해 stale 를 확인한다(로그인 사용자 누구나 Lint 실행 — carol).
        r = await c.post("/api/wiki/sources", headers=bob_h, json={"file_id": bobfolder_id})
        assert r.status_code == 201, r.text
        await _ingest(bobfolder_id)
        r = await c.post(
            f"/api/files/{bobfolder_id}/upload",
            headers=bob_h,
            data={"base_version": "2"},
            files={"file": ("bobfolder.txt", DOC.encode(), "text/plain")},
        )
        assert r.status_code == 201 and r.json()["current_version"] == 3, r.text
        r = await c.post("/api/wiki/lint", headers=carol_h)
        assert r.status_code == 200, r.text
        assert any("stale" in msg for msg in r.json()["reports"]), r.json()
        _ok("⑦ Lint(누구나 실행) → 소스 버전 갱신 stale 리포트")

        # ⑧ 답변 승격(누구나) → 위키 페이지 생성. 타인 메시지 승격은 404.
        r = await c.post(
            "/api/wiki/promote",
            headers=bob_h,
            json={"message_id": done_bob["message_id"], "title": "승격된 답변"},
        )
        assert r.status_code == 200, r.text
        promoted_id = r.json()["file_id"]
        assert await _find_page_id(root_id, "승격된 답변.md") == promoted_id
        r = await c.post(
            "/api/wiki/promote",
            headers=carol_h,
            json={"message_id": done_bob["message_id"], "title": "탈취"},
        )
        assert r.status_code == 404, ("타인 세션 메시지 승격 불가", r.text)
        _ok("⑧ 답변 승격(누구나) → 페이지 생성, 타인 메시지 승격 404")

    await engine.dispose()
    await redis_client.aclose()
    print("\n위키 v2 통합 시나리오 전체 통과.")


def main() -> int:
    try:
        asyncio.run(scenario())
    except AssertionError as exc:
        print(f"\n[FAIL] {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
