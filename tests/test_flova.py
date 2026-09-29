import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from app import server


class FlovaIntegrationTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.old = server.DATA, server.FILES, server.DB, server.SECRETS, server.ENV_FILE
        server.DATA = Path(self.tmp.name)
        server.FILES = server.DATA / "files"
        server.DB = server.DATA / "workbench.sqlite3"
        server.SECRETS = server.DATA / "provider-settings.json"
        server.ENV_FILE = server.DATA / ".env"
        server.ENV_FILE.write_text("DEEPSEEK_API_KEY=test-deepseek-key\n", encoding="utf-8")
        self.p = server.new_project("测试视频")
        server.save(self.p)

    def tearDown(self):
        server.DATA, server.FILES, server.DB, server.SECRETS, server.ENV_FILE = self.old
        self.tmp.cleanup()

    def test_deepseek_env_is_loaded_without_exposing_key(self):
        with patch.dict(os.environ, {"DEEPSEEK_API_KEY": ""}):
            self.assertTrue(server.provider_public()["deepseek_configured"])
            self.assertNotIn("test-deepseek-key", str(server.provider_public()))

    def test_create_run_and_recover_same_flova_project(self):
        with patch.object(server.flova, "create_project", return_value={"project_id": "remote-1", "project_url": "https://flova.ai/p/remote-1"}) as create:
            server.flova_action(self.p, "create", {})
        self.assertEqual(create.call_count, 1)
        with patch.object(server.flova, "run", return_value={"terminal": True, "stream_chat_id": "stream-1", "assistant_messages": []}) as run:
            server.flova_action(self.p, "run", {"prompt": "制作 15 秒竖屏广告"})
        run.assert_called_once_with("remote-1", "制作 15 秒竖屏广告")
        self.assertEqual(self.p["external"]["flova_run_state"], "已返回")
        with patch.object(server.flova, "recover", return_value={"terminal": True, "stream_chat_id": "stream-1"}), patch.object(server.flova, "run_result", return_value={"terminal": True, "assistant_messages": ["done"]}) as result:
            server.flova_action(self.p, "recover", {})
        result.assert_called_once_with("remote-1", "stream-1")

    def test_failed_run_requires_recovery_before_resend(self):
        self.p["external"]["flova_project_id"] = "remote-1"
        with patch.object(server.flova, "run", side_effect=ValueError("network")) as run:
            with self.assertRaisesRegex(ValueError, "network"):
                server.flova_action(self.p, "run", {"prompt": "制作广告"})
            with self.assertRaisesRegex(ValueError, "恢复状态"):
                server.flova_action(self.p, "run", {"prompt": "制作广告"})
        self.assertEqual(run.call_count, 1)
