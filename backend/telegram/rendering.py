"""Render rich Markdown-like Telegram replies as HTML/PNG artifacts."""

from __future__ import annotations

import html
import os
import re
import tempfile
from datetime import datetime

from backend.config import REPORTS_DIR


STYLE = """
body{font-family:-apple-system,BlinkMacSystemFont,"Segoe UI","Noto Sans CJK SC","Microsoft YaHei",Arial,sans-serif;
margin:0;background:#f6f7f9;color:#121417;}
.page{max-width:920px;margin:0 auto;padding:28px 30px 34px;background:#fff;min-height:100vh;}
h1{font-size:24px;margin:0 0 18px;border-bottom:2px solid #111;padding-bottom:10px;}
h2{font-size:18px;margin:22px 0 10px;}
h3{font-size:16px;margin:18px 0 8px;}
p{line-height:1.68;margin:8px 0;}
ul{padding-left:20px;margin:8px 0 12px;}
li{line-height:1.62;margin:4px 0;}
table{border-collapse:collapse;width:100%;margin:12px 0;font-size:13px;}
th,td{border:1px solid #d6d9de;padding:7px 9px;text-align:left;vertical-align:top;}
th{background:#eef1f5;font-weight:700;}
pre{background:#101418;color:#edf2f7;border-radius:6px;padding:12px;overflow:auto;white-space:pre-wrap;}
code{font-family:"SFMono-Regular",Consolas,monospace;}
.meta{font-size:12px;color:#666;margin-bottom:18px;}
"""


TELEGRAM_HTML_LIMIT = 3800


def _inline(text: str) -> str:
    value = html.escape(text or "")
    value = re.sub(r"\*\*(.+?)\*\*", r"<b>\1</b>", value)
    value = re.sub(r"(?<!\*)\*([^*\n]+)\*(?!\*)", r"<i>\1</i>", value)
    value = re.sub(r"`([^`]+)`", r"<code>\1</code>", value)
    return value


def _is_table_block(lines: list[str], index: int) -> bool:
    if index + 1 >= len(lines):
        return False
    return "|" in lines[index] and re.match(r"^\s*\|?[\s:\-|]+\|[\s:\-|]*$", lines[index + 1] or "")


def _table_html(lines: list[str]) -> str:
    rows = []
    for line in lines:
        cells = [c.strip() for c in line.strip().strip("|").split("|")]
        rows.append(cells)
    if len(rows) >= 2:
        rows.pop(1)
    body = []
    for idx, cells in enumerate(rows):
        tag = "th" if idx == 0 else "td"
        body.append("<tr>" + "".join(f"<{tag}>{_inline(c)}</{tag}>" for c in cells) + "</tr>")
    return "<table>" + "".join(body) + "</table>"


def _parse_table(lines: list[str]) -> tuple[list[str], list[list[str]]]:
    rows = []
    for line in lines:
        cells = [c.strip() for c in line.strip().strip("|").split("|")]
        rows.append(cells)
    if len(rows) < 3:
        return [], []
    headers = rows[0]
    body = rows[2:]
    return headers, body


def _table_to_telegram_list(lines: list[str]) -> list[str]:
    headers, body = _parse_table(lines)
    if not headers or not body:
        return ["<pre>" + html.escape("\n".join(lines)) + "</pre>"]
    blocks: list[str] = []
    for row in body:
        if not any(cell.strip() for cell in row):
            continue
        title = row[0] if row else ""
        block = [f"<b>{_inline(title or '记录')}</b>"]
        for idx, value in enumerate(row[1:], 1):
            header = headers[idx] if idx < len(headers) else f"字段{idx + 1}"
            block.append(f"• {_inline(header)}：{_inline(value)}")
        blocks.append("\n".join(block))
    return blocks or ["<pre>" + html.escape("\n".join(lines)) + "</pre>"]


def markdown_to_telegram_html(markdown_text: str) -> str:
    """Convert Markdown-ish text to Telegram-supported HTML.

    Telegram does not support real tables. Markdown tables are converted into
    mobile-friendly row cards so users do not have to open an attachment.
    """
    lines = (markdown_text or "").splitlines()
    parts: list[str] = []
    code_lines: list[str] = []
    in_code = False
    i = 0
    while i < len(lines):
        line = lines[i]
        stripped = line.strip()
        if stripped.startswith("```"):
            if in_code:
                parts.append("<pre>" + html.escape("\n".join(code_lines)) + "</pre>")
                code_lines = []
                in_code = False
            else:
                in_code = True
            i += 1
            continue
        if in_code:
            code_lines.append(line)
            i += 1
            continue
        if _is_table_block(lines, i):
            block = [line]
            i += 1
            while i < len(lines) and "|" in lines[i]:
                block.append(lines[i])
                i += 1
            parts.extend(_table_to_telegram_list(block))
            continue
        if not stripped:
            parts.append("")
        elif stripped.startswith("#"):
            level = len(stripped) - len(stripped.lstrip("#"))
            title = stripped[level:].strip()
            parts.append(f"<b>{_inline(title)}</b>")
        elif stripped.startswith(("- ", "* ")):
            parts.append("• " + _inline(stripped[2:].strip()))
        elif re.match(r"^\d+[.)]\s+", stripped):
            number, content = re.split(r"\s+", stripped, 1)
            parts.append(f"{html.escape(number)} {_inline(content)}")
        else:
            parts.append(_inline(stripped))
        i += 1
    if in_code:
        parts.append("<pre>" + html.escape("\n".join(code_lines)) + "</pre>")
    text = "\n".join(parts)
    text = re.sub(r"\n{3,}", "\n\n", text).strip()
    return text


