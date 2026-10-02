"""Small, allowlisted adapter for the released Flova CLI."""

import json
import os
import shutil
import subprocess
from pathlib import Path


class FlovaError(ValueError):
    pass


def executable():
    found = shutil.which("flova")
    if found:
        return found
    local = Path(os.environ.get("LOCALAPPDATA", "")) / "Programs" / "flova" / "flova.exe"
    return str(local) if local.is_file() else None


def invoke(*args):
    exe = executable()
    if not exe:
        raise FlovaError("未找到 Flova CLI，请先安装并登录")
    # The released CLI owns polling for run and export. Never use --no-wait.
    completed = subprocess.run([exe, *args], capture_output=True, text=True, encoding="utf-8", errors="replace")
    try:
        envelope = json.loads(completed.stdout)
    except ValueError as exc:
        raise FlovaError("Flova CLI 未返回有效 JSON，请检查登录和网络状态") from exc
    if completed.returncode or envelope.get("code") not in (0, "0"):
        raise FlovaError(str(envelope.get("message") or envelope.get("code") or "Flova 调用失败"))
    return envelope.get("data") or {}


def auth_status():
    data = invoke("auth", "status")
    return bool(data.get("token_stored"))


def create_project(name, description):
    return invoke("project", "create", "--name", name, "--description", description)


def project_info(project_id):
    return invoke("project", "info", project_id)


def run(project_id, prompt):
    return invoke("run", project_id, "--content", prompt)


def recover(project_id):
    return invoke("run", "current", project_id)


def run_result(project_id, stream_chat_id):
    return invoke("run", "result", project_id, "--stream-chat-id", stream_chat_id)


def pending_actions(project_id):
    return invoke("chat", "pending-actions", project_id)


def readiness(project_id):
    return invoke("export", "readiness", project_id)


def export_video(project_id):
    return invoke("export", "video", project_id)
