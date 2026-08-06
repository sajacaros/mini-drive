"""미리보기 응답 공용 헬퍼 (PRD 3.2).

인증 파일 미리보기(`/api/files/{id}/preview`)와 공개 공유 미리보기
(`/api/public/shares/...preview`)가 같은 PreviewPlan → HTTP 응답 변환을 공유하므로 여기 모은다.

미지원 타입은 415 로 응답하되, detail 을 구조화(dict)해 프론트가 "미리보기 불가 → 다운로드 폴백"
을 구분할 수 있게 한다.

영상만 응답 모양이 다르다: 바이트가 아니라 재생 주소(JSON)를 준다. 호출자는 스트림 라우트 경로와
그 라우트가 재인가에 쓸 티켓 payload 를 넘겨야 한다 — 경로/인가 주체가 파일과 공유에서 다르므로
여기서 정하지 않고 받는다.
"""

from __future__ import annotations

from typing import Any

from fastapi import HTTPException, Response, status
from fastapi.responses import JSONResponse
from redis.asyncio import Redis

from app.api.download import gateway_inline_response, preview_text_response
from app.schemas.files import PreviewStreamResponse
from app.services import tickets as tickets_service
from app.services.previews import PreviewPlan


async def render_preview(
    plan: PreviewPlan,
    filename: str,
    *,
    redis: Redis,
    stream_path: str,
    ticket_payload: dict[str, Any],
) -> Response:
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
    if plan.kind == "stream":
        token = await tickets_service.issue_preview_ticket(redis, ticket_payload)
        return JSONResponse(
            PreviewStreamResponse(
                mime=plan.mime,
                url=f"{stream_path}?ticket={token}",
                expires_in=tickets_service.PREVIEW_TICKET_TTL_SECONDS,
            ).model_dump()
        )
    if plan.kind == "redirect":
        assert plan.internal_redirect is not None
        return gateway_inline_response(plan.internal_redirect, filename, plan.mime)
    # text
    return preview_text_response(
        plan.text_content or b"", filename, truncated=plan.truncated
    )
