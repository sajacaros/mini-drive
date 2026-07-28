"""위키 토글 API 통합 검증 (spec/wiki-index.md).

검증 축:
  1. 지원 형식(md/html)만 토글이 켜지고, 그 외는 400 + 이유.
  2. 인덱싱과 전사 공개는 **독립**이다 — (끔, 공개) 조합이 정상 동작한다.
  3. 폴더 토글은 하위로 상속되고, 파일의 명시 FALSE 가 상속을 이긴다(소유자 탈출구).
  4. 폴더 scope 는 제외 사유별 개수를 돌려준다(확인 문구용).
  5. 발행 권한 없는 사용자에게는 404 (존재 은닉).
  6. 전사 공개를 켜면 다른 사용자가 즉시 접근한다.
"""

from __future__ import annotations

import asyncio

import httpx

from app.core.database import Base, engine
from app.core.redis import redis_client
from app.main import app
from app.services.storage import storage_service
from tests._bootstrap import register_active, setup_admin
from tests._dbreset import stamp_alembic_head

BOB = {"email": "bob@example.com", "password": "Passw0rd!", "display_name": "Bob"}
MD = b"# \xec\xa0\x9c\xeb\xaa\xa9\n\n\xeb\xb3\xb8\xeb\xac\xb8.\n"


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


async def _mkfolder(c: httpx.AsyncClient, h: dict[str, str], name: str) -> int:
    r = await c.post("/api/files", headers=h, json={"name": name})
    assert r.status_code == 201, r.text
    return r.json()["id"]


async def _upload(
    c: httpx.AsyncClient, h: dict[str, str], name: str, parent: int, body: bytes = MD
) -> int:
    r = await c.post(
        "/api/files/upload",
        headers=h,
        files={"file": (name, body, "application/octet-stream")},
        data={"parent_id": str(parent)},
    )
    assert r.status_code == 201, r.text
    return r.json()["id"]


async def main() -> None:  # noqa: PLR0915 - 순차 시나리오
    await _reset()

    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as c:
        alice_h, code = await setup_admin(c)
        bob_h, _ = await register_active(c, code, BOB)

        folder = await _mkfolder(c, alice_h, "문서함")
        md_id = await _upload(c, alice_h, "guide.md", folder)
        html_id = await _upload(c, alice_h, "page.html", folder)
        pdf_id = await _upload(c, alice_h, "plan.pdf", folder)

        # 1. 대상 판정 — 지원 형식만 켤 수 있고, 그 외는 이유와 함께 거부
        r = await c.get(f"/api/files/{pdf_id}/wiki", headers=alice_h)
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["indexable"] is False and body["reason"], body
        r = await c.put(f"/api/files/{pdf_id}/wiki", headers=alice_h, json={"enabled": True})
        assert r.status_code == 400, r.text
        assert "Markdown" in r.json()["detail"], r.text
        _ok("PDF — indexable=false + 이유 노출, 켜기 시도 400")

        r = await c.put(f"/api/files/{md_id}/wiki", headers=alice_h, json={"enabled": True})
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["enabled"] is True and body["explicit"] is True, body
        assert body["source_file_id"] == md_id, body
        _ok("md — 켜기 성공 (explicit=true, 출처=자기 자신)")

        # 2. 인덱싱과 전사 공개는 독립 — (끔, 공개) 조합
        r = await c.put(
            f"/api/files/{pdf_id}/wiki", headers=alice_h, json={"public": True}
        )
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["enabled"] is False and body["public"] is True, body
        _ok("(인덱싱 끔, 전사 공개 켬) 조합 — PDF 도 전사 공유 가능")

        # 6. 전사 공개 → bob 이 즉시 접근
        r = await c.get(f"/api/files/{pdf_id}", headers=bob_h)
        assert r.status_code == 200, r.text
        _ok("전사 공개 → 다른 사용자가 즉시 접근")

        r = await c.put(
            f"/api/files/{pdf_id}/wiki", headers=alice_h, json={"public": False}
        )
        assert r.status_code == 200 and r.json()["public"] is False, r.text
        r = await c.get(f"/api/files/{pdf_id}", headers=bob_h)
        assert r.status_code == 404, r.text
        _ok("전사 공개 해제 → 즉시 차단")

        # 3. 폴더 토글 상속 + 파일 명시 FALSE 가 상속을 이긴다
        r = await c.put(
            f"/api/files/{folder}/wiki", headers=alice_h, json={"enabled": True}
        )
        assert r.status_code == 200, r.text
        r = await c.get(f"/api/files/{html_id}/wiki", headers=alice_h)
        body = r.json()
        assert body["enabled"] is True and body["explicit"] is False, body
        assert body["source_file_id"] == folder, body
        _ok("폴더 켜기 → 하위 html 상속 (explicit=false, 출처=폴더)")

        r = await c.put(
            f"/api/files/{html_id}/wiki", headers=alice_h, json={"enabled": False}
        )
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["enabled"] is False and body["explicit"] is True, body
        _ok("파일 명시 OFF 가 폴더 상속을 이긴다 (소유자 탈출구)")

        # null 을 명시하면 상속으로 되돌아간다
        r = await c.put(
            f"/api/files/{html_id}/wiki", headers=alice_h, json={"enabled": None}
        )
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["enabled"] is True and body["explicit"] is False, body
        _ok("enabled=null → 상속으로 복귀")

        # 4. 폴더 scope — 제외 사유별 개수
        r = await c.get(f"/api/files/{folder}/wiki", headers=alice_h)
        scope = r.json()["folder_scope"]
        assert scope is not None, r.text
        assert scope["target_count"] == 2, scope  # guide.md + page.html
        assert scope["skipped_by_format"] == 1, scope  # plan.pdf
        _ok(f"폴더 scope — 대상 {scope['target_count']}건, 형식 제외 {scope['skipped_by_format']}건")

        # 5. 발행 권한 없는 사용자에게는 404 (존재 은닉)
        r = await c.get(f"/api/files/{md_id}/wiki", headers=bob_h)
        assert r.status_code == 404, r.text
        r = await c.put(f"/api/files/{md_id}/wiki", headers=bob_h, json={"enabled": False})
        assert r.status_code == 404, r.text
        _ok("발행 권한 없는 사용자 — 조회·변경 모두 404")

        # 문서 목록은 아직 인덱싱 전이라 비어 있다 (워커는 다음 단계)
        r = await c.get("/api/wiki/documents", headers=alice_h)
        assert r.status_code == 200 and r.json()["total"] == 0, r.text
        _ok("문서 목록 — 인덱싱 전이므로 0건")

    await engine.dispose()
    print("\n위키 토글 API 통합 시나리오 전체 통과.")


if __name__ == "__main__":
    asyncio.run(main())
