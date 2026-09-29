"""One-click local launcher for the forked canvas and companion API."""

import subprocess
import importlib.util
import os
import sys
import threading
import time
import webbrowser
from http.server import ThreadingHTTPServer
from pathlib import Path
from urllib.error import URLError
from urllib.request import urlopen

ROOT = Path(__file__).resolve().parent.parent
WEB = ROOT / "infinite-canvas" / "web"
PYTHON_DEPS = ROOT / "data" / "commerce-studio" / "python-deps"


def clear_unusable_proxy():
    for name in ("HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY"):
        if os.environ.get(name, "").rstrip("/") == "http://127.0.0.1:9":
            os.environ.pop(name, None)


def ensure_dependencies():
    if not (WEB / "node_modules" / "vite").exists():
        print("首次启动：安装画布依赖，可能需要几分钟……")
        try:
            subprocess.run(["npm.cmd", "install", "--legacy-peer-deps"], cwd=WEB, check=True)
        except (OSError, subprocess.CalledProcessError) as exc:
            raise SystemExit("画布依赖安装失败，请检查 npm 网络连接") from exc
    if str(PYTHON_DEPS) not in sys.path:
        sys.path.insert(0, str(PYTHON_DEPS))
    if any(importlib.util.find_spec(name) is None for name in ("PIL", "pypdf", "docx")):
        print("首次启动：安装本机图片与文档解析依赖……")
        try:
            subprocess.run([sys.executable, "-m", "pip", "install", "--target", str(PYTHON_DEPS), "-r", str(ROOT / "commerce_studio" / "requirements.txt")], check=True)
        except (OSError, subprocess.CalledProcessError) as exc:
            raise SystemExit("Python 依赖安装失败，请检查 PyPI 网络连接") from exc
        importlib.invalidate_caches()
        if any(importlib.util.find_spec(name) is None for name in ("PIL", "pypdf", "docx")):
            raise SystemExit("Python 依赖安装后仍无法加载，请检查 Python 环境")


def main():
    clear_unusable_proxy()
    ensure_dependencies()
    from .http import Handler

    server = ThreadingHTTPServer(("127.0.0.1", 8766), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    frontend = subprocess.Popen(["npm.cmd", "run", "dev", "--", "--host", "127.0.0.1", "--strictPort"], cwd=WEB)
    try:
        for _ in range(80):
            if frontend.poll() is not None:
                raise SystemExit("画布启动失败，请检查终端输出")
            try:
                with urlopen("http://127.0.0.1:3000", timeout=1):
                    break
            except (URLError, TimeoutError):
                time.sleep(0.25)
        else:
            raise SystemExit("画布启动超时，请检查端口 3000")
        print("广告电商工作台已启动：http://127.0.0.1:3000/canvas")
        webbrowser.open("http://127.0.0.1:3000/canvas")
        frontend.wait()
    finally:
        if frontend.poll() is None:
            frontend.terminate()
        server.shutdown()


if __name__ == "__main__":
    main()
