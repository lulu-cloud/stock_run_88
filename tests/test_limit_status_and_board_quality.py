import unittest

import pandas as pd

from backend.data.loader import compute_limit_status, daily_price_adjustment
from backend.macro.report import _limit_pool_analytics
from backend.trading.rules import is_limit_down, is_limit_up, is_main_board


class LimitStatusAndBoardQualityTestCase(unittest.TestCase):
    def test_st_stock_uses_five_percent_limit_threshold(self):
        df = pd.DataFrame([
            {"trade_date": "20260605", "pct_chg": 4.95, "is_st": 1},
            {"trade_date": "20260605", "pct_chg": 9.0, "is_st": 0},
            {"trade_date": "20260605", "pct_chg": 9.85, "is_st": 0},
            {"trade_date": "20260605", "pct_chg": -4.95, "is_st": 1},
        ])

        out = compute_limit_status(df)

        self.assertTrue(bool(out.iloc[0]["is_limit_up"]))
        self.assertFalse(bool(out.iloc[1]["is_limit_up"]))
        self.assertTrue(bool(out.iloc[2]["is_limit_up"]))
        self.assertTrue(bool(out.iloc[3]["is_limit_down"]))
        self.assertEqual(float(out.iloc[0]["limit_threshold_pct"]), 5.0)
        self.assertEqual(float(out.iloc[1]["limit_threshold_pct"]), 10.0)

    def test_main_board_filters_official_st_flag(self):
        self.assertFalse(is_main_board("600000.SH", "浦发银行", "主板", "上市", 1))
        self.assertFalse(is_main_board("600000.SH", "ST浦发", "主板", "上市", 0))
        self.assertTrue(is_main_board("600000.SH", "浦发银行", "主板", "上市", 0))
        self.assertTrue(is_main_board("600000.SH", "ST浦发", "主板", "上市", 1, allow_st=True))
        self.assertTrue(is_limit_up(4.95, True))
        self.assertFalse(is_limit_up(9.0, False))
        self.assertTrue(is_limit_down(-4.95, True))

    def test_daily_price_basis_is_declared_as_qfq(self):
        self.assertEqual(daily_price_adjustment(), "qfq")

    def test_limit_pool_analytics_uses_akshare_pool_fields(self):
        snapshot = {
            "limit_up_pool": {
                "items": [
                    {
                        "代码": "600001",
                        "名称": "强势一",
                        "涨跌幅": 10.0,
                        "封板资金": 100_000_000,
                        "首次封板时间": 92501,
                        "最后封板时间": 92501,
                        "炸板次数": 0,
                        "连板数": 2,
                        "所属行业": "通信设备",
                    }
                ]
            },
            "previous_limit_up_pool": {
                "items": [
                    {"代码": "600001", "名称": "强势一", "涨跌幅": 10.0, "昨日连板数": 1},
                    {"代码": "600002", "名称": "退潮二", "涨跌幅": -6.0, "昨日连板数": 1},
                ]
            },
            "broken_limit_up_pool": {"items": [{"代码": "600002", "名称": "退潮二"}]},
        }

        analytics = _limit_pool_analytics(snapshot)

        self.assertEqual(analytics["board_stage_counts"]["二板"], 1)
        self.assertEqual(analytics["promotion"]["previous_limit_up_count"], 2)
        self.assertEqual(analytics["promotion"]["promoted_count"], 1)
        self.assertEqual(analytics["promotion"]["killed_count"], 1)
        quality = analytics["board_quality_top"][0]
        self.assertEqual(quality["first_seal_time"], "09:25:01")
        self.assertEqual(quality["last_seal_time"], "09:25:01")
        self.assertEqual(quality["broken_count"], 0)
        self.assertEqual(quality["board_stage"], "二板")


if __name__ == "__main__":
    unittest.main()
