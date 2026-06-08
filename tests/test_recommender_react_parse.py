import unittest

from backend.telegram import recommender


class RecommenderReactParseTestCase(unittest.TestCase):
    def test_coerces_substantial_markdown_reply_when_json_missing(self):
        raw = """用户第三次追问沃格光电，我需要汇总工具证据。

---

📌 **沃格光电（603773.SH）还能上车吗？**

## 结论

不建议现在追入。玻璃基板方向确实有产业逻辑，但现价已经显著偏离 MA20，获利盘较重。

## 风险提示

以上分析仅供研究参考，不构成投资建议。
"""

        data = recommender._coerce_markdown_react_reply(raw, "沃格光电还能上车吗？")

        self.assertIn(data["intent"], {"followup", "recommend", "chat"})
        self.assertIn("沃格光电", data["reply"])
        self.assertNotIn("用户第三次追问", data["reply"])
        self.assertTrue(data["_coerced_from_markdown"])

    def test_does_not_coerce_short_process_text(self):
        data = recommender._coerce_markdown_react_reply("我需要先调用工具。", "沃格光电还能上车吗？")

        self.assertEqual(data, {})


if __name__ == "__main__":
    unittest.main()
