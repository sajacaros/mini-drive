"""요청 컨텍스트 미들웨어 — 구조화 로깅 + 메트릭 일원화 (PRD 11장).

요청마다:
- request_id 를 확정한다(수신 `X-Request-ID` 를 신뢰, 없으면 생성). 응답에 되돌려준다.
- structlog contextvars 에 request_id/method/path/client_ip/user_id 를 바인딩해
  이 요청 동안의 모든 로그가 자동으로 상관관계를 갖게 한다.
- 처리 시간을 재고 access 로그 1줄 + Prometheus 메트릭을 남긴다.
- 처리 중 예외는 스택과 함께 500 으로 로깅하고 500 응답을 만든다(재던지지 않음).

user_id 는 로그 상관용으로 Authorization 의 access 토큰을 **베스트에포트**로 디코드해 얻는다
(인가는 라우트 의존성이 담당 — 여기서 status 검증은 하지 않는다).
"""

from __future__ import annotations

import time
import uuid

import structlog
from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint
from starlette.requests import Request
from starlette.responses import JSONResponse, Response

from app.core.config import settings
from app.core.logging import get_logger
from app.core.metrics import UNMATCHED_PATH, observe_request
from app.core.security import TokenError, decode_token

REQUEST_ID_HEADER = "X-Request-ID"

_log = get_logger("app.request")


def _client_ip(request: Request) -> str:
    real_ip = request.headers.get("X-Real-IP")
    if real_ip:
        return real_ip.strip()
    if request.client is not None:
        return request.client.host
    return "unknown"


def _user_id_from_auth(request: Request) -> str | None:
    """access 토큰에서 user_id 를 베스트에포트로 추출(로그 상관용). 실패 시 None."""
    header = request.headers.get("Authorization")
    if not header or not header.lower().startswith("bearer "):
        return None
    token = header[7:].strip()
    try:
        payload = decode_token(token, expected_type="access")
        return str(payload["sub"])
    except (TokenError, KeyError, ValueError):
        return None


def _route_template(request: Request) -> str:
    """매칭된 라우트 템플릿(카디널리티 안전). 미매칭이면 __unmatched__.

    이 FastAPI 버전은 include_router 를 평탄화하지 않고 중첩(`_IncludedRouter`)으로 두므로,
    `scope["route"].path` 는 접두어가 빠진 리프 경로(예: `/{file_id}/download`)만 준다.
    실제 경로에서 리프의 렌더링분을 떼어 접두어를 복원해 전체 템플릿
    (예: `/api/files/{file_id}/download`)을 만든다 — FastAPI 내부구조 비의존.
    """
    route = request.scope.get("route")
    leaf = getattr(route, "path", None)
    if not isinstance(leaf, str) or not leaf:
        return UNMATCHED_PATH

    params = request.scope.get("path_params") or {}
    leaf_actual = leaf
    for name, value in params.items():
        leaf_actual = leaf_actual.replace("{" + name + "}", str(value))

    full = request.url.path
    if leaf_actual and full.endswith(leaf_actual):
        return full[: len(full) - len(leaf_actual)] + leaf
    return leaf


BATCH_UPLOAD_PATH = "/api/files/batch"


class BatchBodyLimitMiddleware(BaseHTTPMiddleware):
    """배치 업로드 본문 크기를 **파싱 전에** Content-Length 로 차단한다.

    FastAPI 는 라우트 의존성을 풀기 전에 multipart 본문을 먼저 읽으므로, 핸들러나 Depends 로는
    파싱을 선점할 수 없다. 상한을 넘는 본문을 다 받고 나서 거부하면 상한의 의미가 없어
    미들웨어에서 막는다.

    운영에서는 nginx 가 같은 상한을 먼저 적용하지만(1차 방어), 게이트웨이를 거치지 않는
    경로(테스트, 다른 배포 형태)에서도 앱이 스스로 안전하도록 여기서 이중으로 건다.
    """

    async def dispatch(
        self, request: Request, call_next: RequestResponseEndpoint
    ) -> Response:
        if request.method == "POST" and request.url.path == BATCH_UPLOAD_PATH:
            raw = request.headers.get("content-length")
            try:
                declared = int(raw) if raw is not None else 0
            except ValueError:
                declared = 0
            if declared > settings.max_batch_bytes:
                return JSONResponse(
                    status_code=413,
                    content={"detail": "요청 본문이 배치 상한을 초과했습니다."},
                )
        return await call_next(request)


class RequestContextMiddleware(BaseHTTPMiddleware):
    async def dispatch(
        self, request: Request, call_next: RequestResponseEndpoint
    ) -> Response:
        request_id = request.headers.get(REQUEST_ID_HEADER) or uuid.uuid4().hex
        client_ip = _client_ip(request)
        user_id = _user_id_from_auth(request)

        structlog.contextvars.clear_contextvars()
        structlog.contextvars.bind_contextvars(
            request_id=request_id,
            method=request.method,
            path=request.url.path,
            client_ip=client_ip,
            **({"user_id": user_id} if user_id else {}),
        )

        start = time.perf_counter()
        try:
            response = await call_next(request)
        except Exception:
            duration_ms = (time.perf_counter() - start) * 1000
            path_tmpl = _route_template(request)
            # 스택 포함 500 로깅.
            _log.exception(
                "request_failed",
                status=500,
                duration_ms=round(duration_ms, 2),
                path_template=path_tmpl,
            )
            if settings.metrics_enabled:
                observe_request(request.method, path_tmpl, 500, duration_ms / 1000)
            response = JSONResponse(
                status_code=500,
                content={"detail": "내부 서버 오류가 발생했습니다."},
            )
            response.headers[REQUEST_ID_HEADER] = request_id
            structlog.contextvars.clear_contextvars()
            return response

        duration_ms = (time.perf_counter() - start) * 1000
        path_tmpl = _route_template(request)
        response.headers[REQUEST_ID_HEADER] = request_id

        # /metrics 스크레이프는 자기관측 노이즈라 카운트에서 제외한다.
        if settings.metrics_enabled and request.url.path != "/metrics":
            observe_request(request.method, path_tmpl, response.status_code, duration_ms / 1000)

        _log.info(
            "request",
            status=response.status_code,
            duration_ms=round(duration_ms, 2),
            path_template=path_tmpl,
        )
        structlog.contextvars.clear_contextvars()
        return response
