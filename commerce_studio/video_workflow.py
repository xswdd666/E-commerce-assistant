"""Canvas-owned video workflow layered beside the frozen image workflow."""

from __future__ import annotations

import base64
import json
import tempfile
import threading
from pathlib import Path

from app import flova
from app.providers import deepseek_complete
from . import flova_flow
from .core import fingerprint, ident, stamp
from .service import provider_key, _existing_request

SCRIPT_KIND = "commerce:script"
SHOT_KIND = "commerce:shot"
COMPOSE_KIND = "commerce:compose"
VIDEO_RESULT_KIND = "commerce:video_result"


def _nodes(body):
    graph = body.get("graph")
    if not isinstance(graph, dict) or not isinstance(graph.get("nodes"), list) or not isinstance(graph.get("edges"), list):
        raise ValueError("缺少当前画布快照")
    nodes = {node.get("id"): node for node in graph["nodes"] if isinstance(node, dict) and isinstance(node.get("id"), str)}
    if len(nodes) != len(graph["nodes"]):
        raise ValueError("画布节点标识无效")
    return nodes, graph["edges"]


def _version(project, node, kind):
    version_id = node.get("selected_version_id")
    version = next((item for item in project.get("workflow_versions", []) if item["id"] == version_id and item["node_id"] == node["id"] and item["kind"] == kind), None)
    if not version:
        raise ValueError("上游尚未选择已确认版本")
    return version


def _inputs(project, nodes, edges, target_id):
    values = []
    for edge in edges:
        if edge.get("toNodeId") != target_id:
            continue
        source = nodes.get(edge.get("fromNodeId"))
        if not source:
            raise ValueError("连线指向不存在的节点")
        port = edge.get("targetPort")
        if port == "image":
            source_id = source.get("source_id")
            if not source_id:
                raise ValueError("商品图片结果缺少素材")
            asset, _ = project_store_source(project, source_id)
            if not asset["mime"].startswith("image/"):
                raise ValueError("视频参考必须是图片结果")
            values.append({"port": port, "node_id": source["id"], "source_id": source_id, "sha256": asset["sha256"], "name": asset["name"]})
        elif port in ("product_details", "script"):
            kind = "commerce:details" if port == "product_details" else SCRIPT_KIND
            version = _version(project, {**source, "selected_version_id": edge.get("selectedVersionId")}, kind)
            values.append({"port": port, "node_id": source["id"], "version_id": version["id"], "text": version.get("text"), "shots": version.get("shots")})
        elif port == "video":
            values.append({"port": port, "node_id": source["id"], "task_id": source.get("task_id"), "version_id": source.get("version_id"), "deliverable_id": source.get("deliverable_id"), "shot_index": source.get("shot_index")})
        else:
            raise ValueError("视频节点输入端口无效")
    return values


def project_store_source(project, source_id):
    source = next((item for item in project.get("sources", []) if item["id"] == source_id), None)
    if not source:
        raise ValueError("图片素材不存在")
    return source, None


def quote(store, project, body):
    nodes, edges = _nodes(body)
    target_id = body.get("node_id")
    target = nodes.get(target_id)
    if not target or target.get("kind") not in (SCRIPT_KIND, SHOT_KIND, COMPOSE_KIND):
        raise ValueError("目标视频节点无效")
    kind = target["kind"]
    inputs = _inputs(project, nodes, edges, target_id)
    if kind == SCRIPT_KIND:
        if not any(item["port"] == "image" for item in inputs):
            raise ValueError("分镜脚本需要连接已确认商品图片")
        snapshot = {"node_id": target_id, "kind": kind, "inputs": inputs, "draft": str(target.get("draft") or "")[:4000]}
        provider = "DeepSeek"
    elif kind == SHOT_KIND:
        shot_index = int(target.get("shot_index") or 0)
        if shot_index not in range(4):
            raise ValueError("分镜编号必须是 1 至 4")
        if not any(item["port"] == "image" for item in inputs) or not any(item["port"] == "script" for item in inputs):
            raise ValueError("分镜视频需要连接商品图片和已确认分镜脚本")
        script = next(item for item in inputs if item["port"] == "script")
        if not isinstance(script.get("shots"), list) or len(script["shots"]) != 4:
            raise ValueError("分镜脚本必须包含四个分镜")
        snapshot = {"node_id": target_id, "kind": kind, "inputs": inputs, "shot_index": shot_index, "duration": 4,
                    "model": str(target.get("model") or "flova"), "ratio": str(target.get("ratio") or "9:16")}
        provider = "Flova"
    else:
        videos = [item for item in inputs if item["port"] == "video"]
        if len(videos) != 4 or sorted(item.get("shot_index") for item in videos) != [0, 1, 2, 3] or any(not item.get("deliverable_id") for item in videos):
            raise ValueError("合成需要四个已完成的分镜视频（1 至 4）")
        snapshot = {"node_id": target_id, "kind": kind, "inputs": inputs, "duration": 15}
        provider = "Flova"
    return {"provider": provider, "input_snapshot": snapshot, "fingerprint": fingerprint(snapshot), "estimate": None, "currency": None,
            "pricing_source": "费用仅作参考", "reliable": False}


