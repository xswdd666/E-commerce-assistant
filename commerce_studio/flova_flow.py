"""One Flova project per local product, using the released aggregated CLI."""

from __future__ import annotations

import json
import hashlib
import subprocess
import tempfile
import threading
from pathlib import Path
from urllib.parse import urlparse
from urllib.request import Request, urlopen

from app import flova
from .core import fingerprint, ident, stamp


ACTIVE = set()
ACTIVE_LOCK = threading.Lock()
MEDIA_TYPES = {"video/mp4": ".mp4", "video/webm": ".webm", "video/quicktime": ".mov",
               "audio/mpeg": ".mp3", "audio/mp4": ".m4a", "audio/wav": ".wav", "audio/ogg": ".ogg", "audio/webm": ".webm"}
MAX_MEDIA_BYTES = 100 * 1024 * 1024


def store_canvas_media(store, project, node_id, kind, mime, stream, length):
    if not isinstance(node_id, str) or not 1 <= len(node_id) <= 128 or any(c not in "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_-" for c in node_id):
        raise ValueError("画布媒体节点标识无效")
    if kind not in ("video", "audio") or mime not in MEDIA_TYPES or not mime.startswith(kind + "/"):
        raise ValueError("画布媒体格式不受支持")
    if not 0 < length <= MAX_MEDIA_BYTES:
        raise ValueError("画布参考媒体为空或超过 100 MB")
    directory = store.root / "files" / project["id"]
    directory.mkdir(parents=True, exist_ok=True)
    temporary = directory / f"{ident()}.partial"
    digest = hashlib.sha256()
    try:
        with temporary.open("wb") as output:
            remaining = length
            while remaining:
                chunk = stream.read(min(1024 * 1024, remaining))
                if not chunk:
                    raise ValueError("画布参考媒体上传未完成")
                output.write(chunk)
                digest.update(chunk)
                remaining -= len(chunk)
        checksum = digest.hexdigest()
        existing = next((source for source in project["sources"] if source.get("origin") == "Canvas media"
                         and source.get("canvas_node_id") == node_id and source["mime"] == mime and source["sha256"] == checksum), None)
        if existing:
            return existing
        stored_bytes = sum(source.get("bytes", 0) for source in project["sources"]) + sum(item.get("bytes", 0) for item in project.get("deliverables", []))
        if stored_bytes + length > 240 * 1024 * 1024:
            raise ValueError("项目文件接近 250 MB 备份上限，请改用较小的参考媒体")
        source_id = ident()
        target = directory / source_id
        temporary.replace(target)
        source = {"id": source_id, "name": f"canvas-{kind}-{node_id}{MEDIA_TYPES[mime]}", "mime": mime,
                  "sha256": checksum, "bytes": length, "created": stamp(), "origin": "Canvas media",
                  "canvas_node_id": node_id, "parse_status": "媒体参考"}
        project["sources"].append(source)
        try:
            store.save(project)
        except Exception:
            project["sources"].remove(source)
            target.unlink(missing_ok=True)
            raise
        return source
    finally:
        temporary.unlink(missing_ok=True)


def _action_details(action):
    details = {key: action.get(key) for key in ("action_id", "type", "message", "resume_message_id", "payload", "action_url")}
    details["blocking"] = action.get("blocking", True)
    return details


def _action_signature(action):
    details = _action_details(action)
    details["options"] = [{"id": option.get("id"), "effect": option.get("effect")} for option in action.get("options") or [] if isinstance(option, dict)]
    return fingerprint(details)


def _apply_run_result(task, result):
    task["remote_id"] = result.get("stream_chat_id") or task.get("remote_id")
    task["result_summary"] = {key: result.get(key) for key in ("status", "terminal", "stream_chat_id", "project_url")}
    pending = result.get("pending_actions") or []
    task["pending_actions"] = [{**_action_details(action),
                                "options": [{"id": option.get("id"), "effect": option.get("effect"), "label": option.get("label")}
                                            for option in action.get("options") or [] if isinstance(option, dict)]}
                               for action in pending if isinstance(action, dict)]
    if len(task["pending_actions"]) != len(pending):
        task["pending_actions"].append({"type": "manual-check", "message": "Flova 确认事项格式异常，请在 Flova 核对", "blocking": True})
    status = str(result.get("status") or "").lower()
    task["status"] = ("待用户确认" if any(a["blocking"] for a in task["pending_actions"])
                      else "待审核" if result.get("terminal") and status in ("success", "completed")
                      else "失败" if result.get("terminal") and status in ("failed", "error")
                      else "待核对")
    task["updated"] = stamp()
    return task