def telegram_html_chunks(markdown_text: str, limit: int = TELEGRAM_HTML_LIMIT) -> list[str]:
    """Split converted Telegram HTML without cutting markup in the middle."""
    raw = markdown_text or ""
    converted = markdown_to_telegram_html(raw)
    if len(converted) <= limit:
        return [converted]
    paragraphs = re.split(r"\n{2,}", raw)
    chunks: list[str] = []
    current: list[str] = []
    for paragraph in paragraphs:
        candidate_raw = "\n\n".join([*current, paragraph]) if current else paragraph
        candidate = markdown_to_telegram_html(candidate_raw)
        if len(candidate) <= limit:
            current.append(paragraph)
            continue
        if current:
            chunks.append(markdown_to_telegram_html("\n\n".join(current)))
            current = [paragraph]
        else:
            rendered = markdown_to_telegram_html(paragraph)
            while len(rendered) > limit:
                cut = rendered.rfind("\n", 0, limit)
                if cut < 500:
                    cut = limit
                chunks.append(rendered[:cut].strip())
                rendered = rendered[cut:].strip()
            if rendered:
                chunks.append(rendered)
            current = []
    if current:
        chunks.append(markdown_to_telegram_html("\n\n".join(current)))
    return [chunk for chunk in chunks if chunk.strip()]


def telegram_preview_text(markdown_text: str, limit: int = 1800) -> str:
    """Return a compact sample suitable for tests and UI diagnostics."""
    text = markdown_to_telegram_html(markdown_text)
    return text if len(text) <= limit else text[:limit] + "\n..."



def markdown_to_html(markdown_text: str, title: str = "Telegram Report") -> str:
    lines = (markdown_text or "").splitlines()
    html_parts: list[str] = []
    in_code = False
    code_lines: list[str] = []
    list_open = False
    i = 0
    while i < len(lines):
        line = lines[i]
        if line.strip().startswith("```"):
            if in_code:
                html_parts.append("<pre><code>" + html.escape("\n".join(code_lines)) + "</code></pre>")
                code_lines = []
                in_code = False
            else:
                if list_open:
                    html_parts.append("</ul>")
                    list_open = False
                in_code = True
            i += 1
            continue
        if in_code:
            code_lines.append(line)
            i += 1
            continue
        if _is_table_block(lines, i):
            if list_open:
                html_parts.append("</ul>")
                list_open = False
            block = [line]
            i += 1
            while i < len(lines) and "|" in lines[i]:
                block.append(lines[i])
                i += 1
            html_parts.append(_table_html(block))
            continue
        stripped = line.strip()
        if not stripped:
            if list_open:
                html_parts.append("</ul>")
                list_open = False
            i += 1
            continue
        if stripped.startswith("#"):
            if list_open:
                html_parts.append("</ul>")
                list_open = False
            level = min(3, len(stripped) - len(stripped.lstrip("#")))
            text = stripped[level:].strip()
            html_parts.append(f"<h{level}>{_inline(text)}</h{level}>")
        elif stripped.startswith(("- ", "* ")):
            if not list_open:
                html_parts.append("<ul>")
                list_open = True
            html_parts.append(f"<li>{_inline(stripped[2:].strip())}</li>")
        else:
            if list_open:
                html_parts.append("</ul>")
                list_open = False
            html_parts.append(f"<p>{_inline(stripped)}</p>")
        i += 1
    if in_code:
        html_parts.append("<pre><code>" + html.escape("\n".join(code_lines)) + "</code></pre>")
    if list_open:
        html_parts.append("</ul>")
    safe_title = html.escape(title or "Telegram Report")
    return (
        "<!doctype html><html><head><meta charset='utf-8'>"
        f"<title>{safe_title}</title><style>{STYLE}</style></head>"
        f"<body><main class='page'><div class='meta'>生成时间 {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}</div>"
        + "".join(html_parts)
        + "</main></body></html>"
    )


def write_html_report(markdown_text: str, title: str = "Telegram Report") -> str:
    root = os.path.join(REPORTS_DIR, "telegram")
    os.makedirs(root, exist_ok=True)
    fd, path = tempfile.mkstemp(prefix="telegram_report_", suffix=".html", dir=root)
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        f.write(markdown_to_html(markdown_text, title))
    return path


def render_markdown_png(markdown_text: str, title: str = "Telegram Report") -> str:
    html_path = write_html_report(markdown_text, title)
    png_path = html_path[:-5] + ".png"
    try:
        from playwright.sync_api import sync_playwright
    except Exception as exc:
        raise RuntimeError(f"playwright unavailable: {exc}") from exc
    with sync_playwright() as p:
        browser = p.chromium.launch()
        page = browser.new_page(viewport={"width": 980, "height": 1200}, device_scale_factor=1)
        page.goto("file://" + html_path, wait_until="load")
        page.locator(".page").screenshot(path=png_path)
        browser.close()
    return png_path


def is_complex_markdown(text: str) -> bool:
    raw = text or ""
    if len(raw) > 2500:
        return True
    if re.search(r"(?m)^\|.+\|$", raw):
        return True
    if raw.count("\n- ") >= 12 or raw.count("\n## ") >= 3:
        return True
    return False
