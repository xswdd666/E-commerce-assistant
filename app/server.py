"""Dependency-free local server for the advertising creation workbench."""

from __future__ import annotations

import base64
import copy
import hashlib
import io
import json
import mimetypes
import os
import shutil
import sqlite3
import subprocess
import sys
import tempfile
import threading
import uuid
import webbrowser
import zipfile
from contextlib import contextmanager
from datetime import datetime, timezone
from html import escape
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, quote, urlparse

try:
    from .providers import ProviderError, deepseek_complete
    from . import seeany, flova
except ImportError:
    from providers import ProviderError, deepseek_complete
    import seeany, flova

ROOT = Path(__file__).resolve().parent.parent
WEB = ROOT / "app" / "web"
DATA = ROOT / "data"
FILES = DATA / "files"
DB = DATA / "workbench.sqlite3"
SECRETS = DATA / "provider-settings.json"
ENV_FILE = ROOT / ".env"
MAX_BODY = 35 * 1024 * 1024
LOCK = threading.RLock()
SEEANY_RUN_LOCK = threading.Lock()
FLOVA_RUN_LOCK = threading.Lock()
STAGES = ["三视图", "试图", "套图", "视频镜头", "AI辅助"]
KINDS = ["主图", "场景图", "卖点图", "卖点图", "细节图", "规格或使用图"]


def now():
    return datetime.now(timezone.utc).isoformat()


def uid():
    return uuid.uuid4().hex[:12]


def connect():
    DATA.mkdir(exist_ok=True)
    FILES.mkdir(exist_ok=True)
    db = sqlite3.connect(DB)
    db.execute("CREATE TABLE IF NOT EXISTS projects (id TEXT PRIMARY KEY, name TEXT NOT NULL, updated TEXT NOT NULL, state TEXT NOT NULL)")
    db.commit()
    return db


@contextmanager
def database():
    db = connect()
    try:
        yield db
        db.commit()
    finally:
        db.close()


def new_project(name):
    pid = uid()
    return {
        "id": pid, "organization_id": "local", "name": name.strip(), "created": now(), "updated": now(),
        "archived": False, "platform": "拼多多", "sources": [], "facts": [], "brief_versions": [],
        "masters": [], "master_approval": None, "details": [], "directions": [], "direction_approval": None,
        "script_versions": [], "script_approval": None, "storyboard_versions": [], "storyboard_approval": None,
        "prompts": [], "assets": [], "adopted_assets": {}, "gallery": [], "clips": [],
        "nodes": [{"id": stage, "stage": stage, "x": x, "y": y} for stage, x, y in (
            ("source", 70, 110), ("brief", 320, 110), ("master", 570, 110),
            ("direction", 820, 110), ("script", 1070, 110), ("storyboard", 1320, 110),
            ("prompt", 570, 320), ("asset", 820, 320), ("gallery", 1070, 320),
            ("video", 1570, 110), ("delivery", 1590, 320))],
        "edges": [], "tasks": [], "decisions": [], "costs": [], "chat": [], "jev_observations": [],
        "settings": {"unattended_limit": 30, "total_budget": 320, "stage_reserve": {"三视图": 30, "试图": 30, "套图": 80, "视频镜头": 160, "AI辅助": 20}},
        "external": {"flova_project_id": "", "flova_url": ""}, "deliverables": [],
    }


def save(p):
    p["updated"] = now()
    with LOCK, database() as db:
        db.execute("INSERT OR REPLACE INTO projects VALUES (?,?,?,?)", (p["id"], p["name"], p["updated"], json.dumps(p, ensure_ascii=False)))


def load(pid):
    with LOCK, database() as db:
        row = db.execute("SELECT state FROM projects WHERE id=?", (pid,)).fetchone()
    if not row:
        raise ValueError("项目不存在")
    return json.loads(row[0])


def listing(query=""):
    with LOCK, database() as db:
        rows = db.execute("SELECT state FROM projects ORDER BY updated DESC").fetchall()
    projects = [json.loads(row[0]) for row in rows]
    if query:
        q = query.lower()
        projects = [p for p in projects if q in json.dumps({k: p[k] for k in ("name", "sources", "prompts", "assets")}, ensure_ascii=False).lower()]
    return [{"id": p["id"], "name": p["name"], "updated": p["updated"], "archived": p["archived"], "sources": len(p["sources"])} for p in projects]


def record(p, kind, target, choice, note=""):
    p["decisions"].append({"id": uid(), "kind": kind, "target": target, "choice": choice, "note": note, "at": now()})


def add_source(p, payload):
    name = Path(payload.get("name", "file")).name
    raw = base64.b64decode(payload["data"], validate=True)
    if len(raw) > 25 * 1024 * 1024:
        raise ValueError("单个文件不能超过 25 MB")
    sid = uid()
    suffix = Path(name).suffix.lower()[:12]
    folder = FILES / p["id"]
    folder.mkdir(parents=True, exist_ok=True)
    (folder / f"{sid}{suffix}").write_bytes(raw)
    mime = mimetypes.guess_type(name)[0] or "application/octet-stream"
    text = ""
    if suffix in (".txt", ".md", ".csv", ".json"):
        text = raw.decode("utf-8-sig", errors="replace")[:30000]
    elif suffix == ".docx":
        try:
            from xml.etree import ElementTree as ET
            with zipfile.ZipFile(io.BytesIO(raw)) as z:
                xml = ET.fromstring(z.read("word/document.xml"))
            text = " ".join(t.text or "" for t in xml.iter() if t.tag.endswith("}t"))[:30000]
        except Exception:
            pass
    item = {"id": sid, "name": name, "path": f"{sid}{suffix}", "mime": mime, "size": len(raw), "sha256": hashlib.sha256(raw).hexdigest(), "text": text, "parse_status": "可读取" if text else "需人工查看", "created": now()}
    p["sources"].append(item)
    return item


def add_asset(p, payload):
    source = add_source(p, payload)
    asset = {"id": uid(), "source_id": source["id"], "name": payload.get("label") or source["name"], "kind": payload.get("kind", "候选图"), "parent_id": payload.get("parent_id"), "prompt_id": payload.get("prompt_id"), "reference_ids": payload.get("reference_ids", []), "origin": payload.get("origin", "手动导入"), "created": now(), "review": "待审核", "reject_reason": ""}
    p["assets"].append(asset)
    return asset


def brief(p):
    return p["brief_versions"][-1] if p["brief_versions"] else None


def approval(p, field, versions):
    aid = p.get(field)
    return any(v["id"] == aid for v in p[versions]) if isinstance(aid, str) else bool(aid)


def invalidated(p, upstream):
    order = ["brief", "master", "direction", "script", "storyboard"]
    affected = order[order.index(upstream) + 1:]
    for n in p["nodes"]:
        if n.get("stage") in affected or n.get("stage") in ("asset", "video", "gallery"):
            n["stale"] = True


def current_node(p, stage):
    for n in p["nodes"]:
        if n.get("stage") == stage:
            n["stale"] = False


