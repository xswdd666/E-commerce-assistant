"""Provider task protocol used by the canvas companion service."""

from __future__ import annotations

import base64
import binascii
import hashlib
import json
import os
from pathlib import Path

from app import flova, seeany
from app.providers import deepseek_complete
from .core import fingerprint, ident, stamp


ROOT = Path(__file__).resolve().parent.parent


def provider_key(name):
    if name in os.environ:
        return os.environ[name]
    path = ROOT / ".env"
    if path.exists():
        for line in path.read_text(encoding="utf-8-sig").splitlines():
            key, separator, value = line.partition("=")
            if separator and key.strip() == name:
                return value.strip().strip('"').strip("'")
    return ""


def quote(store, project, node_id):
    snap = store.snapshot(project, node_id)
    inputs = snap["inputs"]
    if "text" not in inputs or not str(inputs["text"]["data"].get("text", "")).strip():
        raise ValueError("任务需要连接非空提示词节点")
    if snap["kind"] in ("image_task", "video_task"):
        if not snap["brief_id"] or not snap["master_id"]:
            raise ValueError("正式生成需先确认产品简报和三视图母版")
        if "reference" not in inputs:
            raise ValueError("正式生成需连接参考图")
        _reference(store, project, snap)
    if snap["kind"] == "chat_task" and "reference" in inputs:
        raise ValueError("此接入暂不支持图片观察，请移除图片输入")
    provider = {"chat_task": "DeepSeek", "image_task": "SeeAny", "video_task": "Flova"}[snap["kind"]]
    return {"provider": provider, "input_snapshot": snap, "fingerprint": fingerprint(snap),
            "estimate": None, "currency": None, "pricing_source": "未核实，费用未知", "reliable": False,
            "requires_explicit_run": True, "ready": snap["kind"] != "video_task",
            "reason": "Flova 单镜素材上传尚未验证，请在 Flova 网页完成并导回素材" if snap["kind"] == "video_task" else None}


def _reference(store, project, snap):
    ref = snap["inputs"]["reference"]["data"]
    source_id = ref.get("source_id") or ref.get("asset_id")
    return store.source_bytes(project, source_id)


def master_quote(store, project, body):
    if not project["brief_versions"]:
        raise ValueError("请先确认产品简报")
    group = body.get("group")
    view = body.get("view_label")
    views = str(body.get("views") or "").strip()
    source_id = body.get("source_id")
    if group not in (1, 2) or view not in ("正面", "侧面", "背面") or not views:
        raise ValueError("请选择候选组、视角并填写 SeeAny 视角预设")
    source, _ = store.source_bytes(project, source_id)
    if not source["mime"].startswith("image/") or source.get("origin") == "SeeAny candidate":
        raise ValueError("三视图参考资料必须是图片")
    if any(t.get("kind") == "master" and t.get("candidate_group") == group and t.get("view_label") == view
           and t["status"] in ("远端运行中", "待核对", "待审核", "完成") for t in project["tasks"]):
        raise ValueError("该组该视角已有任务，请先同步或核对")
    snapshot = {"brief_id": project["brief_versions"][-1]["id"], "source_id": source_id,
                "source_sha256": source["sha256"], "group": group, "view_label": view, "views": views}
    return {"provider": "SeeAny", "input_snapshot": snapshot, "fingerprint": fingerprint(snapshot),
            "estimate": None, "currency": None, "pricing_source": "未核实，费用未知", "reliable": False,
            "requires_explicit_run": True}


def chat_quote(project, body):
    prompt = str(body.get("prompt") or "").strip()
    if not prompt or len(prompt) > 12000:
        raise ValueError("请输入有效的聊天需求")
    brief = project["brief_versions"][-1] if project["brief_versions"] else None
    snapshot = {"prompt": prompt, "brief_id": brief["id"] if brief else None,
                "facts": [{"field": f["field"], "value": f["value"]} for f in brief["facts"]] if brief else []}
    return {"provider": "DeepSeek", "input_snapshot": snapshot, "fingerprint": fingerprint(snapshot),
            "estimate": None, "currency": None, "pricing_source": "未核实，费用未知", "reliable": False,
            "requires_explicit_run": True}


