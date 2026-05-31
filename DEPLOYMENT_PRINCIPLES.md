# stock_run_88 部署原理说明

本文解释当前项目为什么这样部署、哪些数据放在哪里、自动发布如何工作。

## 1. 部署目标

当前系统是单用户投研/模拟交易系统，核心诉求是：

- 本地继续开发，代码合并到 GitHub `main` 后自动发布到云服务器。
- 服务器长期运行后端、前端、Telegram polling、Agent 调度、政策抓取、股票池刷新。
- 运行数据保持稳定，不因为每次发版被覆盖。
- API key、Telegram token 不进 GitHub。

## 2. 服务器上的目录结构

线上应用目录是：

```text
/opt/stock_run_88
├── backend/              # Python 后端源码
├── frontend/             # Vue 前端源码和 dist
├── data/                 # 行情 CSV、SQLite 数据库，运行态数据
├── agent_memory/         # Agent 记忆
├── reports/              # 每日复盘报告
├── logs/                 # 运行日志
├── .venv/                # Python 虚拟环境
└── .env                  # 服务器密钥和运行配置
```

其中 `data/`、`agent_memory/`、`reports/`、`logs/`、`.env`、`.venv` 是服务器运行态，不随 GitHub 发版覆盖。

## 3. GitHub 管什么

GitHub 只管理：

- 后端源码。
- 前端源码。
- 脚本。
- 文档。
- 空目录占位文件。
- GitHub Actions workflow。

GitHub 不管理：

- `data/stock_run.db`
- `data/daily/*.csv`
- `logs/`
- `reports/`
- `agent_memory/`
- `.env`
- 私钥、API key、Telegram token

原因是这些文件要么很大，要么频繁变化，要么包含敏感信息。实时 DB 放进 Git 会导致历史泄露、二进制冲突、本地和服务器数据分叉。

## 4. 首次迁移数据

首次部署时，本地运行数据被打成压缩包上传到服务器：

```text
stock_run_88_runtime_YYYYMMDD_HHMMSS.tar.gz
```

压缩包包含：

- `data/`
- `agent_memory/`
- `reports/`
- `logs/`

SQLite DB 使用 SQLite backup API 复制，不是直接拷贝正在写入的 DB 文件，这样能避免 DB 半写入状态。

服务器当前已经有完整运行数据：

- `data/` 约 1GB。
- 日线 CSV 约 3400 多个。
- `stock_run.db` 已恢复。

## 5. 密钥放在哪里

服务器真实运行使用：

```text
/opt/stock_run_88/.env
```

里面配置：

```text
DEEPSEEK_API_KEY=...
MINIMAX_API_KEY=...
TELEGRAM_BOT_TOKEN=...
TELEGRAM_API_BASE=https://api.telegram.org
AGENT_MAX_CONCURRENCY=1
```

当前服务器已经配置好这些 key，所以不需要再把 AI key 放进 GitHub Actions Secrets。

GitHub Actions Secrets 只需要部署用 SSH 信息：

```text
SERVER_HOST=服务器 IP
SERVER_USER=ubuntu
SERVER_SSH_KEY=服务器登录私钥
SERVER_PORT=22
```

除非以后你想让 GitHub Actions 每次发版都重写服务器 `.env`，否则 AI key 不应该放在 GitHub Secrets 里。当前 workflow 明确保留服务器 `.env`，不会覆盖它。

## 6. 自动部署怎么工作

当前 workflow 文件：

```text
.github/workflows/deploy.yml
```

触发条件：

- push 到 `main`。
- 手动运行 workflow。

流程：

1. GitHub Actions checkout 当前源码。
2. 用 `scp` 上传源码到服务器 `/tmp/stock_run_88_release`。
3. 用 SSH 登录服务器。
4. 把源码同步到 `/opt/stock_run_88`。
5. 同步时排除运行态目录：

```text
.env
.venv
/data
/logs
/reports
/agent_memory
frontend/node_modules
frontend/dist
```

6. 安装 Python 依赖：

```bash
. .venv/bin/activate
pip install -r requirements.txt
```

7. 构建前端：

```bash
cd frontend
npm ci
npm run build
```

8. 重启后端：

```bash
sudo systemctl restart stock-run-api
```

这个方案不要求服务器能访问私有 GitHub 仓库，也不要求服务器保存 GitHub token。

## 7. systemd 和 Nginx 分工

后端由 systemd 托管：

```text
stock-run-api.service
```

它运行：

```bash
/opt/stock_run_88/.venv/bin/uvicorn backend.main:app --host 127.0.0.1 --port 8000
```

Nginx 监听公网 80 端口：

- `/` 返回前端 `frontend/dist`。
- `/api/` 反向代理到 `127.0.0.1:8000/api/`。

所以浏览器访问：

```text
http://服务器IP/
```

API 访问：

```text
http://服务器IP/api/health
```

## 8. 后台自动任务

后端启动后会自动开启几个后台线程：

### Agent 调度

每 5 分钟检查一次：

```text
/api/scheduler/status
```

它会读取 DB 中每个 Agent 的调度配置，到时间后运行复盘/推送。

### 行情增量更新

Agent 调度每次运行时都会调用行情检查逻辑。默认逻辑是：

- `MARKET_DATA_FETCH_TIME` 默认 `18:00`。
- 18:00 后检查最近交易日数据。
- 如果数据不完整，启动后台线程增量拉取。
- 如果数据已完整，`market_data.running` 会是 `false`，表示当前没有拉取任务，而不是没开。

状态查看：

```text
/api/automation/status
```

### 股票列表每周刷新

股票基础列表刷新线程默认：

- 每 60 分钟检查一次。
- 每 7 天最多刷新一次。
- 默认 20:30 后执行。

状态查看：

```text
/api/stock-universe/status
```

### Telegram polling

服务器启动后会自动启动 Telegram long polling。

注意同一个 bot 只能有一个 long polling 实例。本地 WSL 如果也开着后端，就会和服务器抢 bot，Telegram 会返回 `409 Conflict`。上线后建议只让服务器运行 Telegram polling。

状态查看：

```text
/api/telegram/status
```

## 9. 常用运维命令

查看后端状态：

```bash
sudo systemctl status stock-run-api
```

查看后端日志：

```bash
journalctl -u stock-run-api -n 100 --no-pager
```

重启后端：

```bash
sudo systemctl restart stock-run-api
```

查看 Nginx 状态：

```bash
sudo systemctl status nginx
```

检查服务是否正常：

```bash
curl http://127.0.0.1/api/health
curl http://127.0.0.1/api/automation/status
curl http://127.0.0.1/api/telegram/status
```

## 10. 推荐的数据策略

上线后建议以服务器 DB 为主库。

本地继续开发代码，但不要让本地和服务器同时承担正式 Telegram polling 和正式 Agent 调度，否则会出现：

- Telegram bot 409 冲突。
- 两边各自写 DB，数据分叉。
- 两边都推送，消息重复。

正确方式：

- 服务器负责正式运行。
- 本地负责代码开发和临时调试。
- 本地调试时可关闭 Telegram polling 或使用测试 bot。
- 定期从服务器备份 DB，而不是把 DB 提交到 Git。

## 11. 为什么不直接 Docker

当前没有强制上 Docker，原因是：

- 项目是单机单用户，systemd + Nginx 足够稳定。
- SQLite、CSV、本地文件目录很多，直接映射 volume 也要设计。
- 现在更重要的是先把代码发布、数据保留、后台任务跑通。

后续如果需要迁移多台机器、加 CI 测试、做标准化交付，再上 Docker Compose 会更合适。
