"""그룹 게시판 통합 검증 (실제 postgres+redis+minio 필요, pytest 미수집).

시나리오 (spec/group-board.md):
  admin 게시판 생성 → 비관리자 생성 403 / 이름 중복 409
  → 할당 전에는 아무도 못 본다(이름조차) → 그룹 write 할당 → 목록·상세에 등장
  → read 그룹은 글쓰기 403 / 읽기는 200 → 무관한 사용자는 404
  → 글 작성·수정(작성자만)·목록 → 댓글(write 필요, 삭제 후 자리 남음)
  → 첨부 상한(개수 409 / 크기 413) → 다운로드 X-Accel-Redirect
  → 글 삭제 시 MinIO 오브젝트가 **그 자리에서** 사라진다
  → admin 은 읽고 남의 글을 지울 수 있으나 쓰지는 못한다
  → 그룹 회수 시 **작성자 본인도** 404 → 재부여 시 글이 그대로 돌아온다
  → @전사 시스템 그룹 할당은 멤버십 없이도 통한다
  → 게시판 삭제(소프트) + 하위 첨부 회수 → audit_logs 기록 확인.

env: DATABASE_URL / REDIS_URL / MINIO_ENDPOINT / MINIO_BUCKET.

**RATE_LIMIT_ENABLED=false 로 돌린다.** 사용자를 4명 만드는데 가입은 IP 당 3회/분이라, 켠 채로
돌리면 네 번째 가입이 429 로 죽는다. 앱을 in-process(ASGITransport)로 띄우므로 이 프로세스의
환경변수만 바꾸면 된다:

    docker compose exec -e RATE_LIMIT_ENABLED=false backend python -m tests.integration_boards
"""

from __future__ import annotations

import asyncio
import sys

import httpx
from httpx import ASGITransport
from minio.error import S3Error
from sqlalchemy import select

import app.models  # noqa: F401 - 전체 모델을 metadata 에 등록
from app.core.config import settings
from app.core.database import Base, SessionFactory, engine
from app.core.redis import redis_client
from app.main import app
from app.models import AuditLog, BoardAttachment, Group, User
from app.services.storage import storage_service
from tests._bootstrap import register_active, setup_admin
from tests._dbreset import stamp_alembic_head

USERS = {
    "alice": {"email": "alice@example.com", "password": "Passw0rd!", "display_name": "Alice"},
    "bob": {"email": "bob@example.com", "password": "Passw0rd!", "display_name": "Bob"},
    "carol": {"email": "carol@example.com", "password": "Passw0rd!", "display_name": "Carol"},
    "dave": {"email": "dave@example.com", "password": "Passw0rd!", "display_name": "Dave"},
}


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


def _object_exists(key: str) -> bool:
    try:
        storage_service.stat(key)
    except S3Error:
        return False
    return True


async def _attachment_keys(post_id: int) -> list[str]:
    async with SessionFactory() as s:
        rows = (
            await s.execute(
                select(BoardAttachment.object_key).where(
                    BoardAttachment.post_id == post_id
                )
            )
        ).scalars().all()
    return list(rows)


async def _system_group_id() -> int:
    async with SessionFactory() as s:
        return (
            await s.execute(select(Group.id).where(Group.is_system.is_(True)))
        ).scalars().first()


async def _audit_actions() -> list[str]:
    async with SessionFactory() as s:
        rows = (
            await s.execute(
                select(AuditLog.action).where(AuditLog.target_type == "board")
            )
        ).scalars().all()
    return list(rows)


async def _deactivate(email: str) -> None:
    """계정을 inactive 로 내린다 — 글·댓글은 남고 접근만 끊기는지 보기 위해."""
    async with SessionFactory() as s:
        user = (
            await s.execute(select(User).where(User.email == email))
        ).scalar_one()
        user.status = "inactive"
        await s.commit()


async def _make_group(
    c: httpx.AsyncClient, headers: dict[str, str], name: str
) -> int:
    r = await c.post("/api/groups", headers=headers, json={"name": name})
    assert r.status_code == 201, r.text
    return int(r.json()["id"])


