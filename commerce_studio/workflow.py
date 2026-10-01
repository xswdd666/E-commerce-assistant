"""Execution protocol for the canvas-owned commerce graph.

The browser owns layout. This module accepts its current graph for each action and
resolves only connected, explicitly selected inputs. No project-global brief or
master is consulted.
"""

from __future__ import annotations

import base64
import json

from app import seeany
from app.providers import deepseek_complete
from .core import fingerprint, ident, stamp
from .service import provider_key, _existing_request


OUTPUTS = {
    "commerce:upload": "image", "commerce:details": "product_details",
    "commerce:views": "image", "commerce:prompt": "prompt",
    "commerce:generate": "image", "commerce:gallery": "image", "commerce:result": "image",
}
INPUTS = {
    "commerce:details": {"image": ("image", True), "text": ("text", False)},
    "commerce:views": {"image": ("image", True), "product_details": ("product_details", False)},
    "commerce:prompt": {"image": ("image", True), "product_details": ("product_details", False)},
    "commerce:generate": {"image": ("image", True), "prompt": ("prompt", False),
                          "product_details": ("product_details", False)},
    "commerce:gallery": {"image": ("image", True), "prompt": ("prompt", False),
                         "product_details": ("product_details", False)},
}
TEXT_KINDS = {"commerce:details", "commerce:prompt"}
PROVIDER = {"commerce:details": "DeepSeek", "commerce:prompt": "DeepSeek",
            "commerce:views": "SeeAny", "commerce:generate": "SeeAny", "commerce:gallery": "SeeAny"}
GALLERY_KINDS = {
    "商品白底图": "纯白背景，商品完整居中，保留真实轮廓、颜色和标识，柔和自然阴影，不添加文字和道具",
    "亚马逊主图": "符合亚马逊主图用途：纯白背景、仅展示商品本体及实际随附配件，不添加文字、徽章或虚构功能",
    "细节特写": "展示商品真实可见的材质和工艺细节，不虚构不可见结构",
    "产品多角度": "从另一个角度展示同一商品，保持外观一致；未提供的背面结构仅作推断",
    "营销主图海报": "制作商品营销主图海报，突出已提供的真实卖点，保留商品外观和品牌标识",
    "核心卖点图": "将已提供的核心卖点可视化，商品外观准确，不编造参数或认证",
}


def _graph(body):
    graph = body.get("graph")
    if not isinstance(graph, dict):
        raise ValueError("缺少当前画布快照")
    nodes = graph.get("nodes")
    edges = graph.get("edges")
    if not isinstance(nodes, list) or not isinstance(edges, list):
        raise ValueError("画布节点或连线无效")
    by_id = {n.get("id"): n for n in nodes if isinstance(n, dict) and isinstance(n.get("id"), str)}
    if len(by_id) != len(nodes):
        raise ValueError("画布节点标识重复")
    for edge in edges:
        if not isinstance(edge, dict):
            raise ValueError("连线无效")
        source, target = by_id.get(edge.get("fromNodeId")), by_id.get(edge.get("toNodeId"))
        if not source or not target:
            raise ValueError("连线指向不存在的节点")
        port = edge.get("targetPort")
        accepted = INPUTS.get(target.get("kind"), {}).get(port)
        if not accepted or OUTPUTS.get(source.get("kind")) != accepted[0]:
            raise ValueError("节点端口类型不兼容")
        if not accepted[1] and sum(e.get("toNodeId") == target["id"] and e.get("targetPort") == port for e in edges) > 1:
            raise ValueError(f"端口 {port} 只能连接一个输入")
    outgoing = {node_id: [] for node_id in by_id}
    for edge in edges:
        outgoing[edge["fromNodeId"]].append(edge["toNodeId"])
    visiting, visited = set(), set()

    def visit(node_id):
        if node_id in visiting:
            raise ValueError("画布连线不能形成循环")
        if node_id in visited:
            return
        visiting.add(node_id)
        for child in outgoing[node_id]:
            visit(child)
        visiting.remove(node_id)
        visited.add(node_id)

    for node_id in by_id:
        visit(node_id)
    return by_id, edges


def _version(project, node, kind):
    version_id = node.get("selected_version_id")
    version = next((v for v in project.get("workflow_versions", []) if v["id"] == version_id
                    and v["node_id"] == node["id"] and v["kind"] == kind), None)
    if not version:
        raise ValueError("上游尚未选择已确认版本")
    return version