def cost_check(p, stage, estimate, unattended=False):
    if stage not in STAGES:
        raise ValueError("未知费用阶段")
    if estimate is None or estimate == "":
        return "费用未知，须人工核对"
    try:
        estimate = float(estimate)
    except (ValueError, TypeError):
        return "费用估算无效"
    if estimate < 0:
        return "费用估算不能为负"
    spent = sum(float(c["amount"]) for c in p["costs"] if c.get("status") == "确认")
    def reserves_budget(t):
        if t.get("estimate") is None:
            return False
        if t["status"] in ("已排队", "远端运行中"):
            return True
        return t.get("provider") == "SeeAny" and t["status"] in ("待审核", "完成", "部分失败", "失败", "待核对") and not any(c.get("task_id") == t["id"] and c.get("status") == "确认" for c in p["costs"])
    reserved = sum(float(t["estimate"]) for t in p["tasks"] if reserves_budget(t))
    if spent + reserved + estimate > float(p["settings"]["total_budget"]):
        return "超过项目总预算"
    stage_spent = sum(float(c["amount"]) for c in p["costs"] if c.get("status") == "确认" and c["stage"] == stage)
    stage_reserved = sum(float(t["estimate"]) for t in p["tasks"] if t["stage"] == stage and reserves_budget(t))
    if stage_spent + stage_reserved + estimate > float(p["settings"]["stage_reserve"].get(stage, 0)):
        return "超过该阶段预留额度"
    if unattended:
        unattended_spent = sum(float(c["amount"]) for c in p["costs"] if c.get("status") == "确认" and any(t["id"] == c["task_id"] and t.get("unattended") for t in p["tasks"]))
        unattended_reserved = sum(float(t["estimate"]) for t in p["tasks"] if t.get("unattended") and t["status"] in ("已排队", "远端运行中") and t.get("estimate") is not None)
        if unattended_spent + unattended_reserved + estimate > float(p["settings"]["unattended_limit"]):
            return "超过无人值守额度"
    return ""


