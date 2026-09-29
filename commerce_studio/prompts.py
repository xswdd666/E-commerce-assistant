"""Immutable structured prompts and one-image SeeAny exploration."""

from __future__ import annotations

import copy
import json

from app import seeany
from app.providers import deepseek_complete
from .core import fingerprint, ident, stamp
from .service import provider_key


TEXT_FIELDS = ("product", "scene", "composition", "lighting", "negative")
RATIOS = {"1:1", "3:4", "4:3", "9:16", "16:9", "3:2", "2:3"}


def _context(project):
    if not project["brief_versions"] or not project["master_versions"]:
        raise ValueError("请先确认简报与三视图母版")
    brief, master = project["brief_versions"][-1], project["master_versions"][-1]
    if master["brief_id"] != brief["id"]:
        raise ValueError("三视图母版未依据当前简报确认")
    return brief, master


def _clean(fields):
    if not isinstance(fields, dict):
        raise ValueError("提示词字段无效")
    result = {}
    for field in TEXT_FIELDS:
        value = fields.get(field)
        if not isinstance(value, str) or not value.strip() or len(value) > 2000:
            raise ValueError(f"提示词字段 {field} 不能为空或过长")
        result[field] = value.strip()
    ratio = fields.get("ratio") or "1:1"
    if ratio not in RATIOS:
        raise ValueError("提示词画幅无效")
    result["ratio"] = ratio
    return result


def render(fields):
    return (f"产品特征：{fields['product']}\n场景：{fields['scene']}\n构图：{fields['composition']}\n"
            f"光线：{fields['lighting']}\n禁止变化：{fields['negative']}\n"
            "仅依据已确认产品事实表达卖点；不要把推断结构写成真实规格；不要生成可读文字。")


def save_version(store, project, fields, parent_id=None, origin="人工编辑", changed_fields=None):
    brief, master = _context(project)
    clean = _clean(fields)
    parent = next((v for v in project["prompt_versions"] if v["id"] == parent_id), None) if parent_id else None
    if parent_id and not parent:
        raise ValueError("提示词父版本不存在")
    version = {"id": ident(), "parent_id": parent_id, "brief_id": brief["id"], "master_id": master["id"],
               "fields": clean, "text": render(clean), "origin": origin,
               "changed_fields": changed_fields or [key for key in clean if not parent or parent["fields"].get(key) != clean[key]],
               "created": stamp()}
    project["prompt_versions"].append(version)
    store.save(project)
    return version


def change_quote(project, parent_id, changed_fields, instruction):
    brief, master = _context(project)
    if any(t.get("kind") == "prompt_change" and t.get("input_snapshot", {}).get("parent_id") == parent_id
           and t.get("status") in ("远端运行中", "待核对") for t in project["tasks"]):
        raise ValueError("此提示词版本有未核对的改写任务，请先处理")
    parent = next((v for v in project["prompt_versions"] if v["id"] == parent_id), None)
    if not parent or parent["brief_id"] != brief["id"] or parent["master_id"] != master["id"]:
        raise ValueError("请选择当前简报和母版对应的提示词版本")
    if not isinstance(changed_fields, list) or not changed_fields or len(changed_fields) != len(set(changed_fields)) or any(field not in TEXT_FIELDS for field in changed_fields):
        raise ValueError("请选择需要修改的字段")
    if not isinstance(instruction, str) or not instruction.strip() or len(instruction) > 2000:
        raise ValueError("请填写有效的修改要求")
    snapshot = {"brief_id": brief["id"], "master_id": master["id"], "parent_id": parent_id,
                "fields": copy.deepcopy(parent["fields"]), "changed_fields": changed_fields,
                "instruction": instruction.strip(),
                "known_facts": [{"field": f["field"], "value": f["value"]} for f in brief["facts"]]}
    return {"provider": "DeepSeek", "input_snapshot": snapshot, "fingerprint": fingerprint(snapshot),
            "estimate": None, "currency": None, "pricing_source": "DeepSeek 实时价格未核实", "reliable": False,
            "requires_explicit_run": True}


def propose_changes(store, project, body):
    request_id = body.get("request_id")
    if not isinstance(request_id, str) or not 8 <= len(request_id) <= 120:
        raise ValueError("请提供请求标识以避免重复提交")
    existing = next((t for t in project["tasks"] if t.get("idempotency_key") == request_id), None)
    if existing:
        return existing
    offer = change_quote(project, body.get("parent_id"), body.get("changed_fields"), body.get("instruction"))
    if body.get("approved_fingerprint") != offer["fingerprint"]:
        raise ValueError("提示词输入已变化，请重新查看快照")
    key = provider_key("DEEPSEEK_API_KEY")
    if not key:
        raise ValueError("缺少 DeepSeek API Key")
    snapshot = offer["input_snapshot"]
    task = {"id": ident(), "kind": "prompt_change", "provider": "DeepSeek", "status": "远端运行中",
            "idempotency_key": request_id, "input_snapshot": snapshot, "estimate": None, "actual": None,
            "currency": None, "remote_id": None, "attempts": 1, "created": stamp(), "updated": stamp()}
    project["tasks"].append(task)
    store.save(project)
    try:
        messages = [{"role": "system", "content": "你是广告提示词编辑器。只返回 JSON 对象：{\"changes\":[{\"字段名\":\"新值\"}]}。给出 2 到 3 个明确不同的方向。只能输出用户允许修改的字段，不能改变其余字段；商品卖点只依据已确认事实。"},
                    {"role": "user", "content": json.dumps(snapshot, ensure_ascii=False)}]
        result = deepseek_complete(key, messages, json_mode=True)
        changes = json.loads(result["text"]).get("changes")
        if not isinstance(changes, list) or not 2 <= len(changes) <= 3:
            raise ValueError("DeepSeek 未返回 2 至 3 个提示词方向")
        for change in changes:
            if not isinstance(change, dict) or set(change) != set(snapshot["changed_fields"]) or any(not isinstance(value, str) or not value.strip() for value in change.values()):
                raise ValueError("DeepSeek 修改了未授权字段或缺少字段")
        if len({fingerprint(change) for change in changes}) != len(changes):
            raise ValueError("DeepSeek 返回了重复的提示词方向")
        versions = []
        for change in changes:
            merged = {**snapshot["fields"], **change}
            versions.append(save_version(store, project, merged, parent_id=snapshot["parent_id"], origin="DeepSeek 候选", changed_fields=snapshot["changed_fields"]))
        task.update(status="待审核", remote_id=result["id"], result={"version_ids": [v["id"] for v in versions], "usage": result["usage"]})
    except Exception as exc:
        task.update(status="待核对", error=str(exc)[:300])
        store.save(project)
        raise ValueError(task["error"] + "；如远端可能已受理，请先核对，勿重复提交") from exc
    task["updated"] = stamp()
    store.save(project)
    return task


