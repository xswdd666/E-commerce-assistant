"""Reviewable SeeAny gallery plans, per-image generation and editable exports."""

from __future__ import annotations

import base64
import copy
import html
import io
import json
import zipfile
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

from app import seeany
from .core import fingerprint, ident, stamp
from .service import provider_key


DEFAULT_KINDS = ("主图", "场景图", "卖点图一", "卖点图二", "细节图", "规格或使用图")
RATIOS = {"1:1", "3:4", "4:3", "9:16", "16:9", "3:2", "2:3"}


def _context(project):
    if not project["brief_versions"] or not project["master_versions"]:
        raise ValueError("请先确认简报与三视图母版")
    brief, master = project["brief_versions"][-1], project["master_versions"][-1]
    if master["brief_id"] != brief["id"]:
        raise ValueError("母版尚未依据当前简报确认")
    return brief, master


def _master_assets(store, project, master):
    return [store.source_bytes(project, source_id) for source_id in master["asset_ids"]]


def plan_quote(store, project, requirement=""):
    brief, master = _context(project)
    if any(t.get("kind") == "gallery_plan" and t["status"] in ("远端运行中", "待核对") for t in project["tasks"]):
        raise ValueError("上次 SeeAny 套图策划尚未核对，请勿重复提交")
    if not isinstance(requirement, str) or len(requirement) > 2000:
        raise ValueError("套图策划补充要求过长")
    assets = _master_assets(store, project, master)
    snapshot = {"brief_id": brief["id"], "master_id": master["id"], "product_name": project["name"],
                "platform": "拼多多", "requirement": requirement.strip(),
                "facts": [{"field": f["field"], "value": f["value"]} for f in brief["facts"]],
                "references": [{"source_id": s["id"], "sha256": s["sha256"], "view": s["view_label"]} for s, _ in assets]}
    return {"provider": "SeeAny", "input_snapshot": snapshot, "fingerprint": fingerprint(snapshot),
            "estimate": None, "currency": None, "pricing_source": "SeeAny 实时策划价格未核实", "reliable": False,
            "requires_explicit_run": True}


def _request_id(body):
    value = body.get("request_id")
    if not isinstance(value, str) or not 8 <= len(value) <= 120:
        raise ValueError("请提供请求标识以避免重复提交")
    return value


def _task(project, kind, snapshot, request_id):
    task = {"id": ident(), "kind": kind, "provider": "SeeAny", "status": "远端运行中",
            "idempotency_key": request_id, "input_snapshot": snapshot, "estimate": None, "actual": None,
            "currency": None, "remote_id": None, "attempts": 1, "created": stamp(), "updated": stamp()}
    project["tasks"].append(task)
    return task


def _upload_master(store, project, master_id, key):
    master = next(v for v in project["master_versions"] if v["id"] == master_id)
    return [seeany.upload_image(key, source["name"], raw) for source, raw in _master_assets(store, project, master)]


def run_plan(store, project, body):
    request_id = _request_id(body)
    existing = next((t for t in project["tasks"] if t.get("idempotency_key") == request_id), None)
    if existing:
        return existing
    offer = plan_quote(store, project, body.get("requirement", ""))
    if body.get("approved_fingerprint") != offer["fingerprint"]:
        raise ValueError("套图策划输入已变化，请重新查看快照")
    key = provider_key("SEEANY_API_KEY")
    if not key:
        raise ValueError("缺少 SeeAny API Key")
    snapshot = offer["input_snapshot"]
    task = _task(project, "gallery_plan", snapshot, request_id)
    store.save(project)
    try:
        urls = _upload_master(store, project, snapshot["master_id"], key)
        payload = {"productName": snapshot["product_name"], "sellingPoints": "\n".join(f"{f['field']}：{f['value']}" for f in snapshot["facts"]),
                   "platform": snapshot["platform"], "desLang": "中文", "customRequirement": snapshot["requirement"], "inputImgs": urls}
        result = seeany.submit(key, "/api/ai/groupplan", payload)
        items = result.get("items")
        if not isinstance(items, list) or not items:
            raise ValueError("SeeAny 套图策划没有返回可编辑方案，请核对远端记录")
        version = save_plan(store, project, [{"kind": str(item.get("cateName") or item.get("name") or "图片"),
                                              "prompt": str(item.get("prompt") or ""), "ratio": item.get("imgRatio") or "1:1"}
                                             for item in items[:12] if isinstance(item, dict)], origin="SeeAny", parent_id=None)
        task.update(status="待审核", result_plan_id=version["id"])
    except Exception as exc:
        task.update(status="待核对", error=str(exc)[:300])
        store.save(project)
        raise ValueError(task["error"] + "；请先核对远端，勿重复提交") from exc
    task["updated"] = stamp()
    store.save(project)
    return task


def default_plan(store, project):
    brief, _ = _context(project)
    known = "；".join(f"{f['field']}：{f['value']}" for f in brief["facts"])
    items = [{"kind": kind, "prompt": f"拼多多商品详情页{kind}，保持产品主体外观与三视图一致。已确认事实：{known}。不要生成可读文字，文案由独立文字层添加。", "ratio": "1:1"} for kind in DEFAULT_KINDS]
    return save_plan(store, project, items, origin="本地默认", parent_id=None)


