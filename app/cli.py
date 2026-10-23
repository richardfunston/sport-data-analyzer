"""Create member accounts from the shell.

The first account's password is deliberately not in the repository, so it comes
from ``--password`` or from ``SDA_INITIAL_MEMBER_PASSWORD``.

    python -m app.cli create-member --email rpf@funston.com --name Richard \
        --password '…' --admin
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

from .auth import usable_email
from .config import Config
from .storage import DuplicateEmail, Store


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="python -m app.cli", description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)

    create = sub.add_parser("create-member", help="create a member account")
    create.add_argument("--email", required=True)
    create.add_argument("--name", required=True)
    create.add_argument(
        "--password",
        default=None,
        help="omit to read SDA_INITIAL_MEMBER_PASSWORD from the environment",
    )
    create.add_argument("--admin", action="store_true", help="make them an admin")
    create.add_argument(
        "--data-dir", default=None, help="override SDA_DATA_DIR for this run"
    )
    return parser


def _create_member(store: Store, args: argparse.Namespace) -> int:
    email = (args.email or "").strip()
    name = (args.name or "").strip()
    password = args.password or os.environ.get("SDA_INITIAL_MEMBER_PASSWORD") or ""

    if not usable_email(email):
        print(f"error: {email!r} is not a usable email address", file=sys.stderr)
        return 2
    if not name:
        print("error: a name is required", file=sys.stderr)
        return 2
    if not password:
        print(
            "error: a password is required, via --password or "
            "SDA_INITIAL_MEMBER_PASSWORD",
            file=sys.stderr,
        )
        return 2

    try:
        user = store.create_member(email, name, password, is_admin=args.admin)
    except DuplicateEmail:
        print(
            f"error: there is already a member with the email {email}", file=sys.stderr
        )
        return 1

    # The password is never echoed back.
    role = "admin" if user["is_admin"] else "member"
    print(f"created {role} {user['name']} <{user['email']}> (id {user['id']})")
    return 0


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    data_dir = Path(args.data_dir) if getattr(args, "data_dir", None) else Config.DATA_DIR

    store = Store(data_dir)
    if args.command == "create-member":
        return _create_member(store, args)
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
