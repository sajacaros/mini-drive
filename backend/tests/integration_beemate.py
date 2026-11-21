"""BeeMate 통합 검증 (실제 postgres+redis+seaweedfs 필요, pytest 미수집). spec/beemate.md.

시나리오: alice/bob 준비 → alice 기기 2대(폰·PC) 등록 → 목록·이름 변경 → bob 은 alice 기기가
404 → 기기 이벤트 스트림을 열고(온라인 표시) 폰→PC 전송 → 스트림에 전송이 오는지 → 밀린 목록
→ 내려받기(X-Accel-Redirect) → 받음 확인(행·오브젝트 삭제) → 크기·대기 합계 상한 → 만료(목록에서
빠지고 정리가 지운다) → 기기 삭제가 밀린 전송의 오브젝트까지 지우는지 → 드라이브 용량 불변.

SSE 는 httpx ASGITransport 가 응답을 끝까지 모으려 해 무한 스트림을 못 받으므로, 라우트 대신
서비스의 생성기를 직접 돌린다(라우트는 소유 확인 후 그 생성기를 그대로 넘긴다).

env: DATABASE_URL / REDIS_URL / S3_ENDPOINT / S3_BUCKET. `python -m tests.integration_beemate`.
"""

from __future__ import annotations

import asyncio
import json
import sys
from datetime import UTC, datetime, timedelta

import httpx
from httpx import ASGITransport
from sqlalchemy import select, text

import app.models  # noqa: F401 - 전체 모델을 metadata 에 등록
from app.core.database import Base, SessionFactory, engine
from app.core.redis import redis_client
from app.main import app
from app.models import BeemateTransfer, User
from app.services import beemate as beemate_service
from app.services.storage import storage_service
from tests._bootstrap import register_active, setup_admin
from tests._dbreset import stamp_alembic_head

ALICE = {"email": "alice@example.com", "password": "Passw0rd!", "display_name": "Alice"}
BOB = {"email": "bob@example.com", "password": "Passw0rd!", "display_name": "Bob"}

PHOTO = b"\x89PNG\r\n\x1a\n" + b"P" * 4096


def _ok(msg: str) -> None:
    print(f"  [PASS] {msg}")


async def _reset() -> None:
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
        await conn.run_sync(Base.metadata.create_all)
        await conn.run_sync(stamp_alembic_head)
    await redis_client.flushdb()
    storage_service.ensure_bucket()
    storage_service.delete_many(storage_service.list_keys())


async def _send(
    c: httpx.AsyncClient,
    h: dict[str, str],
    to: int,
    name: str = "photo.png",
    payload: bytes = PHOTO,
    mime: str = "image/png",
    frm: int | None = None,
) -> httpx.Response:
    data = {"to_device_id": str(to)}
    if frm is not None:
        data["from_device_id"] = str(frm)
    return await c.post(
        "/api/beemate/transfers", headers=h, files={"file": (name, payload, mime)}, data=data
    )


async def _row(transfer_id: int) -> BeemateTransfer | None:
    async with SessionFactory() as s:
        return await s.get(BeemateTransfer, transfer_id)


async def _storage_used(user_id: int) -> int:
    async with SessionFactory() as s:
        return (await s.execute(select(User).where(User.id == user_id))).scalar_one().storage_used


async def _backdate(transfer_id: int, hours: int) -> None:
    async with SessionFactory() as s:
        await s.execute(
            text("UPDATE beemate_transfers SET created_at = :ts WHERE id = :id"),
            {"ts": datetime.now(UTC) - timedelta(hours=hours), "id": transfer_id},
        )
        await s.commit()


