"""HTTP 路由与访问控制；通过 b 注入桥运行时，避免循环导入。"""
import hmac
import json
import os
import re
import mimetypes
import shutil
from urllib.parse import unquote
from bridge_events import snapshot
import traceback
from http.server import BaseHTTPRequestHandler
from urllib.parse import parse_qs, urlsplit


class BridgeHandler(BaseHTTPRequestHandler):
    def _send(self, code, body, ctype="application/json"):
        if isinstance(body, (dict, list)):
            body = json.dumps(body, ensure_ascii=False)
        data = body.encode("utf-8") if isinstance(body, str) else body
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(data)))
        # 页面 URL 可能带 ?token=，必须阻止它随 Referer 泄漏到任何外部资源。
        # 用响应头而不是 <meta name="referrer">：meta 只对其后发起的请求生效，
        # 有人把远程资源挪到 meta 之前就会静默失效；响应头覆盖所有响应且改不掉。
        self.send_header("Referrer-Policy", "no-referrer")
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.end_headers()
        self.wfile.write(data)

    # ---------- 访问控制 ----------

    def _host_ok(self):
        """校验 Host 头（阻断 DNS rebinding）。"""
        b = self.runtime
        raw = (self.headers.get("Host") or "").strip()
        if not raw:
            return True                    # HTTP/1.0 客户端不带 Host，放行避免误伤
        if raw.startswith("["):            # [::1] 或 [::1]:8000 —— 不能按冒号切
            end = raw.find("]")
            host = raw[1:end] if end > 0 else raw
        else:
            host = raw.rsplit(":", 1)[0]
        return host.strip().lower() in b.ALLOWED_HOSTS or host.strip() in b.LOCAL_IPS

    def _is_loopback_client(self):
        """请求是否来自本机（本机转发器与浏览器无需携带令牌）。"""
        ip = (self.client_address[0] if self.client_address else "") or ""
        return (ip.startswith("127.") or ip in ("::1", "0:0:0:0:0:0:0:1")
                or ip == "::ffff:127.0.0.1")

    def _token_ok(self, allow_query=False):
        """只读视图令牌校验。

        回环客户端免校验（本机转发器与 OBS 浏览器源照常工作）；非回环客户端
        必须带对令牌。只有页面入口（GET /）允许用 ?token= 传令牌——那是用户
        手输的入口，浏览器无法在地址栏带自定义头。
        """
        b = self.runtime
        if not b.BRIDGE_TOKEN:
            return True          # 仅在回环绑定或显式 ALLOW_UNAUTHENTICATED 下出现
        if self._is_loopback_client():
            return True
        got = self.headers.get("X-Bridge-Token") or ""
        if not got and allow_query:
            try:
                got = (parse_qs(urlsplit(self.path).query).get("token") or [""])[0]
            except ValueError:
                got = ""
        if not got:
            return False
        return hmac.compare_digest(got.encode("utf-8"), b.TOKEN_B)

    def _origin_ok(self):
        """校验 Origin（阻断跨站 POST）。非浏览器客户端不带 Origin，放行。"""
        b = self.runtime
        origin = (self.headers.get("Origin") or "").strip()
        if not origin:
            return True
        try:
            parts = urlsplit(origin)
        except ValueError:
            return False
        host = (parts.hostname or "").lower()
        if host not in b.ALLOWED_HOSTS and host not in b.LOCAL_IPS:
            return False
        # 端口也要比：否则任意本地端口的页面都能对本服务发起跨站请求
        try:
            port = parts.port
        except ValueError:
            return False
        if port is None:
            return b.PORT in (80, 443)
        return port == b.PORT

    def _deny(self):
        return self._send(403, {"error": "forbidden: host/origin not allowed"})

    def _page(self, path):
        b = self.runtime
        if not self._token_ok(allow_query=True):
            return self._send(401, {"error": "bad token"})
        if path == "/avatar2d":
            return self._send(200, b.AVATAR_HTML, "text/html; charset=utf-8")
        index = os.path.join(b.STATIC_DIR, "3d", "index.html")
        if not os.path.isfile(index):
            return self._send(503, "请先运行 npm ci && npm run build，再刷新页面。",
                              "text/plain; charset=utf-8")
        with open(index, encoding="utf-8") as f:
            return self._send(200, f.read(), "text/html; charset=utf-8")

    def _events(self):
        b = self.runtime
        # fetch SSE 携带 X-Bridge-Token，令牌不进入事件 URL / 日志。
        self.connection.settimeout(20)
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream; charset=utf-8")
        self.send_header("Cache-Control", "no-cache")
        self.send_header("Referrer-Policy", "no-referrer")
        self.send_header("X-Accel-Buffering", "no")
        self.end_headers()
        previous = -1
        try:
            while True:
                revision = b.EVENTS.wait(previous)
                if revision == previous:
                    data = ": heartbeat\n\n"
                else:
                    state = json.dumps(snapshot(b), ensure_ascii=False)
                    data = f"id: {revision}\nevent: state\ndata: {state}\n\n"
                self.wfile.write(data.encode("utf-8"))
                self.wfile.flush()
                previous = revision
        except (OSError, ConnectionError):
            pass

    def _audio(self):
        b = self.runtime
        query = parse_qs(urlsplit(self.path).query)
        with b.LOCK:
            if "seq" in query:
                try:
                    p = b.REPLY_AUDIO.get(int(query["seq"][0]))
                except ValueError:
                    return self._send(400, {"error": "invalid audio sequence"})
            else:
                p = b.LATEST_AUDIO
        # 旧音频被清理时必须 404，不能用最新回复冒充这个分段。
        return self._media(p, "audio/wav")

    def _media(self, path, content_type):
        try:
            with open(path or "", "rb") as f:
                self.send_response(200)
                self.send_header("Content-Type", content_type)
                self.send_header("Content-Length", str(os.fstat(f.fileno()).st_size))
                self.send_header("Referrer-Policy", "no-referrer")
                self.send_header("X-Content-Type-Options", "nosniff")
                self.send_header("Cache-Control", "no-cache")
                self.end_headers()
                shutil.copyfileobj(f, self.wfile, length=128 * 1024)
        except (FileNotFoundError, IsADirectoryError):
            return self._send(404, {"error": "media unavailable"})

    def _static(self, path, prefix, directory):
        rel = unquote(path[len(prefix):])
        full = os.path.realpath(os.path.join(directory, rel))
        if not full.startswith(os.path.realpath(directory) + os.sep):
            return self._send(403, {"error": "forbidden"})
        ct = {".js": "application/javascript", ".vrm": "model/gltf-binary",
              ".glb": "model/gltf-binary"}.get(os.path.splitext(full)[1])
        ct = ct or mimetypes.guess_type(full)[0] or "application/octet-stream"
        return self._media(full, ct)

    def do_GET(self):
        b = self.runtime
        if not self._host_ok():
            return self._deny()
        path = urlsplit(self.path).path
        if path in ("/", "/avatar", "/avatar3d", "/avatar2d"):
            return self._page(path)
        if path.startswith("/static/"):
            return self._static(path, "/static/", b.STATIC_DIR)
        if path.startswith("/live2d/"):
            return self._static(path, "/live2d/", b.LIVE2D_DIR)
        if not self._token_ok():
            return self._send(401, {"error": "bad token"})
        if path == "/api/events":
            return self._events()
        if path == "/api/state":
            match = re.search(r"[?&]since=(\d+)", self.path)
            return self._send(200, snapshot(b, int(match[1]) if match else 0))
        if path == "/api/avatar":
            with open(os.path.join(b.BASE_DIR, "config", "avatar3d.json"), encoding="utf-8") as f:
                return self._send(200, json.load(f))
        if path == "/audio":
            return self._audio()
        if path == "/video":
            with b.LOCK:
                p = b.LATEST.get("local_path", "")
            return self._media(p, "video/mp4")
        return self._send(404, {"error": "not found"})

    def do_POST(self):
        b = self.runtime
        if not self._host_ok() or not self._origin_ok():
            return self._deny()
        if self.path == "/api/danmaku":
            # 写入口只允许本机：转发器 cyber-xiaoya-danmaku 永远连
            # http://127.0.0.1:8000，远程写没有真实需求。
            # 返回 403 而不是 401 —— 避免转发器把「远程写被禁」误判成令牌过期
            # 去反复重试；同时明确区分「没权限做这件事」与「令牌不对」。
            if not self._is_loopback_client():
                return self._send(403, {"error": "remote write disabled"})
            try:
                n = int(self.headers.get("Content-Length", 0) or 0)
                if n <= 0:
                    return self._send(400, {"error": "empty body"})
                if n > b.MAX_BODY:
                    return self._send(413, {"error": "payload too large"})
                body = json.loads(self.rfile.read(n) or b"{}")
            except Exception:
                return self._send(400, {"error": "bad json"})
            if not isinstance(body, dict):
                return self._send(400, {"error": "json object required"})
            fields = ("kind", "priority", "nick", "say_nick", "gift_item", "content", "user", "text")
            if any(body.get(k) is not None and not isinstance(body[k], str) for k in fields):
                return self._send(400, {"error": "text fields must be strings"})
            return self._accept_danmaku(body)
        return self._send(404, {"error": "not found"})

    def _accept_danmaku(self, body):
        b = self.runtime
        kind = (body.get("kind") or "").strip()
        priority = (body.get("priority") or "normal").strip()
        action = None
        if kind == "gift":
            nick = (body.get("nick") or body.get("say_nick") or "").strip()
            itm = (body.get("gift_item") or "礼物").strip()
            try:
                num = int(body.get("gift_num") or 1)
                gift_value = int(body.get("gift_value") or 0)
                combo = int(body.get("combo") or num)
            except (TypeError, ValueError):
                return self._send(400, {"error": "gift_num/gift_value/combo must be numbers"})
            num = max(1, num)
            gift_value = max(0, gift_value)
            combo = max(num, combo)
            # 先走“快速演出”通道：不等 DeepSeek/TTS，礼物一到就能触发动作。
            # 导演失败不能影响礼物入队/LLM 感谢链路，所以单独兜底。
            try:
                action = b.publish_action(b.DIRECTOR.ingest({
                    "nick": nick or "神秘人",
                    "gift": itm,
                    "count": num,
                    "value": gift_value,
                    "combo": combo,
                    "gift_id": str(body.get("gift_id") or ""),
                    "event_id": str(body.get("event_id") or ""),
                }))
            except Exception:  # noqa: BLE001
                traceback.print_exc()
            item = {
                "kind": "gift", "user": nick, "priority": priority,
                "gift_item": itm, "gift_num": num,
                "gift_value": gift_value,
                "event_id": str(body.get("event_id") or ""),
                "content": f"观众「{nick or '神秘人'}」送了 {num} 个「{itm}」",
            }
        elif (body.get("content") or "").strip():
            item = {
                "kind": "chat", "priority": priority,
                "user": (body.get("user") or body.get("nick") or "").strip(),
                "content": (body.get("content") or "").strip(),
                "event_id": str(body.get("event_id") or ""),
            }
        elif (body.get("text") or "").strip():
            item = {"kind": "speak", "user": "", "priority": priority,
                    "text": (body.get("text") or "").strip(),
                    "event_id": str(body.get("event_id") or "")}
        else:
            return self._send(400, {"error": "content required"})
        ok = b.enqueue(item)
        response = {"queued": ok, "depth": len(b.QUEUE),
                    "status": "queued" if ok else "skipped"}
        if action:
            response["action"] = action
        return self._send(200, response)

    def log_message(self, *a):
        pass



def make_handler(runtime):
    class Handler(BridgeHandler):
        pass
    Handler.runtime = runtime
    return Handler
