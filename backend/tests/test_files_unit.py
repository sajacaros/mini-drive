"""파일 서비스 단위 테스트 (DB/MinIO 불필요).

키 생성 규약, presign→/_minio/ 변환, 권한 관문, Content-Disposition 인코딩을 검증한다.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from app.api.routes.files import _content_disposition
from app.services.files import (
    FileServiceError,
    build_file_key,
    build_version_key,
    ensure_file_access,
)
from app.services.storage import StorageService


class TestObjectKeys:
    def test_file_key(self) -> None:
        assert build_file_key(7, 42) == "users/7/42"

    def test_version_key(self) -> None:
        assert build_version_key(42, 3) == "versions/42/v3"


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
    def test_owner_allowed(self) -> None:
        user = SimpleNamespace(id=1)
        file = SimpleNamespace(user_id=1)
        assert ensure_file_access(user, file) is file

    def test_non_owner_404(self) -> None:
        user = SimpleNamespace(id=2)
        file = SimpleNamespace(user_id=1)
        with pytest.raises(FileServiceError) as exc:
            ensure_file_access(user, file)
        assert exc.value.status_code == 404

    def test_missing_404(self) -> None:
        user = SimpleNamespace(id=1)
        with pytest.raises(FileServiceError) as exc:
            ensure_file_access(user, None)
        assert exc.value.status_code == 404


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
