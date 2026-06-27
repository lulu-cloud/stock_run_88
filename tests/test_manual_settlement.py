import unittest
from datetime import date, datetime
from unittest.mock import MagicMock, patch
from zoneinfo import ZoneInfo

from backend.pipeline import daily_pipeline
from backend.telegram import manual_settlement


SHANGHAI = ZoneInfo("Asia/Shanghai")


def _conn_with_rows(rows):
    conn = MagicMock()
    conn.execute.return_value.fetchall.return_value = rows
    return conn


class ManualSettlementPipelineTestCase(unittest.TestCase):
    def test_rejects_before_market_close(self):
        now = datetime(2026, 6, 26, 14, 30, tzinfo=SHANGHAI)
        with patch.object(daily_pipeline, "is_stock_trade_day", return_value=True):
            result = daily_pipeline.run_manual_settlement(now=now)
        self.assertEqual(result["status"], "market_open")

    def test_data_not_ready_does_not_run_or_mutate_schedule(self):
        now = datetime(2026, 6, 27, 16, 0, tzinfo=SHANGHAI)
        rows = [{"id": 1, "display_name": "xulu", "last_run_date": None}]
        with patch.object(daily_pipeline, "is_stock_trade_day", return_value=False), \
             patch.object(daily_pipeline, "_latest_trading_day", return_value=date(2026, 6, 26)), \
             patch.object(daily_pipeline, "get_conn", return_value=_conn_with_rows(rows)), \
             patch.object(daily_pipeline, "check_data_freshness", return_value=False), \
             patch.object(daily_pipeline, "run_daily_pipeline") as run_pipeline, \
             patch.object(daily_pipeline, "_mark_agent_run_complete") as mark_complete, \
             patch.object(daily_pipeline, "maybe_start_market_data_fetch") as start_fetch:
            result = daily_pipeline.run_manual_settlement(now=now)

        self.assertEqual(result["status"], "data_not_ready")
        self.assertEqual(result["expected_trading_day"], "20260626")
        run_pipeline.assert_not_called()
        mark_complete.assert_not_called()
        start_fetch.assert_not_called()

    def test_runs_due_agents_skips_settled_and_pushes_now(self):
        now = datetime(2026, 6, 27, 16, 0, tzinfo=SHANGHAI)
        rows = [
            {"id": 1, "display_name": "xulu", "last_run_date": None},
            {"id": 2, "display_name": "steady", "last_run_date": "20260626"},
        ]
        with patch.object(daily_pipeline, "is_stock_trade_day", return_value=False), \
             patch.object(daily_pipeline, "_latest_trading_day", return_value=date(2026, 6, 26)), \
             patch.object(daily_pipeline, "get_conn", return_value=_conn_with_rows(rows)), \
             patch.object(daily_pipeline, "check_data_freshness", return_value=True), \
             patch("backend.macro.report.has_usable_macro_report", return_value=True), \
             patch.object(daily_pipeline, "snapshot_agent_state", return_value={}), \
             patch.object(daily_pipeline, "run_daily_pipeline", return_value={"xulu": {"status": "ok"}}) as run_pipeline, \
             patch.object(daily_pipeline, "_push_agent_once", return_value={"sent": []}) as push, \
             patch.object(daily_pipeline, "_mark_agent_run_complete") as mark_complete:
            result = daily_pipeline.run_manual_settlement(now=now)

        self.assertEqual(result["status"], "completed")
        self.assertEqual(result["due"], 1)
        self.assertEqual(result["agents"]["steady"]["status"], "already_settled")
        run_pipeline.assert_called_once_with("20260626", [1], fail_fast=True)
        push.assert_called_once_with(1, "20260626")
        mark_complete.assert_called_once_with(1, "20260626")

    def test_busy_lock_blocks_manual_and_scheduled_runs(self):
        daily_pipeline._agent_settlement_lock.acquire()
        try:
            manual = daily_pipeline.run_manual_settlement(
                now=datetime(2026, 6, 27, 16, 0, tzinfo=SHANGHAI)
            )
            scheduled = daily_pipeline.run_due_agents(
                now=datetime(2026, 6, 27, 16, 0, tzinfo=SHANGHAI)
            )
        finally:
            daily_pipeline._agent_settlement_lock.release()
        self.assertEqual(manual["status"], "busy")
        self.assertEqual(scheduled["status"], "busy")


class ManualSettlementTelegramTestCase(unittest.TestCase):
    def tearDown(self):
        manual_settlement._worker = None

    def test_unauthorized_user_is_rejected(self):
        with patch.object(manual_settlement, "is_allowed_telegram_user", return_value=False):
            reply = manual_settlement.start_manual_settlement("chat", "user", "name")
        self.assertIn("\u6ca1\u6709\u624b\u52a8\u7ed3\u7b97\u6743\u9650", reply)

    def test_authorized_request_starts_background_worker(self):
        worker = MagicMock()
        with patch.object(manual_settlement, "is_allowed_telegram_user", return_value=True), \
             patch.object(manual_settlement.threading, "Thread", return_value=worker) as thread_cls:
            reply = manual_settlement.start_manual_settlement("chat", "user", "name", agent_id=1)
        self.assertIn("\u5df2\u53d7\u7406", reply)
        thread_cls.assert_called_once()
        worker.start.assert_called_once()

    def test_worker_pushes_result_to_request_chat(self):
        result = {
            "status": "data_not_ready",
            "expected_trading_day": "20260626",
            "message": "data missing",
            "agents": {},
        }
        with patch.object(manual_settlement, "run_manual_settlement", return_value=result), \
             patch.object(manual_settlement.time, "sleep"), \
             patch.object(manual_settlement, "send_rich_message") as send:
            manual_settlement._run_and_notify("chat", None)
        send.assert_called_once()
        self.assertEqual(send.call_args.args[0], "chat")
        self.assertIn("20260626", send.call_args.args[1])


if __name__ == "__main__":
    unittest.main()