def facts_quote(project, body):
    source = next((s for s in project["sources"] if s["id"] == body.get("source_id")), None)
    if not source or not source.get("extracted_text", "").strip():
        raise ValueError("此来源没有可提取文本，请人工查看原文件")
    snapshot = {"source_id": source["id"], "source_sha256": source["sha256"], "text": source["extracted_text"]}
    return {"provider": "DeepSeek", "input_snapshot": snapshot, "fingerprint": fingerprint(snapshot),
            "estimate": None, "currency": None, "pricing_source": "未核实，费用未知", "reliable": False,
            "requires_explicit_run": True}


def directions_quote(project):
    if not project["brief_versions"] or not project["master_versions"]:
        raise ValueError("请先确认产品简报与三视图母版")
    brief = project["brief_versions"][-1]
    master = project["master_versions"][-1]
    snapshot = {"brief_id": brief["id"], "master_id": master["id"],
                "facts": [{"field": f["field"], "value": f["value"]} for f in brief["facts"]]}
    return {"provider": "DeepSeek", "input_snapshot": snapshot, "fingerprint": fingerprint(snapshot),
            "estimate": None, "currency": None, "pricing_source": "未核实，费用未知", "reliable": False,
            "requires_explicit_run": True}


def run_directions(store, project, body):
    existing = next((t for t in project["tasks"] if t["idempotency_key"] == body.get("request_id")), None)
    if existing:
        return existing
    offer = directions_quote(project)
    if body.get("approved_fingerprint") != offer["fingerprint"]:
        raise ValueError("简报或母版已变化，请重新查看输入")
    request_id = body.get("request_id")
    if not isinstance(request_id, str) or len(request_id) < 8 or len(request_id) > 120:
        raise ValueError("请提供请求标识以避免重复提交")
    key = provider_key("DEEPSEEK_API_KEY")
    if not key:
        raise ValueError("缺少 DeepSeek API Key")
    snapshot = offer["input_snapshot"]
    task = {"id": ident(), "kind": "directions", "provider": "DeepSeek", "status": "远端运行中",
            "idempotency_key": request_id, "input_snapshot": snapshot, "estimate": None, "actual": None,
            "currency": None, "remote_id": None, "attempts": 1, "created": stamp(), "updated": stamp()}
    project["tasks"].append(task)
    store.save(project)
    try:
        messages = [{"role": "system", "content": "你是小家电广告策划。只返回 JSON 对象：{\"directions\":[{\"title\":\"\",\"audience\":\"\",\"opening\":\"\",\"selling_point\":\"\",\"ending\":\"\"}]}。恰好三个明显不同的方向，卖点仅依据已确认事实，不杜撰规格。"},
                    {"role": "user", "content": json.dumps(snapshot["facts"], ensure_ascii=False)}]
        result = deepseek_complete(key, messages, json_mode=True)
        items = json.loads(result["text"]).get("directions")
        if not isinstance(items, list) or len(items) != 3 or any(not isinstance(item, dict) or any(not str(item.get(field) or "").strip() for field in ("title", "audience", "opening", "selling_point", "ending")) for item in items):
            raise ValueError("DeepSeek 未返回三个完整广告方向")
        directions = [{"id": ident(), "brief_id": snapshot["brief_id"], "master_id": snapshot["master_id"],
                       **{field: str(item[field]).strip() for field in ("title", "audience", "opening", "selling_point", "ending")},
                       "created": stamp()} for item in items]
        project["directions"].extend(directions)
        task.update(status="待审核", remote_id=result["id"], result={"direction_ids": [d["id"] for d in directions], "usage": result["usage"]})
    except Exception as exc:
        task.update(status="待核对", error=str(exc)[:300])
        store.save(project)
        raise ValueError(task["error"] + "；如远端可能已受理，请先核对，勿重复提交") from exc
    task["updated"] = stamp()
    store.save(project)
    return task


