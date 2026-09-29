#!/usr/bin/env python3
import argparse
import json
import mimetypes
import os
import sys
import urllib.error
import urllib.parse
import urllib.request
import uuid


DEFAULT_API_BASE = "https://api.seeany.com"
DEFAULT_DOCS_URL = "https://www.seeany.com/developer/api-docs.json"
DEFAULT_USER_AGENT = "seeany-api"
KEY_SETUP_URL = "https://www.seeany.com/developer/keys"


def output(value, exit_code=0):
    if isinstance(value, (dict, list)):
        value = json.dumps(value, ensure_ascii=False, indent=2)
    sys.stdout.write(str(value) + ("" if str(value).endswith("\n") else "\n"))
    raise SystemExit(exit_code)


def fail(code, message, exit_code=2, **extra):
    output({"ok": False, "error": {"code": code, "message": message, **extra}}, exit_code)


def load_json_file(path):
    try:
        with open(path, "r", encoding="utf-8") as stream:
            return json.load(stream)
    except Exception as exc:
        fail("INVALID_JSON_FILE", f"Unable to read {path}: {exc}")


def load_docs(args):
    if args.docs_file:
        return load_json_file(args.docs_file)
    request = urllib.request.Request(args.docs_url, headers={"User-Agent": args.user_agent})
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            return json.load(response)
    except Exception as exc:
        fail("DOCS_UNAVAILABLE", f"Unable to load SeeAny API docs: {exc}", docs_url=args.docs_url)


def endpoints(docs):
    return [
        {**item, "group": group.get("title", "")}
        for group in docs.get("groups", [])
        for item in group.get("items", [])
    ]


def compact_endpoint(item):
    return {
        "aiTypeId": item.get("aiTypeId"),
        "name": item.get("name"),
        "group": item.get("group"),
        "method": item.get("method"),
        "path": item.get("path"),
    }


def find_endpoint(docs, selector):
    candidates = endpoints(docs)
    needle = str(selector).strip().lower()
    exact = [
        item
        for item in candidates
        if needle in {str(item.get("id", "")).lower(), str(item.get("aiTypeId", "")).lower(), str(item.get("name", "")).lower()}
    ]
    if len(exact) == 1:
        return exact[0]
    partial = [item for item in candidates if needle in str(item.get("name", "")).lower()]
    if len(partial) == 1:
        return partial[0]
    matches = exact or partial
    if matches:
        fail("AMBIGUOUS_CAPABILITY", "Multiple capabilities matched.", matches=[compact_endpoint(item) for item in matches])
    fail("CAPABILITY_NOT_FOUND", f"No documented capability matched: {selector}")


def resolve_api_key(explicit):
    api_key = (explicit or os.getenv("SEEANY_API_KEY", "")).strip()
    if not api_key:
        fail("MISSING_API_KEY", "SeeAny API key not found.", setup_url=KEY_SETUP_URL)
    return api_key


def read_payload(path, optional=False):
    if path:
        payload = load_json_file(path)
    elif not sys.stdin.isatty():
        raw = sys.stdin.read().strip()
        if not raw and optional:
            return {}
        try:
            payload = json.loads(raw)
        except json.JSONDecodeError as exc:
            fail("INVALID_JSON", f"Invalid JSON payload: {exc}")
    elif optional:
        return {}
    else:
        fail("MISSING_PAYLOAD", "Pass --payload-file or pipe a JSON object through stdin.")
    if not isinstance(payload, dict):
        fail("INVALID_PAYLOAD", "The request payload must be a JSON object.")
    return payload


def parse_response(response):
    content_type = response.headers.get_content_type()
    if content_type == "text/event-stream":
        for raw_line in response:
            sys.stdout.write(raw_line.decode("utf-8", errors="replace"))
            sys.stdout.flush()
        raise SystemExit(0)
    raw = response.read().decode("utf-8", errors="replace")
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        return raw


def send(method, url, api_key, user_agent, payload=None, body=None, content_type=None):
    headers = {"Authorization": f"Bearer {api_key}", "User-Agent": user_agent, "Accept": "application/json, text/event-stream"}
    if content_type:
        headers["Content-Type"] = content_type
    elif payload is not None:
        headers["Content-Type"] = "application/json"
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    request = urllib.request.Request(url, data=body, headers=headers, method=method)
    try:
        with urllib.request.urlopen(request, timeout=120) as response:
            return parse_response(response)
    except urllib.error.HTTPError as exc:
        raw = exc.read().decode("utf-8", errors="replace")
        try:
            detail = json.loads(raw)
        except json.JSONDecodeError:
            detail = raw
        fail("UPSTREAM_HTTP_ERROR", f"SeeAny API returned HTTP {exc.code}.", 1, status_code=exc.code, response=detail)
    except urllib.error.URLError as exc:
        fail("NETWORK_ERROR", str(exc), 1)


