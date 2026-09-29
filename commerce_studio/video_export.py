"""Flova final export after explicit video review, with local MP4 retention."""

from __future__ import annotations

import hashlib
import threading
from pathlib import Path
from urllib.parse import urlparse
from urllib.request import Request, urlopen

from app import flova
from .core import fingerprint, ident, stamp


ACTIVE = set()
LOCK = threading.Lock()
MAX_VIDEO_BYTES = 2 * 1024 * 1024 * 1024


def quote(project):
    approval = project.get("video_approval")
    if not approval:
        raise ValueError("请先在 Flova 审核本轮视频并批准导出")
    task = next((t for t in project["tasks"] if t["id"] == approval["task_id"]), None)
    if not task or task.get("status") != "待审核" or task.get("remote_id") != approval["stream_chat_id"]:
        raise ValueError("Flova 视频审核版本已变化")
    inputs = task.get("input_snapshot") or {}
    if (not project["brief_versions"] or not project["master_versions"] or
            inputs.get("brief_id") != project["brief_versions"][-1]["id"] or
            inputs.get("master_id") != project["master_versions"][-1]["id"] or
            inputs.get("storyboard_id") != project["storyboard_approval"] or
            inputs.get("flova_project_id") != project["external"]["flova_project_id"]):
        raise ValueError("Flova 视频依据的产品或分镜版本已变化，请重新审核")
    if any(t.get("kind") == "video_export" and t.get("status") in ("已排队", "远端运行中", "待核对", "待下载") for t in project["tasks"]):
        raise ValueError("上次 Flova 导出尚未核对，请先恢复")
    project_id = project["external"]["flova_project_id"]
    readiness = flova.readiness(project_id)
    if not readiness.get("can_export"):
        raise ValueError("Flova 当前时间线尚不满足导出条件")
    snapshot = {"flova_project_id": project_id, "approved_run_task_id": task["id"],
                "stream_chat_id": approval["stream_chat_id"], "approved_at": approval["approved_at"]}
    return {"provider": "Flova", "input_snapshot": snapshot, "fingerprint": fingerprint(snapshot),
            "estimate": None, "currency": None, "pricing_source": "Flova CLI 未提供可靠导出费用预测", "reliable": False,
            "requires_explicit_run": True, "readiness": {"can_export": True}}


def _download_video(store, project, task, export_url, transport=None):
    if task.get("deliverable_id"):
        return next(d for d in project["deliverables"] if d["id"] == task["deliverable_id"])
    parsed = urlparse(export_url)
    if parsed.scheme != "https" or not parsed.hostname:
        raise ValueError("Flova 导出地址无效")
    directory = store.root / "deliverables" / project["id"]
    directory.mkdir(parents=True, exist_ok=True)
    deliverable_id = task.get("deliverable_id") or ident()
    target = directory / f"{deliverable_id}.mp4"
    temporary = target.with_suffix(".part")
    digest = hashlib.sha256()
    size = 0
    with (transport or urlopen)(Request(export_url, headers={"User-Agent": "commerce-studio"}), timeout=180) as response, temporary.open("wb") as output:
        while chunk := response.read(4 * 1024 * 1024):
            size += len(chunk)
            if size > MAX_VIDEO_BYTES:
                raise ValueError("Flova 成片超过本地支持的 2 GB")
            output.write(chunk)
            digest.update(chunk)
    if not size:
        raise ValueError("Flova 成片下载为空")
    temporary.replace(target)
    deliverable = {"id": deliverable_id, "kind": "video", "task_id": task["id"], "name": "flova-final.mp4",
                   "bytes": size, "sha256": digest.hexdigest(), "created": stamp()}
    with store.lock:
        latest = store.load(project["id"])
        latest_task = next(t for t in latest["tasks"] if t["id"] == task["id"])
        latest["deliverables"].append(deliverable)
        latest_task["deliverable_id"] = deliverable_id
        latest_task["status"] = "待审核"
        latest_task["updated"] = stamp()
        store.save(latest)
    return deliverable