def _remember_project_url(project, result):
    url = result.get("project_url")
    if not isinstance(url, str):
        return
    try:
        parsed = urlparse(url)
    except ValueError:
        return
    if parsed.scheme == "https" and parsed.hostname:
        project["external"]["flova_project_url"] = url


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


def _canvas_input(project, body):
    if body is None:
        return None
    if not isinstance(body, dict) or not all(isinstance(body.get(key), str) and body[key] for key in ("canvas_project_id", "config_node_id")):
        raise ValueError("画布视频配置节点无效")
    edges = body.get("connection_ids")
    prompt = body.get("prompt")
    if not isinstance(edges, list) or not edges or len(edges) > 30 or any(not isinstance(edge, str) or not edge for edge in edges) or len(set(edges)) != len(edges):
        raise ValueError("画布视频输入需要有效连线")
    if not isinstance(prompt, str) or not prompt.strip() or len(prompt) > 12000:
        raise ValueError("画布视频提示词无效")
    references = body.get("reference_images")
    if not isinstance(references, list) or not 1 <= len(references) <= 3:
        raise ValueError("画布视频需要一至三张已确认母版参考图")
    master = project["master_versions"][-1]
    sources = {source["id"]: source for source in project["sources"] if source["id"] in master["asset_ids"]}
    selected = []
    for reference in references:
        if not isinstance(reference, dict) or not isinstance(reference.get("node_id"), str) or not reference["node_id"]:
            raise ValueError("画布参考图节点无效")
        digest = reference.get("sha256")
        if not isinstance(digest, str) or len(digest) != 64 or any(character not in "0123456789abcdef" for character in digest):
            raise ValueError("画布参考图摘要无效")
        source = next((item for item in sources.values() if item["sha256"] == digest), None)
        if not source:
            raise ValueError("画布视频参考图须是当前已确认母版的原图")
        selected.append({"node_id": reference["node_id"], "source_id": source["id"], "sha256": digest, "view": source["view_label"]})
    if len({item["node_id"] for item in selected}) != len(selected):
        raise ValueError("画布参考图节点重复")
    media = body.get("reference_media", [])
    if not isinstance(media, list) or len(media) > 6:
        raise ValueError("画布最多接受六份音视频参考")
    media_sources = {source["id"]: source for source in project["sources"] if source.get("origin") == "Canvas media"}
    selected_media = []
    for reference in media:
        if not isinstance(reference, dict) or not all(isinstance(reference.get(key), str) for key in ("node_id", "source_id", "sha256", "kind")):
            raise ValueError("画布音视频参考无效")
        source = media_sources.get(reference.get("source_id"))
        if not source or source.get("canvas_node_id") != reference.get("node_id") or source.get("sha256") != reference.get("sha256") or not source["mime"].startswith(str(reference.get("kind")) + "/"):
            raise ValueError("画布音视频参考已变化，请重新上传并查看输入")
        selected_media.append({"node_id": reference["node_id"], "source_id": source["id"], "sha256": source["sha256"],
                               "kind": reference["kind"], "mime": source["mime"]})
    if len({item["node_id"] for item in selected_media}) != len(selected_media):
        raise ValueError("画布音视频参考节点重复")
    return {"canvas_project_id": body["canvas_project_id"], "config_node_id": body["config_node_id"],
            "connection_ids": edges, "prompt": prompt.strip(), "reference_images": selected, "reference_media": selected_media}


