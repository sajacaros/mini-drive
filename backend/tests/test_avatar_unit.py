"""아바타 업로드 검증 단위 테스트 — 콘텐츠 타입/크기 (스토리지/DB 불필요).

validate_avatar 는 허용 타입과 크기 상한만 순수 검증한다. 실제 MinIO 저장·avatar_url 갱신은
라우트/서비스 오케스트레이션이 수행하므로 여기서는 검사 대상이 아니다.
"""

from __future__ import annotations

import pytest

from app.services.avatars import (
    ALLOWED_AVATAR_TYPES,
    AVATAR_MAX_SIZE,
    AvatarError,
    build_avatar_key,
    validate_avatar,
)


class TestValidateAvatar:
    @pytest.mark.parametrize("content_type", sorted(ALLOWED_AVATAR_TYPES))
    def test_allowed_types_pass(self, content_type: str) -> None:
        assert validate_avatar(content_type, 1024) == content_type

    def test_content_type_with_charset_normalized(self) -> None:
        # `image/png; charset=binary` 처럼 파라미터가 붙어도 타입만 보고 통과.
        assert validate_avatar("image/PNG; charset=binary", 1024) == "image/png"

    @pytest.mark.parametrize(
        "content_type",
        ["image/gif", "application/pdf", "text/plain", "", None],
    )
    def test_disallowed_type_rejected(self, content_type: str | None) -> None:
        with pytest.raises(AvatarError) as exc:
            validate_avatar(content_type, 1024)
        assert exc.value.status_code == 415

    def test_at_limit_passes(self) -> None:
        validate_avatar("image/png", AVATAR_MAX_SIZE)  # 상한 경계는 허용

    def test_over_limit_rejected(self) -> None:
        with pytest.raises(AvatarError) as exc:
            validate_avatar("image/png", AVATAR_MAX_SIZE + 1)
        assert exc.value.status_code == 413

    def test_empty_file_rejected(self) -> None:
        with pytest.raises(AvatarError) as exc:
            validate_avatar("image/png", 0)
        assert exc.value.status_code == 422

    def test_type_check_precedes_size_check(self) -> None:
        # 타입이 틀리면 크기 초과 여부와 무관하게 415 가 우선한다.
        with pytest.raises(AvatarError) as exc:
            validate_avatar("image/gif", AVATAR_MAX_SIZE + 1)
        assert exc.value.status_code == 415


class TestBuildAvatarKey:
    def test_key_format(self) -> None:
        assert build_avatar_key(42) == "avatars/42"
