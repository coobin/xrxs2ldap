from __future__ import annotations

import argparse
import os
import time
from datetime import datetime
from pathlib import Path

from xrxs2ldap.adapters.json_file import JsonFileAdapter
from xrxs2ldap.adapters.xinrenxinshi import XinrenxinshiAdapter
from xrxs2ldap.config import load_settings
from xrxs2ldap.ldap_sync import LdapSyncService


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Sync HR data into OpenLDAP.")
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Preview LDAP changes without writing them.",
    )
    parser.add_argument(
        "--once",
        action="store_true",
        help="Run one sync and exit.",
    )
    return parser


def load_dotenv_file(file_path: str = ".env") -> None:
    env_file = Path(file_path)
    if not env_file.exists():
        return

    for raw_line in env_file.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        if key and key not in os.environ:
            os.environ[key] = value


def main() -> None:
    parser = build_parser()
    args = parser.parse_args()
    load_dotenv_file()
    settings = load_settings()
    if args.dry_run:
        settings.dry_run = True
    if args.once:
        settings.run_once = True

    adapter = _build_adapter(settings)
    if settings.run_once:
        _run_sync_once(adapter, settings)
        return

    print(
        f"{_timestamp()} scheduler started; first sync runs now, next runs every "
        f"{settings.sync_interval_seconds} seconds."
    )
    while True:
        _run_sync_once(adapter, settings)
        print(f"{_timestamp()} sleeping for {settings.sync_interval_seconds} seconds.")
        time.sleep(settings.sync_interval_seconds)


def _build_adapter(settings):
    if settings.hr_source == "json_file":
        return JsonFileAdapter(settings.json_file_path or "samples/hr_data.json")
    if settings.hr_source == "xinrenxinshi":
        return XinrenxinshiAdapter(settings)
    raise ValueError(f"Unsupported HR_SOURCE: {settings.hr_source}")


def _run_sync_once(adapter, settings) -> None:
    print(f"{_timestamp()} sync started.")
    try:
        snapshot = adapter.fetch_snapshot()
        stats = LdapSyncService(settings).sync(snapshot)
        print(
            f"{_timestamp()} sync finished:",
            {
                "departments_created": stats.departments_created,
                "departments_updated": stats.departments_updated,
                "employees_created": stats.employees_created,
                "employees_updated": stats.employees_updated,
                "employees_archived": stats.employees_archived,
                "dry_run": settings.dry_run,
            },
        )
    except Exception as exc:
        print(f"{_timestamp()} sync failed: {exc}")


def _timestamp() -> str:
    return datetime.now().isoformat(timespec="seconds")


if __name__ == "__main__":
    main()