def quote(project, canvas_context=None):
    project_id = project["external"]["flova_project_id"]
    storyboard_id = project["storyboard_approval"]
    if not project_id or not storyboard_id or not project["master_versions"] or not project["brief_versions"]:
        raise ValueError("请先关联 Flova 项目并批准完整分镜")
    storyboard = next((s for s in project["storyboard_versions"] if s["id"] == storyboard_id), None)
    script = next((s for s in project["script_versions"] if s["id"] == project["script_approval"]), None)
    master = project["master_versions"][-1]
    brief = project["brief_versions"][-1]
    if not storyboard or not script or storyboard["script_id"] != script["id"] or storyboard["master_id"] != master["id"] or script["brief_id"] != brief["id"] or script["master_id"] != master["id"]:
        raise ValueError("上游版本已变化，请重新审核分镜")
    details = {d["id"]: d for d in master.get("inferred_details", []) if isinstance(d, dict)}
    if any(details.get(detail_id, {}).get("status") != "已核实" for shot in storyboard["shots"] for detail_id in shot.get("detail_ids", [])):
        raise ValueError("分镜含未核实的推断细节，请重新审核")
    sources = {s["id"]: s for s in project["sources"]}
    canvas_input = _canvas_input(project, canvas_context)
    selected_ids = {item["source_id"] for item in canvas_input["reference_images"]} if canvas_input else set(master["asset_ids"])
    if canvas_input and not {shot["reference_asset_id"] for shot in storyboard["shots"]}.issubset(selected_ids):
        raise ValueError("画布连线参考图缺少已批准分镜使用的母版视角")
    assets = [{"id": asset_id, "sha256": sources[asset_id]["sha256"], "view": sources[asset_id]["view_label"]} for asset_id in master["asset_ids"] if asset_id in selected_ids]
    snapshot = {"flova_project_id": project_id, "brief_id": brief["id"], "master_id": master["id"],
                "script_id": script["id"], "storyboard_id": storyboard_id, "assets": assets,
                "shot_count": len(storyboard["shots"]), "duration": sum(s["duration"] for s in storyboard["shots"])}
    if canvas_input:
        snapshot["canvas_input"] = canvas_input
    return {"provider": "Flova", "input_snapshot": snapshot, "fingerprint": fingerprint(snapshot),
            "estimate": None, "currency": None, "pricing_source": "Flova CLI 未提供可靠的单镜费用预测", "reliable": False,
            "requires_explicit_run": True}


def shot_quote(project, shot_id):
    offer = quote(project)
    storyboard = next(s for s in project["storyboard_versions"] if s["id"] == project["storyboard_approval"])
    found = next(((index, shot) for index, shot in enumerate(storyboard["shots"]) if shot["id"] == shot_id), None)
    if not found:
        raise ValueError("镜头不在当前已批准分镜中")
    index, shot = found
    if any(project["shot_approvals"].get(s["id"], {}).get("storyboard_id") != storyboard["id"] for s in storyboard["shots"][:index]):
        raise ValueError("请先完成并审核前面的镜头")
    if shot_id in project["shot_approvals"]:
        raise ValueError("此镜头已批准；如需修改请先保存新的分镜版本")
    snapshot = dict(offer["input_snapshot"])
    snapshot.update(shot_id=shot_id, shot_index=index, shot=shot,
                    assets=[asset for asset in snapshot["assets"] if asset["id"] == shot["reference_asset_id"]])
    offer["input_snapshot"] = snapshot
    offer["fingerprint"] = fingerprint(snapshot)
    return offer


