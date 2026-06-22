import os
import tempfile
import unittest
from unittest.mock import patch

from backend.db.repository import get_conn
from backend.db.schema import init_db
from backend.pipeline import daily_pipeline as dp


class DataFreshnessCriticalSymbolsTestCase(unittest.TestCase):
    def setUp(self):
        self.db_file = tempfile.NamedTemporaryFile(delete=False)
        self.db_file.close()
        init_db(self.db_file.name).close()
        self.daily_dir = tempfile.TemporaryDirectory()

    def tearDown(self):
        self.daily_dir.cleanup()
        try:
            os.unlink(self.db_file.name)
        except FileNotFoundError:
            pass

    def _conn(self):
        return get_conn(self.db_file.name)

    def _write_daily(self, ts_code: str, trade_date: str):
        path = os.path.join(self.daily_dir.name, f"{ts_code}_daily.csv")
        with open(path, "w", encoding="utf-8") as f:
            f.write("trade_date,close\n")
            f.write(f"{trade_date},10.0\n")

    def test_position_symbol_must_have_expected_trade_date(self):
        conn = self._conn()
        conn.execute(
            "INSERT INTO agent_position (agent_id, ts_code, stock_name, quantity) VALUES (1, '600522.SH', '中天科技', 100)"
        )
        conn.commit()
        conn.close()

        self._write_daily("600522.SH", "20260621")
        with patch.object(dp, "get_conn", self._conn), patch("backend.config.DAILY_DIR", self.daily_dir.name):
            missing = dp._missing_critical_agent_symbols("20260622")
        self.assertIn("600522.SH:20260621", missing)

        self._write_daily("600522.SH", "20260622")
        with patch.object(dp, "get_conn", self._conn), patch("backend.config.DAILY_DIR", self.daily_dir.name):
            missing = dp._missing_critical_agent_symbols("20260622")
        self.assertEqual(missing, [])

    def test_pending_order_symbol_is_critical(self):
        conn = self._conn()
        conn.execute(
            """INSERT INTO agent_order
               (agent_id, ts_code, stock_name, direction, quantity, price, status, trade_date)
               VALUES (1, '600522.SH', '中天科技', 'buy', 100, 10.0, 'pending', '20260622')"""
        )
        conn.commit()
        conn.close()

        with patch.object(dp, "get_conn", self._conn), patch("backend.config.DAILY_DIR", self.daily_dir.name):
            missing = dp._missing_critical_agent_symbols("20260622")
        self.assertEqual(missing, ["600522.SH:-"])


if __name__ == "__main__":
    unittest.main()
