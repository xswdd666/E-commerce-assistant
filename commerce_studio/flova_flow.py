"""One Flova project per local product, using the released aggregated CLI."""

from __future__ import annotations

import json
import subprocess
import tempfile
import threading
from pathlib import Path

from app import flova
from .core import fingerprint, ident, stamp


ACTIVE = set()
ACTIVE_LOCK = threading.Lock()


def _apply_run_result(task, result):
    task["remote_id"] = result.get("stream_chat_id") or task.get("remote_id")
    task["result_summary"] = {key: result.get(key) for key in ("status", "terminal", "stream_chat_id", "project_url")}
    task["pending_actions"] = [{"type": a.get("type"), "message": a.get("message"), "blocking": a.get("blocking", True)} for a in result.get("pending_actions") or []]
    status = str(result.get("status") or "").lower()
    task["status"] = ("待用户确认" if any(a["blocking"] for a in task["pending_actions"])
                      else "待审核" if result.get("terminal") and status in ("success", "completed")
                      else "失败" if result.get("terminal") and status in ("failed", "error")
                      else "待核对")
    task["updated"] = stamp()
    return task


def create_project(store, project):
    if project["external"]["flova_project_id"]:
        raise ValueError("已关联 Flova 项目")
    data = flova.create_project(project["name"], "广告电商创作工作台项目")
    project_id = str(data.get("project_id") or data.get("id") or "")
    if not project_id:
        raise ValueError("Flova 未返回项目 ID，请先在 Flova 核对")
    project["external"] = {"flova_project_id": project_id, "flova_project_url": str(data.get("project_url") or "")}
    store.save(project)
    return project["external"]


def attach_project(store, project, project_id):
    if project["external"]["flova_project_id"]:
        raise ValueError("已关联 Flova 项目")
    if not isinstance(project_id, str) or not project_id.strip():
        raise ValueError("请输入 Flova 项目标识")
    data = flova.project_info(project_id.strip())
    project["external"] = {"flova_project_id": project_id.strip(), "flova_project_url": str(data.get("project_url") or "")}
    store.save(project)
    return project["external"]


def quote(project):
    project_id = project["external"]["flova_project_id"]
    storyboard_id = project["storyboard_approval"]
    if not project_id or not storyboard_id:
        raise ValueError("请先关联 Flova 项目并批准完整分镜")
    storyboard = next((s for s in project["storyboard_versions"] if s["id"] == storyboard_id), None)
    script = next((s for s in project["script_versions"] if s["id"] == project["script_approval"]), None)
    master = project["master_versions"][-1]
    brief = project["brief_versions"][-1]
    if not storyboard or not script or storyboard["script_id"] != script["id"] or storyboard["master_id"] != master["id"] or script["brief_id"] != brief["id"]:
        raise ValueError("上游版本已变化，请重新审核分镜")
    details = {d["id"]: d for d in master.get("inferred_details", []) if isinstance(d, dict)}
    if any(details.get(detail_id, {}).get("status") != "已核实" for shot in storyboard["shots"] for detail_id in shot.get("detail_ids", [])):
        raise ValueError("分镜含未核实的推断细节，请重新审核")
    sources = {s["id"]: s for s in project["sources"]}
    assets = [{"id": asset_id, "sha256": sources[asset_id]["sha256"], "view": sources[asset_id]["view_label"]} for asset_id in master["asset_ids"]]
    snapshot = {"flova_project_id": project_id, "brief_id": brief["id"], "master_id": master["id"],
                "script_id": script["id"], "storyboard_id": storyboard_id, "assets": assets,
                "shot_count": len(storyboard["shots"]), "duration": sum(s["duration"] for s in storyboard["shots"])}
    return {"provider": "Flova", "input_snapshot": snapshot, "fingerprint": fingerprint(snapshot),
            "estimate": None, "currency": None, "pricing_source": "Flova CLI 未提供可靠的单镜费用预测", "reliable": False,
            "requires_explicit_run": True}


