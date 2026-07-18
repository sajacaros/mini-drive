"""Admin stats/그룹/공유/감사 응답 스키마 단위 테스트 (DB 불필요).

get_stats 가 반환하는 dict 형태가 AdminStatsResponse 로 그대로 검증되는지 확인해
프론트 계약(활성/비활성 공유 카운트, top_users 형태 등)이 깨지지 않게 고정한다.
"""

from __future__ import annotations

from datetime import UTC, datetime

from app.schemas.admin import (
    AdminAuditLogResponse,
    AdminGroupResponse,
    AdminShareResponse,
    AdminStatsResponse,
)


def test_stats_schema_from_service_shape() -> None:
    # get_stats() 가 만드는 dict 와 동일한 형태.
    stats = {
        "total_users": 4,
        "users_by_status": {"active": 3, "pending": 1},
        "total_files": 12,
        "total_folders": 5,
        "total_storage_used": 2048,
        "total_shares": {"active": 2, "inactive": 1, "total": 3},
        "total_groups": 2,
        "top_users": [
            {"email": "a@x.com", "storage_used": 1024, "max_storage": 10_737_418_240},
        ],
    }
    resp = AdminStatsResponse(**stats)
    assert resp.total_shares.active == 2
    assert resp.total_shares.total == 3
    assert resp.top_users[0].email == "a@x.com"
    # 직렬화가 안정적인지 (프론트 계약).
    dumped = resp.model_dump()
    assert dumped["total_shares"] == {"active": 2, "inactive": 1, "total": 3}
    assert dumped["users_by_status"] == {"active": 3, "pending": 1}


def test_group_response_shape() -> None:
    resp = AdminGroupResponse(
        id=1,
        name="개발1팀",
        description=None,
        owner_id=7,
        owner_email="owner@x.com",
        is_active=True,
        member_count=3,
        file_count=10,
        created_at=datetime.now(UTC),
    )
    assert resp.member_count == 3
    assert resp.file_count == 10


def test_share_response_shape() -> None:
    resp = AdminShareResponse(
        id=1,
        file_id=2,
        file_name="report.pdf",
        created_by=7,
        creator_email="u@x.com",
        permission="read",
        is_active=False,
        download_count=4,
        max_downloads=10,
        expires_at=None,
        created_at=datetime.now(UTC),
    )
    assert resp.is_active is False
    assert resp.file_name == "report.pdf"


def test_audit_log_response_shape() -> None:
    resp = AdminAuditLogResponse(
        id=1,
        actor_id=7,
        actor_email="admin@x.com",
        action="share.force_disable",
        target_type="share",
        target_id=3,
        detail={"is_active": {"from": True, "to": False}},
        created_at=datetime.now(UTC),
    )
    assert resp.action == "share.force_disable"
    assert resp.detail == {"is_active": {"from": True, "to": False}}
