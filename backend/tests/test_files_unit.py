"""파일 서비스 단위 테스트 (DB/MinIO 불필요).

키 생성 규약, presign→/_minio/ 변환, 권한 관문, Content-Disposition 인코딩을 검증한다.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from app.api.routes.files import _content_disposition
from app.services.files import (
    FileServiceError,
    annotate_location,
    build_file_key,
    build_version_key,
    ensure_file_access,
    versioned_filename,
)
from app.services.storage import StorageService


class TestObjectKeys:
    def test_file_key(self) -> None:
        assert build_file_key(7, 42) == "users/7/42"

    def test_version_key(self) -> None:
        assert build_version_key(42, 3) == "versions/42/v3"


class TestVersionedFilename:
    def test_inserts_before_extension(self) -> None:
        assert versioned_filename("report.pdf", 3) == "report (v3).pdf"

    def test_no_extension(self) -> None:
        assert versioned_filename("README", 2) == "README (v2)"

    def test_multi_dot_uses_last_extension(self) -> None:
        assert versioned_filename("archive.tar.gz", 5) == "archive.tar (v5).gz"

    def test_unicode_name(self) -> None:
        assert versioned_filename("보고서.hwp", 1) == "보고서 (v1).hwp"


class TestInternalRedirect:
    def _svc(self) -> StorageService:
        # 생성자는 네트워크에 접속하지 않는다(지연 연결) — 오프라인 테스트 가능.
        return StorageService(endpoint="minio:9000", bucket="minidrive")

    def test_strips_scheme_host_keeps_path_and_query(self) -> None:
        svc = self._svc()
        url = "http://minio:9000/minidrive/users/1/5?X-Amz-Algorithm=AWS4-HMAC-SHA256&X-Amz-Signature=abc"
        out = svc.to_internal_redirect(url)
        assert out == (
            "/_minio/minidrive/users/1/5"
            "?X-Amz-Algorithm=AWS4-HMAC-SHA256&X-Amz-Signature=abc"
        )

    def test_no_query(self) -> None:
        svc = self._svc()
        assert svc.to_internal_redirect("http://minio:9000/minidrive/users/1/5") == (
            "/_minio/minidrive/users/1/5"
        )

    def test_internal_path_is_not_browser_facing_host(self) -> None:
        # 변환 결과에 MinIO 엔드포인트/스킴이 남지 않아야 한다.
        svc = self._svc()
        out = svc.to_internal_redirect("http://minio:9000/minidrive/a?b=c")
        assert "minio:9000" not in out
        assert out.startswith("/_minio/")


class TestEnsureFileAccess:
    """소유자/부재 경로는 DB 접근 전에 판정되므로 session 없이 검증한다.

    비소유자의 그룹 권한 판정(조회 시 상속)은 DB/Redis 가 필요하므로 통합 테스트
    (integration_permissions)에서 다룬다. 순수 판정 로직은 test_permissions_unit 참조.
    """

    async def test_owner_allowed(self) -> None:
        user = SimpleNamespace(id=1)
        file = SimpleNamespace(user_id=1)
        assert await ensure_file_access(None, user, file) is file  # type: ignore[arg-type]

    async def test_missing_404(self) -> None:
        user = SimpleNamespace(id=1)
        with pytest.raises(FileServiceError) as exc:
            await ensure_file_access(None, user, None)  # type: ignore[arg-type]
        assert exc.value.status_code == 404


class TestLocationPrefix:
    """루트 직속(부모 없음) 항목의 위치 접두사 — 소유 여부에 따라 갈린다.

    부모 폴더가 없으면(parent_folder_id is None) 조상 배치 조회가 일어나지 않으므로 session
    없이 검증할 수 있다. 통합 드라이브에서 공유받은 항목은 "내 드라이브 / 공유" 아래로 합쳐진다.
    """

    async def test_owned_root_item_prefix(self) -> None:
        user = SimpleNamespace(id=1)
        file = SimpleNamespace(user_id=1, parent_folder_id=None)
        await annotate_location(None, user, [file])  # type: ignore[arg-type]
        assert file.location == "내 드라이브"

    async def test_shared_root_item_prefix(self) -> None:
        user = SimpleNamespace(id=1)
        file = SimpleNamespace(user_id=2, parent_folder_id=None)  # 타인 소유
        await annotate_location(None, user, [file])  # type: ignore[arg-type]
        assert file.location == "내 드라이브 / 공유"


class TestContentDisposition:
    def test_ascii(self) -> None:
        cd = _content_disposition("report.pdf")
        assert 'filename="report.pdf"' in cd
        assert "filename*=UTF-8''report.pdf" in cd

    def test_unicode_percent_encoded(self) -> None:
        cd = _content_disposition("보고서.pdf")
        assert "filename*=UTF-8''" in cd
        assert "%EB%B3%B4%EA%B3%A0%EC%84%9C.pdf" in cd
        assert cd.startswith("attachment;")
