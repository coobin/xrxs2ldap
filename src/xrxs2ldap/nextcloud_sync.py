from __future__ import annotations

import csv
import io
import subprocess
from collections import defaultdict
from dataclasses import dataclass
from typing import Any

from xrxs2ldap.config import Settings
from xrxs2ldap.models import HrSnapshot


@dataclass(slots=True)
class NextcloudGroupSyncStats:
    users_seen: int = 0
    users_missing: int = 0
    users_without_matching_department_group: int = 0
    duplicate_display_names: int = 0
    memberships_added: int = 0
    memberships_removed: int = 0


class NextcloudGroupSyncService:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings

    def sync(self, snapshot: HrSnapshot) -> NextcloudGroupSyncStats:
        stats = NextcloudGroupSyncStats()
        self._ensure_group(self.settings.nextcloud_default_group)

        groups = self._load_groups()
        users = self._load_users()
        memberships = self._load_group_memberships()

        departments_by_id = {department.id: department for department in snapshot.departments}
        department_names = {department.name for department in snapshot.departments}
        group_gid_by_display_name, duplicate_display_names = self._index_groups_by_display_name(
            groups,
            department_names,
        )
        stats.duplicate_display_names = len(duplicate_display_names)
        managed_department_gids = set(group_gid_by_display_name.values())

        for employee in snapshot.employees:
            if not employee.active:
                self._remove_user_from_managed_groups(
                    employee.username,
                    memberships,
                    managed_department_gids | {self.settings.nextcloud_default_group},
                    stats,
                )
                continue

            stats.users_seen += 1
            if employee.username not in users:
                stats.users_missing += 1
                print(f"nextcloud user missing uid={employee.username}; skipping group sync")
                continue

            desired_gids = {self.settings.nextcloud_default_group}
            department = departments_by_id.get(employee.department_id or "")
            if department is not None:
                department_gid = group_gid_by_display_name.get(department.name)
                if department_gid:
                    desired_gids.add(department_gid)
                else:
                    stats.users_without_matching_department_group += 1
                    print(
                        "nextcloud department group display name not found "
                        f"uid={employee.username} department={department.name}"
                    )

            self._sync_user_memberships(
                employee.username,
                desired_gids,
                managed_department_gids | {self.settings.nextcloud_default_group},
                memberships,
                stats,
            )

        for display_name, gids in sorted(duplicate_display_names.items()):
            print(
                "nextcloud duplicate group display name ignored "
                f"displayName={display_name} gids={', '.join(sorted(gids))}"
            )

        return stats

    def _index_groups_by_display_name(
        self,
        groups: dict[str, str],
        department_names: set[str],
    ) -> tuple[dict[str, str], dict[str, list[str]]]:
        gids_by_display_name: dict[str, list[str]] = defaultdict(list)
        for gid, display_name in groups.items():
            if display_name in department_names:
                gids_by_display_name[display_name].append(gid)

        unique = {
            display_name: gids[0]
            for display_name, gids in gids_by_display_name.items()
            if len(gids) == 1
        }
        duplicates = {
            display_name: gids
            for display_name, gids in gids_by_display_name.items()
            if len(gids) > 1
        }
        return unique, duplicates

    def _sync_user_memberships(
        self,
        uid: str,
        desired_gids: set[str],
        managed_gids: set[str],
        memberships: dict[str, set[str]],
        stats: NextcloudGroupSyncStats,
    ) -> None:
        current_gids = memberships.get(uid, set())
        for gid in sorted(desired_gids - current_gids):
            self._add_user_to_group(uid, gid)
            memberships[uid].add(gid)
            stats.memberships_added += 1

        for gid in sorted((current_gids & managed_gids) - desired_gids):
            self._remove_user_from_group(uid, gid)
            memberships[uid].discard(gid)
            stats.memberships_removed += 1

    def _remove_user_from_managed_groups(
        self,
        uid: str,
        memberships: dict[str, set[str]],
        managed_gids: set[str],
        stats: NextcloudGroupSyncStats,
    ) -> None:
        for gid in sorted(memberships.get(uid, set()) & managed_gids):
            self._remove_user_from_group(uid, gid)
            memberships[uid].discard(gid)
            stats.memberships_removed += 1

    def _ensure_group(self, gid: str) -> None:
        if gid in self._load_groups():
            return
        if self.settings.dry_run:
            print(f"DRY-RUN nextcloud create group gid={gid}")
            return
        if self.settings.nextcloud_db_host:
            self._execute_nextcloud_db(
                "INSERT INTO oc_groups (gid, displayname) VALUES (%s, %s)",
                (gid, gid),
            )
            print(f"nextcloud created group gid={gid}")
            return
        self._run_occ("group:add", gid)

    def _add_user_to_group(self, uid: str, gid: str) -> None:
        if self.settings.dry_run:
            print(f"DRY-RUN nextcloud add uid={uid} gid={gid}")
            return
        if self.settings.nextcloud_db_host:
            self._execute_nextcloud_db(
                "INSERT IGNORE INTO oc_group_user (gid, uid) VALUES (%s, %s)",
                (gid, uid),
            )
            print(f"nextcloud added uid={uid} gid={gid}")
            return
        self._run_occ("group:adduser", gid, uid)
        print(f"nextcloud added uid={uid} gid={gid}")

    def _remove_user_from_group(self, uid: str, gid: str) -> None:
        if self.settings.dry_run:
            print(f"DRY-RUN nextcloud remove uid={uid} gid={gid}")
            return
        if self.settings.nextcloud_db_host:
            self._execute_nextcloud_db(
                "DELETE FROM oc_group_user WHERE gid = %s AND uid = %s",
                (gid, uid),
            )
            print(f"nextcloud removed uid={uid} gid={gid}")
            return
        self._run_occ("group:removeuser", gid, uid)
        print(f"nextcloud removed uid={uid} gid={gid}")

    def _load_groups(self) -> dict[str, str]:
        rows = self._query_nextcloud_db("SELECT gid, displayname FROM oc_groups ORDER BY gid")
        return {row["gid"]: row["displayname"] for row in rows}

    def _load_users(self) -> set[str]:
        rows = self._query_nextcloud_db("SELECT uid FROM oc_users")
        return {row["uid"] for row in rows}

    def _load_group_memberships(self) -> dict[str, set[str]]:
        rows = self._query_nextcloud_db("SELECT uid, gid FROM oc_group_user")
        memberships: dict[str, set[str]] = defaultdict(set)
        for row in rows:
            memberships[row["uid"]].add(row["gid"])
        return memberships

    def _query_nextcloud_db(self, sql: str) -> list[dict[str, str]]:
        if self.settings.nextcloud_db_host:
            return self._query_nextcloud_db_direct(sql)

        db_name = self._run_occ("config:system:get", "dbname").stdout.strip()
        db_user = self._run_occ("config:system:get", "dbuser").stdout.strip()
        db_password = self._run_occ("config:system:get", "dbpassword").stdout.strip()
        result = subprocess.run(
            [
                "docker",
                "exec",
                self.settings.nextcloud_db_container,
                "mariadb",
                f"-u{db_user}",
                f"-p{db_password}",
                "--batch",
                "--raw",
                db_name,
                "-e",
                sql,
            ],
            check=True,
            capture_output=True,
            text=True,
        )
        reader = csv.DictReader(io.StringIO(result.stdout), delimiter="\t")
        return [dict(row) for row in reader]

    def _query_nextcloud_db_direct(self, sql: str) -> list[dict[str, str]]:
        connection = self._connect_nextcloud_db()
        try:
            with connection.cursor() as cursor:
                cursor.execute(sql)
                rows = cursor.fetchall()
            return [{key: str(value) for key, value in row.items()} for row in rows]
        finally:
            connection.close()

    def _execute_nextcloud_db(self, sql: str, params: tuple[Any, ...]) -> None:
        connection = self._connect_nextcloud_db()
        try:
            with connection.cursor() as cursor:
                cursor.execute(sql, params)
            connection.commit()
        finally:
            connection.close()

    def _connect_nextcloud_db(self):
        if not self.settings.nextcloud_db_host:
            raise RuntimeError("NEXTCLOUD_DB_HOST is required for direct Nextcloud DB access")
        if not self.settings.nextcloud_db_password:
            raise RuntimeError("NEXTCLOUD_DB_PASSWORD is required for direct Nextcloud DB access")

        import pymysql
        import pymysql.cursors

        return pymysql.connect(
            host=self.settings.nextcloud_db_host,
            port=self.settings.nextcloud_db_port,
            user=self.settings.nextcloud_db_user,
            password=self.settings.nextcloud_db_password,
            database=self.settings.nextcloud_db_name,
            charset="utf8mb4",
            cursorclass=pymysql.cursors.DictCursor,
        )

    def _run_occ(
        self,
        *args: str,
        check: bool = True,
    ) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [
                "docker",
                "exec",
                "-u",
                "www-data",
                self.settings.nextcloud_app_container,
                "php",
                "occ",
                *args,
            ],
            check=check,
            capture_output=True,
            text=True,
        )