def quote(store, project, body):
    by_id, edges = _graph(body)
    node_id = body.get("node_id")
    target = by_id.get(node_id)
    if not target or target.get("kind") not in PROVIDER:
        raise ValueError("目标业务节点无效")
    kind = target["kind"]
    inputs = []
    for edge in edges:
        if edge["toNodeId"] != node_id:
            continue
        source = by_id[edge["fromNodeId"]]
        value = {"edge_id": edge["id"], "node_id": source["id"], "port": edge["targetPort"],
                 "kind": source["kind"]}
        if edge["targetPort"] == "image":
            source_id = source.get("source_id")
            asset, _ = store.source_bytes(project, source_id)
            if not asset["mime"].startswith("image/"):
                raise ValueError("连入素材不是图片")
            value.update(source_id=source_id, sha256=asset["sha256"], name=asset["name"])
        elif edge["targetPort"] in ("prompt", "product_details"):
            version = _version(project, {**source, "selected_version_id": edge.get("selectedVersionId")}, source["kind"])
            if edge["targetPort"] == "product_details":
                for field in version.get("fields") or []:
                    if field["status"] == "已知事实" and not field.get("source_id"):
                        raise ValueError("已知事实缺少来源证据，请核实后再引用")
            value.update(version_id=version["id"], text=version.get("text"), fields=version.get("fields"))
        inputs.append(value)
    ports = [item["port"] for item in inputs]
    if kind in ("commerce:details", "commerce:views", "commerce:generate", "commerce:gallery") and "image" not in ports:
        raise ValueError("请先连接商品原图")
    if kind == "commerce:generate" and "prompt" not in ports:
        raise ValueError("请先连接并选择已确认提示词版本")
    if kind == "commerce:prompt" and not inputs and not str(target.get("draft") or "").strip():
        raise ValueError("请填写提示词草稿或连接输入")
    if kind == "commerce:views" and target.get("view") not in ("正面", "侧面", "背面"):
        raise ValueError("请选择正面、侧面或背面视角")
    snapshot = {"node_id": node_id, "kind": kind, "inputs": inputs,
                "draft": str(target.get("draft") or ""), "view": target.get("view"),
                "ratio": target.get("ratio") or "1:1", "model": target.get("model") or
                ("deepseek-flash" if kind in TEXT_KINDS else "nano-banana-pro" if kind in ("commerce:views", "commerce:gallery") else "nano2")}
    if snapshot["model"] not in (("nano-banana-pro", "gpt-image-2") if kind == "commerce:gallery" else
                                  ("deepseek-flash",) if kind in TEXT_KINDS else
                                  ("nano-banana-pro",) if kind == "commerce:views" else ("nano2",)):
        raise ValueError("当前节点尚不支持所选模型")
    if kind == "commerce:generate" and snapshot["ratio"] not in ("1:1", "3:4", "4:3", "9:16", "16:9", "3:2", "2:3"):
        raise ValueError("图片比例不受支持")
    if kind == "commerce:gallery":
        kinds = target.get("gallery_kinds") or []
        if not isinstance(kinds, list) or not 1 <= len(kinds) <= 6 or len(set(kinds)) != len(kinds) or any(k not in GALLERY_KINDS for k in kinds):
            raise ValueError("请选择 1 至 6 种套图类型")
        if len([i for i in inputs if i["port"] == "image"]) > 6:
            raise ValueError("电商套图最多连接 6 张商品图")
        size = target.get("size") or "1K"
        main_ratio, detail_ratio = target.get("main_ratio") or "1:1", target.get("detail_ratio") or "3:4"
        if size not in ("1K", "2K") or main_ratio not in ("1:1", "3:4", "4:3") or detail_ratio not in ("1:1", "3:4", "4:3"):
            raise ValueError("套图分辨率或比例无效")
        snapshot.update(gallery_kinds=kinds, size=size, main_ratio=main_ratio, detail_ratio=detail_ratio,
                        product_name=str(target.get("product_name") or "商品")[:100],
                        platform=str(target.get("platform") or "")[:80], market=str(target.get("market") or "")[:80],
                        language=str(target.get("language") or "中文")[:40], style=str(target.get("style") or "")[:120])
        if len(snapshot["draft"]) > 1000:
            raise ValueError("商品信息过长")
    return {"provider": PROVIDER[kind], "input_snapshot": snapshot,
            "fingerprint": fingerprint(snapshot), "estimate": None, "currency": None,
            "pricing_source": "未核实，费用未知", "reliable": False}


