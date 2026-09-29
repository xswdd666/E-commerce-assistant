import base64
import io
import hashlib
import json
import shutil
import sqlite3
from contextlib import closing
import subprocess
import tempfile
import threading
import unittest
import zipfile
from pathlib import Path
from unittest.mock import patch
from http.server import ThreadingHTTPServer
from urllib.request import Request, urlopen

from PIL import Image

from commerce_studio.core import Store
from commerce_studio import service
from commerce_studio import backup, delivery, finishing, flova_flow, gallery, http as bridge, legacy, prompts, video_export


class CommerceStudioTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.store = Store(Path(self.temp.name))
        self.project = self.store.create("测试电热杯")

    def tearDown(self):
        self.temp.cleanup()

    def test_legacy_import_copies_sources_and_facts_without_touching_old_project(self):
        root = Path(self.temp.name)
        db_path = root / "old.sqlite3"
        files = root / "old-files"
        old_id = "a" * 12
        (files / old_id).mkdir(parents=True)
        raw = b"legacy image"
        (files / old_id / f"{'b' * 12}.png").write_bytes(raw)
        old = {"id": old_id, "name": "旧项目", "updated": "2026-09-29", "sources": [{"id": "b" * 12,
               "name": "front.png", "path": f"{'b' * 12}.png", "mime": "image/png", "sha256": hashlib.sha256(raw).hexdigest()}],
               "facts": [{"id": "c" * 12, "field": "颜色", "value": "银色", "status": "已知事实", "source_ids": ["b" * 12]}],
               "masters": [{"id": "d" * 12}], "settings": {"unattended_limit": 30}}
        with closing(sqlite3.connect(db_path)) as db:
            db.execute("CREATE TABLE projects (id TEXT PRIMARY KEY, name TEXT, updated TEXT, state TEXT)")
            db.execute("INSERT INTO projects VALUES (?,?,?,?)", (old_id, old["name"], old["updated"], json.dumps(old)))
            db.commit()
        self.assertEqual(len(legacy.list_projects(db_path)), 1)
        imported = legacy.import_project(self.store, db_path, files, old_id)
        self.assertEqual(self.store.source_bytes(imported, imported["sources"][0]["id"])[1], raw)
        self.assertEqual(imported["facts"][0]["status"], "已知事实")
        self.assertEqual(imported["master_versions"], [])
        self.assertEqual(imported["legacy_import"]["archive"]["masters"], old["masters"])
        self.assertEqual(legacy.import_project(self.store, db_path, files, old_id)["id"], imported["id"])
        self.assertEqual((files / old_id / f"{'b' * 12}.png").read_bytes(), raw)

    def approved_project(self):
        source = self.store.add_source(self.project, "front.png", "image/png", base64.b64encode(b"image bytes").decode())
        fact = self.store.add_fact(self.project, "颜色", "白色", source["id"], "已知事实")
        self.store.confirm_brief(self.project, [fact["id"]])
        candidates = []
        for view in ("正面", "侧面", "背面"):
            candidate = self.store.add_source(self.project, view + ".png", "image/png", base64.b64encode(view.encode()).decode())
            candidate.update(origin="SeeAny candidate", candidate_group=1, view_label=view, brief_id=self.project["brief_versions"][-1]["id"])
            candidates.append(candidate["id"])
        self.store.save(self.project)
        self.store.confirm_master(self.project, candidates)
        return source

    def test_unverified_fact_cannot_enter_brief(self):
        fact = self.store.add_fact(self.project, "容量", "不确定")
        with self.assertRaisesRegex(ValueError, "已知事实"):
            self.store.confirm_brief(self.project, [fact["id"]])
        self.assertEqual(self.project["brief_versions"], [])

    def test_deepseek_extraction_is_reviewed_before_brief(self):
        source = self.store.add_source(self.project, "merchant.txt", "text/plain", base64.b64encode("杯体为白色".encode()).decode())
        offer = service.facts_quote(self.project, {"source_id": source["id"]})
        response = {"id": "remote", "text": '{"facts":[{"field":"颜色","value":"白色"}]}', "usage": {}}
        with patch.object(service, "provider_key", return_value="test-key"), patch.object(service, "deepseek_complete", return_value=response):
            service.run_facts(self.store, self.project, {"source_id": source["id"], "approved_fingerprint": offer["fingerprint"], "request_id": "facts-request-123"})
        fact = self.project["facts"][0]
        self.assertEqual(fact["status"], "待核实")
        self.assertEqual(fact["source_id"], source["id"])
        with self.assertRaisesRegex(ValueError, "已知事实"):
            self.store.confirm_brief(self.project, [fact["id"]])
        self.store.review_fact(self.project, fact["id"], "已知事实")
        version = self.store.confirm_brief(self.project, [fact["id"]])
        self.assertEqual(version["facts"][0]["value"], "白色")

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
        self.assertEqual(candidate["brief_id"], self.project["brief_versions"][-1]["id"])
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

    def test_three_directions_and_storyboard_require_stepwise_approval(self):
        self.approved_project()
        offer = service.directions_quote(self.project)
        directions = [{"title": str(i), "audience": "年轻人", "opening": "开头", "selling_point": "白色外观", "ending": "结尾"} for i in range(3)]
        with patch.object(service, "provider_key", return_value="test-key"), patch.object(service, "deepseek_complete", return_value={"id": "remote", "text": __import__("json").dumps({"directions": directions}), "usage": {}}):
            service.run_directions(self.store, self.project, {"approved_fingerprint": offer["fingerprint"], "request_id": "directions-123"})
        self.assertEqual(len(self.project["directions"]), 3)
        direction = self.store.approve_direction(self.project, self.project["directions"][0]["id"])
        script = self.store.add_script(self.project, "15 秒静音可理解脚本")
        self.store.approve_script(self.project, script["id"])
        master_id = self.project["master_versions"][-1]["asset_ids"][0]
        draft = self.store.add_storyboard(self.project, [{"visual": "产品正面展示", "duration": 3, "reference_asset_id": master_id}])
        self.store.approve_storyboard(self.project, draft["id"])
        self.assertEqual(self.project["storyboard_approval"], draft["id"])
        self.assertEqual(direction["brief_id"], self.project["brief_versions"][-1]["id"])
        new_fact = self.store.add_fact(self.project, "用途", "加热", status="已知事实")
        self.store.confirm_brief(self.project, [new_fact["id"]])
        with self.assertRaisesRegex(ValueError, "上游版本已变化"):
            self.store.add_script(self.project, "旧方向的新脚本")

    def test_flova_run_uses_approved_storyboard_and_blocks_duplicate_round(self):
        self.approved_project()
        with self.assertRaisesRegex(ValueError, "批准完整分镜"):
            flova_flow.quote(self.project)
        self.project["directions"].append({"id": "direction-1", "brief_id": self.project["brief_versions"][-1]["id"], "master_id": self.project["master_versions"][-1]["id"]})
        self.store.approve_direction(self.project, "direction-1")
        script = self.store.add_script(self.project, "15 秒脚本")
        self.store.approve_script(self.project, script["id"])
        shot = {"visual": "产品正面", "duration": 3, "reference_asset_id": self.project["master_versions"][-1]["asset_ids"][0]}
        storyboard = self.store.add_storyboard(self.project, [shot])
        self.store.approve_storyboard(self.project, storyboard["id"])
        with patch.object(flova_flow.flova, "create_project", return_value={"project_id": "remote-project", "project_url": "https://flova.tv/p/remote-project"}):
            flova_flow.create_project(self.store, self.project)
        offer = flova_flow.quote(self.project)
        with patch.object(flova_flow.threading, "Thread") as thread:
            task = flova_flow.run(self.store, self.project, {"approved_fingerprint": offer["fingerprint"], "request_id": "flova-request-123"})
            self.assertEqual(flova_flow.run(self.store, self.project, {"approved_fingerprint": offer["fingerprint"], "request_id": "flova-request-123"})["id"], task["id"])
            with self.assertRaisesRegex(ValueError, "尚未核对"):
                flova_flow.run(self.store, self.project, {"approved_fingerprint": offer["fingerprint"], "request_id": "flova-request-456"})
            thread.assert_called_once()
        flova_flow.ACTIVE.discard(self.project["id"])

    def test_flova_recovery_never_accepts_unrelated_current_run(self):
        task = {"id": "local-task", "provider": "Flova", "status": "待核对", "remote_id": None,
                "remote_started": "2026-09-29T00:00:00+00:00", "input_snapshot": {"flova_project_id": "remote-project"}}
        self.project["tasks"].append(task)
        with patch.object(flova_flow.flova, "recover", return_value={"terminal": True, "stream_chat_id": "older-run"}), patch.object(flova_flow.flova, "run_result") as detail:
            recovered = flova_flow.recover(self.store, self.project, task["id"])
        self.assertEqual(recovered["status"], "待核对")
        self.assertIsNone(recovered["remote_id"])
        self.assertEqual(recovered["recovery_candidate"]["stream_chat_id"], "older-run")
        detail.assert_not_called()

    def test_flova_recovery_uses_known_remote_id(self):
        task = {"id": "local-task", "provider": "Flova", "status": "待核对", "remote_id": "this-run",
                "input_snapshot": {"flova_project_id": "remote-project"}}
        self.project["tasks"].append(task)
        with patch.object(flova_flow.flova, "recover", return_value={"terminal": True, "stream_chat_id": "older-run"}), patch.object(flova_flow.flova, "run_result", return_value={"status": "completed", "terminal": True, "stream_chat_id": "this-run", "pending_actions": []}) as detail:
            recovered = flova_flow.recover(self.store, self.project, task["id"])
        self.assertEqual(recovered["status"], "待审核")
        detail.assert_called_once_with("remote-project", "this-run")

    def test_flova_terminal_failure_is_not_presented_as_reviewable_video(self):
        task = {}
        flova_flow._apply_run_result(task, {"status": "failed", "terminal": True, "stream_chat_id": "failed-run", "pending_actions": []})
        self.assertEqual(task["status"], "失败")

    def test_flova_export_requires_review_and_keeps_final_video_local(self):
        self.project["external"]["flova_project_id"] = "remote-project"
        run_task = {"id": "creative-run", "kind": "video", "provider": "Flova", "status": "待审核", "remote_id": "stream-123",
                    "pending_actions": [], "estimate": None, "actual": None, "currency": None, "created": "2026-09-29T00:00:00+00:00"}
        self.project["tasks"].append(run_task)
        with self.assertRaisesRegex(ValueError, "批准导出"):
            video_export.quote(self.project)
        flova_flow.approve_video(self.store, self.project, "creative-run")
        with patch.object(video_export.flova, "readiness", return_value={"can_export": True}):
            offer = video_export.quote(self.project)
            with patch.object(video_export.threading, "Thread") as thread:
                export_task = video_export.run(self.store, self.project, {"approved_fingerprint": offer["fingerprint"], "request_id": "export-request-123"})
            thread.assert_called_once()
        with patch.object(video_export, "urlopen", side_effect=lambda *_args, **_kwargs: io.BytesIO(b"valid nonempty mp4 bytes")):
            video_export._finish(self.store, self.project["id"], export_task["id"], {"task_id": "export-remote", "status": "completed", "terminal": True, "export_url": "https://flova.example/final.mp4"})
        restored = self.store.load(self.project["id"])
        self.assertEqual(restored["tasks"][-1]["status"], "待审核")
        self.assertEqual(len(restored["deliverables"]), 1)
        self.assertEqual(video_export.deliverable_bytes(self.store, restored, restored["deliverables"][0]["id"]), b"valid nonempty mp4 bytes")
        archive = backup.export_project(self.store, restored)
        imported = backup.restore_project(self.store, archive)
        self.assertEqual(video_export.deliverable_bytes(self.store, imported, imported["deliverables"][0]["id"]), b"valid nonempty mp4 bytes")
        video_export.ACTIVE.discard(self.project["id"])

    def test_local_finishing_trims_speeds_and_preserves_original(self):
        ffmpeg = shutil.which("ffmpeg")
        if not ffmpeg or not shutil.which("ffprobe"):
            self.skipTest("FFmpeg unavailable")
        source_id = "a" * 32
        folder = self.store.root / "deliverables" / self.project["id"]
        folder.mkdir(parents=True)
        original = folder / f"{source_id}.mp4"
        result = subprocess.run([ffmpeg, "-y", "-hide_banner", "-loglevel", "error", "-f", "lavfi", "-i", "color=c=blue:s=320x480:d=2:r=30",
                                 "-f", "lavfi", "-i", "sine=frequency=440:duration=2", "-c:v", "libx264", "-c:a", "aac", "-shortest", str(original)],
                                capture_output=True, text=True, timeout=60)
        self.assertEqual(result.returncode, 0, result.stderr)
        original_hash = hashlib.sha256(original.read_bytes()).hexdigest()
        self.project["deliverables"].append({"id": source_id, "kind": "video", "name": "original.mp4", "bytes": original.stat().st_size,
                                            "sha256": original_hash, "created": "2026-09-29T00:00:00+00:00"})
        edit = finishing.save_edit(self.store, self.project, [{"source_id": source_id, "start": 0.25, "end": 1.75,
            "speed": 1.5, "caption": "商品展示"}])
        self.assertAlmostEqual(edit["preview_duration"], 1, places=2)
        output = finishing.render(self.store, self.project, edit["id"])
        self.assertEqual(output["kind"], "finished_video")
        self.assertGreater(output["bytes"], 0)
        self.assertEqual(hashlib.sha256(original.read_bytes()).hexdigest(), original_hash)
        self.assertAlmostEqual(finishing.probe(self.store, self.project, output["id"])["duration"], 1, delta=0.15)
        with self.assertRaisesRegex(ValueError, "已导出"):
            finishing.render(self.store, self.project, edit["id"])

    def test_delivery_package_contains_video_gallery_and_provenance(self):
        self.approved_project()
        plan = gallery.default_plan(self.store, self.project)
        gallery.approve_plan(self.store, self.project, plan["id"])
        with self.assertRaisesRegex(ValueError, "请选择"):
            delivery.export_delivery(self.store, self.project, "missing")
        image = io.BytesIO()
        Image.new("RGB", (64, 64), "white").save(image, format="PNG")
        candidate = self.store.add_source(self.project, "generated.png", "image/png", base64.b64encode(image.getvalue()).decode())
        candidate.update(origin="SeeAny gallery", gallery_plan_id=plan["id"])
        for item in plan["items"]:
            candidate["gallery_item_id"] = item["id"]
            self.project["tasks"].append({"kind": "gallery_image", "input_snapshot": {"item": item, "plan_id": plan["id"]}, "asset_ids": [candidate["id"]]})
            gallery.review_image(self.store, self.project, item["id"], candidate["id"], "采用")
        self.project["directions"].append({"id": "direction-1", "brief_id": self.project["brief_versions"][-1]["id"], "master_id": self.project["master_versions"][-1]["id"]})
        self.store.approve_direction(self.project, "direction-1")
        script = self.store.add_script(self.project, "已确认广告脚本")
        self.store.approve_script(self.project, script["id"])
        board = self.store.add_storyboard(self.project, [{"visual": "展示产品", "duration": 3, "reference_asset_id": self.project["master_versions"][-1]["asset_ids"][0]}])
        self.store.approve_storyboard(self.project, board["id"])
        video_id = "b" * 32
        folder = self.store.root / "deliverables" / self.project["id"]
        folder.mkdir(parents=True)
        (folder / f"{video_id}.mp4").write_bytes(b"video bytes")
        self.project["deliverables"].append({"id": video_id, "kind": "video", "name": "flova.mp4", "sha256": hashlib.sha256(b"video bytes").hexdigest(), "bytes": 11})
        with zipfile.ZipFile(io.BytesIO(delivery.export_delivery(self.store, self.project, video_id))) as archive:
            names = archive.namelist()
            self.assertIn("交付/商品详情页视频.mp4", names)
            self.assertEqual(sum(name.endswith(".svg") for name in names), 6)
            self.assertEqual(sum(name.endswith(".png") for name in names), 6)
            self.assertEqual(archive.read("交付/已确认脚本.txt").decode(), "已确认广告脚本")
            self.assertEqual(json.loads(archive.read("交付/交付清单.json"))["video_id"], video_id)

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

    def test_gallery_plan_approval_image_review_and_editable_export(self):
        self.approved_project()
        first = gallery.default_plan(self.store, self.project)
        self.assertEqual(len(first["items"]), 6)
        edited = json.loads(json.dumps(first["items"], ensure_ascii=False))
        edited[0].update(title="真实白色外观", subtitle="商品主图", x=50, y=12)
        plan = gallery.save_plan(self.store, self.project, edited, parent_id=first["id"])
        gallery.approve_plan(self.store, self.project, plan["id"])
        item = plan["items"][0]
        quote = gallery.image_quote(self.store, self.project, item["id"])
        with patch.object(gallery, "provider_key", return_value="test-key"), patch.object(gallery.seeany, "upload_image", return_value="https://seeany.com/upload"), patch.object(gallery.seeany, "submit", return_value={"data": {"task_uuid": "gallery-remote"}}) as submit:
            task = gallery.run_image(self.store, self.project, {"item_id": item["id"], "approved_fingerprint": quote["fingerprint"], "request_id": "gallery-request-123"})
        self.assertEqual(task["remote_id"], "gallery-remote")
        self.assertEqual(submit.call_args.args[1], "/api/ai/grouptask")
        self.assertEqual(submit.call_args.args[2]["groups"][0]["prompt"], item["prompt"])
        picture = io.BytesIO()
        Image.new("RGB", (320, 320), "white").save(picture, format="PNG")
        response = {"data": {"task": {"status": "succeeded"}, "works": [{"url": "https://cdn.seeany.com/gallery.png"}]}}
        with patch.object(service, "provider_key", return_value="test-key"), patch.object(service.seeany, "task_status", return_value=response), patch.object(service.seeany, "download_image", return_value=(picture.getvalue(), ".png")):
            service.sync(self.store, self.project, task["id"])
        generated = self.project["sources"][-1]
        self.assertEqual(generated["gallery_item_id"], item["id"])
        gallery.review_image(self.store, self.project, item["id"], generated["id"], "采用")
        with self.assertRaisesRegex(ValueError, "每张套图"):
            gallery.export_gallery(self.store, self.project)
        for remaining in plan["items"][1:]:
            # Simulate independently generated candidates for the remaining approved slots.
            self.project["tasks"].append({"kind": "gallery_image", "input_snapshot": {"item": remaining, "plan_id": plan["id"]}, "asset_ids": [generated["id"]]})
            gallery.review_image(self.store, self.project, remaining["id"], generated["id"], "采用")
        with zipfile.ZipFile(io.BytesIO(gallery.export_gallery(self.store, self.project))) as archive:
            names = archive.namelist()
            self.assertEqual(sum(name.endswith(".png") for name in names), 6)
            self.assertEqual(sum(name.endswith(".svg") for name in names), 6)
            self.assertIn("真实白色外观", archive.read("gallery/01-主图.svg").decode())
            manifest = json.loads(archive.read("source-manifest.json"))
            self.assertEqual(manifest["images"][0]["source_id"], generated["id"])

    def test_gallery_planning_uses_only_confirmed_facts_and_blocks_ambiguous_repeat(self):
        self.approved_project()
        self.store.add_fact(self.project, "虚构容量", "99 升", status="待核实")
        offer = gallery.plan_quote(self.store, self.project, "白底为主")
        self.assertNotIn("99 升", str(offer["input_snapshot"]))
        returned = {"items": [{"cateName": "主图", "prompt": "白底产品主图", "imgRatio": "1:1"}]}
        with patch.object(gallery, "provider_key", return_value="test-key"), patch.object(gallery.seeany, "upload_image", return_value="https://seeany.com/upload"), patch.object(gallery.seeany, "submit", return_value=returned) as submit:
            task = gallery.run_plan(self.store, self.project, {"requirement": "白底为主", "approved_fingerprint": offer["fingerprint"], "request_id": "plan-request-123"})
        self.assertEqual(task["status"], "待审核")
        self.assertNotIn("99 升", submit.call_args.args[2]["sellingPoints"])
        self.assertEqual(self.project["gallery_versions"][-1]["origin"], "SeeAny")
        self.project["tasks"].append({"kind": "gallery_plan", "status": "待核对"})
        with self.assertRaisesRegex(ValueError, "尚未核对"):
            gallery.plan_quote(self.store, self.project)

    def test_cost_ledger_keeps_unknown_separate_from_confirmed_actual(self):
        task = {"id": "billable-task", "kind": "gallery_image", "provider": "SeeAny", "status": "远端运行中",
                "estimate": None, "actual": None, "currency": None, "created": "2026-09-29T00:00:00+00:00"}
        self.project["tasks"].append(task)
        self.store.save(self.project)
        self.store.save(self.project)
        self.assertEqual(len(self.project["costs"]), 1)
        self.assertIsNone(self.project["costs"][0]["actual"])
        task.update(status="失败", actual=0.5, currency="CNY")
        self.store.save(self.project)
        entry = self.project["costs"][0]
        self.assertEqual(entry["actual"], 0.5)
        self.assertEqual(entry["currency"], "CNY")
        self.assertEqual(entry["status"], "失败")

    def test_supplier_bill_reconciliation_preserves_original_units_and_corrections(self):
        task = {"id": "cost-task", "kind": "preview", "provider": "SeeAny", "status": "失败",
                "estimate": None, "actual": None, "currency": None, "created": "2026-09-29T00:00:00+00:00"}
        self.project["tasks"].append(task)
        with self.assertRaisesRegex(ValueError, "账单核对来源"):
            self.store.settle_cost(self.project, task["id"], 0.3, "CNY", "")
        with self.assertRaisesRegex(ValueError, "实付金额无效"):
            self.store.settle_cost(self.project, task["id"], True, "CNY", "账单")
        entry = self.store.settle_cost(self.project, task["id"], 0.3, "CNY", "SeeAny 后台账单 123")
        self.assertEqual(entry["actual"], 0.3)
        self.assertEqual(entry["settlement_method"], "人工核对供应商账单")
        self.assertEqual(len(entry.get("revisions", [])), 0)
        self.store.settle_cost(self.project, task["id"], 0.3, "CNY", "SeeAny 后台账单 123")
        self.assertEqual(len(entry.get("revisions", [])), 0)
        self.store.settle_cost(self.project, task["id"], 0.5, "CNY", "更正账单 124")
        self.assertEqual(len(entry["revisions"]), 1)
        self.assertEqual(entry["revisions"][-1]["actual"], 0.3)

    def test_companion_backup_restores_sources_versions_and_remote_review_gate(self):
        self.approved_project()
        plan = gallery.default_plan(self.store, self.project)
        gallery.approve_plan(self.store, self.project, plan["id"])
        self.project["tasks"].append({"id": "remote-task", "kind": "gallery_image", "provider": "SeeAny",
                                      "status": "远端运行中", "remote_id": "seeany-123", "estimate": None,
                                      "actual": None, "currency": None, "created": "2026-09-29T00:00:00+00:00"})
        self.store.save(self.project)
        archive = backup.export_project(self.store, self.project)
        restored = backup.restore_project(self.store, archive)
        self.assertNotEqual(restored["id"], self.project["id"])
        self.assertEqual(restored["restored_from"], self.project["id"])
        self.assertEqual(restored["gallery_approval"], plan["id"])
        self.assertEqual(restored["tasks"][-1]["status"], "待核对")
        source_id = self.project["sources"][0]["id"]
        self.assertEqual(self.store.source_bytes(restored, source_id)[1], self.store.source_bytes(self.project, source_id)[1])
        self.assertEqual(len(restored["costs"]), len(self.project["costs"]))

    def test_public_backup_routes_round_trip_without_exposing_keys(self):
        source = self.store.add_source(self.project, "source.txt", "text/plain", base64.b64encode(b"merchant facts").decode())
        server = ThreadingHTTPServer(("127.0.0.1", 0), bridge.Handler)
        worker = threading.Thread(target=server.serve_forever, daemon=True)
        with patch.object(bridge, "STORE", self.store):
            worker.start()
            try:
                base = f"http://127.0.0.1:{server.server_port}/api"
                with urlopen(f"{base}/projects/{self.project['id']}/backup") as response:
                    archive = response.read()
                request = Request(f"{base}/restore", data=archive, headers={"Content-Type": "application/zip"}, method="POST")
                with urlopen(request) as response:
                    restored = json.load(response)
                self.assertNotEqual(restored["id"], self.project["id"])
                self.assertEqual(self.store.source_bytes(restored, source["id"])[1], b"merchant facts")
                self.assertNotIn(b"DEEPSEEK_API_KEY", archive)
            finally:
                server.shutdown()
                server.server_close()
                worker.join(timeout=2)

    def test_structured_prompt_change_locks_other_fields_and_adopts_reviewed_preview(self):
        self.approved_project()
        fields = {"product": "白色外观", "scene": "厨房台面", "composition": "产品居中", "lighting": "柔和日光",
                  "negative": "不得改变杯体结构", "ratio": "1:1"}
        original = prompts.save_version(self.store, self.project, fields)
        offer = prompts.change_quote(self.project, original["id"], ["scene"], "只改成卧室场景")
        ai = {"id": "deepseek-remote", "text": json.dumps({"changes": [{"scene": "卧室床头"}, {"scene": "卧室书桌"}]}, ensure_ascii=False), "usage": {}}
        with patch.object(prompts, "provider_key", return_value="test-key"), patch.object(prompts, "deepseek_complete", return_value=ai):
            task = prompts.propose_changes(self.store, self.project, {"parent_id": original["id"], "changed_fields": ["scene"],
                "instruction": "只改成卧室场景", "approved_fingerprint": offer["fingerprint"], "request_id": "prompt-change-123"})
        self.assertEqual(task["status"], "待审核")
        versions = self.project["prompt_versions"][-2:]
        self.assertEqual([v["fields"]["scene"] for v in versions], ["卧室床头", "卧室书桌"])
        self.assertTrue(all(v["fields"]["product"] == fields["product"] and v["fields"]["negative"] == fields["negative"] for v in versions))
        reference_id = self.project["master_versions"][-1]["asset_ids"][0]
        preview_quote = prompts.preview_quote(self.store, self.project, versions[0]["id"], reference_id)
        with patch.object(prompts, "provider_key", return_value="test-key"), patch.object(prompts.seeany, "upload_image", return_value="https://seeany.com/upload"), patch.object(prompts.seeany, "submit", return_value={"data": {"task_uuid": "seeany-preview"}}) as submit:
            preview_task = prompts.run_preview(self.store, self.project, {"version_id": versions[0]["id"], "reference_id": reference_id,
                "approved_fingerprint": preview_quote["fingerprint"], "request_id": "prompt-preview-123"})
        self.assertIn("卧室床头", submit.call_args.args[2]["prompt"])
        result = {"data": {"task": {"status": "succeeded"}, "works": [{"url": "https://cdn.seeany.com/preview.png"}]}}
        with patch.object(service, "provider_key", return_value="test-key"), patch.object(service.seeany, "task_status", return_value=result), patch.object(service.seeany, "download_image", return_value=(b"preview", ".png")):
            service.sync(self.store, self.project, preview_task["id"])
        source_id = preview_task["asset_ids"][0]
        prompts.review_preview(self.store, self.project, versions[0]["id"], source_id, "采用")
        self.assertEqual(self.project["prompt_adoption"]["source_id"], source_id)
        self.assertEqual(self.project["sources"][-1]["reference_ids"], [reference_id])

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
