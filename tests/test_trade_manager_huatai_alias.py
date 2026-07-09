import unittest

from arbcore.traders.trade_manager import TradeManager


class HuataiAliasTradeManager(TradeManager):
    def __init__(self):
        self.tdx_available = False
        self.xtquant_available = False
        self.xt_trader = None
        self.xt_account = None
        self.xtconstant = None


class TradeManagerHuataiAliasTest(unittest.TestCase):
    def test_huatai_qmt_uses_xtquant_channel_readiness(self):
        manager = HuataiAliasTradeManager()

        success, message = manager.send_order(
            broker="huatai_qmt",
            action="BUY",
            symbol="510300.SH",
            volume=100,
            price=1.23,
        )

        self.assertFalse(success)
        self.assertIn("华泰QMT接口未就绪", message)


if __name__ == "__main__":
    unittest.main()