def confirm(store, project, body):
    kind = body.get("kind")
    if kind not in TEXT_KINDS:
        raise ValueError("只能确认文本节点")
    node_id = body.get("node_id")
    candidates = [t for t in project["tasks"] if t.get("node_id") == node_id and t.get("kind") == "workflow_text" and t.get("status") == "待审核"]
    fields = body.get("fields")
    text = str(body.get("text") or "").strip()
    if kind == "commerce:details":
        if not isinstance(fields, list) or not fields or any(not isinstance(f, dict) or
                not str(f.get("field") or "").strip() or not str(f.get("value") or "").strip() or
                f.get("status") not in ("待核实", "已知事实", "创意假设") for f in fields):
            raise ValueError("请逐项选择或填写商品信息及核实状态")
        selected = []
        for field in fields:
            source_id = field.get("source_id")
            if source_id and not any(s["id"] == source_id for s in project["sources"]):
                raise ValueError("事实来源不存在")
            if field["status"] == "已知事实" and not source_id:
                raise ValueError("已知事实需关联来源图片")
            if field.get("candidate_task_id") and not any(t["id"] == field["candidate_task_id"] for t in candidates):
                raise ValueError("候选任务不属于当前节点")
            selected.append({"field": field["field"].strip(), "value": field["value"].strip(),
                             "status": field["status"], "source_id": source_id,
                             "candidate_task_id": field.get("candidate_task_id")})
        fields = selected
        text = "\n".join(f"{f['field']}：{f['value']}（{f['status']}）" for f in fields)
    elif not text:
        raise ValueError("提示词不能为空")
    version = {"id": ident(), "node_id": node_id, "kind": kind, "text": text,
               "fields": fields if kind == "commerce:details" else None,
               "origin": "model+manual" if any(f.get("candidate_task_id") for f in fields or []) or body.get("candidate_task_id") else "manual",
               "candidate_task_ids": list({f.get("candidate_task_id") for f in fields or [] if f.get("candidate_task_id")}) if kind == "commerce:details" else [body["candidate_task_id"]] if body.get("candidate_task_id") else [],
               "created": stamp()}
    if body.get("candidate_task_id") and not any(t["id"] == body["candidate_task_id"] for t in candidates):
        raise ValueError("候选任务不存在")
    project.setdefault("workflow_versions", []).append(version)
    store.save(project)
    return version


