"""本地 HTTP 桥：让 ZCode hook 等外部程序给桌宠发事件。

只监听 127.0.0.1，用 stdlib 实现，不引入任何 web 框架依赖。
接口：
  GET  /health                      -> {"ok": true}（无需 token，无敏感信息）
  GET  /react?event=edit&token=xxx  -> 触发事件（curl 最省事的姿势）
  POST /react  body {"event":"edit","message":"改了xx"}
       header X-Petfw-Token: xxx
合法请求会把 {"type":"hook", ...} 投进 sink 队列，由宿主消费。
"""
import hmac
import http.server
import json
import queue
import threading
import urllib.parse


def _make_handler(port_ref, token: str, sink: "queue.Queue", on_error=None):
    class Handler(http.server.BaseHTTPRequestHandler):
        def log_message(self, *args):  # 静音默认访问日志
            pass

        def _deny(self, code, msg):
            self.send_response(code)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.end_headers()
            self.wfile.write(json.dumps({"ok": False, "error": msg}).encode("utf-8"))

        def _emit(self, ev: str, message: str):
            if not ev:
                ev = "ping"
            item = {"type": "hook", "event": ev[:40]}
            if message:
                item["message"] = str(message)[:200]
            sink.put(item)
            self.send_response(200)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.end_headers()
            self.wfile.write(json.dumps({"ok": True}, ensure_ascii=False).encode("utf-8"))

        def do_GET(self):
            parsed = urllib.parse.urlparse(self.path)
            qs = urllib.parse.parse_qs(parsed.query)
            get1 = lambda k: (qs.get(k) or [""])[0]
            if parsed.path == "/health":
                self.send_response(200)
                self.send_header("Content-Type", "application/json; charset=utf-8")
                self.end_headers()
                self.wfile.write(b'{"ok":true,"pet":"petfw"}')
                return
            if parsed.path == "/react":
                # 时序安全比较，避免细微的侧信道
                if not hmac.compare_digest(get1("token"), token):
                    return self._deny(401, "bad token")
                return self._emit(get1("event"), get1("message"))
            return self._deny(404, "not found")

        def do_POST(self):
            parsed = urllib.parse.urlparse(self.path)
            if parsed.path != "/react":
                return self._deny(404, "not found")
            header_token = self.headers.get("X-Petfw-Token", "")
            if not hmac.compare_digest(header_token, token):
                return self._deny(401, "bad token")
            try:
                length = int(self.headers.get("Content-Length", 0) or 0)
                body = json.loads(self.rfile.read(length).decode("utf-8") or "{}")
            except Exception:
                return self._deny(400, "bad json")
            return self._emit(str(body.get("event", "")), body.get("message", ""))

    return Handler


class BridgeServer:
    """线程化的本地事件入口；start() 之后往 sink 里投递解析好的事件。"""

    def __init__(self, port: int, token: str):
        self.sink: "queue.Queue" = queue.Queue()
        self.port = port
        self._httpd = None
        self._thread = None
        self.handler = _make_handler(None, token, self.sink)

    def start(self) -> int:
        """返回实际监听的端口；端口被占则抛 OSError 由上层决定降级。"""
        self._httpd = http.server.ThreadingHTTPServer(("127.0.0.1", self.port), self.handler)
        self.port = self._httpd.server_address[1]
        self._thread = threading.Thread(target=self._httpd.serve_forever,
                                        daemon=True, name="petfw-bridge")
        self._thread.start()
        return self.port

    def stop(self):
        if self._httpd:
            self._httpd.shutdown()
            self._httpd.server_close()