def _upload(path: Path, envelope_path: Path):
    executable = flova.executable()
    if not executable:
        raise ValueError("未找到 Flova CLI")
    command = [executable, "upload", str(path)]
    if path.stat().st_size > 20 * 1024 * 1024:
        command.extend(["--timeout", "1800"])
    completed = subprocess.run(command, capture_output=True, text=True, encoding="utf-8", errors="replace")
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
            shots = [snapshot["shot"]] if task["kind"] == "video_shot" else storyboard["shots"]
            material.write_text("已确认产品事实：\n" + "\n".join(f"{f['field']}：{f['value']}" for f in brief["facts"])
                                + "\n\n已批准脚本：\n" + script["text"] + "\n\n已批准分镜：\n"
                                + "\n".join(f"镜头 {(snapshot['shot_index'] if task['kind'] == 'video_shot' else i)+1}（{shot['duration']} 秒）：{shot['visual']}；字幕：{shot['caption']}；已核实细节：{', '.join(next(d['text'] for d in master.get('inferred_details', []) if isinstance(d, dict) and d['id'] == detail_id) for detail_id in shot.get('detail_ids', [])) or '无'}" for i, shot in enumerate(shots))
                                + "\n\n母版仍待核实的结构不得用于特写、规格或卖点宣称。"
                                + ("\n\n画布视频配置节点输入：\n" + snapshot["canvas_input"]["prompt"]
                                   + "\n画布连线选定的母版视角：" + "、".join(item["view"] for item in snapshot["canvas_input"]["reference_images"])
                                   + "\n画布连线音视频参考：" + "、".join(item["kind"] + " " + item["node_id"] for item in snapshot["canvas_input"]["reference_media"])
                                   if snapshot.get("canvas_input") else ""), encoding="utf-8")
            files = [material]
            for asset in snapshot["assets"]:
                source, raw = store.source_bytes(project, asset["id"])
                suffix = {"image/png": ".png", "image/jpeg": ".jpg", "image/webp": ".webp"}.get(source["mime"])
                if not suffix:
                    raise ValueError("Flova 母版图片格式不支持")
                path = directory / f"master-{asset['view']}{suffix}"
                path.write_bytes(raw)
                files.append(path)
            for index, media in enumerate(snapshot.get("canvas_input", {}).get("reference_media", [])):
                source = next((item for item in project["sources"] if item["id"] == media["source_id"]), None)
                if not source:
                    raise ValueError("画布音视频参考文件已丢失")
                if source["sha256"] != media["sha256"] or source["mime"] != media["mime"]:
                    raise ValueError("画布音视频参考文件已变化")
                path = directory / f"canvas-{media['kind']}-{index}{MEDIA_TYPES[media['mime']]}"
                digest = hashlib.sha256()
                with (store.root / "files" / project["id"] / source["id"]).open("rb") as original, path.open("wb") as output:
                    for chunk in iter(lambda: original.read(1024 * 1024), b""):
                        digest.update(chunk)
                        output.write(chunk)
                if digest.hexdigest() != source["sha256"]:
                    raise ValueError("画布音视频参考文件校验失败")
                files.append(path)
            envelopes = []
            for index, path in enumerate(files):
                envelope_path = directory / f"upload-{index}.json"
                _upload(path, envelope_path)
                envelopes.extend(["--file-from", str(envelope_path)])
            if task["kind"] == "video_shot":
                prompt = (f"请仅制作已上传文件中的第 {snapshot['shot_index']+1} 个镜头，目标 {snapshot['shot']['duration']} 秒，9:16 竖屏。"
                          "只使用本次上传的已确认事实与母版参考，不生成或改动其他镜头，不导出成片。"
                          "静音时也要能理解；未核实结构不能作为特写或卖点。完成后呈现该镜头供人工审核。")
            else:
                prompt = ("请依据已上传的产品事实、批准脚本、完整分镜与三视图母版，在当前 Flova 项目制作约 15 秒 9:16 竖屏商品详情页视频。"
                          "上传的 approved-plan.txt 是正式脚本与分镜，master 图片是同一真实商品的已确认参考。"
                          "每个镜头遵守分镜目标时长；静音时也要能理解；不要把未获核实的结构当作卖点。"
                          "本轮先制作并呈现镜头与时间线，最终导出等待人工审核。")
                if snapshot.get("canvas_input"):
                    prompt += "请同时遵守 approved-plan.txt 中的画布视频配置节点输入；其中列出的视角是画布连线选定的参考图，canvas-video 和 canvas-audio 文件是对应节点的参考媒体。请只将其作为该轮镜头创作的参考，不直接覆盖已批准分镜。与已确认事实或批准分镜冲突时，以已批准内容为准。"
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
            _remember_project_url(project, result)
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
    request_id = body.get("request_id")
    if not isinstance(request_id, str) or len(request_id) < 8 or len(request_id) > 120:
        raise ValueError("请提供请求标识以避免重复提交")
    existing = next((t for t in project["tasks"] if t.get("idempotency_key") == request_id), None)
    if existing:
        return existing
    offer = quote(project, body.get("canvas_context"))
    if any(t.get("kind") == "video_shot" and t.get("input_snapshot", {}).get("storyboard_id") == project["storyboard_approval"] for t in project["tasks"]):
        raise ValueError("当前分镜已开始逐镜制作，请完成逐镜审核")
    if body.get("approved_fingerprint") != offer["fingerprint"]:
        raise ValueError("分镜输入已变化，请重新查看快照")
    if any(t["provider"] == "Flova" and t["status"] in ("已排队", "上传素材", "远端运行中", "待核对", "待用户确认") for t in project["tasks"]):
        raise ValueError("上次 Flova 运行尚未核对，请先恢复状态")
    with ACTIVE_LOCK:
        if project["id"] in ACTIVE:
            raise ValueError("Flova 项目已有运行中的本地进程")
        ACTIVE.add(project["id"])
    task = {"id": ident(), "kind": "video", "provider": "Flova", "status": "已排队",
            "idempotency_key": request_id, "input_snapshot": offer["input_snapshot"], "estimate": None,
            "actual": None, "currency": None, "remote_id": None, "attempts": 1, "created": stamp(), "updated": stamp()}
    if offer["input_snapshot"].get("canvas_input"):
        task["node_id"] = offer["input_snapshot"]["canvas_input"]["config_node_id"]
    project["video_approval"] = None
    project["tasks"].append(task)
    store.save(project)
    threading.Thread(target=_run_worker, args=(store, project["id"], task["id"]), daemon=True).start()
    return task