def execute(p, action, body):
    if action == "rename":
        p["name"] = str(body["name"]).strip()[:100]
    elif action == "archive":
        p["archived"] = bool(body["archived"])
    elif action == "source":
        return add_source(p, body)
    elif action == "fact":
        value = str(body.get("value", "")).strip()
        if not value:
            raise ValueError("事实内容不能为空")
        status = body.get("status", "待核实")
        if status not in ("已知事实", "待核实", "创意假设"):
            raise ValueError("事实状态无效")
        item = {"id": uid(), "field": str(body.get("field", "其他")), "value": value, "status": status, "source_ids": body.get("source_ids", []), "confirmed": False, "created": now()}
        p["facts"].append(item)
        return item
    elif action == "fact_review":
        item = next(f for f in p["facts"] if f["id"] == body["id"])
        if body.get("confirmed") and item["status"] != "已知事实":
            raise ValueError("只有已知事实可以纳入正式简报")
        item["confirmed"] = bool(body.get("confirmed"))
        record(p, "产品事实", item["id"], "确认" if item["confirmed"] else "撤回", body.get("note", ""))
    elif action == "fact_edit":
        item = next(f for f in p["facts"] if f["id"] == body["id"])
        if item["confirmed"]:
            raise ValueError("已确认事实请创建新候选再修改")
        status = body.get("status", item["status"])
        if status not in ("已知事实", "待核实", "创意假设"):
            raise ValueError("事实状态无效")
        item["status"] = status
        item["value"] = str(body.get("value", item["value"]))
        record(p, "候选事实", item["id"], "人工核对为" + status, body.get("note", ""))
    elif action == "brief_confirm":
        facts = [copy.deepcopy(f) for f in p["facts"] if f["confirmed"] and f["status"] == "已知事实"]
        if not facts:
            raise ValueError("至少确认一条已知事实")
        item = {"id": uid(), "number": len(p["brief_versions"]) + 1, "facts": facts, "created": now()}
        p["brief_versions"].append(item)
        invalidated(p, "brief")
        current_node(p, "brief")
        p["master_approval"] = None
        p["direction_approval"] = None
        p["script_approval"] = None
        p["storyboard_approval"] = None
        record(p, "产品简报", item["id"], "确认")
        return item
    elif action == "master":
        if not brief(p):
            raise ValueError("请先确认产品简报")
        groups = {m["group"] for m in p["masters"]}
        group = int(body.get("group", 1))
        if group not in (1, 2) or (group not in groups and len(groups) >= 2):
            raise ValueError("最多两组三视图候选")
        item = {"id": uid(), "group": group, "view": body.get("view", "正面"), "asset_id": body["asset_id"], "source_ids": body.get("source_ids", []), "inferred": bool(body.get("inferred", False)), "brief_id": brief(p)["id"], "created": now()}
        p["masters"].append(item)
        return item
    elif action == "master_approve":
        group = int(body["group"])
        views = [m for m in p["masters"] if m["group"] == group]
        if not {m["view"] for m in views} >= {"正面", "侧面", "背面"}:
            raise ValueError("母版需要正面、侧面、背面三个视角")
        p["master_approval"] = {"group": group, "brief_id": brief(p)["id"], "at": now()}
        invalidated(p, "master")
        current_node(p, "master")
        p["direction_approval"] = None
        p["script_approval"] = None
        p["storyboard_approval"] = None
        record(p, "三视图母版", str(group), "批准整体用于创作", body.get("note", ""))
    elif action == "detail":
        item = {"id": uid(), "description": body["description"], "source_ids": body.get("source_ids", []), "verified": bool(body.get("verified")) and bool(body.get("source_ids")), "created": now()}
        p["details"].append(item)
        record(p, "推断细节", item["id"], "已核实" if item["verified"] else "未核实")
        return item
    elif action == "direction":
        if not p["master_approval"]:
            raise ValueError("请先批准母版")
        item = {"id": uid(), "title": body["title"], "audience": body.get("audience", ""), "opening": body.get("opening", ""), "selling_point": body.get("selling_point", ""), "ending": body.get("ending", ""), "brief_id": brief(p)["id"], "created": now()}
        p["directions"].append(item)
        return item
    elif action == "direction_approve":
        if not any(d["id"] == body["id"] for d in p["directions"]):
            raise ValueError("方向不存在")
        p["direction_approval"] = body["id"]
        invalidated(p, "direction")
        current_node(p, "direction")
        p["script_approval"] = None
        p["storyboard_approval"] = None
        record(p, "广告方向", body["id"], "批准")
    elif action == "script":
        if not p["direction_approval"]:
            raise ValueError("请先选择广告方向")
        item = {"id": uid(), "text": body["text"], "direction_id": p["direction_approval"], "brief_id": brief(p)["id"], "created": now()}
        p["script_versions"].append(item)
        invalidated(p, "script")
        return item
    elif action == "script_approve":
        if not any(s["id"] == body["id"] for s in p["script_versions"]):
            raise ValueError("脚本不存在")
        p["script_approval"] = body["id"]
        current_node(p, "script")
        record(p, "脚本", body["id"], "批准")
    elif action == "storyboard":
        if not p["script_approval"]:
            raise ValueError("请先批准脚本")
        shots = body.get("shots", [])
        if not shots or any(not s.get("description") for s in shots):
            raise ValueError("至少需要一个完整镜头")
        for s in shots:
            s.setdefault("duration", 3)
            s.setdefault("ratio", "9:16")
            s.setdefault("master_group", p["master_approval"]["group"])
        item = {"id": uid(), "shots": shots, "script_id": p["script_approval"], "created": now()}
        p["storyboard_versions"].append(item)
        invalidated(p, "storyboard")
        return item
    elif action == "storyboard_approve":
        item = next((s for s in p["storyboard_versions"] if s["id"] == body["id"]), None)
        if not item:
            raise ValueError("分镜不存在")
        unverified = [d["id"] for d in p["details"] if not d["verified"]]
        if any(any(d in shot.get("detail_ids", []) for d in unverified) for shot in item["shots"]):
            raise ValueError("分镜引用了未核实的推断细节")
        p["storyboard_approval"] = item["id"]
        current_node(p, "storyboard")
        record(p, "分镜", item["id"], "批准")
    elif action == "prompt":
        fields = {k: str(body.get(k, "")) for k in ("identity", "scene", "composition", "lighting", "style", "ratio", "prohibited", "purpose")}
        parent = body.get("parent_id")
        if parent and not any(x["id"] == parent for x in p["prompts"]):
            raise ValueError("父提示词不存在")
        item = {"id": uid(), "parent_id": parent, "fields": fields, "text": "\n".join(f"{k}: {v}" for k, v in fields.items() if v), "brief_id": brief(p)["id"] if brief(p) else None, "created": now()}
        p["prompts"].append(item)
        return item
    elif action == "asset":
        return add_asset(p, body)
    elif action == "asset_review":
        item = next(a for a in p["assets"] if a["id"] == body["id"])
        choice = body.get("choice")
        if choice not in ("通过真实性", "废图"):
            raise ValueError("无效的审核选择")
        item["review"] = choice
        item["reject_reason"] = body.get("reason", "")
        record(p, "候选图", item["id"], choice, item["reject_reason"])
    elif action == "asset_reference":
        item = next(a for a in p["assets"] if a["id"] == body["id"])
        ids = body.get("source_ids", [])
        if any(not any(s["id"] == sid and s["mime"].startswith("image/") for s in p["sources"]) for sid in ids):
            raise ValueError("参考图须为已保存的本地图片")
        item["reference_ids"] = ids
        record(p, "资产参考图", item["id"], "更新来源", ",".join(ids))
    elif action == "adopt":
        item = next(a for a in p["assets"] if a["id"] == body["id"])
        if item["review"] != "通过真实性":
            raise ValueError("未通过真实性审核的图片不能采用")
        slot = body.get("slot", "产品主体")
        p["adopted_assets"][slot] = item["id"]
        current_node(p, "asset")
        record(p, "采用资产", item["id"], slot)
    elif action == "gallery":
        if not p["master_approval"]:
            raise ValueError("请先批准母版")
        items = body.get("items") or [{"id": uid(), "kind": k, "prompt": "", "asset_id": None, "title": "", "subtitle": "", "x": 50, "y": 10} for k in KINDS]
        item = {"id": uid(), "items": items, "master_group": p["master_approval"]["group"], "approved": False, "created": now()}
        p["gallery"].append(item)
        return item
    elif action == "gallery_approve":
        item = next(g for g in p["gallery"] if g["id"] == body["id"])
        item["approved"] = True
        current_node(p, "gallery")
        record(p, "套图策划", item["id"], "批准")
    elif action == "clip":
        asset = next(a for a in p["assets"] if a["id"] == body["asset_id"])
        source = next(s for s in p["sources"] if s["id"] == asset["source_id"])
        if not source["mime"].startswith("video/"):
            raise ValueError("请选择视频资产")
        item = {"id": uid(), "asset_id": asset["id"], "start": max(0, float(body.get("start", 0))), "end": float(body.get("end", 0)), "speed": float(body.get("speed", 1)), "caption": body.get("caption", "")}
        if item["end"] <= item["start"] or not 0.5 <= item["speed"] <= 2:
            raise ValueError("裁剪时间或变速值无效")
        p["clips"].append(item)
        return item
    elif action == "clip_update":
        p["clips"] = body["clips"]
    elif action == "settings":
        s = p["settings"]
        for k in ("unattended_limit", "total_budget"):
            if k in body:
                s[k] = max(0, float(body[k]))
        if "stage_reserve" in body:
            s["stage_reserve"] = {k: max(0, float(body["stage_reserve"].get(k, 0))) for k in STAGES}
    elif action == "task":
        stage = body["stage"]
        gate = {"三视图": bool(brief(p)), "试图": bool(p["master_approval"]), "套图": any(g["approved"] for g in p["gallery"]), "视频镜头": bool(p["storyboard_approval"]), "AI辅助": bool(brief(p))}[stage]
        if not gate:
            raise ValueError("上游审核尚未完成")
        reason = cost_check(p, stage, body.get("estimate"), bool(body.get("unattended")))
        adopted = []
        for slot, aid in p["adopted_assets"].items():
            a = next((a for a in p["assets"] if a["id"] == aid), None)
            if a:
                adopted.append({"slot": slot, "asset_id": aid, "prompt_id": a.get("prompt_id"), "reference_ids": a.get("reference_ids", [])})
        item = {"id": uid(), "stage": stage, "provider": body.get("provider", ""), "description": body.get("description", ""), "estimate": float(body["estimate"]) if body.get("estimate") not in (None, "") else None, "unattended": bool(body.get("unattended")), "status": "费用阻止" if reason else "待批准", "reason": reason or "等待核对供应商能力与明确提交", "remote_id": "", "idempotency_key": uid(), "attempts": 0, "input_versions": {"brief": brief(p)["id"] if brief(p) else None, "master": p["master_approval"], "storyboard": p["storyboard_approval"], "adopted": adopted}, "created": now()}
        p["tasks"].append(item)
        return item
    elif action == "task_update":
        item = next(t for t in p["tasks"] if t["id"] == body["id"])
        status = body["status"]
        if status not in ("待批准", "已排队", "远端运行中", "待审核", "完成", "部分失败", "失败", "费用阻止", "等待网络"):
            raise ValueError("任务状态无效")
        previous = item["status"]
        if status in ("已排队", "远端运行中") and previous not in ("已排队", "远端运行中"):
            reason = cost_check(p, item["stage"], item["estimate"], item.get("unattended", False))
            if reason:
                raise ValueError(reason)
            if previous == "等待网络" and not body.get("reconfirm"):
                raise ValueError("网络恢复后须重新确认当前版本和费用")
        item["status"] = status
        item["remote_id"] = body.get("remote_id", item["remote_id"])
        item["reason"] = body.get("reason", item["reason"])
        if status == "已排队" and previous != "已排队":
            item["attempts"] += 1
            if item["attempts"] > 3:
                raise ValueError("最多两次自动重试")
        record(p, "任务", item["id"], status, item["reason"])
    elif action == "cost":
        task = next(t for t in p["tasks"] if t["id"] == body["task_id"])
        if any(c.get("task_id") == task["id"] and c.get("status") == "确认" for c in p["costs"]):
            raise ValueError("该任务已记录实际费用")
        amount = float(body["amount"])
        if amount < 0:
            raise ValueError("费用不能为负")
        item = {"id": uid(), "task_id": task["id"], "provider": task["provider"], "stage": task["stage"], "amount": amount, "unit": "CNY", "status": body.get("status", "确认"), "created": now()}
        p["costs"].append(item)
        return item
    elif action == "chat":
        item = {"id": uid(), "role": body.get("role", "user"), "text": str(body.get("text", ""))[:10000], "at": now()}
        p["chat"].append(item)
        return item
    elif action == "external":
        p["external"].update({k: str(body.get(k, "")) for k in ("flova_project_id", "flova_url")})
    elif action == "node":
        item = body["node"]
        existing = next((n for n in p["nodes"] if n["id"] == item["id"]), None)
        if existing:
            existing.update(item)
        else:
            p["nodes"].append(item)
    elif action == "edge":
        source = next(n for n in p["nodes"] if n["id"] == body["source"])
        target = next(n for n in p["nodes"] if n["id"] == body["target"])
        allowed = {"source": ["brief", "master", "asset"], "brief": ["master", "direction", "gallery", "prompt"], "master": ["direction", "prompt", "asset", "gallery", "storyboard"], "direction": ["script"], "script": ["storyboard"], "storyboard": ["video"], "prompt": ["asset"], "asset": ["master", "gallery", "video"], "video": ["delivery"], "gallery": ["delivery"]}
        if target["stage"] not in allowed.get(source["stage"], []):
            raise ValueError("节点类型不兼容")
        if source["id"] == target["id"]:
            raise ValueError("不能连接自身")
        item = {"id": uid(), "source": source["id"], "target": target["id"]}
        p["edges"].append(item)
        return item
    elif action == "edge_delete":
        p["edges"] = [e for e in p["edges"] if e["id"] != body["id"]]
    else:
        raise ValueError("未知操作")
    return {"ok": True}


def stored_provider_settings():
    try:
        return json.loads(SECRETS.read_text(encoding="utf-8"))
    except (FileNotFoundError, ValueError):
        return {}