def _task(project, snapshot, provider, request_id):
    task = {"id": ident(), "kind": snapshot["kind"], "provider": provider, "model": snapshot.get("model", provider),
            "status": "已点击", "idempotency_key": request_id, "input_snapshot": snapshot, "remote_id": None,
            "created": stamp(), "updated": stamp()}
    project.setdefault("tasks", []).append(task)
    return task


def _run_flova_worker(store, project_id, task_id, prompt, files, compose=False):
    try:
        with store.lock:
            project = store.load(project_id)
            task = next(item for item in project["tasks"] if item["id"] == task_id)
            task.update(status="正在生成", updated=stamp())
            store.save(project)
        project_dir = store.root / "files" / project_id / "video-workflow" / task_id
        project_dir.mkdir(parents=True, exist_ok=True)
        project = store.load(project_id)
        remote_project = project.get("external", {}).get("video_flova_project_id")
        if not remote_project:
            remote_project = str(flova.create_project(f"{project['name']} 视频工作流", "画布分镜视频与合成").get("project_id") or "")
            if not remote_project:
                raise ValueError("Flova 未返回项目 ID")
            with store.lock:
                project = store.load(project_id)
                project.setdefault("external", {})["video_flova_project_id"] = remote_project
                store.save(project)
        envelopes = []
        for index, (name, data) in enumerate(files):
            path = project_dir / f"{index}-{name}"
            path.write_bytes(data)
            envelope = project_dir / f"upload-{index}.json"
            flova_flow._upload(path, envelope)
            envelopes.extend(["--file-from", str(envelope)])
        result = flova.invoke("run", remote_project, "--content", prompt, *envelopes)
        with store.lock:
            project = store.load(project_id)
            task = next(item for item in project["tasks"] if item["id"] == task_id)
            task["remote_id"] = result.get("stream_chat_id") or result.get("task_id")
            task["result_summary"] = {key: result.get(key) for key in ("status", "terminal", "stream_chat_id", "project_url")}
            task["status"] = "待审核" if result.get("terminal") and str(result.get("status", "")).lower() in ("success", "completed") else "失败" if result.get("terminal") else "待核对"
            task["updated"] = stamp()
            store.save(project)
        if task["status"] == "待审核":
            project = store.load(project_id)
            items = flova_flow.resources(project).get("items", [])
            videos = [item for item in items if item.get("media_type") == "video"]
            if videos:
                deliverable = flova_flow.pull_video_resource(store, project, videos[-1]["resource_id"])
                with store.lock:
                    project = store.load(project_id)
                    task = next(item for item in project["tasks"] if item["id"] == task_id)
                    task["deliverable_id"] = deliverable["id"]
                    task["updated"] = stamp()
                    store.save(project)
    except Exception as exc:
        with store.lock:
            project = store.load(project_id)
            task = next(item for item in project["tasks"] if item["id"] == task_id)
            task.update(status="失败", error=str(exc)[:300], updated=stamp())
            store.save(project)


