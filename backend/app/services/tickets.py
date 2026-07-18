"""단기 일회성 다운로드 티켓 (Redis) — 브라우저 대용량 다운로드용.

문제: 게이트웨이 다운로드는 매 요청 인가가 필요한데, 브라우저의 대용량 스트리밍
다운로드(`window.location`)는 Authorization 헤더나 요청 바디를 실을 수 없다. 그래서:

  1. 인증된 클라이언트가 `POST .../download-ticket` 로 인가 검사를 통과하고 티켓을 발급받는다.
  2. 브라우저가 `GET /api/files/download?ticket=...`(무헤더)로 스트리밍 다운로드한다.

티켓은 Redis 에 짧은 TTL(60초)로 저장하고, 소비 시 원자적 삭제(GETDEL)로 **일회성**을
보장한다. 티켓 값에는 재인가/재해석에 필요한 최소 정보만 담는다.
"""

from __future__ import annotations

import json
import secrets
from typing import Any

from redis.asyncio import Redis

# 티켓 TTL — 내부 presign(60초)과 동일 수명. 발급 후 즉시 사용을 전제한다.
TICKET_TTL_SECONDS = 60

_TICKET_PREFIX = "dlticket:"

# 티켓 토큰 — URL 쿼리에 실린다. 32바이트 ≈ 43자, 추측 불가.
_TOKEN_BYTES = 32


async def issue_ticket(redis: Redis, payload: dict[str, Any]) -> str:
    """티켓을 발급한다. payload 를 Redis 에 TTL 과 함께 저장하고 토큰을 반환한다."""
    token = secrets.token_urlsafe(_TOKEN_BYTES)
    await redis.set(
        _TICKET_PREFIX + token, json.dumps(payload), ex=TICKET_TTL_SECONDS
    )
    return token


async def consume_ticket(redis: Redis, token: str) -> dict[str, Any] | None:
    """티켓을 원자적으로 조회+삭제한다(GETDEL). 없거나 이미 사용됐으면 None.

    GETDEL 로 조회와 삭제를 원자화해 동시 재사용(race)까지 차단한다 — 일회성 보장.
    """
    if not token:
        return None
    raw = await redis.getdel(_TICKET_PREFIX + token)
    if raw is None:
        return None
    try:
        return json.loads(raw)
    except (ValueError, TypeError):
        return None
