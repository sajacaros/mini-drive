"""Prometheus 메트릭 (PRD 11장).

카디널리티 폭발을 막기 위해 HTTP 라벨의 `path` 는 **실제 경로가 아니라 라우트 템플릿**
(예: `/api/files/{file_id}`)만 사용한다. 매칭되지 않은 경로(404 등)는 `__unmatched__`
단일 라벨로 접는다.

메트릭 목록
-----------
- http_requests_total{method,path,status}          — 요청 수 카운터
- http_request_duration_seconds{method,path}        — 요청 지연 히스토그램
- minidrive_upload_bytes_total                       — 업로드 저장 바이트 누적
- minidrive_download_bytes_total                     — 다운로드 인가 바이트 누적
- rate_limit_rejections_total{scope}                 — rate limit 429 거부 수

노출은 `/metrics`(app.main) 이며, 게이트웨이(nginx)에서 외부 접근을 차단한다(내부 스크레이프 전용).
"""

from __future__ import annotations

from prometheus_client import CONTENT_TYPE_LATEST, Counter, Histogram, generate_latest

UNMATCHED_PATH = "__unmatched__"

http_requests_total = Counter(
    "http_requests_total",
    "HTTP 요청 수",
    labelnames=("method", "path", "status"),
)

http_request_duration_seconds = Histogram(
    "http_request_duration_seconds",
    "HTTP 요청 처리 시간(초)",
    labelnames=("method", "path"),
    # 파일 서비스 특성상 빠른 API~느린 업로드까지 폭넓게 커버.
    buckets=(0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1, 2.5, 5, 10, 30, 60),
)

upload_bytes_total = Counter(
    "minidrive_upload_bytes_total",
    "MinIO 로 저장된 업로드 바이트 누적",
)

download_bytes_total = Counter(
    "minidrive_download_bytes_total",
    "게이트웨이 다운로드로 인가된 바이트 누적",
)

rate_limit_rejections_total = Counter(
    "rate_limit_rejections_total",
    "rate limit 으로 429 거부된 요청 수",
    labelnames=("scope",),
)


def observe_request(method: str, path: str, status: int, duration_seconds: float) -> None:
    """요청 1건의 카운터/히스토그램을 기록한다 (미들웨어에서 호출)."""
    http_requests_total.labels(method=method, path=path, status=str(status)).inc()
    http_request_duration_seconds.labels(method=method, path=path).observe(duration_seconds)


def observe_upload_bytes(n: int) -> None:
    if n > 0:
        upload_bytes_total.inc(n)


def observe_download_bytes(n: int) -> None:
    if n > 0:
        download_bytes_total.inc(n)


def observe_rate_limit_rejection(scope: str) -> None:
    rate_limit_rejections_total.labels(scope=scope).inc()


def render_metrics() -> tuple[bytes, str]:
    """(payload, content_type) 을 반환한다 — /metrics 응답용."""
    return generate_latest(), CONTENT_TYPE_LATEST