def save_plan(store, project, items, origin="人工编辑", parent_id=None):
    brief, master = _context(project)
    if not isinstance(items, list) or not 1 <= len(items) <= 12:
        raise ValueError("套图方案须包含 1 至 12 张图片")
    if parent_id and not any(v["id"] == parent_id for v in project["gallery_versions"]):
        raise ValueError("套图父版本不存在")
    cleaned = []
    for item in items:
        if not isinstance(item, dict):
            raise ValueError("套图项目无效")
        kind, prompt = str(item.get("kind") or "").strip(), str(item.get("prompt") or "").strip()
        ratio = item.get("ratio") or "1:1"
        if not kind or len(kind) > 80 or not prompt or len(prompt) > 4000 or ratio not in RATIOS:
            raise ValueError("套图类型、提示词或画幅无效")
        x, y = item.get("x", 50), item.get("y", 10)
        if not isinstance(x, (int, float)) or not isinstance(y, (int, float)) or not 0 <= x <= 100 or not 0 <= y <= 100:
            raise ValueError("文字位置须在画面范围内")
        title, subtitle = str(item.get("title") or "").strip(), str(item.get("subtitle") or "").strip()
        if len(title) > 100 or len(subtitle) > 200:
            raise ValueError("文字层过长")
        cleaned.append({"id": ident(), "kind": kind, "prompt": prompt, "ratio": ratio,
                        "title": title, "subtitle": subtitle, "x": x, "y": y})
    version = {"id": ident(), "parent_id": parent_id, "brief_id": brief["id"], "master_id": master["id"],
               "origin": origin, "items": cleaned, "created": stamp()}
    project["gallery_versions"].append(version)
    store.save(project)
    return version


def approve_plan(store, project, plan_id):
    brief, master = _context(project)
    version = next((v for v in project["gallery_versions"] if v["id"] == plan_id), None)
    if not version or version["brief_id"] != brief["id"] or version["master_id"] != master["id"]:
        raise ValueError("套图方案不存在或上游版本已变化")
    project["gallery_approval"] = plan_id
    store.save(project)
    return version


def image_quote(store, project, item_id):
    brief, master = _context(project)
    plan = next((v for v in project["gallery_versions"] if v["id"] == project["gallery_approval"]), None)
    if not plan or plan["brief_id"] != brief["id"] or plan["master_id"] != master["id"]:
        raise ValueError("请先批准基于当前简报和母版的套图方案")
    item = next((i for i in plan["items"] if i["id"] == item_id), None)
    if not item:
        raise ValueError("套图项目不存在")
    if any(t.get("kind") == "gallery_image" and t["input_snapshot"]["item"]["id"] == item_id
           and t["status"] in ("远端运行中", "待核对") for t in project["tasks"]):
        raise ValueError("此套图项目有未核对的远端任务，请先同步状态")
    snapshot = {"brief_id": brief["id"], "master_id": master["id"], "plan_id": plan["id"],
                "item": copy.deepcopy(item), "references": [{"source_id": s["id"], "sha256": s["sha256"]} for s, _ in _master_assets(store, project, master)]}
    return {"provider": "SeeAny", "input_snapshot": snapshot, "fingerprint": fingerprint(snapshot),
            "estimate": None, "currency": None, "pricing_source": "SeeAny 单张套图实时价格未核实", "reliable": False,
            "requires_explicit_run": True}


def run_image(store, project, body):
    request_id = _request_id(body)
    existing = next((t for t in project["tasks"] if t.get("idempotency_key") == request_id), None)
    if existing:
        return existing
    offer = image_quote(store, project, body.get("item_id"))
    if body.get("approved_fingerprint") != offer["fingerprint"]:
        raise ValueError("套图输入已变化，请重新查看快照")
    key = provider_key("SEEANY_API_KEY")
    if not key:
        raise ValueError("缺少 SeeAny API Key")
    snapshot = offer["input_snapshot"]
    task = _task(project, "gallery_image", snapshot, request_id)
    store.save(project)
    try:
        urls = _upload_master(store, project, snapshot["master_id"], key)
        brief = next(v for v in project["brief_versions"] if v["id"] == snapshot["brief_id"])
        item = snapshot["item"]
        payload = {"aiTypeId": 2373, "aiType": "imageSets", "productName": project["name"],
                   "sellingPoints": "\n".join(f"{f['field']}：{f['value']}" for f in brief["facts"]),
                   "platform": "拼多多", "desLang": "中文", "inputImgs": urls,
                   "groups": [{"mode": "nano-banana-pro", "name": item["kind"], "cateName": item["kind"],
                               "size": "1K", "imgNum": 1, "imgRatio": item["ratio"], "prompt": item["prompt"],
                               "options": {"电商平台": "拼多多", "文案语种": "中文"}, "refImgs": []}]}
        result = seeany.submit(key, "/api/ai/grouptask", payload)
        remote_id = (result.get("data") or {}).get("task_uuid")
        if not remote_id:
            raise ValueError("SeeAny 未返回套图任务标识，请先核对远端")
        task["remote_id"] = remote_id
    except Exception as exc:
        task.update(status="待核对", error=str(exc)[:300])
        store.save(project)
        raise ValueError(task["error"] + "；请先核对远端，勿重复提交") from exc
    task["updated"] = stamp()
    store.save(project)
    return task


