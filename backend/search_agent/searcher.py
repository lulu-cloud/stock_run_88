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

BAD_CACHE_PATTERNS = (
    "搜索限制说明",
    "当前网络搜索服务已达到使用限额",
    "无法获取",
    "未检索到明确信息",
    "未检索到明确重大变化",
    "暂无公司业务缓存",
)


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


def is_bad_business_cache(content: str) -> bool:
    """Return True when a generated MD is a quota/error placeholder, not usable research."""
    text = str(content or "").strip()
    if not text:
        return True
    if len(text) < 120:
        return True
    hit_count = sum(1 for pattern in BAD_CACHE_PATTERNS if pattern in text)
    if "搜索限制" in text or "使用限额" in text:
        return True
    return hit_count >= 2


def _cache_candidates(ts_code: str) -> list[dict]:
    if not os.path.exists(COMPANY_BUSINESS_DIR):
        return []
    candidates = []
    for f in os.listdir(COMPANY_BUSINESS_DIR):
        if not (f.startswith(ts_code) and f.endswith(".md")):
            continue
        date_match = re.search(r"(\d{8})", f)
        d = date_match.group(1) if date_match else "00000000"
        filepath = os.path.join(COMPANY_BUSINESS_DIR, f)
        candidates.append({"filename": f, "filepath": filepath, "date_raw": d})
    candidates.sort(key=lambda x: x["date_raw"], reverse=True)
    return candidates


def _read_file(path: str) -> str:
    with open(path, "r", encoding="utf-8") as f:
        return f.read()


def _freshness_from_candidate(item: dict, is_bad: bool = False) -> dict:
    filepath = item["filepath"]
    best_date = item.get("date_raw") or "00000000"
    try:
        file_date = datetime.strptime(best_date, "%Y%m%d")
        age_days = (datetime.now() - file_date).days
        return {
            "filepath": filepath,
            "date": file_date.strftime("%Y-%m-%d"),
            "age_days": age_days,
            "is_fresh": age_days <= CACHE_FRESH_DAYS and not is_bad,
            "is_bad": bool(is_bad),
        }
    except ValueError:
        return {"filepath": filepath, "date": best_date, "age_days": 999, "is_fresh": False, "is_bad": bool(is_bad)}


def get_freshness(ts_code: str, include_bad: bool = False) -> Optional[dict]:
    """获取最新缓存的新鲜度信息

    Returns:
        {"filepath": str, "date": "YYYY-MM-DD", "age_days": int, "is_fresh": bool, "is_bad": bool}
        或 None（无缓存）
    """
    bad_fallback = None
    for item in _cache_candidates(ts_code):
        try:
            is_bad = is_bad_business_cache(_read_file(item["filepath"]))
        except Exception:
            is_bad = True
        if is_bad:
            bad_fallback = bad_fallback or item
            if include_bad:
                return _freshness_from_candidate(item, True)
            continue
        return _freshness_from_candidate(item, False)
    if bad_fallback:
        return _freshness_from_candidate(bad_fallback, True)
    return None


def save_with_date(ts_code: str, name: str, business_md: str, sectors: list[str], keywords: list[str]) -> str:
    """保存公司业务 MD 文件（带日期戳，不覆盖旧文件）"""
    ensure_dir()
    if is_bad_business_cache(business_md):
        raise ValueError("公司业务内容疑似搜索限额/失败占位，拒绝写入缓存")
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


def get_cached(ts_code: str, include_bad: bool = False) -> Optional[str]:
    """获取最新的缓存 MD 内容（按日期戳取最新版本）"""
    for item in _cache_candidates(ts_code):
        content = _read_file(item["filepath"])
        if include_bad or not is_bad_business_cache(content):
            return content
    return None


def refresh_company_business_cache(ts_code: str, name: str = "") -> dict:
    """Run MiniMax search and save a reliable company-business MD cache."""
    from backend.search_agent.minimax_search import company_business_search
    from backend.search_agent.sector import extract_keywords, match_sectors_from_text

    result = company_business_search(ts_code, name)
    if not result.get("ok"):
        return {"ok": False, "error": result.get("error", "MiniMax搜索失败"), "raw": result}
    content = result.get("content") or ""
    if is_bad_business_cache(content):
        return {"ok": False, "error": "MiniMax返回内容疑似搜索限额/失败占位", "raw": result}
    sectors = match_sectors_from_text(content)
    keywords = extract_keywords(content)
    try:
        filepath = save_with_date(ts_code, name, content, sectors, keywords)
    except Exception as exc:
        return {"ok": False, "error": str(exc), "raw": result}
    return {
        "ok": True,
        "content": content,
        "filepath": filepath,
        "freshness": get_freshness(ts_code),
        "source": "minimax",
    }


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
