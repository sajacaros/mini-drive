"""파일/폴더 이동 통합 검증 (실제 postgres+redis+minio 필요, pytest 미수집).

시나리오 (PRD 6.2 POST /api/files/{id}/move):
  A 가 src/dst 폴더 + 파일 구성 → 파일 이동(목록 이동 확인) → 루트로 이동(parent_id=null)
  → 폴더째 이동 → 자기 자신/자손으로 이동 400 → 대상 위치 동명 409 → 같은 폴더로 재이동 no-op
  → 루트 폴더 이동 400 → 휴지통 항목 이동 409 → foldersOnly 목록
  → 그룹 read 공유 폴더에서 꺼내오기 차단 / 밀어 넣기 차단 → write 로 승격하면 허용
  → 이동 후 상속 권한이 즉시 반영되는지(권한 캐시 무효화).

env: DATABASE_URL / REDIS_URL / MINIO_ENDPOINT / MINIO_BUCKET.
실행: `python -m tests.integration_move`.
"""

from __future__ import annotations

import asyncio
import sys

import httpx
from httpx import ASGITransport

import app.models  # noqa: F401 - 전체 모델을 metadata 에 등록
from app.core.database import Base, engine
from app.core.redis import redis_client
from app.main import app
from app.services.storage import storage_service
from tests._bootstrap import register_active, setup_admin
from tests._dbreset import stamp_alembic_head

USERS = {
    "alice": {"email": "alice@example.com", "password": "Passw0rd!", "display_name": "Alice"},
    "bob": {"email": "bob@example.com", "password": "Passw0rd!", "display_name": "Bob"},
}
PAYLOAD = b"MiniDrive-move-test-payload"


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


async def _mkfolder(
    c: httpx.AsyncClient, h: dict[str, str], name: str, parent_id: int | None
) -> int:
    r = await c.post("/api/files", headers=h, json={"name": name, "parent_id": parent_id})
    assert r.status_code == 201, r.text
    return r.json()["id"]


async def _upload(
    c: httpx.AsyncClient, h: dict[str, str], name: str, parent_id: int | None
) -> int:
    data = {"parent_id": str(parent_id)} if parent_id is not None else {}
    r = await c.post(
        "/api/files/upload",
        headers=h,
        files={"file": (name, PAYLOAD, "application/octet-stream")},
        data=data,
    )
    assert r.status_code == 201, r.text
    return r.json()["id"]


async def _move(
    c: httpx.AsyncClient, h: dict[str, str], file_id: int, parent_id: int | None
) -> httpx.Response:
    return await c.post(
        f"/api/files/{file_id}/move", headers=h, json={"parent_id": parent_id}
    )


async def _child_ids(
    c: httpx.AsyncClient, h: dict[str, str], parent_id: int | None
) -> set[int]:
    params = {"parentId": parent_id} if parent_id is not None else {}
    r = await c.get("/api/files", headers=h, params=params)
    assert r.status_code == 200, r.text
    return {it["id"] for it in r.json()["items"]}


