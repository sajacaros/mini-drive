"""BeeMate 단위 테스트 — DB 없이 (spec/beemate.md).

검증 축: 요청 스키마(이름 공백 정리·플랫폼 제한), 만료 기준 시각, 응답의 expires_at,
키·채널 이름 규약. 소유 판정·정리·실시간은 실 DB 통합 시나리오(integration_beemate)가 본다.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from pydantic import ValidationError

from app.models import BeemateTransfer
from app.schemas.beemate import DeviceCreateRequest, DeviceUpdateRequest
from app.services import beemate


def test_device_name_is_stripped() -> None:
    assert DeviceCreateRequest(name="  내 PC ", platform="web").name == "내 PC"
    assert DeviceUpdateRequest(name=" 갤럭시").name == "갤럭시"


@pytest.mark.parametrize("name", ["", "   "])
def test_device_name_rejects_blank(name: str) -> None:
    with pytest.raises(ValidationError):
        DeviceCreateRequest(name=name, platform="android")


def test_device_platform_is_limited() -> None:
    with pytest.raises(ValidationError):
        DeviceCreateRequest(name="x", platform="windows")  # type: ignore[arg-type]


def test_expiry_cutoff_follows_ttl(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(beemate.settings, "beemate_transfer_ttl_hours", 6)
    now = datetime(2026, 11, 21, 12, tzinfo=UTC)
    assert beemate.expiry_cutoff(now) == datetime(2026, 11, 21, 6, tzinfo=UTC)


def test_transfer_response_carries_expiry(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(beemate.settings, "beemate_transfer_ttl_hours", 24)
    created = datetime(2026, 11, 21, 9, tzinfo=UTC)
    t = BeemateTransfer(
        id=7,
        user_id=1,
        to_device_id=2,
        from_device_id=None,
        name="a.png",
        mime_type="image/png",
        size=10,
        object_key="beemate/1/7",
        created_at=created,
    )
    r = beemate.to_transfer_response(t)
    assert r.expires_at == created + timedelta(hours=24)
    assert r.from_device_id is None


def test_key_and_channel_names() -> None:
    assert beemate.build_object_key(3, 42) == "beemate/3/42"
    assert beemate.events_channel(5) == "beemate:events:5"
    assert beemate.online_key(5) == "beemate:online:5"
