#!/usr/bin/env python3
"""Recommendation assistant smoke checks.

Default mode stays on deterministic fast paths. Use --include-react for one slow
free-form ReAct case when debugging provider output.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys
import time

ROOT_DIR = Path(__file__).resolve().parents[1]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from backend.db.schema import init_db
from backend.db.repository import get_conn
from backend.telegram.recommender import handle_text_message, run_recommend_react_agent


FAST_CASES = [
    {
        "name": "leader_one",
        "text": "推荐一个龙头股",
        "type": "recommend",
        "must_contain": ["选股结果: momentum", "仅供研究"],
    },
    {
        "name": "strong_three",
        "text": "推荐3只强势股",
        "type": "recommend",
        "must_contain": ["选股结果: momentum", "1."],
    },
    {
        "name": "ma20_pullback",
        "text": "帮我选几支股票回踩20线的股票",
        "type": "recommend",
        "must_contain": ["选股结果: ma_pullback", "20日均线"],
        "must_not_contain": ["60日均线"],
    },
    {
        "name": "ma20_pullback_alt",
        "text": "推荐几支回踩20日均线的股票",
        "type": "recommend",
        "must_contain": ["选股结果: ma_pullback"],
        "must_not_contain": ["60日均线"],
    },
    {
        "name": "tech_momentum",
        "text": "科技股里选3只强势股",
        "type": "recommend",
        "must_contain": ["选股结果: momentum"],
    },
    {
        "name": "low_risk_momentum",
        "text": "稳健一点推荐两只龙头股",
        "type": "recommend",
        "must_contain": ["选股结果: momentum"],
    },
    {
        "name": "dividend_momentum",
        "text": "高股息方向推荐3只强势股",
        "type": "recommend",
        "must_contain": ["选股结果: momentum"],
    },
    {
        "name": "analyze_stock",
        "text": "/analyze 600000.SH",
        "type": "chat",
        "must_contain": ["600000.SH"],
    },
    {
        "name": "compare_stock",
        "text": "/compare 600000.SH 600036.SH",
        "type": "chat",
        "must_contain": ["多股对比"],
    },
    {
        "name": "profile",
        "text": "/profile",
        "type": "chat",
        "must_contain": ["风险"],
    },
]

REACT_CASES = [
    {
        "name": "react_market_chase",
        "text": "今天适合追涨吗，给我一个有依据的判断",
        "type": "recommend",
        "must_contain": ["仅供"],
    },
]


def latest_eval(chat_id: str) -> dict:
    conn = get_conn()
    row = conn.execute(
        """SELECT id, status, fallback_used, json_parse_ok, trace_json, response_latency_ms
           FROM telegram_recommend_eval
           WHERE chat_id=?
           ORDER BY id DESC LIMIT 1""",
        (chat_id,),
    ).fetchone()
    conn.close()
    return dict(row) if row else {}


def run_case(case: dict, chat_id: str) -> dict:
    started = time.perf_counter()
    if case["type"] == "recommend":
        result = run_recommend_react_agent(case["text"], chat_id, "smoke")
        reply = result.get("message") or ""
        meta = result
    else:
        reply = handle_text_message(case["text"], chat_id, "smoke")
        meta = {}
    elapsed_ms = (time.perf_counter() - started) * 1000
    errors = []
    if not reply.strip():
        errors.append("empty reply")
    if "推荐助手没有生成有效回复" in reply:
        errors.append("invalid fallback message leaked")
    for needle in case.get("must_contain", []):
        if needle not in reply:
            errors.append(f"missing: {needle}")
    for needle in case.get("must_not_contain", []):
        if needle in reply:
            errors.append(f"forbidden: {needle}")
    eval_row = latest_eval(chat_id) if case["type"] == "recommend" else {}
    if case["type"] == "recommend" and not eval_row:
        errors.append("missing eval row")
    trace = {}
    if eval_row.get("trace_json"):
        try:
            trace = json.loads(eval_row["trace_json"] or "{}")
        except Exception:
            errors.append("eval trace_json is not JSON")
    return {
        "name": case["name"],
        "ok": not errors,
        "errors": errors,
        "elapsed_ms": round(elapsed_ms, 2),
        "mode": meta.get("mode") or trace.get("mode") or ("react" if case["type"] == "recommend" else "chat"),
        "fallback": bool(meta.get("fallback") or eval_row.get("fallback_used")),
        "eval_id": eval_row.get("id"),
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--include-react", action="store_true", help="run one slow free-form ReAct case")
    parser.add_argument("--chat-id", default=f"smoke_{int(time.time())}")
    args = parser.parse_args()

    init_db().close()
    cases = FAST_CASES + (REACT_CASES if args.include_react else [])
    results = [run_case(case, args.chat_id) for case in cases]
    for item in results:
        flag = "PASS" if item["ok"] else "FAIL"
        print(
            f"{flag} {item['name']} {item['elapsed_ms']}ms "
            f"mode={item['mode']} fallback={item['fallback']} eval={item.get('eval_id') or '-'}"
        )
        for err in item["errors"]:
            print(f"  - {err}")
    failed = [x for x in results if not x["ok"]]
    print(f"summary: {len(results) - len(failed)}/{len(results)} passed")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