def review_image(store, project, item_id, source_id, decision, reason=""):
    if decision not in ("采用", "废图"):
        raise ValueError("请选择采用或废图")
    task = next((t for t in project["tasks"] if t.get("kind") == "gallery_image" and
                 t["input_snapshot"]["item"]["id"] == item_id and source_id in t.get("asset_ids", [])), None)
    if not task:
        raise ValueError("图片不是此套图项目的生成候选")
    if task["input_snapshot"]["plan_id"] != project["gallery_approval"]:
        raise ValueError("套图方案已改变，请重新审核")
    if decision == "废图" and not str(reason).strip():
        raise ValueError("请记录废图原因")
    review = {"id": ident(), "item_id": item_id, "source_id": source_id, "decision": decision,
              "reason": str(reason).strip()[:500], "at": stamp(), "reviewer": "local"}
    project["gallery_reviews"].append(review)
    if decision == "采用":
        project["gallery_choices"][item_id] = source_id
    elif project["gallery_choices"].get(item_id) == source_id:
        project["gallery_choices"].pop(item_id)
    store.save(project)
    return review


def _font(size):
    for path in (Path("C:/Windows/Fonts/msyh.ttc"), Path("C:/Windows/Fonts/simhei.ttf")):
        if path.exists():
            return ImageFont.truetype(str(path), size)
    return ImageFont.load_default()


def export_gallery(store, project):
    brief, master = _context(project)
    plan = next((v for v in project["gallery_versions"] if v["id"] == project["gallery_approval"]), None)
    if not plan or plan["brief_id"] != brief["id"] or plan["master_id"] != master["id"]:
        raise ValueError("请先批准当前套图方案")
    if any(item["id"] not in project["gallery_choices"] for item in plan["items"]):
        raise ValueError("每张套图都需先挑选并采用一张生成图")
    archive = io.BytesIO()
    manifest = {"project_id": project["id"], "plan_id": plan["id"], "brief_id": brief["id"],
                "master_id": master["id"], "created": stamp(), "images": []}
    with zipfile.ZipFile(archive, "w", compression=zipfile.ZIP_DEFLATED) as output:
        for index, item in enumerate(plan["items"], 1):
            source_id = project["gallery_choices"][item["id"]]
            source, raw = store.source_bytes(project, source_id)
            if not source["mime"].startswith("image/"):
                raise ValueError("采用素材不是图片")
            image = Image.open(io.BytesIO(raw)).convert("RGB")
            width, height = image.size
            drawer = ImageDraw.Draw(image)
            svg_text = []
            for offset, content, scale in ((0, item["title"], .055), (0.075, item["subtitle"], .032)):
                if not content:
                    continue
                x = width * item["x"] / 100
                y = height * (item["y"] / 100 + offset)
                size = max(18, round(width * scale))
                stroke = max(1, round(size / 16))
                drawer.text((x, y), content, font=_font(size), fill="white", stroke_width=stroke,
                            stroke_fill="black", anchor="mm")
                svg_text.append(f'<text x="{x:.1f}" y="{y:.1f}" text-anchor="middle" dominant-baseline="middle" '
                                f'font-family="Microsoft YaHei,sans-serif" font-size="{size}" fill="white" '
                                f'stroke="black" stroke-width="{stroke * 2}" paint-order="stroke">{html.escape(content)}</text>')
            png = io.BytesIO()
            image.save(png, format="PNG")
            name = f"{index:02d}-{item['kind']}"
            output.writestr(f"gallery/{name}.png", png.getvalue())
            embedded = base64.b64encode(raw).decode("ascii")
            svg = (f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" viewBox="0 0 {width} {height}">'
                   f'<image href="data:{source["mime"]};base64,{embedded}" width="{width}" height="{height}"/>'
                   + "".join(svg_text) + "</svg>")
            output.writestr(f"gallery/{name}.svg", svg.encode("utf-8"))
            manifest["images"].append({"item_id": item["id"], "source_id": source_id, "source_sha256": source["sha256"],
                                       "kind": item["kind"], "prompt": item["prompt"], "title": item["title"],
                                       "subtitle": item["subtitle"], "reference_ids": master["asset_ids"]})
        script = next((s for s in project["script_versions"] if s["id"] == project["script_approval"]), None)
        if script:
            output.writestr("approved-script.txt", script["text"].encode("utf-8"))
        output.writestr("source-manifest.json", json.dumps(manifest, ensure_ascii=False, indent=2).encode("utf-8"))
    return archive.getvalue()
