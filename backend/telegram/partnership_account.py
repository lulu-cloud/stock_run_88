"""Partnership account accounting tools for Telegram commands."""

from __future__ import annotations

import json
import re
from datetime import datetime
from zoneinfo import ZoneInfo

from backend.db.repository import get_conn


TZ = ZoneInfo("Asia/Shanghai")


def _today() -> str:
    return datetime.now(TZ).strftime("%Y-%m-%d")


def _money(text: str) -> float | None:
    raw = str(text or "").strip().replace(",", "").replace("，", "")
    m = re.search(r"([+-]?\d+(?:\.\d+)?)\s*(万|w|W|千|k|K)?", raw)
    if not m:
        return None
    value = float(m.group(1))
    unit = m.group(2) or ""
    if unit in {"万", "w", "W"}:
        value *= 10000
    elif unit in {"千", "k", "K"}:
        value *= 1000
    return value


def _fmt_money(value: float | int | None) -> str:
    return f"{float(value or 0):,.2f}"


def _extract_date(text: str) -> str:
    raw = text or ""
    m = re.search(r"(20\d{2})[-/.年](\d{1,2})[-/.月](\d{1,2})", raw)
    if not m:
        return _today()
    return f"{int(m.group(1)):04d}-{int(m.group(2)):02d}-{int(m.group(3)):02d}"


def _participants(conn) -> list[dict]:
    return [dict(r) for r in conn.execute("SELECT name, equity, net_invest FROM participants ORDER BY rowid").fetchall()]


def _aliases(names: list[str]) -> dict[str, str]:
    result = {name: name for name in names}
    if names:
        result["我"] = names[0]
        result["自己"] = names[0]
        result["本人"] = names[0]
    if len(names) > 1:
        result["对象"] = names[1]
        result["合伙人"] = names[1]
        result["另一半"] = names[1]
    return result


def account_help() -> str:
    return "\n".join([
        "合伙账户命令:",
        "/init xulu hsw 150000 100000",
        "/init 初始建仓，xulu出资15万，hsw出资10万",
        "/daily 256000 0 5000",
        "/daily amend 465759.29  更正最近一天录错的总资产",
        "今天总资产25.6万，hsw入金5000",
        "刚刚录错了，今天总资产改成46.575929万",
        "/status 查看当前权益和累计盈亏",
        "/history 查看最近7天分成明细",
        "",
        "计算规则: 当日盈亏 = 今日总资产 - 昨日总资产 - 当日总净入金；盈亏按昨日权益比例分配，今日出入金从明天开始影响分配比例。",
    ])


def _parse_init(text: str) -> tuple[dict[str, float], str]:
    raw = re.sub(r"^/init(@\w+)?", "", text or "", flags=re.I).strip()
    parts = [x for x in re.split(r"\s+", raw) if x]
    if len(parts) >= 4:
        a = _money(parts[-2])
        b = _money(parts[-1])
        if a is not None and b is not None:
            return {parts[-4]: a, parts[-3]: b}, ""

    pairs: dict[str, float] = {}
    pattern = re.compile(r"([A-Za-z0-9_\-\u4e00-\u9fff]{1,20})\s*(?:出资|投入|入金|转入|注入)\s*([+-]?\d+(?:\.\d+)?\s*(?:万|w|W|千|k|K)?)")
    for name, amount_text in pattern.findall(raw):
        amount = _money(amount_text)
        if amount is not None:
            pairs[name] = amount
    if len(pairs) >= 2:
        return dict(list(pairs.items())[:2]), ""
    return {}, "未识别到两个参与人及初始金额。示例: /init xulu hsw 150000 100000，或 /init xulu出资15万，hsw出资10万"


def partnership_init_account(text: str) -> str:
    """Initialize the two-person partnership account from a Telegram command."""
    participants, error = _parse_init(text)
    if error:
        return error
    total = sum(participants.values())
    if total <= 0:
        return "初始化失败: 初始总资产必须大于 0。"
    conn = get_conn()
    try:
        existing = conn.execute("SELECT last_total_asset FROM account LIMIT 1").fetchone()
        if existing:
            return "账户已经初始化。为避免误覆盖历史，请先人工确认是否需要重置数据库。"
        for name, amount in participants.items():
            if amount < 0:
                return f"初始化失败: {name} 的初始投入不能为负数。"
        conn.execute("INSERT INTO account (last_total_asset, last_date) VALUES (?, ?)", (total, _today()))
        for name, amount in participants.items():
            conn.execute(
                "INSERT INTO participants (name, equity, net_invest) VALUES (?, ?, ?)",
                (name, amount, amount),
            )
        conn.commit()
    finally:
        conn.close()

    lines = ["合伙账户已初始化", f"初始总资产: {_fmt_money(total)}"]
    for name, amount in participants.items():
        lines.append(f"- {name}: 投入 {_fmt_money(amount)}，初始权益 {_fmt_money(amount)}")
    return "\n".join(lines)


