"""Explicit, replaceable provider boundary. Only DeepSeek text calls are enabled."""

from __future__ import annotations

import json
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen


class ProviderError(Exception):
    pass


def deepseek_complete(api_key, messages, *, json_mode=False, max_tokens=1600, transport=urlopen):
    payload = {"model": "deepseek-flash", "messages": messages, "max_tokens": max_tokens, "stream": False}
    if json_mode:
        payload["response_format"] = {"type": "json_object"}
        payload["thinking"] = {"type": "disabled"}
    request = Request(
        "https://api.deepseek.com/chat/completions",
        data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
        headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
        method="POST",
    )
    try:
        with transport(request, timeout=90) as response:
            result = json.load(response)
    except HTTPError as exc:
        raise ProviderError(f"DeepSeek HTTP {exc.code}") from exc
    except (URLError, TimeoutError) as exc:
        raise ProviderError("DeepSeek 网络连接失败或超时") from exc
    choices = result.get("choices") or []
    if not choices or not choices[0].get("message", {}).get("content"):
        raise ProviderError("DeepSeek 未返回可用内容")
    if choices[0].get("finish_reason") == "length":
        raise ProviderError("DeepSeek 输出被截断，请缩小输入范围")
    return {"id": result.get("id", ""), "text": choices[0]["message"]["content"], "usage": result.get("usage", {}), "model": result.get("model", "deepseek-flash")}
