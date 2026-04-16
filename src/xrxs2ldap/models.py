from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(slots=True)
class Department:
    id: str
    name: str
    parent_id: str | None = None
    manager_employee_id: str | None = None


@dataclass(slots=True)
class Employee:
    id: str
    username: str
    display_name: str
    email: str | None = None
    department_id: str | None = None
    title: str | None = None
    manager_employee_id: str | None = None
    first_name: str | None = None
    last_name: str | None = None
    phone: str | None = None
    active: bool = True


@dataclass(slots=True)
class HrSnapshot:
    departments: list[Department] = field(default_factory=list)
    employees: list[Employee] = field(default_factory=list)
