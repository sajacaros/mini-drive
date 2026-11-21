"""StorageService(boto3) 유닛 테스트 — 네트워크 없이 botocore Stubber 로 응답을 대본으로 준다.

실서버(RustFS) 왕복은 integration_* 가 본다. 여기서는 SDK 위에 얹은 우리 로직만 고정한다:
not-found 변환, ETag 따옴표 정리, ListParts 페이지 넘김의 중복 접기·멈춤 조건, 업로드 길이 상한,
DeleteObjects 1000개 분할.
"""

from __future__ import annotations

import io
from collections.abc import Iterator

import pytest
from botocore.stub import ANY, Stubber

from app.services.storage import (
    ObjectNotFoundError,
    StorageService,
    _LimitedReader,
)


@pytest.fixture()
def svc() -> StorageService:
    return StorageService(
        endpoint="objectstorage:9000", bucket="b", access_key="k", secret_key="s", secure=False
    )


@pytest.fixture()
def stub(svc: StorageService) -> Iterator[Stubber]:
    with Stubber(svc._client) as s:
        yield s
        s.assert_no_pending_responses()


class TestNotFound:
    def test_stat_missing_raises_domain_error(self, svc: StorageService, stub: Stubber) -> None:
        # HEAD 는 본문이 없어 오류 코드가 "404" 로만 온다.
        stub.add_client_error("head_object", service_error_code="404", http_status_code=404)
        with pytest.raises(ObjectNotFoundError) as exc:
            svc.stat("users/1/9")
        assert exc.value.key == "users/1/9"

    def test_exists_false_on_missing(self, svc: StorageService, stub: Stubber) -> None:
        stub.add_client_error("head_object", service_error_code="404", http_status_code=404)
        assert svc.exists("x") is False

    def test_get_missing(self, svc: StorageService, stub: Stubber) -> None:
        stub.add_client_error("get_object", service_error_code="NoSuchKey", http_status_code=404)
        with pytest.raises(ObjectNotFoundError):
            svc.get_bytes("x")

    def test_other_errors_propagate(self, svc: StorageService, stub: Stubber) -> None:
        # 권한 오류를 "없음" 으로 삼키면 404 로 둔갑해 장애가 가려진다.
        stub.add_client_error("head_object", service_error_code="403", http_status_code=403)
        with pytest.raises(Exception) as exc:
            svc.stat("x")
        assert not isinstance(exc.value, ObjectNotFoundError)

    def test_bucket_exists(self, svc: StorageService, stub: Stubber) -> None:
        stub.add_response("head_bucket", {}, {"Bucket": "b"})
        stub.add_client_error("head_bucket", service_error_code="404", http_status_code=404)
        assert svc.bucket_exists() is True
        assert svc.bucket_exists() is False


class TestStat:
    def test_strips_etag_quotes(self, svc: StorageService, stub: Stubber) -> None:
        stub.add_response(
            "head_object",
            {"ContentLength": 11, "ContentType": "text/plain", "ETag": '"abc"'},
            {"Bucket": "b", "Key": "k"},
        )
        st = svc.stat("k")
        assert (st.size, st.content_type, st.etag) == (11, "text/plain", "abc")


class TestMultipart:
    def test_upload_part_returns_bare_etag(self, svc: StorageService, stub: Stubber) -> None:
        stub.add_response("upload_part", {"ETag": '"e1"'}, {
            "Bucket": "b", "Key": "k", "UploadId": "u", "PartNumber": 1, "Body": ANY,
        })
        assert svc.upload_part("k", "u", 1, b"data") == "e1"

    def test_list_parts_follows_pages(self, svc: StorageService, stub: Stubber) -> None:
        stub.add_response("list_parts", {
            "Parts": [{"PartNumber": 1, "Size": 5, "ETag": '"a"'}],
            "IsTruncated": True, "NextPartNumberMarker": 1,
        }, {"Bucket": "b", "Key": "k", "UploadId": "u"})
        stub.add_response("list_parts", {
            "Parts": [{"PartNumber": 2, "Size": 3, "ETag": '"b"'}],
            "IsTruncated": False,
        }, {"Bucket": "b", "Key": "k", "UploadId": "u", "PartNumberMarker": 1})
        assert svc.list_parts("k", "u") == [(1, 5, "a"), (2, 3, "b")]

    def test_list_parts_ignored_marker_does_not_loop(
        self, svc: StorageService, stub: Stubber
    ) -> None:
        # 예전 SeaweedFS 4.47 실측: marker 를 줘도 처음부터 다시 준다(RustFS 는 정상, 방어 유지).
        # 중복을 접고, 새 파트가 없으면 멈춘다.
        page = {
            "Parts": [
                {"PartNumber": 1, "Size": 5, "ETag": '"a"'},
                {"PartNumber": 2, "Size": 3, "ETag": '"b"'},
            ],
            "IsTruncated": True, "NextPartNumberMarker": 2,
        }
        stub.add_response("list_parts", page, {"Bucket": "b", "Key": "k", "UploadId": "u"})
        stub.add_response(
            "list_parts", page,
            {"Bucket": "b", "Key": "k", "UploadId": "u", "PartNumberMarker": 2},
        )
        assert svc.list_parts("k", "u") == [(1, 5, "a"), (2, 3, "b")]

    def test_complete_sends_parts(self, svc: StorageService, stub: Stubber) -> None:
        stub.add_response("complete_multipart_upload", {}, {
            "Bucket": "b", "Key": "k", "UploadId": "u",
            "MultipartUpload": {"Parts": [
                {"PartNumber": 1, "ETag": "a"}, {"PartNumber": 2, "ETag": "b"},
            ]},
        })
        svc.complete_multipart("k", "u", [(1, "a"), (2, "b")])


class TestDeleteMany:
    def test_empty_is_noop(self, svc: StorageService, stub: Stubber) -> None:
        assert svc.delete_many([]) == []

    def test_splits_into_batches_and_collects_failures(
        self, svc: StorageService, stub: Stubber
    ) -> None:
        keys = [f"k{i}" for i in range(1001)]
        stub.add_response("delete_objects", {}, {
            "Bucket": "b",
            "Delete": {"Objects": [{"Key": k} for k in keys[:1000]], "Quiet": True},
        })
        failed = {"Errors": [{"Key": "k1000", "Code": "AccessDenied"}]}
        stub.add_response("delete_objects", failed, {
            "Bucket": "b",
            "Delete": {"Objects": [{"Key": "k1000"}], "Quiet": True},
        })
        assert svc.delete_many(keys) == ["k1000"]


class TestLimitedReader:
    def test_stops_at_length(self) -> None:
        r = _LimitedReader(io.BytesIO(b"0123456789"), 4)
        assert r.read(3) == b"012"
        assert r.read(3) == b"3"
        assert r.read(3) == b""

    def test_read_all(self) -> None:
        assert _LimitedReader(io.BytesIO(b"abcdef"), 4).read() == b"abcd"


class TestPresign:
    def test_path_style_url_on_endpoint_host(self, svc: StorageService) -> None:
        # 서명 Host 가 엔드포인트 그대로여야 nginx 가 같은 Host 로 넘겨 SigV4 가 맞는다(PRD 12).
        url = svc.presign_get("users/1/5", ttl=60)
        assert url.startswith("http://objectstorage:9000/b/users/1/5?")
        assert "X-Amz-Signature=" in url
        assert svc.to_internal_redirect(url).startswith("/_s3/b/users/1/5?")
