"""BeeMate 단위 테스트 — DB 없이 (spec/beemate.md).

검증 축: 요청 스키마(이름 공백 정리·플랫폼 제한), 만료 기준 시각, 응답의 expires_at,
키·채널 이름 규약. 소유 판정·정리·실시간은 실 DB 통합 시나리오(integration_beemate)가 본다.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from pydantic import ValidationError

from app.models import BeemateTransfer
from app.schemas.beemate import DeviceRegisterRequest, DeviceResponse, DeviceUpdateRequest
from app.services import beemate

CID = "c0ffee00-1234"


def test_device_name_is_stripped() -> None:
    assert DeviceRegisterRequest(client_id=CID, name="  내 PC ", platform="web").name == "내 PC"
    assert DeviceUpdateRequest(name=" 갤럭시").name == "갤럭시"


def test_register_name_is_cut_to_column_length() -> None:
    # 브라우저가 만든 긴 기본 이름도 등록은 되어야 한다 — 컬럼 길이(100)로 자른다.
    assert len(DeviceRegisterRequest(client_id=CID, name="x" * 150, platform="web").name) == 100


@pytest.mark.parametrize("name", ["", "   "])
def test_device_name_rejects_blank(name: str) -> None:
    with pytest.raises(ValidationError):
        DeviceRegisterRequest(client_id=CID, name=name, platform="android")


def test_device_platform_is_limited() -> None:
    with pytest.raises(ValidationError):
        DeviceRegisterRequest(client_id=CID, name="x", platform="windows")  # type: ignore[arg-type]


@pytest.mark.parametrize("cid", ["short", "has space here", "a/b/c/d/e/f", "x" * 65])
def test_client_id_pattern(cid: str) -> None:
    with pytest.raises(ValidationError):
        DeviceRegisterRequest(client_id=cid, name="x", platform="web")


def test_update_needs_at_least_one_field() -> None:
    with pytest.raises(ValidationError):
        DeviceUpdateRequest()
    assert DeviceUpdateRequest(is_hidden=False).is_hidden is False


def _dev(i: int, *, fav: bool = False, online: bool = False) -> DeviceResponse:
    return DeviceResponse(
        id=i,
        name=str(i),
        platform="web",
        is_favorite=fav,
        is_hidden=False,
        created_at=datetime(2026, 11, 1, tzinfo=UTC) + timedelta(days=i),
        last_seen_at=None,
        online=online,
    )


def test_sort_favorites_then_online_then_rest() -> None:
    devices = [
        _dev(1),
        _dev(2, online=True),
        _dev(3, fav=True),
        _dev(4, fav=True, online=True),
        _dev(5, online=True),
    ]
    # 즐겨찾기(켜진 것 먼저) → 켜진 기기 → 나머지, 같은 무리 안은 등록 순.
    assert [d.id for d in beemate.sort_devices(devices)] == [4, 3, 2, 5, 1]


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
