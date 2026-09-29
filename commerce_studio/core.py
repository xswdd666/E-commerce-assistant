"""Durable project state and typed canvas dependencies.

This module has no dependency on the legacy web application or its budget gate.
"""

from __future__ import annotations

import copy
import base64
import binascii
import hashlib
import json
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
                   "nodes": [], "edges": [], "tasks": [], "costs": [], "chat": []}
        self.save(project)
        return project

    def list(self):
        return [{"id": p["id"], "name": p["name"], "updated": p["updated"]} for path in self.root.glob("*.json")
                if (p := json.loads(path.read_text(encoding="utf-8")))]

    def load(self, project_id):
        try:
            return json.loads(self._path(project_id).read_text(encoding="utf-8"))
        except FileNotFoundError as exc:
            raise ValueError("项目不存在") from exc

    def save(self, project):
        with self.lock:
            project["updated"] = stamp()
            path = self._path(project["id"])
            tmp = path.with_suffix(".tmp")
            tmp.write_text(json.dumps(project, ensure_ascii=False, indent=2), encoding="utf-8")
            tmp.replace(path)

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

    def add_source(self, project, name, mime, encoded):
        if not isinstance(name, str) or not name.strip() or len(name) > 200:
            raise ValueError("文件名无效")
        if not isinstance(mime, str) or not (mime.startswith("image/") or mime in ("text/plain", "application/pdf")):
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
        source = {"id": source_id, "name": name, "mime": mime, "sha256": hashlib.sha256(raw).hexdigest(),
                  "bytes": len(raw), "created": stamp()}
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
        version = {"id": ident(), "brief_id": project["brief_versions"][-1]["id"],
                   "asset_ids": list(asset_ids), "inferred_details": copy.deepcopy(inferred_details or []), "created": stamp()}
        project["master_versions"].append(version)
        self.save(project)
        return version