async def scenario() -> None:
    await _reset()
    settings = beemate_service.settings
    settings.beemate_transfer_ttl_hours = 24
    settings.beemate_max_file_bytes = 64 * 1024
    settings.beemate_max_pending_bytes = 16 * 1024

    transport = ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test", timeout=60) as c:
        _admin_h, code = await setup_admin(c)
        alice_h, alice_id = await register_active(c, code, ALICE)
        bob_h, _bob_id = await register_active(c, code, BOB)
        _ok("셋업 + alice/bob 가입")

        # 1. 자동 등록(upsert) · 이름 · 즐겨찾기 · 숨김 · 정렬
        dev = "/api/beemate/devices"
        galaxy = {"client_id": "phone-0001", "name": " 갤럭시 ", "platform": "android"}
        r = await c.post(dev, headers=alice_h, json=galaxy)
        assert r.status_code == 200, r.text
        phone = r.json()["id"]
        assert r.json()["name"] == "갤럭시", r.json()
        r = await c.post(dev, headers=alice_h, json={**galaxy, "name": "다른 이름"})
        assert r.json()["id"] == phone and r.json()["name"] == "갤럭시", r.text  # 덮어쓰지 않음
        pc_req = {"client_id": "pc-browser-1", "name": "Windows · Chrome", "platform": "web"}
        pc = (await c.post(dev, headers=alice_h, json=pc_req)).json()["id"]
        old = {"client_id": "old-laptop-1", "name": "옛 노트북", "platform": "web"}
        laptop = (await c.post(dev, headers=alice_h, json=old)).json()["id"]
        r = await c.post(dev, headers=alice_h, json={**pc_req, "client_id": "bad id"})
        assert r.status_code == 422, r.text
        r = await c.post(dev, headers=alice_h, json={**pc_req, "platform": "symbian"})
        assert r.status_code == 422, r.text

        r = await c.patch(f"{dev}/{pc}", headers=alice_h, json={"name": "내 PC"})
        assert r.status_code == 200 and r.json()["name"] == "내 PC", r.text
        r = await c.patch(f"{dev}/{laptop}", headers=alice_h, json={"is_favorite": True})
        assert r.json()["is_favorite"] is True, r.text
        r = await c.patch(f"{dev}/{pc}", headers=alice_h, json={})
        assert r.status_code == 422, r.text
        r = await c.get(dev, headers=alice_h)
        # 즐겨찾기(꺼져 있어도) → 나머지 등록 순
        assert [d["id"] for d in r.json()] == [laptop, phone, pc], r.text
        await c.patch(f"{dev}/{phone}", headers=alice_h, json={"is_hidden": True})
        r = await c.get(dev, headers=alice_h)
        assert [d["id"] for d in r.json()] == [laptop, pc], r.text
        r = await c.get(f"{dev}?include_hidden=true", headers=alice_h)
        assert [d["id"] for d in r.json()] == [laptop, phone, pc], r.text
        await c.patch(f"{dev}/{phone}", headers=alice_h, json={"is_hidden": False})
        _ok("자동 등록(upsert, 이름 보존) · 이름 · 즐겨찾기 · 숨김 · 정렬")

        # 2. 남의 기기는 404 (존재를 흘리지 않는다). 같은 client_id 도 사용자별로 따로다.
        assert (await c.get(dev, headers=bob_h)).json() == []
        r = await c.patch(f"{dev}/{pc}", headers=bob_h, json={"name": "탈취"})
        assert r.status_code == 404, r.text
        assert (await _send(c, bob_h, to=pc)).status_code == 404
        r = await c.get(f"/api/beemate/transfers?device_id={pc}", headers=bob_h)
        assert r.status_code == 404, r.text
        r = await c.post(dev, headers=bob_h, json=pc_req)
        assert r.status_code == 200 and r.json()["id"] != pc, r.text
        _ok("bob 은 alice 기기를 볼 수도 보낼 수도 없다(404), client_id 는 사용자별")

        # 3. PC 스트림 → hello · 온라인(켜진 기기가 위로) → 보내면 내용 없는 pending 신호
        stream = beemate_service.subscribe_device_events(pc)
        assert await anext(stream) == ": connected\n\n"
        hello = json.loads((await anext(stream))[len("data: ") :])
        assert hello == {"type": "hello", "device_id": pc}, hello
        r = await c.get(dev, headers=alice_h)
        assert [(d["id"], d["online"]) for d in r.json()] == [
            (laptop, False),
            (pc, True),
            (phone, False),
        ], r.text

        r = await _send(c, alice_h, to=pc, frm=phone)
        assert r.status_code == 201, r.text
        sent = r.json()
        assert sent["name"] == "photo.png" and sent["size"] == len(PHOTO), sent
        assert sent["from_device_id"] == phone and sent["to_device_id"] == pc

        frame = await asyncio.wait_for(anext(stream), timeout=10)
        while frame.startswith(":"):
            frame = await asyncio.wait_for(anext(stream), timeout=40)
        assert json.loads(frame[len("data: ") :]) == {"type": "pending"}, frame
        await stream.aclose()
        assert await redis_client.get(beemate_service.online_key(pc)) is None
        _ok("스트림: hello → 온라인 정렬, 보내면 내용 없는 pending 신호, 닫으면 오프라인")

        # 4. 밀린 목록 · 내려받기 · 받음 확인
        r = await c.get(f"/api/beemate/transfers?device_id={pc}", headers=alice_h)
        assert [t["id"] for t in r.json()] == [sent["id"]], r.text
        r = await c.get(f"/api/beemate/transfers?device_id={phone}", headers=alice_h)
        assert r.json() == [], r.text
        r = await c.get(f"/api/beemate/transfers/{sent['id']}/content", headers=alice_h)
        assert r.status_code == 200, r.text
        assert r.headers["x-accel-redirect"], r.headers
        assert "photo.png" in r.headers["content-disposition"]
        key = beemate_service.build_object_key(alice_id, sent["id"])
        assert storage_service.get_bytes(key) == PHOTO
        r = await c.get(f"/api/beemate/transfers/{sent['id']}/content", headers=bob_h)
        assert r.status_code == 404, r.text

        r = await c.delete(f"/api/beemate/transfers/{sent['id']}", headers=alice_h)
        assert r.status_code == 204, r.text
        assert await _row(sent["id"]) is None
        assert not storage_service.exists(key)
        r = await c.delete(f"/api/beemate/transfers/{sent['id']}", headers=alice_h)
        assert r.status_code == 404, r.text
        _ok("밀린 목록 → 내려받기(게이트웨이) → 받음 확인 = 행·오브젝트 삭제")

        # 5. 상한 — 파일 1개, 대기 합계
        r = await _send(c, alice_h, to=pc, name="big.bin", payload=b"B" * (64 * 1024 + 1))
        assert r.status_code == 413, r.text
        ids = []
        for i in range(4):
            r = await _send(c, alice_h, to=pc, name=f"{i}.bin", payload=b"x" * 4096)
            assert r.status_code == 201, r.text
            ids.append(r.json()["id"])
        r = await _send(c, alice_h, to=pc, name="over.bin", payload=b"x" * 1)
        assert r.status_code == 413, r.text
        _ok("파일 크기 상한·대기 합계 상한 413")

        # 6. 만료 — 목록·내려받기에서 빠지고, 다음 보내기가 그 사용자 몫을 지운다
        await _backdate(ids[0], hours=25)
        r = await c.get(f"/api/beemate/transfers?device_id={pc}", headers=alice_h)
        assert ids[0] not in [t["id"] for t in r.json()], r.text
        r = await c.get(f"/api/beemate/transfers/{ids[0]}/content", headers=alice_h)
        assert r.status_code == 404, r.text
        assert await _row(ids[0]) is not None  # 아직 지우지는 않았다
        r = await _send(c, alice_h, to=pc, name="after.bin", payload=b"y" * 4096)
        assert r.status_code == 201, r.text  # 만료분이 빠져 상한 안으로 들어온다
        assert await _row(ids[0]) is None
        assert not storage_service.exists(beemate_service.build_object_key(alice_id, ids[0]))
        ids = ids[1:] + [r.json()["id"]]
        _ok("만료분은 바로 안 보이고, 다음 보내기에서 정리")

        # 7. purger 전체 정리
        await _backdate(ids[0], hours=48)
        async with SessionFactory() as s:
            result = await beemate_service.purge_expired(s, storage_service)
        assert result.deleted == 1 and result.bytes_reclaimed == 4096, result
        assert await _row(ids[0]) is None
        _ok("purge_expired 가 만료분만 지운다")

        # 8. 기기 삭제 — 밀린 전송의 오브젝트까지
        keys = [beemate_service.build_object_key(alice_id, i) for i in ids[1:]]
        assert all(storage_service.exists(k) for k in keys)
        r = await c.delete(f"/api/beemate/devices/{pc}", headers=alice_h)
        assert r.status_code == 204, r.text
        assert not any(storage_service.exists(k) for k in keys)
        async with SessionFactory() as s:
            left = (await s.execute(select(BeemateTransfer.id))).scalars().all()
        assert left == [], left
        _ok("기기 삭제 → 밀린 전송 행·오브젝트 모두 삭제")

        # 9. 드라이브 용량은 건드리지 않는다
        assert await _storage_used(alice_id) == 0
        _ok("드라이브 storage_used 불변")


def main() -> int:
    print("== BeeMate 통합 검증 ==")
    try:
        asyncio.run(scenario())
    except AssertionError as exc:
        print(f"  [FAIL] {exc}", file=sys.stderr)
        return 1
    print("== 전체 통과 ==")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