def _upload(path: Path, envelope_path: Path):
    executable = flova.executable()
    if not executable:
        raise ValueError("未找到 Flova CLI")
    completed = subprocess.run([executable, "upload", str(path)], capture_output=True, text=True, encoding="utf-8", errors="replace")
    try:
        envelope = json.loads(completed.stdout)
    except ValueError as exc:
        raise ValueError("Flova 上传未返回有效 JSON") from exc
    if completed.returncode or envelope.get("code") not in (0, "0"):
        raise ValueError(str(envelope.get("message") or "Flova 上传失败")[:300])
    envelope_path.write_text(completed.stdout, encoding="utf-8")


def _run_worker(store, project_id, task_id):
    try:
        with store.lock:
            project = store.load(project_id)
            task = next(t for t in project["tasks"] if t["id"] == task_id)
            task["status"] = "上传素材"
            store.save(project)
        snapshot = task["input_snapshot"]
        brief = next(v for v in project["brief_versions"] if v["id"] == snapshot["brief_id"])
        script = next(v for v in project["script_versions"] if v["id"] == snapshot["script_id"])
        storyboard = next(v for v in project["storyboard_versions"] if v["id"] == snapshot["storyboard_id"])
        master = next(v for v in project["master_versions"] if v["id"] == snapshot["master_id"])
        with tempfile.TemporaryDirectory(prefix="commerce-flova-") as temporary:
            directory = Path(temporary)
            material = directory / "approved-plan.txt"
            material.write_text("已确认产品事实：\n" + "\n".join(f"{f['field']}：{f['value']}" for f in brief["facts"])
                                + "\n\n已批准脚本：\n" + script["text"] + "\n\n已批准分镜：\n"
                                + "\n".join(f"镜头 {i+1}（{shot['duration']} 秒）：{shot['visual']}；字幕：{shot['caption']}；已核实细节：{', '.join(next(d['text'] for d in master.get('inferred_details', []) if isinstance(d, dict) and d['id'] == detail_id) for detail_id in shot.get('detail_ids', [])) or '无'}" for i, shot in enumerate(storyboard["shots"]))
                                + "\n\n母版仍待核实的结构不得用于特写、规格或卖点宣称。", encoding="utf-8")
            files = [material]
            for asset in snapshot["assets"]:
                source, raw = store.source_bytes(project, asset["id"])
                suffix = {"image/png": ".png", "image/jpeg": ".jpg", "image/webp": ".webp"}.get(source["mime"])
                if not suffix:
                    raise ValueError("Flova 母版图片格式不支持")
                path = directory / f"master-{asset['view']}{suffix}"
                path.write_bytes(raw)
                files.append(path)
            envelopes = []
            for index, path in enumerate(files):
                envelope_path = directory / f"upload-{index}.json"
                _upload(path, envelope_path)
                envelopes.extend(["--file-from", str(envelope_path)])
            prompt = ("请依据已上传的产品事实、批准脚本、完整分镜与三视图母版，在当前 Flova 项目制作约 15 秒 9:16 竖屏商品详情页视频。"
                      "上传的 approved-plan.txt 是正式脚本与分镜，master 图片是同一真实商品的已确认参考。"
                      "每个镜头遵守分镜目标时长；静音时也要能理解；不要把未获核实的结构当作卖点。"
                      "本轮先制作并呈现镜头与时间线，最终导出等待人工审核。")
            with store.lock:
                project = store.load(project_id)
                task = next(t for t in project["tasks"] if t["id"] == task_id)
                task["status"] = "远端运行中"
                task["remote_started"] = stamp()
                store.save(project)
            result = flova.invoke("run", snapshot["flova_project_id"], "--content", prompt, *envelopes)
        with store.lock:
            project = store.load(project_id)
            task = next(t for t in project["tasks"] if t["id"] == task_id)
            _apply_run_result(task, result)
            store.save(project)
    except Exception as exc:
        with store.lock:
            project = store.load(project_id)
            task = next(t for t in project["tasks"] if t["id"] == task_id)
            task.update(status="待核对", error=str(exc)[:300], updated=stamp())
            store.save(project)
    finally:
        with ACTIVE_LOCK:
            ACTIVE.discard(project_id)


