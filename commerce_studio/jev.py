"""Explicit, read-only Jev comparison of three approved-brief ad directions."""

from __future__ import annotations

import json
import math
from urllib.request import Request, urlopen

from .core import fingerprint, ident, stamp
from .service import provider_key


ENDPOINT = "https://api.typesafe.ai/v1/systemone"
MODEL = "jev-latest"


def quote(project):
    if not project["brief_versions"] or not project["master_versions"]:
        raise ValueError("请先确认产品简报和母版")
    brief = project["brief_versions"][-1]
    master = project["master_versions"][-1]
    task = next((item for item in reversed(project["tasks"]) if item.get("kind") == "directions" and item.get("status") == "待审核" and item.get("result", {}).get("direction_ids")), None)
    if not task:
        raise ValueError("请先生成三个广告方向")
    directions = [next((item for item in project["directions"] if item["id"] == direction_id), None) for direction_id in task["result"]["direction_ids"]]
    if len(directions) != 3 or any(not item or item["brief_id"] != brief["id"] or item["master_id"] != master["id"] for item in directions):
        raise ValueError("广告方向的上游版本已变化，请重新生成")
    snapshot = {"brief_id": brief["id"], "master_id": master["id"], "direction_task_id": task["id"],
                "facts": [{"field": item["field"], "value": item["value"]} for item in brief["facts"]],
                "directions": [{key: direction[key] for key in ("id", "title", "audience", "opening", "selling_point", "ending")} for direction in directions]}
    return {"provider": "Jev", "model": MODEL, "input_snapshot": snapshot, "fingerprint": fingerprint(snapshot),
            "estimate": None, "currency": None, "pricing_source": "TypeSafe 账户价格尚未核对，费用未知", "reliable": False,
            "requires_explicit_run": True}


def run(store, project, body, transport=urlopen):
    request_id = body.get("request_id")
    if not isinstance(request_id, str) or not 8 <= len(request_id) <= 120:
        raise ValueError("请提供请求标识以避免重复提交")
    existing = next((item for item in project["tasks"] if item.get("idempotency_key") == request_id), None)
    if existing:
        return existing
    offer = quote(project)
    if body.get("approved_fingerprint") != offer["fingerprint"]:
        raise ValueError("Jev 输入已变化，请重新查看快照")
    if any(item.get("kind") == "jev_direction" and item.get("input_fingerprint") == offer["fingerprint"] and item["status"] != "失败" for item in project["tasks"]):
        raise ValueError("这组三个方向已有 Jev 任务，请先核对原任务")
    key = provider_key("TYPESAFE_API_KEY")
    if not key:
        raise ValueError("缺少 TypeSafe Jev API Key；可在本机 .env 设置 TYPESAFE_API_KEY")
    snapshot = offer["input_snapshot"]
    task = {"id": ident(), "kind": "jev_direction", "provider": "Jev", "model": MODEL, "status": "远端运行中",
            "idempotency_key": request_id, "input_fingerprint": offer["fingerprint"], "input_snapshot": snapshot,
            "estimate": None, "actual": None, "currency": None, "remote_id": None, "attempts": 1,
            "created": stamp(), "updated": stamp()}
    project["tasks"].append(task)
    store.save(project)
    labels = {f"d{index}": direction for index, direction in enumerate(snapshot["directions"])}
    criteria = {label: "；".join(str(direction[field]) for field in ("title", "audience", "opening", "selling_point", "ending")) for label, direction in labels.items()}
    payload = {"model": MODEL, "state": {"confirmed_facts": snapshot["facts"], "directions": criteria},
               "questions": {"direction": {"type": "choice", "instructions": "只依据已确认商品事实，选择最适合作为约 15 秒竖屏商品广告主线的方向。此判断仅供人工对照。", "criteria": criteria}}}
    try:
        request = Request(ENDPOINT, data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
                          headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"}, method="POST")
        with transport(request, timeout=90) as response:
            result = json.load(response)
        answer = result["answers"]["direction"]
        label = answer["choice"]
        confidence = answer["confidence"]
        probabilities = answer["probabilities"]
        if (answer.get("type") != "choice" or label not in labels or
                isinstance(confidence, bool) or not isinstance(confidence, (int, float)) or not math.isfinite(confidence) or not 0 <= confidence <= 1 or
                not isinstance(probabilities, dict) or set(probabilities) != set(labels) or
                any(isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or not 0 <= value <= 1 for value in probabilities.values())):
            raise ValueError("Jev 返回的选择或概率无效")
        model = result.get("model")
        usage = result.get("usage")
        observation = {"id": ident(), "task_id": task["id"], "brief_id": snapshot["brief_id"], "master_id": snapshot["master_id"],
                       "direction_ids": [item["id"] for item in snapshot["directions"]], "choice_id": labels[label]["id"],
                       "confidence": confidence, "probabilities": {labels[name]["id"]: value for name, value in probabilities.items()},
                       "model": model if isinstance(model, str) and model else MODEL,
                       "usage": usage if isinstance(usage, dict) else None, "comparisons": [], "created": stamp()}
        project["jev_observations"].append(observation)
        task.update(status="完成", result={"observation_id": observation["id"]}, updated=stamp())
        if project.get("direction_approval") in observation["direction_ids"]:
            observation["comparisons"].append({"direction_id": project["direction_approval"],
                                               "agrees": observation["choice_id"] == project["direction_approval"], "at": stamp()})
    except Exception as exc:
        task.update(status="待核对", error="Jev 请求失败或响应无法核验；请先核对 TypeSafe 账单和任务状态", updated=stamp())
        store.save(project)
        raise ValueError(task["error"]) from exc
    store.save(project)
    return task
