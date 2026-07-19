"""파일 변경 이벤트 단위 테스트 (Phase 8-1) — Redis/DB 없이 모킹.

검증 축: 발행 페이로드 스키마·채널 / 발행 fail-open / 구독자 권한 필터
(비정상 file_id 조기 거부 · 소유자 즉시 통과 · 그룹 권한 유무에 따른 전달/차단).
"""

from __future__ import annotations

import json
from types import SimpleNamespace

from app.services import file_events as fe


class FakeRedis:
    """publish 만 캡처하는 최소 페이크."""

    def __init__(self, *, fail: bool = False) -> None:
        self.published: list[tuple[str, str]] = []
        self.fail = fail

    async def publish(self, channel: str, message: str) -> int:
        if self.fail:
            raise RuntimeError("boom")
        self.published.append((channel, message))
        return 1


class TestPublishPayload:
    async def test_payload_schema_and_channel(self, monkeypatch) -> None:
        fake = FakeRedis()
        monkeypatch.setattr(fe, "redis_client", fake)
        await fe.publish_file_event(
            type="upload",
            file_id=42,
            parent_folder_id=7,
            actor_id=3,
            name="report.pdf",
        )
        assert len(fake.published) == 1
        channel, raw = fake.published[0]
        assert channel == fe.FILE_EVENTS_CHANNEL
        payload = json.loads(raw)
        assert payload["type"] == "upload"
        assert payload["file_id"] == 42
        assert payload["parent_folder_id"] == 7
        assert payload["actor_id"] == 3
        assert payload["name"] == "report.pdf"
        assert isinstance(payload["ts"], str) and payload["ts"]

    async def test_root_parent_is_null(self, monkeypatch) -> None:
        fake = FakeRedis()
        monkeypatch.setattr(fe, "redis_client", fake)
        await fe.publish_file_event(
            type="folder", file_id=1, parent_folder_id=None, actor_id=1, name="새 폴더"
        )
        payload = json.loads(fake.published[0][1])
        assert payload["parent_folder_id"] is None

    async def test_publish_fail_open(self, monkeypatch) -> None:
        # 발행 실패는 예외를 전파하지 않는다(파일 조작을 막지 않는다).
        monkeypatch.setattr(fe, "redis_client", FakeRedis(fail=True))
        await fe.publish_file_event(
            type="delete", file_id=9, parent_folder_id=None, actor_id=1, name="x"
        )


class _FakeSession:
    def __init__(self, file: object | None) -> None:
        self._file = file

    async def __aenter__(self) -> _FakeSession:
        return self

    async def __aexit__(self, *exc: object) -> bool:
        return False

    async def get(self, _model: object, _pk: int) -> object | None:
        return self._file


def _use_session(monkeypatch, file: object | None) -> None:
    monkeypatch.setattr(fe, "SessionFactory", lambda: _FakeSession(file))


class TestPermissionFilter:
    async def test_non_int_file_id_rejected(self, monkeypatch) -> None:
        # 조기 거부 — 세션조차 열지 않는다.
        user = SimpleNamespace(id=1)
        assert await fe.user_can_receive_event(user, {"file_id": "nope"}) is False
        assert await fe.user_can_receive_event(user, {}) is False

    async def test_missing_file_row_rejected(self, monkeypatch) -> None:
        _use_session(monkeypatch, None)
        user = SimpleNamespace(id=1)
        assert await fe.user_can_receive_event(user, {"file_id": 5}) is False

    async def test_owner_passes_without_group_check(self, monkeypatch) -> None:
        _use_session(monkeypatch, SimpleNamespace(id=5, user_id=1))

        async def _boom(*_a, **_k):  # get_access_level 은 호출되면 안 된다
            raise AssertionError("owner 는 그룹 권한을 조회하지 않아야 한다")

        monkeypatch.setattr(fe.permissions_service, "get_access_level", _boom)
        user = SimpleNamespace(id=1)
        assert await fe.user_can_receive_event(user, {"file_id": 5}) is True

    async def test_non_owner_with_group_access_passes(self, monkeypatch) -> None:
        _use_session(monkeypatch, SimpleNamespace(id=5, user_id=999))

        async def _level(*_a, **_k):
            return object()  # None 이 아니면 read 이상

        monkeypatch.setattr(fe.permissions_service, "get_access_level", _level)
        user = SimpleNamespace(id=1)
        assert await fe.user_can_receive_event(user, {"file_id": 5}) is True

    async def test_non_owner_without_access_blocked(self, monkeypatch) -> None:
        _use_session(monkeypatch, SimpleNamespace(id=5, user_id=999))

        async def _none(*_a, **_k):
            return None

        monkeypatch.setattr(fe.permissions_service, "get_access_level", _none)
        user = SimpleNamespace(id=1)
        assert await fe.user_can_receive_event(user, {"file_id": 5}) is False
