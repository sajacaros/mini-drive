"""드라이브 UX(Phase 8) 통합 검증 — 즐겨찾기·최근 항목·SSE 이벤트 (실 postgres+redis+minio).

시나리오:
  A/B 가입 → A 파일 업로드
  [즐겨찾기] PUT 멱등 → favorites 목록/목록·단건 is_favorite 파생 → DELETE 멱등 → 목록 비움
  [권한 숨김] A 폴더/파일 + 그룹 G read 를 B 에 부여 → 부여 전 B 의 favorite PUT 404
             → 부여 후 B favorite 등록·조회 → A 회수 → B favorites 에서 숨김
  [최근] A 미리보기 성공 → recent 반영 → 다운로드 티켓 발급 → recent 갱신(중복 없음)
  [SSE] 채널 구독 상태에서 폴더 생성 → 발행 이벤트 1건 수신(payload 검증)
        + 권한 필터(user_can_receive_event): 타인 비공개 파일 차단 → 권한 부여 후 통과

env: DATABASE_URL / REDIS_URL / MINIO_ENDPOINT / MINIO_BUCKET.
실행: `python -m tests.integration_drive_ux`.
"""

from __future__ import annotations

import asyncio
import json
import sys

import httpx
from httpx import ASGITransport
from sqlalchemy import select

import app.models  # noqa: F401 - 전체 모델을 metadata 에 등록
from app.core.database import Base, SessionFactory, engine
from app.core.redis import redis_client
from app.main import app
from app.models import User
from app.services import file_events as fe
from app.services.storage import storage_service
from tests._bootstrap import register_active, setup_admin
from tests._dbreset import stamp_alembic_head

USERS = {
    "alice": {"email": "alice@example.com", "password": "Passw0rd!", "display_name": "Alice"},
    "bob": {"email": "bob@example.com", "password": "Passw0rd!", "display_name": "Bob"},
}
PAYLOAD = b"MiniDrive-drive-ux-phase8-payload"
TEXT_PAYLOAD = b"hello recent preview\nsecond line\n"


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
    c: httpx.AsyncClient,
    h: dict[str, str],
    name: str,
    content: bytes,
    mime: str,
    parent_id: int | None = None,
) -> int:
    data = {"parent_id": str(parent_id)} if parent_id is not None else None
    r = await c.post(
        "/api/files/upload",
        headers=h,
        files={"file": (name, content, mime)},
        data=data,
    )
    assert r.status_code == 201, r.text
    return r.json()["id"]


async def _load_user(user_id: int) -> User:
    async with SessionFactory() as s:
        return (await s.execute(select(User).where(User.id == user_id))).scalar_one()


async def _drain_event(pubsub, *, tries: int = 20) -> dict | None:
    """구독 채널에서 데이터 메시지 1건을 폴링해 파싱한다(없으면 None)."""
    for _ in range(tries):
        message = await pubsub.get_message(
            ignore_subscribe_messages=True, timeout=1.0
        )
        if message is None:
            continue
        data = message.get("data")
        if data is None:
            continue
        if isinstance(data, bytes):
            data = data.decode()
        return json.loads(data)
    return None


