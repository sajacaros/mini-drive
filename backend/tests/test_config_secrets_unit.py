"""운영 비밀키 기동 가드 단위 테스트 (app.core.config._forbid_insecure_secrets).

기본값이 **동작하는 값**이라 `JWT_SECRET` 을 빠뜨린 배포가 조용히 뜨던 문제를 막는 가드다.
개발은 그대로 뜨고 운영만 거부해야 하므로 양쪽을 함께 검증한다.
"""

from __future__ import annotations

import pytest

from app.core.config import (
    MIN_JWT_SECRET_BYTES,
    PLACEHOLDER_SECRET,
    InsecureSecretError,
    Settings,
)

# 가드를 통과하는 값들 — 개별 테스트가 하나씩만 어긋나게 바꿔 쓴다.
_STRONG_JWT = "x" * MIN_JWT_SECRET_BYTES
_SAFE = {
    "jwt_secret": _STRONG_JWT,
    "minio_secret_key": "minio-secret-value",
    "database_url": "postgresql+asyncpg://u:p@db:5432/minidrive",
}


def _settings(**overrides: str) -> Settings:
    return Settings(**{**_SAFE, **overrides})  # type: ignore[arg-type]


class TestDevelopmentUnaffected:
    """개발 환경은 placeholder 그대로 떠야 한다 — clone 후 바로 실행되는 것이 기본값의 목적이다."""

    def test_placeholder_allowed_in_development(self) -> None:
        settings = _settings(environment="development", jwt_secret=PLACEHOLDER_SECRET)
        assert settings.jwt_secret == PLACEHOLDER_SECRET

    def test_short_secret_allowed_in_development(self) -> None:
        assert _settings(environment="development", jwt_secret="short").jwt_secret == "short"


class TestProductionGuard:
    def test_placeholder_jwt_secret_rejected(self) -> None:
        with pytest.raises(InsecureSecretError, match="JWT_SECRET"):
            _settings(environment="production", jwt_secret=PLACEHOLDER_SECRET)

    def test_placeholder_minio_secret_rejected(self) -> None:
        with pytest.raises(InsecureSecretError, match="MINIO_SECRET_KEY"):
            _settings(environment="production", minio_secret_key=PLACEHOLDER_SECRET)

    def test_placeholder_in_database_url_rejected(self) -> None:
        with pytest.raises(InsecureSecretError, match="DATABASE_URL"):
            _settings(
                environment="production",
                database_url=f"postgresql+asyncpg://postgres:{PLACEHOLDER_SECRET}@db:5432/d",
            )

    def test_all_offenders_reported_at_once(self) -> None:
        """하나씩 고쳐 가며 재배포하지 않도록 어긋난 항목을 한 번에 모아 보여준다."""
        with pytest.raises(InsecureSecretError) as exc:
            _settings(
                environment="production",
                jwt_secret=PLACEHOLDER_SECRET,
                minio_secret_key=PLACEHOLDER_SECRET,
            )
        assert "JWT_SECRET" in str(exc.value)
        assert "MINIO_SECRET_KEY" in str(exc.value)

    def test_short_jwt_secret_rejected(self) -> None:
        """placeholder 만 피한 짧은 키도 같은 문제를 남긴다 (RFC 7518 3.2)."""
        with pytest.raises(InsecureSecretError, match="너무 짧습니다"):
            _settings(environment="production", jwt_secret="x" * (MIN_JWT_SECRET_BYTES - 1))

    def test_multibyte_secret_measured_in_bytes(self) -> None:
        """길이는 문자 수가 아니라 바이트로 잰다 — 한글 11자는 33바이트라 통과해야 한다."""
        assert len("가" * 11) < MIN_JWT_SECRET_BYTES  # 문자 수로 재면 탈락하는 값
        assert _settings(environment="production", jwt_secret="가" * 11).environment == "production"

    def test_strong_secrets_accepted(self) -> None:
        assert _settings(environment="production").environment == "production"

    def test_environment_match_is_case_and_space_insensitive(self) -> None:
        """`ENVIRONMENT=" Production "` 같은 값으로 가드를 우회할 수 없어야 한다."""
        with pytest.raises(InsecureSecretError):
            _settings(environment="  Production  ", jwt_secret=PLACEHOLDER_SECRET)
