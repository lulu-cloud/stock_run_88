"""MiniMax/Token Plan web search for company business research."""

import json
import os
import subprocess
import urllib.error
import urllib.request

from backend.config import MINIMAX_API_HOST, MINIMAX_API_KEY, MINIMAX_MODEL, MINIMAX_SEARCH_ENDPOINT


def web_search(query: str, timeout: int = 45) -> dict:
    """Search the web via MiniMax HTTP first, then MCP subprocess fallback."""
    http_result = _http_web_search(query, timeout)
    if http_result.get("ok"):
        return http_result
    mcp_result = _mcp_web_search(query, timeout)
    if mcp_result.get("ok"):
        mcp_result["fallback_from"] = http_result
        return mcp_result
    return {
        "ok": False,
        "query": query,
        "error": "MiniMax HTTP and MCP search both failed",
        "http_error": http_result.get("error"),
        "mcp_error": mcp_result.get("error"),
    }


def company_business_search(ts_code: str, name: str) -> dict:
    """Search company business facts and summarize them into the local MD template."""
    query = (
        f"A股 {name} {ts_code} 主营业务 最新业务变化 算力租赁 数据中心 "
        f"行业板块 公司公告 新闻"
    )
    result = web_search(query)
    if not result.get("ok"):
        return result
    text = _normalize_search_text(result)
    sources = _extract_sources(result.get("raw"))
    content = _summarize_company_business(ts_code, name, sources, text)
    return {
        "ok": True,
        "query": query,
        "summary": text[:2000],
        "sources": sources,
        "content": content,
        "raw": result,
    }


class MiniMaxCompanyResearchAgent:
    """Tiny two-step agent: web search first, MiniMax summarization second."""

    def run(self, ts_code: str, name: str) -> dict:
        return company_business_search(ts_code, name)


def _http_web_search(query: str, timeout: int) -> dict:
    if not MINIMAX_API_KEY:
        return {"ok": False, "error": "MINIMAX_API_KEY is not configured"}
    endpoints = []
    if MINIMAX_SEARCH_ENDPOINT:
        endpoints.append(MINIMAX_SEARCH_ENDPOINT)
    endpoints.extend([
        f"{MINIMAX_API_HOST.rstrip('/')}/v1/coding_plan/search",
    ])
    payload = json.dumps({"q": query, "count": 8}, ensure_ascii=False).encode("utf-8")
    headers = {
        "Content-Type": "application/json",
        "Authorization": f"Bearer {MINIMAX_API_KEY}",
    }
    last_error = ""
    for endpoint in endpoints:
        req = urllib.request.Request(endpoint, data=payload, headers=headers, method="POST")
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                data = json.loads(resp.read().decode("utf-8"))
                if _raw_contains_error(data):
                    last_error = f"{endpoint}: {data}"
                    continue
                return {"ok": True, "source": "minimax_http", "endpoint": endpoint, "query": query, "raw": data}
        except urllib.error.HTTPError as e:
            try:
                body = e.read().decode("utf-8")
            except Exception:
                body = str(e)
            last_error = f"{endpoint}: HTTP {e.code} {body[:500]}"
        except Exception as e:
            last_error = f"{endpoint}: {e}"
    return {"ok": False, "source": "minimax_http", "query": query, "error": last_error}


def _mcp_web_search(query: str, timeout: int) -> dict:
    if not MINIMAX_API_KEY:
        return {"ok": False, "error": "MINIMAX_API_KEY is not configured"}
    prompt = json.dumps({"tool": "web_search", "query": query}, ensure_ascii=False)
    cmd = ["uvx", "minimax-coding-plan-mcp", "-y"]
    try:
        env = os.environ.copy()
        env.update({"MINIMAX_API_KEY": MINIMAX_API_KEY, "MINIMAX_API_HOST": MINIMAX_API_HOST})
        proc = subprocess.run(
            cmd,
            input=prompt,
            text=True,
            capture_output=True,
            timeout=timeout,
            env=env,
        )
    except FileNotFoundError:
        return {"ok": False, "source": "minimax_mcp", "error": "uvx is not installed"}
    except Exception as e:
        return {"ok": False, "source": "minimax_mcp", "error": str(e)}
    if proc.returncode != 0:
        return {"ok": False, "source": "minimax_mcp", "error": proc.stderr[-1000:] or proc.stdout[-1000:]}
    if _raw_contains_error(proc.stdout):
        return {"ok": False, "source": "minimax_mcp", "error": proc.stdout[-1000:] or "MCP returned an error notification"}
    return {"ok": True, "source": "minimax_mcp", "query": query, "raw": proc.stdout}


