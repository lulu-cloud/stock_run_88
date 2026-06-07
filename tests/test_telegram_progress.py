import unittest

from backend.telegram.polling import _TelegramProgress


class TelegramProgressTestCase(unittest.TestCase):
    def test_formats_public_agent_process_events(self):
        progress = _TelegramProgress("chat")

        self.assertEqual(progress._format_event({"type": "intent", "intent": "recommend"}), "识别意图: 推荐/选股")
        self.assertEqual(
            progress._format_event({
                "type": "memory_context",
                "short_count": 8,
                "memory_count": 3,
                "has_session_summary": True,
            }),
            "加载记忆: 短期8条 / 长期3条 / 会话摘要有",
        )
        self.assertEqual(
            progress._format_event({
                "type": "llm_decision",
                "tools": ["recommend_search_stocks", "recommend_analyze_stock"],
            }),
            "决策: 需要调用 recommend_search_stocks、recommend_analyze_stock",
        )
        self.assertEqual(
            progress._format_event({"type": "finalizing"}),
            "正在整合证据，生成最终回复",
        )

    def test_formats_tool_args_without_values(self):
        progress = _TelegramProgress("chat")
        line = progress._format_event({
            "type": "tool_start",
            "tool": "recommend_search_stocks",
            "args": {"query": "多头均线", "max_results": 5},
        })

        self.assertEqual(line, "正在调用工具: recommend_search_stocks (query, max_results)")


if __name__ == "__main__":
    unittest.main()
