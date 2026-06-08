import os
import tempfile
import unittest
from unittest.mock import patch

from backend.search_agent import searcher


BAD_MD = """# 沃格光电 (603773.SH)

## 主营业务
无法获取沃格光电（603773.SH）的详细信息

**搜索限制说明：** 当前网络搜索服务已达到使用限额（5小时内60/60次），暂时无法获取最新公开资料。

## 主要产品与服务体系
未检索到明确信息。
"""


GOOD_MD = """# 沃格光电 (603773.SH)

## 主营业务
沃格光电主营光电玻璃精加工、显示触控模组相关产品，并围绕玻璃基材、Mini LED、先进封装相关材料和器件拓展。

## 主要产品与服务体系
- 光电玻璃精加工
- 显示触控模组
- 玻璃基先进封装材料
"""


class CompanyBusinessCacheTestCase(unittest.TestCase):
    def test_bad_cache_is_not_fresh_or_readable_by_default(self):
        with tempfile.TemporaryDirectory() as tmp:
            with patch.object(searcher, "COMPANY_BUSINESS_DIR", tmp):
                path = os.path.join(tmp, "603773.SH_沃格光电_20260608.md")
                with open(path, "w", encoding="utf-8") as f:
                    f.write(BAD_MD)

                freshness = searcher.get_freshness("603773.SH")

        self.assertTrue(freshness["is_bad"])
        self.assertFalse(freshness["is_fresh"])
        with tempfile.TemporaryDirectory() as tmp:
            with patch.object(searcher, "COMPANY_BUSINESS_DIR", tmp):
                path = os.path.join(tmp, "603773.SH_沃格光电_20260608.md")
                with open(path, "w", encoding="utf-8") as f:
                    f.write(BAD_MD)
                self.assertIsNone(searcher.get_cached("603773.SH"))

    def test_older_reliable_cache_beats_newer_bad_cache(self):
        with tempfile.TemporaryDirectory() as tmp:
            with patch.object(searcher, "COMPANY_BUSINESS_DIR", tmp):
                with open(os.path.join(tmp, "603773.SH_沃格光电_20260608.md"), "w", encoding="utf-8") as f:
                    f.write(BAD_MD)
                with open(os.path.join(tmp, "603773.SH_沃格光电_20260601.md"), "w", encoding="utf-8") as f:
                    f.write(GOOD_MD)

                content = searcher.get_cached("603773.SH")
                freshness = searcher.get_freshness("603773.SH")

        self.assertIn("光电玻璃精加工", content)
        self.assertFalse(freshness["is_bad"])
        self.assertEqual(freshness["date"], "2026-06-01")


if __name__ == "__main__":
    unittest.main()