def _minimax_chat(system_prompt: str, user_prompt: str, timeout: int = 90) -> dict:
    if not MINIMAX_API_KEY:
        return {"ok": False, "error": "MINIMAX_API_KEY is not configured"}
    endpoint = f"{MINIMAX_API_HOST.rstrip('/')}/v1/text/chatcompletion_v2"
    payload = {
        "model": MINIMAX_MODEL,
        "messages": [
            {"role": "system", "name": "MiniMax AI", "content": system_prompt},
            {"role": "user", "name": "user", "content": user_prompt},
        ],
        "temperature": 0.2,
        "max_tokens": 3200,
        "stream": False,
    }
    req = urllib.request.Request(
        endpoint,
        data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
        headers={"Content-Type": "application/json", "Authorization": f"Bearer {MINIMAX_API_KEY}"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            data = json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        try:
            body = e.read().decode("utf-8")
        except Exception:
            body = str(e)
        return {"ok": False, "error": f"HTTP {e.code} {body[:500]}"}
    except Exception as e:
        return {"ok": False, "error": str(e)}
    if _raw_contains_error(data):
        return {"ok": False, "error": json.dumps(data, ensure_ascii=False)[:1000]}
    try:
        content = data["choices"][0]["message"]["content"]
    except Exception:
        return {"ok": False, "error": f"unexpected response: {str(data)[:1000]}"}
    return {"ok": True, "content": _strip_reasoning(content)}


def _summarize_company_business(ts_code: str, name: str, sources: list[dict], raw_text: str) -> str:
    source_lines = []
    for i, s in enumerate(sources[:8], 1):
        source_lines.append(
            f"[{i}] 标题: {s.get('title', '')}\n"
            f"URL: {s.get('url', '')}\n"
            f"日期: {s.get('date', '')}\n"
            f"摘要: {s.get('snippet', '')}"
        )
    source_block = "\n\n".join(source_lines) or raw_text[:5000]
    system_prompt = (
        "你是A股上市公司业务研究Agent。你只根据给定联网搜索结果和常识性行业分类整理公司业务，"
        "不得编造未在材料中出现的重大订单、客户、财务变化。输出必须是中文Markdown。"
    )
    user_prompt = f"""
请研究 A股公司 {name}（{ts_code}）并输出一份业务说明。

要求严格对齐以下结构，不能输出一级标题，不能输出“所属板块”和“关键词”两个章节，因为外层保存模板会追加它们：

## 公司主营业务概览
用2-4段说明公司到底做什么、商业模式、核心客户/应用场景。

## 主要产品与服务体系
按“### 1. ...”分组，列出核心产品、软件/平台、解决方案或渠道/品牌。每组用要点说明。

## 所属行业板块分析
说明核心申万/行业分类和热门概念板块。只纳入与主营业务强相关的板块，不要写与主营无关板块的排除说明。

## 近期重要业务变化与动态
按“### 1. ...”列出2-4条近期业务变化。没有可靠材料时写“未检索到明确重大变化”，不要硬编。

联网搜索结果：
{source_block}
"""
    chat = _minimax_chat(system_prompt, user_prompt)
    if chat.get("ok") and len(chat.get("content", "").strip()) >= 100:
        return _clean_business_md(chat["content"])
    return _fallback_business_md(name, ts_code, sources, raw_text)


def _normalize_search_text(result: dict) -> str:
    raw = result.get("raw")
    if isinstance(raw, str):
        return raw
    return json.dumps(raw, ensure_ascii=False, indent=2)


def _strip_reasoning(text: str) -> str:
    text = str(text or "")
    if "</think>" in text:
        text = text.split("</think>", 1)[1]
    return text.strip()


def _clean_business_md(text: str) -> str:
    text = _strip_reasoning(text)
    lines = []
    for line in text.splitlines():
        stripped = line.strip()
        if stripped.startswith("# "):
            continue
        if stripped in ("## 所属板块", "## 关键词"):
            break
        if any(k in stripped for k in ("银行", "保险", "金融")) and any(k in stripped for k in ("不纳入", "无强相关", "不涉及")):
            continue
        lines.append(line.rstrip())
    cleaned = "\n".join(lines).strip()
    if "## 公司主营业务概览" not in cleaned:
        cleaned = "## 公司主营业务概览\n\n" + cleaned
    return cleaned


def _fallback_business_md(name: str, ts_code: str, sources: list[dict], raw_text: str) -> str:
    snippets = [s.get("snippet", "") for s in sources if s.get("snippet")]
    joined = "\n".join(snippets) or raw_text[:2000]
    return f"""## 公司主营业务概览

{name}（{ts_code}）的业务信息来自 MiniMax 联网搜索结果。以下为搜索摘要整理，需以后续公告、年报和互动平台信息校验。

{joined[:900]}

## 主要产品与服务体系

### 1. 核心产品与业务
- 根据搜索结果提取主营产品、服务和应用场景。

### 2. 业务模式
- 结合公告、新闻和交易所互动信息，关注收入来源、客户类型、渠道和交付方式。

## 所属行业板块分析

- 以主营业务对应的申万行业和A股常用概念为准，剔除融资、贷款、偿债等非主营业务词造成的误判。

## 近期重要业务变化与动态

### 1. 联网搜索摘要
- {joined[:500]}
"""


def _extract_sources(raw) -> list[dict]:
    if isinstance(raw, str):
        try:
            raw = json.loads(raw)
        except json.JSONDecodeError:
            return []
    if not isinstance(raw, dict):
        return []
    items = raw.get("organic") or raw.get("results") or []
    sources = []
    for item in items[:8]:
        if not isinstance(item, dict):
            continue
        sources.append({
            "title": item.get("title", ""),
            "url": item.get("link") or item.get("url") or "",
            "snippet": item.get("snippet") or item.get("summary") or "",
            "date": item.get("date", ""),
        })
    return sources


def _raw_contains_error(raw) -> bool:
    if isinstance(raw, dict):
        if raw.get("error"):
            return True
        params = raw.get("params")
        if isinstance(params, dict) and str(params.get("level", "")).lower() == "error":
            return True
        return any(_raw_contains_error(v) for v in raw.values())
    if isinstance(raw, list):
        return any(_raw_contains_error(v) for v in raw)
    text = str(raw or "").lower()
    return (
        '"level":"error"' in text
        or '"level": "error"' in text
        or "internal server error" in text
        or '"error"' in text and "jsonrpc" in text
    )


def _to_business_md(ts_code: str, name: str, search_text: str) -> str:
    return f"""## 主营业务与近期信息

以下内容来自 MiniMax 联网搜索结果整理，需以后续公告和年报校验。

### 公司
- 股票: {ts_code}
- 名称: {name}

### 搜索摘要
{search_text[:6000]}
"""
