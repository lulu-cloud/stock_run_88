# 云服务器部署说明

## 原则

- GitHub 只管理源码、文档和空目录占位，不管理 `data/stock_run.db`、行情 CSV、日志、报告、`.env`。
- 首次迁移用运行数据压缩包恢复 `data/`、`agent_memory/`、`reports/`、`logs/`。
- 服务器上线后，以服务器数据库作为主库；本地和服务器不要同时写同一个业务库。
- API key、Telegram token 只写在服务器 `.env`，不要提交到 Git。

## 首次部署

```bash
sudo apt update
sudo apt install -y git python3 python3-venv python3-pip nodejs npm nginx

cd /opt
sudo git clone https://github.com/lulu-cloud/stock_run_88.git
sudo chown -R "$USER:$USER" /opt/stock_run_88
cd /opt/stock_run_88

python3 -m venv .venv
. .venv/bin/activate
pip install -r requirements.txt

cd frontend
npm ci
npm run build
cd ..
```

把本地生成的运行数据包上传到服务器，例如：

```bash
scp -i /home/xulu/ssh.pem /home/xulu/stock_run_88_runtime_YYYYMMDD_HHMMSS.tar.gz root@SERVER_IP:/opt/
```

在服务器解压：

```bash
cd /opt
tar -xzf stock_run_88_runtime_YYYYMMDD_HHMMSS.tar.gz
rsync -a stock_run_88_runtime_YYYYMMDD_HHMMSS/ /opt/stock_run_88/
```

创建 `.env`：

```bash
cd /opt/stock_run_88
cp .env.example .env
nano .env
```

填入：

```bash
DEEPSEEK_API_KEY=你的新key
MINIMAX_API_KEY=你的新key
TELEGRAM_BOT_TOKEN=你的telegram_bot_token
TELEGRAM_API_BASE=https://api.telegram.org
AGENT_MAX_CONCURRENCY=1
```

## systemd 服务

后端服务 `/etc/systemd/system/stock-run-api.service`：

```ini
[Unit]
Description=stock_run_88 backend
After=network.target

[Service]
WorkingDirectory=/opt/stock_run_88
EnvironmentFile=/opt/stock_run_88/.env
ExecStart=/opt/stock_run_88/.venv/bin/uvicorn backend.main:app --host 127.0.0.1 --port 8000
Restart=always
RestartSec=5

[Install]
WantedBy=multi-user.target
```

启动：

```bash
sudo systemctl daemon-reload
sudo systemctl enable --now stock-run-api
sudo systemctl status stock-run-api
```

## Nginx

`/etc/nginx/sites-available/stock-run`：

```nginx
server {
    listen 80;
    server_name _;

    root /opt/stock_run_88/frontend/dist;
    index index.html;

    location /api/ {
        proxy_pass http://127.0.0.1:8000/api/;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
    }

    location / {
        try_files $uri $uri/ /index.html;
    }
}
```

启用：

```bash
sudo ln -sf /etc/nginx/sites-available/stock-run /etc/nginx/sites-enabled/stock-run
sudo nginx -t
sudo systemctl reload nginx
```

## 后续发版

```bash
cd /opt/stock_run_88
git pull --ff-only
. .venv/bin/activate
pip install -r requirements.txt
cd frontend
npm ci
npm run build
cd ..
sudo systemctl restart stock-run-api
```

如果要做“本地 push 后服务器自动部署”，推荐用 GitHub Actions SSH 到服务器执行上述发版命令。服务器需先能用 SSH key 登录。

## 数据备份

不要把实时 DB 放进 Git。推荐每天做 SQLite 一致性备份：

```bash
cd /opt/stock_run_88
python3 - <<'PY'
import datetime, pathlib, sqlite3
src = sqlite3.connect("data/stock_run.db")
pathlib.Path("backups").mkdir(exist_ok=True)
dst_path = "backups/stock_run_%s.db" % datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
dst = sqlite3.connect(dst_path)
src.backup(dst)
dst.close()
src.close()
print(dst_path)
PY
```