def run_facts(store, project, body):
    existing = next((t for t in project["tasks"] if t["idempotency_key"] == body.get("request_id")), None)
    if existing:
        return existing
    offer = facts_quote(project, body)
    if body.get("approved_fingerprint") != offer["fingerprint"]:
        raise ValueError("来源文件已变化，请重新查看输入")
    request_id = body.get("request_id")
    if not isinstance(request_id, str) or len(request_id) < 8 or len(request_id) > 120:
        raise ValueError("请提供请求标识以避免重复提交")
    key = provider_key("DEEPSEEK_API_KEY")
    if not key:
        raise ValueError("缺少 DeepSeek API Key")
    snapshot = offer["input_snapshot"]
    task = {"id": ident(), "kind": "facts", "provider": "DeepSeek", "status": "远端运行中",
            "idempotency_key": request_id, "input_snapshot": {k: v for k, v in snapshot.items() if k != "text"},
            "estimate": None, "actual": None, "currency": None, "remote_id": None,
            "attempts": 1, "created": stamp(), "updated": stamp()}
    project["tasks"].append(task)
    store.save(project)
    try:
        messages = [{"role": "system", "content": "从商品资料提取可由原文支持的候选信息。只返回 JSON 对象：{\"facts\":[{\"field\":\"字段\",\"value\":\"内容\"}]}。不得把推断写成事实。"},
                    {"role": "user", "content": snapshot["text"]}]
        result = deepseek_complete(key, messages, json_mode=True)
        parsed = json.loads(result["text"])
        items = parsed.get("facts")
        if not isinstance(items, list):
            raise ValueError("DeepSeek 未返回可用候选事实")
        for item in items[:30]:
            if isinstance(item, dict) and str(item.get("field") or "").strip() and str(item.get("value") or "").strip():
                store.add_fact(project, str(item["field"])[:80], str(item["value"])[:500], snapshot["source_id"], "待核实")
        task.update(status="待审核", remote_id=result["id"], result={"candidate_count": len(items), "usage": result["usage"]})
    except Exception as exc:
        task.update(status="待核对", error=str(exc)[:300])
        store.save(project)
        raise ValueError(task["error"] + "；如远端可能已受理，请先核对，勿重复提交") from exc
    task["updated"] = stamp()
    store.save(project)
    return task


def run_chat(store, project, body):
    existing = next((t for t in project["tasks"] if t["idempotency_key"] == body.get("request_id")), None)
    if existing:
        return existing
    offer = chat_quote(project, body)
    if body.get("approved_fingerprint") != offer["fingerprint"]:
        raise ValueError("聊天输入或简报已变化，请重新查看输入")
    request_id = body.get("request_id")
    if not isinstance(request_id, str) or len(request_id) < 8 or len(request_id) > 120:
        raise ValueError("请提供请求标识以避免重复提交")
    key = provider_key("DEEPSEEK_API_KEY")
    if not key:
        raise ValueError("缺少 DeepSeek API Key")
    snapshot = offer["input_snapshot"]
    task = {"id": ident(), "kind": "chat", "provider": "DeepSeek", "status": "远端运行中",
            "idempotency_key": request_id, "input_snapshot": snapshot, "estimate": None, "actual": None,
            "currency": None, "remote_id": None, "attempts": 1, "created": stamp(), "updated": stamp()}
    project["tasks"].append(task)
    store.save(project)
    try:
        messages = [{"role": "system", "content": "你是广告电商创作助手。正式产品事实仅以已确认简报为准；未知信息明确标为待核实。已确认事实：" + str(snapshot["facts"])},
                    {"role": "user", "content": snapshot["prompt"]}]
        result = deepseek_complete(key, messages)
        task.update(status="待审核", remote_id=result["id"], result={"text": result["text"], "usage": result["usage"]})
        project["chat"].append({"id": ident(), "task_id": task["id"], "prompt": snapshot["prompt"], "reply": result["text"], "brief_id": snapshot["brief_id"], "created": stamp()})
    except Exception as exc:
        task.update(status="待核对", error=str(exc)[:300])
        store.save(project)
        raise ValueError(task["error"] + "；如远端可能已受理，请先核对，勿重复提交") from exc
    task["updated"] = stamp()
    store.save(project)
    return task