def provider_settings():
    config = stored_provider_settings()
    if ENV_FILE.is_file():
        for line in ENV_FILE.read_text(encoding="utf-8-sig").splitlines():
            line = line.strip()
            for env_name, config_name in (("DEEPSEEK_API_KEY", "deepseek_api_key"), ("SEEANY_API_KEY", "seeany_api_key")):
                if line.startswith(env_name + "="):
                    value = line.split("=", 1)[1].strip().strip('"').strip("'")
                    if value:
                        config[config_name] = value
    for env_name, config_name in (("DEEPSEEK_API_KEY", "deepseek_api_key"), ("SEEANY_API_KEY", "seeany_api_key")):
        if os.getenv(env_name):
            config[config_name] = os.environ[env_name]
    return config


def provider_public():
    config = provider_settings()
    return {"deepseek_configured": bool(config.get("deepseek_api_key")), "seeany_configured": bool(config.get("seeany_api_key")), "jev_configured": bool(config.get("jev_api_key")), "input_cny_per_m": config.get("input_cny_per_m", ""), "output_cny_per_m": config.get("output_cny_per_m", "")}


def update_provider_settings(body):
    config = stored_provider_settings()
    for name in ("deepseek_api_key", "seeany_api_key", "jev_api_key"):
        if body.get(name):
            config[name] = str(body[name]).strip()
    for name in ("input_cny_per_m", "output_cny_per_m"):
        if name in body:
            value = body[name]
            config[name] = max(0, float(value)) if value not in ("", None) else ""
    DATA.mkdir(exist_ok=True)
    SECRETS.write_text(json.dumps(config, ensure_ascii=False), encoding="utf-8")
    return provider_public()


SEEANY_KINDS = {
    "master": ("三视图", 464, "multiview", "/api/ai/batchsmarttask", "三视图候选"),
    "preview": ("试图", 113, "smartImg", "/api/ai/smarttask", "提示词试图"),
    "plan": ("套图", 72373, "groupPlanning", "/api/ai/groupplan", "套图策划"),
    "gallery": ("套图", 2373, "imageSets", "/api/ai/grouptask", "正式套图"),
}


def seeany_quote(p, body):
    kind = body.get("kind")
    if kind not in SEEANY_KINDS:
        raise ValueError("未知 SeeAny 生成类型")
    stage, type_id, ai_type, path, label = SEEANY_KINDS[kind]
    current_brief = brief(p)
    if not current_brief:
        raise ValueError("请先确认产品简报")
    if kind != "master" and not p["master_approval"]:
        raise ValueError("请先批准三视图母版")
    source_ids = body.get("source_ids") or []
    if not isinstance(source_ids, list) or not source_ids or len(source_ids) > 6 or len(set(source_ids)) != len(source_ids):
        raise ValueError("请选择 1～6 张不重复的商品参考图")
    sources = [safe_file(p, sid)[0] for sid in source_ids]
    if any(not s["mime"].startswith("image/") for s in sources):
        raise ValueError("参考资料必须是图片")
    facts = [f"{f['field']}：{f['value']}" for f in current_brief["facts"]]
    payload = {"inputImgs": []}
    extra = {}
    if kind == "master":
        group = int(body.get("group", 1))
        if group not in (1, 2):
            raise ValueError("母版候选组只能是 1 或 2")
        views = str(body.get("views", "")).strip()
        view_label = str(body.get("view_label", "")).strip()
        if not views or view_label not in ("正面", "侧面", "背面", "顶部", "其他"):
            raise ValueError("请填写 SeeAny 视角预设并选择本地视角")
        payload.update({"aiTypeId": type_id, "aiType": ai_type, "views": views, "imgNum": 1, "imgRatio": "1:1", "mode": "nano-banana-pro", "size": "1K"})
        extra = {"group": group, "view_label": view_label}
    elif kind == "preview":
        prompt = next((x for x in p["prompts"] if x["id"] == body.get("prompt_id")), None)
        if not prompt or prompt.get("brief_id") != current_brief["id"]:
            raise ValueError("请选择基于当前简报的提示词版本")
        ratio = prompt["fields"].get("ratio") or "1:1"
        if ratio not in ("1:1", "3:4", "4:3", "9:16", "16:9", "3:2", "2:3"):
            raise ValueError("提示词画幅请填写 SeeAny 支持的比例，如 1:1 或 3:4")
        payload.update({"aiTypeId": type_id, "aiType": ai_type, "prompt": prompt["text"], "imgNum": 1, "imgRatio": ratio, "mode": "nano2", "size": "1K"})
        extra = {"prompt_id": prompt["id"]}
    elif kind == "plan":
        payload.update({"productName": p["name"], "sellingPoints": "\n".join(facts), "platform": p["platform"], "desLang": "中文", "customRequirement": str(body.get("requirement", "")).strip()[:2000]})
    else:
        gallery = next((g for g in p["gallery"] if g["id"] == body.get("gallery_id") and g["approved"]), None)
        if not gallery or not gallery["items"] or any(not str(i.get("prompt", "")).strip() for i in gallery["items"]):
            raise ValueError("请批准已填写全部单张提示词的套图方案")
        payload.update({"aiTypeId": type_id, "aiType": ai_type, "productName": p["name"], "sellingPoints": "\n".join(facts), "platform": p["platform"], "desLang": "中文",
                        "groups": [{"mode": "nano-banana-pro", "name": i["kind"], "cateName": i["kind"], "size": "1K", "imgNum": 1, "imgRatio": i.get("imgRatio") or "1:1", "prompt": i["prompt"], "options": {"电商平台": p["platform"], "文案语种": "中文"}, "refImgs": []} for i in gallery["items"]]})
        extra = {"gallery_id": gallery["id"]}
    estimate = 0.10 if kind == "plan" else body.get("estimate")
    try:
        estimate = float(estimate)
    except (ValueError, TypeError):
        estimate = None
    if estimate is not None and estimate <= 0:
        raise ValueError("预计费用必须大于零")
    reason = cost_check(p, stage, estimate)
    if not provider_public()["seeany_configured"]:
        reason = "请先在 .env 中填写 SEEANY_API_KEY"
    request = {"kind": kind, "source_ids": source_ids, "payload": payload, "extra": extra, "estimate": estimate, "brief_id": current_brief["id"], "master_approval": p["master_approval"]}
    digest = hashlib.sha256(json.dumps(request, ensure_ascii=False, sort_keys=True).encode()).hexdigest()
    return {"kind": kind, "label": label, "stage": stage, "path": path, "type_id": type_id, "estimate": estimate, "reason": reason, "request": request, "request_preview": payload, "fingerprint": digest,
            "source_names": [s["name"] for s in sources], "summary": {"model": payload.get("mode", "平台默认"), "size": payload.get("size", "—"), "count": len(payload.get("groups", [])) or 1, "views": payload.get("views", "")}}


