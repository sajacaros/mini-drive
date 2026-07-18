"""운영 CLI (PRD 3.6.2).

`python -m app.cli create-admin --email a@b.com --password ...` — 운영 중 기존 사용자
승격/비상 복구용 admin 생성 경로. 첫 부팅의 정상 경로는 셋업 위저드(POST /api/setup)다.
"""

from __future__ import annotations

import argparse
import asyncio
import getpass
import sys

from app.core.database import SessionFactory
from app.core.security import PasswordPolicyError, validate_password_policy
from app.services.setup import admin_exists, create_admin_account
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

    args = parser.parse_args(argv)
    if args.command == "create-admin":
        password = args.password or getpass.getpass("admin 비밀번호: ")
        return asyncio.run(_create_admin(args.email, password, force=args.force))
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
