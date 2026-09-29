import tempfile
import unittest
import base64
import io
import shutil
import subprocess
import zipfile
import json
import threading
from http.server import ThreadingHTTPServer
from pathlib import Path
from unittest.mock import patch
from urllib.request import Request, urlopen

from app import server


class WorkflowTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.old = server.DATA, server.FILES, server.DB, server.SECRETS
        server.DATA = Path(self.tmp.name)
        server.FILES = server.DATA / "files"
        server.DB = server.DATA / "workbench.sqlite3"
        server.SECRETS = server.DATA / "provider-settings.json"
        self.p = server.new_project("试点商品")

    def tearDown(self):
        server.DATA, server.FILES, server.DB, server.SECRETS = self.old
        self.tmp.cleanup()

    def test_confirmed_brief_is_immutable_snapshot(self):
        fact = server.execute(self.p, "fact", {"field": "规格", "value": "白色机身", "status": "已知事实"})
        server.execute(self.p, "fact_review", {"id": fact["id"], "confirmed": True})
        first = server.execute(self.p, "brief_confirm", {})
        fact["value"] = "黑色机身"
        second = server.execute(self.p, "brief_confirm", {})
        self.assertEqual(first["facts"][0]["value"], "白色机身")
        self.assertEqual(second["facts"][0]["value"], "黑色机身")
        self.assertNotEqual(first["id"], second["id"])

    def test_unverified_detail_blocks_storyboard(self):
        fact = server.execute(self.p, "fact", {"field": "外观", "value": "白色", "status": "已知事实"})
        server.execute(self.p, "fact_review", {"id": fact["id"], "confirmed": True})
        server.execute(self.p, "brief_confirm", {})
        a = server.execute(self.p, "asset", {"name": "front.png", "data": "iVBORw0KGgo="})
        for view in ("正面", "侧面", "背面"):
            server.execute(self.p, "master", {"group": 1, "view": view, "asset_id": a["id"], "inferred": True})
        server.execute(self.p, "master_approve", {"group": 1})
        detail = server.execute(self.p, "detail", {"description": "背部进风口", "verified": True})
        self.assertFalse(detail["verified"])
        direction = server.execute(self.p, "direction", {"title": "便捷"})
        server.execute(self.p, "direction_approve", {"id": direction["id"]})
        script = server.execute(self.p, "script", {"text": "展示商品"})
        server.execute(self.p, "script_approve", {"id": script["id"]})
        board = server.execute(self.p, "storyboard", {"shots": [{"description": "背部特写", "detail_ids": [detail["id"]]}]})
        with self.assertRaisesRegex(ValueError, "未核实"):
            server.execute(self.p, "storyboard_approve", {"id": board["id"]})

    def test_budget_unknown_and_stage_reserve_block(self):
        fact = server.execute(self.p, "fact", {"field": "规格", "value": "白色", "status": "已知事实"})
        server.execute(self.p, "fact_review", {"id": fact["id"], "confirmed": True})
        server.execute(self.p, "brief_confirm", {})
        unknown = server.execute(self.p, "task", {"stage": "三视图", "provider": "SeeAny"})
        self.assertEqual(unknown["status"], "费用阻止")
        too_much = server.execute(self.p, "task", {"stage": "三视图", "provider": "SeeAny", "estimate": 31})
        self.assertEqual(too_much["status"], "费用阻止")
        allowed = server.execute(self.p, "task", {"stage": "三视图", "provider": "SeeAny", "estimate": 10})
        self.assertEqual(allowed["status"], "待批准")

    def test_upstream_reapproval_and_unattended_aggregate(self):
        fact = server.execute(self.p, "fact", {"field": "规格", "value": "白色", "status": "已知事实"})
        server.execute(self.p, "fact_review", {"id": fact["id"], "confirmed": True})
        server.execute(self.p, "brief_confirm", {})
        self.p["master_approval"] = {"group": 1, "brief_id": server.brief(self.p)["id"]}
        server.execute(self.p, "brief_confirm", {})
        self.assertIsNone(self.p["master_approval"])
        self.p["settings"]["stage_reserve"]["三视图"] = 100
        first = server.execute(self.p, "task", {"stage": "三视图", "provider": "SeeAny", "estimate": 20, "unattended": True})
        server.execute(self.p, "task_update", {"id": first["id"], "status": "已排队"})
        second = server.execute(self.p, "task", {"stage": "三视图", "provider": "SeeAny", "estimate": 15, "unattended": True})
        self.assertEqual(second["status"], "费用阻止")

    def test_task_captures_adopted_asset_prompt_and_reference(self):
        fact = server.execute(self.p, "fact", {"field": "外观", "value": "白色", "status": "已知事实"})
        server.execute(self.p, "fact_review", {"id": fact["id"], "confirmed": True})
        server.execute(self.p, "brief_confirm", {})
        src = server.execute(self.p, "source", {"name": "实拍.png", "data": "iVBORw0KGgo="})
        prompt = server.execute(self.p, "prompt", {"identity": "白色机身", "purpose": "母版"})
        a = server.execute(self.p, "asset", {"name": "候选.png", "data": "iVBORw0KGgo=", "prompt_id": prompt["id"], "reference_ids": [src["id"]]})
        server.execute(self.p, "asset_review", {"id": a["id"], "choice": "通过真实性"})
        server.execute(self.p, "adopt", {"id": a["id"], "slot": "产品主体"})
        task = server.execute(self.p, "task", {"stage": "三视图", "provider": "SeeAny", "estimate": 5})
        self.assertEqual(task["input_versions"]["adopted"][0]["prompt_id"], prompt["id"])
        self.assertEqual(task["input_versions"]["adopted"][0]["reference_ids"], [src["id"]])

    def test_incompatible_connection_and_backup(self):
        with self.assertRaisesRegex(ValueError, "不兼容"):
            server.execute(self.p, "edge", {"source": "source", "target": "video"})
        server.execute(self.p, "edge", {"source": "source", "target": "brief"})
        self.assertEqual(len(self.p["edges"]), 1)
        self.assertIn(b"project.json", server.export_zip(self.p, backup=True))

    def test_gallery_export_has_png_and_editable_svg(self):
        from PIL import Image
        buffer = io.BytesIO()
        Image.new("RGB", (240, 180), "#c8e6dc").save(buffer, "PNG")
        a = server.execute(self.p, "asset", {"name": "product.png", "data": base64.b64encode(buffer.getvalue()).decode()})
        self.p["gallery"].append({"id": "gallery1", "approved": True, "items": [{"kind": "主图", "asset_id": a["id"], "title": "洁净生活", "subtitle": "产品展示", "x": 50, "y": 10}]})
        with zipfile.ZipFile(io.BytesIO(server.export_zip(self.p))) as z:
            names = z.namelist()
            self.assertIn("交付/套图/01-主图.png", names)
            svg = z.read("交付/套图/01-主图.svg").decode()
            self.assertIn("洁净生活", svg)
            self.assertIn("<text", svg)
            png = Image.open(io.BytesIO(z.read("交付/套图/01-主图.png")))
            self.assertEqual(png.size, (1000, 1000))

    def test_deepseek_mock_produces_unverified_candidates_and_no_secret_backup(self):
        server.update_provider_settings({"deepseek_api_key": "test-secret", "input_cny_per_m": 1, "output_cny_per_m": 2})
        s = server.execute(self.p, "source", {"name": "说明.txt", "data": base64.b64encode("机身为白色".encode()).decode()})
        request = {"kind": "facts", "source_id": s["id"]}
        quote = server.ai_quote(self.p, "facts", request)
        self.assertFalse(quote["reason"])
        with patch.object(server, "deepseek_complete", return_value={"id": "remote-1", "text": '{"facts":[{"field":"外观","value":"白色机身"}]}', "usage": {"prompt_tokens": 60, "completion_tokens": 30}}):
            result = server.run_ai(self.p, "facts", {**request, "approved_quote": quote["estimate"], "request_id": "test-request-123"})
        self.assertEqual(result["task"]["status"], "待审核")
        self.assertEqual(self.p["facts"][0]["status"], "待核实")
        self.assertFalse(self.p["facts"][0]["confirmed"])
        self.assertNotIn(b"test-secret", server.export_zip(self.p, backup=True))

    @unittest.skipUnless(shutil.which("ffmpeg") and shutil.which("ffprobe"), "FFmpeg unavailable")
    def test_local_video_export(self):
        sample = Path(self.tmp.name) / "sample.mp4"
        subprocess.run(["ffmpeg", "-y", "-hide_banner", "-loglevel", "error", "-f", "lavfi", "-i", "color=c=blue:s=360x640:d=1", "-f", "lavfi", "-i", "sine=frequency=440:duration=1", "-c:v", "libx264", "-c:a", "aac", "-shortest", str(sample)], check=True)
        a = server.execute(self.p, "asset", {"name": "sample.mp4", "data": base64.b64encode(sample.read_bytes()).decode()})
        self.p["clips"].append({"id": "clip1", "asset_id": a["id"], "start": 0, "end": 0.8, "speed": 1.25, "caption": "产品展示"})
        result = server.export_video(self.p)
        self.assertGreater(len(result), 1000)
        self.assertTrue(result[4:8] == b"ftyp")

    def test_http_project_fact_backup_restore_delete(self):
        http = ThreadingHTTPServer(("127.0.0.1", 0), server.Handler)
        thread = threading.Thread(target=http.serve_forever, daemon=True)
        thread.start()
        base = f"http://127.0.0.1:{http.server_port}"

        def post(path, body):
            request = Request(base + path, data=json.dumps(body, ensure_ascii=False).encode(), headers={"Content-Type": "application/json"}, method="POST")
            with urlopen(request) as response:
                return json.load(response)

        try:
            p = post("/api/projects", {"name": "接口验证"})
            result = post(f"/api/projects/{p['id']}/action/fact", {"field": "外观", "value": "白色机身", "status": "已知事实"})
            fact_id = result["result"]["id"]
            post(f"/api/projects/{p['id']}/action/fact_review", {"id": fact_id, "confirmed": True})
            post(f"/api/projects/{p['id']}/action/brief_confirm", {})
            with urlopen(base + f"/api/projects/{p['id']}/backup") as response:
                backup = response.read()
            request = Request(base + "/api/restore", data=backup, headers={"Content-Type": "application/zip"}, method="POST")
            with urlopen(request) as response:
                restored = json.load(response)
            self.assertNotEqual(p["id"], restored["id"])
            self.assertEqual(restored["brief_versions"][0]["facts"][0]["value"], "白色机身")
            post(f"/api/projects/{p['id']}/delete", {"confirm": "接口验证"})
            self.assertEqual(len(server.listing()), 1)
        finally:
            http.shutdown()
            http.server_close()


if __name__ == "__main__":
    unittest.main()