async def scenario() -> None:  # noqa: C901, PLR0915 - 순차 시나리오
    await _reset()

    transport = ASGITransport(app=app)
    async with httpx.AsyncClient(
        transport=transport, base_url="http://test", timeout=60
    ) as c:
        _admin_h, code = await setup_admin(c)
        alice_h, alice_id = await register_active(c, code, USERS["alice"])
        bob_h, bob_id = await register_active(c, code, USERS["bob"])
        _ok("셋업 + 코드 가입 → alice(A)/bob(B) 로그인")

        # === 즐겨찾기 (A 소유 파일) =========================================
        file_id = await _upload(c, alice_h, "doc.bin", PAYLOAD, "application/octet-stream")

        r = await c.put(f"/api/files/{file_id}/favorite", headers=alice_h)
        assert r.status_code == 204, r.text
        # 멱등 — 다시 등록해도 204
        r = await c.put(f"/api/files/{file_id}/favorite", headers=alice_h)
        assert r.status_code == 204, r.text
        _ok("즐겨찾기 PUT 멱등 (204)")

        r = await c.get("/api/files/favorites", headers=alice_h)
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["total"] == 1 and body["items"][0]["id"] == file_id
        assert body["items"][0]["is_favorite"] is True
        _ok("favorites 목록에 등록 (is_favorite=true)")

        # 목록/단건 응답의 is_favorite 파생 필드
        r = await c.get("/api/files", headers=alice_h)
        item = next(i for i in r.json()["items"] if i["id"] == file_id)
        assert item["is_favorite"] is True
        r = await c.get(f"/api/files/{file_id}", headers=alice_h)
        assert r.json()["is_favorite"] is True
        _ok("목록/단건 is_favorite 파생 필드 반영")

        r = await c.delete(f"/api/files/{file_id}/favorite", headers=alice_h)
        assert r.status_code == 204, r.text
        # 멱등 해제
        r = await c.delete(f"/api/files/{file_id}/favorite", headers=alice_h)
        assert r.status_code == 204, r.text
        r = await c.get("/api/files/favorites", headers=alice_h)
        assert r.json()["total"] == 0
        r = await c.get(f"/api/files/{file_id}", headers=alice_h)
        assert r.json()["is_favorite"] is False
        _ok("즐겨찾기 DELETE 멱등 → 목록 비움 / is_favorite=false")

        # === 권한 회수 시 즐겨찾기 숨김 =====================================
        # A 가 그룹 G 생성 + B member, 파일에 read 부여.
        r = await c.post("/api/groups", headers=alice_h, json={"name": "G팀"})
        gid = r.json()["id"]
        r = await c.post(
            f"/api/groups/{gid}/members",
            headers=alice_h,
            json={"user_id": bob_id, "role": "member"},
        )
        assert r.status_code == 201, r.text

        # 부여 전 — B 는 접근 불가라 favorite 등록 404(존재 은닉)
        r = await c.put(f"/api/files/{file_id}/favorite", headers=bob_h)
        assert r.status_code == 404, r.text
        _ok("접근 불가 파일 favorite 등록 404 (존재 은닉)")

        r = await c.post(
            f"/api/files/{file_id}/permissions",
            headers=alice_h,
            json={"group_id": gid, "permission": "read", "inherit_to_children": True},
        )
        assert r.status_code == 201, r.text

        r = await c.put(f"/api/files/{file_id}/favorite", headers=bob_h)
        assert r.status_code == 204, r.text
        r = await c.get("/api/files/favorites", headers=bob_h)
        assert r.json()["total"] == 1 and r.json()["items"][0]["id"] == file_id
        _ok("권한 부여 후 B favorite 등록·조회")

        # A 가 권한 회수 → B favorites 에서 숨김(행은 유지, 조회 시 재검증으로 숨김)
        r = await c.delete(
            f"/api/files/{file_id}/permissions/{gid}", headers=alice_h
        )
        assert r.status_code == 204, r.text
        r = await c.get("/api/files/favorites", headers=bob_h)
        assert r.json()["total"] == 0
        _ok("권한 회수 → B favorites 에서 숨김")

        # === 최근 항목 (미리보기 / 다운로드 티켓) ===========================
        text_id = await _upload(c, alice_h, "note.txt", TEXT_PAYLOAD, "text/plain")

        r = await c.get(f"/api/files/{text_id}/preview", headers=alice_h)
        assert r.status_code == 200, r.text
        r = await c.get("/api/files/recent", headers=alice_h)
        assert r.status_code == 200, r.text
        recent_ids = [i["id"] for i in r.json()]
        assert text_id in recent_ids
        _ok("미리보기 성공 → recent 반영")

        # 다운로드 티켓 발급 → 같은 파일 recent 갱신(중복 행 없음)
        r = await c.post(f"/api/files/{text_id}/download-ticket", headers=alice_h)
        assert r.status_code == 200, r.text
        r = await c.get("/api/files/recent", headers=alice_h)
        got = [i["id"] for i in r.json()]
        assert got.count(text_id) == 1, got
        _ok("다운로드 티켓 발급 → recent 갱신(중복 없음)")

        # === SSE 발행/구독 + 권한 필터 ======================================
        pubsub = redis_client.pubsub()
        await pubsub.subscribe(fe.FILE_EVENTS_CHANNEL)
        # 구독 등록이 반영되도록 잠깐 양보.
        await asyncio.sleep(0.1)
        r = await c.post(
            "/api/files", headers=alice_h, json={"name": "이벤트폴더", "parent_id": None}
        )
        assert r.status_code == 201, r.text
        folder_id = r.json()["id"]
        event = await _drain_event(pubsub)
        assert event is not None, "폴더 생성 이벤트를 수신하지 못했습니다"
        assert event["type"] == "folder"
        assert event["file_id"] == folder_id
        assert event["actor_id"] == alice_id
        await pubsub.unsubscribe(fe.FILE_EVENTS_CHANNEL)
        await pubsub.aclose()
        _ok("폴더 생성 → file-events 채널에서 이벤트 1건 수신(payload 검증)")

        # 권한 필터 — B 는 A 의 비공개 파일 이벤트를 받지 못한다.
        await redis_client.flushdb()  # 권한 캐시 초기화(회수 잔재 제거)
        bob_user = await _load_user(bob_id)
        private_event = {"file_id": file_id, "type": "rename"}
        assert await fe.user_can_receive_event(bob_user, private_event) is False
        _ok("권한 필터: 타인 비공개 파일 이벤트 차단")

        # 다시 read 부여하면 B 도 이벤트 수신 가능.
        r = await c.post(
            f"/api/files/{file_id}/permissions",
            headers=alice_h,
            json={"group_id": gid, "permission": "read", "inherit_to_children": True},
        )
        assert r.status_code == 201, r.text
        await redis_client.flushdb()
        bob_user = await _load_user(bob_id)
        assert await fe.user_can_receive_event(bob_user, private_event) is True
        _ok("권한 필터: 권한 부여 후 이벤트 통과")

    await engine.dispose()
    await redis_client.aclose()
    print("\n드라이브 UX(Phase 8) 통합 시나리오 전체 통과.")


def main() -> int:
    try:
        asyncio.run(scenario())
    except AssertionError as exc:
        print(f"\n[FAIL] {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
