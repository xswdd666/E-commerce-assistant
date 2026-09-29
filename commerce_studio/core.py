"""Durable project state and typed canvas dependencies.

This module has no dependency on the legacy web application or its budget gate.
"""

from __future__ import annotations

import copy
import base64
import binascii
import hashlib
import json
import shutil
import threading
import uuid
from datetime import datetime, timezone
from pathlib import Path


PORTS = {
    "prompt": {"out": "text"},
    "source_image": {"out": "image"},
    "image": {"out": "image"},
    "video": {"out": "video"},
    "audio": {"out": "audio"},
    "config": {"out": "config"},
    "image_task": {"text": "text", "reference": "image", "config": "config", "out": "image"},
    "video_task": {"text": "text", "reference": "image", "audio": "audio", "config": "config", "out": "video"},
    "chat_task": {"text": "text", "reference": "image", "out": "text"},
}


def stamp():
    return datetime.now(timezone.utc).isoformat()


def ident():
    return uuid.uuid4().hex


def fingerprint(value):
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True).encode()).hexdigest()


class Store:
    def __init__(self, root: Path):
        self.root = Path(root)
        self.lock = threading.RLock()
        self.root.mkdir(parents=True, exist_ok=True)

    def _path(self, project_id):
        if not isinstance(project_id, str) or len(project_id) != 32 or any(c not in "0123456789abcdef" for c in project_id):
            raise ValueError("项目标识无效")
        return self.root / f"{project_id}.json"

    def create(self, name):
        if not isinstance(name, str) or not name.strip():
            raise ValueError("请输入项目名称")
        project = {"id": ident(), "organization_id": "local", "name": name.strip(), "created": stamp(),
                   "updated": stamp(), "sources": [], "facts": [], "brief_versions": [], "master_versions": [],
                   "directions": [], "direction_approval": None, "script_versions": [], "script_approval": None,
                   "storyboard_versions": [], "storyboard_approval": None, "external": {"flova_project_id": "", "flova_project_url": ""},
                   "gallery_versions": [], "gallery_approval": None, "gallery_choices": {}, "gallery_reviews": [],
                   "prompt_versions": [], "prompt_adoption": None, "prompt_reviews": [],
                   "image_observations": [],
                   "video_approval": None, "deliverables": [], "video_edits": [],
                   "nodes": [], "edges": [], "tasks": [], "costs": [], "chat": []}
        self.save(project)
        return project

    def list(self):
        return [{"id": p["id"], "name": p["name"], "updated": p["updated"], "legacy_id": p.get("legacy_id")}
                for path in self.root.glob("*.json")
                if (p := json.loads(path.read_text(encoding="utf-8")))]

    def search(self, query):
        if not isinstance(query, str) or not query.strip() or len(query) > 100:
            raise ValueError("请输入 1 至 100 字的关键词")
        needle = query.strip().casefold()
        matches = []
        for item in self.list():
            project = self.load(item["id"])
            fields = [("项目", project["name"])]
            fields.extend(("素材", source.get("name", "")) for source in project["sources"])
            fields.extend(("提示词", prompt.get("text", "")) for prompt in project["prompt_versions"])
            fields.extend(("旧提示词", prompt.get("text", "")) for prompt in project.get("legacy_import", {}).get("archive", {}).get("prompts", []))
            for kind, value in fields:
                if isinstance(value, str) and needle in value.casefold():
                    matches.append({"project_id": project["id"], "project_name": project["name"], "kind": kind,
                                    "text": value[:160], "updated": project["updated"]})
                    if len(matches) >= 100:
                        return matches
        return matches

    def delete(self, project_id):
        with self.lock:
            path = self._path(project_id)
            already_missing = not path.is_file()
            root = self.root.resolve()
            directories = [self.root / kind / project_id for kind in ("files", "deliverables")]
            for directory in directories:
                resolved = directory.resolve()
                expected_parent = root / directory.parent.name
                if expected_parent.resolve() != expected_parent or resolved.parent != expected_parent or directory.is_symlink():
                    raise ValueError("项目文件目录无效，停止删除")
            for directory in directories:
                if directory.exists():
                    shutil.rmtree(directory)
            path.unlink(missing_ok=True)
            return {"deleted_id": project_id, "already_missing": already_missing}

    def load(self, project_id):
        try:
            project = json.loads(self._path(project_id).read_text(encoding="utf-8"))
            project.setdefault("gallery_versions", [])
            project.setdefault("gallery_approval", None)
            project.setdefault("gallery_choices", {})
            project.setdefault("gallery_reviews", [])
            project.setdefault("prompt_versions", [])
            project.setdefault("prompt_adoption", None)
            project.setdefault("prompt_reviews", [])
            project.setdefault("image_observations", [])
            project.setdefault("video_approval", None)
            project.setdefault("deliverables", [])
            project.setdefault("video_edits", [])
            project.setdefault("costs", [])
            return project
        except FileNotFoundError as exc:
            raise ValueError("项目不存在") from exc

    def save(self, project):
        with self.lock:
            costs = {entry["task_id"]: entry for entry in project.setdefault("costs", [])}
            for task in project.get("tasks", []):
                if not task.get("id") or not task.get("provider"):
                    continue
                entry = costs.get(task["id"])
                if entry is None:
                    entry = {"id": ident(), "task_id": task["id"], "provider": task["provider"],
                             "stage": task.get("kind") or "canvas", "node_id": task.get("node_id"),
                             "purpose": "草稿" if task.get("kind") in ("preview", "chat", "gallery_plan", "facts", "directions", "prompt_change", "prompt_preview", "image_observation") else "正式",
                             "model": task.get("model"), "estimate": task.get("estimate"), "actual": None,
                             "currency": task.get("currency"), "pricing_source": task.get("pricing_source") or "未核实",
                             "submitted_at": task.get("created") or stamp(), "settled_at": None,
                             "status": task.get("status")}
                    project["costs"].append(entry)
                    costs[task["id"]] = entry
                entry["status"] = task.get("status")
                actual = task.get("actual")
                if isinstance(actual, (int, float)) and not isinstance(actual, bool) and actual >= 0:
                    entry["actual"] = actual
                    entry["currency"] = task.get("currency")
                    entry["settled_at"] = entry["settled_at"] or stamp()
            project["updated"] = stamp()
            path = self._path(project["id"])
            tmp = path.with_suffix(".tmp")
            tmp.write_text(json.dumps(project, ensure_ascii=False, indent=2), encoding="utf-8")
            tmp.replace(path)

    def settle_cost(self, project, task_id, amount, currency, receipt):
        task = next((t for t in project["tasks"] if t.get("id") == task_id and t.get("provider")), None)
        if not task:
            raise ValueError("费用任务不存在")
        if isinstance(amount, bool):
            raise ValueError("实付金额无效")
        try:
            amount = float(amount)
        except (ValueError, TypeError) as exc:
            raise ValueError("实付金额无效") from exc
        if not 0 <= amount <= 1_000_000 or amount != amount:
            raise ValueError("实付金额超出范围")
        if not isinstance(currency, str) or not currency.strip() or len(currency) > 20:
            raise ValueError("请填写供应商账单原始单位")
        if not isinstance(receipt, str) or not receipt.strip() or len(receipt) > 500:
            raise ValueError("请填写账单核对来源")
        self.save(project)
        entry = next(c for c in project["costs"] if c["task_id"] == task_id)
        currency, receipt = currency.strip(), receipt.strip()
        if (entry["actual"], entry["currency"], entry.get("receipt_source")) == (amount, currency, receipt):
            return entry
        if entry["actual"] is not None:
            entry.setdefault("revisions", []).append({"actual": entry["actual"], "currency": entry["currency"],
                                                        "receipt_source": entry.get("receipt_source"), "replaced_at": stamp()})
        task["actual"] = amount
        task["currency"] = currency
        entry["actual"] = amount
        entry["currency"] = currency
        entry["receipt_source"] = receipt
        entry["settlement_method"] = "人工核对供应商账单"
        entry["settled_at"] = stamp()
        self.save(project)
        return entry

    def add_node(self, project, kind, data=None):
        if kind not in PORTS:
            raise ValueError("不支持的画布节点类型")
        node = {"id": ident(), "kind": kind, "data": copy.deepcopy(data or {}), "created": stamp()}
        project["nodes"].append(node)
        self.save(project)
        return node

    def connect(self, project, source_id, target_id, target_port):
        nodes = {n["id"]: n for n in project["nodes"]}
        source, target = nodes.get(source_id), nodes.get(target_id)
        if source is None or target is None or source_id == target_id:
            raise ValueError("连线节点不存在或形成自连接")
        output_type = PORTS[source["kind"]]["out"]
        if PORTS[target["kind"]].get(target_port) != output_type or target_port == "out":
            raise ValueError("连线端口类型不兼容")
        if any(e["target"] == target_id and e["port"] == target_port for e in project["edges"]):
            raise ValueError("输入端口已有连线，请先移除旧连线")
        adjacency = {}
        for edge in project["edges"]:
            adjacency.setdefault(edge["source"], []).append(edge["target"])
        todo, seen = [target_id], set()
        while todo:
            current = todo.pop()
            if current == source_id:
                raise ValueError("连线会形成循环依赖")
            if current not in seen:
                seen.add(current)
                todo.extend(adjacency.get(current, []))
        edge = {"id": ident(), "source": source_id, "target": target_id, "port": target_port}
        project["edges"].append(edge)
        self.save(project)  # Saving a connection never submits a provider task.
        return edge

    def snapshot(self, project, node_id):
        nodes = {n["id"]: n for n in project["nodes"]}
        target = nodes.get(node_id)
        if target is None or target["kind"] not in ("image_task", "video_task", "chat_task"):
            raise ValueError("请选择生成或聊天任务节点")
        inputs = {}
        for edge in project["edges"]:
            if edge["target"] == node_id:
                source = nodes[edge["source"]]
                inputs[edge["port"]] = {"node_id": source["id"], "kind": source["kind"], "data": copy.deepcopy(source["data"])}
        result = {"node_id": node_id, "kind": target["kind"], "inputs": inputs,
                  "brief_id": project["brief_versions"][-1]["id"] if project["brief_versions"] else None,
                  "master_id": project["master_versions"][-1]["id"] if project["master_versions"] else None}
        result["fingerprint"] = fingerprint(result)
        return result

    def add_fact(self, project, field, value, source_id=None, status="待核实"):
        if status not in ("待核实", "已知事实", "创意假设"):
            raise ValueError("事实状态无效")
        if not str(field).strip() or not str(value).strip():
            raise ValueError("事实字段和值不能为空")
        if source_id and not any(s["id"] == source_id for s in project["sources"]):
            raise ValueError("来源文件不存在")
        fact = {"id": ident(), "field": field.strip(), "value": value.strip(), "source_id": source_id,
                "status": status, "created": stamp()}
        project["facts"].append(fact)
        self.save(project)
        return fact

    def review_fact(self, project, fact_id, status):
        if status not in ("待核实", "已知事实", "创意假设"):
            raise ValueError("事实状态无效")
        fact = next((f for f in project["facts"] if f["id"] == fact_id), None)
        if fact is None:
            raise ValueError("候选事实不存在")
        fact.setdefault("review_history", []).append({"from": fact["status"], "to": status, "at": stamp(), "reviewer": "local"})
        fact["status"] = status
        self.save(project)
        return fact

    def add_source(self, project, name, mime, encoded):
        if not isinstance(name, str) or not name.strip() or len(name) > 200:
            raise ValueError("文件名无效")
        if not isinstance(mime, str) or not (mime.startswith("image/") or mime in ("text/plain", "application/pdf", "application/vnd.openxmlformats-officedocument.wordprocessingml.document")):
            raise ValueError("文件类型暂不支持")
        try:
            raw = base64.b64decode(encoded, validate=True)
        except (ValueError, TypeError, binascii.Error) as exc:
            raise ValueError("文件编码无效") from exc
        if not raw or len(raw) > 25 * 1024 * 1024:
            raise ValueError("文件为空或超过 25 MB")
        source_id = ident()
        directory = self.root / "files" / project["id"]
        directory.mkdir(parents=True, exist_ok=True)
        (directory / source_id).write_bytes(raw)
        extracted = ""
        if mime == "text/plain":
            try:
                extracted = raw.decode("utf-8-sig")[:12000]
            except UnicodeDecodeError:
                pass
        source = {"id": source_id, "name": name, "mime": mime, "sha256": hashlib.sha256(raw).hexdigest(),
                  "bytes": len(raw), "created": stamp(),
                  "extracted_text": extracted, "parse_status": "可提取文本" if extracted.strip() else "需人工查看"}
        project["sources"].append(source)
        self.save(project)
        return source

    def source_bytes(self, project, source_id):
        source = next((s for s in project["sources"] if s["id"] == source_id), None)
        if source is None:
            raise ValueError("来源文件不存在")
        raw = (self.root / "files" / project["id"] / source_id).read_bytes()
        if hashlib.sha256(raw).hexdigest() != source["sha256"]:
            raise ValueError("来源文件校验失败")
        return source, raw

    def confirm_brief(self, project, fact_ids):
        selected = [f for f in project["facts"] if f["id"] in fact_ids]
        if not selected or len(selected) != len(set(fact_ids)) or any(f["status"] != "已知事实" for f in selected):
            raise ValueError("简报只能包含逐项确认的已知事实")
        version = {"id": ident(), "facts": copy.deepcopy(selected), "created": stamp()}
        project["brief_versions"].append(version)
        self.save(project)
        return version

    def confirm_master(self, project, asset_ids, inferred_details=None):
        if not project["brief_versions"]:
            raise ValueError("请先确认产品简报")
        if not isinstance(asset_ids, list) or len(asset_ids) != 3 or len(set(asset_ids)) != 3:
            raise ValueError("请选择正面、侧面和背面各一张候选图")
        chosen = [s for s in project["sources"] if s["id"] in asset_ids]
        if len(chosen) != 3 or any(s.get("origin") != "SeeAny candidate" for s in chosen):
            raise ValueError("母版只能使用已生成的三视图候选图")
        if {s.get("view_label") for s in chosen} != {"正面", "侧面", "背面"} or len({s.get("candidate_group") for s in chosen}) != 1:
            raise ValueError("三视图必须来自同一候选组，且覆盖正面、侧面和背面")
        if any(s.get("brief_id") != project["brief_versions"][-1]["id"] for s in chosen):
            raise ValueError("三视图候选须基于当前已确认简报")
        version = {"id": ident(), "brief_id": project["brief_versions"][-1]["id"],
                   "asset_ids": list(asset_ids), "inferred_details": copy.deepcopy(inferred_details or []), "created": stamp()}
        project["master_versions"].append(version)
        self.save(project)
        return version

    def approve_direction(self, project, direction_id):
        direction = next((d for d in project["directions"] if d["id"] == direction_id), None)
        if not direction or direction["brief_id"] != project["brief_versions"][-1]["id"] or direction["master_id"] != project["master_versions"][-1]["id"]:
            raise ValueError("广告方向不存在或上游版本已变化")
        project["direction_approval"] = direction_id
        self.save(project)
        return direction

    def add_script(self, project, text):
        if not project["direction_approval"] or not isinstance(text, str) or not text.strip():
            raise ValueError("请先选择广告方向并填写脚本")
        direction = next((d for d in project["directions"] if d["id"] == project["direction_approval"]), None)
        if not direction or direction["brief_id"] != project["brief_versions"][-1]["id"] or direction["master_id"] != project["master_versions"][-1]["id"]:
            raise ValueError("广告方向的上游版本已变化，请重新选择")
        version = {"id": ident(), "direction_id": project["direction_approval"], "brief_id": project["brief_versions"][-1]["id"],
                   "master_id": project["master_versions"][-1]["id"], "text": text.strip(), "created": stamp()}
        project["script_versions"].append(version)
        self.save(project)
        return version

    def approve_script(self, project, script_id):
        script = next((s for s in project["script_versions"] if s["id"] == script_id), None)
        if not script or script["direction_id"] != project["direction_approval"] or script["brief_id"] != project["brief_versions"][-1]["id"] or script["master_id"] != project["master_versions"][-1]["id"]:
            raise ValueError("脚本不存在或上游版本已变化")
        project["script_approval"] = script_id
        self.save(project)
        return script

    def add_storyboard(self, project, shots):
        if not project["script_approval"] or not isinstance(shots, list) or not shots:
            raise ValueError("请先批准脚本并填写分镜")
        script = next((s for s in project["script_versions"] if s["id"] == project["script_approval"]), None)
        if not script or script["brief_id"] != project["brief_versions"][-1]["id"] or script["master_id"] != project["master_versions"][-1]["id"]:
            raise ValueError("脚本的上游版本已变化，请重新批准")
        cleaned = []
        for shot in shots:
            if not isinstance(shot, dict) or not str(shot.get("visual") or "").strip():
                raise ValueError("分镜缺少画面描述")
            try:
                duration = float(shot["duration"])
            except (KeyError, ValueError, TypeError) as exc:
                raise ValueError("分镜时长无效") from exc
            if not 0.5 <= duration <= 15:
                raise ValueError("单镜时长须在 0.5 至 15 秒之间")
            cleaned.append({"id": ident(), "visual": str(shot["visual"]).strip(), "duration": duration,
                            "reference_asset_id": shot.get("reference_asset_id"), "caption": str(shot.get("caption") or "")})
        version = {"id": ident(), "script_id": project["script_approval"], "master_id": project["master_versions"][-1]["id"],
                   "shots": cleaned, "created": stamp()}
        project["storyboard_versions"].append(version)
        self.save(project)
        return version

    def approve_storyboard(self, project, storyboard_id):
        storyboard = next((s for s in project["storyboard_versions"] if s["id"] == storyboard_id), None)
        if not storyboard or storyboard["script_id"] != project["script_approval"] or storyboard["master_id"] != project["master_versions"][-1]["id"]:
            raise ValueError("分镜不存在或上游版本已变化")
        approved_assets = set(project["master_versions"][-1]["asset_ids"])
        if any(shot["reference_asset_id"] not in approved_assets for shot in storyboard["shots"]):
            raise ValueError("每个镜头必须引用当前已确认母版素材")
        project["storyboard_approval"] = storyboard_id
        self.save(project)
        return storyboard
