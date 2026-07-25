"""휴지통 보존 기간 자동 정리 단위 테스트 (spec/trash-retention-purge.md) — DB/Redis 없이.

검증 축: 실행 시각 계산(벽시계 고정·KST 경계) / purge 이벤트 필터(행이 없어도 소유자만 통과) /
요약 이벤트 페이로드 / 보존 기간 0 이면 아무 일도 하지 않음.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from types import SimpleNamespace
from zoneinfo import ZoneInfo

import pytest

from app.services import file_events as fe
from app.services import trash as trash_service

KST = ZoneInfo("Asia/Seoul")


class FakeRedis:
    """publish 만 캡처하는 최소 페이크 (test_file_events_unit 와 같은 관용구)."""

    def __init__(self) -> None:
        self.published: list[tuple[str, str]] = []

    async def publish(self, channel: str, message: str) -> int:
        self.published.append((channel, message))
        return 1


class TestNextRunAt:
    def test_same_day_when_target_is_later(self) -> None:
        now = datetime(2026, 7, 25, 1, 30, tzinfo=KST)
        assert trash_service.next_run_at(now, 4) == datetime(2026, 7, 25, 4, 0, tzinfo=KST)

    def test_next_day_when_target_has_passed(self) -> None:
        now = datetime(2026, 7, 25, 9, 0, tzinfo=KST)
        assert trash_service.next_run_at(now, 4) == datetime(2026, 7, 26, 4, 0, tzinfo=KST)

    def test_exactly_on_target_goes_to_next_day(self) -> None:
        """정각에 깨어났을 때 같은 회차를 두 번 돌지 않는다."""
        now = datetime(2026, 7, 25, 4, 0, tzinfo=KST)
        assert trash_service.next_run_at(now, 4) == datetime(2026, 7, 26, 4, 0, tzinfo=KST)

    def test_utc_input_is_converted_to_kst(self) -> None:
        # 2026-07-25 20:00 UTC == 2026-07-26 05:00 KST → 당일 04:00 은 지났으므로 27일.
        now = datetime(2026, 7, 25, 20, 0, tzinfo=UTC)
        target = trash_service.next_run_at(now, 4)
        assert target.astimezone(KST) == datetime(2026, 7, 27, 4, 0, tzinfo=KST)

    def test_restart_does_not_shift_run_time(self) -> None:
        """기동 시각이 달라도 목표 시각은 같다 — 간격 기반 sleep 과의 차이."""
        targets = {
            trash_service.next_run_at(datetime(2026, 7, 25, h, 17, tzinfo=KST), 4).hour
            for h in (0, 5, 13, 23)
        }
        assert targets == {4}

    def test_hour_defaults_to_settings(self, monkeypatch) -> None:
        monkeypatch.setattr(trash_service.settings, "trash_purge_hour", 6)
        now = datetime(2026, 7, 25, 1, 0, tzinfo=KST)
        assert trash_service.next_run_at(now).hour == 6


class TestPurgeEventFilter:
    """행이 사라진 뒤에도 소유자에게는 전달돼야 한다 — 기존 필터는 조회 실패로 전부 버린다."""

    async def test_owner_receives_purge_without_row(self) -> None:
        user = SimpleNamespace(id=7)
        event = {"type": "purge", "file_id": 42, "owner_id": 7}
        assert await fe.user_can_receive_event(user, event) is True

    async def test_other_user_does_not_receive_purge(self) -> None:
        user = SimpleNamespace(id=8)
        event = {"type": "purge", "file_id": 42, "owner_id": 7}
        assert await fe.user_can_receive_event(user, event) is False

    async def test_summary_event_without_file_id_passes_to_owner(self) -> None:
        """요약 이벤트는 file_id 가 없다 — 타입 가드보다 purge 분기가 앞서야 통과한다."""
        user = SimpleNamespace(id=7)
        event = {"type": "purge", "file_id": None, "owner_id": 7, "purged": 12}
        assert await fe.user_can_receive_event(user, event) is True

    async def test_missing_owner_id_is_rejected(self) -> None:
        user = SimpleNamespace(id=7)
        assert await fe.user_can_receive_event(user, {"type": "purge", "file_id": 1}) is False


class TestPurgeEventPayload:
    async def test_purge_payload(self, monkeypatch) -> None:
        fake = FakeRedis()
        monkeypatch.setattr(fe, "redis_client", fake)
        await fe.publish_purge_event(
            file_id=42, parent_folder_id=7, actor_id=None, name="old.pdf", owner_id=3
        )
        channel, raw = fake.published[0]
        assert channel == fe.FILE_EVENTS_CHANNEL
        payload = json.loads(raw)
        assert payload["type"] == "purge"
        assert payload["file_id"] == 42
        assert payload["owner_id"] == 3
        assert payload["actor_id"] is None
        assert payload["name"] == "old.pdf"
        # 자동 정리 경로에서 컨테이너 정규화(DB 조회)를 하지 않으므로 원본 값이 그대로 실린다.
        assert payload["parent_folder_id"] == 7

    async def test_summary_payload(self, monkeypatch) -> None:
        fake = FakeRedis()
        monkeypatch.setattr(fe, "redis_client", fake)
        await fe.publish_purge_summary(owner_id=5, purged=120)
        payload = json.loads(fake.published[0][1])
        assert payload["type"] == "purge"
        assert payload["owner_id"] == 5
        assert payload["purged"] == 120
        assert payload["file_id"] is None


class TestDisabled:
    async def test_retention_zero_does_nothing(self, monkeypatch) -> None:
        """기본값(0)에서는 DB/Redis 를 건드리지 않는다 — 세션 없이 호출해도 안전해야 한다."""
        monkeypatch.setattr(trash_service.settings, "trash_retention_days", 0)
        result = await trash_service.purge_expired(None, None)  # type: ignore[arg-type]
        assert result.disabled is True
        assert (result.roots, result.rows, result.bytes_reclaimed) == (0, 0, 0)

    @pytest.mark.parametrize("dry_run", [True, False])
    async def test_retention_zero_reports_disabled_for_both_modes(
        self, monkeypatch, dry_run: bool
    ) -> None:
        monkeypatch.setattr(trash_service.settings, "trash_retention_days", 0)
        result = await trash_service.purge_expired(None, None, dry_run=dry_run)  # type: ignore[arg-type]
        assert result.disabled is True
