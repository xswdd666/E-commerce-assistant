import base64
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from app import server, seeany


class SeeAnyWorkflowTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.old = server.DATA, server.FILES, server.DB, server.SECRETS, server.ENV_FILE
        server.DATA = Path(self.tmp.name)
        server.FILES = server.DATA / "files"
        server.DB = server.DATA / "workbench.sqlite3"
        server.SECRETS = server.DATA / "provider-settings.json"
        server.ENV_FILE = server.DATA / ".env"
        server.ENV_FILE.write_text("SEEANY_API_KEY=test-only-key\n", encoding="utf-8")
        self.env = patch.dict(os.environ, {"SEEANY_API_KEY": ""})
        self.env.start()
        self.p = server.new_project("测试商品")
        fact = server.execute(self.p, "fact", {"field": "外观", "value": "白色机身", "status": "已知事实"})
        server.execute(self.p, "fact_review", {"id": fact["id"], "confirmed": True})
        server.execute(self.p, "brief_confirm", {})
        self.source = server.execute(self.p, "source", {"name": "product.png", "data": base64.b64encode(b"test image").decode()})
        server.save(self.p)

    def tearDown(self):
        self.env.stop()
        server.DATA, server.FILES, server.DB, server.SECRETS, server.ENV_FILE = self.old
        self.tmp.cleanup()

    def test_env_key_is_server_only_and_quote_blocks_unknown_cost(self):
        self.assertTrue(server.provider_public()["seeany_configured"])
        self.assertNotIn("test-only-key", str(server.provider_public()))
        quote = server.seeany_quote(self.p, {"kind": "master", "source_ids": [self.source["id"]], "views": "front", "view_label": "正面", "group": 1})
        self.assertIn("费用未知", quote["reason"])
        self.assertNotIn("test-only-key", str(self.p))

    def test_result_assets_reads_completed_work_images(self):
        task = {"id": "task-result", "provider": "SeeAny", "remote_id": "remote-result", "status": "远端运行中", "reason": "", "imported_urls": [], "description": "试图", "source_ids": [self.source["id"]], "input_versions": {}, "seeany_kind": "preview"}
        self.p["tasks"].append(task)
        response = {"code": 0, "msg": "success", "data": {"task_uuid": "remote-result", "status": "succeeded", "assets": [{"work_uuid": "work-123", "images": [{"url": "https://img1.seeany.com/final-image.png", "preview": "https://img1.seeany.com/preview-image.png", "width": 1024, "height": 1024}]}]}}
        with patch.object(seeany, "task_status", return_value=response), patch.object(seeany, "download_image", return_value=(b"image bytes", ".png")) as download:
            result = server.sync_seeany(self.p, task["id"])
        self.assertEqual(result["remote_status"], "succeeded")
        self.assertEqual(result["imported"], 1)
        self.assertEqual(len(self.p["assets"]), 1)
        download.assert_called_once_with("https://img1.seeany.com/final-image.png")

    def test_master_submit_and_status_import_are_idempotent(self):
        body = {"kind": "master", "source_ids": [self.source["id"]], "views": "front", "view_label": "正面", "group": 1, "estimate": "1"}
        quote = server.seeany_quote(self.p, body)
        self.assertEqual(quote["reason"], "")
        body.update(approved_fingerprint=quote["fingerprint"], request_id="same-request-123")
        with patch.object(server.seeany, "upload_image", return_value="https://img1.seeany.com/input.png") as upload, patch.object(server.seeany, "submit", return_value={"code": 0, "data": {"task_uuid": "wtask_123"}}) as submit:
            task = server.run_seeany(self.p, body)
            again = server.run_seeany(self.p, body)
        self.assertEqual(task["id"], again["id"])
        self.assertEqual(upload.call_count, 1)
        self.assertEqual(submit.call_count, 1)
        self.assertEqual(task["remote_id"], "wtask_123")
        self.assertEqual(server.cost_check(self.p, "三视图", 30), "超过该阶段预留额度")
        remote = {"code": 0, "data": {"status": "succeeded", "works": [{"assets": [{"url": "https://img1.seeany.com/result.png"}]}]}}
        with patch.object(server.seeany, "task_status", return_value=remote), patch.object(server.seeany, "download_image", return_value=(b"image bytes", ".png")) as download:
            server.sync_seeany(self.p, task["id"])
            server.sync_seeany(self.p, task["id"])
        self.assertEqual(download.call_count, 1)
        self.assertEqual(len(self.p["assets"]), 1)
        self.assertEqual(len(self.p["masters"]), 1)
        self.assertTrue(self.p["masters"][0]["inferred"])
        self.assertEqual(task["status"], "待审核")
        server.execute(self.p, "cost", {"task_id": task["id"], "amount": 0.6})
        with self.assertRaisesRegex(ValueError, "已记录实际费用"):
            server.execute(self.p, "cost", {"task_id": task["id"], "amount": 0.6})

    def test_group_plan_creates_reviewable_gallery(self):
        self.p["master_approval"] = {"group": 1, "brief_id": server.brief(self.p)["id"]}
        body = {"kind": "plan", "source_ids": [self.source["id"]], "requirement": "先做白底主图"}
        quote = server.seeany_quote(self.p, body)
        self.assertEqual(quote["estimate"], 0.10)
        self.assertEqual(quote["reason"], "")
        body.update(approved_fingerprint=quote["fingerprint"], request_id="plan-request-123")
        result = {"items": [{"cateName": "白底主图", "prompt": "商品居中，白色背景", "imgRatio": "1:1"}]}
        with patch.object(server.seeany, "upload_image", return_value="https://img1.seeany.com/input.png"), patch.object(server.seeany, "submit", return_value=result):
            task = server.run_seeany(self.p, body)
        self.assertEqual(task["status"], "待审核")
        self.assertEqual(self.p["gallery"][-1]["items"][0]["prompt"], "商品居中，白色背景")
        self.assertFalse(self.p["gallery"][-1]["approved"])

    def test_ambiguous_submit_is_saved_and_not_resent(self):
        body = {"kind": "master", "source_ids": [self.source["id"]], "views": "front", "view_label": "正面", "group": 1, "estimate": "1"}
        quote = server.seeany_quote(self.p, body)
        body.update(approved_fingerprint=quote["fingerprint"], request_id="failed-request-123")
        with patch.object(server.seeany, "upload_image", return_value="https://img1.seeany.com/input.png"), patch.object(server.seeany, "submit", side_effect=server.seeany.SeeAnyError("网络连接失败")) as submit:
            with self.assertRaisesRegex(ValueError, "不要重复提交"):
                server.run_seeany(self.p, body)
            existing = server.run_seeany(self.p, body)
        self.assertEqual(submit.call_count, 1)
        self.assertEqual(existing["status"], "待核对")
        self.assertEqual(server.cost_check(self.p, "三视图", 30), "超过该阶段预留额度")


if __name__ == "__main__":
    unittest.main()