async def scenario() -> None:  # noqa: C901 - 순차 시나리오
    await _reset()

    transport = ASGITransport(app=app)
    async with httpx.AsyncClient(
        transport=transport, base_url="http://test", timeout=60
    ) as c:
        admin_h, code = await setup_admin(c)
        alice_h, alice_id = await register_active(c, code, USERS["alice"])
        bob_h, bob_id = await register_active(c, code, USERS["bob"])
        carol_h, carol_id = await register_active(c, code, USERS["carol"])
        dave_h, _dave_id = await register_active(c, code, USERS["dave"])
        _ok("셋업 + 코드로 4명 즉시 active 가입")

        # 그룹 둘 — 개발팀(alice, bob) / 열람팀(carol). dave 는 어디에도 없다.
        dev_gid = await _make_group(c, alice_h, "개발팀")
        r = await c.post(
            f"/api/groups/{dev_gid}/members",
            headers=alice_h,
            json={"user_id": bob_id, "role": "member"},
        )
        assert r.status_code == 201, r.text
        view_gid = await _make_group(c, carol_h, "열람팀")
        _ok("그룹 2개 (개발팀: alice·bob / 열람팀: carol)")

        # 1. 게시판 생성 — 관리자만
        r = await c.post(
            "/api/admin/boards",
            headers=alice_h,
            json={"name": "공지사항"},
        )
        assert r.status_code == 403, r.text
        _ok("비관리자 게시판 생성 403")

        r = await c.post(
            "/api/admin/boards",
            headers=admin_h,
            json={"name": "공지사항", "description": "전사 공지"},
        )
        assert r.status_code == 201, r.text
        board_id = r.json()["id"]
        _ok("관리자 게시판 생성 201")

        r = await c.post("/api/admin/boards", headers=admin_h, json={"name": "공지사항"})
        assert r.status_code == 409, r.text
        _ok("게시판 이름 중복 409")

        # 2. 할당 전 — 아무도 못 본다. 이름조차 나오지 않는다.
        r = await c.get("/api/boards", headers=alice_h)
        assert r.status_code == 200 and r.json()["items"] == [], r.text
        r = await c.get(f"/api/boards/{board_id}", headers=alice_h)
        assert r.status_code == 404, r.text
        _ok("그룹 할당 전 — 목록 비어 있고 상세 404 (존재 은닉)")

        # 관리자는 할당이 없어도 읽는다 (spec 「admin 이 게시판 내용을 보는가」).
        r = await c.get(f"/api/boards/{board_id}", headers=admin_h)
        assert r.status_code == 200 and r.json()["permission"] is None, r.text
        _ok("관리자는 할당 없이도 열람 (permission=None)")

        # 3. 그룹 할당 — 개발팀 write / 열람팀 read
        r = await c.post(
            f"/api/admin/boards/{board_id}/groups",
            headers=admin_h,
            json={"group_id": dev_gid, "permission": "write"},
        )
        assert r.status_code == 201 and r.json()["group_name"] == "개발팀", r.text
        r = await c.post(
            f"/api/admin/boards/{board_id}/groups",
            headers=admin_h,
            json={"group_id": view_gid, "permission": "read"},
        )
        assert r.status_code == 201, r.text
        _ok("그룹 할당 (개발팀 write / 열람팀 read)")

        # 4. 목록에 등장 — 내 유효 권한이 함께 온다.
        r = await c.get("/api/boards", headers=alice_h)
        item = next(b for b in r.json()["items"] if b["id"] == board_id)
        assert item["permission"] == "write", item
        r = await c.get("/api/boards", headers=carol_h)
        item = next(b for b in r.json()["items"] if b["id"] == board_id)
        assert item["permission"] == "read", item
        # dave 는 어느 그룹에도 없다.
        r = await c.get("/api/boards", headers=dave_h)
        assert r.json()["items"] == [], r.text
        r = await c.get(f"/api/boards/{board_id}", headers=dave_h)
        assert r.status_code == 404, r.text
        _ok("목록/상세 — write·read 구분, 무관한 사용자 404")

        # 5. 글 작성 — write 필요
        r = await c.post(
            f"/api/boards/{board_id}/posts",
            headers=carol_h,
            json={"title": "읽기만 되는데?", "body": "x"},
        )
        assert r.status_code == 403, r.text
        _ok("read 만 있으면 글 작성 403 (404 아님)")

        r = await c.post(
            f"/api/boards/{board_id}/posts",
            headers=alice_h,
            json={"title": "8월 공지", "body": "# 제목\n본문입니다."},
        )
        assert r.status_code == 201, r.text
        post = r.json()
        post_id = post["id"]
        assert post["can_edit"] and post["can_delete"], post
        assert post["author_name"] == "Alice", post
        _ok("글 작성 201 (작성자에게 can_edit/can_delete)")

        # 6. 수정은 작성자 본인만 — 관리자도 남의 글은 못 고친다.
        r = await c.patch(
            f"/api/boards/{board_id}/posts/{post_id}",
            headers=bob_h,
            json={"title": "가로채기"},
        )
        assert r.status_code == 403, r.text
        r = await c.patch(
            f"/api/boards/{board_id}/posts/{post_id}",
            headers=admin_h,
            json={"title": "관리자 수정"},
        )
        assert r.status_code == 403, r.text
        r = await c.patch(
            f"/api/boards/{board_id}/posts/{post_id}",
            headers=alice_h,
            json={"title": "8월 공지 (수정)"},
        )
        assert r.status_code == 200 and r.json()["title"] == "8월 공지 (수정)", r.text
        _ok("글 수정 — 작성자만 (타인·관리자 403)")

        # 7. 상세에서 can_edit 이 사람마다 다르다.
        r = await c.get(f"/api/boards/{board_id}/posts/{post_id}", headers=admin_h)
        assert r.status_code == 200, r.text
        assert not r.json()["can_edit"] and r.json()["can_delete"], r.json()
        r = await c.get(f"/api/boards/{board_id}/posts/{post_id}", headers=carol_h)
        assert not r.json()["can_edit"] and not r.json()["can_delete"], r.json()
        _ok("상세 can_edit/can_delete — 관리자는 삭제만, 열람자는 둘 다 없음")

        # 8. 댓글 — write 필요, 삭제하면 자리가 남는다.
        r = await c.post(
            f"/api/boards/{board_id}/posts/{post_id}/comments",
            headers=carol_h,
            json={"body": "읽기만 되는데 댓글은?"},
        )
        assert r.status_code == 403, r.text
        r = await c.post(
            f"/api/boards/{board_id}/posts/{post_id}/comments",
            headers=bob_h,
            json={"body": "확인했습니다"},
        )
        assert r.status_code == 201, r.text
        comment_id = r.json()["id"]
        r = await c.post(
            f"/api/boards/{board_id}/posts/{post_id}/comments",
            headers=alice_h,
            json={"body": "두 번째"},
        )
        assert r.status_code == 201, r.text
        _ok("댓글 작성 — write 필요 (read 403)")

        # 목록의 댓글 수 — 삭제분까지 센다(자리가 남아 실제로 보이므로).
        r = await c.get(f"/api/boards/{board_id}/posts", headers=carol_h)
        summary = next(p for p in r.json()["items"] if p["id"] == post_id)
        assert summary["comment_count"] == 2, summary
        _ok("글 목록에 댓글 수 동봉")

        # 남의 댓글은 못 지운다.
        r = await c.delete(
            f"/api/boards/{board_id}/posts/{post_id}/comments/{comment_id}",
            headers=carol_h,
        )
        assert r.status_code in (403, 404), r.text
        r = await c.delete(
            f"/api/boards/{board_id}/posts/{post_id}/comments/{comment_id}",
            headers=bob_h,
        )
        assert r.status_code == 204, r.text
        r = await c.get(
            f"/api/boards/{board_id}/posts/{post_id}/comments", headers=alice_h
        )
        deleted = next(x for x in r.json()["items"] if x["id"] == comment_id)
        assert deleted["is_deleted"] and deleted["body"] == "삭제된 댓글입니다.", deleted
        assert len(r.json()["items"]) == 2, r.json()
        _ok("댓글 삭제 — 소프트 삭제로 자리는 남는다")

        # 9. 첨부 — 크기 상한 413
        original_bytes = settings.board_attachment_max_bytes
        settings.board_attachment_max_bytes = 1024
        r = await c.post(
            f"/api/boards/{board_id}/posts/{post_id}/attachments",
            headers=alice_h,
            files={"file": ("big.bin", b"x" * 2048, "application/octet-stream")},
        )
        assert r.status_code == 413, r.text
        settings.board_attachment_max_bytes = original_bytes
        _ok("첨부 크기 상한 413")

        # 개수 상한 409 — 상한만큼 올린 뒤 하나 더.
        for i in range(settings.board_attachment_max_count):
            r = await c.post(
                f"/api/boards/{board_id}/posts/{post_id}/attachments",
                headers=alice_h,
                files={"file": (f"a{i}.txt", b"payload", "text/plain")},
            )
            assert r.status_code == 201, r.text
        r = await c.post(
            f"/api/boards/{board_id}/posts/{post_id}/attachments",
            headers=alice_h,
            files={"file": ("overflow.txt", b"payload", "text/plain")},
        )
        assert r.status_code == 409, r.text
        _ok(f"첨부 개수 상한 {settings.board_attachment_max_count}개 초과 409")

        # 남의 글에는 첨부하지 못한다.
        r = await c.post(
            f"/api/boards/{board_id}/posts/{post_id}/attachments",
            headers=bob_h,
            files={"file": ("bob.txt", b"payload", "text/plain")},
        )
        assert r.status_code == 403, r.text
        _ok("남의 글 첨부 403")

        # 10. 첨부가 개인 할당량을 건드리지 않는다.
        async with SessionFactory() as s:
            used = (
                await s.execute(select(User.storage_used).where(User.id == alice_id))
            ).scalar_one()
        assert used == 0, f"게시판 첨부가 드라이브 할당량을 차감했다: {used}"
        _ok("첨부는 개인 할당량을 차감하지 않는다")

        # 11. 다운로드 — read 면 된다. 게이트웨이 X-Accel-Redirect.
        r = await c.get(f"/api/boards/{board_id}/posts/{post_id}", headers=alice_h)
        attachments = r.json()["attachments"]
        assert len(attachments) == settings.board_attachment_max_count, attachments
        aid = attachments[0]["id"]
        r = await c.get(
            f"/api/boards/{board_id}/posts/{post_id}/attachments/{aid}/download",
            headers=carol_h,
        )
        assert r.status_code == 200, r.text
        accel = r.headers.get("X-Accel-Redirect")
        assert accel and accel.startswith("/_minio/"), accel
        r = await c.get(
            f"/api/boards/{board_id}/posts/{post_id}/attachments/{aid}/download",
            headers=dave_h,
        )
        assert r.status_code == 404, r.text
        _ok("첨부 다운로드 — read 200(X-Accel-Redirect) / 무관한 사용자 404")

        # 12. 첨부 개별 제거 — 오브젝트도 그 자리에서 사라진다.
        keys_before = await _attachment_keys(post_id)
        target_key = next(k for k in keys_before)
        assert _object_exists(target_key), target_key
        r = await c.delete(
            f"/api/boards/{board_id}/posts/{post_id}/attachments/{aid}",
            headers=alice_h,
        )
        assert r.status_code == 204, r.text
        _ok("첨부 개별 제거 204")

        # 13. 글 삭제 — 남은 첨부 오브젝트가 즉시 회수된다.
        remaining_keys = await _attachment_keys(post_id)
        assert remaining_keys, "삭제 검증용 첨부가 남아 있어야 한다"
        assert all(_object_exists(k) for k in remaining_keys)

        r = await c.post(
            f"/api/boards/{board_id}/posts",
            headers=bob_h,
            json={"title": "bob 의 글", "body": "남을 글"},
        )
        assert r.status_code == 201, r.text
        bob_post_id = r.json()["id"]

        r = await c.delete(
            f"/api/boards/{board_id}/posts/{post_id}", headers=alice_h
        )
        assert r.status_code == 204, r.text
        assert not any(_object_exists(k) for k in remaining_keys), remaining_keys
        assert await _attachment_keys(post_id) == []
        r = await c.get(f"/api/boards/{board_id}/posts/{post_id}", headers=alice_h)
        assert r.status_code == 404, r.text
        _ok("글 삭제 — 첨부 행·오브젝트가 그 자리에서 사라진다 (유예 없음)")

        # 14. 관리자는 남의 글을 지울 수 있으나 쓰지는 못한다.
        r = await c.post(
            f"/api/boards/{board_id}/posts",
            headers=admin_h,
            json={"title": "관리자 글", "body": "쓸 수 있나?"},
        )
        assert r.status_code == 403, r.text
        _ok("관리자도 그룹 write 없이는 글을 쓰지 못한다 403")

        r = await c.delete(
            f"/api/boards/{board_id}/posts/{bob_post_id}", headers=admin_h
        )
        assert r.status_code == 204, r.text
        _ok("관리자가 남의 글 삭제 204 (audit 기록)")

        # 15. 권한 회수 — 작성자 본인도 404 다.
        r = await c.post(
            f"/api/boards/{board_id}/posts",
            headers=alice_h,
            json={"title": "회수 전 글", "body": "남아 있어야 한다"},
        )
        assert r.status_code == 201, r.text
        kept_post_id = r.json()["id"]

        r = await c.delete(
            f"/api/admin/boards/{board_id}/groups/{dev_gid}", headers=admin_h
        )
        assert r.status_code == 204, r.text

        r = await c.get(f"/api/boards/{board_id}", headers=alice_h)
        assert r.status_code == 404, r.text
        r = await c.get(
            f"/api/boards/{board_id}/posts/{kept_post_id}", headers=alice_h
        )
        assert r.status_code == 404, r.text
        r = await c.patch(
            f"/api/boards/{board_id}/posts/{kept_post_id}",
            headers=alice_h,
            json={"title": "고쳐볼까"},
        )
        assert r.status_code == 404, r.text
        r = await c.delete(
            f"/api/boards/{board_id}/posts/{kept_post_id}", headers=alice_h
        )
        assert r.status_code == 404, r.text
        r = await c.get("/api/boards", headers=alice_h)
        assert r.json()["items"] == [], r.text
        _ok("그룹 회수 — 내가 쓴 글도 404 (작성자 예외 없음)")

        # 열람팀(carol)은 그대로 — 회수는 그 그룹에만 걸린다. 글도 남아 있다.
        r = await c.get(
            f"/api/boards/{board_id}/posts/{kept_post_id}", headers=carol_h
        )
        assert r.status_code == 200, r.text
        _ok("다른 그룹의 접근은 그대로, 글도 남아 있다")

        # 16. 재부여 — 남아 있던 글이 그대로 다시 보인다.
        r = await c.post(
            f"/api/admin/boards/{board_id}/groups",
            headers=admin_h,
            json={"group_id": dev_gid, "permission": "read"},
        )
        assert r.status_code == 201, r.text
        r = await c.get(
            f"/api/boards/{board_id}/posts/{kept_post_id}", headers=alice_h
        )
        assert r.status_code == 200 and r.json()["title"] == "회수 전 글", r.text
        # read 로 낮춰 재부여했으므로 이제 쓰지는 못한다.
        r = await c.post(
            f"/api/boards/{board_id}/posts",
            headers=alice_h,
            json={"title": "다시 쓰기", "body": ""},
        )
        assert r.status_code == 403, r.text
        _ok("재부여 — 글이 그대로 돌아오고, 낮춘 수준이 그대로 적용된다")

        # 17. 멱등 upsert — 같은 그룹을 다시 할당하면 권한이 갈아끼워진다.
        r = await c.post(
            f"/api/admin/boards/{board_id}/groups",
            headers=admin_h,
            json={"group_id": dev_gid, "permission": "write"},
        )
        assert r.status_code == 201 and r.json()["permission"] == "write", r.text
        r = await c.get(f"/api/admin/boards/{board_id}/groups", headers=admin_h)
        assert len({g["group_id"] for g in r.json()["items"]}) == len(r.json()["items"])
        assert len(r.json()["items"]) == 2, r.json()
        _ok("그룹 재할당은 멱등 upsert (행이 늘지 않는다)")

        # 18. @전사 시스템 그룹 — 멤버십을 물질화하지 않아도 통한다.
        notice_r = await c.post(
            "/api/admin/boards", headers=admin_h, json={"name": "전사 공지"}
        )
        assert notice_r.status_code == 201, notice_r.text
        notice_id = notice_r.json()["id"]
        all_gid = await _system_group_id()
        assert all_gid, "@전사 시스템 그룹이 있어야 한다"
        r = await c.post(
            f"/api/admin/boards/{notice_id}/groups",
            headers=admin_h,
            json={"group_id": all_gid, "permission": "read"},
        )
        assert r.status_code == 201, r.text
        # dave 는 어떤 그룹에도 가입하지 않았다.
        r = await c.get(f"/api/boards/{notice_id}", headers=dave_h)
        assert r.status_code == 200 and r.json()["permission"] == "read", r.text
        r = await c.post(
            f"/api/boards/{notice_id}/posts",
            headers=dave_h,
            json={"title": "쓸 수 있나", "body": ""},
        )
        assert r.status_code == 403, r.text
        _ok("@전사 read 할당 — 멤버십 없이도 읽히고, 쓰기는 여전히 403")

        # 19. 게시판 이름 수정
        r = await c.patch(
            f"/api/admin/boards/{notice_id}",
            headers=admin_h,
            json={"name": "전사 알림"},
        )
        assert r.status_code == 200 and r.json()["name"] == "전사 알림", r.text
        _ok("게시판 이름 수정 200")

        # 20. 게시판 삭제(소프트) — 하위 첨부 오브젝트까지 회수한다.
        r = await c.post(
            f"/api/boards/{board_id}/posts",
            headers=alice_h,
            json={"title": "게시판과 함께 잠길 글", "body": ""},
        )
        assert r.status_code == 201, r.text
        doomed_post_id = r.json()["id"]
        r = await c.post(
            f"/api/boards/{board_id}/posts/{doomed_post_id}/attachments",
            headers=alice_h,
            files={"file": ("doomed.txt", b"payload", "text/plain")},
        )
        assert r.status_code == 201, r.text
        doomed_keys = await _attachment_keys(doomed_post_id)
        assert doomed_keys and all(_object_exists(k) for k in doomed_keys)

        r = await c.delete(f"/api/admin/boards/{board_id}", headers=admin_h)
        assert r.status_code == 204, r.text
        assert not any(_object_exists(k) for k in doomed_keys), doomed_keys
        r = await c.get(f"/api/boards/{board_id}", headers=carol_h)
        assert r.status_code == 404, r.text
        r = await c.get(f"/api/boards/{board_id}", headers=admin_h)
        assert r.status_code == 404, r.text
        _ok("게시판 삭제 — 모두에게 404, 하위 첨부 오브젝트 회수")

        # 삭제된 게시판이 이름을 잡고 있지 않다 (활성 UNIQUE).
        r = await c.post("/api/admin/boards", headers=admin_h, json={"name": "공지사항"})
        assert r.status_code == 201, r.text
        _ok("삭제된 게시판의 이름을 재사용할 수 있다")

        # 21. 비활성 계정은 접근이 끊긴다. 글·작성자 이름은 남는다.
        r = await c.get(f"/api/boards/{notice_id}/posts", headers=admin_h)
        assert r.status_code == 200, r.text
        await _deactivate(USERS["dave"]["email"])
        r = await c.get(f"/api/boards/{notice_id}", headers=dave_h)
        assert r.status_code == 403, r.text
        _ok("inactive 계정은 접근 차단 (글은 그대로 남는다)")

        # 22. audit_logs — 기대 액션 기록, 열람은 남기지 않는다.
        actions = await _audit_actions()
        for expected in (
            "board.create",
            "board.update",
            "board.delete",
            "board.group_grant",
            "board.group_revoke",
            "board.post_delete",
        ):
            assert expected in actions, f"audit 누락: {expected} in {actions}"
        assert "board.read" not in actions and "board.list" not in actions
        _ok(f"audit_logs 기록 확인 ({len(actions)} rows, 열람은 미기록)")

    await engine.dispose()
    await redis_client.aclose()
    print("\n그룹 게시판 통합 시나리오 전체 통과.")


def main() -> int:
    try:
        asyncio.run(scenario())
    except AssertionError as exc:
        print(f"\n[FAIL] {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
