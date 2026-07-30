"""Operator CLI: `python -m backend.cli set-password`.

Bootstraps or rotates the single shared application password. The plain
password is only ever held in memory for the duration of this process; only
its Argon2id hash is written to the database.
"""
import argparse
import getpass
import sys

from .app import db, security


def cmd_set_password(_args: argparse.Namespace) -> int:
    pw1 = getpass.getpass("New password: ")
    if len(pw1) < 8:
        print("Password must be at least 8 characters.", file=sys.stderr)
        return 1
    pw2 = getpass.getpass("Confirm password: ")
    if pw1 != pw2:
        print("Passwords do not match.", file=sys.stderr)
        return 1
    conn = db.get_connection()
    security.set_password(conn, pw1)
    print("Password updated.")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(prog="python -m backend.cli")
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("set-password", help="Set or rotate the shared login password")
    args = parser.parse_args()
    if args.command == "set-password":
        return cmd_set_password(args)
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
