"""공유 링크 서비스 단위 테스트 (DB/MinIO 불필요).

토큰 생성 규약, 만료 판정, 응답 파생(비밀번호 필요 여부), 승격된 Content-Disposition 헬퍼를
검증한다. 충돌 재시도/횟수 원자 증가 등 DB 의존 경로는 tests.integration_shares 에서 다룬다.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest

from app.api.download import content_disposition
from app.api.routes.files import _content_disposition
from app.models.enums import SharePermission
from app.schemas.shares import ShareResponse
from app.services import shares as shares_service


class TestTokenGeneration:
    def test_token_within_column_length_and_urlsafe(self) -> None:
        import secrets
        import string

        allowed = set(string.ascii_letters + string.digits + "-_")
        for _ in range(200):
            token = secrets.token_urlsafe(shares_service._TOKEN_BYTES)
            assert 0 < len(token) <= 64  # shares.share_url VARCHAR(64)
            assert set(token) <= allowed

    def test_tokens_are_unique(self) -> None:
        import secrets

        tokens = {
            secrets.token_urlsafe(shares_service._TOKEN_BYTES) for _ in range(1000)
        }
        assert len(tokens) == 1000  # 충돌 없음(사실상)


class TestExpiry:
    def test_none_never_expires(self) -> None:
        share = SimpleNamespace(expires_at=None)
        assert shares_service._is_expired(share) is False

    def test_future_not_expired(self) -> None:
        share = SimpleNamespace(expires_at=datetime.now(UTC) + timedelta(hours=1))
        assert shares_service._is_expired(share) is False

    def test_past_expired(self) -> None:
        share = SimpleNamespace(expires_at=datetime.now(UTC) - timedelta(seconds=1))
        assert shares_service._is_expired(share) is True

    def test_naive_datetime_treated_as_utc(self) -> None:
        # TIMESTAMPTZ 라 보통 aware 지만, naive 가 와도 UTC 로 간주해 비교한다(방어적).
        share = SimpleNamespace(expires_at=datetime(2020, 1, 1, 0, 0, 0))  # noqa: DTZ001
        assert shares_service._is_expired(share) is True


class TestAllowedPermissions:
    def test_read_and_download_allowed(self) -> None:
        assert SharePermission.READ in shares_service._ALLOWED_PERMISSIONS
        assert SharePermission.DOWNLOAD in shares_service._ALLOWED_PERMISSIONS

    def test_write_not_allowed_phase1(self) -> None:
        assert SharePermission.WRITE not in shares_service._ALLOWED_PERMISSIONS


class TestShareResponseFactory:
    def _share(self, **over: object) -> SimpleNamespace:
        base = dict(
            id=1,
            file_id=2,
            share_url="abc123",
            permission="read",
            is_active=True,
            password_hash=None,
            expires_at=None,
            max_downloads=None,
            download_count=0,
            created_at=datetime.now(UTC),
        )
        base.update(over)
        return SimpleNamespace(**base)

    def test_password_required_false_when_no_hash(self) -> None:
        resp = ShareResponse.from_share(self._share(), "report.pdf")
        assert resp.password_required is False
        assert resp.file_name == "report.pdf"

    def test_password_required_true_when_hash_present(self) -> None:
        resp = ShareResponse.from_share(
            self._share(password_hash="$argon2id$..."), "s.pdf"
        )
        assert resp.password_required is True

    def test_does_not_expose_password_hash(self) -> None:
        resp = ShareResponse.from_share(
            self._share(password_hash="$argon2id$secret"), "s.pdf"
        )
        assert "secret" not in resp.model_dump_json()


class TestContentDispositionPromotion:
    def test_files_route_reexports_shared_helper(self) -> None:
        # files.py 의 _content_disposition 이 app.api.download 로 승격된 동일 함수여야 한다.
        assert _content_disposition is content_disposition

    def test_unicode_percent_encoded(self) -> None:
        cd = content_disposition("보고서.pdf")
        assert cd.startswith("attachment;")
        assert "filename*=UTF-8''" in cd
        assert "%EB%B3%B4%EA%B3%A0%EC%84%9C.pdf" in cd


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(pytest.main([__file__, "-q"]))
