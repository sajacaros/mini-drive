"""가입 코드 서비스 (PRD 3.1, 5.11, 6.7 — 가입 코드제).

- 코드 생성: `secrets` 기반 추측 불가 토큰.
- 원자적 소비: 조건부 UPDATE ... RETURNING 으로 검증(활성→만료→사용 횟수)과 use_count 증가를
  한 번에 처리한다(PRD 5.10 패턴, 동시 가입 레이스 방어).
- admin CRUD: 발급/목록/수정. 모든 상태 변경은 audit_logs 에 기록한다.
"""

from __future__ import annotations

import secrets
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import func, or_, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import AuditLog, SignupCode

# 32자 내외의 URL-safe 토큰(VARCHAR(64) 이내). 추측 불가 랜덤 (PRD 10장).
_CODE_NBYTES = 24


class SignupCodeError(Exception):
    """가입 코드 검증/조작 실패. HTTP 상태 코드를 함께 전달한다."""

    def __init__(self, status_code: int, detail: str) -> None:
        super().__init__(detail)
        self.status_code = status_code
        self.detail = detail


def generate_signup_code() -> str:
    """추측 불가한 URL-safe 가입 코드를 생성한다."""
    return secrets.token_urlsafe(_CODE_NBYTES)


def _record_audit(
    session: AsyncSession,
    actor_id: int,
    action: str,
    target_id: int,
    detail: dict[str, Any] | None,
) -> None:
    session.add(
        AuditLog(
            actor_id=actor_id,
            action=action,
            target_type="signup_code",
            target_id=target_id,
            detail=detail,
        )
    )


async def consume_signup_code(session: AsyncSession, code: str) -> None:
    """가입 코드를 원자적으로 소비한다 (PRD 5.10/5.11).

    성공 시 use_count 를 1 증가시키고 반환한다. 실패 시 사유별 구조화된 SignupCodeError.
    호출자의 트랜잭션에 포함되므로(커밋하지 않음) 가입 실패 시 소비도 함께 롤백된다.
    """
    result = await session.execute(
        update(SignupCode)
        .where(
            SignupCode.code == code,
            SignupCode.is_active.is_(True),
            or_(SignupCode.expires_at.is_(None), SignupCode.expires_at > func.now()),
            or_(SignupCode.max_uses.is_(None), SignupCode.use_count < SignupCode.max_uses),
        )
        .values(use_count=SignupCode.use_count + 1)
        .returning(SignupCode.id)
    )
    if result.first() is not None:
        return

    # 조건부 UPDATE 가 0행이면 사유를 판별해 구조화된 4xx 를 돌려준다.
    row = (
        await session.execute(select(SignupCode).where(SignupCode.code == code))
    ).scalar_one_or_none()
    if row is None:
        raise SignupCodeError(400, "존재하지 않는 가입 코드입니다.")
    if not row.is_active:
        raise SignupCodeError(400, "비활성화된 가입 코드입니다.")
    if row.expires_at is not None and row.expires_at <= datetime.now(UTC):
        raise SignupCodeError(400, "만료된 가입 코드입니다.")
    raise SignupCodeError(400, "사용 횟수가 모두 소진된 가입 코드입니다.")


async def create_signup_code(
    session: AsyncSession,
    actor_id: int,
    *,
    memo: str = "",
    expires_at: datetime | None = None,
    max_uses: int | None = None,
    code: str | None = None,
) -> SignupCode:
    """가입 코드를 발급한다(+ audit). 호출자가 commit 한다."""
    obj = SignupCode(
        code=code or generate_signup_code(),
        memo=memo or "",
        expires_at=expires_at,
        max_uses=max_uses,
        created_by=actor_id,
    )
    session.add(obj)
    await session.flush()
    _record_audit(
        session,
        actor_id,
        "signup_code.create",
        obj.id,
        {
            "memo": obj.memo,
            "max_uses": max_uses,
            "expires_at": expires_at.isoformat() if expires_at else None,
        },
    )
    return obj


async def list_signup_codes(
    session: AsyncSession, page: int, size: int
) -> tuple[list[SignupCode], int]:
    """가입 코드 목록 + 총 개수 (최신순)."""
    total = (
        await session.execute(select(func.count()).select_from(SignupCode))
    ).scalar_one()
    offset = (page - 1) * size
    rows = (
        await session.execute(
            select(SignupCode)
            .order_by(SignupCode.created_at.desc(), SignupCode.id.desc())
            .offset(offset)
            .limit(size)
        )
    ).scalars().all()
    return list(rows), total


# PATCH 로 갱신 가능한 필드. exclude_unset dict 로 전달돼 "미변경"과 "null 로 설정"을 구분한다.
_UPDATABLE_FIELDS = ("is_active", "memo", "expires_at", "max_uses")


def _audit_value(value: Any) -> Any:
    return value.isoformat() if isinstance(value, datetime) else value


async def update_signup_code(
    session: AsyncSession,
    actor_id: int,
    code_id: int,
    changes: dict[str, Any],
) -> SignupCode:
    """가입 코드 수정(비활성화/재활성화, 만료·횟수·메모 조정) + audit. 호출자가 commit 한다."""
    code = await session.get(SignupCode, code_id)
    if code is None:
        raise SignupCodeError(404, "가입 코드를 찾을 수 없습니다.")

    audit_changes: dict[str, Any] = {}
    for field in _UPDATABLE_FIELDS:
        if field not in changes:
            continue
        new_value = changes[field]
        old_value = getattr(code, field)
        if old_value != new_value:
            audit_changes[field] = {
                "from": _audit_value(old_value),
                "to": _audit_value(new_value),
            }
            setattr(code, field, new_value)

    if audit_changes:
        _record_audit(session, actor_id, "signup_code.update", code.id, audit_changes)
    return code