def run(store, project, body):
    existing = _existing_request(project, body.get("request_id"))
    if existing:
        return existing
    offer = quote(store, project, body)
    if not isinstance(body.get("request_id"), str) or len(body["request_id"]) < 8:
        raise ValueError("请求标识无效")
    snapshot = offer["input_snapshot"]
    if snapshot["kind"] == SCRIPT_KIND:
        key = provider_key("DEEPSEEK_API_KEY")
        if not key:
            raise ValueError("缺少 DeepSeek API Key")
        content = [{"type": "text", "text": "根据商品图片和商品信息生成四段分镜脚本。仅输出 JSON：{\"shots\":[{\"index\":1,\"duration\":4,\"visual\":\"\",\"action\":\"\",\"camera\":\"\",\"scene\":\"\",\"caption\":\"\",\"constraints\":\"\"}]}。每段 duration 固定为 4。不可见参数写待核实，不得编造。\n" + snapshot["draft"]}]
        for item in snapshot["inputs"]:
            if item["port"] == "image":
                asset = next(source for source in project["sources"] if source["id"] == item["source_id"])
                raw = (store.root / "files" / project["id"] / asset["id"]).read_bytes()
                content.append({"type": "image_url", "image_url": {"url": f"data:{asset['mime']};base64,{base64.b64encode(raw).decode()}", "detail": "original"}})
            elif item["port"] == "product_details":
                content[0]["text"] += "\n" + str(item.get("text") or "")
        task = _task(project, snapshot, "DeepSeek", body["request_id"])
        result = deepseek_complete(key, [{"role": "user", "content": content}], json_mode=True)
        parsed = json.loads(result["text"])
        shots = parsed.get("shots")
        if not isinstance(shots, list) or len(shots) != 4:
            raise ValueError("DeepSeek 未返回完整四段分镜")
        task.update(status="待审核", remote_id=result.get("id"), candidate_text=result["text"], shots=shots, result={"usage": result.get("usage", {}), "model": result.get("model")}, updated=stamp())
        store.save(project)
        return task
    task = _task(project, snapshot, offer["provider"], body["request_id"])
    store.save(project)
    if snapshot["kind"] == SHOT_KIND:
        image = next(item for item in snapshot["inputs"] if item["port"] == "image")
        asset = next(source for source in project["sources"] if source["id"] == image["source_id"])
        raw = (store.root / "files" / project["id"] / asset["id"]).read_bytes()
        script = next(item for item in snapshot["inputs"] if item["port"] == "script")["shots"][snapshot["shot_index"]]
        files = [(asset["name"], raw), ("shot-script.json", json.dumps(script, ensure_ascii=False).encode())]
        prompt = f"只制作第 {snapshot['shot_index'] + 1} 个商品分镜，目标时长 4 秒，画幅 {snapshot['ratio']}。严格依据已上传商品图和分镜脚本，不生成其他镜头。分镜脚本：{json.dumps(script, ensure_ascii=False)}"
    else:
        files, prompt = [], "将上传的四个已确认 4 秒商品分镜按编号 1 至 4 合成为约 15 秒广告视频，保持顺序和商品外观一致。"
        for index, item in enumerate(snapshot["inputs"]):
            deliverable = next((d for d in project.get("deliverables", []) if d["id"] == item["deliverable_id"]), None)
            if not deliverable:
                raise ValueError("合成输入视频文件不存在")
            files.append((f"shot-{index + 1}.mp4", (store.root / "deliverables" / project["id"] / f"{deliverable['id']}.mp4").read_bytes()))
    threading.Thread(target=_run_flova_worker, args=(store, project["id"], task["id"], prompt, files), daemon=True).start()
    return task


def confirm(store, project, body):
    kind = body.get("kind")
    if kind != SCRIPT_KIND:
        raise ValueError("当前只能确认分镜脚本")
    shots = body.get("shots")
    if not isinstance(shots, list) or len(shots) != 4 or any(not isinstance(item, dict) or item.get("duration") != 4 for item in shots):
        raise ValueError("分镜脚本必须包含四段，每段 4 秒")
    version = {"id": ident(), "node_id": body.get("node_id"), "kind": kind, "text": json.dumps({"shots": shots}, ensure_ascii=False), "shots": shots, "created": stamp()}
    project.setdefault("workflow_versions", []).append(version)
    store.save(project)
    return version
