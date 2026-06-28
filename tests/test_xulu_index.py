import os
import sqlite3
import tempfile
import unittest

from openpyxl import Workbook

from backend.db.repository import get_conn
from backend.db.schema import init_db
from backend.telegram.xulu_index import (
    format_xulu_index,
    get_xulu_index_summary,
    replace_xulu_index_history,
    upsert_xulu_index_daily,
)
from backend.telegram.xulu_index_importer import (
    LedgerEvent,
    backup_database,
    parse_gf_events,
    parse_legacy_events,
    replay_account,
)


class StaticPrices:
    def calendar(self, start_date, end_date):
        return ["2026-06-09", "2026-06-10"]

    def close(self, code, trade_date):
        prices = {"2026-06-09": 10.0, "2026-06-10": 11.0}
        return prices[trade_date], trade_date


class XuluIndexTestCase(unittest.TestCase):
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

    def test_upsert_uses_previous_asset_and_amends_same_day(self):
        conn = self._conn()
        upsert_xulu_index_daily(conn, "2026-06-09", 100.0, 0.0)
        row = upsert_xulu_index_daily(conn, "2026-06-10", 111.0, 10.0, 1.0)
        self.assertAlmostEqual(row["daily_return"], 0.1)
        self.assertAlmostEqual(row["index_value"], 1100.0)

        amended = upsert_xulu_index_daily(conn, "2026-06-10", 91.0, -10.0, 1.0)
        self.assertAlmostEqual(amended["daily_return"], -0.1)
        self.assertAlmostEqual(amended["index_value"], 900.0)
        conn.close()

    def test_summary_drawdown_win_rate_history_and_limit(self):
        conn = self._conn()
        rows = []
        for day, value, daily_return in [
            ("2026-06-01", 1000.0, 0.0),
            ("2026-06-02", 1100.0, 0.1),
            ("2026-06-03", 990.0, -0.1),
            ("2026-06-04", 1080.0, 0.0909090909),
        ]:
            rows.append({
                "trade_date": day, "index_value": value,
                "daily_return": daily_return,
                "cumulative_return": value / 1000.0 - 1.0,
                "total_asset": value, "daily_pnl": 0.0,
            })
        replace_xulu_index_history(conn, rows)
        summary = get_xulu_index_summary(conn, limit=2)
        self.assertAlmostEqual(summary["high_watermark"], 1100.0)
        self.assertAlmostEqual(summary["max_drawdown"], -0.1)
        self.assertAlmostEqual(summary["win_rate"], 2 / 3)
        self.assertEqual(len(summary["history"]), 2)
        self.assertIn("1,080.00", format_xulu_index(2, conn=conn))
        conn.close()

    def test_replace_removes_stale_rows_only_inside_payload_range(self):
        conn = self._conn()
        for day in ("2026-06-01", "2026-06-02", "2026-06-03", "2026-06-04"):
            upsert_xulu_index_daily(conn, day, 100.0, 0.0)
        payload = [{
            "trade_date": day, "index_value": 1000.0, "daily_return": 0.0,
            "cumulative_return": 0.0, "total_asset": 100.0, "daily_pnl": 0.0,
        } for day in ("2026-06-01", "2026-06-03")]
        replace_xulu_index_history(conn, payload)
        dates = [row[0] for row in conn.execute(
            "SELECT trade_date FROM xulu_index_daily ORDER BY trade_date"
        ).fetchall()]
        self.assertEqual(dates, ["2026-06-01", "2026-06-03", "2026-06-04"])
        conn.close()

    def test_legacy_and_gf_parsers_classify_flows_and_trades(self):
        legacy = tempfile.NamedTemporaryFile(delete=False)
        gf = tempfile.NamedTemporaryFile(delete=False, suffix=".xlsx")
        legacy.close()
        gf.close()
        try:
            header = "成交日期\t操作\t证券代码\t证券名称\t成交数量\t成交均价\t发生金额\n"
            body = (
                "20260227\t银证转入\t\t\t0\t0\t10000\n"
                "20260227\t证券买入\t000001\t平安银行\t100\t10\t-1005\n"
                "20260610\t银证转出\t\t\t0\t0\t-100\n"
            )
            with open(legacy.name, "wb") as handle:
                handle.write((header + body).encode("gb18030"))
            legacy_events = parse_legacy_events(legacy.name)
            self.assertEqual(len(legacy_events), 2)
            self.assertEqual(legacy_events[0].external_flow, 10000.0)
            self.assertEqual(legacy_events[1].quantity, 100.0)

            workbook = Workbook()
            sheet = workbook.active
            sheet.append(["时间", "日期", "业务", "代码", "名称", "价格", "数量", "金额"])
            sheet.append(["09:00", "20260610", "银行转存", "", "", "", "", "150000"])
            sheet.append(["09:31", "20260610", "证券卖出", "000001", "平安银行", "11", "100", "1095"])
            workbook.save(gf.name)
            gf_events = parse_gf_events(gf.name)
            self.assertEqual(gf_events[0].external_flow, 150000.0)
            self.assertEqual(gf_events[1].quantity, -100.0)
        finally:
            os.unlink(legacy.name)
            os.unlink(gf.name)

    def test_replay_values_holdings_and_carries_price(self):
        events = [
            LedgerEvent("2026-06-09", "", "test", "银证转入", "", "", 0, 0, 1000, 1000, 1),
            LedgerEvent("2026-06-09", "", "test", "证券买入", "000001", "平安银行", 50, 10, -505, 0, 2),
            LedgerEvent("2026-06-10", "", "test", "股息", "", "", 0, 0, 5, 0, 3),
        ]
        rows = replay_account(events, StaticPrices(), "2026-06-09", "2026-06-10")
        self.assertEqual(rows[0]["cash"], 495.0)
        self.assertEqual(rows[0]["market_value"], 500.0)
        self.assertEqual(rows[0]["net_flow"], 1000.0)
        self.assertEqual(rows[1]["total_asset"], 1050.0)
        self.assertEqual(rows[1]["net_flow"], 0.0)

    def test_backup_is_standalone_and_contains_latest_committed_data(self):
        conn = sqlite3.connect(self.tmp.name)
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute(
            "INSERT INTO xulu_index_daily "
            "(trade_date, index_value, daily_return, cumulative_return, total_asset, daily_pnl) "
            "VALUES ('2026-06-26', 1416.55, -0.047, 0.41655, 480271.11, -23717.42)"
        )
        conn.commit()
        conn.close()

        backup = backup_database(self.tmp.name)
        try:
            restored = sqlite3.connect(backup)
            value = restored.execute(
                "SELECT index_value FROM xulu_index_daily WHERE trade_date='2026-06-26'"
            ).fetchone()[0]
            integrity = restored.execute("PRAGMA integrity_check").fetchone()[0]
            restored.close()
            self.assertAlmostEqual(value, 1416.55)
            self.assertEqual(integrity, "ok")
        finally:
            os.unlink(backup)


if __name__ == "__main__":
    unittest.main()
