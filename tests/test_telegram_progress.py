import unittest

from backend.telegram.polling import _TelegramProgress


class TelegramProgressTestCase(unittest.TestCase):
    def test_formats_public_agent_process_events(self):
        progress = _TelegramProgress("chat")

        self.assertEqual(progress._format_event({"type": "intent", "intent": "recommend"}), "🎯 识别意图: 推荐/选股")
        memory_line = progress._format_event({
            "type": "memory_context",
            "short_count": 8,
            "memory_count": 3,
            "has_session_summary": True,
            "session_summary": "用户最近连续追问多头均线股票。",
            "memory_preview": ["偏好短线右侧交易", "关注半导体和AI"],
            "recent_preview": ["user: 推荐利通电子怎么办", "assistant: 已分析利通电子风险"],
        })
        self.assertIn("🧠 加载记忆: 短期8条 / 长期3条 / 会话摘要有", memory_line)
        self.assertIn("摘要: 用户最近连续追问多头均线股票。", memory_line)
        self.assertIn("相关: 偏好短线右侧交易；关注半导体和AI", memory_line)
        self.assertIn("最近: user: 推荐利通电子怎么办 / assistant: 已分析利通电子风险", memory_line)

        decision_line = progress._format_event({
            "type": "llm_decision",
            "tools": ["recommend_search_stocks", "recommend_analyze_stock"],
        })
        self.assertIn("🤖 决策: 需要调用 recommend_search_stocks、recommend_analyze_stock", decision_line)
        self.assertIn("- recommend_search_stocks: 按自然语言策略筛选候选股票。", decision_line)
        self.assertEqual(
            progress._format_event({"type": "finalizing"}),
            "✍️ 正在整合证据，生成最终回复",
        )

    def test_formats_tool_args_without_values(self):
        progress = _TelegramProgress("chat")
        line = progress._format_event({
            "type": "tool_start",
            "tool": "recommend_search_stocks",
            "args": {"query": "多头均线", "max_results": 5},
        })

        self.assertIn("🛠️ 调用工具: recommend_search_stocks", line)
        self.assertIn("参数: query=多头均线, max_results=5", line)
        self.assertIn("用途: 按自然语言策略筛选候选股票。", line)

    def test_formats_tool_completion_with_public_source(self):
        progress = _TelegramProgress("chat")
        line = progress._format_event({
            "type": "tool",
            "tool": "recommend_get_macro_report",
            "result_preview": "今日市场偏 risk-off，涨停晋级率下降。",
        })

        self.assertIn("✅ 工具完成: recommend_get_macro_report", line)
        self.assertIn("来源: 读取每日宏观市场报告。", line)
        self.assertIn("摘要: 今日市场偏 risk-off，涨停晋级率下降。", line)


if __name__ == "__main__":
    unittest.main()
