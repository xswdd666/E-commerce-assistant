"""Small server-side adapter for documented SeeAny developer endpoints."""

import json
import mimetypes
import uuid
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode, urlparse
from urllib.request import Request, urlopen

BASE = "https://api.seeany.com"
HEADERS = {"User-Agent": "seeany-api", "Accept": "application/json, text/event-stream"}


class SeeAnyError(Exception):
    pass


def _request(key, path, *, method="GET", payload=None, body=None, content_type=None, timeout=120, transport=urlopen):
    if not key:
        raise SeeAnyError("请先在 .env 中设置 SEEANY_API_KEY")
    headers = {**HEADERS, "Authorization": f"Bearer {key}"}
    if payload is not None:
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        headers["Content-Type"] = "application/json"
    elif content_type:
        headers["Content-Type"] = content_type
    req = Request(BASE + path, data=body, headers=headers, method=method)
    try:
        with transport(req, timeout=timeout) as response:
            raw = response.read().decode("utf-8")
            if response.headers.get_content_type() == "text/event-stream":
                result = None
                event = ""
                for line in raw.splitlines():
                    if line.startswith("event:"):
                        event = line[6:].strip()
                    elif line.startswith("data:") and event == "result":
                        result = json.loads(line[5:].strip())
                    elif line.startswith("data:") and event == "error":
                        raise SeeAnyError(line[5:].strip()[:300])
                if result is None:
                    raise SeeAnyError("套图策划未返回 result 事件，请勿重复提交，先核对远端记录")
                return result
            result = json.loads(raw)
            if result.get("code") != 0:
                raise SeeAnyError(str(result.get("msg") or "SeeAny 请求失败")[:300])
            return result
    except HTTPError as exc:
        raise SeeAnyError(f"SeeAny HTTP {exc.code}，请核对密钥、参数和账户余额") from exc
    except URLError as exc:
        raise SeeAnyError("SeeAny 网络连接失败或超时；如已提交任务，请先核对远端状态") from exc


def upload_image(key, name, raw, *, transport=urlopen):
    mime = mimetypes.guess_type(name)[0] or "application/octet-stream"
    if not mime.startswith("image/"):
        raise SeeAnyError("SeeAny 参考素材必须是图片")
    boundary = "----seeany-" + uuid.uuid4().hex
    safe_name = name.replace('"', "_").replace("\r", "_").replace("\n", "_")
    body = (f'--{boundary}\r\nContent-Disposition: form-data; name="file"; filename="{safe_name}"\r\n'
            f"Content-Type: {mime}\r\n\r\n").encode() + raw + f"\r\n--{boundary}--\r\n".encode()
    response = _request(key, "/api/upload/image", method="POST", body=body,
                        content_type=f"multipart/form-data; boundary={boundary}", transport=transport)
    data = response.get("data") or {}
    url = data.get("url") if isinstance(data, dict) else None
    if not url:
        raise SeeAnyError("图片上传成功但未返回 URL")
    return url


def submit(key, path, payload, *, transport=urlopen):
    if path not in ("/api/ai/batchsmarttask", "/api/ai/smarttask", "/api/ai/groupplan", "/api/ai/grouptask"):
        raise SeeAnyError("未允许的 SeeAny 能力路径")
    return _request(key, path, method="POST", payload=payload, transport=transport)


def task_status(key, task_uuid, *, transport=urlopen):
    if not task_uuid or len(task_uuid) > 120:
        raise SeeAnyError("任务标识无效")
    return _request(key, "/api/developer/task/status?" + urlencode({"task_uuid": task_uuid}), transport=transport)


def result_assets(value):
    """Collect final asset URLs from documented status/callback result shapes."""
    found = []
    containers = ("data", "task", "result", "works", "results", "items", "outputs", "assets", "images")

    def walk(node, inside_assets=False):
        if isinstance(node, dict):
            url = node.get("url")
            if inside_assets and isinstance(url, str) and url.startswith(("https://", "http://")):
                found.append(url)
            for key in containers:
                if key in node:
                    walk(node[key], inside_assets or key in ("works", "results", "items", "outputs", "assets", "images"))
        elif isinstance(node, list):
            for item in node:
                walk(item, inside_assets)
    walk(value)
    return list(dict.fromkeys(found))


def download_image(url, *, transport=urlopen):
    parsed = urlparse(url)
    if parsed.scheme != "https" or not (parsed.hostname == "seeany.com" or parsed.hostname.endswith(".seeany.com")):
        raise SeeAnyError("生成资源 URL 不在 SeeAny 域名下，请手动核对")
    try:
        with transport(Request(url, headers={"User-Agent": "seeany-api"}), timeout=60) as response:
            mime = response.headers.get_content_type()
            raw = response.read(25 * 1024 * 1024 + 1)
    except (HTTPError, URLError) as exc:
        raise SeeAnyError("生成资源下载失败，可稍后重试状态同步") from exc
    if not mime.startswith("image/") or len(raw) > 25 * 1024 * 1024:
        raise SeeAnyError("生成资源不是可保存的图片，或超过 25 MB")
    suffix = ".png" if mime == "image/png" else ".jpg" if mime == "image/jpeg" else ".webp" if mime == "image/webp" else ""
    if not suffix:
        raise SeeAnyError("生成资源图片格式暂不支持自动导入")
    return raw, suffix