def run_seeany(p, body):
    quote = seeany_quote(p, body)
    if quote["reason"]:
        raise ValueError(quote["reason"])
    if quote["fingerprint"] != body.get("approved_fingerprint"):
        raise ValueError("输入或费用已变化，请重新预估并批准")
    request_id = str(body.get("request_id", ""))
    if len(request_id) < 8:
        raise ValueError("缺少请求标识")
    existing = next((t for t in p["tasks"] if t.get("idempotency_key") == request_id), None)
    if existing:
        return existing
    kind = quote["kind"]
    task = {"id": uid(), "stage": quote["stage"], "provider": "SeeAny", "description": quote["label"], "estimate": quote["estimate"], "unattended": False,
            "status": "远端运行中", "reason": "正在上传参考图；网络中断后不要直接重试提交", "remote_id": "", "idempotency_key": request_id,
            "attempts": 1, "input_versions": {"brief": brief(p)["id"], "master": p["master_approval"], **quote["request"]["extra"]},
            "seeany_kind": kind, "source_ids": quote["request"]["source_ids"], "imported_urls": [], "created": now()}
    p["tasks"].append(task)
    save(p)
    key = provider_settings()["seeany_api_key"]
    try:
        urls = []
        for sid in task["source_ids"]:
            source, path = safe_file(p, sid)
            urls.append(seeany.upload_image(key, source["name"], path.read_bytes()))
        payload = copy.deepcopy(quote["request"]["payload"])
        payload["inputImgs"] = urls
        response = seeany.submit(key, quote["path"], payload)
        if kind == "plan":
            items = response.get("items", [])
            if not isinstance(items, list) or not items:
                raise ValueError("SeeAny 套图策划没有返回可用图片方案")
            drafted = [{"id": uid(), "kind": str(i.get("cateName") or i.get("name") or "图片"), "prompt": str(i.get("prompt") or ""), "imgRatio": i.get("imgRatio") or "1:1", "asset_id": None, "title": "", "subtitle": "", "x": 50, "y": 10} for i in items[:12] if isinstance(i, dict)]
            if not drafted:
                raise ValueError("SeeAny 套图策划结果格式不正确")
            result = execute(p, "gallery", {"items": drafted})
            task["status"] = "待审核"
            task["result_gallery_id"] = result["id"]
            task["reason"] = "策划已写入新方案，等待逐张审核；实际扣费请按供应商记录"
        else:
            remote_id = (response.get("data") or {}).get("task_uuid")
            if not remote_id:
                raise ValueError("SeeAny 未返回 task_uuid，请先核对远端，勿重复提交")
            task["remote_id"] = remote_id
            task["reason"] = "已提交，点击任务中的“同步状态”导入结果；勿重复提交"
        record(p, "SeeAny", task["id"], task["status"], quote["label"])
        save(p)
        return task
    except Exception as exc:
        task["status"] = "待核对"
        task["reason"] = str(exc)[:300] + "；如远端可能已受理，请先核对，不要重复提交"
        save(p)
        raise ValueError(task["reason"]) from exc


def sync_seeany(p, task_id):
    task = next((t for t in p["tasks"] if t["id"] == task_id and t.get("provider") == "SeeAny"), None)
    if not task or not task.get("remote_id"):
        raise ValueError("此任务没有可查询的 SeeAny 远端标识")
    response = seeany.task_status(provider_settings().get("seeany_api_key"), task["remote_id"])
    data = response.get("data") or {}
    status_node = data.get("task") if isinstance(data.get("task"), dict) else data
    status = str(status_node.get("status") or status_node.get("task_status") or "").lower()
    if status in ("failed", "partial_failed"):
        task["status"] = "部分失败" if status == "partial_failed" else "失败"
    elif status == "succeeded":
        task["status"] = "待审核"
    else:
        task["status"] = "远端运行中"
    task["reason"] = f"SeeAny 状态：{status or '处理中'}"
    if status in ("succeeded", "partial_failed"):
        for url in seeany.result_assets(data):
            if url in task["imported_urls"]:
                continue
            raw, suffix = seeany.download_image(url)
            result = add_asset(p, {"name": f"seeany-{task['id']}-{len(task['imported_urls']) + 1}{suffix}", "data": base64.b64encode(raw).decode(),
                                   "label": f"{task['description']} 候选 {len(task['imported_urls']) + 1}", "kind": "候选图", "origin": "SeeAny", "reference_ids": task["source_ids"], "prompt_id": task["input_versions"].get("prompt_id")})
            task["imported_urls"].append(url)
            if task["seeany_kind"] == "master" and len(task["imported_urls"]) == 1:
                execute(p, "master", {"group": task["input_versions"]["group"], "view": task["input_versions"]["view_label"], "asset_id": result["id"], "source_ids": task["source_ids"], "inferred": True})
            save(p)
    save(p)
    return {"task": task, "remote_status": status, "imported": len(task["imported_urls"])}


def ai_request(p, kind, body):
    latest_brief = brief(p)
    if kind == "facts":
        source, _ = safe_file(p, body["source_id"])
        if not source["text"].strip():
            raise ValueError("此文件尚无可读文本，请人工查看或转成 TXT/DOCX")
        system = "你是商品资料整理助手。只返回 JSON 对象，格式：{\"facts\":[{\"field\":\"规格/卖点/使用场景/限制/外观/其他\",\"value\":\"原文可支持的表述\"}]}。不得把推断写成事实。"
        user = f"文件名：{source['name']}\n内容：{source['text'][:12000]}"
        json_mode = True
    elif kind == "directions":
        if not latest_brief or not p["master_approval"]:
            raise ValueError("请先确认简报和母版")
        system = "你是小家电广告策划。只返回 JSON 对象，格式：{\"directions\":[{\"title\":\"\",\"audience\":\"\",\"opening\":\"\",\"selling_point\":\"\",\"ending\":\"\"}]}。提供恰好三个不同方向，卖点只依据已确认事实，不杜撰规格。"
        user = "已确认产品事实：" + json.dumps([{"field": f["field"], "value": f["value"]} for f in latest_brief["facts"]], ensure_ascii=False)
        json_mode = True
    elif kind == "chat":
        if not latest_brief:
            raise ValueError("请先确认产品简报")
        user = str(body.get("text", "")).strip()[:4000]
        if not user:
            raise ValueError("请输入问题")
        system = "你是广告电商创作助手。已确认简报是唯一正式事实来源。没有证据的细节须说明为推断；你的建议不构成用户审核决定。已确认简报：" + json.dumps([{"field": f["field"], "value": f["value"]} for f in latest_brief["facts"]], ensure_ascii=False)
        json_mode = False
    elif kind == "prompt":
        parent = next((x for x in p["prompts"] if x["id"] == body.get("prompt_id")), None)
        if not parent:
            raise ValueError("请先保存提示词版本")
        user = "当前字段：" + json.dumps(parent["fields"], ensure_ascii=False) + "\n修改要求：" + str(body.get("text", ""))[:2000]
        system = "你是提示词字段编辑助手。只返回 JSON 对象，格式：{\"changes\":{\"scene\":\"新值\"}}。只返回明确要求修改的字段，不改产品身份和禁止变化项等其他字段。字段名可选 identity,scene,composition,lighting,style,ratio,prohibited,purpose。"
        json_mode = True
    else:
        raise ValueError("未知 AI 辅助类型")
    return [{"role": "system", "content": system}, {"role": "user", "content": user}], json_mode


def ai_quote(p, kind, body):
    config = provider_settings()
    if not config.get("deepseek_api_key"):
        raise ValueError("请先在本机设置 DeepSeek API Key")
    try:
        input_rate = float(config["input_cny_per_m"])
        output_rate = float(config["output_cny_per_m"])
    except (KeyError, ValueError, TypeError):
        raise ValueError("请先按当前官方价格设置 DeepSeek 每百万输入/输出 token 的人民币费用")
    messages, json_mode = ai_request(p, kind, body)
    max_tokens = 1600
    # UTF-8 byte count plus message overhead is a conservative upper bound for text tokens.
    input_bound = sum(len(m["content"].encode("utf-8")) for m in messages) + 1000
    estimate = round(max(0.01, (input_bound * input_rate + max_tokens * output_rate) / 1_000_000), 2)
    reason = cost_check(p, "AI辅助", estimate)
    return {"estimate": estimate, "input_token_bound": input_bound, "output_token_cap": max_tokens, "reason": reason, "model": "deepseek-flash", "messages": messages, "json_mode": json_mode}


