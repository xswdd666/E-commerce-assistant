"""Loopback JSON API for the future infinite-canvas extension."""

from __future__ import annotations

import json
import base64
import os
import shutil
import tempfile
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from .core import Store
from . import backup, delivery, finishing, flova_flow, gallery, jev, legacy, observations, prompts, service, video_export


ROOT = Path(__file__).resolve().parent.parent
STORE = Store(ROOT / "data" / "commerce-studio")
MAX_BODY = 35 * 1024 * 1024
ALLOWED_ORIGINS = frozenset({
    "http://127.0.0.1:3000", "http://localhost:3000",
    "http://127.0.0.1:5173", "http://localhost:5173",
})


class Handler(BaseHTTPRequestHandler):
    def log_message(self, format, *args):
        # Request bodies, prompts, provider errors and keys must never enter logs.
        pass

    def _trusted_request(self):
        try:
            host = urlparse(f"http://{self.headers.get('Host', '')}").hostname
        except ValueError:
            host = None
        origin = self.headers.get("Origin")
        if (host not in ("127.0.0.1", "localhost")
                or (origin is not None and origin not in ALLOWED_ORIGINS)
                or (origin is None and self.headers.get("Sec-Fetch-Site") == "cross-site")):
            self._send(403, {"error": "请求来源不受信任"})
            return False
        return True

    def _send(self, code, value):
        raw = json.dumps(value, ensure_ascii=False).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(raw)))
        self.send_header("Cache-Control", "no-store")
        origin = self.headers.get("Origin", "")
        if origin in ALLOWED_ORIGINS:
            self.send_header("Access-Control-Allow-Origin", origin)
            self.send_header("Vary", "Origin")
        self.end_headers()
        self.wfile.write(raw)

    def _send_binary(self, raw, filename, mime):
        self.send_response(200)
        self.send_header("Content-Type", mime)
        self.send_header("Content-Disposition", f'attachment; filename="{filename}"')
        self.send_header("Content-Length", str(len(raw)))
        self.send_header("Cache-Control", "no-store")
        origin = self.headers.get("Origin", "")
        if origin in ALLOWED_ORIGINS:
            self.send_header("Access-Control-Allow-Origin", origin)
        self.end_headers()
        self.wfile.write(raw)

    def _send_archive(self, raw, filename):
        self._send_binary(raw, filename, "application/zip")

    def _send_archive_file(self, path, filename):
        self.send_response(200)
        self.send_header("Content-Type", "application/zip")
        self.send_header("Content-Disposition", f'attachment; filename="{filename}"')
        self.send_header("Content-Length", str(path.stat().st_size))
        self.send_header("Cache-Control", "no-store")
        origin = self.headers.get("Origin", "")
        if origin in ALLOWED_ORIGINS:
            self.send_header("Access-Control-Allow-Origin", origin)
        self.end_headers()
        with path.open("rb") as archive:
            shutil.copyfileobj(archive, self.wfile, length=1024 * 1024)

    def do_OPTIONS(self):
        if not self._trusted_request():
            return
        self.send_response(204)
        origin = self.headers.get("Origin", "")
        if origin in ALLOWED_ORIGINS:
            self.send_header("Access-Control-Allow-Origin", origin)
            self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
            self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.end_headers()

    def _body(self):
        length = int(self.headers.get("Content-Length", "0"))
        if length < 0 or length > MAX_BODY:
            raise ValueError("请求体过大")
        return json.loads(self.rfile.read(length)) if length else {}

    def _route(self, method):
        parts = [part for part in urlparse(self.path).path.split("/") if part]
        if parts[:1] != ["api"]:
            raise ValueError("接口不存在")
        if method == "GET" and parts == ["api", "health"]:
            return {"ok": True, "deepseek": bool(service.provider_key("DEEPSEEK_API_KEY")),
                    "seeany": bool(service.provider_key("SEEANY_API_KEY")), "flova": service.flova.executable() is not None,
                    "jev": bool(service.provider_key("TYPESAFE_API_KEY"))}
        if method == "GET" and parts == ["api", "projects"]:
            return STORE.list()
        if method == "GET" and parts == ["api", "search"]:
            return STORE.search(parse_qs(urlparse(self.path).query).get("q", [""])[0])
        if method == "GET" and parts == ["api", "legacy", "projects"]:
            return legacy.list_projects(ROOT / "data" / "workbench.sqlite3")
        if method == "POST" and parts == ["api", "legacy", "import"]:
            with STORE.lock:
                return legacy.import_project(STORE, ROOT / "data" / "workbench.sqlite3", ROOT / "data" / "files", self._body().get("id"))
        if method == "POST" and parts == ["api", "projects"]:
            return STORE.create(self._body().get("name"))
        if len(parts) < 3 or parts[:2] != ["api", "projects"]:
            raise ValueError("接口不存在")
        if method == "POST" and parts[3:] == ["delete"]:
            return STORE.delete(parts[2])
        project = STORE.load(parts[2])
        if method == "GET" and len(parts) == 3:
            return project
        if method == "GET" and len(parts) == 5 and parts[3] == "sources":
            source, raw = STORE.source_bytes(project, parts[4])
            return {"name": source["name"], "mime": source["mime"], "data_url": f"data:{source['mime']};base64," + base64.b64encode(raw).decode()}
        if method == "POST":
            body = self._body()
            with STORE.lock:
                project = STORE.load(parts[2])
                if parts[3:] == ["sources"]:
                    return STORE.add_source(project, body.get("name"), body.get("mime"), body.get("base64"))
                if parts[3:] == ["facts"]:
                    return STORE.add_fact(project, body.get("field"), body.get("value"), body.get("source_id"), body.get("status", "待核实"))
                if len(parts) == 6 and parts[3] == "facts" and parts[5] == "review":
                    return STORE.review_fact(project, parts[4], body.get("status"))
                if parts[3:] == ["briefs", "confirm"]:
                    return STORE.confirm_brief(project, body.get("fact_ids", []))
                if parts[3:] == ["masters", "confirm"]:
                    return STORE.confirm_master(project, body.get("asset_ids", []), body.get("inferred_details"))
                if parts[3:] == ["masters", "verify-detail"]:
                    return STORE.verify_master_detail(project, body.get("detail_id"), body.get("evidence_source_id"))
                if parts[3:] == ["master-candidates", "quote"]:
                    return service.master_quote(STORE, project, body)
                if parts[3:] == ["master-candidates", "run"]:
                    return service.run_master(STORE, project, body)
                if parts[3:] == ["chat", "quote"]:
                    return service.chat_quote(project, body)
                if parts[3:] == ["chat", "run"]:
                    return service.run_chat(STORE, project, body)
                if parts[3:] == ["facts", "quote"]:
                    return service.facts_quote(project, body)
                if parts[3:] == ["facts", "run"]:
                    return service.run_facts(STORE, project, body)
                if parts[3:] == ["observations", "quote"]:
                    return observations.quote(STORE, project, body.get("original_id"), body.get("candidate_id"))
                if parts[3:] == ["observations", "run"]:
                    return observations.run(STORE, project, body)
                if len(parts) == 6 and parts[3] == "observations" and parts[5] == "correct":
                    return observations.correct(STORE, project, parts[4], body.get("field"), body.get("text"))
                if parts[3:] == ["preview", "quote"]:
                    return service.preview_quote(project, body)
                if parts[3:] == ["preview", "run"]:
                    return service.run_preview(STORE, project, body)
                if parts[3:] == ["preview", "review"]:
                    return service.review_canvas_preview(STORE, project, body.get("task_id"), body.get("source_id"), body.get("decision"), body.get("reason", ""))
                if parts[3:] == ["directions", "quote"]:
                    return service.directions_quote(project)
                if parts[3:] == ["directions", "run"]:
                    return service.run_directions(STORE, project, body)
                if parts[3:] == ["directions", "approve"]:
                    return STORE.approve_direction(project, body.get("direction_id"))
                if parts[3:] == ["scripts"]:
                    return STORE.add_script(project, body.get("text"))
                if parts[3:] == ["scripts", "approve"]:
                    return STORE.approve_script(project, body.get("script_id"))
                if parts[3:] == ["storyboards"]:
                    return STORE.add_storyboard(project, body.get("shots"))
                if parts[3:] == ["storyboards", "approve"]:
                    return STORE.approve_storyboard(project, body.get("storyboard_id"))
                if parts[3:] == ["flova", "create"]:
                    return flova_flow.create_project(STORE, project)
                if parts[3:] == ["flova", "attach"]:
                    return flova_flow.attach_project(STORE, project, body.get("project_id"))
                if parts[3:] == ["flova", "quote"]:
                    return flova_flow.quote(project, body.get("canvas_context"))
                if parts[3:] == ["flova", "shots", "quote"]:
                    return flova_flow.shot_quote(project, body.get("shot_id"))
                if parts[3:] == ["flova", "shots", "run"]:
                    return flova_flow.run_shot(STORE, project, body)
                if parts[3:] == ["flova", "shots", "approve"]:
                    return flova_flow.approve_shot(STORE, project, body.get("task_id"))
                if parts[3:] == ["flova", "shots", "approve-sequence"]:
                    return flova_flow.approve_shot_sequence(STORE, project)
                if parts[3:] == ["flova", "resources"]:
                    return flova_flow.resources(project)
                if parts[3:] == ["flova", "resources", "pull"]:
                    return flova_flow.pull_video_resource(STORE, project, body.get("resource_id"))
                if parts[3:] == ["flova", "approve"]:
                    return flova_flow.approve_video(STORE, project, body.get("task_id"))
                if parts[3:] == ["flova", "run"]:
                    return flova_flow.run(STORE, project, body)
                if parts[3:] == ["flova", "actions", "resume"]:
                    return flova_flow.resume_action(STORE, project, body.get("task_id"), body.get("action_id"), body.get("option_id"))
                if len(parts) == 6 and parts[3] == "flova" and parts[5] == "recover":
                    return flova_flow.recover(STORE, project, parts[4])
                if parts[3:] == ["flova", "export", "quote"]:
                    return video_export.quote(project)
                if parts[3:] == ["flova", "export", "run"]:
                    return video_export.run(STORE, project, body)
                if len(parts) == 7 and parts[3:5] == ["flova", "export"] and parts[6] == "recover":
                    return video_export.recover(STORE, project, parts[5])
                if parts[3:] == ["finishing", "probe"]:
                    return finishing.probe(STORE, project, body.get("source_id"))
                if parts[3:] == ["finishing", "edits"]:
                    return finishing.save_edit(STORE, project, body.get("clips"), body.get("parent_id"))
                if parts[3:] == ["finishing", "render"]:
                    return finishing.render(STORE, project, body.get("edit_id"))
                if len(parts) == 6 and parts[3] == "costs" and parts[5] == "settle":
                    return STORE.settle_cost(project, parts[4], body.get("amount"), body.get("currency"), body.get("receipt"))
                if parts[3:] == ["costs", "target"]:
                    return STORE.set_cost_target(project, body.get("amount"), body.get("currency"))
                if len(parts) == 6 and parts[3] == "tasks" and parts[5] == "resolve-unidentified":
                    return STORE.resolve_unidentified_task(project, parts[4], body.get("note"))
                if parts[3:] == ["jev", "directions", "quote"]:
                    return jev.quote(project)
                if parts[3:] == ["jev", "directions", "run"]:
                    return jev.run(STORE, project, body)
                if parts[3:] == ["gallery", "default"]:
                    return gallery.default_plan(STORE, project)
                if parts[3:] == ["gallery", "plans"]:
                    return gallery.save_plan(STORE, project, body.get("items"), parent_id=body.get("parent_id"))
                if parts[3:] == ["gallery", "plans", "approve"]:
                    return gallery.approve_plan(STORE, project, body.get("plan_id"))
                if parts[3:] == ["gallery", "planning", "quote"]:
                    return gallery.plan_quote(STORE, project, body.get("requirement", ""))
                if parts[3:] == ["gallery", "planning", "run"]:
                    return gallery.run_plan(STORE, project, body)
                if parts[3:] == ["gallery", "images", "quote"]:
                    return gallery.image_quote(STORE, project, body.get("item_id"))
                if parts[3:] == ["gallery", "images", "run"]:
                    return gallery.run_image(STORE, project, body)
                if parts[3:] == ["gallery", "images", "review"]:
                    return gallery.review_image(STORE, project, body.get("item_id"), body.get("source_id"), body.get("decision"), body.get("reason", ""))
                if parts[3:] == ["prompts", "versions"]:
                    return prompts.save_version(STORE, project, body.get("fields"), body.get("parent_id"))
                if parts[3:] == ["prompts", "changes", "quote"]:
                    return prompts.change_quote(project, body.get("parent_id"), body.get("changed_fields"), body.get("instruction"))
                if parts[3:] == ["prompts", "changes", "run"]:
                    return prompts.propose_changes(STORE, project, body)
                if parts[3:] == ["prompts", "preview", "quote"]:
                    return prompts.preview_quote(STORE, project, body.get("version_id"), body.get("reference_id"))
                if parts[3:] == ["prompts", "preview", "run"]:
                    return prompts.run_preview(STORE, project, body)
                if parts[3:] == ["prompts", "preview", "review"]:
                    return prompts.review_preview(STORE, project, body.get("version_id"), body.get("source_id"), body.get("decision"), body.get("reason", ""))
                if parts[3:] == ["prompts", "preview", "import"]:
                    return prompts.import_preview(STORE, project, body.get("version_id"), body.get("reference_id"), body.get("name"), body.get("mime"), body.get("base64"), body.get("external_prompt"))
                if parts[3:] == ["nodes"]:
                    return STORE.add_node(project, body.get("kind"), body.get("data"))
                if parts[3:] == ["edges"]:
                    return STORE.connect(project, body.get("source"), body.get("target"), body.get("port"))
                if len(parts) == 6 and parts[3] == "tasks" and parts[5] == "run":
                    return service.run(STORE, project, parts[4], body)
                if len(parts) == 6 and parts[3] == "tasks" and parts[5] == "sync":
                    return service.sync(STORE, project, parts[4])
        if method == "GET" and len(parts) == 6 and parts[3] == "tasks" and parts[5] == "quote":
            return service.quote(STORE, project, parts[4])
        raise ValueError("接口不存在")

    def do_GET(self):
        if not self._trusted_request():
            return
        try:
            parts = [part for part in urlparse(self.path).path.split("/") if part]
            if len(parts) == 4 and parts[:2] == ["api", "projects"] and parts[3] == "backup":
                project = STORE.load(parts[2])
                with tempfile.TemporaryDirectory(dir=STORE.root) as temporary:
                    path = Path(temporary) / "project.zip"
                    backup.write_project_archive(STORE, project, path)
                    self._send_archive_file(path, f"commerce-project-{project['id']}.zip")
                return
            if len(parts) == 5 and parts[:2] == ["api", "projects"] and parts[3] == "deliverables":
                project = STORE.load(parts[2])
                self._send_binary(video_export.deliverable_bytes(STORE, project, parts[4]), "flova-final.mp4", "video/mp4")
                return
            if len(parts) == 5 and parts[:2] == ["api", "projects"] and parts[3:] == ["gallery", "export"]:
                project = STORE.load(parts[2])
                self._send_archive(gallery.export_gallery(STORE, project), f"commerce-gallery-{project['id']}.zip")
                return
            if len(parts) == 6 and parts[:2] == ["api", "projects"] and parts[3:5] == ["delivery", "export"]:
                project = STORE.load(parts[2])
                self._send_archive(delivery.export_delivery(STORE, project, parts[5]), f"commerce-delivery-{project['id']}.zip")
                return
            self._send(200, self._route("GET"))
        except ValueError as exc:
            self._send(400, {"error": str(exc)})

    def do_POST(self):
        if not self._trusted_request():
            return
        try:
            if urlparse(self.path).path == "/api/restore":
                length = int(self.headers.get("Content-Length", "0"))
                if length <= 0 or length > 250 * 1024 * 1024:
                    raise ValueError("备份为空或超过 250 MB")
                with tempfile.TemporaryDirectory(dir=STORE.root) as temporary:
                    path = Path(temporary) / "upload.zip"
                    with path.open("wb") as output:
                        remaining = length
                        while remaining:
                            chunk = self.rfile.read(min(1024 * 1024, remaining))
                            if not chunk:
                                raise ValueError("备份上传未完成")
                            output.write(chunk)
                            remaining -= len(chunk)
                    self._send(200, backup.restore_project(STORE, path))
                return
            self._send(200, self._route("POST"))
        except (ValueError, json.JSONDecodeError) as exc:
            self._send(400, {"error": str(exc)})


def main():
    port = int(os.environ.get("COMMERCE_STUDIO_PORT", "8766"))
    server = ThreadingHTTPServer(("127.0.0.1", port), Handler)
    STORE.reconcile_interrupted_tasks()
    print(f"Commerce Studio bridge: http://127.0.0.1:{port}")
    server.serve_forever()


if __name__ == "__main__":
    main()
