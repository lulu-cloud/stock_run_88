"""应用配置管理"""

import os

# 项目根目录
ROOT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _load_dotenv():
    """Load simple KEY=VALUE pairs from project .env if process env lacks them."""
    env_path = os.path.join(ROOT_DIR, ".env")
    if not os.path.exists(env_path):
        return
    with open(env_path, "r", encoding="utf-8") as f:
        for line in f:
            text = line.strip()
            if not text or text.startswith("#") or "=" not in text:
                continue
            key, value = text.split("=", 1)
            key = key.strip()
            value = value.strip().strip('"').strip("'")
            if key and key not in os.environ:
                os.environ[key] = value


_load_dotenv()

# 数据目录
DATA_DIR = os.path.join(ROOT_DIR, "data")
DAILY_DIR = os.path.join(DATA_DIR, "daily")
INDEX_DIR = os.path.join(DATA_DIR, "index")
COMPANY_BUSINESS_DIR = os.path.join(DATA_DIR, "company_business")

# 日志和报告
LOGS_DIR = os.path.join(ROOT_DIR, "logs")
REPORTS_DIR = os.path.join(ROOT_DIR, "reports")

# 数据库
DATABASE_PATH = os.path.join(ROOT_DIR, "data", "stock_run.db")

# LLM - DeepSeek v4 pro
DEEPSEEK_API_KEY = os.environ.get("DEEPSEEK_API_KEY", "")
DEEPSEEK_BASE_URL = "https://api.deepseek.com"
DEEPSEEK_MODEL = "deepseek-v4-pro"

# Minimax 搜索
MINIMAX_API_KEY = (
    os.environ.get("MINIMAX_CODE_PLAN_KEY")
    or os.environ.get("MINIMAX_CODING_API_KEY")
    or os.environ.get("MINIMAX_API_KEY", "")
)
MINIMAX_API_HOST = os.environ.get("MINIMAX_API_HOST", "https://api.minimaxi.com")
MINIMAX_SEARCH_ENDPOINT = os.environ.get("MINIMAX_SEARCH_ENDPOINT", "")
MINIMAX_MODEL = os.environ.get("MINIMAX_MODEL", "MiniMax-M2.7")

# 交易规则常量
INITIAL_CAPITAL = 150_000.0  # 初始本金
COMMISSION_RATE = 0.0000854  # 券商佣金 万0.854 双向
STAMP_TAX_RATE = 0.0005      # 印花税 万5 卖出单向
T1_ENABLED = True            # T+1 制度
VALID_STOCK_PREFIXES = ("60", "00")  # 仅主板

# 仓位约束
MAX_POSITION_COUNT = 5  # 最大持仓股票数

# LLM 推理深度
DEFAULT_REASONING_EFFORT = "high"  # "high" 或 "max"

# 调度与 Telegram
AGENT_MAX_CONCURRENCY = int(os.environ.get("AGENT_MAX_CONCURRENCY", "1"))
TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "")
TELEGRAM_API_BASE = os.environ.get("TELEGRAM_API_BASE", "https://api.telegram.org")

# 均线周期（日）
MA_PERIODS = [5, 10, 20, 60]