def run_ai(p, kind, body):
    quote = ai_quote(p, kind, body)
    if quote["reason"]:
        raise ValueError(quote["reason"])
    if float(body.get("approved_quote", -1)) != quote["estimate"]:
        raise ValueError("价格或输入已变化，请重新预估并批准")
    request_id = str(body.get("request_id", ""))
    if len(request_id) < 8:
        raise ValueError("缺少请求幂等标识")
    existing = next((t for t in p["tasks"] if t["idempotency_key"] == request_id), None)
    if existing:
        return {"task": existing, "result": existing.get("result")}
    task = {"id": uid(), "stage": "AI辅助", "provider": "DeepSeek", "description": kind, "estimate": quote["estimate"], "unattended": False, "status": "远端运行中", "reason": "中断后勿自动重发，先人工核对", "remote_id": "", "idempotency_key": request_id, "attempts": 1, "input_versions": {"brief": brief(p)["id"] if brief(p) else None, "master": p["master_approval"]}, "created": now()}
    p["tasks"].append(task)
    if kind == "chat":
        execute(p, "chat", {"role": "user", "text": body["text"]})
    save(p)
    try:
        response = deepseek_complete(provider_settings()["deepseek_api_key"], quote["messages"], json_mode=quote["json_mode"], max_tokens=quote["output_token_cap"])
        task["remote_id"] = response["id"]
        usage = response.get("usage", {})
        if usage.get("prompt_tokens") is not None and usage.get("completion_tokens") is not None:
            config = provider_settings()
            actual = round((usage["prompt_tokens"] * float(config["input_cny_per_m"]) + usage["completion_tokens"] * float(config["output_cny_per_m"])) / 1_000_000, 4)
            p["costs"].append({"id": uid(), "task_id": task["id"], "provider": "DeepSeek", "stage": "AI辅助", "amount": actual, "unit": "CNY", "status": "确认", "created": now(), "usage": usage})
        result = response["text"]
        if quote["json_mode"]:
            parsed = json.loads(result)
            if kind == "facts":
                candidates = parsed.get("facts", [])[:25]
                for f in candidates:
                    execute(p, "fact", {"field": str(f.get("field", "其他")), "value": str(f.get("value", "")), "status": "待核实", "source_ids": [body["source_id"]]})
                result = f"已提取 {len(candidates)} 条待核实候选事实"
            elif kind == "directions":
                directions = parsed.get("directions", [])[:3]
                for d in directions:
                    execute(p, "direction", d)
                result = f"已提出 {len(directions)} 个广告方向，等待用户选择"
            elif kind == "prompt":
                parent = next(x for x in p["prompts"] if x["id"] == body["prompt_id"])
                fields = dict(parent["fields"])
                for field, value in parsed.get("changes", {}).items():
                    if field in fields and field not in ("identity", "prohibited"):
                        fields[field] = str(value)
                execute(p, "prompt", {**fields, "parent_id": parent["id"]})
                result = "已创建提示词新版本，请查看字段差异"
        elif kind == "chat":
            execute(p, "chat", {"role": "assistant", "text": result})
        task["status"] = "待审核"
        task["reason"] = "模型结果只供参考，需人工审核"
        task["result"] = result
    except Exception as exc:
        task["status"] = "失败"
        task["reason"] = str(exc)[:300]
        save(p)
        raise
    save(p)
    return {"task": task, "result": result}


def safe_file(p, sid):
    source = next((s for s in p["sources"] if s["id"] == sid), None)
    if not source:
        raise ValueError("文件不存在")
    path = FILES / p["id"] / source["path"]
    return source, path


def safe_label(label):
    return "".join(c if c not in '/\\:*?"<>|\r\n' else "_" for c in str(label))[:90]


def gallery_png_pair(path, item):
    try:
        from PIL import Image, ImageDraw, ImageFont, ImageOps
    except ImportError as exc:
        raise ValueError("套图导出需要 Pillow。请安装 Pillow 后重试") from exc
    with Image.open(path) as src:
        base = ImageOps.fit(src.convert("RGB"), (1000, 1000), method=Image.Resampling.LANCZOS)
    plain = io.BytesIO()
    base.save(plain, format="PNG")
    annotated = base.copy()
    draw = ImageDraw.Draw(annotated)
    font_path = Path("C:/Windows/Fonts/msyh.ttc")
    if not font_path.exists():
        font_path = Path("C:/Windows/Fonts/arial.ttf")
    x = max(0, min(1000, float(item.get("x", 50)) * 10))
    y = max(0, min(1000, float(item.get("y", 10)) * 10))
    for value, size, offset in ((item.get("title", ""), 62, 0), (item.get("subtitle", ""), 34, 70)):
        if value:
            font = ImageFont.truetype(str(font_path), size) if font_path.exists() else ImageFont.load_default()
            draw.text((x, y + offset), str(value), font=font, anchor="mt", fill="white", stroke_width=2, stroke_fill="#222222")
    final = io.BytesIO()
    annotated.save(final, format="PNG")
    return plain.getvalue(), final.getvalue()


def export_video(p):
    """Render imported Flova clips locally; atempo preserves pitch when changing speed."""
    if not p["clips"]:
        raise ValueError("时间线没有镜头")
    if not shutil.which("ffmpeg") or not shutil.which("ffprobe"):
        raise ValueError("导出视频需要 FFmpeg 和 FFprobe")
    with tempfile.TemporaryDirectory(prefix="workbench-video-") as tmp:
        folder = Path(tmp)
        segments = []
        for i, clip in enumerate(p["clips"]):
            a = next((a for a in p["assets"] if a["id"] == clip["asset_id"]), None)
            if not a:
                raise ValueError(f"镜头 {i + 1} 缺少素材")
            _, path = safe_file(p, a["source_id"])
            duration = float(clip["end"]) - float(clip["start"])
            speed = float(clip["speed"])
            if duration <= 0 or not 0.5 <= speed <= 2:
                raise ValueError(f"镜头 {i + 1} 裁剪或变速无效")
            probe = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "stream=codec_type", "-of", "csv=p=0", str(path)], capture_output=True, text=True, timeout=20)
            if probe.returncode:
                raise ValueError(f"镜头 {i + 1} 不是可读取的视频")
            has_audio = "audio" in probe.stdout
            output = folder / f"shot-{i:03d}.mp4"
            vf = f"scale=720:1280:force_original_aspect_ratio=decrease,pad=720:1280:(ow-iw)/2:(oh-ih)/2,setpts=(PTS-STARTPTS)/{speed}"
            caption = str(clip.get("caption", "")).strip()
            if caption:
                caption_file = folder / f"caption-{i:03d}.txt"
                caption_file.write_text(caption, encoding="utf-8")
                font = "C\\:/Windows/Fonts/msyh.ttc"
                vf += f",drawtext=fontfile='{font}':textfile='{caption_file.name}':fontcolor=white:fontsize=38:box=1:boxcolor=black@0.55:boxborderw=16:x=(w-text_w)/2:y=h-170"
            cmd = ["ffmpeg", "-y", "-hide_banner", "-loglevel", "error", "-ss", str(clip["start"]), "-t", str(duration), "-i", str(path)]
            if not has_audio:
                cmd += ["-f", "lavfi", "-t", str(duration / speed), "-i", "anullsrc=r=48000:cl=stereo"]
            cmd += ["-vf", vf, "-map", "0:v:0", "-map", "0:a:0" if has_audio else "1:a:0"]
            if has_audio:
                cmd += ["-af", f"atempo={speed}"]
            cmd += ["-r", "30", "-c:v", "libx264", "-pix_fmt", "yuv420p", "-preset", "veryfast", "-crf", "22", "-c:a", "aac", "-ar", "48000", "-ac", "2", "-shortest", str(output)]
            result = subprocess.run(cmd, cwd=folder, capture_output=True, text=True, timeout=180)
            if result.returncode:
                raise ValueError(f"镜头 {i + 1} 导出失败：{result.stderr[-500:]}")
            segments.append(output)
        manifest = folder / "concat.txt"
        manifest.write_text("".join(f"file '{segment.name}'\n" for segment in segments), encoding="utf-8")
        final = folder / "final.mp4"
        result = subprocess.run(["ffmpeg", "-y", "-hide_banner", "-loglevel", "error", "-f", "concat", "-safe", "0", "-i", str(manifest), "-c", "copy", "-movflags", "+faststart", str(final)], cwd=folder, capture_output=True, text=True, timeout=180)
        if result.returncode:
            raise ValueError("成片合并失败：" + result.stderr[-500:])
        return final.read_bytes()


