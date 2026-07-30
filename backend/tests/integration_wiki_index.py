"""위키 인덱싱 파이프라인 통합 검증 (spec/wiki-index.md).

**실제 사내 vLLM 을 호출한다.** WIKI_LLM_BASE_URL 이 닿지 않으면 요약이 본문 앞부분으로
대체되므로 트리 구조 검증은 그대로 유효하지만, 요약 품질은 확인할 수 없다.

검증 축:
  1. 토글 ON → 큐 적재 → 워커가 트리를 만든다 (md).
  2. HTML 도 같은 경로로 인덱싱된다 (변환 후 md_to_tree).
  3. 버전업 → 재인덱싱 예약 → 트리의 version 이 따라 올라간다.
  4. 토글 OFF → 대기 작업이 취소된다.
  5. 인덱싱은 멱등하다 — 두 번 돌려도 결과가 같다.
  6. 전사 위키 불변식 — 켜면 별도 공개 스위치 없이 타인도 보고, `@전사 read` 를 회수하면
     위키도 함께 꺼진다. 목록은 사람마다 달라지지 않는다.
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


def _count_tree(nodes: list[dict]) -> int:
    """카탈로그 응답의 절 수 — 목록의 node_count 와 같아야 한다(같은 트리에서 나온다)."""
    return sum(1 + _count_tree(n["nodes"]) for n in nodes)


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
        # 위치 — 소유자 드라이브 안에서의 폴더 경로. 루트 이름('root')은 들어가지 않는다.
        assert docs["deploy.md"]["location"] == "문서함", docs
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

        # 6. 전사 위키 — 켜는 것이 곧 전사 공개다. bob 은 아무 권한을 받지 않았는데도
        #    켜진 문서를 목록에서 보고 카탈로그를 열 수 있어야 한다. 별도의 공개 스위치를
        #    거쳐야 보인다면, 실측에서 467건 중 2건만 공개됐던 그 상태로 되돌아간다
        #    (spec/wiki-index.md 「왜 스위치가 하나인가」).
        r = await c.get("/api/wiki/documents", headers=bob_h)
        assert r.status_code == 200, r.text
        # 목록은 상태와 무관하게 **위키 문서 전부**를 보여준다 — 사용자가 무엇이 왜
        # 빠졌는지(disabled) 알 수 있어야 하기 때문이다. 질의 대상은 ready/stale 뿐이다.
        items = {d["name"]: d["status"] for d in r.json()["items"]}
        # 5건이다 — 예전에는 권한 필터 때문에 bob 에게 4건이었다(꺼진 폴더로 옮겨진
        # 이동대상.md 가 빠졌다). 목록이 사람마다 달라지지 않는 것이 이 개정의 결과다.
        assert r.json()["total"] == 5, r.text
        assert items["deploy.md"] == "ready" and items["휴지통대상.md"] == "ready", items
        assert items["ops.html"] == "disabled" and items["신규.txt"] == "disabled", items
        assert items["이동대상.md"] == "disabled", items
        _ok(f"전사 위키 — 공개 스위치 없이 타인도 5건을 본다 {items}")

        # 6-1. 위치도 사람마다 다르지 않다. bob 은 이 문서들에 아무 권한이 없고 자기 드라이브에
        #      있지도 않은데, 드라이브 목록처럼 "내 드라이브" 접두사를 붙이면 그 자리에서
        #      거짓말이 된다. 소유 여부로 접두사를 가르면 같은 문서가 사람마다 다른 위치로
        #      보이고, 그러면 "방금 켠 그 문서"를 위치로 지목할 수 없다.
        # 소유자는 **파일 주인**이지 요청자가 아니다 — bob 이 물었는데 bob 이 나오면
        # 조인이 요청자에 걸린 것이고, 그러면 목록이 사람마다 달라진다.
        owners = {d["name"]: d["owner_display_name"] for d in r.json()["items"]}
        assert owners["deploy.md"], owners
        assert owners["deploy.md"] != BOB["display_name"], owners
        locs = {d["name"]: d["location"] for d in r.json()["items"]}
        assert not any(loc.startswith("내 드라이브") for loc in locs.values()), locs
        assert locs["deploy.md"] == "문서함", locs
        # 중첩 폴더는 최상위부터 이어 붙인다(옮겨진 문서라 경로도 따라 바뀌어 있어야 한다).
        assert locs["이동대상.md"] == "위키없는폴더 / 하위", locs
        _ok(f"위치 표기 — 보는 사람과 무관하게 같은 경로 {locs['이동대상.md']!r}")

        # 업로드 경고의 재료 — 목록 응답이 "이 폴더가 전사 위키인가"를 실어야 한다.
        # 이 플래그가 항목이 아니라 목록에 붙는 이유는 경고를 봐야 하는 사람이 write 권한자라서다
        # (그에게는 위키 상태 API 가 404 다). 그래서 **bob 으로** 확인한다.
        r = await c.get("/api/files", headers=alice_h, params={"parentId": folder})
        assert r.status_code == 200 and r.json()["wiki_enabled"] is True, r.text
        r = await c.get("/api/files", headers=alice_h, params={"parentId": off_folder})
        assert r.status_code == 200 and r.json()["wiki_enabled"] is False, r.text
        # 루트는 파일 행이 없어 항상 꺼짐이다 — 상속 판정이 조상 없이도 안전해야 한다.
        r = await c.get("/api/files", headers=bob_h)
        assert r.status_code == 200 and r.json()["wiki_enabled"] is False, r.text
        _ok("업로드 경고 — 목록 응답의 wiki_enabled 가 폴더별로 갈린다 (루트는 꺼짐)")

        # 불변식: 인덱싱 켜짐 ⟺ 그 파일에 @전사 read 직접 부여. 폴더에 상속 부여가 아니라
        # 대상 파일마다 걸려야 한다 — 폴더에 걸면 인덱싱 대상이 아닌 파일까지 공개된다.
        async with SessionFactory() as session:
            assert await wiki_service.is_public(session, md_id), "켠 문서가 비공개다"
            assert not await wiki_service.is_public(session, folder), "폴더에 공개가 걸렸다"
            # neo 는 .txt 로 이름이 바뀌어 색인 대상 밖이 된 파일이다.
            assert not await wiki_service.is_public(session, neo), "대상 아닌 파일이 공개됐다"
        _ok("불변식 — 공개는 대상 파일에만, 폴더·비대상 파일에는 걸리지 않는다")

        # 반대 방향 — 권한 화면에서 @전사 read 를 회수하면 위키도 함께 꺼져야 한다.
        # 한쪽만 풀리면 카탈로그·질의가 권한 판정을 생략하는 근거가 그 문서에서 무너진다.
        async with SessionFactory() as session:
            all_users_gid = await wiki_service.get_all_users_group_id(session)
        r = await c.delete(
            f"/api/files/{md_id}/permissions/{all_users_gid}", headers=alice_h
        )
        assert r.status_code == 204, r.text
        async with SessionFactory() as session:
            doc = await wiki_service.get_document(session, md_id)
            assert doc.status == "disabled", doc.status
            assert not await wiki_service.is_public(session, md_id)
        _ok("불변식 — @전사 read 회수가 위키를 함께 끈다")

        # 다시 켜서 이후 단계(카탈로그)가 쓸 상태로 되돌린다. 트리가 그대로라 비용 0 이다.
        r = await c.put(
            f"/api/files/{md_id}/wiki", headers=alice_h, json={"enabled": True}
        )
        assert r.status_code == 200, r.text
        async with SessionFactory() as session:
            assert (await wiki_service.get_document(session, md_id)).status == "ready"
            assert await wiki_service.is_public(session, md_id)
        _ok("다시 켜면 트리 그대로 ready + 공개 복구")

        # 7. 카탈로그 — 목록에서 문서를 눌렀을 때 보이는 절 트리. 질의가 절을 고를 때 보는 것과
        #    같은 것(title+summary)을 보여줘야 한다.
        r2 = await c.get(f"/api/wiki/documents/{md_id}", headers=bob_h)
        assert r2.status_code == 200, r2.text
        catalog = r2.json()
        assert catalog["status"] == "ready" and catalog["nodes"], catalog
        # 상세도 목록과 같은 위치를 말해야 한다 — 목록에서 눌러 들어온 화면이라 어긋나면 안 된다.
        assert catalog["location"] == "문서함", catalog
        root = catalog["nodes"][0]
        assert root["node_id"] and root["title"] and root["line_num"] >= 1, root
        # 노드 수는 목록의 node_count 와 같은 트리에서 나온다 — 두 화면이 어긋나면 안 된다.
        assert catalog["node_count"] == _count_tree(catalog["nodes"]), catalog
        _ok(f"카탈로그 — 절 {catalog['node_count']}개, 최상위 '{root['title']}'")

        # 위키 문서가 아닌 것(폴더)은 카탈로그도 없다 — 목록에 없는 것은 id 로도 열리지 않는다.
        r2 = await c.get(f"/api/wiki/documents/{folder}", headers=alice_h)
        assert r2.status_code == 404, r2.text
        _ok("카탈로그 — 위키 문서가 아닌 대상은 404")

    await engine.dispose()
    print("\n위키 인덱싱 파이프라인 통합 시나리오 전체 통과.")


if __name__ == "__main__":
    asyncio.run(main())
