# 服务器部署与运维

## 边界

这是独立生产配置，不与 `docker-compose.yml` 叠加。默认仅 Caddy 暴露 80/443；应用端口 8765 不发布到宿主机。域名白名单、Origin 检查和随机代理令牌在后端再次校验；不信任客户端自报的 Forwarded 头。Caddy 对所有页面、报告、下载及 API 要求 HTTP Basic 登录，密码只保存 bcrypt 哈希，公网只能通过 HTTPS 使用。

当前为单团队共享工作台：所有获准登录的人共享历史报告、搜索额度、复查任务与操作权限。不是多租户系统，不提供每用户数据隔离、角色权限、MFA、登录限速或完整操作审计。不要当作无限制开放平台；若面向公众，需要另外加账号、额度与限流。本版本按单后端实例运行（SQLite 和进程内调度/并发锁），不要横向扩容副本。

## 所需信息

- 服务器公网 IP、SSH 地址/端口/用户名、已有 SSH 密钥的本机路径或连接别名、是否有 sudo/Docker 权限；不要在聊天中粘贴私钥或密码。
- Linux 发行版、CPU 架构、可用内存和磁盘，是否已装 Docker Engine / Compose v2；建议从 2 核、4 GB 内存起按真实负载验收。
- 完整域名、DNS 管理权限、A/AAAA 指向。没有可用 IPv6 就不要保留错误 AAAA。
- 80/443 是否被现有 Nginx/Caddy/网站占用，云安全组/防火墙规则，是否经过 CDN 或内网穿透。有现成代理时不能直接覆盖，需另行适配。
- 证书通知邮箱；服务器需可访问证书颁发机构、元宝 SearchPro、DeepSeek，以及首次构建所需镜像/软件源。
- 允许访问的人员范围、是否需 VPN/IP 白名单、单团队共享数据是否可接受。
- 元宝和 DeepSeek 密钥在服务器上的安全文件位置，登录账号及密码通过服务器终端配置。
- 是否迁移现有报告、复查清单和提醒，备份目的地/保留周期，安装目录，是否需要启用每日复查。

## 首次部署

1. 将代码放在服务器独立目录。不要直接传含 `.env`、CSV 密钥、`.backups` 和整个 `output` 的目录归档；仅传源文件及所需部署文件。API 密钥单独安全配置。
2. 从根目录执行：

```sh
cp .env.server.example .env.server
chmod 600 .env.server
# 交互式输入密码，终端生成 bcrypt 哈希；不要把明文密码写入命令参数/历史。
docker run --rm -it caddy:2.10.2-alpine caddy hash-password
python3 -c 'import secrets; print(secrets.token_hex(32))'
```

3. 编辑 `.env.server`，填写真实域名（无协议与路径）、邮箱、用户名、密码哈希、代理令牌及 API 凭据。密码哈希必须用单引号包围以保留 `$`。使用强随机登录密码；代理令牌是另一份至少 32 字符的随机值。不要使用示例占位符。不要在共享终端输出展开后的 Compose 配置。
4. DNS A/AAAA 正确指向服务器，在云安全组与宿主防火墙允许 TCP 80/443；SSH 仅允许管理来源。8765 不应开放。启动：

```sh
docker compose --env-file .env.server -f compose.server.yml config --quiet
docker compose --env-file .env.server -f compose.server.yml up -d --build --wait --wait-timeout 90
docker compose --env-file .env.server -f compose.server.yml ps
```

5. 打开 `https://你的域名`，输入浏览器提示的账号密码。Caddy 自动申请和续期证书；域名/DNS/端口不满足时不会变成可用 HTTPS。Basic 登录没有应用内退出按钮，退出请关闭该浏览器会话/使用独立隐私窗口；改密码需更新哈希并重建 Caddy 容器。

配置依据：[Caddy HTTPS](https://caddyserver.com/docs/automatic-https)、[Caddy basic_auth](https://caddyserver.com/docs/caddyfile/directives/basic_auth)。生产 Caddy 固定在 2.10.2；上线前按维护策略检查安全更新并重新验证，未声明这是最新版本。

## 上线验收（必须在目标服务器执行）

- HTTP 跳转 HTTPS；证书链/域名/有效期正常，不能使用 `curl -k` 当作验收。
- 未登录访问 `/`、`/api/status`、历史报告及下载均返回 401；错误密码仍为 401。
- 登录后页面、历史、OCR 正常；运行一份真实报告并核对元宝请求、DeepSeek 评分与可点击来源。
- 外部无法访问 8765；跨站 Origin 和伪造代理头不能绕过认证；不要把代理令牌暴露给前端。
- 备份、容器重建后报告仍在；另建隔离环境做恢复演练。
- 用户明确开启后复查才运行；网页关闭时服务端仍保存提醒，桌面通知需要页面打开且获授权。
- 图片上传到服务器临时目录识别，处理后删除，不调用第三方 OCR。这不同于原来的本机隐私边界。

## 数据与备份

固定 Compose 项目名 `company-detective-server`。命名卷：`company-detective-server_reports`（SQLite）、`company-detective-server_caddy_data`（证书/私钥）、`company-detective-server_caddy_config`。容器重建不丢卷；严禁 `down -v` 或无差别卷清理。

```sh
./deploy/backup.sh
```

脚本以 SQLite backup API 在线生成一致快照并做完整性检查，保存到 `deploy/backups`，权限受 umask 077 保护。不要直接复制运行中的单个 SQLite 文件忽略 WAL。备份含报告与任务，需加密异机保存；同机命名卷不是备份。证书卷及 `.env.server` 需另行做安全备份，不要把证书私钥或 API 密钥放进公开交付包。当前不自动删除旧备份或安排备份任务，需按确认的保留策略配置。

恢复/迁移：先保留当前备份，停止服务器 Caddy 与后端，使用独立临时容器将选定快照以 SQLite backup API 写入 reports 卷；校验 `PRAGMA integrity_check` 后再启动。目标已经有数据时不得直接覆盖。恢复会带回复查任务，需在启动前确认/禁用不希望立即到期的计划。服务器未提供前不执行数据迁移或恢复。

## 更新与回滚

生产环境不挂载源代码，不会因文件变化直接热重启。上传代码不等于已发布；使用镜像构建更新，正在执行的调查会等待完成（最多 360 秒）。

```sh
./deploy/backup.sh
docker image tag company-detective:server company-detective:rollback
docker compose --env-file .env.server -f compose.server.yml up -d --build --wait --wait-timeout 90
```

更新仅 `.env.server` 时使用 `up -d --force-recreate --wait`。若回滚代码，先确认无数据库结构不兼容，再将 rollback 镜像重新标记为 server，运行 `up -d --no-build --force-recreate --wait`。数据库结构变更不能只回滚镜像，必须评估备份恢复。先在测试时段做更新，当前单实例部署不是零停机方案。

停止服务用 `docker compose --env-file .env.server -f compose.server.yml down`，不加 `-v`。Docker 引擎应由系统启动；`unless-stopped` 不会替你重启被手动停止的容器。

## 本地隔离验收脚本

先 `docker build -t company-detective:server .`，再 `python3 tools/verify_server_deployment.py`。需空闲的本机 18080/18443 端口。脚本创建随机名称的独立 Compose 项目、临时登录凭据与空数据库，使用内部测试 CA（校验证书和主机名，不跳过 TLS），验证未登录/错误密码拦截、域名访问、跨站拒绝、后端令牌防绕过、容器重建数据保留及在线备份完整性；完成后只清理自身创建的测试卷。不会读取真实 API 密钥或调用外部公司搜索。此验证不能替代目标服务器公网证书签发与真实查询验收。