def run_shot(store, project, body):
    request_id = body.get("request_id")
    if not isinstance(request_id, str) or not 8 <= len(request_id) <= 120:
        raise ValueError("请提供请求标识以避免重复提交")
    existing = next((t for t in project["tasks"] if t.get("idempotency_key") == request_id), None)
    if existing:
        return existing
    offer = shot_quote(project, body.get("shot_id"))
    if body.get("approved_fingerprint") != offer["fingerprint"]:
        raise ValueError("镜头输入已变化，请重新查看快照")
    snapshot = offer["input_snapshot"]
    if any(t.get("kind") == "video" and t.get("input_snapshot", {}).get("storyboard_id") == snapshot["storyboard_id"] for t in project["tasks"]):
        raise ValueError("当前分镜已开始整片创作，请使用该运行结果")
    active_statuses = ("已排队", "上传素材", "远端运行中", "待核对", "待用户确认")
    if any(t.get("provider") == "Flova" and t.get("status") in active_statuses for t in project["tasks"]):
        raise ValueError("上次 Flova 运行尚未核对，请先恢复状态")
    previous = [t for t in project["tasks"] if t.get("kind") == "video_shot" and t.get("input_snapshot", {}).get("shot_id") == snapshot["shot_id"]]
    if any(t["status"] == "待审核" for t in previous):
        raise ValueError("此镜头已有待审核结果；请先在 Flova 核对")
    with ACTIVE_LOCK:
        if project["id"] in ACTIVE:
            raise ValueError("Flova 项目已有运行中的本地进程")
        ACTIVE.add(project["id"])
    task = {"id": ident(), "kind": "video_shot", "provider": "Flova", "status": "已排队",
            "idempotency_key": request_id, "input_snapshot": snapshot, "estimate": None,
            "actual": None, "currency": None, "remote_id": None, "attempts": len(previous) + 1,
            "created": stamp(), "updated": stamp()}
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
    _remember_project_url(project, result)
    store.save(project)
    return task


