"""배치 업로드(폴더 업로드) 통합 검증 (실제 postgres+redis+minio 필요, pytest 미수집).

시나리오: 중첩 폴더 업로드 → 트리 확인 → folders 맵 확인 → 기존 폴더 병합 → 동명 충돌 시
부분 성공 → 경로 규칙 위반 항목만 실패 → dirs 만 보내는 트리 확정 요청(빈 폴더 포함) →
파일/폴더 이름 충돌 → 개수 상한 413 → files/paths 개수 불일치 422.

env: DATABASE_URL / REDIS_URL / MINIO_ENDPOINT / MINIO_BUCKET. `python -m tests.integration_batch`.
"""

from __future__ import annotations

import asyncio
import sys

import httpx
from httpx import ASGITransport

import app.models  # noqa: F401 - 전체 모델을 metadata 에 등록
from app.core.config import settings
from app.core.database import Base, engine
from app.core.redis import redis_client
from app.main import app
from app.services.storage import storage_service
from tests._bootstrap import register_active, setup_admin
from tests._dbreset import stamp_alembic_head

ALICE = {"email": "alice@example.com", "password": "Passw0rd!", "display_name": "Alice"}


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


def _multipart(
    entries: list[tuple[str, bytes]], dirs: list[str] | None = None
) -> list[tuple[str, tuple[str | None, bytes | str, str] | str]]:
    """(경로, 내용) 목록을 files/paths 반복 필드로 변환한다."""
    parts: list = []
    for path, content in entries:
        parts.append(("files", (path.split("/")[-1], content, "application/octet-stream")))
        parts.append(("paths", (None, path)))
    for d in dirs or []:
        parts.append(("dirs", (None, d)))
    return parts


async def _children(c: httpx.AsyncClient, h: dict[str, str], parent: int | None) -> dict[str, dict]:
    # 목록 API 의 쿼리 파라미터는 alias 가 걸려 있다 (routes/files.py: Query(alias="parentId")).
    params = {"parentId": parent} if parent is not None else {}
    r = await c.get("/api/files", headers=h, params=params)
    assert r.status_code == 200, r.text
    return {it["name"]: it for it in r.json()["items"]}


