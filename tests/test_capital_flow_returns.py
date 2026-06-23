import unittest

from backend.trading.calculator import (
    calc_flow_adjusted_daily_pnl,
    calc_flow_adjusted_daily_return,
)


class CapitalFlowReturnTestCase(unittest.TestCase):
    def test_deposit_is_not_counted_as_daily_profit(self):
        self.assertEqual(calc_flow_adjusted_daily_pnl(460000, 260000, 200000), 0.0)
        self.assertEqual(calc_flow_adjusted_daily_return(460000, 260000, 200000), 0.0)

    def test_profit_after_deposit_uses_adjusted_start_assets(self):
        self.assertEqual(calc_flow_adjusted_daily_pnl(461000, 260000, 200000), 1000.0)
        self.assertAlmostEqual(calc_flow_adjusted_daily_return(461000, 260000, 200000), 0.2174)

    def test_withdrawal_is_not_counted_as_daily_loss(self):
        self.assertEqual(calc_flow_adjusted_daily_pnl(210000, 260000, -50000), 0.0)
        self.assertEqual(calc_flow_adjusted_daily_return(210000, 260000, -50000), 0.0)


if __name__ == "__main__":
    unittest.main()
