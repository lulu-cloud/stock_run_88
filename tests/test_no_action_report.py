import unittest
from unittest.mock import mock_open, patch

from backend.agents.base import AgentDecision
from backend.logs.report_generator import _no_action_note
from backend.telegram.digest import _agent_orders_md


class NoActionReportTestCase(unittest.TestCase):
    def test_no_action_note_uses_agent_reason(self):
        decision = AgentDecision(
            agent_id=5,
            trade_date="20260608",
            orders=[],
            market_analysis="市场 risk-off，股票池候选均未触发买点。",
            risk_assessment="当前空仓等待右侧确认。",
        )

        note = _no_action_note([], decision)

        self.assertIn("没有成交、没有新条件单", note)
        self.assertIn("股票池候选均未触发买点", note)

    def test_no_action_note_suppressed_when_orders_exist(self):
        decision = AgentDecision(agent_id=5, trade_date="20260608", orders=[{"ts_code": "000001.SZ"}])

        self.assertEqual(_no_action_note([], decision), "")

    def test_digest_includes_no_action_reason_when_no_pending_orders(self):
        class Row(dict):
            def __getitem__(self, key):
                return self.get(key)

        class Conn:
            def execute(self, sql, params=()):
                if "FROM agent_order" in sql:
                    return self
                return self

            def fetchall(self):
                return []

            def fetchone(self):
                return Row({"report_md_path": "/tmp/report.md"})

            def close(self):
                pass

        report = """# report
## 4. 操作总结
Agent 未生成分析。 复盘异常: Agent review timed out after 600s

### 无操作说明

本轮没有成交、没有新条件单。复盘异常: Agent review timed out after 600s。

## 5. 新生成条件单
"""
        with patch("backend.telegram.digest.get_conn", return_value=Conn()), \
             patch("backend.telegram.digest.os.path.exists", return_value=True), \
             patch("builtins.open", mock_open(read_data=report)):
            text = _agent_orders_md(6, "20260609")

        self.assertIn("无待触发预操作单", text)
        self.assertIn("空仓/无单理由", text)
        self.assertIn("Agent review timed out", text)


if __name__ == "__main__":
    unittest.main()
