import asyncio
import os
import tempfile
import unittest
from unittest.mock import patch

from backend.api import agent_routes
from backend.api.agent_routes import CapitalFlowRequest
from backend.db.repository import get_conn
from backend.db.schema import init_db


class AgentCapitalFlowTestCase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.NamedTemporaryFile(delete=False)
        self.tmp.close()
        init_db(self.tmp.name).close()
        conn = self._conn()
        conn.execute(
            """INSERT INTO agent_info (id, name, display_name, initial_capital, current_cash, status)
               VALUES (6, 'xulu', 'xulu的agent', 260000, 100000, 'active')"""
        )
        conn.commit()
        conn.close()

    def tearDown(self):
        try:
            os.unlink(self.tmp.name)
        except FileNotFoundError:
            pass

    def _conn(self):
        return get_conn(self.tmp.name)

    def test_deposit_updates_cash_capital_and_records_flow(self):
        req = CapitalFlowRequest(flow_type="deposit", amount=200000, note="追加本金")
        with patch.object(agent_routes, "get_conn", self._conn):
            result = asyncio.run(agent_routes.create_agent_capital_flow_api(6, req))

        self.assertEqual(result["status"], "ok")
        self.assertAlmostEqual(result["agent"]["current_cash"], 300000.0)
        self.assertAlmostEqual(result["agent"]["initial_capital"], 460000.0)
        conn = self._conn()
        agent = conn.execute("SELECT current_cash, initial_capital FROM agent_info WHERE id=6").fetchone()
        flow = conn.execute("SELECT * FROM agent_capital_flow WHERE agent_id=6").fetchone()
        conn.close()
        self.assertAlmostEqual(agent["current_cash"], 300000.0)
        self.assertAlmostEqual(agent["initial_capital"], 460000.0)
        self.assertEqual(flow["flow_type"], "deposit")
        self.assertAlmostEqual(flow["amount"], 200000.0)
        self.assertAlmostEqual(flow["cash_before"], 100000.0)
        self.assertAlmostEqual(flow["cash_after"], 300000.0)
        self.assertEqual(flow["note"], "追加本金")

    def test_withdraw_rejects_negative_cash(self):
        req = CapitalFlowRequest(flow_type="withdraw", amount=150000, note="出金")
        with patch.object(agent_routes, "get_conn", self._conn):
            result = asyncio.run(agent_routes.create_agent_capital_flow_api(6, req))

        self.assertIn("negative", result["error"])
        conn = self._conn()
        count = conn.execute("SELECT COUNT(*) FROM agent_capital_flow").fetchone()[0]
        agent = conn.execute("SELECT current_cash, initial_capital FROM agent_info WHERE id=6").fetchone()
        conn.close()
        self.assertEqual(count, 0)
        self.assertAlmostEqual(agent["current_cash"], 100000.0)
        self.assertAlmostEqual(agent["initial_capital"], 260000.0)


if __name__ == "__main__":
    unittest.main()
