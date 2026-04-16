from __future__ import annotations

import os
from dataclasses import dataclass


def _env(name: str, default: str | None = None) -> str | None:
    value = os.getenv(name)
    if value is None or value == "":
        return default
    return value


def _bool_env(name: str, default: bool = False) -> bool:
    value = _env(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "y", "on"}


def _int_env(name: str, default: int) -> int:
    value = _env(name)
    if value is None:
        return default
    return int(value)


@dataclass(slots=True)
class Settings:
    hr_source: str
    ldap_uri: str
    ldap_bind_dn: str
    ldap_bind_password: str
    ldap_base_dn: str
    people_ou: str
    departments_ou: str
    dry_run: bool
    archive_missing: bool
    sync_interval_seconds: int
    run_once: bool
    json_file_path: str | None
    xrxs_base_url: str | None
    xrxs_token_url: str | None
    xrxs_departments_url: str | None
    xrxs_employees_url: str | None
    xrxs_app_id: str | None
    xrxs_app_secret: str | None
    xrxs_company_id: str | None

    @property
    def people_base_dn(self) -> str:
        return f"{self.people_ou},{self.ldap_base_dn}"

    @property
    def departments_base_dn(self) -> str:
        return f"{self.departments_ou},{self.ldap_base_dn}"


def load_settings() -> Settings:
    base_dn = _env("LDAP_BASE_DN", "dc=chencytech,dc=com")
    return Settings(
        hr_source=_env("HR_SOURCE", "json_file") or "json_file",
        ldap_uri=_env("LDAP_URI", "ldap://localhost:1389") or "ldap://localhost:1389",
        ldap_bind_dn=_env("LDAP_BIND_DN", f"cn=admin,{base_dn}") or f"cn=admin,{base_dn}",
        ldap_bind_password=_env("LDAP_BIND_PASSWORD", "") or "",
        ldap_base_dn=base_dn,
        people_ou=_env("LDAP_PEOPLE_OU", "ou=people") or "ou=people",
        departments_ou=_env("LDAP_DEPARTMENTS_OU", "ou=departments") or "ou=departments",
        dry_run=_bool_env("DRY_RUN", True),
        archive_missing=_bool_env("ARCHIVE_MISSING_USERS", False),
        sync_interval_seconds=_int_env("SYNC_INTERVAL_SECONDS", 3600),
        run_once=_bool_env("RUN_ONCE", False),
        json_file_path=_env("JSON_FILE_PATH", "samples/hr_data.json"),
        xrxs_base_url=_env("XRXS_BASE_URL", "https://api.xinrenxinshi.com"),
        xrxs_token_url=_env("XRXS_TOKEN_URL"),
        xrxs_departments_url=_env("XRXS_DEPARTMENTS_URL"),
        xrxs_employees_url=_env("XRXS_EMPLOYEES_URL"),
        xrxs_app_id=_env("XRXS_APP_ID"),
        xrxs_app_secret=_env("XRXS_APP_SECRET"),
        xrxs_company_id=_env("XRXS_COMPANY_ID"),
    )
