"""운영 CLI (PRD 3.6.2, spec/trash-retention-purge.md).

- `python -m app.cli create-admin --email a@b.com --password ...` — 운영 중 기존 사용자
  승격/비상 복구용 admin 생성 경로. 첫 부팅의 정상 경로는 셋업 위저드(POST /api/setup)다.
- `python -m app.cli purge-trash [--once|--loop|--dry-run]` — 보존 기간을 넘긴 휴지통 항목을
  영구 삭제한다. `--loop` 은 `purger` 사이드카가 쓰는 모드다.
- `python -m app.cli index-wiki [--once|--loop]` — 위키 인덱싱 큐를 처리한다.
  `--loop` 은 `wiki-indexer` 사이드카가 쓰는 모드다 (spec/wiki-index.md).
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
from app.services import wiki as wiki_service
from app.services import wiki_indexer, wiki_llm
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


async def _purge_wiki_trees() -> int:
    """위키를 끈 뒤 유예가 지난 트리를 지운다 (spec/wiki-index.md).

    휴지통 정리와 같은 회차에 얹는다 — 둘 다 "유예가 지난 파생물 정리"이고 하루 1회면 충분해서
    사이드카를 새로 띄울 이유가 없다. 파일이 영구 삭제되는 경우는 wiki_documents 의
    ON DELETE CASCADE 가 처리하므로 여기서 다루지 않는다.
    """
    if not settings.wiki_enabled:
        return 0
    async with SessionFactory() as session:
        result = await wiki_service.purge_disabled_trees(session)
    return result.deleted


# 사이드카가 메트릭을 노출할 포트. backend 의 내부 포트와 같은 번호를 쓰되 컨테이너가 다르므로
# 충돌하지 않는다 (스크레이프 대상은 `purger:8000`).
_METRICS_PORT = 8000

# 인덱싱 큐가 비었을 때 다시 볼 때까지의 대기. 업로드 직후 반응이 있어야 하므로 짧게 둔다.
_IDLE_SLEEP_SECONDS = 5


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
        try:
            wiki_trees = await _purge_wiki_trees()
        except Exception:  # noqa: BLE001 - 위키 정리 실패가 휴지통 회차 보고를 막지 않는다
            log.exception("wiki_tree_purge_failed")
            wiki_trees = 0
        log.info(
            "trash_purge_done",
            wiki_trees_purged=wiki_trees,
            roots=result.roots,
            rows=result.rows,
            bytes_reclaimed=result.bytes_reclaimed,
            failed=result.failed,
            locked_out=result.locked_out,
            duration_ms=round((datetime.now(UTC) - started).total_seconds() * 1000),
        )


async def _index_wiki(*, loop: bool, limit: int) -> int:
    """위키 인덱싱 워커 (spec/wiki-index.md).

    `purge-trash --loop` 과 달리 **스케줄이 아니라 큐 구동**이다. 큐가 비면 짧게 자고 다시
    본다 — 업로드 직후 반응이 있어야 하므로 긴 주기로 자면 안 된다.
    """
    configure_logging()
    log = get_logger("app.wiki.indexer")

    if not settings.wiki_enabled:
        # 종료하면 restart 정책이 재시작을 반복하므로 유휴 대기한다(purger 와 같은 처리).
        log.info("wiki_indexer_disabled_idling")
        while True:
            await asyncio.sleep(3600)

    reachable = await wiki_llm.health()
    log.info(
        "wiki_indexer_started",
        model=settings.wiki_llm_model,
        base_url=settings.wiki_llm_base_url,
        llm_reachable=reachable,
        loop=loop,
    )

    # 기동 시 한 번 — 큐가 유실된 채로 남아 있던 작업을 회수한다(Redis flush·크래시 복구).
    async with SessionFactory() as session:
        try:
            recovered = await wiki_indexer.requeue_orphans(session)
            if recovered:
                log.info("wiki_orphans_requeued", count=recovered)
        except Exception:  # noqa: BLE001 - 회수 실패가 기동을 막지 않는다
            log.exception("wiki_orphan_requeue_failed")

    storage = get_storage()
    while True:
        async with SessionFactory() as session:
            try:
                counts = await wiki_indexer.run_once(session, storage, limit=limit)
            except Exception:  # noqa: BLE001 - 한 회차의 실패로 루프를 죽이지 않는다
                log.exception("wiki_index_cycle_failed")
                counts = {}
        if counts:
            log.info("wiki_index_cycle", **counts)
        if not loop:
            return 0
        if not counts:
            await asyncio.sleep(_IDLE_SLEEP_SECONDS)


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

    p_wiki = sub.add_parser(
        "index-wiki", help="위키 인덱싱 큐 처리 (기본: 1회 실행)"
    )
    p_wiki.add_argument(
        "--once", action="store_true", help="큐를 한 번만 비우고 종료 (기본 동작, 명시용)"
    )
    p_wiki.add_argument(
        "--loop", action="store_true", help="계속 대기하며 처리 (wiki-indexer 사이드카 모드)"
    )
    p_wiki.add_argument(
        "--limit", type=int, default=5, help="한 회차에 처리할 최대 문서 수"
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
    if args.command == "index-wiki":
        return asyncio.run(_index_wiki(loop=args.loop, limit=args.limit))
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