def run(store, project, body):
    existing = _existing_request(project, body.get("request_id"))
    if existing:
        return existing
    if any(t.get("node_id") == body.get("node_id") and t.get("kind", "").startswith("workflow_")
           and t.get("status") == "待核对" for t in project["tasks"]):
        raise ValueError("此节点存在待核对任务，请先同步或记录远端核对结果")
    offer = quote(store, project, body)
    if body.get("approved_fingerprint") != offer["fingerprint"]:
        raise ValueError("画布输入已变化，请重新核对报价")
    request_id = body.get("request_id")
    if not isinstance(request_id, str) or not 8 <= len(request_id) <= 120:
        raise ValueError("请求标识无效")
    snapshot = offer["input_snapshot"]
    kind = snapshot["kind"]
    key = provider_key("DEEPSEEK_API_KEY" if kind in TEXT_KINDS else "SEEANY_API_KEY")
    if not key:
        raise ValueError(f"缺少 {offer['provider']} API Key")
    task = {"id": ident(), "kind": "workflow_text" if kind in TEXT_KINDS else "workflow_views" if kind == "commerce:views" else "workflow_gallery" if kind == "commerce:gallery" else "workflow_image",
            "node_id": snapshot["node_id"], "provider": offer["provider"], "model": snapshot["model"], "status": "远端运行中",
            "idempotency_key": request_id, "input_snapshot": snapshot, "estimate": None, "actual": None,
            "currency": None, "pricing_source": offer["pricing_source"], "remote_id": None,
            "attempts": 1, "created": stamp(), "updated": stamp()}
    project["tasks"].append(task)
    store.save(project)
    try:
        if kind in TEXT_KINDS:
            content = [{"type": "text", "text": ("提取商品图片中可见的信息。仅输出 JSON：{\"facts\":[{\"field\":\"字段\",\"value\":\"内容\"}]}。不可见结构和规格标记待核实。" if kind == "commerce:details" else "根据以下实际连入的内容，生成一份商品图提示词，直接输出提示词全文。") + "\n" + snapshot["draft"]}]
            for item in snapshot["inputs"]:
                if item["port"] == "image":
                    asset, raw = store.source_bytes(project, item["source_id"])
                    content.append({"type": "image_url", "image_url": {"url": f"data:{asset['mime']};base64,{base64.b64encode(raw).decode()}", "detail": "original"}})
                elif item.get("text"):
                    content[0]["text"] += "\n" + item["text"]
            task["provider_request"] = {"model": snapshot["model"], "max_tokens": 1600,
                                         "json_mode": kind == "commerce:details", "text": content[0]["text"],
                                         "image_source_ids": [i["source_id"] for i in snapshot["inputs"] if i["port"] == "image"]}
            store.save(project)
            result = deepseek_complete(key, [{"role": "user", "content": content}], json_mode=kind == "commerce:details")
            task["provider_response_text"] = result["text"]
            if kind == "commerce:details":
                parsed = json.loads(result["text"])
                facts = parsed.get("facts")
                if not isinstance(facts, list):
                    raise ValueError("模型未返回商品信息候选")
                task["candidates"] = [{"field": str(f.get("field") or ""), "value": str(f.get("value") or ""),
                                       "status": "待核实", "source_id": next((i["source_id"] for i in snapshot["inputs"] if i["port"] == "image"), None),
                                       "candidate_task_id": task["id"]} for f in facts if isinstance(f, dict) and f.get("field") and f.get("value")]
            else:
                task["candidate_text"] = result["text"]
            task.update(status="待审核", remote_id=result["id"], result={"usage": result["usage"], "model": result["model"]})
        else:
            uploaded = []
            for image in (i for i in snapshot["inputs"] if i["port"] == "image"):
                asset, raw = store.source_bytes(project, image["source_id"])
                uploaded.append(seeany.upload_image(key, asset["name"], raw))
            if kind == "commerce:gallery":
                details = next((i["text"] for i in snapshot["inputs"] if i["port"] == "product_details"), "")
                extra = next((i["text"] for i in snapshot["inputs"] if i["port"] == "prompt"), "")
                selling_points = "\n".join(part for part in (snapshot["draft"], details) if part)
                groups = []
                for name in snapshot["gallery_kinds"]:
                    groups.append({"mode": snapshot["model"], "name": name, "cateName": name,
                                   "size": snapshot["size"], "imgNum": 1,
                                   "imgRatio": snapshot["detail_ratio"] if name in ("细节特写", "核心卖点图") else snapshot["main_ratio"],
                                   "prompt": f"基于输入商品图生成{name}。{GALLERY_KINDS[name]}。商品信息：{selling_points}。{extra}"[:4000],
                                   "options": {"电商平台": snapshot["platform"], "目标市场": snapshot["market"], "文案语种": snapshot["language"]},
                                   "refImgs": []})
                payload = {"aiTypeId": 2373, "aiType": "imageSets", "inputImgs": uploaded,
                           "productName": snapshot["product_name"], "sellingPoints": selling_points,
                           "platform": snapshot["platform"], "desMarket": snapshot["market"],
                           "desLang": snapshot["language"], "visualStyle": snapshot["style"], "groups": groups}
                endpoint = "/api/ai/grouptask"
            elif kind == "commerce:views":
                payload = {"aiTypeId": 464, "aiType": "multiview", "inputImgs": uploaded,
                           "views": {"正面": "front", "侧面": "side", "背面": "back"}[snapshot["view"]],
                           "imgNum": 1, "imgRatio": "1:1", "mode": "nano-banana-pro", "size": "1K"}
                endpoint = "/api/ai/batchsmarttask"
            else:
                prompt = next(i["text"] for i in snapshot["inputs"] if i["port"] == "prompt")
                details = next((i["text"] for i in snapshot["inputs"] if i["port"] == "product_details"), "")
                payload = {"aiTypeId": 113, "aiType": "smartImg", "prompt": prompt + ("\n" + details if details else ""),
                           "inputImgs": uploaded, "imgNum": 1, "imgRatio": snapshot["ratio"], "mode": "nano2", "size": "1K"}
                endpoint = "/api/ai/smarttask"
            task["provider_request"] = {k: v for k, v in payload.items() if k != "inputImgs"}
            store.save(project)
            result = seeany.submit(key, endpoint, payload)
            remote_id = (result.get("data") or {}).get("task_uuid")
            if not remote_id:
                raise ValueError("SeeAny 未返回任务标识")
            task["remote_id"] = remote_id
    except Exception as exc:
        task.update(status="待核对", error=str(exc)[:300], updated=stamp())
        store.save(project)
        raise ValueError(task["error"] + "；请先核对远端，勿重复提交") from exc
    task["updated"] = stamp()
    store.save(project)
    return task


def review(store, project, body):
    task = next((t for t in project["tasks"] if t["id"] == body.get("task_id") and
                 t.get("kind") in ("workflow_image", "workflow_views", "workflow_gallery")), None)
    if not task or body.get("source_id") not in task.get("asset_ids", []):
        raise ValueError("图片结果不存在")
    if body.get("decision") not in ("采用", "废图"):
        raise ValueError("请选择采用或废图")
    reviews = project.setdefault("workflow_reviews", [])
    review_item = {"id": ident(), "task_id": task["id"], "source_id": body["source_id"],
                   "decision": body["decision"], "created": stamp()}
    reviews.append(review_item)
    store.save(project)
    return review_item