async def scenario() -> None:
    await _reset()

    transport = ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test", timeout=60) as c:
        _admin_h, code = await setup_admin(c)
        alice_h, _alice_id = await register_active(c, code, ALICE)
        _ok("셋업 + 코드 가입 → alice 로그인")

        # 1. 중첩 폴더 업로드
        r = await c.post(
            "/api/files/batch",
            headers=alice_h,
            files=_multipart(
                [
                    ("사진/여행/한라산.txt", b"halla"),
                    ("사진/여행/성산.txt", b"seongsan"),
                    ("사진/프로필.txt", b"profile"),
                    ("메모.txt", b"memo"),
                ]
            ),
        )
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["succeeded"] == 4, body
        assert body["failed"] == 0, body
        assert set(body["folders"]) == {"사진", "사진/여행"}, body["folders"]
        _ok("중첩 폴더 업로드 4건 (folders 맵에 조상 경로 포함)")

        # 2. 트리 구조 확인
        root = await _children(c, alice_h, None)
        assert set(root) == {"사진", "메모.txt"}, list(root)
        assert root["사진"]["is_folder"] is True
        photos = await _children(c, alice_h, root["사진"]["id"])
        assert set(photos) == {"여행", "프로필.txt"}, list(photos)
        trip = await _children(c, alice_h, photos["여행"]["id"])
        assert set(trip) == {"한라산.txt", "성산.txt"}, list(trip)
        assert trip["한라산.txt"]["size"] == 5
        _ok("폴더 트리가 경로대로 만들어짐")

        # 3. 기존 폴더 병합 — 같은 경로에 다른 파일을 올리면 폴더를 재사용한다.
        existing_trip_id = photos["여행"]["id"]
        r = await c.post(
            "/api/files/batch",
            headers=alice_h,
            files=_multipart([("사진/여행/우도.txt", b"udo")]),
        )
        assert r.status_code == 200, r.text
        assert r.json()["succeeded"] == 1, r.text
        assert r.json()["folders"]["사진/여행"] == existing_trip_id, r.text
        trip = await _children(c, alice_h, existing_trip_id)
        assert set(trip) == {"한라산.txt", "성산.txt", "우도.txt"}, list(trip)
        _ok("기존 폴더 재사용(병합) — 새 폴더를 만들지 않음")

        # 4. 부분 성공 — 동명 파일은 409, 나머지는 저장된다.
        r = await c.post(
            "/api/files/batch",
            headers=alice_h,
            files=_multipart(
                [
                    ("사진/여행/한라산.txt", b"dup"),   # 이미 있음 → 409
                    ("사진/여행/마라도.txt", b"mara"),  # 신규 → 성공
                ]
            ),
        )
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["succeeded"] == 1 and body["failed"] == 1, body
        errors = {i["path"]: i for i in body["items"] if i["status"] == "error"}
        assert errors["사진/여행/한라산.txt"]["code"] == 409, errors
        created = {i["path"] for i in body["items"] if i["status"] == "created"}
        assert created == {"사진/여행/마라도.txt"}, created
        _ok("동명 충돌 부분 성공 (409 항목만 실패, 나머지 저장)")

        # 5. 경로 규칙 위반은 해당 항목만 422 로 떨어진다.
        r = await c.post(
            "/api/files/batch",
            headers=alice_h,
            files=_multipart(
                [
                    ("../탈출.txt", b"escape"),
                    ("사진/정상.txt", b"fine"),
                ]
            ),
        )
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["succeeded"] == 1 and body["failed"] == 1, body
        bad = next(i for i in body["items"] if i["status"] == "error")
        assert bad["code"] == 422 and "상위 경로" in bad["detail"], bad
        # 탈출이 실제로 막혔는지 — 루트에 "탈출.txt" 가 생기지 않아야 한다.
        root = await _children(c, alice_h, None)
        assert "탈출.txt" not in root, list(root)
        _ok("경로 탈출(..) 항목만 422, 파일은 생성되지 않음")

        # 6. dirs 만 보내는 트리 확정 요청 — 빈 폴더도 만들어진다.
        r = await c.post(
            "/api/files/batch",
            headers=alice_h,
            files=_multipart([], dirs=["보관함/2026/빈폴더", "사진/여행"]),
        )
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["succeeded"] == 0 and body["failed"] == 0, body
        assert body["folders"]["보관함/2026/빈폴더"] > 0, body["folders"]
        assert body["folders"]["사진/여행"] == existing_trip_id, body["folders"]
        archive = await _children(c, alice_h, body["folders"]["보관함/2026"])
        assert set(archive) == {"빈폴더"}, list(archive)
        _ok("dirs 전용 요청으로 빈 폴더 생성 + 기존 폴더 id 회수")

        # 7. 이름이 겹치는 파일을 폴더로 쓰려 하면 409.
        r = await c.post(
            "/api/files/batch",
            headers=alice_h,
            files=_multipart([("메모.txt/안쪽.txt", b"x")]),
        )
        assert r.status_code == 200, r.text
        bad = r.json()["items"][0]
        assert bad["status"] == "error" and bad["code"] == 409, bad
        _ok("파일과 이름이 겹치는 폴더 경로 409")

        # 8. 썸네일을 만들 수 없는 이미지가 섞여도 배치가 죽지 않는다.
        #
        # 회귀: image/svg+xml 은 mime 이 image/ 로 시작해 썸네일 대상으로 잡혔는데 PIL 이
        # 열지 못해 예외가 났고, 그 처리 경로가 rollback 뒤 expire 된 file.id 를 읽어
        # MissingGreenlet → 500 으로 요청 전체를 무너뜨렸다. 이미 저장된 앞선 파일까지
        # 클라이언트에서 실패로 처리됐다. 실제로 SVG 2개가 배치 두 개를 통째로 날렸다.
        svg = b'<svg xmlns="http://www.w3.org/2000/svg"><rect width="1" height="1"/></svg>'
        r = await c.post(
            "/api/files/batch",
            headers=alice_h,
            files=[
                ("files", ("그림.svg", svg, "image/svg+xml")),
                ("paths", (None, "혼합/그림.svg")),
                ("files", ("가짜.png", b"not really a png", "image/png")),
                ("paths", (None, "혼합/가짜.png")),
                ("files", ("메모.txt", b"plain", "text/plain")),
                ("paths", (None, "혼합/메모.txt")),
            ],
        )
        assert r.status_code == 200, r.text  # 500 이면 회귀
        body = r.json()
        assert body["succeeded"] == 3 and body["failed"] == 0, body
        mixed = await _children(c, alice_h, body["folders"]["혼합"])
        assert set(mixed) == {"그림.svg", "가짜.png", "메모.txt"}, list(mixed)
        _ok("썸네일 불가 이미지(SVG/깨진 PNG)가 섞여도 전부 저장 (500 회귀 방지)")

        # 9. 개수 상한 초과 413 (본문은 작게 유지).
        many = [(f"대량/{i}.txt", b"x") for i in range(settings.max_batch_files + 1)]
        r = await c.post("/api/files/batch", headers=alice_h, files=_multipart(many))
        assert r.status_code == 413, r.text
        _ok(f"파일 수 상한({settings.max_batch_files}) 초과 413")

        # 10. files/paths 개수 불일치 422.
        r = await c.post(
            "/api/files/batch",
            headers=alice_h,
            files=[
                ("files", ("a.txt", b"a", "application/octet-stream")),
                ("paths", (None, "a.txt")),
                ("paths", (None, "b.txt")),
            ],
        )
        assert r.status_code == 422, r.text
        _ok("files/paths 개수 불일치 422")

        # 11. 본문 크기 상한은 미들웨어가 파싱 전에 막는다 (Content-Length 기준).
        r = await c.post(
            "/api/files/batch",
            headers={**alice_h, "Content-Length": str(settings.max_batch_bytes + 1)},
            content=b"x",
        )
        assert r.status_code == 413, r.text
        _ok("Content-Length 상한 초과 413 (파싱 전 차단)")

    await engine.dispose()
    await redis_client.aclose()
    print("\n배치 업로드 통합 시나리오 전체 통과.")


def main() -> int:
    try:
        asyncio.run(scenario())
    except AssertionError as exc:
        print(f"\n[FAIL] {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
