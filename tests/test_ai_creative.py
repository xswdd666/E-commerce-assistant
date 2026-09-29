import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from app import server


class CreativeAiTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.old = server.DATA, server.FILES, server.DB, server.SECRETS
        server.DATA = Path(self.tmp.name)
        server.FILES = server.DATA / "files"
        server.DB = server.DATA / "workbench.sqlite3"
        server.SECRETS = server.DATA / "provider-settings.json"
        self.p = server.new_project("白色商品")
        fact = server.execute(self.p, "fact", {"field": "外观", "value": "白色机身", "status": "已知事实"})
        server.execute(self.p, "fact_review", {"id": fact["id"], "confirmed": True})
        server.execute(self.p, "brief_confirm", {})
        self.p["master_approval"] = {"group": 1}
        direction = server.execute(self.p, "direction", {"title": "展示外观", "opening": "产品近景"})
        server.execute(self.p, "direction_approve", {"id": direction["id"]})
        self.config = patch.object(server, "provider_settings", return_value={"deepseek_api_key": "test-key", "input_cny_per_m": 1, "output_cny_per_m": 2})
        self.config.start()

    def tearDown(self):
        self.config.stop()
        server.DATA, server.FILES, server.DB, server.SECRETS = self.old
        self.tmp.cleanup()

    def run_mock(self, kind, response, request_id):
        quote = server.ai_quote(self.p, kind, {})
        self.assertEqual(quote["model"], "deepseek-flash")
        self.assertFalse(quote["reason"])
        with patch.object(server, "deepseek_complete", return_value={"id": request_id, "text": json.dumps(response, ensure_ascii=False), "usage": {"prompt_tokens": 80, "completion_tokens": 60}}):
            return server.run_ai(self.p, kind, {"approved_quote": quote["estimate"], "request_id": request_id})

    def test_script_storyboard_and_prompt_are_new_unapproved_versions(self):
        self.run_mock("script", {"script": "白色机身近景，字幕说明外观。"}, "script-request-1")
        self.assertEqual(len(self.p["script_versions"]), 1)
        self.assertIsNone(self.p["script_approval"])
        server.execute(self.p, "script_approve", {"id": self.p["script_versions"][-1]["id"]})
        self.run_mock("storyboard", {"shots": [{"description": "白色机身特写", "duration": 3, "caption": "白色机身"}]}, "board-request-1")
        self.assertEqual(self.p["storyboard_versions"][-1]["shots"][0]["ratio"], "9:16")
        self.assertIsNone(self.p["storyboard_approval"])
        result = self.run_mock("prompt_generate", {"fields": {"identity": "白色机身", "scene": "干净背景", "purpose": "商品主图"}}, "prompt-request-1")
        self.assertEqual(result["task"]["status"], "待审核")
        self.assertEqual(self.p["prompts"][-1]["fields"]["ratio"], "9:16")
        self.assertEqual(len(self.p["costs"]), 3)

    def test_storyboard_requires_approved_script_and_rejects_invalid_output(self):
        with self.assertRaisesRegex(ValueError, "批准母版和脚本"):
            server.ai_request(self.p, "storyboard", {})
        script = server.execute(self.p, "script", {"text": "展示白色机身"})
        server.execute(self.p, "script_approve", {"id": script["id"]})
        quote = server.ai_quote(self.p, "storyboard", {})
        with patch.object(server, "deepseek_complete", return_value={"id": "bad", "text": '{"shots":[{"description":"画面","duration":0}]}', "usage": {}}):
            with self.assertRaisesRegex(ValueError, "单镜时长"):
                server.run_ai(self.p, "storyboard", {"approved_quote": quote["estimate"], "request_id": "invalid-board-1"})
        self.assertEqual(self.p["storyboard_versions"], [])