def run(store, project, body):
    existing = next((t for t in project["tasks"] if t["idempotency_key"] == body.get("request_id")), None)
    if existing:
        return existing
    offer = quote(project)
    if body.get("approved_fingerprint") != offer["fingerprint"]:
        raise ValueError("分镜输入已变化，请重新查看快照")
    request_id = body.get("request_id")
    if not isinstance(request_id, str) or len(request_id) < 8 or len(request_id) > 120:
        raise ValueError("请提供请求标识以避免重复提交")
    if any(t["provider"] == "Flova" and t["status"] in ("已排队", "上传素材", "远端运行中", "待核对", "待用户确认") for t in project["tasks"]):
        raise ValueError("上次 Flova 运行尚未核对，请先恢复状态")
    with ACTIVE_LOCK:
        if project["id"] in ACTIVE:
            raise ValueError("Flova 项目已有运行中的本地进程")
        ACTIVE.add(project["id"])
    task = {"id": ident(), "kind": "video", "provider": "Flova", "status": "已排队",
            "idempotency_key": request_id, "input_snapshot": offer["input_snapshot"], "estimate": None,
            "actual": None, "currency": None, "remote_id": None, "attempts": 1, "created": stamp(), "updated": stamp()}
    project["video_approval"] = None
    project["tasks"].append(task)
    store.save(project)
    threading.Thread(target=_run_worker, args=(store, project["id"], task["id"]), daemon=True).start()
    return task


def recover(store, project, task_id):
    task = next((t for t in project["tasks"] if t["id"] == task_id and t["provider"] == "Flova"), None)
    if not task:
        raise ValueError("Flova 任务不存在")
    with ACTIVE_LOCK:
        if project["id"] in ACTIVE:
            return task
    if not task.get("remote_started") and not task.get("remote_id"):
        task["status"] = "待核对"
        task["pending_actions"] = [{"type": "manual-check", "message": "本地任务在提交到 Flova 前中断。请检查 Flova 项目，再决定是否新建任务。", "blocking": True}]
        task["updated"] = stamp()
        store.save(project)
        return task
    result = flova.recover(task["input_snapshot"]["flova_project_id"])
    if task.get("remote_id") and result.get("stream_chat_id") != task["remote_id"]:
        result = flova.run_result(task["input_snapshot"]["flova_project_id"], task["remote_id"])
    elif not task.get("remote_id"):
        task["status"] = "待核对"
        task["recovery_candidate"] = {key: result.get(key) for key in ("terminal", "stream_chat_id", "project_url")}
        task["pending_actions"] = [{"type": "manual-check", "message": "Flova 返回当前运行，但尚无法证明它属于本次提交。请在 Flova 项目核对。", "blocking": True}]
        task["updated"] = stamp()
        store.save(project)
        return task
    _apply_run_result(task, result)
    store.save(project)
    return task


def resources(project):
    project_id = project["external"]["flova_project_id"]
    if not project_id:
        raise ValueError("请先关联 Flova 项目")
    result = flova.invoke("project", "resources", project_id, "--types", "image,video,audio,music")
    found = []

    def visit(value):
        if isinstance(value, list):
            for item in value:
                visit(item)
        elif isinstance(value, dict):
            media_type = str(value.get("media_type") or value.get("type") or "").lower()
            resource_id = value.get("resource_id") or value.get("id")
            if media_type in ("image", "video", "audio", "music") and resource_id:
                found.append({key: value.get(key) for key in ("resource_id", "name", "status", "media_type", "artifact_role", "created_at")})
                found[-1]["resource_id"] = str(resource_id)
                found[-1]["media_type"] = media_type
            for child in value.values():
                if isinstance(child, (dict, list)):
                    visit(child)

    visit(result)
    unique = list({item["resource_id"]: item for item in found}.values())
    return {"items": unique, "unparsed": bool(result) and not bool(unique)}


def approve_video(store, project, task_id):
    task = next((t for t in project["tasks"] if t["id"] == task_id and t.get("kind") == "video" and t.get("provider") == "Flova"), None)
    if not task or task["status"] != "待审核" or not task.get("remote_id"):
        raise ValueError("请先等待 Flova 本轮成功完成并人工核对镜头")
    if any(action.get("blocking") for action in task.get("pending_actions") or []):
        raise ValueError("Flova 尚有待用户确认的操作")
    project["video_approval"] = {"task_id": task_id, "stream_chat_id": task["remote_id"], "approved_at": stamp(), "reviewer": "local"}
    store.save(project)
    return project["video_approval"]
