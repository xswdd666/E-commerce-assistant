"""DeepSeek visual observations are review aids, never product facts."""

from __future__ import annotations

import base64
import json

from app.providers import deepseek_complete
from .core import fingerprint, ident, stamp
from .service import provider_key


FIELDS = ("颜色", "形状", "结构", "Logo", "文字", "配件", "场景可信度", "卖点表达")
MIMES = {"image/jpeg", "image/png", "image/gif", "image/webp"}


def quote(store, project, original_id, candidate_id):
    if not project["brief_versions"]:
        raise ValueError("请先确认产品简报")
    original, raw_original = store.source_bytes(project, original_id)
    candidate, raw_candidate = store.source_bytes(project, candidate_id)
    if original_id == candidate_id or original["mime"] not in MIMES or candidate["mime"] not in MIMES:
        raise ValueError("请选择不同的原始实拍和候选图片")
    if original.get("origin", "").startswith("SeeAny") or not candidate.get("origin", "").startswith("SeeAny"):
        raise ValueError("图片观察必须对照原始实拍与 SeeAny 候选图")
    if 4 * (len(raw_original) + len(raw_candidate)) // 3 >= 48 * 1024 * 1024:
        raise ValueError("两张图片超过 DeepSeek 图文请求大小，请先导入较小图片")
    brief = project["brief_versions"][-1]
    snapshot = {"original_id": original_id, "original_sha256": original["sha256"],
                "candidate_id": candidate_id, "candidate_sha256": candidate["sha256"],
                "brief_id": brief["id"], "facts": [{"field": f["field"], "value": f["value"]} for f in brief["facts"]]}
    return {"provider": "DeepSeek", "input_snapshot": snapshot, "fingerprint": fingerprint(snapshot),
            "estimate": None, "currency": None, "pricing_source": "图文 token 用量事前未知", "reliable": False,
            "requires_explicit_run": True}


def run(store, project, body):
    request_id = body.get("request_id")
    if not isinstance(request_id, str) or not 8 <= len(request_id) <= 120:
        raise ValueError("请提供请求标识以避免重复提交")
    existing = next((t for t in project["tasks"] if t.get("idempotency_key") == request_id), None)
    if existing:
        return existing
    offer = quote(store, project, body.get("original_id"), body.get("candidate_id"))
    if body.get("approved_fingerprint") != offer["fingerprint"]:
        raise ValueError("图片或简报已变化，请重新查看输入")
    key = provider_key("DEEPSEEK_API_KEY")
    if not key:
        raise ValueError("缺少 DeepSeek API Key")
    snapshot = offer["input_snapshot"]
    task = {"id": ident(), "kind": "image_observation", "provider": "DeepSeek", "status": "远端运行中",
            "idempotency_key": request_id, "input_snapshot": snapshot, "estimate": None, "actual": None,
            "currency": None, "remote_id": None, "attempts": 1, "created": stamp(), "updated": stamp()}
    project["tasks"].append(task)
    store.save(project)
    try:
        original, original_raw = store.source_bytes(project, snapshot["original_id"])
        candidate, candidate_raw = store.source_bytes(project, snapshot["candidate_id"])
        content = [{"type": "text", "text": "先看原始实拍，再看生成候选。只对照可见内容，未知写无法判断。已确认事实：" + json.dumps(snapshot["facts"], ensure_ascii=False)}]
        for source, raw in ((original, original_raw), (candidate, candidate_raw)):
            content.append({"type": "image_url", "image_url": {"url": f"data:{source['mime']};base64,{base64.b64encode(raw).decode('ascii')}", "detail": "original"}})
        messages = [{"role": "system", "content": "对比商品原始实拍和候选图。只返回 JSON 对象，键 observations 下恰好包含颜色、形状、结构、Logo、文字、配件、场景可信度、卖点表达八个字符串字段。逐项指出一致、差异或无法判断；不得替用户批准真实性。"},
                    {"role": "user", "content": content}]
        response = deepseek_complete(key, messages, json_mode=True)
        parsed = json.loads(response["text"])
        fields = parsed.get("observations")
        if not isinstance(fields, dict) or any(not isinstance(fields.get(field), str) or not fields[field].strip() for field in FIELDS):
            raise ValueError("DeepSeek 未返回完整图片观察")
        observation = {"id": ident(), "task_id": task["id"], "original_id": snapshot["original_id"],
                       "candidate_id": snapshot["candidate_id"], "brief_id": snapshot["brief_id"],
                       "fields": {field: fields[field].strip() for field in FIELDS}, "corrections": [], "created": stamp()}
        project["image_observations"].append(observation)
        task.update(status="待审核", remote_id=response["id"], result={"observation_id": observation["id"], "usage": response["usage"]})
    except Exception as exc:
        task.update(status="待核对", error=str(exc)[:300])
        store.save(project)
        raise ValueError(task["error"] + "；如远端可能已受理，请先核对，勿重复提交") from exc
    task["updated"] = stamp()
    store.save(project)
    return task


def correct(store, project, observation_id, field, text):
    observation = next((item for item in project["image_observations"] if item["id"] == observation_id), None)
    if not observation or field not in FIELDS or not isinstance(text, str) or not text.strip():
        raise ValueError("请选择观察条目、字段并填写纠正内容")
    correction = {"id": ident(), "field": field, "text": text.strip(), "at": stamp(), "reviewer": "local"}
    observation["corrections"].append(correction)
    store.save(project)
    return correction