def _preview_reference(body):
    data_url = body.get("reference_data_url")
    if not isinstance(data_url, str) or not data_url.startswith("data:image/"):
        raise ValueError("画布参考图必须是本机可读取的图片")
    header, separator, encoded = data_url.partition(",")
    mime = header[5:].split(";", 1)[0]
    if not separator or not header.endswith(";base64") or mime not in ("image/png", "image/jpeg", "image/webp"):
        raise ValueError("仅支持 PNG、JPEG 和 WebP 画布参考图")
    try:
        raw = base64.b64decode(encoded, validate=True)
    except binascii.Error as exc:
        raise ValueError("画布参考图编码无效") from exc
    if not raw or len(raw) > 25 * 1024 * 1024:
        raise ValueError("参考图为空或超过 25 MB")
    return mime, raw


def preview_quote(project, body):
    if not project["brief_versions"] or not project["master_versions"]:
        raise ValueError("请先确认简报和三视图母版")
    prompt = str(body.get("prompt") or "").strip()
    if not prompt or len(prompt) > 12000:
        raise ValueError("画布提示词无效")
    if not all(isinstance(body.get(key), str) and body[key] for key in ("canvas_project_id", "config_node_id", "reference_node_id")):
        raise ValueError("画布节点引用无效")
    if not isinstance(body.get("connection_ids"), list) or not body["connection_ids"]:
        raise ValueError("参考图必须来自画布连线")
    ratio = body.get("ratio") or "1:1"
    if ratio not in ("1:1", "3:4", "4:3", "9:16", "16:9", "3:2", "2:3"):
        raise ValueError("图片比例不受支持")
    mime, raw = _preview_reference(body)
    reference_sha256 = hashlib.sha256(raw).hexdigest()
    master_ids = set(project["master_versions"][-1]["asset_ids"])
    if not any(source["id"] in master_ids and source["sha256"] == reference_sha256 for source in project["sources"]):
        raise ValueError("试图参考图须为当前已确认母版中的原始文件")
    snapshot = {"brief_id": project["brief_versions"][-1]["id"], "master_id": project["master_versions"][-1]["id"],
                "canvas_project_id": body["canvas_project_id"], "config_node_id": body["config_node_id"],
                "reference_node_id": body["reference_node_id"], "connection_ids": body["connection_ids"],
                "prompt": prompt, "ratio": ratio, "reference_mime": mime, "reference_sha256": reference_sha256}
    return {"provider": "SeeAny", "input_snapshot": snapshot, "fingerprint": fingerprint(snapshot),
            "estimate": None, "currency": None, "pricing_source": "未核实，费用未知", "reliable": False,
            "requires_explicit_run": True}


def run_preview(store, project, body):
    existing = next((t for t in project["tasks"] if t["idempotency_key"] == body.get("request_id")), None)
    if existing:
        return existing
    offer = preview_quote(project, body)
    if body.get("approved_fingerprint") != offer["fingerprint"]:
        raise ValueError("画布输入已变化，请重新查看快照")
    request_id = body.get("request_id")
    if not isinstance(request_id, str) or len(request_id) < 8 or len(request_id) > 120:
        raise ValueError("请提供请求标识以避免重复提交")
    key = provider_key("SEEANY_API_KEY")
    if not key:
        raise ValueError("缺少 SeeAny API Key")
    snapshot = offer["input_snapshot"]
    mime, raw = _preview_reference(body)
    task = {"id": ident(), "kind": "preview", "provider": "SeeAny", "status": "远端运行中",
            "idempotency_key": request_id, "input_snapshot": snapshot, "estimate": None, "actual": None,
            "currency": None, "remote_id": None, "attempts": 1, "created": stamp(), "updated": stamp()}
    project["tasks"].append(task)
    store.save(project)
    try:
        extension = {"image/png": ".png", "image/jpeg": ".jpg", "image/webp": ".webp"}[mime]
        source = store.add_source(project, f"canvas-{snapshot['reference_node_id']}{extension}", mime, base64.b64encode(raw).decode())
        source["origin"] = "Canvas reference"
        source["canvas_node_id"] = snapshot["reference_node_id"]
        task["source_id"] = source["id"]
        store.save(project)
        url = seeany.upload_image(key, source["name"], raw)
        payload = {"aiTypeId": 113, "aiType": "smartImg", "prompt": snapshot["prompt"],
                   "inputImgs": [url], "imgNum": 1, "imgRatio": snapshot["ratio"], "mode": "nano2", "size": "1K"}
        result = seeany.submit(key, "/api/ai/smarttask", payload)
        remote_id = (result.get("data") or {}).get("task_uuid")
        if not remote_id:
            raise ValueError("SeeAny 未返回任务标识，请先核对远端")
        task["remote_id"] = remote_id
    except Exception as exc:
        task.update(status="待核对", error=str(exc)[:300])
        store.save(project)
        raise ValueError(task["error"] + "；请先核对远端，勿重复提交") from exc
    task["updated"] = stamp()
    store.save(project)
    return task


