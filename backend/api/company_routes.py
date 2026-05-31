"""公司业务搜索 API"""

import os
import re
from datetime import datetime
from fastapi import APIRouter
from pydantic import BaseModel
from backend.config import COMPANY_BUSINESS_DIR
from backend.search_agent.searcher import is_cached, get_cached, get_freshness

router = APIRouter(prefix="/api/company", tags=["company"])


class SearchRequest(BaseModel):
    ts_code: str
    name: str = ""


@router.get("/business/{ts_code}")
async def get_company_business_api(ts_code: str):
    """获取公司业务信息（最新缓存）"""
    content = get_cached(ts_code)
    if content:
        freshness = get_freshness(ts_code)
        return {
            "ts_code": ts_code,
            "content": content,
            "freshness": freshness,
            "cached": True,
        }
    return {"ts_code": ts_code, "content": "", "cached": False, "freshness": None}


@router.get("/business/{ts_code}/history")
async def list_business_history(ts_code: str):
    """列出某只股票的所有业务缓存版本"""
    os.makedirs(COMPANY_BUSINESS_DIR, exist_ok=True)
    versions = []
    for f in sorted(os.listdir(COMPANY_BUSINESS_DIR), reverse=True):
        if f.startswith(ts_code) and f.endswith(".md"):
            # Parse date from filename: ts_code_name_YYYYMMDD.md or ts_code_YYYYMMDD.md
            date_match = re.search(r"(\d{8})", f)
            date_str = date_match.group(1) if date_match else ""
            versions.append({
                "filename": f,
                "date": f"{date_str[:4]}-{date_str[4:6]}-{date_str[6:8]}" if date_str else "unknown",
            })
    return {"ts_code": ts_code, "versions": versions}


@router.post("/search")
async def search_company_business(req: SearchRequest):
    """搜索公司业务信息（MiniMax 联网搜索 + 保存为 MD）"""
    from backend.search_agent.searcher import save_with_date, get_cached, get_freshness
    from backend.search_agent.minimax_search import company_business_search
    from backend.search_agent.sector import match_sectors_from_text, extract_keywords

    # 检查是否有新鲜缓存
    freshness = get_freshness(req.ts_code)
    if freshness and freshness["is_fresh"]:
        content = get_cached(req.ts_code)
        return {
            "ts_code": req.ts_code,
            "content": content,
            "freshness": freshness,
            "cached": True,
            "source": "cache",
        }

    result = company_business_search(req.ts_code, req.name)
    if not result.get("ok"):
        return {"ts_code": req.ts_code, "error": result.get("error", "MiniMax搜索失败"), "cached": False, "source": "minimax"}

    llm_response = result["content"]
    sectors = match_sectors_from_text(llm_response)
    keywords = extract_keywords(llm_response)

    # 保存带日期戳的文件
    filepath = save_with_date(req.ts_code, req.name, llm_response, sectors, keywords)
    freshness = get_freshness(req.ts_code)

    return {
        "ts_code": req.ts_code,
        "content": llm_response,
        "freshness": freshness,
        "cached": False,
        "source": "minimax",
        "filepath": filepath,
    }


class SaveRequest(BaseModel):
    ts_code: str
    name: str
    content: str
    sectors: list[str] = []
    keywords: list[str] = []


@router.post("/save")
async def save_company_business_api(req: SaveRequest):
    """保存公司业务 MD（带日期戳）"""
    from backend.search_agent.searcher import save_with_date
    filepath = save_with_date(req.ts_code, req.name, req.content, req.sectors, req.keywords)
    return {"ts_code": req.ts_code, "filepath": filepath, "saved": True}
