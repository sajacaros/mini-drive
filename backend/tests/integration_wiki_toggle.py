"""위키 토글 API 통합 검증 (spec/wiki-index.md).

검증 축:
  1. 지원 형식(md/html)만 토글이 켜지고, 그 외는 400 + 이유.
  2. **축은 하나다** — 켜면 인덱싱과 전사 공개가 함께 걸리고, 끄면 함께 풀린다.
  3. 폴더 토글은 하위로 상속되고, 파일의 명시 FALSE 가 상속을 이긴다(소유자 탈출구).
  4. 폴더 scope 는 제외 사유별 개수를 돌려준다(확인 문구용).
  5. 발행 권한 없는 사용자에게는 404 (존재 은닉).
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

        # 2. 축은 하나다 (spec/wiki-index.md 「왜 스위치가 하나인가」).
        #    md 를 켠 것 말고는 아무 권한도 준 적이 없는데 bob 이 즉시 열람해야 한다 —
        #    별도의 공개 스위치를 한 번 더 거쳐야 했던 시절이 실측에서 467건 중 2건만
        #    공개된 상태를 만들었고, 그래서 축을 합쳤다.
        r = await c.get(f"/api/files/{md_id}", headers=bob_h)
        assert r.status_code == 200, r.text
        _ok("켜기 = 전사 공개 — 권한 부여 없이 다른 사용자가 즉시 접근")

        # 끄면 공개도 **함께** 풀린다. 한쪽만 풀리면 인덱스에서 빠진 문서가 계속 열려 있거나
        # (공개 잔류) 카탈로그가 권한 판정 없이 도는 근거가 깨진다.
        r = await c.put(
            f"/api/files/{md_id}/wiki", headers=alice_h, json={"enabled": False}
        )
        assert r.status_code == 200 and r.json()["enabled"] is False, r.text
        r = await c.get(f"/api/files/{md_id}", headers=bob_h)
        assert r.status_code == 404, r.text
        _ok("끄기 → 전사 공개도 함께 풀려 즉시 차단")

        # 뒤 단계(폴더 상속·scope)를 위해 상속 상태로 돌려 둔다 — 명시 OFF 로 남기면 폴더를
        # 켜도 되살아나지 않아(skipped_by_optout) scope 의 대상 건수가 달라진다.
        r = await c.put(
            f"/api/files/{md_id}/wiki", headers=alice_h, json={"enabled": None}
        )
        assert r.status_code == 200 and r.json()["explicit"] is False, r.text

        # PDF 는 애초에 켤 수 없으므로(1번) 전사 공개에 이르는 경로 자체가 없다 — 축을 합친
        # 뒤로는 "인덱싱은 끄고 공개만" 하는 조합이 존재하지 않는다.
        r = await c.get(f"/api/files/{pdf_id}", headers=bob_h)
        assert r.status_code == 404, r.text
        _ok("지원 형식이 아닌 파일은 전사 공개에 이르는 경로가 없다")

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
        targets, skipped = scope["target_count"], scope["skipped_by_format"]
        _ok(f"폴더 scope — 대상 {targets}건, 형식 제외 {skipped}건")

        # 5. 발행 권한 없는 사용자에게는 404 (존재 은닉)
        r = await c.get(f"/api/files/{md_id}/wiki", headers=bob_h)
        assert r.status_code == 404, r.text
        r = await c.put(f"/api/files/{md_id}/wiki", headers=bob_h, json={"enabled": False})
        assert r.status_code == 404, r.text
        _ok("발행 권한 없는 사용자 — 조회·변경 모두 404")

        # 6. 목록이 폴더의 위키 선언을 말한다 (spec 「폴더 상속 사고 경로」).
        #    폴더는 인덱싱 상태가 없어(wiki_status 가 늘 off) 이 값이 아니면 목록에서 위키
        #    폴더를 알아볼 방법이 없다. 폴더 행은 드롭 대상이라 **열기 전에** 알아야 한다.
        r = await c.post(
            "/api/files", headers=alice_h, json={"name": "하위", "parent_id": folder}
        )
        assert r.status_code == 201, r.text

        r = await c.get("/api/files", headers=alice_h)
        assert r.status_code == 200, r.text
        body = r.json()
        # 루트 자신은 위키가 아니지만, 그 안의 위키 폴더는 목록에서 드러나야 한다.
        assert body["wiki_enabled"] is False, body
        root_items = {i["name"]: i for i in body["items"]}
        assert root_items["문서함"]["wiki_declared"] is True, root_items["문서함"]
        assert root_items["문서함"]["wiki_status"] == "off", root_items["문서함"]
        _ok("루트 목록 — 위키로 선언된 폴더가 wiki_declared=true 로 드러난다")

        # 상속받는 하위 폴더는 명시값이 없다(null) — 화면도 여기에 배지를 달지 않는다.
        # 배지는 "선언된 폴더" 하나만 말한다(spec 「표시는 여기까지다」).
        r = await c.get("/api/files", headers=alice_h, params={"parentId": folder})
        body = r.json()
        # 최상위 wiki_enabled 는 **지금 폴더의 유효값**, 항목의 wiki_declared 는 **명시값**이다.
        assert body["wiki_enabled"] is True, body
        kids = {i["name"]: i for i in body["items"]}
        assert kids["하위"]["wiki_declared"] is None, kids["하위"]
        _ok("하위 목록 — 상속 폴더는 명시값 없음(null), 선언된 폴더만 표시 대상")

        # 문서 목록에는 인덱싱 전(pending) 항목도 나온다 — 폴더를 켜고 하나씩 올라오는 것을
        # 지켜볼 수 있어야 한다. 검색은 ready/stale 만 보므로 미완성 트리가 답변에 쓰이지 않는다.
        r = await c.get("/api/wiki/documents", headers=alice_h)
        assert r.status_code == 200, r.text
        items = {d["name"]: d for d in r.json()["items"]}
        assert set(items) == {"guide.md", "page.html"}, items
        assert all(d["status"] == "pending" for d in items.values()), items
        assert all(d["node_count"] is None for d in items.values()), items
        _ok("문서 목록 — 인덱싱 전 항목이 pending 으로 노출 (트리는 아직 없음)")

    await engine.dispose()
    print("\n위키 토글 API 통합 시나리오 전체 통과.")


if __name__ == "__main__":
    asyncio.run(main())