def _resume_worker(store, project_id, task_id, action_id, message_id, option_id):
    try:
        with store.lock:
            project = store.load(project_id)
            task = next(t for t in project["tasks"] if t["id"] == task_id)
            task.update(status="远端运行中", updated=stamp())
            store.save(project)
        result = flova.invoke("run", "resume", project["external"]["flova_project_id"],
                              "--message-id", message_id, "--action-id", action_id, "--option", option_id)
        with store.lock:
            project = store.load(project_id)
            task = next(t for t in project["tasks"] if t["id"] == task_id)
            _apply_run_result(task, result)
            _remember_project_url(project, result)
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


def resume_action(store, project, task_id, action_id, option_id):
    task = next((item for item in project["tasks"] if item.get("id") == task_id and item.get("provider") == "Flova"), None)
    if not task or task.get("status") != "待用户确认" or not task.get("remote_id"):
        raise ValueError("Flova 任务没有可继续的确认事项")
    action = next((item for item in task.get("pending_actions", []) if item.get("action_id") == action_id), None)
    if not action or not action.get("resume_message_id") or not isinstance(option_id, str):
        raise ValueError("Flova 确认事项标识不完整")
    option = next((item for item in action.get("options", []) if item.get("id") == option_id and item.get("effect") == "resume"), None)
    if not option:
        raise ValueError("请选择可继续的 Flova 选项")
    project_id = task["input_snapshot"]["flova_project_id"]
    if project_id != project["external"]["flova_project_id"]:
        raise ValueError("Flova 项目关联已变化，请先核对")
    live = flova.pending_actions(project_id)
    current = next((item for item in live.get("pending_actions", []) if isinstance(item, dict) and item.get("action_id") == action_id), None)
    if not current or _action_signature(current) != _action_signature(action):
        raise ValueError("Flova 确认事项已变化，请恢复状态后重新选择")
    with ACTIVE_LOCK:
        if project["id"] in ACTIVE:
            raise ValueError("Flova 项目已有运行中的本地进程")
        ACTIVE.add(project["id"])
    try:
        task.update(status="已排队", updated=stamp())
        store.save(project)
        threading.Thread(target=_resume_worker, args=(store, project["id"], task_id, action_id,
                                                       action["resume_message_id"], option_id), daemon=True).start()
    except Exception:
        with ACTIVE_LOCK:
            ACTIVE.discard(project["id"])
        raise
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


def pull_video_resource(store, project, resource_id, transport=None):
    if not isinstance(resource_id, str) or not resource_id or len(resource_id) > 128:
        raise ValueError("Flova 资源标识无效")
    existing = next((d for d in project["deliverables"] if d.get("kind") == "shot_video" and d.get("resource_id") == resource_id), None)
    if existing:
        return existing
    available = next((item for item in resources(project)["items"] if item["resource_id"] == resource_id and item["media_type"] == "video"), None)
    if not available:
        raise ValueError("Flova 项目中没有这个视频资源")
    details = flova.invoke("resource", "info", project["external"]["flova_project_id"], resource_id)

    def urls(value):
        if isinstance(value, dict):
            for key in ("resource_url", "download_url", "url"):
                if isinstance(value.get(key), str):
                    yield value[key]
            for nested in value.values():
                if isinstance(nested, (dict, list)):
                    yield from urls(nested)
        elif isinstance(value, list):
            for nested in value:
                yield from urls(nested)

    url = next((item for item in urls(details) if urlparse(item).scheme == "https" and urlparse(item).hostname), None)
    if not url:
        raise ValueError("Flova 视频资源尚无可下载地址，请稍后刷新资源")
    directory = store.root / "deliverables" / project["id"]
    directory.mkdir(parents=True, exist_ok=True)
    deliverable_id = ident()
    target = directory / f"{deliverable_id}.mp4"
    temporary = target.with_suffix(".part")
    digest = hashlib.sha256()
    size = 0
    try:
        with (transport or urlopen)(Request(url, headers={"User-Agent": "commerce-studio"}), timeout=180) as response, temporary.open("wb") as output:
            while chunk := response.read(4 * 1024 * 1024):
                size += len(chunk)
                if size > 2 * 1024 * 1024 * 1024:
                    raise ValueError("Flova 镜头超过本地支持的 2 GB")
                output.write(chunk)
                digest.update(chunk)
        if not size:
            raise ValueError("Flova 镜头下载为空")
        temporary.replace(target)
    except ValueError:
        raise
    except Exception as exc:
        raise ValueError("Flova 镜头下载失败，请在项目中核对该资源") from exc
    finally:
        temporary.unlink(missing_ok=True)
    deliverable = {"id": deliverable_id, "kind": "shot_video", "resource_id": resource_id,
                   "name": str(available.get("name") or f"flova-shot-{resource_id}.mp4")[:200],
                   "bytes": size, "sha256": digest.hexdigest(), "created": stamp()}
    with store.lock:
        latest = store.load(project["id"])
        previous = next((d for d in latest["deliverables"] if d.get("kind") == "shot_video" and d.get("resource_id") == resource_id), None)
        if previous:
            target.unlink(missing_ok=True)
            return previous
        latest["deliverables"].append(deliverable)
        store.save(latest)
        project["deliverables"] = latest["deliverables"]
    return deliverable


