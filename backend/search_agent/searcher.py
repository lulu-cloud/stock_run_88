"""公司业务搜索 — 管理与缓存

搜索逻辑依赖 WebSearch 工具，此模块负责：
1. 管理搜索队列（哪些股票需要搜索）
2. 检查缓存（MD 文件是否已存在，是否新鲜）
3. 保存搜索结果到本地 MD 文件（带日期戳）
4. 解析板块信息
"""

import os
import re
import time
from datetime import datetime
from typing import Optional
from backend.config import COMPANY_BUSINESS_DIR

# 缓存有效期（天）
CACHE_FRESH_DAYS = 30


def ensure_dir():
    """确保公司业务目录存在"""
    os.makedirs(COMPANY_BUSINESS_DIR, exist_ok=True)


def is_cached(ts_code: str) -> bool:
    """检查是否已有缓存（任意版本）"""
    if not os.path.exists(COMPANY_BUSINESS_DIR):
        return False
    for f in os.listdir(COMPANY_BUSINESS_DIR):
        if f.startswith(ts_code) and f.endswith(".md"):
            return True
    return False


def get_freshness(ts_code: str) -> Optional[dict]:
    """获取最新缓存的新鲜度信息

    Returns:
        {"filepath": str, "date": "YYYY-MM-DD", "age_days": int, "is_fresh": bool}
        或 None（无缓存）
    """
    if not os.path.exists(COMPANY_BUSINESS_DIR):
        return None
    best = None
    best_date = ""
    for f in os.listdir(COMPANY_BUSINESS_DIR):
        if f.startswith(ts_code) and f.endswith(".md"):
            date_match = re.search(r"(\d{8})", f)
            if date_match:
                d = date_match.group(1)
                if d > best_date:
                    best_date = d
                    best = f
    if not best:
        return None
    filepath = os.path.join(COMPANY_BUSINESS_DIR, best)
    try:
        file_date = datetime.strptime(best_date, "%Y%m%d")
        age_days = (datetime.now() - file_date).days
        return {
            "filepath": filepath,
            "date": file_date.strftime("%Y-%m-%d"),
            "age_days": age_days,
            "is_fresh": age_days <= CACHE_FRESH_DAYS,
        }
    except ValueError:
        return {"filepath": filepath, "date": best_date, "age_days": 999, "is_fresh": False}


def save_with_date(ts_code: str, name: str, business_md: str, sectors: list[str], keywords: list[str]) -> str:
    """保存公司业务 MD 文件（带日期戳，不覆盖旧文件）"""
    ensure_dir()
    datestamp = datetime.now().strftime("%Y%m%d")
    safe_name = re.sub(r"[^一-龥a-zA-Z0-9]", "_", name)[:30]
    filename = f"{ts_code}_{safe_name}_{datestamp}.md"
    filepath = os.path.join(COMPANY_BUSINESS_DIR, filename)

    sector_lines = "\n".join(f"- {s}" for s in sectors) if sectors else "- 暂无"
    kw_str = ", ".join(keywords) if keywords else "暂无"

    md_content = f"""# {name} ({ts_code})

> 更新时间: {datetime.now().strftime('%Y-%m-%d %H:%M')}

## 主营业务
{business_md}

## 所属板块
{sector_lines}

## 关键词
{kw_str}
"""
    with open(filepath, "w", encoding="utf-8") as f:
        f.write(md_content)
    return filepath


def save_company_business(ts_code: str, name: str, business_md: str, sectors: list[str], keywords: list[str]):
    """保存公司业务 MD 文件（兼容旧接口，内部调用 save_with_date）"""
    save_with_date(ts_code, name, business_md, sectors, keywords)


def get_cached(ts_code: str) -> Optional[str]:
    """获取最新的缓存 MD 内容（按日期戳取最新版本）"""
    if not os.path.exists(COMPANY_BUSINESS_DIR):
        return None
    best = None
    best_date = ""
    for f in os.listdir(COMPANY_BUSINESS_DIR):
        if f.startswith(ts_code) and f.endswith(".md"):
            date_match = re.search(r"(\d{8})", f)
            d = date_match.group(1) if date_match else "00000000"
            if d > best_date:
                best_date = d
                best = f
    if not best:
        return None
    filepath = os.path.join(COMPANY_BUSINESS_DIR, best)
    with open(filepath, "r", encoding="utf-8") as f:
        return f.read()


def list_cached() -> list[str]:
    """列出已缓存的公司"""
    ensure_dir()
    files = [f.replace(".md", "") for f in os.listdir(COMPANY_BUSINESS_DIR) if f.endswith(".md")]
    return sorted(files)


def get_search_queue(stock_list: list[dict], max_new: int = 50) -> list[dict]:
    """获取需要搜索的股票队列（未缓存的）

    Args:
        stock_list: [{"ts_code": ..., "name": ...}, ...]
        max_new: 最多返回多少个未缓存的

    Returns:
        需要搜索的股票列表
    """
    ensure_dir()
    queue = []
    for s in stock_list:
        ts_code = s["ts_code"]
        if not is_cached(ts_code):
            queue.append(s)
            if len(queue) >= max_new:
                break
    return queue


SEARCH_PROMPT = """帮我搜索关于{name}（{ts_code}）这家A股上市公司，主要了解：
1. 它的具体主营业务，是做什么的
2. 它属于哪些行业/板块
3. 最近有什么重要的业务变化或新闻

联网搜索最新的消息，然后以md的形式返回给我。重点描述它的业务。"""
