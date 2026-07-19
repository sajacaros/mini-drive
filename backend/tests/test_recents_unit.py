"""최근 이용 항목 단위 테스트 (Phase 8-3) — DB 없이 페이크 세션.

검증 축: 기록 fail-open(실패 시 롤백·예외 없음) / 조회 limit 클램프(최대 50) /
최신순 정렬은 SQL(ORDER BY last_accessed_at DESC)이 보장하므로 슬라이스 개수만 확인.
upsert·100개 상한 등 postgres 특화 동작은 integration_drive_ux 에서 실 DB 로 검증한다.
"""

from __future__ import annotations

from types import SimpleNamespace

from app.services import recents


class _FakeResult:
    def __init__(self, rows: list) -> None:
        self._rows = rows

    def all(self) -> list:
        return self._rows


class _FakeSession:
    """execute/commit/rollback 만 지원. execute 는 항상 준비된 rows 를 돌려준다."""

    def __init__(self, rows: list | None = None, *, fail: bool = False) -> None:
        self._rows = rows or []
        self.fail = fail
        self.committed = False
        self.rolled_back = False
        self.execute_calls = 0

    async def execute(self, *_a, **_k) -> _FakeResult:
        self.execute_calls += 1
        if self.fail:
            raise RuntimeError("boom")
        return _FakeResult(self._rows)

    async def commit(self) -> None:
        self.committed = True

    async def rollback(self) -> None:
        self.rolled_back = True


class TestRecordRecent:
    async def test_happy_path_upserts_and_prunes(self) -> None:
        session = _FakeSession()
        await recents.record_recent(session, user_id=1, file_id=9)
        # upsert + prune = execute 2회, 그리고 commit.
        assert session.execute_calls == 2
        assert session.committed is True

    async def test_fail_open_rolls_back(self) -> None:
        session = _FakeSession(fail=True)
        # 예외를 전파하지 않는다(미리보기/다운로드는 이미 성공).
        await recents.record_recent(session, user_id=1, file_id=9)
        assert session.rolled_back is True
        assert session.committed is False


def _owned(file_id: int, uid: int) -> tuple:
    return (SimpleNamespace(id=file_id, user_id=uid), None)


class TestListRecentLimit:
    async def test_limit_clamped_to_max(self) -> None:
        rows = [_owned(i, 1) for i in range(60)]
        session = _FakeSession(rows)
        user = SimpleNamespace(id=1)
        out = await recents.list_recent(session, user, limit=1000)
        assert len(out) == recents.MAX_RECENT_LIMIT  # 50 으로 클램프

    async def test_limit_honored_within_bounds(self) -> None:
        rows = [_owned(i, 1) for i in range(10)]
        session = _FakeSession(rows)
        user = SimpleNamespace(id=1)
        out = await recents.list_recent(session, user, limit=3)
        assert len(out) == 3
        assert [f.id for f in out] == [0, 1, 2]

    async def test_zero_or_negative_clamped_to_one(self) -> None:
        rows = [_owned(i, 1) for i in range(5)]
        session = _FakeSession(rows)
        user = SimpleNamespace(id=1)
        out = await recents.list_recent(session, user, limit=0)
        assert len(out) == 1
