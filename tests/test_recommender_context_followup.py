import unittest
from unittest.mock import patch

from backend.telegram import recommender


class RecommenderContextFollowupTestCase(unittest.TestCase):
    def test_short_stock_question_uses_contextual_followup(self):
        with patch.object(recommender, "extract_stock_mentions", return_value=["002916.SZ"]):
            self.assertTrue(recommender._is_contextual_stock_followup("那深南电路呢？"))
            self.assertEqual(recommender._guess_intent("那深南电路呢？"), "followup")

    def test_contextual_followup_runs_before_strategy_fallback(self):
        with patch.object(recommender, "apply_inferred_preferences", return_value={}), \
             patch.object(recommender, "extract_stock_mentions", return_value=["002916.SZ"]), \
             patch.object(recommender, "_format_contextual_stock_followup", return_value="ctx-reply") as contextual:
            reply = recommender._handle_text_message_inner("那深南电路呢？", chat_id="chat", username="user")

        self.assertEqual(reply, "ctx-reply")
        contextual.assert_called_once()


if __name__ == "__main__":
    unittest.main()