def approve_video(store, project, task_id):
    task = next((t for t in project["tasks"] if t["id"] == task_id and t.get("kind") == "video" and t.get("provider") == "Flova"), None)
    if not task or task["status"] != "待审核" or not task.get("remote_id"):
        raise ValueError("请先等待 Flova 本轮成功完成并人工核对镜头")
    if any(action.get("blocking") for action in task.get("pending_actions") or []):
        raise ValueError("Flova 尚有待用户确认的操作")
    project["video_approval"] = {"task_id": task_id, "stream_chat_id": task["remote_id"], "approved_at": stamp(), "reviewer": "local"}
    store.save(project)
    return project["video_approval"]


def approve_shot(store, project, task_id):
    task = next((t for t in project["tasks"] if t["id"] == task_id and t.get("kind") == "video_shot"), None)
    if not task or task["status"] != "待审核" or not task.get("remote_id"):
        raise ValueError("请先等待当前镜头完成并在 Flova 人工核对")
    if any(action.get("blocking") for action in task.get("pending_actions") or []):
        raise ValueError("Flova 尚有待用户确认的操作")
    offer = shot_quote(project, task["input_snapshot"]["shot_id"])
    if offer["fingerprint"] != fingerprint(task["input_snapshot"]):
        raise ValueError("镜头上游版本已变化，请重新审核")
    shot_id = task["input_snapshot"]["shot_id"]
    project["shot_approvals"][shot_id] = {"task_id": task_id, "stream_chat_id": task["remote_id"],
                                          "storyboard_id": project["storyboard_approval"], "approved_at": stamp()}
    store.save(project)
    return project["shot_approvals"][shot_id]


def approve_shot_sequence(store, project):
    offer = quote(project)
    storyboard = next(s for s in project["storyboard_versions"] if s["id"] == project["storyboard_approval"])
    approvals = project["shot_approvals"]
    if any(shot["id"] not in approvals or approvals[shot["id"]]["storyboard_id"] != storyboard["id"] for shot in storyboard["shots"]):
        raise ValueError("请先逐镜完成并审核全部镜头")
    task = None
    for index, shot in enumerate(storyboard["shots"]):
        approval = approvals[shot["id"]]
        task = next((t for t in project["tasks"] if t["id"] == approval["task_id"] and t.get("kind") == "video_shot"), None)
        if not task or task["status"] != "待审核" or task.get("remote_id") != approval["stream_chat_id"]:
            raise ValueError("逐镜审核任务已变化，请重新核对")
        inputs = task.get("input_snapshot") or {}
        if (inputs.get("shot_id") != shot["id"] or inputs.get("shot_index") != index or inputs.get("shot") != shot or
                any(inputs.get(key) != offer["input_snapshot"].get(key) for key in ("flova_project_id", "brief_id", "master_id", "script_id", "storyboard_id"))):
            raise ValueError("逐镜输入已变化，请重新审核")
    project["video_approval"] = {"task_id": task["id"], "stream_chat_id": task["remote_id"],
                                 "storyboard_id": storyboard["id"], "mode": "shots", "approved_at": stamp(), "reviewer": "local"}
    store.save(project)
    return project["video_approval"]
