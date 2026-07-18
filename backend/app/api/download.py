"""게이트웨이 다운로드 공용 헬퍼 (PRD 2.2, 8절).

파일 다운로드(`/api/files/{id}/download`)와 공유 링크 다운로드
(`/api/public/shares/{shareUrl}/download`)가 동일한 X-Accel-Redirect 응답 패턴을
공유하므로, 라우트별 중복 없이 여기 한 곳에서 만든다.

presigned URL 은 브라우저에 노출하지 않고(PRD 2.2), 서비스 계층이 nginx `/_minio/`
internal 경로로 변환한 값을 `X-Accel-Redirect` 헤더로 넘겨 nginx→MinIO 스트리밍을 유도한다.
"""

from __future__ import annotations

from urllib.parse import quote

from fastapi import Response, status


def content_disposition(filename: str) -> str:
    """RFC 5987 filename* (UTF-8) + ASCII fallback 로 첨부 헤더를 만든다."""
    ascii_fallback = filename.encode("ascii", "replace").decode("ascii").replace('"', "")
    return f"attachment; filename=\"{ascii_fallback}\"; filename*=UTF-8''{quote(filename)}"


def gateway_download_response(internal_redirect: str, filename: str, mime: str) -> Response:
    """X-Accel-Redirect 다운로드 응답을 만든다 (PRD 2.2).

    본문은 비우고 헤더만 채워 nginx 가 internal location 으로 재라우팅하도록 한다.
    """
    return Response(
        status_code=status.HTTP_200_OK,
        headers={
            "X-Accel-Redirect": internal_redirect,
            "Content-Type": mime,
            "Content-Disposition": content_disposition(filename),
        },
    )