def preview_quote(store, project, version_id, reference_id):
    brief, master = _context(project)
    if any(t.get("kind") == "prompt_preview" and t.get("input_snapshot", {}).get("version_id") == version_id
           and t.get("status") in ("远端运行中", "待核对") for t in project["tasks"]):
        raise ValueError("此提示词版本有未核对的试图任务，请先同步")
    version = next((v for v in project["prompt_versions"] if v["id"] == version_id), None)
    if not version or version["brief_id"] != brief["id"] or version["master_id"] != master["id"]:
        raise ValueError("提示词版本与当前简报或母版不匹配")
    if reference_id not in master["asset_ids"]:
        raise ValueError("预览参考图须来自当前已确认母版")
    source, _ = store.source_bytes(project, reference_id)
    snapshot = {"brief_id": brief["id"], "master_id": master["id"], "version_id": version_id,
                "prompt": version["text"], "ratio": version["fields"]["ratio"],
                "reference_id": reference_id, "reference_sha256": source["sha256"]}
    return {"provider": "SeeAny", "input_snapshot": snapshot, "fingerprint": fingerprint(snapshot),
            "estimate": None, "currency": None, "pricing_source": "SeeAny 单张预览实时价格未核实", "reliable": False,
            "requires_explicit_run": True}


def run_preview(store, project, body):
    request_id = body.get("request_id")
    if not isinstance(request_id, str) or not 8 <= len(request_id) <= 120:
        raise ValueError("请提供请求标识以避免重复提交")
    existing = next((t for t in project["tasks"] if t.get("idempotency_key") == request_id), None)
    if existing:
        return existing
    offer = preview_quote(store, project, body.get("version_id"), body.get("reference_id"))
    if body.get("approved_fingerprint") != offer["fingerprint"]:
        raise ValueError("提示词预览输入已变化，请重新查看快照")
    key = provider_key("SEEANY_API_KEY")
    if not key:
        raise ValueError("缺少 SeeAny API Key")
    snapshot = offer["input_snapshot"]
    task = {"id": ident(), "kind": "prompt_preview", "provider": "SeeAny", "status": "远端运行中",
            "idempotency_key": request_id, "input_snapshot": snapshot, "estimate": None, "actual": None,
            "currency": None, "remote_id": None, "attempts": 1, "created": stamp(), "updated": stamp()}
    project["tasks"].append(task)
    store.save(project)
    try:
        source, raw = store.source_bytes(project, snapshot["reference_id"])
        url = seeany.upload_image(key, source["name"], raw)
        payload = {"aiTypeId": 113, "aiType": "smartImg", "prompt": snapshot["prompt"],
                   "inputImgs": [url], "imgNum": 1, "imgRatio": snapshot["ratio"], "mode": "nano2", "size": "1K"}
        result = seeany.submit(key, "/api/ai/smarttask", payload)
        remote_id = (result.get("data") or {}).get("task_uuid")
        if not remote_id:
            raise ValueError("SeeAny 未返回试图任务标识")
        task["remote_id"] = remote_id
    except Exception as exc:
        task.update(status="待核对", error=str(exc)[:300])
        store.save(project)
        raise ValueError(task["error"] + "；请先核对远端，勿重复提交") from exc
    task["updated"] = stamp()
    store.save(project)
    return task


def review_preview(store, project, version_id, source_id, decision, reason=""):
    if decision not in ("采用", "废图"):
        raise ValueError("请选择采用或废图")
    task = next((t for t in project["tasks"] if t.get("kind") == "prompt_preview" and
                 t["input_snapshot"]["version_id"] == version_id and source_id in t.get("asset_ids", [])), None)
    if not task:
        raise ValueError("图片不是此提示词版本的试图候选")
    brief, master = _context(project)
    if task["input_snapshot"]["brief_id"] != brief["id"] or task["input_snapshot"]["master_id"] != master["id"]:
        raise ValueError("上游版本已变化，请重新试图")
    if decision == "废图" and not str(reason).strip():
        raise ValueError("请记录废图原因")
    review = {"id": ident(), "version_id": version_id, "source_id": source_id, "decision": decision,
              "reason": str(reason).strip()[:500], "reviewer": "local", "at": stamp()}
    project["prompt_reviews"].append(review)
    if decision == "采用":
        project["prompt_adoption"] = {"version_id": version_id, "source_id": source_id, "review_id": review["id"]}
    elif project["prompt_adoption"] and project["prompt_adoption"]["source_id"] == source_id:
        project["prompt_adoption"] = None
    store.save(project)
    return review