def run_master(store, project, body):
    existing = next((t for t in project["tasks"] if t["idempotency_key"] == body.get("request_id")), None)
    if existing:
        return existing
    offer = master_quote(store, project, body)
    if body.get("approved_fingerprint") != offer["fingerprint"]:
        raise ValueError("输入已变化，请重新预估并确认")
    request_id = body.get("request_id")
    if not isinstance(request_id, str) or len(request_id) < 8 or len(request_id) > 120:
        raise ValueError("请提供请求标识以避免重复提交")
    key = provider_key("SEEANY_API_KEY")
    if not key:
        raise ValueError("缺少 SeeAny API Key")
    snapshot = offer["input_snapshot"]
    task = {"id": ident(), "kind": "master", "provider": "SeeAny", "status": "远端运行中",
            "candidate_group": snapshot["group"], "view_label": snapshot["view_label"],
            "idempotency_key": request_id, "input_snapshot": snapshot, "estimate": None, "actual": None,
            "currency": None, "remote_id": None, "attempts": 1, "created": stamp(), "updated": stamp()}
    project["tasks"].append(task)
    store.save(project)
    try:
        source, raw = store.source_bytes(project, snapshot["source_id"])
        url = seeany.upload_image(key, source["name"], raw)
        payload = {"aiTypeId": 464, "aiType": "multiview", "inputImgs": [url],
                   "views": snapshot["views"], "imgNum": 1, "imgRatio": "1:1", "mode": "nano-banana-pro", "size": "1K"}
        result = seeany.submit(key, "/api/ai/batchsmarttask", payload)
        remote_id = (result.get("data") or {}).get("task_uuid")
        if not remote_id:
            raise ValueError("SeeAny 未返回任务标识，请先核对远端记录")
        task["remote_id"] = remote_id
    except Exception as exc:
        task.update(status="待核对", error=str(exc)[:300])
        store.save(project)
        raise ValueError(task["error"] + "；请先核对远端，勿重复提交") from exc
    task["updated"] = stamp()
    store.save(project)
    return task


