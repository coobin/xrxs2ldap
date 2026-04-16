from __future__ import annotations

import json
from pathlib import Path

from xrxs2ldap.adapters.base import HrAdapter
from xrxs2ldap.models import Department, Employee, HrSnapshot


class JsonFileAdapter(HrAdapter):
    def __init__(self, file_path: str) -> None:
        self.file_path = Path(file_path)

    def fetch_snapshot(self) -> HrSnapshot:
        payload = json.loads(self.file_path.read_text(encoding="utf-8"))
        departments = [
            Department(
                id=str(item["id"]),
                name=item["name"],
                parent_id=str(item["parent_id"]) if item.get("parent_id") is not None else None,
                manager_employee_id=(
                    str(item["manager_employee_id"])
                    if item.get("manager_employee_id") is not None
                    else None
                ),
            )
            for item in payload.get("departments", [])
        ]
        employees = [
            Employee(
                id=str(item["id"]),
                username=item.get("username") or str(item["id"]),
                display_name=item["display_name"],
                email=item.get("email"),
                department_id=str(item["department_id"]) if item.get("department_id") is not None else None,
                title=item.get("title"),
                manager_employee_id=(
                    str(item["manager_employee_id"])
                    if item.get("manager_employee_id") is not None
                    else None
                ),
                first_name=item.get("first_name"),
                last_name=item.get("last_name"),
                phone=item.get("phone"),
                active=bool(item.get("active", True)),
            )
            for item in payload.get("employees", [])
        ]
        return HrSnapshot(departments=departments, employees=employees)
