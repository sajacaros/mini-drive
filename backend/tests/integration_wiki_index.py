"""위키 인덱싱 파이프라인 통합 검증 (spec/wiki-index.md).

**실제 사내 vLLM 을 호출한다.** WIKI_LLM_BASE_URL 이 닿지 않으면 요약이 본문 앞부분으로
대체되므로 트리 구조 검증은 그대로 유효하지만, 요약 품질은 확인할 수 없다.

검증 축:
  1. 토글 ON → 큐 적재 → 워커가 트리를 만든다 (md).
  2. HTML 도 같은 경로로 인덱싱된다 (변환 후 md_to_tree).
  3. 버전업 → 재인덱싱 예약 → 트리의 version 이 따라 올라간다.
  4. 토글 OFF → 대기 작업이 취소된다.
  5. 인덱싱은 멱등하다 — 두 번 돌려도 결과가 같다.
  6. 문서 목록에 권한이 걸린다 — 접근 못 하는 사람에게는 보이지 않는다.
"""

from __future__ import annotations

import asyncio

import httpx

from app.core.database import Base, SessionFactory, engine
from app.core.redis import redis_client
from app.main import app
from app.services import wiki as wiki_service
from app.services import wiki_indexer, wiki_queue
from app.services.storage import get_storage, storage_service
from tests._bootstrap import register_active, setup_admin
from tests._dbreset import stamp_alembic_head

BOB = {"email": "bob@example.com", "password": "Passw0rd!", "display_name": "Bob"}

MD_V1 = """# 배포 가이드

이 문서는 배포 절차를 설명한다.

## 사전 준비

배포 전에 확인할 것들을 정리한다. 스테이징에서 스모크 테스트를 돌리고, 마이그레이션이
있으면 롤백 계획을 먼저 세운다. 비밀 값은 환경변수로만 주입하고 이미지에 굽지 않는다.

## 롤백

문제가 생기면 직전 태그로 되돌린다. 데이터 마이그레이션이 섞여 있으면 되돌리기 전에
스키마 호환성을 먼저 확인해야 한다.
"""

MD_V2 = MD_V1 + """
## 모니터링

배포 직후 5분간 에러율과 지연을 지켜본다. 임계값을 넘으면 자동으로 이전 버전으로 돌린다.
"""

HTML_DOC = """<!DOCTYPE html><html><head><title>운영 메모</title>
<style>body{color:red}</style></head><body>
<h1>운영 메모</h1><p>운영 중 알아두면 좋은 것들.</p>
<h2>로그 확인</h2><p>구조화 로깅이라 request_id 로 추적한다.</p>
</body></html>"""


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


async def _upload(
    c: httpx.AsyncClient, h: dict[str, str], name: str, parent: int, body: bytes
) -> int:
    r = await c.post(
        "/api/files/upload",
        headers=h,
        files={"file": (name, body, "application/octet-stream")},
        data={"parent_id": str(parent)},
    )
    assert r.status_code == 201, r.text
    return r.json()["id"]


async def _drain() -> dict[str, int]:
    """디바운스를 무시하고 큐를 즉시 비운다 (테스트가 10초를 기다리지 않도록)."""
    pending = await redis_client.zrange(wiki_queue.QUEUE_KEY, 0, -1)
    if pending:
        await redis_client.zadd(wiki_queue.QUEUE_KEY, {m: 0 for m in pending})
    async with SessionFactory() as session:
        return await wiki_indexer.run_once(session, get_storage(), limit=20)