def export_zip(p, backup=False):
    out = io.BytesIO()
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("project.json", json.dumps(p, ensure_ascii=False, indent=2))
        for source in p["sources"]:
            _, path = safe_file(p, source["id"])
            if path.exists():
                z.write(path, f"files/{source['path']}")
        if not backup:
            latest_script = next((s for s in p["script_versions"] if s["id"] == p["script_approval"]), None)
            if latest_script:
                z.writestr("交付/已确认脚本.txt", latest_script["text"])
            z.writestr("交付/采用提示词.json", json.dumps([x for x in p["prompts"] if any(a.get("prompt_id") == x["id"] and a["id"] in p["adopted_assets"].values() for a in p["assets"])], ensure_ascii=False, indent=2))
            z.writestr("交付/素材来源清单.json", json.dumps(p["sources"], ensure_ascii=False, indent=2))
            gallery = next((g for g in reversed(p["gallery"]) if g["approved"]), None)
            if gallery:
                for i, item in enumerate(gallery["items"], 1):
                    aid = item.get("asset_id")
                    asset = next((a for a in p["assets"] if a["id"] == aid), None)
                    if not asset:
                        continue
                    source, path = safe_file(p, asset["source_id"])
                    if not path.exists() or not source["mime"].startswith("image/"):
                        continue
                    plain_png, png = gallery_png_pair(path, item)
                    label = safe_label(item["kind"])
                    z.writestr(f"交付/套图/{i:02d}-{label}.png", png)
                    uri = "data:image/png;base64," + base64.b64encode(plain_png).decode()
                    title = escape(item.get("title", ""))
                    subtitle = escape(item.get("subtitle", ""))
                    x, y = item.get("x", 50), item.get("y", 10)
                    svg = f'<svg xmlns="http://www.w3.org/2000/svg" width="1000" height="1000" viewBox="0 0 1000 1000"><image href="{uri}" width="1000" height="1000"/><text x="{x}%" y="{y}%" dominant-baseline="hanging" text-anchor="middle" font-family="sans-serif" font-size="62" fill="white" stroke="#222" stroke-width="2" paint-order="stroke">{title}</text><text x="{x}%" y="{float(y)+7}%" dominant-baseline="hanging" text-anchor="middle" font-family="sans-serif" font-size="34" fill="white" stroke="#222" stroke-width="2" paint-order="stroke">{subtitle}</text></svg>'
                    z.writestr(f"交付/套图/{i:02d}-{label}.svg", svg)
            for source in p["sources"]:
                if source["mime"].startswith("video/"):
                    _, path = safe_file(p, source["id"])
                    if path.exists():
                        z.write(path, f"交付/视频素材/{safe_label(source['name'])}")
            if p["clips"]:
                z.writestr("交付/视频/本地收尾.mp4", export_video(p))
    return out.getvalue()


def restore_zip(z):
    if z.getinfo("project.json").file_size > 10 * 1024 * 1024:
        raise ValueError("项目状态文件过大")
    p = json.loads(z.read("project.json"))
    if not isinstance(p.get("sources"), list) or len(p["sources"]) > 10000:
        raise ValueError("备份项目格式无效")
    p["id"] = uid()
    p["name"] = str(p["name"])[:100] + "（恢复）"
    p["tasks"] = [{**t, "status": "待批准", "reason": "恢复后须核对远端状态"} for t in p["tasks"]]
    folder = FILES / p["id"]
    folder.mkdir(parents=True, exist_ok=True)
    try:
        total_size = 0
        for source in p["sources"]:
            filename = source["path"]
            if Path(filename).name != filename or filename in (".", ".."):
                raise ValueError("备份文件路径无效")
            info = z.getinfo("files/" + filename)
            total_size += info.file_size
            if total_size > 2 * 1024 * 1024 * 1024:
                raise ValueError("备份文件总大小超过 2 GB")
            with z.open(info) as src, (folder / filename).open("wb") as dest:
                sha = hashlib.sha256()
                while chunk := src.read(1024 * 1024):
                    sha.update(chunk)
                    dest.write(chunk)
            if source.get("sha256") and sha.hexdigest() != source["sha256"]:
                raise ValueError("备份文件校验失败：" + filename)
    except Exception:
        shutil.rmtree(folder, ignore_errors=True)
        raise
    save(p)
    return p


def image_png(path):
    if path.suffix.lower() == ".png":
        return path.read_bytes()
    result = subprocess.run(["ffmpeg", "-hide_banner", "-loglevel", "error", "-i", str(path), "-frames:v", "1", "-f", "image2pipe", "-vcodec", "png", "-"], capture_output=True, timeout=30)
    if result.returncode:
        raise ValueError("图片转 PNG 失败，请确认 FFmpeg 已安装")
    return result.stdout


def flova_action(p, kind, body):
    external = p.setdefault("external", {})
    project_id = external.get("flova_project_id", "")
    if kind == "create":
        if project_id:
            raise ValueError("已关联 Flova 项目；如需更换，请先手动核对现有项目")
        data = flova.create_project(p["name"], "广告电商创作工作台项目")
        project_id = str(data.get("project_id") or data.get("id") or "")
        if not project_id:
            raise ValueError("Flova 已响应，但未返回项目 ID；请在 Flova 项目列表核对后手动关联")
        external["flova_project_id"] = project_id
        external["flova_url"] = str(data.get("project_url") or "")
        external["flova_status"] = "已创建"
    elif kind == "info":
        if not project_id:
            raise ValueError("请先创建或关联 Flova 项目")
        data = flova.project_info(project_id)
        external["flova_status"] = "已连接"
        external["flova_name"] = str(data.get("name") or data.get("project_name") or "")[:160]
        if data.get("project_url"):
            external["flova_url"] = str(data["project_url"])
    elif kind == "run":
        if not project_id:
            raise ValueError("请先创建或关联 Flova 项目")
        prompt = str(body.get("prompt", "")).strip()
        if not prompt or len(prompt) > 12000:
            raise ValueError("请输入 1 到 12000 字的 Flova 创作要求")
        if external.get("flova_run_state") in ("运行中", "状态待恢复", "待用户确认"):
            raise ValueError("上次 Flova 运行尚未确认结束或仍有待确认操作，请先恢复状态")
        external["flova_run_state"] = "运行中"
        external["flova_last_prompt"] = prompt
        save(p)
        try:
            data = flova.run(project_id, prompt)
        except Exception:
            external["flova_run_state"] = "状态待恢复"
            save(p)
            raise
        has_blocking_action = any(a.get("blocking", True) for a in data.get("pending_actions") or [])
        external["flova_run_state"] = "待用户确认" if has_blocking_action else ("已返回" if data.get("terminal", True) else "状态待恢复")
        external["flova_last_result"] = flova_result_summary(data)
    elif kind == "recover":
        if not project_id:
            raise ValueError("请先创建或关联 Flova 项目")
        data = flova.recover(project_id)
        if data.get("terminal") and data.get("stream_chat_id"):
            data = flova.run_result(project_id, str(data["stream_chat_id"]))
        external["flova_last_result"] = flova_result_summary(data)
        has_blocking_action = any(a.get("blocking", True) for a in data.get("pending_actions") or [])
        external["flova_run_state"] = "待用户确认" if has_blocking_action else ("已返回" if data.get("terminal") else "状态待恢复")
    elif kind == "readiness":
        if not project_id:
            raise ValueError("请先创建或关联 Flova 项目")
        data = flova.readiness(project_id)
        external["flova_export_readiness"] = {k: data.get(k) for k in ("can_export", "reason", "message")}
    elif kind == "export":
        if not project_id or not external.get("flova_export_readiness", {}).get("can_export"):
            raise ValueError("请先检查 Flova 导出条件")
        if external.get("flova_run_state") in ("运行中", "状态待恢复", "待用户确认"):
            raise ValueError("创作运行尚未确认结束，不能导出")
        pending = flova.pending_actions(project_id)
        actions = pending.get("pending_actions") or pending.get("actions") or []
        if any(a.get("blocking", True) for a in actions):
            external["flova_pending_actions"] = actions
            save(p)
            raise ValueError("Flova 仍有待确认操作，请先在 Flova 处理并重新检查导出条件")
        data = flova.export_video(project_id)
        external["flova_export"] = {k: data.get(k) for k in ("task_id", "terminal", "status", "export_url")}
    else:
        raise ValueError("未知 Flova 操作")
    fresh = load(p["id"])
    fresh.setdefault("external", {}).update(external)
    save(fresh)
    return {"project": fresh, "result": fresh["external"]}


