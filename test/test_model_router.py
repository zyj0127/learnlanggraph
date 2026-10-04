# -*- coding: utf-8 -*-
"""分级模型路由测试：路由规则矩阵 + 档位归纳 + 轻量模型工厂回退。"""
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from agent.model_router import light_model_name, route_model_tier, usage_tier


class TestRouteModelTier(unittest.TestCase):
    """路由矩阵：light 需短 + 无政策/工具词 + 命中问候模式；其余一律 main。"""

    def test_greetings_go_light(self):
        for q in ("你好", "您好！", "在吗", "谢谢", "早上好", "再见",
                  "你是谁", "hi", "好的", "辛苦了"):
            self.assertEqual(route_model_tier(q), "light", q)

    def test_policy_questions_stay_main(self):
        for q in ("年假几天", "差旅住宿标准多少", "报销流程怎么走",
                  "病假能休几天呢", "社保基数是多少"):
            self.assertEqual(route_model_tier(q), "main", q)

    def test_tool_intent_stays_main(self):
        for q in ("帮我查余额", "我要请假", "开个在职证明", "我的工资单"):
            self.assertEqual(route_model_tier(q), "main", q)

    def test_long_or_unknown_stays_main(self):
        # 长句（>15 字）即使像闲聊也不分流
        self.assertEqual(route_model_tier("你好，我想了解一下咱们公司的各种制度"), "main")
        # 不命中间候模式的短句默认 main（保守：宁可少分流）
        self.assertEqual(route_model_tier("今天天气如何"), "main")
        self.assertEqual(route_model_tier("讲个笑话吧"), "main")
        self.assertEqual(route_model_tier(""), "main")
        self.assertEqual(route_model_tier(None), "main")

    def test_greeting_with_policy_word_stays_main(self):
        # 「你好，年假几天」——短但含政策词，不错分流
        self.assertEqual(route_model_tier("你好年假几天"), "main")


class TestUsageTier(unittest.TestCase):
    def _settings(self, light=""):
        return SimpleNamespace(llm_model_light=light)

    def test_no_light_model_always_main(self):
        with patch("config.get_settings", return_value=self._settings("")):
            self.assertEqual(usage_tier(["deepseek-chat"]), "main")
            self.assertEqual(usage_tier([]), "main")

    def test_all_light_is_light(self):
        with patch("config.get_settings", return_value=self._settings("light-m")):
            self.assertEqual(usage_tier(["light-m", "light-m"]), "light")

    def test_mixed_is_main(self):
        # 轻量闲聊 + 主模型审计混合 → main（埋点口径：本轮用了主模型）
        with patch("config.get_settings", return_value=self._settings("light-m")):
            self.assertEqual(usage_tier(["light-m", "deepseek-chat"]), "main")
            self.assertEqual(usage_tier(["deepseek-chat"]), "main")

    def test_light_model_name_reads_settings(self):
        with patch("config.get_settings", return_value=self._settings("light-m")):
            self.assertEqual(light_model_name(), "light-m")
        with patch("config.get_settings", return_value=self._settings("")):
            self.assertEqual(light_model_name(), "")


class TestLightLlmFallback(unittest.TestCase):
    def test_fallback_to_main_when_unconfigured(self):
        # 未配置 LLM_MODEL_LIGHT → get_light_chat_llm 回退主模型工厂
        import config

        with patch("config.get_settings",
                   return_value=SimpleNamespace(
                       llm_model_light="", llm_model="m", llm_api_key="k",
                       llm_base_url="http://x", deepseek_model_name_chat="",
                       deepseek_api_key="", deepseek_base_url="")), \
                patch("config.get_chat_llm", return_value="MAIN") as main_mock:
            self.assertEqual(config.get_light_chat_llm(), "MAIN")
            main_mock.assert_called_once()


if __name__ == "__main__":
    unittest.main()
