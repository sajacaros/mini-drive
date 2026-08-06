"""단기 일회성 다운로드 티켓 (Redis) — 브라우저 대용량 다운로드용.

문제: 게이트웨이 다운로드는 매 요청 인가가 필요한데, 브라우저의 대용량 스트리밍
다운로드(`window.location`)는 Authorization 헤더나 요청 바디를 실을 수 없다. 그래서:

  1. 인증된 클라이언트가 `POST .../download-ticket` 로 인가 검사를 통과하고 티켓을 발급받는다.
  2. 브라우저가 `GET /api/files/download?ticket=...`(무헤더)로 스트리밍 다운로드한다.

티켓은 Redis 에 짧은 TTL(60초)로 저장하고, 소비 시 원자적 삭제(GETDEL)로 **일회성**을
보장한다. 티켓 값에는 재인가/재해석에 필요한 최소 정보만 담는다.

영상 미리보기 티켓은 성질이 달라 별도 접두어로 나눈다(아래 `issue_preview_ticket`).
"""

from __future__ import annotations

import json
import secrets
from typing import Any

from redis.asyncio import Redis

# 티켓 TTL — 내부 presign(60초)과 동일 수명. 발급 후 즉시 사용을 전제한다.
TICKET_TTL_SECONDS = 60

_TICKET_PREFIX = "dlticket:"

# 영상 미리보기 티켓 TTL — 마지막 사용 시점부터 30분(아래 peek 가 슬라이딩 갱신).
PREVIEW_TICKET_TTL_SECONDS = 30 * 60

_PREVIEW_PREFIX = "pvticket:"

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
    return _decode(raw)


async def issue_preview_ticket(redis: Redis, payload: dict[str, Any]) -> str:
    """영상 미리보기 스트림 티켓을 발급한다.

    다운로드 티켓과 두 가지가 다르다:
      - **재사용 가능**: 영상 한 편 재생은 `<video>` 가 보내는 Range 요청 수십 건이라 일회용으로는
        첫 조각만 받고 끊긴다. 소비 대신 조회(peek)하고, 쓰일 때마다 TTL 을 밀어 준다.
      - **별도 접두어**: 다운로드 티켓과 공간이 갈려 있어, 미리보기 티켓이 다운로드 라우트에서
        소비되어 공유의 max_downloads 를 깎는 일이 구조적으로 일어날 수 없다.

    티켓은 헤더 없는 주소 하나로 영상을 여는 열쇠이므로, 소비 측(스트림 라우트)이 매 요청 인가를
    다시 판정한다 — 공유가 비활성화되거나 권한이 회수되면 남은 티켓도 그 즉시 막힌다.
    """
    token = secrets.token_urlsafe(_TOKEN_BYTES)
    await redis.set(
        _PREVIEW_PREFIX + token,
        json.dumps(payload),
        ex=PREVIEW_TICKET_TTL_SECONDS,
    )
    return token


async def peek_preview_ticket(redis: Redis, token: str) -> dict[str, Any] | None:
    """미리보기 티켓을 조회하고 TTL 을 갱신한다(삭제하지 않음). 없거나 만료면 None.

    재생 중에는 Range 요청이 계속 TTL 을 밀어 티켓이 살아 있고, 창을 닫으면 30분 뒤 사라진다.
    """
    if not token:
        return None
    key = _PREVIEW_PREFIX + token
    raw = await redis.get(key)
    if raw is None:
        return None
    await redis.expire(key, PREVIEW_TICKET_TTL_SECONDS)
    return _decode(raw)


def _decode(raw: Any) -> dict[str, Any] | None:
    """Redis 원문을 티켓 payload 로 되돌린다. 깨졌으면 None(=무효 티켓)."""
    if raw is None:
        return None
    try:
        return json.loads(raw)
    except (ValueError, TypeError):
        return None