def flova_result_summary(data):
    return {k: data.get(k) for k in ("terminal", "stream_chat_id", "retry_status", "retry_result", "pending_actions", "assistant_messages", "project_url") if k in data}


class Handler(BaseHTTPRequestHandler):
    def log_message(self, format, *args):
        if not self.path.startswith("/api/"):
            return
        print("API", format % args)

    def send(self, status, data, mime="application/json; charset=utf-8", filename=None):
        raw = json.dumps(data, ensure_ascii=False).encode() if isinstance(data, (dict, list)) else data
        self.send_response(status)
        self.send_header("Content-Type", mime)
        self.send_header("Content-Length", str(len(raw)))
        self.send_header("Cache-Control", "no-store")
        if filename:
            self.send_header("Content-Disposition", f"attachment; filename*=UTF-8''{quote(filename)}")
        self.end_headers()
        self.wfile.write(raw)

    def do_GET(self):
        try:
            u = urlparse(self.path)
            parts = u.path.strip("/").split("/")
            if u.path == "/api/projects":
                self.send(200, listing(parse_qs(u.query).get("q", [""])[0]))
            elif u.path == "/api/capabilities":
                self.send(200, {"deepseek": provider_public()["deepseek_configured"], "seeany": "已配置，可提交" if provider_public()["seeany_configured"] else "等待 .env 密钥", "flova": flova.executable() is not None, "jev": "旁路待接入", "ffmpeg": shutil.which("ffmpeg") is not None})
            elif u.path == "/api/provider-settings":
                self.send(200, provider_public())
            elif len(parts) == 3 and parts[:2] == ["api", "projects"]:
                self.send(200, load(parts[2]))
            elif len(parts) == 5 and parts[:2] == ["api", "projects"] and parts[3] == "file":
                p = load(parts[2]); source, path = safe_file(p, parts[4])
                self.send(200, path.read_bytes(), source["mime"])
            elif len(parts) == 4 and parts[:2] == ["api", "projects"] and parts[3] in ("backup", "export"):
                p = load(parts[2]); backup = parts[3] == "backup"
                self.send(200, export_zip(p, backup), "application/zip", f"{p['name']}-{'备份' if backup else '交付'}.zip")
            elif len(parts) == 4 and parts[:2] == ["api", "projects"] and parts[3] == "video-export":
                p = load(parts[2])
                self.send(200, export_video(p), "video/mp4", safe_label(p["name"]) + "-本地收尾.mp4")
            else:
                path = WEB / (u.path.lstrip("/") or "index.html")
                if not path.resolve().is_relative_to(WEB.resolve()) or not path.is_file():
                    path = WEB / "index.html"
                self.send(200, path.read_bytes(), mimetypes.guess_type(path)[0] or "text/html; charset=utf-8")
        except Exception as e:
            self.send(400, {"error": str(e)})

    def do_POST(self):
        try:
            if urlparse(self.path).path == "/api/restore" and self.headers.get("Content-Type", "").startswith("application/zip"):
                length = int(self.headers.get("Content-Length", "0"))
                if not 0 < length <= 2 * 1024 * 1024 * 1024:
                    raise ValueError("备份 ZIP 大小无效或超过 2 GB")
                DATA.mkdir(exist_ok=True)
                tmp_path = None
                try:
                    with tempfile.NamedTemporaryFile(prefix="restore-", suffix=".zip", dir=DATA, delete=False) as temp:
                        tmp_path = Path(temp.name)
                        remaining = length
                        while remaining:
                            chunk = self.rfile.read(min(1024 * 1024, remaining))
                            if not chunk:
                                raise ValueError("备份文件传输不完整")
                            temp.write(chunk)
                            remaining -= len(chunk)
                    with zipfile.ZipFile(tmp_path) as z:
                        p = restore_zip(z)
                finally:
                    if tmp_path:
                        tmp_path.unlink(missing_ok=True)
                self.send(200, p)
                return
            length = int(self.headers.get("Content-Length", "0"))
            if length > MAX_BODY:
                raise ValueError("请求超过 35 MB")
            body = json.loads(self.rfile.read(length) or b"{}")
            parts = urlparse(self.path).path.strip("/").split("/")
            if parts == ["api", "projects"]:
                name = str(body.get("name", "")).strip()
                if not name:
                    raise ValueError("项目名称不能为空")
                p = new_project(name); save(p); self.send(200, p)
            elif parts == ["api", "provider-settings"]:
                self.send(200, update_provider_settings(body))
            elif parts == ["api", "restore"]:
                raw = base64.b64decode(body["data"], validate=True)
                with zipfile.ZipFile(io.BytesIO(raw)) as z:
                    p = restore_zip(z)
                self.send(200, p)
            elif len(parts) == 5 and parts[:2] == ["api", "projects"] and parts[3] == "action":
                p = load(parts[2]); result = execute(p, parts[4], body); save(p); self.send(200, {"result": result, "project": p})
            elif len(parts) == 5 and parts[:2] == ["api", "projects"] and parts[3] == "assist":
                p = load(parts[2]); kind = body["kind"]
                if parts[4] == "quote":
                    quote = ai_quote(p, kind, body)
                    self.send(200, {k: quote[k] for k in ("estimate", "input_token_bound", "output_token_cap", "reason", "model")})
                elif parts[4] == "run":
                    result = run_ai(p, kind, body)
                    self.send(200, {"result": result, "project": p})
                else:
                    self.send(404, {"error": "接口不存在"})
            elif len(parts) == 5 and parts[:2] == ["api", "projects"] and parts[3] == "seeany":
                if parts[4] == "quote":
                    p = load(parts[2])
                    q = seeany_quote(p, body)
                    self.send(200, {k: q[k] for k in ("kind", "label", "stage", "estimate", "reason", "fingerprint", "source_names", "summary", "request_preview")})
                elif parts[4] == "run":
                    with SEEANY_RUN_LOCK:
                        p = load(parts[2]); result = run_seeany(p, body)
                    self.send(200, {"result": result, "project": p})
                elif parts[4] == "status":
                    with SEEANY_RUN_LOCK:
                        p = load(parts[2]); result = sync_seeany(p, body["task_id"])
                    self.send(200, {"result": result, "project": p})
                else:
                    self.send(404, {"error": "接口不存在"})
            elif len(parts) == 5 and parts[:2] == ["api", "projects"] and parts[3] == "flova":
                with FLOVA_RUN_LOCK:
                    p = load(parts[2]); self.send(200, flova_action(p, parts[4], body))
            elif len(parts) == 4 and parts[:2] == ["api", "projects"] and parts[3] == "delete":
                pid = parts[2]; p = load(pid)
                if body.get("confirm") != p["name"]:
                    raise ValueError("请输入项目名称确认彻底删除")
                with LOCK, database() as db:
                    db.execute("DELETE FROM projects WHERE id=?", (pid,))
                shutil.rmtree(FILES / pid, ignore_errors=True)
                self.send(200, {"ok": True})
            else:
                self.send(404, {"error": "接口不存在"})
        except Exception as e:
            self.send(400, {"error": str(e)})


def main():
    connect().close()
    host, port = "127.0.0.1", int(os.getenv("WORKBENCH_PORT", "8765"))
    server = ThreadingHTTPServer((host, port), Handler)
    url = f"http://{host}:{port}"
    print(f"广告电商创作工作台：{url}")
    if "--no-browser" not in sys.argv:
        threading.Timer(0.7, lambda: webbrowser.open(url)).start()
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
