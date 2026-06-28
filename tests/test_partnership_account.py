import os
import tempfile
import unittest
from unittest.mock import patch

from backend.db.repository import get_conn
from backend.db.schema import init_db
from backend.telegram import partnership_account as pa
from backend.telegram import recommender


class PartnershipAccountTestCase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.NamedTemporaryFile(delete=False)
        self.tmp.close()
        init_db(self.tmp.name).close()

    def tearDown(self):
        try:
            os.unlink(self.tmp.name)
        except FileNotFoundError:
            pass

    def _conn(self):
        return get_conn(self.tmp.name)

    def test_daily_report_allocates_pnl_by_previous_equity_before_cash_flow(self):
        with patch.object(pa, "get_conn", self._conn):
            init_reply = pa.partnership_init_account("/init xulu hsw 150000 100000")
            self.assertIn("初始总资产: 250,000.00", init_reply)

            reply = pa.partnership_daily_report("/daily 256000 0 5000")

            self.assertIn("当日净入金: 5,000.00", reply)
            self.assertIn("当日盈亏: 1,000.00", reply)
            self.assertIn("xulu: 昨日权益 150,000.00 (60.00%)，分得盈亏 600.00", reply)
            self.assertIn("hsw: 昨日权益 100,000.00 (40.00%)，分得盈亏 400.00，出入金 5,000.00", reply)

            conn = self._conn()
            rows = {r["name"]: dict(r) for r in conn.execute("SELECT * FROM participants").fetchall()}
            index_row = conn.execute(
                "SELECT * FROM xulu_index_daily ORDER BY trade_date DESC LIMIT 1"
            ).fetchone()
            conn.close()
            self.assertAlmostEqual(rows["xulu"]["equity"], 150600.0)
            self.assertAlmostEqual(rows["hsw"]["equity"], 105400.0)
            self.assertAlmostEqual(rows["hsw"]["net_invest"], 105000.0)
            self.assertIsNotNone(index_row)
            self.assertAlmostEqual(index_row["index_value"], 1000.0)
            self.assertAlmostEqual(index_row["daily_pnl"], 1000.0)

    def test_natural_daily_uses_partner_alias(self):
        with patch.object(pa, "get_conn", self._conn):
            pa.partnership_init_account("/init xulu hsw 150000 100000")
            reply = pa.partnership_daily_report("今天总资产25.6万，对象入金5000")

            self.assertIn("当日净入金: 5,000.00", reply)
            self.assertIn("hsw: 昨日权益 100,000.00", reply)

    def test_daily_fixed_mode_accepts_explicit_date(self):
        with patch.object(pa, "get_conn", self._conn):
            pa.partnership_init_account("/init xulu hsw 150000 100000")
            reply = pa.partnership_daily_report("/daily 2026-06-11 256000 0 5000")

            self.assertIn("2026-06-11 合伙账户分成报告", reply)
            self.assertIn("今日总资产: 256,000.00", reply)

    def test_natural_daily_without_cash_flow_is_account_message(self):
        self.assertTrue(pa.is_partnership_account_message("今天总资产25.6万"))
        with patch.object(pa, "get_conn", self._conn):
            pa.partnership_init_account("/init xulu hsw 150000 100000")
            reply = pa.partnership_daily_report("今天总资产25.6万")

            self.assertIn("当日净入金: 0.00", reply)
            self.assertIn("当日盈亏: 6,000.00", reply)

    def test_daily_amend_recalculates_latest_day(self):
        with patch.object(pa, "get_conn", self._conn), patch.object(pa, "_today", return_value="2026-06-22"):
            pa.partnership_init_account("/init xulu hsw 150000 100000")
            first = pa.partnership_daily_report("/daily 256000")
            self.assertIn("当日盈亏: 6,000.00", first)

            duplicate = pa.partnership_daily_report("/daily 260000")
            self.assertIn("已经上报过", duplicate)

            amended = pa.partnership_daily_report("/daily amend 260000")
            self.assertIn("合伙账户更正报告", amended)
            self.assertIn("更正前总资产: 256,000.00", amended)
            self.assertIn("当日盈亏: 10,000.00", amended)
            self.assertIn("xulu: 昨日权益 150,000.00 (60.00%)，分得盈亏 6,000.00", amended)
            self.assertIn("hsw: 昨日权益 100,000.00 (40.00%)，分得盈亏 4,000.00", amended)

            conn = self._conn()
            account = conn.execute("SELECT * FROM account").fetchone()
            rows = {r["name"]: dict(r) for r in conn.execute("SELECT * FROM participants").fetchall()}
            history_count = conn.execute("SELECT COUNT(*) FROM daily_history").fetchone()[0]
            hist = conn.execute("SELECT * FROM daily_history WHERE date='2026-06-22'").fetchone()
            index_row = conn.execute(
                "SELECT * FROM xulu_index_daily WHERE trade_date='2026-06-22'"
            ).fetchone()
            conn.close()
            self.assertAlmostEqual(account["last_total_asset"], 260000.0)
            self.assertAlmostEqual(rows["xulu"]["equity"], 156000.0)
            self.assertAlmostEqual(rows["hsw"]["equity"], 104000.0)
            self.assertEqual(history_count, 1)
            self.assertAlmostEqual(hist["total_asset"], 260000.0)
            self.assertAlmostEqual(index_row["total_asset"], 260000.0)
            self.assertAlmostEqual(index_row["daily_pnl"], 10000.0)

    def test_daily_rolls_back_account_when_index_write_fails(self):
        with patch.object(pa, "get_conn", self._conn):
            pa.partnership_init_account("/init xulu hsw 150000 100000")
            with patch.object(
                pa, "upsert_xulu_index_daily", side_effect=RuntimeError("index write failed")
            ):
                with self.assertRaisesRegex(RuntimeError, "index write failed"):
                    pa.partnership_daily_report("/daily 256000")

            conn = self._conn()
            account = conn.execute("SELECT * FROM account").fetchone()
            participants = conn.execute(
                "SELECT name, equity FROM participants ORDER BY name"
            ).fetchall()
            daily_count = conn.execute("SELECT COUNT(*) FROM daily_history").fetchone()[0]
            index_count = conn.execute("SELECT COUNT(*) FROM xulu_index_daily").fetchone()[0]
            conn.close()
            self.assertAlmostEqual(account["last_total_asset"], 250000.0)
            self.assertEqual([row["equity"] for row in participants], [100000.0, 150000.0])
            self.assertEqual(daily_count, 0)
            self.assertEqual(index_count, 0)

    def test_recommender_routes_account_commands_before_agent_status(self):
        with patch.object(recommender, "apply_inferred_preferences", return_value=None), \
             patch.object(recommender, "dispatch_partnership_command", return_value="account-status") as dispatch:
            reply = recommender._handle_text_message_inner("/status", chat_id="chat", username="u")

        self.assertEqual(reply, "account-status")
        dispatch.assert_called_once_with("/status")

    def test_status_with_agent_id_still_uses_agent_performance(self):
        with patch.object(recommender, "apply_inferred_preferences", return_value=None), \
             patch.object(recommender, "format_agent_performance", return_value="agent-status") as fmt:
            reply = recommender._handle_text_message_inner("/status 1", chat_id="chat", username="u")

        self.assertEqual(reply, "agent-status")
        fmt.assert_called_once_with(1)


if __name__ == "__main__":
    unittest.main()
