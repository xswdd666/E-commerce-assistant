"""One-click local launcher for the forked canvas and companion API."""

import subprocess
import threading
import time
import webbrowser
from http.server import ThreadingHTTPServer
from pathlib import Path
from urllib.error import URLError
from urllib.request import urlopen

from .http import Handler


ROOT = Path(__file__).resolve().parent.parent
WEB = ROOT / "infinite-canvas" / "web"


def main():
    if not (WEB / "node_modules" / "vite").exists():
        raise SystemExit("请先在 infinite-canvas\\web 运行 npm install --legacy-peer-deps")
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
