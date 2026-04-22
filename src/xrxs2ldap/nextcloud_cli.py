from __future__ import annotations

import argparse

from xrxs2ldap.cli import _build_adapter, _timestamp, load_dotenv_file
from xrxs2ldap.config import load_settings
from xrxs2ldap.nextcloud_sync import NextcloudGroupSyncService


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Sync HR users into existing Nextcloud groups.")
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Preview Nextcloud group changes without writing them.",
    )
    return parser


def main() -> None:
    parser = build_parser()
    args = parser.parse_args()
    load_dotenv_file()
    settings = load_settings()
    if args.dry_run:
        settings.dry_run = True

    print(f"{_timestamp()} nextcloud group sync started.")
    snapshot = _build_adapter(settings).fetch_snapshot()
    stats = NextcloudGroupSyncService(settings).sync(snapshot)
    print(
        f"{_timestamp()} nextcloud group sync finished:",
        {
            "users_seen": stats.users_seen,
            "users_missing": stats.users_missing,
            "users_without_matching_department_group": stats.users_without_matching_department_group,
            "duplicate_display_names": stats.duplicate_display_names,
            "memberships_added": stats.memberships_added,
            "memberships_removed": stats.memberships_removed,
            "dry_run": settings.dry_run,
        },
    )


if __name__ == "__main__":
    main()