def _parse_total_asset(raw: str) -> float | None:
    m = re.search(r"(?:总资产|总权益|账户资产|合并账户|账户)\s*(?:是|为|=|:|：)?\s*([+-]?\d+(?:\.\d+)?\s*(?:万|w|W|千|k|K)?)", raw)
    if m:
        return _money(m.group(1))
    parts = _daily_value_parts(raw)
    if parts:
        return _money(parts[0])
    return None


def _is_correction(raw: str) -> bool:
    lower = (raw or "").lower()
    return any(token in lower for token in ("amend", "update", "correct", "fix", "append", "overwrite")) or any(
        token in (raw or "") for token in ("更正", "修正", "改成", "改为", "录错", "覆盖", "重算", "重新记", "重新上报")
    )


def _daily_value_parts(raw: str) -> list[str]:
    body = re.sub(r"^/daily(@\w+)?", "", raw or "", flags=re.I).strip()
    parts = [x for x in re.split(r"\s+", body) if x]
    if parts and re.fullmatch(r"20\d{2}[-/.]\d{1,2}[-/.]\d{1,2}", parts[0]):
        parts = parts[1:]
    control = {"amend", "update", "correct", "fix", "append", "overwrite"}
    return [p for p in parts if p.lower() not in control]


def _parse_daily(text: str, names: list[str]) -> tuple[str, float | None, dict[str, float], str]:
    raw = text or ""
    trade_date = _extract_date(raw)
    total_asset = _parse_total_asset(raw)
    flows = {name: 0.0 for name in names}
    aliases = _aliases(names)

    parts = _daily_value_parts(raw)
    if len(parts) >= 1 and total_asset is not None and len(parts[1:]) >= len(names):
        fixed_flows = []
        ok = True
        for item in parts[1:1 + len(names)]:
            amount = _money(item)
            if amount is None:
                ok = False
                break
            fixed_flows.append(amount)
        if ok:
            for name, amount in zip(names, fixed_flows):
                flows[name] = amount
            return trade_date, total_asset, flows, ""

    for alias, name in sorted(aliases.items(), key=lambda x: len(x[0]), reverse=True):
        escaped = re.escape(alias)
        for verb, amount_text in re.findall(
            escaped + r"\s*(入金|转入|追加|出资|投入|出金|转出|取出|提现|赎回)\s*([+-]?\d+(?:\.\d+)?\s*(?:万|w|W|千|k|K)?)",
            raw,
        ):
            amount = _money(amount_text)
            if amount is None:
                continue
            if verb in {"出金", "转出", "取出", "提现", "赎回"}:
                amount = -abs(amount)
            else:
                amount = abs(amount)
            flows[name] = flows.get(name, 0.0) + amount

    if total_asset is None:
        return trade_date, None, flows, "未识别到今日总资产。示例: /daily 256000 0 5000，或 今天总资产25.6万，hsw入金5000"
    return trade_date, total_asset, flows, ""


def _load_json_dict(text: str | None) -> dict[str, float]:
    try:
        data = json.loads(text or "{}")
    except Exception:
        return {}
    if not isinstance(data, dict):
        return {}
    result = {}
    for k, v in data.items():
        try:
            result[str(k)] = float(v or 0)
        except Exception:
            result[str(k)] = 0.0
    return result


