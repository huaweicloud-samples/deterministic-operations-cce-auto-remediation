"""
FunctionGraph 自定义镜像运行时适配层（shim）

华为云 FunctionGraph 自定义镜像要求容器以 HTTP 服务形式提供运行时：
- GET  /healthz            健康检查（平台定期探测）
- POST /invoke 或 POST /   接收事件体，调用对应函数的 handler(event, context)
                           并将返回值以 JSON 返回

本 shim 用标准库实现，无额外依赖；由 Dockerfile 注入 FUNC_HANDLER 指定入口。
业务代码（functions/*.py）保持与平台无关的 handler(event, context) 签名。
"""

import os
import sys
import json
import importlib
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

APP_ROOT = os.environ.get("APP_ROOT", "/app/src")
if APP_ROOT not in sys.path:
    sys.path.insert(0, APP_ROOT)

PORT = int(os.environ.get("PORT", "8000"))
FUNC_HANDLER = os.environ.get("FUNC_HANDLER", "functions.detection_function.handler")


def load_handler():
    mod_path, func_name = FUNC_HANDLER.rsplit(".", 1)
    mod = importlib.import_module(mod_path)
    return getattr(mod, func_name)


HANDLER = None


class Handler(BaseHTTPRequestHandler):
    def _send(self, code, body):
        if isinstance(body, (bytes, bytearray)):
            data = body
        else:
            data = json.dumps(body, ensure_ascii=False, default=str).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self):
        if self.path in ("/healthz", "/health", "/"):
            self._send(200, {"status": "ok"})
        else:
            self._send(404, {"error": "not found"})

    def do_POST(self):
        if self.path == "/init":
            # FunctionGraph 自定义镜像冷启动初始化入口，按规范需返回 200
            self._send(200, {"status": "ok"})
            return
        length = int(self.headers.get("Content-Length", 0) or 0)
        raw = self.rfile.read(length) if length else b"{}"
        try:
            event = json.loads(raw.decode("utf-8") or "{}")
        except Exception:
            event = {"raw": raw.decode("utf-8", errors="ignore")}
        try:
            result = HANDLER(event, None)
            self._send(200, result if result is not None else {})
        except Exception as e:  # noqa: BLE001
            self._send(500, {"error": str(e)})

    def log_message(self, *args):  # 静默访问日志
        pass


if __name__ == "__main__":
    HANDLER = load_handler()
    server = ThreadingHTTPServer(("0.0.0.0", PORT), Handler)
    server.serve_forever()
