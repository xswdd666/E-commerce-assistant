import base64
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from commerce_studio.core import Store
from commerce_studio import service


class CommerceStudioTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.store = Store(Path(self.temp.name))
        self.project = self.store.create("测试电热杯")

    def tearDown(self):
        self.temp.cleanup()

    def approved_project(self):
        source = self.store.add_source(self.project, "front.png", "image/png", base64.b64encode(b"image bytes").decode())
        fact = self.store.add_fact(self.project, "颜色", "白色", source["id"], "已知事实")
        self.store.confirm_brief(self.project, [fact["id"]])
        candidates = []
        for view in ("正面", "侧面", "背面"):
            candidate = self.store.add_source(self.project, view + ".png", "image/png", base64.b64encode(view.encode()).decode())
            candidate.update(origin="SeeAny candidate", candidate_group=1, view_label=view)
            candidates.append(candidate["id"])
        self.store.save(self.project)
        self.store.confirm_master(self.project, candidates)
        return source

    def test_unverified_fact_cannot_enter_brief(self):
        fact = self.store.add_fact(self.project, "容量", "不确定")
        with self.assertRaisesRegex(ValueError, "已知事实"):
            self.store.confirm_brief(self.project, [fact["id"]])
        self.assertEqual(self.project["brief_versions"], [])

    def test_raw_photo_cannot_be_approved_as_complete_master(self):
        source = self.store.add_source(self.project, "front.png", "image/png", base64.b64encode(b"photo").decode())
        fact = self.store.add_fact(self.project, "颜色", "白色", source["id"], "已知事实")
        self.store.confirm_brief(self.project, [fact["id"]])
        with self.assertRaisesRegex(ValueError, "正面、侧面和背面"):
            self.store.confirm_master(self.project, [source["id"]])

    def test_master_candidate_submission_and_sync_keep_reference_and_are_idempotent(self):
        original = self.store.add_source(self.project, "photo.png", "image/png", base64.b64encode(b"original").decode())
        fact = self.store.add_fact(self.project, "颜色", "白色", original["id"], "已知事实")
        self.store.confirm_brief(self.project, [fact["id"]])
        body = {"group": 1, "view_label": "正面", "views": "front", "source_id": original["id"]}
        quote = service.master_quote(self.store, self.project, body)
        run_body = {**body, "approved_fingerprint": quote["fingerprint"], "request_id": "master-request-123"}
        with patch.object(service, "provider_key", return_value="test-key"), patch.object(service.seeany, "upload_image", return_value="https://seeany.com/upload") as upload, patch.object(service.seeany, "submit", return_value={"data": {"task_uuid": "remote-123"}}) as submit:
            task = service.run_master(self.store, self.project, run_body)
            again = service.run_master(self.store, self.project, run_body)
        self.assertEqual(task["id"], again["id"])
        upload.assert_called_once()
        submit.assert_called_once()
        response = {"data": {"task": {"status": "succeeded"}, "works": [{"url": "https://cdn.seeany.com/image.png"}]}}
        with patch.object(service, "provider_key", return_value="test-key"), patch.object(service.seeany, "task_status", return_value=response), patch.object(service.seeany, "download_image", return_value=(b"generated", ".png")) as download:
            service.sync(self.store, self.project, task["id"])
            service.sync(self.store, self.project, task["id"])
        download.assert_called_once()
        candidate = next(s for s in self.project["sources"] if s["id"] == task["asset_ids"][0])
        self.assertEqual(candidate["view_label"], "正面")
        self.assertEqual(candidate["reference_ids"], [original["id"]])

    def test_typed_edges_only_change_graph_and_quote_uses_approved_versions(self):
        source = self.approved_project()
        prompt = self.store.add_node(self.project, "prompt", {"text": "白底产品图"})
        image = self.store.add_node(self.project, "source_image", {"source_id": source["id"]})
        task = self.store.add_node(self.project, "image_task")
        with self.assertRaisesRegex(ValueError, "不兼容"):
            self.store.connect(self.project, prompt["id"], task["id"], "reference")
        self.store.connect(self.project, prompt["id"], task["id"], "text")
        self.store.connect(self.project, image["id"], task["id"], "reference")
        self.assertEqual(self.project["tasks"], [])
        quote = service.quote(self.store, self.project, task["id"])
        self.assertEqual(quote["provider"], "SeeAny")
        self.assertIsNone(quote["estimate"])
        self.assertEqual(quote["input_snapshot"]["brief_id"], self.project["brief_versions"][-1]["id"])
        self.assertEqual(quote["input_snapshot"]["master_id"], self.project["master_versions"][-1]["id"])

    def test_changed_inputs_require_fresh_approval_and_same_request_is_not_resubmitted(self):
        prompt = self.store.add_node(self.project, "prompt", {"text": "三个广告方向"})
        task = self.store.add_node(self.project, "chat_task")
        self.store.connect(self.project, prompt["id"], task["id"], "text")
        quote = service.quote(self.store, self.project, task["id"])
        with patch.object(service, "provider_key", return_value="test-key"), patch.object(service, "deepseek_complete", return_value={"id": "remote", "text": "草案", "usage": {}}) as remote:
            with self.assertRaisesRegex(ValueError, "重新查看快照"):
                service.run(self.store, self.project, task["id"], {"approved_fingerprint": "old", "request_id": "request-123"})
            result = service.run(self.store, self.project, task["id"], {"approved_fingerprint": quote["fingerprint"], "request_id": "request-123"})
            again = service.run(self.store, self.project, task["id"], {"approved_fingerprint": quote["fingerprint"], "request_id": "request-123"})
        self.assertEqual(result["id"], again["id"])
        remote.assert_called_once()
        self.assertEqual(len(self.store.load(self.project["id"])["tasks"]), 1)

    def test_chat_reads_only_confirmed_brief_and_keeps_discussion_out_of_facts(self):
        pending = self.store.add_fact(self.project, "容量", "未知数值")
        confirmed = self.store.add_fact(self.project, "颜色", "白色", status="已知事实")
        self.store.confirm_brief(self.project, [confirmed["id"]])
        offer = service.chat_quote(self.project, {"prompt": "给出广告方向"})
        body = {"prompt": "给出广告方向", "approved_fingerprint": offer["fingerprint"], "request_id": "chat-request-123"}
        with patch.object(service, "provider_key", return_value="test-key"), patch.object(service, "deepseek_complete", return_value={"id": "remote", "text": "方向草稿", "usage": {}}) as remote:
            service.run_chat(self.store, self.project, body)
        messages = remote.call_args.args[1]
        self.assertIn("白色", messages[0]["content"])
        self.assertNotIn("未知数值", messages[0]["content"])
        self.assertEqual(len(self.project["facts"]), 2)
        self.assertEqual(self.project["chat"][0]["reply"], "方向草稿")

    def test_video_is_not_submitted_without_verified_flova_reference_upload(self):
        source = self.approved_project()
        prompt = self.store.add_node(self.project, "prompt", {"text": "产品展示"})
        image = self.store.add_node(self.project, "source_image", {"source_id": source["id"]})
        task = self.store.add_node(self.project, "video_task")
        self.store.connect(self.project, prompt["id"], task["id"], "text")
        self.store.connect(self.project, image["id"], task["id"], "reference")
        quote = service.quote(self.store, self.project, task["id"])
        self.assertFalse(quote["ready"])
        with self.assertRaisesRegex(ValueError, "Flova"):
            service.run(self.store, self.project, task["id"], {"approved_fingerprint": quote["fingerprint"], "request_id": "request-456"})
        self.assertFalse(self.project["tasks"])

    def test_canvas_preview_uses_snapshot_and_saves_original_reference(self):
        self.approved_project()
        _, master_raw = self.store.source_bytes(self.project, self.project["master_versions"][-1]["asset_ids"][0])
        body = {"canvas_project_id": "canvas-1", "config_node_id": "config-1", "reference_node_id": "image-1",
                "connection_ids": ["edge-image", "edge-text"], "prompt": "白底实拍风格", "ratio": "1:1",
                "reference_data_url": "data:image/png;base64," + base64.b64encode(master_raw).decode()}
        offer = service.preview_quote(self.project, body)
        with patch.object(service, "provider_key", return_value="test-key"), patch.object(service.seeany, "upload_image", return_value="https://seeany.com/upload"), patch.object(service.seeany, "submit", return_value={"data": {"task_uuid": "preview-remote"}}) as submit:
            task = service.run_preview(self.store, self.project, {**body, "approved_fingerprint": offer["fingerprint"], "request_id": "preview-request-123"})
        payload = submit.call_args.args[2]
        self.assertEqual(payload["prompt"], "白底实拍风格")
        self.assertEqual(task["input_snapshot"]["connection_ids"], ["edge-image", "edge-text"])
        source, raw = self.store.source_bytes(self.project, task["source_id"])
        self.assertEqual(source["canvas_node_id"], "image-1")
        self.assertEqual(raw, master_raw)


if __name__ == "__main__":
    unittest.main()
