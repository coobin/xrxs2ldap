# XRXS2LDAP

将 HR 系统中的组织架构和员工信息同步到 OpenLDAP。

当前项目支持：

- 同步员工到 OpenLDAP
- 同步部门为 LDAP 用户组，供 Authelia / OIDC 输出 `groups` claim
- `dry-run` 预览模式，写入前可先检查变更
- 单次运行或长期定时运行
- 本地 JSON 示例数据
- 薪人薪事适配器

同步过程不会写入或覆盖用户密码。

## 工作方式

为了避免部门改名、员工调部门时造成大量 DN 变化，同步使用稳定 DN：

- 用户：`uid=<username>,ou=people,<base_dn>`
- 部门组：`cn=<department_name>,cn=<parent_department>,ou=groups,<base_dn>`

部门名称和员工属性可以更新，但用户 DN 会尽量保持稳定。

部门组使用 `posixGroup`，按 HR 部门父子关系嵌套在 `ou=groups` 下，成员通过 `memberUid` 维护，值与员工 `uid` 一致。这样 Authelia 可以通过 LDAP 查询得到用户所属部门，并在 OIDC token 中输出 `groups`，Nextcloud 等服务即可同步部门组。

如果历史系统里已经存在旧组名，可以用 `LDAP_GROUP_NAME_ALIASES` 把新仁薪事部门名映射到旧组名，避免 Nextcloud 生成重复组。例如：

```env
LDAP_GROUP_NAME_ALIASES=人力资源部=行政人事部
```

如果同一个父部门下有多个部门名称重复，部门组名会追加部门 ID 前缀片段，例如：

```text
交付组-37092ea7
```

## 同步属性

用户条目使用 `inetOrgPerson`，当前写入：

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

部门组条目使用 `posixGroup`，当前写入：

- `cn`
- `gidNumber`
- `memberUid`

已有部门组只会更新 `memberUid`，不会覆盖已有 `gidNumber`。

## 快速开始

创建虚拟环境并安装：

```bash
python3 -m venv .venv
.venv/bin/pip install -e .
```

复制环境变量模板：

```bash
cp .env.example .env
```

先 dry-run 预览：

```bash
.venv/bin/xrxs2ldap --dry-run --once
```

确认输出后执行真实同步：

```bash
DRY_RUN=false .venv/bin/xrxs2ldap --once
```

## 配置

完整配置见 [.env.example](.env.example)。

常用 LDAP 配置示例：

```dotenv
LDAP_URI=ldap://localhost:1389
LDAP_BASE_DN=dc=example,dc=com
LDAP_BIND_DN=cn=admin,dc=example,dc=com
LDAP_BIND_PASSWORD=change-me
LDAP_PEOPLE_OU=ou=people
LDAP_GROUPS_OU=ou=groups
LDAP_GROUP_NAME_ALIASES=人力资源部=行政人事部
```

使用薪人薪事数据源：

```dotenv
HR_SOURCE=xinrenxinshi
XRXS_BASE_URL=https://api.xinrenxinshi.com
XRXS_APP_ID=
XRXS_APP_SECRET=
XRXS_COMPANY_ID=
```

## Docker

仓库包含示例 Compose 文件：[docker-compose.sync.example.yml](docker-compose.sync.example.yml)。

基本流程：

1. 复制 `.env.example` 为 `.env`
2. 修改 LDAP 和 HR 系统配置
3. 预览同步：

```bash
docker compose run --rm -e DRY_RUN=true xrxs2ldap xrxs2ldap --dry-run --once
```

4. 启动长期同步服务：

```bash
docker compose up -d xrxs2ldap
```

默认调度行为是启动后先同步一次，然后按 `SYNC_INTERVAL_SECONDS` 休眠。

Docker 示例默认使用 `Asia/Shanghai` 时区。

## Authelia 组同步

如果要让 Authelia 把部门组同步到 OIDC `groups` claim，可使用类似配置：

```yaml
authentication_backend:
  ldap:
    attributes:
      username: uid
      display_name: cn
      mail: mail
      group_name: cn
    groups_filter: '(&(objectClass=posixGroup)(memberUid={username}))'
```

Nextcloud 的 `user_oidc` 可将 `groups` claim 映射为 Nextcloud 组。

## Nextcloud 组同步

如果不希望 Nextcloud 自动按 OIDC `groups` claim 创建部门组，可以关闭 `user_oidc` 的组自动同步，然后在 Nextcloud 服务器本机运行：

```bash
xrxs2ldap-nextcloud-groups --dry-run
DRY_RUN=false xrxs2ldap-nextcloud-groups
```

这个命令会：

- 把所有 HR active 用户加入默认组 `ALL`
- 用 HR 部门名匹配 Nextcloud 组的界面显示名 `displayname`
- 只在匹配到唯一现有组时把用户加入该部门组
- 找不到同名显示组时不创建新组，用户只保留在 `ALL`
- 从它管理的部门组里移除用户的旧部门成员关系

它不会按 Nextcloud 内部组 ID 匹配部门名，但最终加组仍会使用匹配到的内部 `gid`，因为 Nextcloud 权限系统使用 `gid`。

相关配置：

```dotenv
NEXTCLOUD_APP_CONTAINER=nextcloud-app-1
NEXTCLOUD_DB_CONTAINER=nextcloud-db-1
NEXTCLOUD_DB_HOST=db
NEXTCLOUD_DB_PORT=3306
NEXTCLOUD_DB_NAME=nextcloud
NEXTCLOUD_DB_USER=nextcloud
NEXTCLOUD_DB_PASSWORD=
NEXTCLOUD_DEFAULT_GROUP=ALL
```

如果 `NEXTCLOUD_DB_HOST` 为空，命令会通过宿主机 Docker CLI 调用 Nextcloud 容器和数据库容器；如果设置了 `NEXTCLOUD_DB_HOST`，命令会直接连接 Nextcloud 数据库，适合放在 Nextcloud 的 Docker 网络里运行。

## 辅助脚本

`deploy/` 目录包含几个运行脚本：

- [deploy/run_sync.sh](deploy/run_sync.sh)
- [deploy/run_sync_dry.sh](deploy/run_sync_dry.sh)
- [deploy/crontab.example](deploy/crontab.example)

## 示例数据

仓库包含 `samples/hr_data.json`，可在连接真实 HR 系统前先本地测试 LDAP 同步流程。

## 薪人薪事适配器

适配器实现位于：

```text
src/xrxs2ldap/adapters/xinrenxinshi.py
```

## 注意事项

- 不会覆盖 `userPassword`
- 缺失员工可以标记为 inactive，而不是删除
- 已存在的 LDAP 去重名称会保留，例如 `cn=李珊(销管)`
- 部门组的成员只包含 active 员工

## License

MIT，见 [LICENSE](LICENSE)。
