# XRXS2LDAP

Sync organization and employee data from HR systems into OpenLDAP.

This project currently supports:

- Synchronizing departments and employees into an OpenLDAP directory
- A `dry-run` mode for previewing changes before writing
- Running once or as a long-running scheduler
- JSON sample data for local testing
- A Xinrenxinshi adapter implementation

Passwords are intentionally left untouched.

## How It Works

To avoid large DN churn when departments are renamed or employees move teams, the sync uses stable DNs:

- People: `uid=<username>,ou=people,<base_dn>`
- Departments: `ou=dept-<department_id>,ou=departments,<base_dn>`

Department names and employee attributes can change without forcing DN changes.

## Synced Attributes

Employee entries use `inetOrgPerson` and currently write:

- `uid`
- `cn`
- `sn`
- `givenName`
- `displayName`
- `mail`
- `title`
- `telephoneNumber`
- `employeeNumber`
- `departmentNumber`
- `employeeType`
- `manager`

Department entries use `organizationalUnit + extensibleObject` and currently write:

- `ou`
- `description`
- `businessCategory`

## Quick Start

1. Create a virtual environment and install the package:

```bash
python3 -m venv .venv
.venv/bin/pip install -e .
```

2. Copy the example environment file:

```bash
cp .env.example .env
```

3. Run a dry-run sync:

```bash
.venv/bin/xrxs2ldap --dry-run --once
```

4. Run a real sync after checking the output:

```bash
DRY_RUN=false .venv/bin/xrxs2ldap --once
```

## Configuration

See [.env.example](.env.example) for all supported settings.

Typical LDAP values look like this:

```dotenv
LDAP_URI=ldap://localhost:1389
LDAP_BASE_DN=dc=example,dc=com
LDAP_BIND_DN=cn=admin,dc=example,dc=com
LDAP_BIND_PASSWORD=change-me
LDAP_PEOPLE_OU=ou=people
LDAP_DEPARTMENTS_OU=ou=departments
```

To use the Xinrenxinshi adapter:

```dotenv
HR_SOURCE=xinrenxinshi
XRXS_BASE_URL=https://api.xinrenxinshi.com
XRXS_APP_ID=
XRXS_APP_SECRET=
XRXS_COMPANY_ID=
```

## Docker

A sample Compose file is included in [docker-compose.sync.example.yml](docker-compose.sync.example.yml).

Basic flow:

1. Copy `.env.example` to `.env`
2. Update the LDAP and HR settings
3. Run a preview:

```bash
docker compose run --rm -e DRY_RUN=true xrxs2ldap xrxs2ldap --dry-run --once
```

4. Start the long-running sync service:

```bash
docker compose up -d xrxs2ldap
```

By default the scheduler runs once immediately and then sleeps for `SYNC_INTERVAL_SECONDS`.

Logs default to timezone `+08:00` and the Docker examples set `TZ=Asia/Shanghai`.

## Helper Scripts

The `deploy/` directory contains a few convenience helpers:

- [deploy/run_sync.sh](deploy/run_sync.sh)
- [deploy/run_sync_dry.sh](deploy/run_sync_dry.sh)
- [deploy/crontab.example](deploy/crontab.example)

## Sample Data

The repository includes `samples/hr_data.json` so you can test the LDAP flow locally before connecting to a live HR system.

## Xinrenxinshi Adapter

The adapter implementation lives in `src/xrxs2ldap/adapters/xinrenxinshi.py`.

## Notes

- `userPassword` is not overwritten
- Missing employees can be marked inactive instead of deleted
- Existing LDAP disambiguation such as `cn=Name(Dept)` is preserved for duplicate names

## License

MIT. See [LICENSE](LICENSE).
