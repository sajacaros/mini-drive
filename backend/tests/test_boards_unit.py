"""게시판 권한 판정 순수 로직 단위 테스트 (DB/MinIO 불필요) — spec/group-board.md.

검증 축: 누적(최고 수준) / 두 단계뿐인 순위 / 할당이 없으면 None / 첨부 키 규약.

드라이브의 `resolve_effective_permission`(조상 경로·상속·만료)과 이름이 같지만 다른 함수다.
게시판은 상속이 없어 입력이 "내가 받은 권한들"의 평평한 목록 하나뿐이다.
"""

from __future__ import annotations

from app.models.enums import BoardPermission
from app.services.boards import (
    build_attachment_key,
    permission_covers,
    resolve_effective_permission,
)

# --- 유효 권한 누적 ----------------------------------------------------------


def test_no_grant_is_none() -> None:
    """할당이 하나도 없으면 None — 라우터는 이걸 404(존재 은닉)로 옮긴다."""
    assert resolve_effective_permission([]) is None


def test_single_grant() -> None:
    assert resolve_effective_permission(["read"]) == BoardPermission.READ
    assert resolve_effective_permission(["write"]) == BoardPermission.WRITE


def test_highest_wins_regardless_of_order() -> None:
    """그룹A read + 그룹B write = write. 순서가 결과를 바꾸지 않는다."""
    assert resolve_effective_permission(["read", "write"]) == BoardPermission.WRITE
    assert resolve_effective_permission(["write", "read"]) == BoardPermission.WRITE


def test_many_reads_never_become_write() -> None:
    """누적은 '최고 수준'이지 '합산'이 아니다 — read 를 아무리 모아도 write 가 되지 않는다."""
    assert resolve_effective_permission(["read"] * 10) == BoardPermission.READ


def test_accepts_enum_members_too() -> None:
    """DB 는 VARCHAR 지만 호출자가 enum 을 넘겨도 같게 동작해야 한다."""
    assert (
        resolve_effective_permission([BoardPermission.READ, BoardPermission.WRITE])
        == BoardPermission.WRITE
    )


# --- 수준 비교 ---------------------------------------------------------------


def test_permission_covers() -> None:
    assert permission_covers(BoardPermission.READ, "read")
    assert permission_covers(BoardPermission.WRITE, "read")
    assert permission_covers(BoardPermission.WRITE, "write")
    # read 만 있으면 쓰기는 못 한다 — 라우터가 403 으로 옮기는 지점이다(404 아님).
    assert not permission_covers(BoardPermission.READ, "write")


def test_no_manage_level_exists() -> None:
    """게시판에는 manage 가 없다. 드라이브의 GroupPermission 을 재사용하지 않은 이유다."""
    assert {p.value for p in BoardPermission} == {"read", "write"}


# --- 첨부 키 규약 ------------------------------------------------------------


def test_attachment_key_prefix() -> None:
    """`board/{board_id}/{post_id}/{uuid}` — 드라이브 키(users/…, versions/…)와 겹치지 않는다."""
    key = build_attachment_key(7, 42)
    assert key.startswith("board/7/42/")
    assert not key.startswith(("users/", "versions/", "thumbnails/"))


def test_attachment_keys_are_unique_for_same_post() -> None:
    """같은 글에 같은 이름을 두 번 올려도 키가 겹치지 않아야 한다(파일명은 DB 가 들고 있다)."""
    keys = {build_attachment_key(1, 1) for _ in range(50)}
    assert len(keys) == 50