def _finish(store, project_id, task_id, result):
    with store.lock:
        project = store.load(project_id)
        task = next(t for t in project["tasks"] if t["id"] == task_id)
        remote_id = result.get("task_id")
        status = str(result.get("status") or "").lower()
        task["remote_id"] = remote_id or task.get("remote_id")
        task["result_summary"] = {key: result.get(key) for key in ("status", "terminal", "task_id", "created_at", "updated_at")}
        if result.get("terminal") and status in ("failed", "error"):
            task["status"] = "失败"
        elif result.get("terminal") and status in ("success", "completed") and task["remote_id"] and result.get("export_url"):
            task["status"] = "待下载"
        else:
            task["status"] = "待核对"
        task["updated"] = stamp()
        store.save(project)
    if task["status"] == "待下载":
        try:
            return _download_video(store, project, task, result["export_url"])
        except Exception as exc:
            with store.lock:
                latest = store.load(project_id)
                latest_task = next(t for t in latest["tasks"] if t["id"] == task_id)
                latest_task["error"] = str(exc)[:300]
                store.save(latest)
    return task


def _worker(store, project_id, task_id):
    try:
        with store.lock:
            project = store.load(project_id)
            task = next(t for t in project["tasks"] if t["id"] == task_id)
            task["status"] = "远端运行中"
            task["remote_started"] = stamp()
            store.save(project)
            remote_project_id = task["input_snapshot"]["flova_project_id"]
        result = flova.export_video(remote_project_id)
        _finish(store, project_id, task_id, result)
    except Exception as exc:
        with store.lock:
            project = store.load(project_id)
            task = next(t for t in project["tasks"] if t["id"] == task_id)
            task.update(status="待核对", error=str(exc)[:300], updated=stamp())
            store.save(project)
    finally:
        with LOCK:
            ACTIVE.discard(project_id)


def run(store, project, body):
    request_id = body.get("request_id")
    if not isinstance(request_id, str) or not 8 <= len(request_id) <= 120:
        raise ValueError("请提供请求标识以避免重复导出")
    existing = next((t for t in project["tasks"] if t.get("idempotency_key") == request_id), None)
    if existing:
        return existing
    offer = quote(project)
    if body.get("approved_fingerprint") != offer["fingerprint"]:
        raise ValueError("视频导出输入已变化，请重新查看快照")
    with LOCK:
        if project["id"] in ACTIVE:
            raise ValueError("Flova 项目已有运行中的导出进程")
        ACTIVE.add(project["id"])
    task = {"id": ident(), "kind": "video_export", "provider": "Flova", "status": "已排队",
            "idempotency_key": request_id, "input_snapshot": offer["input_snapshot"], "estimate": None,
            "actual": None, "currency": None, "remote_id": None, "attempts": 1, "created": stamp(), "updated": stamp()}
    project["tasks"].append(task)
    store.save(project)
    threading.Thread(target=_worker, args=(store, project["id"], task["id"]), daemon=True).start()
    return task


def recover(store, project, task_id):
    task = next((t for t in project["tasks"] if t["id"] == task_id and t.get("kind") == "video_export"), None)
    if not task:
        raise ValueError("Flova 导出任务不存在")
    if task.get("deliverable_id"):
        return task
    with LOCK:
        if project["id"] in ACTIVE:
            return task
    remote_project_id = task["input_snapshot"]["flova_project_id"]
    if task.get("remote_id"):
        result = flova.invoke("export", "status", remote_project_id, "--task-id", task["remote_id"])
        return _finish(store, project["id"], task_id, result)
    if not task.get("remote_started"):
        task["status"] = "待核对"
        task["error"] = "本地进程在远端导出提交前中断；请核对 Flova 项目"
        store.save(project)
        return task
    candidate = flova.invoke("export", "current", remote_project_id)
    task["recovery_candidate"] = {key: candidate.get(key) for key in ("task_id", "status", "terminal", "created_at", "updated_at")}
    task["status"] = "待核对"
    task["error"] = "Flova 返回当前导出任务，但尚无法证明它属于本次提交；请人工核对任务 ID 和时间"
    store.save(project)
    return task


def deliverable_bytes(store, project, deliverable_id):
    item = next((d for d in project.get("deliverables", []) if d["id"] == deliverable_id), None)
    if not item or item.get("kind") not in ("video", "shot_video", "finished_video"):
        raise ValueError("本地成片不存在")
    raw = (store.root / "deliverables" / project["id"] / f"{deliverable_id}.mp4").read_bytes()
    if hashlib.sha256(raw).hexdigest() != item["sha256"]:
        raise ValueError("本地成片校验失败")
    return raw
