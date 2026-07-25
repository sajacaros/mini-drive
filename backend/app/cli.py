"""운영 CLI (PRD 3.6.2, spec/trash-retention-purge.md).

- `python -m app.cli create-admin --email a@b.com --password ...` — 운영 중 기존 사용자
  승격/비상 복구용 admin 생성 경로. 첫 부팅의 정상 경로는 셋업 위저드(POST /api/setup)다.
- `python -m app.cli purge-trash [--once|--loop|--dry-run]` — 보존 기간을 넘긴 휴지통 항목을
  영구 삭제한다. `--loop` 은 `purger` 사이드카가 쓰는 모드다.
"""

from __future__ import annotations

import argparse
import asyncio
import getpass
import sys
from datetime import UTC, datetime

from prometheus_client import start_http_server

from app.core.config import settings
from app.core.database import SessionFactory
from app.core.logging import configure_logging, get_logger
from app.core.security import PasswordPolicyError, validate_password_policy
from app.services import trash as trash_service
from app.services.setup import admin_exists, create_admin_account
from app.services.storage import get_storage
from app.services.users import get_user_by_email


async def _create_admin(email: str, password: str, *, force: bool) -> int:
    email = email.strip().lower()
    try:
        validate_password_policy(password)
    except PasswordPolicyError as exc:
        print(f"[create-admin] 비밀번호 정책 위반: {exc}", file=sys.stderr)
        return 2

    async with SessionFactory() as session:
        if await get_user_by_email(session, email) is not None:
            print(f"[create-admin] 이미 존재하는 이메일: {email}", file=sys.stderr)
            return 1
        if await admin_exists(session) and not force:
            print(
                "[create-admin] 이미 admin 이 존재합니다. 그래도 추가하려면 --force.",
                file=sys.stderr,
            )
            return 1
        admin = await create_admin_account(session, email, password)
        await session.commit()
        print(f"[create-admin] admin 생성 완료: id={admin.id} email={admin.email}")
    return 0


def _human_bytes(n: int) -> str:
    unit = ("B", "KiB", "MiB", "GiB", "TiB")
    size = float(n)
    for u in unit:
        if size < 1024 or u == unit[-1]:
            return f"{size:.1f} {u}" if u != "B" else f"{int(size)} B"
        size /= 1024
    return f"{n} B"


def _report(result: trash_service.PurgeResult) -> None:
    tag = "[purge-trash]" + (" (dry-run)" if result.dry_run else "")
    if result.disabled:
        print(f"{tag} TRASH_RETENTION_DAYS=0 — 자동 정리가 비활성화되어 있습니다.")
        return
    if result.locked_out:
        print(f"{tag} 다른 프로세스가 정리 중입니다 — 이번 회차를 건너뜁니다.")
        return
    verb = "삭제 예정" if result.dry_run else "삭제"
    print(
        f"{tag} 보존 기간 {settings.trash_retention_days}일 초과 — "
        f"항목 {result.roots}건({result.rows}행) {verb}, "
        f"회수 {_human_bytes(result.bytes_reclaimed)}"
        + (f", 실패 {result.failed}건" if result.failed else "")
    )


async def _purge_once(*, dry_run: bool) -> trash_service.PurgeResult:
    async with SessionFactory() as session:
        return await trash_service.purge_expired(session, get_storage(), dry_run=dry_run)


# 사이드카가 메트릭을 노출할 포트. backend 의 내부 포트와 같은 번호를 쓰되 컨테이너가 다르므로
# 충돌하지 않는다 (스크레이프 대상은 `purger:8000`).
_METRICS_PORT = 8000