async def scenario() -> None:  # noqa: C901, PLR0915 - 순차 시나리오
    await _reset()

    transport = ASGITransport(app=app)
    async with httpx.AsyncClient(
        transport=transport, base_url="http://test", timeout=60
    ) as c:
        _admin_h, code = await setup_admin(c)
        alice_h, _alice_id = await register_active(c, code, USERS["alice"])
        bob_h, bob_id = await register_active(c, code, USERS["bob"])
        _ok("셋업 + alice/bob 가입")

        # 1. 트리 구성 — root/src/file, root/dst
        src = await _mkfolder(c, alice_h, "src", None)
        dst = await _mkfolder(c, alice_h, "dst", None)
        deep = await _mkfolder(c, alice_h, "deep", src)
        file_id = await _upload(c, alice_h, "note.txt", src)
        _ok("트리 구성 (src/deep, src/note.txt, dst)")

        # 2. 파일 이동 src → dst
        r = await _move(c, alice_h, file_id, dst)
        assert r.status_code == 200, r.text
        assert r.json()["parent_folder_id"] == dst, r.text
        assert file_id not in await _child_ids(c, alice_h, src)
        assert file_id in await _child_ids(c, alice_h, dst)
        _ok("파일 이동 src → dst (양쪽 목록 반영)")

        # 3. 루트로 이동 (parent_id=null)
        r = await _move(c, alice_h, file_id, None)
        assert r.status_code == 200, r.text
        assert file_id in await _child_ids(c, alice_h, None)
        _ok("루트로 이동 (parent_id=null)")

        # 4. 같은 폴더로 재이동 — 조작 없이 200 (no-op)
        r = await _move(c, alice_h, file_id, None)
        assert r.status_code == 200, r.text
        _ok("같은 폴더로 재이동 no-op 200")

        # 5. 폴더째 이동 src → dst (하위 deep 이 따라온다)
        r = await _move(c, alice_h, src, dst)
        assert r.status_code == 200, r.text
        assert src in await _child_ids(c, alice_h, dst)
        assert deep in await _child_ids(c, alice_h, src)
        _ok("폴더째 이동 (하위 유지)")

        # 6. 순환 이동 400 — 자기 자신 / 자기 자손 아래로
        r = await _move(c, alice_h, src, src)
        assert r.status_code == 400, r.text
        r = await _move(c, alice_h, src, deep)
        assert r.status_code == 400, r.text
        # 막힌 뒤에도 원래 위치 그대로여야 한다(부분 적용 없음).
        r = await c.get(f"/api/files/{src}", headers=alice_h)
        assert r.json()["parent_folder_id"] == dst, r.text
        _ok("자기 자신/자손으로 이동 400 (위치 불변)")

        # 7. 대상 위치 동명 충돌 409 — dst 아래에 이미 있는 이름으로 이동
        dup = await _mkfolder(c, alice_h, "src", None)
        r = await _move(c, alice_h, dup, dst)
        assert r.status_code == 409, r.text
        _ok("대상 위치 동명 409")

        # 8. 루트 폴더 자체는 이동 불가 400 — dst 의 부모가 개인 루트 폴더 행이다.
        real_root = (await c.get(f"/api/files/{dst}", headers=alice_h)).json()[
            "parent_folder_id"
        ]
        r = await _move(c, alice_h, real_root, dst)
        assert r.status_code == 400, r.text
        _ok("루트 폴더 이동 400")

        # 9. 휴지통 항목 이동 409
        await c.post(f"/api/files/{dup}/delete", headers=alice_h)
        r = await _move(c, alice_h, dup, dst)
        assert r.status_code == 409, r.text
        _ok("휴지통 항목 이동 409")

        # 10. foldersOnly — 파일은 빠지고 폴더만
        r = await c.get(
            "/api/files", headers=alice_h, params={"parentId": dst, "foldersOnly": "true"}
        )
        assert r.status_code == 200, r.text
        assert all(it["is_folder"] for it in r.json()["items"]), r.text
        assert src in {it["id"] for it in r.json()["items"]}
        _ok("foldersOnly 목록 (폴더만)")

        # 11. 그룹 read 공유 — bob 은 alice 의 shared 폴더를 읽기만 가능
        shared = await _mkfolder(c, alice_h, "shared", None)
        inner = await _upload(c, alice_h, "inner.txt", shared)
        r = await c.post("/api/groups", headers=alice_h, json={"name": "G팀"})
        gid = r.json()["id"]
        r = await c.post(
            f"/api/groups/{gid}/members",
            headers=alice_h,
            json={"user_id": bob_id, "role": "member"},
        )
        assert r.status_code == 201, r.text
        r = await c.post(
            f"/api/files/{shared}/permissions",
            headers=alice_h,
            json={"group_id": gid, "permission": "read", "inherit_to_children": True},
        )
        assert r.status_code == 201, r.text
        bob_root_folder = await _mkfolder(c, bob_h, "bob-box", None)
        _ok("shared 폴더에 G read 부여 + bob 폴더 준비")

        # read 만으로는 꺼내올 수 없다 (대상 항목 write 없음 → 404).
        r = await _move(c, bob_h, inner, bob_root_folder)
        assert r.status_code == 404, r.text
        # 읽기 전용 폴더에 밀어 넣기도 불가.
        bob_file = await _upload(c, bob_h, "bob.txt", bob_root_folder)
        r = await _move(c, bob_h, bob_file, shared)
        assert r.status_code == 404, r.text
        _ok("read 공유 폴더 — 꺼내기/밀어넣기 모두 차단")

        # 12. write 로 승격하면 양방향 허용 (캐시 무효화가 즉시 반영돼야 한다)
        r = await c.put(
            f"/api/files/{shared}/permissions/{gid}",
            headers=alice_h,
            json={"permission": "write"},
        )
        assert r.status_code == 200, r.text
        r = await _move(c, bob_h, bob_file, shared)
        assert r.status_code == 200, r.text
        assert bob_file in await _child_ids(c, alice_h, shared)
        _ok("write 승격 후 공유 폴더로 이동 성공")

        # 13. 꺼내오기는 여전히 불가 — 출발지(shared)는 write 지만 alice 소유 파일 inner 를
        #     bob 의 폴더로 옮기는 것은 허용된다. 반대로 출발지 write 가 없으면 막혀야 하므로
        #     읽기 전용으로 되돌려 출발지 게이팅만 따로 확인한다.
        r = await c.put(
            f"/api/files/{shared}/permissions/{gid}",
            headers=alice_h,
            json={"permission": "read"},
        )
        assert r.status_code == 200, r.text
        r = await _move(c, bob_h, bob_file, bob_root_folder)
        assert r.status_code == 404, r.text
        _ok("read 로 되돌리면 출발지 게이팅으로 꺼내기 차단")

    print("\n파일/폴더 이동 통합 시나리오 전체 통과.")


def main() -> int:
    try:
        asyncio.run(scenario())
    except AssertionError as exc:
        print(f"\n[FAIL] {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