async def main() -> None:  # noqa: PLR0915 - 순차 시나리오
    await _reset()

    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as c:
        alice_h, code = await setup_admin(c)
        bob_h, _ = await register_active(c, code, BOB)

        r = await c.post("/api/files", headers=alice_h, json={"name": "문서함"})
        folder = r.json()["id"]
        md_id = await _upload(c, alice_h, "deploy.md", folder, MD_V1.encode())
        html_id = await _upload(c, alice_h, "ops.html", folder, HTML_DOC.encode())

        # 1. 토글 ON → 큐 적재
        for fid in (md_id, html_id):
            r = await c.put(f"/api/files/{fid}/wiki", headers=alice_h, json={"enabled": True})
            assert r.status_code == 200, r.text
        assert await wiki_queue.pending_count() == 2, "큐에 2건이 있어야 한다"
        _ok("토글 ON → 큐 적재 2건")

        counts = await _drain()
        assert counts.get("ready") == 2, counts
        _ok(f"워커 처리 완료 — {counts}")

        # 트리 검증 (md)
        r = await c.get("/api/wiki/documents", headers=alice_h)
        docs = {d["name"]: d for d in r.json()["items"]}
        assert r.json()["total"] == 2, r.text
        assert docs["deploy.md"]["status"] == "ready", docs
        assert docs["deploy.md"]["node_count"] == 3, docs  # 배포 가이드 + 사전 준비 + 롤백
        _ok(f"md 트리 — 노드 {docs['deploy.md']['node_count']}개, status=ready")

        # 2. HTML 도 같은 경로로 (style 은 걷히고 h1/h2 가 노드가 된다)
        assert docs["ops.html"]["node_count"] == 2, docs
        _ok(f"html 트리 — 노드 {docs['ops.html']['node_count']}개")

        async with SessionFactory() as session:
            doc = await wiki_service.get_document(session, md_id)
            titles = [n["title"] for n in doc.tree["structure"][0]["nodes"]]
            assert titles == ["사전 준비", "롤백"], titles
            assert "text" not in doc.tree["structure"][0], "본문은 트리에 담지 않는다"
            assert doc.tree["structure"][0].get("summary"), "요약이 있어야 한다"
            v1_version = doc.version
        _ok("트리 구조 — 하위 절 제목 보존, 본문 미포함, 요약 생성")

        # 5. 멱등 — 다시 돌려도 같은 결과 (이미 최신이라 재작업하지 않는다)
        await wiki_queue.enqueue(md_id, delay=0)
        counts = await _drain()
        assert counts.get("ready") == 1, counts
        async with SessionFactory() as session:
            doc = await wiki_service.get_document(session, md_id)
            assert doc.version == v1_version, "버전이 그대로여야 한다"
        _ok("멱등 — 최신이면 재인덱싱하지 않는다")

        # 3. 버전업 → 재인덱싱 → version 추적
        r = await c.post(
            f"/api/files/{md_id}/upload",
            headers=alice_h,
            files={"file": ("deploy.md", MD_V2.encode(), "text/markdown")},
        )
        assert r.status_code == 201, r.text
        assert await wiki_queue.pending_count() >= 1, "버전업이 재인덱싱을 예약해야 한다"
        counts = await _drain()
        assert counts.get("ready") == 1, counts
        async with SessionFactory() as session:
            doc = await wiki_service.get_document(session, md_id)
            titles = [n["title"] for n in doc.tree["structure"][0]["nodes"]]
            assert titles == ["사전 준비", "롤백", "모니터링"], titles
            assert doc.version > v1_version, (doc.version, v1_version)
        _ok(f"버전업 → 재인덱싱 (v{v1_version} → v{doc.version}, 새 절 반영)")

        # 4. 토글 OFF → 대기 작업 취소
        await wiki_queue.enqueue(html_id, delay=0)
        r = await c.put(f"/api/files/{html_id}/wiki", headers=alice_h, json={"enabled": False})
        assert r.status_code == 200, r.text
        assert await wiki_queue.pending_count() == 0, "끄면 대기 작업이 취소돼야 한다"
        _ok("토글 OFF → 대기 작업 취소")

        # 7. 위키가 켜진 폴더에 **신규 업로드** → 자동 색인 (폴더 토글 UI 의 약속)
        r = await c.put(
            f"/api/files/{folder}/wiki", headers=alice_h, json={"enabled": True}
        )
        assert r.status_code == 200, r.text
        neo = await _upload(c, alice_h, "신규.md", folder, MD_V1.encode())
        r = await c.get(f"/api/files/{neo}/wiki", headers=alice_h)
        body = r.json()
        assert body["enabled"] is True and body["explicit"] is False, body
        assert body["status"] == "pending", body
        counts = await _drain()
        r = await c.get(f"/api/files/{neo}/wiki", headers=alice_h)
        assert r.json()["status"] == "ready", r.text
        _ok("위키 폴더에 신규 업로드 → 상속 + 자동 색인")

        # 폴더를 켜도 **명시적으로 끈 파일은 되살아나지 않는다** — 소유자 탈출구의 의미.
        r = await c.get(f"/api/files/{html_id}/wiki", headers=alice_h)
        # API 는 사용자 관점의 "off" 를 준다. DB 행은 disabled 로 남아 트리를 보관하되
        # 질의 대상에서는 빠진다 — 이 분리를 아래에서 각각 확인한다.
        assert r.json()["enabled"] is False, r.text
        assert r.json()["status"] == "off", r.text
        async with SessionFactory() as session:
            doc = await wiki_service.get_document(session, html_id)
            assert doc.status == "disabled" and doc.tree is not None, doc.status
        r = await c.get(f"/api/files/{folder}/wiki", headers=alice_h)
        scope = r.json()["folder_scope"]
        assert scope["skipped_by_optout"] == 1, scope
        _ok("폴더 켜기가 명시 OFF 파일을 되살리지 않는다 (scope 에서도 제외)")

        # 8. 끄면 **질의 대상에서 즉시** 빠진다 (차단은 즉시, 삭제는 유예)
        async with SessionFactory() as session:
            doc = await wiki_service.get_document(session, neo)
            assert doc.status == "ready", doc.status
        r = await c.put(f"/api/files/{neo}/wiki", headers=alice_h, json={"enabled": False})
        assert r.status_code == 200, r.text
        async with SessionFactory() as session:
            doc = await wiki_service.get_document(session, neo)
            # 트리는 유예 동안 남기되(재켜기 비용 0), 상태는 즉시 내려 검색에서 뺀다.
            assert doc.status == "disabled", doc.status
            assert doc.tree is not None, "트리는 유예 동안 보관한다"
        _ok("끄기 → status=disabled (트리는 보관, 질의 대상에서 즉시 제외)")

        # 다시 켜면 재색인 없이 ready 로 복구된다 — 큐를 돌리지 않고 확인한다.
        r = await c.put(f"/api/files/{neo}/wiki", headers=alice_h, json={"enabled": True})
        assert r.status_code == 200, r.text
        async with SessionFactory() as session:
            doc = await wiki_service.get_document(session, neo)
            assert doc.status == "ready", doc.status
        _ok("다시 켜기 → 재색인 없이 ready 복구 (비용 0)")

        # 9. 유효 상태를 바꾸는 나머지 경로 — 위치(이동)와 이름(확장자)
        #    판정만 맞고 wiki_documents 가 따라가지 않으면 꺼진 문서가 계속 검색된다.
        off_folder = (
            await c.post("/api/files", headers=alice_h, json={"name": "위키없는폴더"})
        ).json()["id"]
        sub = (
            await c.post(
                "/api/files", headers=alice_h,
                json={"name": "하위", "parent_id": folder},
            )
        ).json()["id"]
        moved = await _upload(c, alice_h, "이동대상.md", sub, MD_V1.encode())
        await _drain()
        async with SessionFactory() as session:
            assert (await wiki_service.get_document(session, moved)).status == "ready"

        r = await c.post(
            f"/api/files/{sub}/move", headers=alice_h, json={"parent_id": off_folder}
        )
        assert r.status_code == 200, r.text
        async with SessionFactory() as session:
            # 폴더를 옮기면 하위 전체의 상속이 한꺼번에 바뀐다 — 자기 자신만 보면 하위가
            # 낡은 상태로 남아 꺼진 폴더의 문서가 계속 검색된다.
            assert (await wiki_service.get_document(session, moved)).status == "disabled"
        _ok("폴더 이동(ON→OFF) → 하위 문서까지 질의 대상에서 제외")

        r = await c.put(
            f"/api/files/{neo}", headers=alice_h, json={"name": "신규.txt"}
        )
        assert r.status_code == 200, r.text
        async with SessionFactory() as session:
            assert (await wiki_service.get_document(session, neo)).status == "disabled"
        _ok("이름 변경(.md → .txt) → 색인 대상 밖이 되어 제외")

        # 10. 휴지통 ↔ 복구 — 복구는 **그 시점의** 위키 여부를 따라야 한다.
        #     휴지통 중 제외는 질의의 is_deleted 필터가 처리하지만, 그 사이 설정이 바뀌었으면
        #     복구 시점에 다시 판정하지 않는 한 꺼진 문서가 되살아난다.
        trash_doc = await _upload(c, alice_h, "휴지통대상.md", folder, MD_V1.encode())
        await _drain()
        async with SessionFactory() as session:
            assert (await wiki_service.get_document(session, trash_doc)).status == "ready"

        r = await c.post(f"/api/files/{trash_doc}/delete", headers=alice_h)
        assert r.status_code in (200, 204), r.text
        r = await c.put(
            f"/api/files/{folder}/wiki", headers=alice_h, json={"enabled": False}
        )
        assert r.status_code == 200, r.text
        r = await c.post(f"/api/files/{trash_doc}/restore-trash", headers=alice_h)
        assert r.status_code == 200, r.text
        async with SessionFactory() as session:
            doc = await wiki_service.get_document(session, trash_doc)
            assert doc.status == "disabled", doc.status
        _ok("휴지통 중 위키 OFF → 복구해도 질의 대상으로 돌아오지 않는다")

        r = await c.put(
            f"/api/files/{folder}/wiki", headers=alice_h, json={"enabled": True}
        )
        assert r.status_code == 200, r.text
        await _drain()
        async with SessionFactory() as session:
            doc = await wiki_service.get_document(session, trash_doc)
            assert doc.status == "ready", doc.status
        _ok("다시 켜면 복구된 문서도 질의 대상으로 돌아온다")

        # 6. 문서 목록 권한 — bob 은 접근 권한이 없어 아무것도 못 본다
        r = await c.get("/api/wiki/documents", headers=bob_h)
        assert r.status_code == 200 and r.json()["total"] == 0, r.text
        r = await c.put(
            f"/api/files/{folder}/wiki", headers=alice_h, json={"public": True}
        )
        assert r.status_code == 200, r.text
        r = await c.get("/api/wiki/documents", headers=bob_h)
        # 목록은 상태와 무관하게 **접근 가능한 위키 문서 전부**를 보여준다 — 사용자가 무엇이
        # 왜 빠졌는지(disabled) 알 수 있어야 하기 때문이다. 질의 대상은 ready/stale 뿐이다.
        items = {d["name"]: d["status"] for d in r.json()["items"]}
        assert r.json()["total"] == 4, r.text
        assert items["deploy.md"] == "ready" and items["휴지통대상.md"] == "ready", items
        assert items["ops.html"] == "disabled" and items["신규.txt"] == "disabled", items
        _ok(f"문서 목록 권한 — 전사 공개 전 0건 → 공개 후 4건 {items}")

    await engine.dispose()
    print("\n위키 인덱싱 파이프라인 통합 시나리오 전체 통과.")


if __name__ == "__main__":
    asyncio.run(main())