def _apply_daily_update(
    conn,
    trade_date: str,
    total_asset: float,
    flows: dict[str, float],
    prev_total: float,
    prev_date: str,
    previous_rows: list[dict],
    existing_history: dict | None = None,
) -> str:
    total_flow = sum(float(v or 0) for v in flows.values())
    daily_pnl = float(total_asset) - prev_total - total_flow
    prev_equity_sum = sum(float(r["equity"] or 0) for r in previous_rows)
    if prev_equity_sum <= 0:
        return "上报失败: 昨日权益合计为 0，无法按比例分配盈亏。"

    allocation: dict[str, float] = {}
    remaining = daily_pnl
    for idx, row in enumerate(previous_rows):
        name = row["name"]
        if idx == len(previous_rows) - 1:
            share = remaining
        else:
            share = daily_pnl * float(row["equity"] or 0) / prev_equity_sum
            remaining -= share
        allocation[name] = share

    before = {r["name"]: float(r["equity"] or 0) for r in previous_rows}
    for row in previous_rows:
        name = row["name"]
        new_equity = before[name] + allocation[name] + flows.get(name, 0.0)
        new_net_invest = float(row["net_invest"] or 0) + flows.get(name, 0.0)
        conn.execute(
            "UPDATE participants SET equity=?, net_invest=? WHERE name=?",
            (new_equity, new_net_invest, name),
        )
    if existing_history:
        conn.execute(
            "UPDATE daily_history SET total_asset=?, daily_pnl=?, cash_flows=?, allocation=? WHERE date=?",
            (
                total_asset,
                daily_pnl,
                json.dumps(flows, ensure_ascii=False),
                json.dumps(allocation, ensure_ascii=False),
                trade_date,
            ),
        )
    else:
        conn.execute(
            "INSERT INTO daily_history (date, total_asset, daily_pnl, cash_flows, allocation) VALUES (?, ?, ?, ?, ?)",
            (
                trade_date,
                total_asset,
                daily_pnl,
                json.dumps(flows, ensure_ascii=False),
                json.dumps(allocation, ensure_ascii=False),
            ),
        )
    conn.execute("UPDATE account SET last_total_asset=?, last_date=?", (total_asset, trade_date))
    conn.commit()

    updated = _participants(conn)
    title = f"{trade_date} 合伙账户分成报告"
    if existing_history:
        title = f"{trade_date} 合伙账户更正报告"
    lines = [
        title,
        f"昨日日期: {prev_date or '-'}",
        f"昨日总资产: {_fmt_money(prev_total)}",
        f"今日总资产: {_fmt_money(total_asset)}",
        f"当日净入金: {_fmt_money(total_flow)}",
        f"当日盈亏: {_fmt_money(daily_pnl)}",
    ]
    if existing_history:
        lines.extend([
            f"更正前总资产: {_fmt_money(existing_history.get('total_asset'))}",
            f"更正前当日盈亏: {_fmt_money(existing_history.get('daily_pnl'))}",
        ])
    lines.extend([
        "",
        "盈亏按昨日权益比例分配，今日出入金 T+1 生效:",
    ])
    updated_by_name = {r["name"]: r for r in updated}
    for row in previous_rows:
        name = row["name"]
        ratio = float(row["equity"] or 0) / prev_equity_sum * 100
        now = updated_by_name[name]
        pnl_total = float(now["equity"] or 0) - float(now["net_invest"] or 0)
        lines.append(
            f"- {name}: 昨日权益 {_fmt_money(before[name])} ({ratio:.2f}%)，"
            f"分得盈亏 {_fmt_money(allocation[name])}，出入金 {_fmt_money(flows.get(name, 0))}，"
            f"当前权益 {_fmt_money(now['equity'])}，累计盈亏 {_fmt_money(pnl_total)}"
        )
    return "\n".join(lines)


def _amend_latest_daily(conn, account, rows: list[dict], trade_date: str, total_asset: float, flows: dict[str, float], existing_row) -> str:
    last_date = account["last_date"] or ""
    if last_date != trade_date:
        return f"{trade_date} 不是当前账本最近日期（最近日期 {last_date or '-'}）。当前只支持更正最近一天，避免破坏后续分成链条。"

    existing = dict(existing_row)
    old_flows = _load_json_dict(existing.get("cash_flows"))
    old_allocation = _load_json_dict(existing.get("allocation"))
    old_total = float(existing.get("total_asset") or 0)
    old_pnl = float(existing.get("daily_pnl") or 0)
    old_total_flow = sum(float(v or 0) for v in old_flows.values())
    prev_total = old_total - old_pnl - old_total_flow

    previous_rows = []
    for row in rows:
        name = row["name"]
        previous_rows.append({
            "name": name,
            "equity": float(row["equity"] or 0) - old_allocation.get(name, 0.0) - old_flows.get(name, 0.0),
            "net_invest": float(row["net_invest"] or 0) - old_flows.get(name, 0.0),
        })
    return _apply_daily_update(conn, trade_date, total_asset, flows, prev_total, "上一记录", previous_rows, existing)


