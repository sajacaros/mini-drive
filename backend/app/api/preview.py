"""미리보기 응답 공용 헬퍼 (PRD 3.2).

인증 파일 미리보기(`/api/files/{id}/preview`)와 공개 공유 미리보기
(`/api/public/shares/...preview`)가 같은 PreviewPlan → HTTP 응답 변환을 공유하므로 여기 모은다.

미지원 타입은 415 로 응답하되, detail 을 구조화(dict)해 프론트가 "미리보기 불가 → 다운로드 폴백"
을 구분할 수 있게 한다.
"""

from __future__ import annotations

from fastapi import HTTPException, Response, status

from app.api.download import gateway_inline_response, preview_text_response
from app.services.previews import PreviewPlan


def render_preview(plan: PreviewPlan, filename: str) -> Response:
    """PreviewPlan 을 인라인 미리보기 응답으로 변환한다. 미지원이면 415(구조화 detail)."""
    if plan.kind == "unsupported":
        raise HTTPException(
            status_code=status.HTTP_415_UNSUPPORTED_MEDIA_TYPE,
            detail={
                "reason": "unsupported_preview",
                "mime_type": plan.mime,
                "message": "미리보기를 지원하지 않는 형식입니다.",
            },
        )
    if plan.kind == "redirect":
        assert plan.internal_redirect is not None
        return gateway_inline_response(plan.internal_redirect, filename, plan.mime)
    # text
    return preview_text_response(
        plan.text_content or b"", filename, truncated=plan.truncated
    )
