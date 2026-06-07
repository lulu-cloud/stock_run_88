import os
import sqlite3
import tempfile
import unittest
from unittest.mock import patch

from backend.db.schema import init_db
from backend.pipeline.order_executor import execute_orders, _match_order
from backend.pipeline.daily_pipeline import _expand_split_orders


class OrderExecutorTestCase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.NamedTemporaryFile(delete=False)
        self.tmp.close()
        init_db(self.tmp.name).close()
        conn = sqlite3.connect(self.tmp.name)
        conn.row_factory = sqlite3.Row
        conn.execute(
            """INSERT INTO agent_info (id, name, display_name, current_cash, initial_capital, status)
               VALUES (99, 'test_agent', '测试Agent', 100000, 100000, 'active')"""
        )
        conn.execute(
            """INSERT INTO agent_position
               (agent_id, ts_code, stock_name, quantity, available_shares, avg_cost, current_price, market_value, buy_date)
               VALUES (99, '600000.SH', '浦发银行', 1000, 1000, 10.0, 10.0, 10000, '20260601')"""
        )
        conn.commit()
        conn.close()
        self.conn_patch = patch("backend.pipeline.order_executor.get_conn", self._get_conn)
        self.conn_patch.start()

    def tearDown(self):
        self.conn_patch.stop()
        for path in (self.tmp.name, f"{self.tmp.name}.migration_backup_done"):
            if os.path.exists(path):
                os.unlink(path)

    def _get_conn(self):
        conn = sqlite3.connect(self.tmp.name)
        conn.row_factory = sqlite3.Row
        return conn

    def _conn(self):
        conn = sqlite3.connect(self.tmp.name)
        conn.row_factory = sqlite3.Row
        return conn

    def test_stop_loss_fills_and_cancels_oco_sibling(self):
        conn = self._conn()
        conn.execute(
            """INSERT INTO agent_order
               (agent_id, ts_code, stock_name, direction, order_type, quantity, price, trigger_price,
                oco_group, status, trade_date, reason)
               VALUES (99, '600000.SH', '浦发银行', 'sell', 'stop_loss', 1000, 9.5, 9.5,
                'oco-1', 'pending', '20260605', '止损')"""
        )
        stop_id = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
        conn.execute(
            """INSERT INTO agent_order
               (agent_id, ts_code, stock_name, direction, order_type, quantity, price, trigger_price,
                oco_group, status, trade_date, reason)
               VALUES (99, '600000.SH', '浦发银行', 'sell', 'stop_profit', 1000, 10.8, 10.8,
                'oco-1', 'pending', '20260605', '止盈')"""
        )
        profit_id = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
        conn.commit()
        conn.close()

        trades = execute_orders(99, "20260605", {
            "600000.SH": {"open": 9.4, "high": 10.9, "low": 9.3, "close": 10.2, "pct_chg": -3.0}
        })

        self.assertEqual(len(trades), 1)
        self.assertEqual(trades[0]["order_id"], stop_id)
        self.assertEqual(trades[0]["price"], 9.4)
        conn = self._conn()
        rows = conn.execute("SELECT id, status FROM agent_order WHERE id IN (?, ?)", (stop_id, profit_id)).fetchall()
        statuses = {r["id"]: r["status"] for r in rows}
        conn.close()
        self.assertEqual(statuses[stop_id], "filled")
        self.assertEqual(statuses[profit_id], "cancelled")

    def test_chase_price_can_fill_after_limit_misses(self):
        matched, exec_price, reason = _match_order(
            {"direction": "buy", "price": 10.0, "chase_enabled": 1, "chase_pct": 3.0},
            open_p=10.8,
            low_p=10.2,
            high_p=10.4,
        )
        self.assertTrue(matched)
        self.assertEqual(exec_price, 10.3)
        self.assertIn("追价", reason)

    def test_split_order_expansion_respects_lots(self):
        rows = _expand_split_orders({"quantity": 700, "price": 10, "split_total": 3})
        self.assertEqual([r["quantity"] for r in rows], [300, 200, 200])
        self.assertEqual([r["split_seq"] for r in rows], [1, 2, 3])
        self.assertTrue(all(r["split_total"] == 3 for r in rows))


if __name__ == "__main__":
    unittest.main()
