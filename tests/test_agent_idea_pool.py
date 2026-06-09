import sqlite3
import unittest
from unittest.mock import patch

from backend.agents.base import AgentDecision
from backend.agents.idea_pool import (
    extract_trade_plan_from_text,
    idea_candidates_from_decision,
    list_agent_ideas,
    upsert_agent_ideas,
)


SCHEMA = """
CREATE TABLE agent_idea_pool (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    agent_id INTEGER NOT NULL,
    trade_date TEXT NOT NULL,
    ts_code TEXT NOT NULL,
    stock_name TEXT,
    source TEXT DEFAULT 'selected',
    score REAL,
    reason TEXT,
    status TEXT DEFAULT 'candidate',
    reject_reason TEXT DEFAULT '',
    discovery_price REAL DEFAULT 0.0,
    market_context_json TEXT DEFAULT '{}',
    raw_json TEXT DEFAULT '{}',
    created_at TEXT DEFAULT (datetime('now')),
    updated_at TEXT DEFAULT (datetime('now')),
    UNIQUE(agent_id, trade_date, ts_code, source)
);
CREATE TABLE agent_idea_outcome (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    idea_id INTEGER NOT NULL,
    ts_code TEXT NOT NULL,
    base_trade_date TEXT,
    base_price REAL DEFAULT 0.0,
    return_1d REAL,
    return_3d REAL,
    return_5d REAL,
    return_10d REAL,
    return_20d REAL,
    benchmark_return_5d REAL,
    beat_benchmark_5d INTEGER,
    max_adverse_excursion REAL DEFAULT 0.0,
    status TEXT DEFAULT 'pending',
    updated_at TEXT DEFAULT (datetime('now')),
    UNIQUE(idea_id)
);
"""


class AgentIdeaPoolTestCase(unittest.TestCase):
    def test_extracts_final_trade_plan_json(self):
        raw = """
        分析过程...
        ```json
        {"market_analysis":"偏暖","selected_stocks":[{"ts_code":"603986.SH","reason":"回调企稳"}],"orders":[],"risk_assessment":"轻仓"}
        ```
        """

        plan = extract_trade_plan_from_text(raw)

        self.assertEqual(plan["market_analysis"], "偏暖")
        self.assertEqual(plan["selected_stocks"][0]["ts_code"], "603986.SH")

    def test_upserts_decision_candidates(self):
        conn = sqlite3.connect(":memory:")
        conn.row_factory = sqlite3.Row
        conn.executescript(SCHEMA)
        decision = AgentDecision(
            agent_id=6,
            trade_date="20260609",
            selected_stocks=[{"ts_code": "603986.SH", "stock_name": "兆易创新", "score": 87, "reason": "回调企稳"}],
            orders=[{"ts_code": "002384.SZ", "stock_name": "东山精密", "direction": "buy", "price": 226.64, "reason": "站上MA5"}],
        )

        with patch("backend.agents.idea_pool.latest_close", return_value=100.0):
            count = upsert_agent_ideas(conn, 6, "20260609", idea_candidates_from_decision(decision))

        self.assertEqual(count, 2)
        rows = list_agent_ideas(conn, 6, 30)
        self.assertEqual(len(rows), 2)
        self.assertEqual(rows[0]["status"], "promoted")
        self.assertEqual(rows[1]["status"], "candidate")
        conn.close()


if __name__ == "__main__":
    unittest.main()