def catalog(args):
    items = endpoints(load_docs(args))
    if args.query:
        needle = args.query.lower()
        items = [
            item
            for item in items
            if needle in " ".join(str(item.get(key, "")) for key in ("name", "group", "desc", "aiTypeId", "path")).lower()
        ]
    output([compact_endpoint(item) for item in items])


def describe(args):
    output(find_endpoint(load_docs(args), args.selector))


def call(args):
    item = None
    method = args.method.upper() if args.method else ""
    path = args.path or ""
    payload = read_payload(args.payload_file, optional=method == "GET")
    if args.selector:
        item = find_endpoint(load_docs(args), args.selector)
        method = method or str(item.get("method") or "POST").upper()
        path = path or str(item.get("path") or "")
        if method != "GET" and item.get("appendTypeFields") is not False:
            payload.setdefault("aiTypeId", item.get("aiTypeId"))
            payload.setdefault("aiType", item.get("aitype"))
    if not method or not path:
        fail("MISSING_ENDPOINT", "Pass a capability selector, or pass both --method and --path.")
    if not path.startswith("/api/") or "://" in path:
        fail("INVALID_PATH", "Only documented /api/ paths are allowed.")
    url = args.api_base.rstrip("/") + path
    body_payload = payload
    if method == "GET":
        url += ("&" if "?" in url else "?") + urllib.parse.urlencode(payload, doseq=True)
        body_payload = None
    response = send(method, url, resolve_api_key(args.api_key), args.user_agent, payload=body_payload)
    output(response)


def upload(args):
    try:
        with open(args.file, "rb") as stream:
            file_data = stream.read()
    except OSError as exc:
        fail("FILE_UNAVAILABLE", f"Unable to read {args.file}: {exc}")
    boundary = "----seeany-" + uuid.uuid4().hex
    filename = os.path.basename(args.file)
    mime = mimetypes.guess_type(filename)[0] or "application/octet-stream"
    body = (
        f"--{boundary}\r\nContent-Disposition: form-data; name=\"file\"; filename=\"{filename}\"\r\n"
        f"Content-Type: {mime}\r\n\r\n"
    ).encode("utf-8") + file_data + f"\r\n--{boundary}--\r\n".encode("utf-8")
    url = args.api_base.rstrip("/") + "/api/upload/image"
    response = send("POST", url, resolve_api_key(args.api_key), args.user_agent, body=body, content_type=f"multipart/form-data; boundary={boundary}")
    output(response)


def build_parser():
    parser = argparse.ArgumentParser(description="Discover and call documented SeeAny APIs.")
    parser.add_argument("--api-base", default=DEFAULT_API_BASE)
    parser.add_argument("--docs-url", default=DEFAULT_DOCS_URL)
    parser.add_argument("--docs-file", default="", help="Use a local api-docs.json snapshot.")
    parser.add_argument("--api-key", default="", help="Falls back to SEEANY_API_KEY.")
    parser.add_argument("--user-agent", default=os.getenv("SEEANY_USER_AGENT", "").strip() or DEFAULT_USER_AGENT)
    commands = parser.add_subparsers(dest="command", required=True)

    catalog_parser = commands.add_parser("catalog", help="List documented capabilities.")
    catalog_parser.add_argument("--query", default="")
    catalog_parser.set_defaults(func=catalog)

    describe_parser = commands.add_parser("describe", help="Show current docs for one capability.")
    describe_parser.add_argument("selector")
    describe_parser.set_defaults(func=describe)

    call_parser = commands.add_parser("call", help="Call a capability or a documented API path.")
    call_parser.add_argument("selector", nargs="?")
    call_parser.add_argument("--method", choices=("GET", "POST", "get", "post"), default="")
    call_parser.add_argument("--path", default="")
    call_parser.add_argument("--payload-file", default="")
    call_parser.set_defaults(func=call)

    upload_parser = commands.add_parser("upload", help="Upload a local image.")
    upload_parser.add_argument("file")
    upload_parser.set_defaults(func=upload)
    return parser


def main():
    args = build_parser().parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