def partnership_daily_report(text: str) -> str:
    """Record daily total asset and cash flows, then allocate PnL by prior equity ratio."""
    conn = get_conn()
    try:
        account = conn.execute("SELECT last_total_asset, last_date FROM account LIMIT 1").fetchone()
        if not account:
            return "账户尚未初始化。请先发送 /init xulu hsw 150000 100000"
        rows = _participants(conn)
        names = [r["name"] for r in rows]
        trade_date, total_asset, flows, error = _parse_daily(text, names)
        if error:
            return error
        if total_asset is None or total_asset < 0:
            return "上报失败: 今日总资产必须是非负数。"
        existing = conn.execute("SELECT * FROM daily_history WHERE date=?", (trade_date,)).fetchone()
        if existing:
            if not _is_correction(text):
                return f"{trade_date} 已经上报过。如需更正最近一天，请发送 /daily amend {total_asset:.2f}，或说“今天总资产改成{total_asset:.2f}”。"
            return _amend_latest_daily(conn, account, rows, trade_date, total_asset, flows, existing)

        prev_total = float(account["last_total_asset"] or 0)
        prev_date = account["last_date"] or ""
        return _apply_daily_update(conn, trade_date, total_asset, flows, prev_total, prev_date, rows)
    finally:
        conn.close()


def partnership_status() -> str:
    """Return current partnership account status."""
    conn = get_conn()
    try:
        account = conn.execute("SELECT last_total_asset, last_date FROM account LIMIT 1").fetchone()
        if not account:
            return "账户尚未初始化。请先发送 /init xulu hsw 150000 100000"
        rows = _participants(conn)
    finally:
        conn.close()
    total_equity = sum(float(r["equity"] or 0) for r in rows)
    lines = [
        "合伙账户当前状态",
        f"最近日期: {account['last_date'] or '-'}",
        f"账户总资产: {_fmt_money(account['last_total_asset'])}",
    ]
    for row in rows:
        equity = float(row["equity"] or 0)
        net_invest = float(row["net_invest"] or 0)
        pnl = equity - net_invest
        ratio = equity / total_equity * 100 if total_equity > 0 else 0
        lines.append(
            f"- {row['name']}: 权益 {_fmt_money(equity)} ({ratio:.2f}%)，"
            f"累计净投入 {_fmt_money(net_invest)}，累计盈亏 {_fmt_money(pnl)}"
        )
    return "\n".join(lines)


def partnership_history(limit: int = 7) -> str:
    """Return recent partnership daily allocation history."""
    conn = get_conn()
    try:
        rows = conn.execute(
            "SELECT * FROM daily_history ORDER BY date DESC LIMIT ?",
            (max(1, min(int(limit or 7), 30)),),
        ).fetchall()
    finally:
        conn.close()
    if not rows:
        return "暂无每日分成记录。"
    lines = [f"最近 {len(rows)} 天合伙账户分成历史"]
    for row in rows:
        try:
            flows = json.loads(row["cash_flows"] or "{}")
        except Exception:
            flows = {}
        try:
            allocation = json.loads(row["allocation"] or "{}")
        except Exception:
            allocation = {}
        alloc_text = "，".join(f"{k}:{_fmt_money(v)}" for k, v in allocation.items()) or "-"
        flow_text = "，".join(f"{k}:{_fmt_money(v)}" for k, v in flows.items() if abs(float(v or 0)) > 0) or "无"
        lines.append(
            f"- {row['date']}: 总资产 {_fmt_money(row['total_asset'])}，"
            f"盈亏 {_fmt_money(row['daily_pnl'])}，出入金 {flow_text}，分配 {alloc_text}"
        )
    return "\n".join(lines)


def dispatch_partnership_command(text: str) -> str:
    """Dispatch a partnership account command through deterministic tools."""
    raw = (text or "").strip()
    lower = raw.lower()
    if lower.startswith("/init"):
        return partnership_init_account(raw)
    if lower.startswith("/daily") or any(token in raw for token in ("总资产", "总权益", "入金", "出金", "转入", "转出")):
        return partnership_daily_report(raw)
    if lower.startswith("/history") or raw.startswith("历史"):
        m = re.search(r"\d+", raw)
        return partnership_history(int(m.group(0)) if m else 7)
    if lower.startswith("/status") or raw.startswith(("状态", "账户状态")):
        return partnership_status()
    return account_help()


def is_partnership_account_message(text: str) -> bool:
    raw = (text or "").strip()
    lower = raw.lower()
    if lower.startswith("/init") or lower.startswith("/history"):
        return True
    if lower.startswith("/daily"):
        return not ("on" in lower or "off" in lower or "开启" in raw or "关闭" in raw)
    if lower.startswith("/status"):
        return not re.search(r"^/status(@\w+)?\s+\d+", lower)
    if any(token in raw for token in ("今日总资产", "今天总资产", "账户总资产", "合并账户总资产")):
        return True
    return any(token in raw for token in ("合伙账户", "分成记录", "今日总资产", "今天总资产", "总资产")) and any(
        token in raw for token in ("入金", "出金", "转入", "转出", "分成", "权益")
    )
