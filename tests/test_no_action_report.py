import unittest

from backend.agents.base import AgentDecision
from backend.logs.report_generator import _no_action_note


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


if __name__ == "__main__":
    unittest.main()