def run(store, project, node_id, body):
    offer = quote(store, project, node_id)
    if not offer["ready"]:
        raise ValueError(offer["reason"])
    if body.get("approved_fingerprint") != offer["fingerprint"]:
        raise ValueError("输入已变化，请重新查看快照并确认运行")
    request_id = body.get("request_id")
    if not isinstance(request_id, str) or len(request_id) < 8 or len(request_id) > 120:
        raise ValueError("请提供请求标识以避免重复提交")
    existing = next((t for t in project["tasks"] if t["idempotency_key"] == request_id), None)
    if existing:
        return existing
    snap = offer["input_snapshot"]
    provider = offer["provider"]
    key_name = {"DeepSeek": "DEEPSEEK_API_KEY", "SeeAny": "SEEANY_API_KEY"}[provider]
    key = provider_key(key_name)
    if not key:
        raise ValueError(f"缺少 {provider} API Key")
    task = {"id": ident(), "node_id": node_id, "provider": offer["provider"], "status": "远端运行中",
            "idempotency_key": request_id, "input_snapshot": snap, "estimate": None, "actual": None,
            "currency": None, "remote_id": None, "attempts": 1, "created": stamp(), "updated": stamp()}
    project["tasks"].append(task)
    store.save(project)  # Persist before a potentially billable remote submission.
    prompt = snap["inputs"]["text"]["data"]["text"]
    try:
        if task["provider"] == "DeepSeek":
            facts = project["brief_versions"][-1]["facts"] if project["brief_versions"] else []
            context = [{"field": f["field"], "value": f["value"]} for f in facts]
            messages = [{"role": "system", "content": "仅以已确认产品事实为依据；未知特征明确标记为推断。已确认事实：" + str(context)},
                        {"role": "user", "content": prompt}]
            result = deepseek_complete(key, messages)
            task.update(status="待审核", remote_id=result["id"], result={"text": result["text"], "usage": result["usage"]})
        elif task["provider"] == "SeeAny":
            source, raw = _reference(store, project, snap)
            url = seeany.upload_image(key, source["name"], raw)
            config = snap["inputs"].get("config", {}).get("data", {})
            ratio = config.get("ratio", "1:1")
            if ratio not in ("1:1", "3:4", "4:3", "9:16", "16:9", "3:2", "2:3"):
                raise ValueError("不支持的图片比例")
            payload = {"aiTypeId": 113, "aiType": "smartImg", "prompt": prompt, "inputImgs": [url],
                       "imgNum": 1, "imgRatio": ratio, "mode": "nano2", "size": "1K"}
            result = seeany.submit(key, "/api/ai/smarttask", payload)
            remote_id = (result.get("data") or {}).get("task_uuid")
            if not remote_id:
                raise ValueError("SeeAny 未返回任务标识；请先核对远端记录")
            task.update(status="远端运行中", remote_id=remote_id)
    except Exception as exc:
        task.update(status="待核对", error=str(exc)[:300])
        store.save(project)
        raise ValueError(task["error"] + "；如远端可能已受理，请先恢复状态，勿重复提交") from exc
    task["updated"] = stamp()
    store.save(project)
    return task


def sync(store, project, task_id):
    task = next((t for t in project["tasks"] if t["id"] == task_id), None)
    if not task:
        raise ValueError("任务不存在")
    if task["provider"] == "SeeAny":
        if not task["remote_id"]:
            raise ValueError("缺少远端任务标识，需人工核对")
        result = seeany.task_status(provider_key("SEEANY_API_KEY"), task["remote_id"])
        data = result.get("data") or {}
        node = data.get("task") if isinstance(data.get("task"), dict) else data
        status = str(node.get("status") or node.get("task_status") or "").lower()
        task["status"] = {"succeeded": "待审核", "failed": "失败", "partial_failed": "部分失败"}.get(status, "远端运行中")
        if status in ("succeeded", "partial_failed"):
            task.setdefault("imported_urls", [])
            task.setdefault("asset_ids", [])
            for url in seeany.result_assets(data):
                if url in task["imported_urls"]:
                    continue
                raw, suffix = seeany.download_image(url)
                source = store.add_source(project, f"seeany-{task['id']}-{len(task['asset_ids']) + 1}{suffix}",
                                          {".png": "image/png", ".jpg": "image/jpeg", ".webp": "image/webp"}[suffix],
                                          base64.b64encode(raw).decode())
                source["origin"] = "SeeAny candidate"
                if task.get("kind") == "master":
                    source["candidate_group"] = task["candidate_group"]
                    source["view_label"] = task["view_label"]
                    source["brief_id"] = task["input_snapshot"]["brief_id"]
                    source["reference_ids"] = [task["input_snapshot"]["source_id"]]
                elif task.get("kind") == "preview":
                    source["reference_ids"] = [task["source_id"]]
                    source["canvas_node_id"] = task["input_snapshot"]["config_node_id"]
                elif task.get("kind") == "gallery_image":
                    source["origin"] = "SeeAny gallery"
                    source["gallery_plan_id"] = task["input_snapshot"]["plan_id"]
                    source["gallery_item_id"] = task["input_snapshot"]["item"]["id"]
                    source["reference_ids"] = [r["source_id"] for r in task["input_snapshot"]["references"]]
                task["asset_ids"].append(source["id"])
                task["imported_urls"].append(url)
                store.save(project)
    elif task["provider"] == "Flova":
        task["result"] = flova.recover(task["external_project_id"])
        task["status"] = "待审核"
    else:
        return task
    task["updated"] = stamp()
    store.save(project)
    return task
