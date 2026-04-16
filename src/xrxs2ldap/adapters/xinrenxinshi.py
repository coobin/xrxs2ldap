from __future__ import annotations

import base64
import hmac
import json
import time
from hashlib import sha1
from urllib.parse import quote_plus

import requests

from xrxs2ldap.adapters.base import HrAdapter
from xrxs2ldap.config import Settings
from xrxs2ldap.models import Department, Employee, HrSnapshot


class XinrenxinshiAdapter(HrAdapter):
    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.session = requests.Session()
        self.token: str | None = None
        self.token_expires_at: float = 0.0

    def fetch_snapshot(self) -> HrSnapshot:
        departments = self._fetch_departments()
        employees = self._fetch_employees()
        return HrSnapshot(departments=departments, employees=employees)

    def _fetch_departments(self) -> list[Department]:
        payload = {"timestamp": self._timestamp_ms()}
        response = self._post_json("/v5/department/list", payload)
        return [self._map_department(item) for item in response]

    def _fetch_employees(self) -> list[Employee]:
        employees: list[Employee] = []
        seen_ids: set[str] = set()

        page_no = 0
        while True:
            payload = {
                "pageNo": page_no,
                "pageSize": 100,
                "fetchChild": 1,
                "status": 0,
                "timestamp": self._timestamp_ms(),
            }
            response = self._post_json("/v5/employee/list", payload)
            page = response if isinstance(response, dict) else {}
            result = page.get("result") or []
            for item in result:
                employee = self._map_employee(item)
                if employee.id in seen_ids:
                    continue
                seen_ids.add(employee.id)
                employees.append(employee)

            if not page.get("hasMore"):
                break
            page_no += 1

        return employees

    def _map_department(self, item: dict) -> Department:
        manager_employee_id = self._optional_str(item.get("adminId"))
        return Department(
            id=self._required_str(item.get("departmentId"), "departmentId"),
            name=self._required_str(item.get("name"), "department.name"),
            parent_id=self._optional_str(item.get("parentId")),
            manager_employee_id=manager_employee_id,
        )

    def _map_employee(self, item: dict) -> Employee:
        fields = item.get("fields") or {}
        employee_id = self._required_str(item.get("employeeId"), "employeeId")
        email = self._optional_str(item.get("email"))
        work_number = self._optional_str(fields.get("工号"))
        username = (
            self._username_from_email(email)
            or work_number
            or employee_id
        )
        title = self._guess_title(fields)
        phone = self._optional_str(fields.get("联系手机")) or self._optional_str(item.get("mobile"))

        return Employee(
            id=employee_id,
            username=username,
            display_name=self._required_str(item.get("name"), "employee.name"),
            email=email,
            department_id=self._optional_str(fields.get("部门")),
            title=title,
            manager_employee_id=self._optional_str(fields.get("汇报对象")),
            first_name=None,
            last_name=None,
            phone=phone,
            active=int(item.get("status", 0)) == 0,
        )

    def _guess_title(self, fields: dict) -> str | None:
        for key in ("岗位名称", "岗位", "职位", "职务", "职级"):
            value = self._optional_str(fields.get(key))
            if not value:
                continue
            if key == "岗位" and len(value) >= 24 and value.replace("-", "").isalnum():
                continue
            return value
        return None

    def _post_json(self, path: str, payload: dict) -> dict | list:
        token = self._get_access_token()
        body = json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
        sign = self._generate_signature(body)
        headers = {
            "access_token": token,
            "Content-Type": "application/json;charset=utf-8",
            "Accept": "application/json",
        }
        if self.settings.xrxs_company_id:
            headers["companyId"] = self.settings.xrxs_company_id

        response = self.session.post(
            f"{self._api_base_url()}{path}",
            params={"sign": sign},
            data=body.encode("utf-8"),
            headers=headers,
            timeout=30,
        )
        response.raise_for_status()
        payload = response.json()
        errcode = payload.get("errcode")
        if errcode != 0:
            raise RuntimeError(
                f"Xinrenxinshi API request failed for {path}: "
                f"errcode={errcode}, errmsg={payload.get('errmsg')}"
            )
        return payload.get("data") or {}

    def _get_access_token(self) -> str:
        now = time.time()
        if self.token and now < self.token_expires_at:
            return self.token

        if not self.settings.xrxs_app_id or not self.settings.xrxs_app_secret:
            raise RuntimeError(
                "XRXS_APP_ID and XRXS_APP_SECRET must be configured when "
                "HR_SOURCE=xinrenxinshi."
            )

        token_url = self.settings.xrxs_token_url or f"{self._api_base_url()}/authorize/oauth/token"
        response = self.session.post(
            token_url,
            headers={"Content-Type": "application/x-www-form-urlencoded;charset=utf-8"},
            data={
                "grant_type": "client_credentials",
                "client_id": self.settings.xrxs_app_id,
                "client_secret": self.settings.xrxs_app_secret,
            },
            timeout=30,
        )
        response.raise_for_status()
        payload = response.json()
        token = payload.get("access_token")
        expires_in = int(payload.get("expires_in", 0))
        if not token:
            raise RuntimeError(f"Failed to acquire Xinrenxinshi access token: {payload}")

        self.token = str(token)
        self.token_expires_at = now + max(expires_in - 300, 60)
        return self.token

    def _generate_signature(self, content: str) -> str:
        secret = self.settings.xrxs_app_secret
        if not secret:
            raise RuntimeError("XRXS_APP_SECRET must be configured for Xinrenxinshi signing.")
        digest = hmac.new(secret.encode("utf-8"), content.encode("utf-8"), sha1).digest()
        return quote_plus(base64.b64encode(digest).decode("ascii"))

    def _api_base_url(self) -> str:
        return (self.settings.xrxs_base_url or "https://api.xinrenxinshi.com").rstrip("/")

    def _timestamp_ms(self) -> int:
        return int(time.time() * 1000)

    def _optional_str(self, value: object) -> str | None:
        if value is None:
            return None
        text = str(value).strip()
        return text or None

    def _required_str(self, value: object, field_name: str) -> str:
        text = self._optional_str(value)
        if not text:
            raise RuntimeError(f"Missing required Xinrenxinshi field: {field_name}")
        return text

    def _username_from_email(self, email: str | None) -> str | None:
        if not email or "@" not in email:
            return None
        return email.split("@", 1)[0].strip() or None
