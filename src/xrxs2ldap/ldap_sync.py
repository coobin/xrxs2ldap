from __future__ import annotations

import zlib
from collections import defaultdict
from dataclasses import dataclass

from ldap3 import ALL, BASE, MODIFY_REPLACE, Connection, Server, SUBTREE
from ldap3.utils.dn import escape_rdn

from xrxs2ldap.config import Settings
from xrxs2ldap.models import Department, Employee, HrSnapshot


@dataclass(slots=True)
class SyncStats:
    employees_created: int = 0
    employees_updated: int = 0
    employees_archived: int = 0
    groups_created: int = 0
    groups_updated: int = 0


@dataclass(slots=True)
class ExistingEmployeeIndex:
    by_employee_number: dict[str, str]
    by_mail: dict[str, list[str]]
    by_cn: dict[str, list[str]]
    by_uid: dict[str, list[str]]
    by_dn: dict[str, object]


@dataclass(slots=True)
class EmployeeDnMatch:
    dn: str | None
    priority: int


class LdapSyncService:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings

    def sync(self, snapshot: HrSnapshot) -> SyncStats:
        stats = SyncStats()
        server = Server(self.settings.ldap_uri, get_info=ALL)
        connection = Connection(
            server,
            user=self.settings.ldap_bind_dn,
            password=self.settings.ldap_bind_password,
            auto_bind=True,
        )
        try:
            self._ensure_base_entries(connection)
            employee_dns, department_members = self._sync_employees(
                connection,
                snapshot.employees,
                stats,
            )
            self._sync_department_groups(connection, snapshot.departments, department_members, stats)
            if self.settings.archive_missing:
                self._archive_missing_employees(connection, employee_dns, stats)
            return stats
        finally:
            connection.unbind()

    def _ensure_base_entries(self, connection: Connection) -> None:
        self._ensure_entry(
            connection,
            self.settings.people_base_dn,
            ["top", "organizationalUnit"],
            {"ou": ["people"]},
        )
        self._ensure_entry(
            connection,
            self.settings.groups_base_dn,
            ["top", "organizationalUnit"],
            {"ou": ["groups"]},
        )

    def _sync_employees(
        self,
        connection: Connection,
        employees: list[Employee],
        stats: SyncStats,
    ) -> tuple[dict[str, str], dict[str, list[str]]]:
        employee_dns: dict[str, str] = {}
        department_members: dict[str, list[str]] = defaultdict(list)
        by_employee_id = {employee.id: employee for employee in employees}
        display_name_counts: dict[str, int] = defaultdict(int)
        for employee in employees:
            display_name_counts[employee.display_name] += 1
        existing = self._load_existing_employees(connection)
        planned_matches = [
            (employee, self._match_employee_dn(employee, existing, display_name_counts))
            for employee in employees
        ]
        claimed_dns: set[str] = set()

        claimed_uids: set[str] = set()
        planned_uid_by_dn: dict[str, str] = {}
        for employee, match in planned_matches:
            if match.dn is not None:
                planned_uid_by_dn[match.dn] = employee.username

        for employee, match in sorted(planned_matches, key=lambda item: item[1].priority):
            dn = match.dn
            if dn is not None and dn in claimed_dns:
                dn = None

            existing_entry = existing.by_dn.get(dn) if dn is not None else None
            uid = self._resolve_employee_uid(
                employee,
                existing,
                existing_entry,
                claimed_uids,
                dn,
                planned_uid_by_dn,
            )
            if dn is None:
                dn = self._employee_dn_for_uid(uid)

            claimed_dns.add(dn)
            claimed_uids.add(uid)
            employee_dns[employee.id] = dn
            if employee.department_id and employee.active:
                department_members[employee.department_id].append(uid)
            attrs = self._employee_attributes(employee, by_employee_id, existing_entry, uid)
            changed = self._upsert_entry(
                connection,
                dn,
                ["top", "person", "organizationalPerson", "inetOrgPerson"],
                attrs,
                log_label=f"employee {attrs['displayName'][0]} (uid={uid}, id={employee.id})",
            )
            if changed == "created":
                stats.employees_created += 1
                existing.by_dn[dn] = None
                existing.by_employee_number[employee.id] = dn
            elif changed == "updated":
                stats.employees_updated += 1
        return employee_dns, {key: sorted(set(value)) for key, value in department_members.items()}

    def _sync_department_groups(
        self,
        connection: Connection,
        departments: list[Department],
        department_members: dict[str, list[str]],
        stats: SyncStats,
    ) -> None:
        departments_by_id = {department.id: department for department in departments}
        name_counts: dict[tuple[str | None, str], int] = defaultdict(int)
        for department in departments:
            name_counts[self._department_sibling_name_key(department, departments_by_id)] += 1

        for department in sorted(
            departments,
            key=lambda item: self._department_depth(item, departments_by_id),
        ):
            group_name = self._department_group_name(department, departments_by_id, name_counts)
            group_dn = self._department_group_dn(department, departments_by_id, name_counts)
            changed = self._upsert_department_group(
                connection,
                group_dn,
                group_name,
                department.id,
                department_members.get(department.id, []),
            )
            if changed == "created":
                stats.groups_created += 1
            elif changed == "updated":
                stats.groups_updated += 1

    def _upsert_department_group(
        self,
        connection: Connection,
        dn: str,
        group_name: str,
        department_id: str,
        member_uids: list[str],
    ) -> str:
        attrs = {
            "cn": [group_name],
            "gidNumber": [self._department_gid_number(department_id)],
        }
        if member_uids:
            attrs["memberUid"] = member_uids

        log_label = f"group {group_name} ({department_id})"
        if not connection.search(dn, "(objectClass=*)", attributes=["memberUid"], search_scope=BASE):
            self._add_entry(connection, dn, ["top", "posixGroup"], attrs, log_label)
            return "created"

        entry = connection.entries[0]
        existing = sorted(str(value) for value in entry["memberUid"].values) if "memberUid" in entry else []
        expected = sorted(member_uids)
        if existing == expected:
            return "unchanged"

        changes = {"memberUid": [(MODIFY_REPLACE, member_uids)]}
        if self.settings.dry_run:
            print(f"DRY-RUN modify {dn}: {changes}")
        else:
            if not connection.modify(dn, changes):
                raise RuntimeError(f"Failed to modify {dn}: {connection.result}")
            self._log_change(
                action="updated",
                label=log_label,
                dn=dn,
                field_names=["memberUid"],
            )
        return "updated"

    def _employee_attributes(
        self,
        employee: Employee,
        by_employee_id: dict[str, Employee],
        existing_entry,
        uid: str,
    ) -> dict[str, list[str]]:
        surname = employee.last_name or employee.display_name[:1] or employee.username
        given_name = employee.first_name or employee.display_name
        cn = employee.display_name
        display_name = employee.display_name

        # Preserve an existing disambiguated LDAP name such as "李珊(销管)"
        # so duplicate display names do not collapse back to the raw HR name.
        if existing_entry is not None:
            existing_cn = self._first_attr_value(existing_entry, "cn")
            existing_display_name = self._first_attr_value(existing_entry, "displayName")
            if existing_cn and existing_cn != employee.display_name:
                cn = existing_cn
                if not existing_display_name:
                    display_name = existing_cn
            if existing_display_name and existing_display_name != employee.display_name:
                display_name = existing_display_name

        employee_type = "active" if employee.active else "inactive"
        # Do not auto-reactivate manually deactivated LDAP users.
        existing_employee_type = self._first_attr_value(existing_entry, "employeeType")
        if employee.active and existing_employee_type and existing_employee_type.lower() in {"deactive", "inactive"}:
            employee_type = existing_employee_type

        attrs: dict[str, list[str]] = {
            "cn": [cn],
            "sn": [surname],
            "uid": [uid],
            "employeeNumber": [employee.id],
            "givenName": [given_name],
            "displayName": [display_name],
            "employeeType": [employee_type],
        }
        if employee.email:
            attrs["mail"] = [employee.email]
        if employee.department_id:
            attrs["departmentNumber"] = [employee.department_id]
            attrs["ou"] = [self._department_rdn_value(employee.department_id)]
        if employee.title:
            attrs["title"] = [employee.title]
        if employee.phone:
            attrs["telephoneNumber"] = [employee.phone]
        if employee.manager_employee_id:
            manager = by_employee_id.get(employee.manager_employee_id)
            if manager:
                attrs["manager"] = [self._employee_dn(manager.display_name)]
        return attrs

    def _archive_missing_employees(
        self,
        connection: Connection,
        current_dns: dict[str, str],
        stats: SyncStats,
    ) -> None:
        connection.search(
            search_base=self.settings.people_base_dn,
            search_filter="(objectClass=inetOrgPerson)",
            search_scope=SUBTREE,
            attributes=["distinguishedName", "employeeNumber", "employeeType"],
        )
        current_ids = set(current_dns)
        for entry in connection.entries:
            employee_number = str(entry.employeeNumber) if "employeeNumber" in entry else None
            if not employee_number or employee_number in current_ids:
                continue
            dn = entry.entry_dn
            changes = {"employeeType": [(MODIFY_REPLACE, ["inactive"])]}
            if self.settings.dry_run:
                print(f"DRY-RUN archive missing employee {employee_number} -> {dn}")
            else:
                connection.modify(dn, changes)
                self._log_change(
                    action="archived employee",
                    label=employee_number,
                    dn=dn,
                    field_names=["employeeType"],
                )
            stats.employees_archived += 1

    def _ensure_entry(
        self,
        connection: Connection,
        dn: str,
        object_classes: list[str],
        attributes: dict[str, list[str]],
    ) -> None:
        if connection.search(dn, "(objectClass=*)", attributes=["objectClass"], search_scope=BASE):
            return
        self._add_entry(connection, dn, object_classes, attributes)

    def _upsert_entry(
        self,
        connection: Connection,
        dn: str,
        object_classes: list[str],
        attributes: dict[str, list[str]],
        log_label: str,
    ) -> str:
        if not connection.search(dn, "(objectClass=*)", attributes=list(attributes), search_scope=BASE):
            self._add_entry(connection, dn, object_classes, attributes, log_label)
            return "created"

        entry = connection.entries[0]
        changes: dict[str, list[tuple[int, list[str]]]] = {}
        for attr, desired in attributes.items():
            existing = sorted(str(value) for value in entry[attr].values) if attr in entry else []
            expected = sorted(desired)
            if existing != expected:
                changes[attr] = [(MODIFY_REPLACE, desired)]

        if not changes:
            return "unchanged"

        if self.settings.dry_run:
            print(f"DRY-RUN modify {dn}: {changes}")
        else:
            if not connection.modify(dn, changes):
                raise RuntimeError(f"Failed to modify {dn}: {connection.result}")
            self._log_change(
                action="updated",
                label=log_label,
                dn=dn,
                field_names=sorted(changes),
            )
        return "updated"

    def _add_entry(
        self,
        connection: Connection,
        dn: str,
        object_classes: list[str],
        attributes: dict[str, list[str]],
        log_label: str | None = None,
    ) -> None:
        if self.settings.dry_run:
            print(f"DRY-RUN add {dn}: {attributes}")
            return
        non_empty_attributes = {
            key: value
            for key, value in attributes.items()
            if value
        }
        if not connection.add(dn, object_classes, non_empty_attributes):
            raise RuntimeError(f"Failed to add {dn}: {connection.result}")
        self._log_change(
            action="created",
            label=log_label or dn,
            dn=dn,
            field_names=sorted(attributes),
        )

    def _department_rdn_value(self, department_id: str) -> str:
        return f"dept-{department_id}"

    def _department_group_name(
        self,
        department: Department,
        departments_by_id: dict[str, Department],
        name_counts: dict[tuple[str | None, str], int],
    ) -> str:
        base_name = self._department_group_base_name(department)
        if name_counts[self._department_sibling_name_key(department, departments_by_id)] <= 1:
            return base_name
        return f"{base_name}-{department.id[:8]}"

    def _department_group_base_name(self, department: Department) -> str:
        return department.name

    def _department_group_dn(
        self,
        department: Department,
        departments_by_id: dict[str, Department],
        name_counts: dict[tuple[str | None, str], int],
    ) -> str:
        path = self._department_group_path(department, departments_by_id)
        rdns = [
            f"cn={escape_rdn(self._department_group_name(item, departments_by_id, name_counts))}"
            for item in reversed(path)
        ]
        return f"{','.join(rdns)},{self.settings.groups_base_dn}"

    def _department_group_path(
        self,
        department: Department,
        departments_by_id: dict[str, Department],
    ) -> list[Department]:
        path = [department]
        seen_ids = {department.id}
        current = department
        while current.parent_id:
            parent = departments_by_id.get(current.parent_id)
            if parent is None or parent.id in seen_ids:
                break
            path.append(parent)
            seen_ids.add(parent.id)
            current = parent
        return list(reversed(path))

    def _department_depth(
        self,
        department: Department,
        departments_by_id: dict[str, Department],
    ) -> int:
        return len(self._department_group_path(department, departments_by_id))

    def _department_sibling_name_key(
        self,
        department: Department,
        departments_by_id: dict[str, Department],
    ) -> tuple[str | None, str]:
        parent_id = department.parent_id if department.parent_id in departments_by_id else None
        return (parent_id, self._department_group_base_name(department))

    def _department_gid_number(self, department_id: str) -> str:
        return str(100000 + zlib.crc32(department_id.encode("utf-8")) % 800000)

    def _employee_dn(self, display_name: str) -> str:
        return f"cn={escape_rdn(display_name)},{self.settings.people_base_dn}"

    def _employee_dn_for_uid(self, uid: str) -> str:
        return f"uid={escape_rdn(uid)},{self.settings.people_base_dn}"

    def _log_change(
        self,
        action: str,
        label: str,
        dn: str,
        field_names: list[str],
    ) -> None:
        print(
            f"{action} {label}: fields={', '.join(field_names)} dn={dn}"
        )

    def _first_attr_value(self, entry: object, attr: str) -> str | None:
        if entry is None or attr not in entry:
            return None
        values = [str(value).strip() for value in entry[attr].values if str(value).strip()]
        if not values:
            return None
        return values[0]

    def _load_existing_employees(self, connection: Connection) -> ExistingEmployeeIndex:
        connection.search(
            search_base=self.settings.people_base_dn,
            search_filter="(objectClass=inetOrgPerson)",
            search_scope=SUBTREE,
            attributes=["cn", "displayName", "mail", "uid", "employeeNumber"],
        )

        by_employee_number: dict[str, str] = {}
        by_mail: dict[str, list[str]] = defaultdict(list)
        by_cn: dict[str, list[str]] = defaultdict(list)
        by_uid: dict[str, list[str]] = defaultdict(list)
        by_dn: dict[str, object] = {}

        for entry in connection.entries:
            dn = entry.entry_dn
            by_dn[dn] = entry

            if "employeeNumber" in entry:
                for value in entry["employeeNumber"].values:
                    text = str(value).strip()
                    if text:
                        by_employee_number[text] = dn

            if "mail" in entry:
                for value in entry["mail"].values:
                    text = str(value).strip().lower()
                    if text:
                        by_mail[text].append(dn)

            if "cn" in entry:
                for value in entry["cn"].values:
                    text = str(value).strip()
                    if text:
                        by_cn[text].append(dn)

            if "uid" in entry:
                for value in entry["uid"].values:
                    text = str(value).strip()
                    if text:
                        by_uid[text].append(dn)

        return ExistingEmployeeIndex(
            by_employee_number=by_employee_number,
            by_mail=dict(by_mail),
            by_cn=dict(by_cn),
            by_uid=dict(by_uid),
            by_dn=by_dn,
        )

    def _match_employee_dn(
        self,
        employee: Employee,
        existing: ExistingEmployeeIndex,
        display_name_counts: dict[str, int],
    ) -> EmployeeDnMatch:
        current_dn = existing.by_employee_number.get(employee.id)
        if current_dn:
            return EmployeeDnMatch(dn=current_dn, priority=0)

        if employee.email:
            mail_matches = existing.by_mail.get(employee.email.lower(), [])
            if len(mail_matches) == 1:
                return EmployeeDnMatch(dn=mail_matches[0], priority=1)
            if len(mail_matches) > 1:
                raise RuntimeError(
                    f"Ambiguous LDAP match for {employee.display_name}: multiple entries share mail "
                    f"{employee.email}: {mail_matches}"
                )

        if display_name_counts.get(employee.display_name, 0) > 1:
            return EmployeeDnMatch(dn=None, priority=3)

        cn_matches = existing.by_cn.get(employee.display_name, [])
        if len(cn_matches) == 1:
            matched_dn = cn_matches[0]
            matched_entry = existing.by_dn.get(matched_dn)
            if self._can_claim_legacy_cn_match(matched_entry):
                return EmployeeDnMatch(dn=matched_dn, priority=2)
            return EmployeeDnMatch(dn=None, priority=3)
        if len(cn_matches) > 1:
            raise RuntimeError(
                f"Ambiguous LDAP match for {employee.display_name}: multiple entries share cn "
                f"{employee.display_name}: {cn_matches}"
            )

        return EmployeeDnMatch(dn=None, priority=3)

    def _can_claim_legacy_cn_match(self, entry: object) -> bool:
        if entry is None:
            return False

        if "employeeNumber" in entry:
            for value in entry["employeeNumber"].values:
                if str(value).strip():
                    return False

        if "mail" in entry:
            for value in entry["mail"].values:
                if str(value).strip():
                    return False

        return True

    def _resolve_employee_uid(
        self,
        employee: Employee,
        existing: ExistingEmployeeIndex,
        existing_entry,
        claimed_uids: set[str],
        dn: str | None,
        planned_uid_by_dn: dict[str, str],
    ) -> str:
        base_uid = employee.username
        uid_matches = existing.by_uid.get(base_uid, [])
        if not uid_matches and base_uid not in claimed_uids:
            return base_uid
        if dn is not None and uid_matches == [dn] and base_uid not in claimed_uids:
            return base_uid
        if base_uid not in claimed_uids and self._uid_will_be_released(base_uid, uid_matches, dn, planned_uid_by_dn):
            return base_uid

        suffix = employee.id[:8]
        candidate = f"{base_uid}-{suffix}"
        counter = 2
        while candidate in claimed_uids or existing.by_uid.get(candidate):
            candidate = f"{base_uid}-{suffix}-{counter}"
            counter += 1
        return candidate

    def _uid_will_be_released(
        self,
        uid: str,
        uid_matches: list[str],
        dn: str | None,
        planned_uid_by_dn: dict[str, str],
    ) -> bool:
        if not uid_matches:
            return True

        for owner_dn in uid_matches:
            if dn is not None and owner_dn == dn:
                continue
            if planned_uid_by_dn.get(owner_dn) == uid:
                return False
            if owner_dn not in planned_uid_by_dn:
                return False

        return True
