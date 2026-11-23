"""Start or reopen the local personal chat application."""
import argparse
import json
import re
import socket
import threading
import time
from urllib.request import build_opener, ProxyHandler
import webbrowser
import uvicorn

from .api import DEFAULT_ROOT, STATIC, create_app
from .contract import API_REVISION
from .store import PRODUCT, root_identity


def instance_status(url, root):
    try:
        with build_opener(ProxyHandler({})).open(url + "api/v1/runtime", timeout=1) as response:
            result = json.load(response)
            if isinstance(result, dict) and result.get("product") == PRODUCT and result.get("instance") == root_identity(root):
                return "ready" if result.get("api_revision") == API_REVISION else "outdated"
    except (OSError, ValueError):
        pass
    return "unavailable"


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--port", type=int, default=17861)
    p.add_argument("--root", default=str(DEFAULT_ROOT))
    p.add_argument("--no-browser", action="store_true")
    args = p.parse_args()
    index = STATIC / "index.html"
    if not index.is_file():
        p.exit(1, "缺少已构建的前端资源，请检查软件目录。\n")
    assets = re.findall(r'(?:src|href)="(/assets/[^"]+)"', index.read_text(encoding="utf8"))
    if not assets or not all((STATIC / asset.lstrip("/")).is_file() for asset in assets):
        p.exit(1, "聊天页面静态文件不完整，请恢复随软件交付的页面或重新构建。\n")
    url = f"http://127.0.0.1:{args.port}/"
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        if hasattr(socket, "SO_EXCLUSIVEADDRUSE"):
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
        try:
            sock.bind(("127.0.0.1", args.port))
        except OSError:
            # A simultaneous double-click can find the socket before the first
            # instance has finished opening SQLite and serving its identity.
            deadline = time.monotonic() + 4
            while time.monotonic() < deadline:
                status = instance_status(url, args.root)
                if status == "ready":
                    if not args.no_browser:
                        webbrowser.open(url)
                    return
                if status == "outdated":
                    p.exit(1, "该端口上的聊天后台版本已过期。请停止旧服务，再运行 Start-GC-Chat.cmd。\n")
                time.sleep(.15)
            p.exit(1, "本机端口已被其他程序占用，请使用 --port 指定其他端口。\n")
        if not args.no_browser:
            def open_when_ready():
                for _ in range(100):
                    if instance_status(url, args.root) == "ready":
                        webbrowser.open(url)
                        return
                    time.sleep(.15)
            threading.Thread(target=open_when_ready, daemon=True).start()
        server = uvicorn.Server(uvicorn.Config(create_app(args.root), host="127.0.0.1", port=args.port,
                                               access_log=False, timeout_graceful_shutdown=5))
        server.run(sockets=[sock])
        if not server.started:
            p.exit(1, "启动失败，请查看上方的本地诊断。\n")


if __name__ == "__main__":
    main()
