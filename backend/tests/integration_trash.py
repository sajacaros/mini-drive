"""휴지통 보존 기간 자동 정리 통합 검증 (실제 postgres+redis+minio 필요, pytest 미수집).

시나리오: alice/bob 준비 → alice 파일 A + 폴더 F(안에 alice 파일 + bob 파일) → 소프트 삭제
→ A 만 기한 초과로 조작 → 정리(A 만 사라지고 F 는 남는다) → dry-run(아무것도 지우지 않음)
→ F 기한 초과 → 정리(하위 포함 제거 + **소유자별** storage_used 회수) → purge 이벤트 발행 확인
→ 보존 기간 0 이면 아무 일도 하지 않음 → 수동 영구 삭제도 이벤트를 발행하는지 확인.

env: DATABASE_URL / REDIS_URL / MINIO_ENDPOINT / MINIO_BUCKET. `python -m tests.integration_trash`.
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
from app.core.config import settings
from app.core.database import Base, SessionFactory, engine
from app.core.redis import redis_client
from app.main import app
from app.models import File, User
from app.services import trash as trash_service
from app.services.file_events import FILE_EVENTS_CHANNEL
from app.services.storage import storage_service
from tests._bootstrap import register_active, setup_admin
from tests._dbreset import stamp_alembic_head

ALICE = {"email": "alice@example.com", "password": "Passw0rd!", "display_name": "Alice"}
BOB = {"email": "bob@example.com", "password": "Passw0rd!", "display_name": "Bob"}

A_BYTES = b"A" * 4096
ALICE_IN_F_BYTES = b"B" * 2048
BOB_IN_F_BYTES = b"C" * 1024


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
        for o in storage_service._client.list_objects(storage_service.bucket, recursive=True)
    ]
    storage_service.delete_many(leftover)


async def _storage_used(user_id: int) -> int:
    async with SessionFactory() as s:
        return (await s.execute(select(User).where(User.id == user_id))).scalar_one().storage_used


async def _row_exists(file_id: int) -> bool:
    async with SessionFactory() as s:
        return (await s.execute(select(File.id).where(File.id == file_id))).first() is not None


async def _backdate(file_id: int, days: int) -> None:
    """deleted_at 을 과거로 밀어 기한 초과 상태를 만든다(하위까지 함께)."""
    async with SessionFactory() as s:
        await s.execute(
            text(
                "WITH RECURSIVE sub AS ("
                "  SELECT id FROM files WHERE id = :root"
                "  UNION ALL SELECT f.id FROM files f JOIN sub ON f.parent_folder_id = sub.id"
                ") UPDATE files SET deleted_at = :ts WHERE id IN (SELECT id FROM sub)"
            ),
            {"root": file_id, "ts": datetime.now(UTC) - timedelta(days=days)},
        )
        await s.commit()


async def _upload(c: httpx.AsyncClient, h: dict[str, str], name: str, payload: bytes, parent=None):
    data = {"parent_id": str(parent)} if parent is not None else None
    r = await c.post(
        "/api/files/upload",
        headers=h,
        files={"file": (name, payload, "application/octet-stream")},
        data=data,
    )
    assert r.status_code == 201, r.text
    return r.json()["id"]


async def _purge(*, dry_run: bool = False) -> trash_service.PurgeResult:
    async with SessionFactory() as s:
        return await trash_service.purge_expired(s, storage_service, dry_run=dry_run)


async def scenario() -> None:
    await _reset()
    # 보존 기간 7일로 고정(테스트는 설정에 의존하지 않는다).
    trash_service.settings.trash_retention_days = 7
    trash_service.settings.trash_purge_batch = 2  # 드레인 루프가 여러 번 도는 상황을 만든다.

    transport = ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test", timeout=60) as c:
        _admin_h, code = await setup_admin(c)
        alice_h, alice_id = await register_active(c, code, ALICE)
        bob_h, bob_id = await register_active(c, code, BOB)
        _ok("셋업 + alice/bob 가입")

        # 1. alice: 파일 A(루트) + 폴더 F + F 안 alice 파일
        a_id = await _upload(c, alice_h, "a.bin", A_BYTES)
        r = await c.post("/api/files", headers=alice_h, json={"name": "F"})
        assert r.status_code == 201, r.text
        f_id = r.json()["id"]
        a_in_f = await _upload(c, alice_h, "in_f.bin", ALICE_IN_F_BYTES, parent=f_id)

        # 2. bob 을 그룹으로 초대해 F 에 write 부여 → bob 이 F 안에 파일을 올린다(소유자 혼재).
        r = await c.post("/api/groups", headers=alice_h, json={"name": "T팀"})
        gid = r.json()["id"]
        r = await c.post(
            f"/api/groups/{gid}/members",
            headers=alice_h,
            json={"user_id": bob_id, "role": "member"},
        )
        assert r.status_code == 201, r.text
        r = await c.post(
            f"/api/files/{f_id}/permissions",
            headers=alice_h,
            json={"group_id": gid, "permission": "write", "inherit_to_children": True},
        )
        assert r.status_code == 201, r.text
        bob_in_f = await _upload(c, bob_h, "bob.bin", BOB_IN_F_BYTES, parent=f_id)
        _ok("A / F(alice 파일 + bob 파일) 구성 — 소유자 혼재 폴더")

        alice_before = await _storage_used(alice_id)
        bob_before = await _storage_used(bob_id)
        assert alice_before == len(A_BYTES) + len(ALICE_IN_F_BYTES), alice_before
        assert bob_before == len(BOB_IN_F_BYTES), bob_before
        _ok(f"업로드 후 storage_used (alice={alice_before}, bob={bob_before})")

        # 3. 둘 다 휴지통으로. 아직 기한 내이므로 정리 대상이 없다.
        for fid in (a_id, f_id):
            r = await c.post(f"/api/files/{fid}/delete", headers=alice_h)
            assert r.status_code == 204, r.text
        result = await _purge()
        assert (result.roots, result.rows) == (0, 0), result
        assert await _row_exists(a_id) and await _row_exists(f_id)
        _ok("기한 내 항목은 정리되지 않는다")

        # 4. A 만 10일 전으로 → A 만 사라지고 F 는 남는다.
        await _backdate(a_id, 10)
        result = await _purge()
        assert (result.roots, result.rows, result.failed) == (1, 1, 0), result
        assert result.bytes_reclaimed == len(A_BYTES), result
        assert not await _row_exists(a_id)
        assert await _row_exists(f_id)
        assert await _storage_used(alice_id) == len(ALICE_IN_F_BYTES)
        _ok("기한 초과 A 만 영구 삭제 + alice storage_used 회수")

        # 휴지통 목록에도 F 만 남는다.
        r = await c.get("/api/files/trash", headers=alice_h)
        assert r.status_code == 200 and [x["id"] for x in r.json()] == [f_id], r.text
        _ok("휴지통 목록에 F 만 남음")

        # 파생 필드 purge_at — 프론트의 "N일 후 삭제" 표시가 전적으로 이 값에 달려 있다.
        # 보존 기간은 따로 내려가지 않고 purge_at - deleted_at 으로 역산되므로 그 관계를 검증한다.
        row = r.json()[0]
        assert row["purge_at"] is not None, row
        gap = datetime.fromisoformat(row["purge_at"]) - datetime.fromisoformat(row["deleted_at"])
        assert gap == timedelta(days=settings.trash_retention_days), row
        _ok(f"purge_at = deleted_at + {settings.trash_retention_days}일 (파생 필드)")

        # 자동 정리가 꺼져 있으면 표시할 예정일이 없다 → None (프론트는 "—" 로 표시).
        original = settings.trash_retention_days
        settings.trash_retention_days = 0
        try:
            r = await c.get("/api/files/trash", headers=alice_h)
            assert r.json()[0]["purge_at"] is None, r.text
        finally:
            settings.trash_retention_days = original
        _ok("보존 기간 0 이면 purge_at 은 None")

        # 5. F 를 기한 초과로 만들고 dry-run — 아무것도 지우지 않고 규모만 보고한다.
        await _backdate(f_id, 30)
        result = await _purge(dry_run=True)
        assert result.dry_run and result.roots == 1, result
        # F + 하위 2개 = 3행, 회수 예상 바이트는 두 소유자 합계.
        assert result.rows == 3, result
        assert result.bytes_reclaimed == len(ALICE_IN_F_BYTES) + len(BOB_IN_F_BYTES), result
        assert await _row_exists(f_id)
        _ok(f"dry-run — {result.roots}건/{result.rows}행 보고, 실제 삭제 없음")

        # 6. 실제 정리 — 하위까지 제거되고 **소유자별로** 할당량이 회수된다.
        pubsub = redis_client.pubsub()
        await pubsub.subscribe(FILE_EVENTS_CHANNEL)
        result = await _purge()
        assert (result.roots, result.rows, result.failed) == (1, 3, 0), result
        for fid in (f_id, a_in_f, bob_in_f):
            assert not await _row_exists(fid), fid
        assert await _storage_used(alice_id) == 0
        assert await _storage_used(bob_id) == 0
        _ok("폴더 정리 — 하위 포함 삭제 + 소유자별 storage_used 회수 (alice=0, bob=0)")

        # 7. purge 이벤트가 발행됐는지 — 소유자 판정에 쓰이는 owner_id 가 실려야 한다.
        events = []
        for _ in range(30):
            msg = await pubsub.get_message(ignore_subscribe_messages=True, timeout=0.1)
            if msg and msg.get("data"):
                events.append(json.loads(msg["data"]))
        await pubsub.unsubscribe(FILE_EVENTS_CHANNEL)
        await pubsub.aclose()
        purges = [e for e in events if e.get("type") == "purge"]
        assert len(purges) == 1, events
        assert purges[0]["file_id"] == f_id, purges
        assert purges[0]["owner_id"] == alice_id, purges
        assert purges[0]["actor_id"] is None, purges  # 자동 정리는 행위자가 없다
        _ok("purge 이벤트 발행 (owner_id=소유자, actor_id=None)")

        # 8. 보존 기간 0 — 비활성화되면 아무것도 지우지 않는다.
        trash_service.settings.trash_retention_days = 0
        b_id = await _upload(c, alice_h, "b.bin", A_BYTES)
        r = await c.post(f"/api/files/{b_id}/delete", headers=alice_h)
        assert r.status_code == 204, r.text
        await _backdate(b_id, 365)
        result = await _purge()
        assert result.disabled and result.roots == 0, result
        assert await _row_exists(b_id)
        _ok("TRASH_RETENTION_DAYS=0 이면 정리하지 않는다")

        # 9. 수동 영구 삭제도 이제 이벤트를 발행한다(의도된 동작 변화).
        pubsub = redis_client.pubsub()
        await pubsub.subscribe(FILE_EVENTS_CHANNEL)
        r = await c.post(f"/api/files/{b_id}/permanent-delete", headers=alice_h)
        assert r.status_code == 204, r.text
        manual = []
        for _ in range(30):
            msg = await pubsub.get_message(ignore_subscribe_messages=True, timeout=0.1)
            if msg and msg.get("data"):
                manual.append(json.loads(msg["data"]))
        await pubsub.unsubscribe(FILE_EVENTS_CHANNEL)
        await pubsub.aclose()
        purges = [e for e in manual if e.get("type") == "purge"]
        assert len(purges) == 1 and purges[0]["file_id"] == b_id, manual
        assert purges[0]["actor_id"] == alice_id, purges  # 수동 경로는 행위자가 있다
        assert not await _row_exists(b_id)
        assert await _storage_used(alice_id) == 0
        _ok("수동 영구 삭제도 purge 이벤트 발행 (actor_id=사용자)")


def main() -> int:
    print("== 휴지통 보존 기간 자동 정리 통합 검증 ==")
    try:
        asyncio.run(scenario())
    except AssertionError as exc:
        print(f"  [FAIL] {exc}", file=sys.stderr)
        return 1
    print("== 전체 통과 ==")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