def _serve_metrics(log) -> None:  # noqa: ANN001 - structlog BoundLogger
    """`--loop` 전용 Prometheus 노출.

    이 프로세스는 ASGI 앱이 아니라 `/metrics` 라우트가 없고, backend 의 `/metrics` 는 **다른
    프로세스의 레지스트리**라 여기서 올린 카운터가 그쪽에 나타나지 않는다. 스크레이프가 가능하려면
    사이드카가 직접 열어야 한다. 실패해도 정리는 계속한다 — 관측이 기능을 막지 않는다.
    """
    if not settings.metrics_enabled:
        return
    try:
        start_http_server(_METRICS_PORT)
        log.info("trash_purge_metrics_served", port=_METRICS_PORT)
    except OSError:
        log.warning("trash_purge_metrics_unavailable", port=_METRICS_PORT)


async def _purge_trash(*, dry_run: bool, loop: bool) -> int:
    if not loop:
        _report(await _purge_once(dry_run=dry_run))
        return 0

    # 사이드카 모드 — 구조화 로깅으로 남긴다(로그가 자동 삭제의 주 관측 경로다).
    configure_logging()
    log = get_logger("app.trash.loop")
    _serve_metrics(log)
    if settings.trash_retention_days <= 0:
        # 종료하면 restart 정책(unless-stopped)이 재시작을 반복하므로 유휴 대기한다.
        log.info("trash_purge_disabled_idling")
        while True:
            await asyncio.sleep(3600)

    log.info(
        "trash_purge_loop_started",
        retention_days=settings.trash_retention_days,
        purge_hour=settings.trash_purge_hour,
    )
    while True:
        # 잠들기를 먼저 한다 — 기동 직후에 돌지 않으므로 첫 부팅의 마이그레이션과 겹치지 않는다.
        now = datetime.now(UTC)
        target = trash_service.next_run_at(now)
        delay = (target - now).total_seconds()
        log.info("trash_purge_sleeping", next_run_at=target.isoformat(), seconds=round(delay))
        await asyncio.sleep(delay)
        started = datetime.now(UTC)
        try:
            result = await _purge_once(dry_run=False)
        except Exception:  # noqa: BLE001 - 한 회차의 실패로 루프를 죽이지 않는다.
            log.exception("trash_purge_cycle_failed")
            continue
        log.info(
            "trash_purge_done",
            roots=result.roots,
            rows=result.rows,
            bytes_reclaimed=result.bytes_reclaimed,
            failed=result.failed,
            locked_out=result.locked_out,
            duration_ms=round((datetime.now(UTC) - started).total_seconds() * 1000),
        )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="app.cli")
    sub = parser.add_subparsers(dest="command", required=True)

    p_admin = sub.add_parser("create-admin", help="admin 계정 생성")
    p_admin.add_argument("--email", required=True)
    p_admin.add_argument(
        "--password", help="미지정 시 대화형 프롬프트로 입력받는다."
    )
    p_admin.add_argument(
        "--force", action="store_true", help="admin 이 이미 있어도 추가 생성"
    )

    p_purge = sub.add_parser(
        "purge-trash", help="보존 기간을 넘긴 휴지통 항목을 영구 삭제 (기본: 1회 실행)"
    )
    p_purge.add_argument(
        "--dry-run",
        action="store_true",
        help="아무것도 지우지 않고 대상 건수/회수될 용량만 출력",
    )
    p_purge.add_argument(
        "--once", action="store_true", help="1회 실행 후 종료 (기본 동작, 명시용)"
    )
    p_purge.add_argument(
        "--loop",
        action="store_true",
        help="매일 TRASH_PURGE_HOUR(KST)에 반복 실행 (purger 사이드카 모드)",
    )

    args = parser.parse_args(argv)
    if args.command == "create-admin":
        password = args.password or getpass.getpass("admin 비밀번호: ")
        return asyncio.run(_create_admin(args.email, password, force=args.force))
    if args.command == "purge-trash":
        if args.loop and args.dry_run:
            print("[purge-trash] --loop 과 --dry-run 은 함께 쓸 수 없습니다.", file=sys.stderr)
            return 2
        return asyncio.run(_purge_trash(dry_run=args.dry_run, loop=args.loop))
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
