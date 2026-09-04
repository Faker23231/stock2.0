# -*- coding: utf-8 -*-
"""本地 HTTP 服务：对外提供 REST API 与静态页面。

只用标准库 http.server，配合 ThreadingHTTPServer 支撑前端并发轮询。
"""

import json
import mimetypes
import os
import posixpath
import threading
import time
import traceback
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from . import (advisor, datasource, equity, forecast, macro, news, paths,
               report, scheduler, store, valuation)

_ANALYSIS_CACHE = {"data": None, "ts": 0.0}
_ANALYSIS_LOCK = threading.Lock()
_NEWS_CACHE = {"data": None, "ts": 0.0}
_NEWS_LOCK = threading.Lock()
_SNAP_RECORD_LOCK = threading.Lock()
_SNAP_RECORD_DAY = ""   # 本进程已自动记录过快照的日期（每日至多一次）


def _maybe_record_snapshot(result):
    """每次持仓估值后按日期幂等地落一条净值快照。

    规则：持仓非空才记录；同一天内本进程只自动写一次（日报/报告生成时
    仍会用收盘值覆盖同一日期，形成“当日最终值”）。空仓日不写，避免
    曲线中间出现无意义的 0 值。
    """
    global _SNAP_RECORD_DAY
    summary = (result or {}).get("summary") or {}
    if int(summary.get("position_count") or 0) <= 0:
        return
    today = time.strftime("%Y-%m-%d")
    with _SNAP_RECORD_LOCK:
        if _SNAP_RECORD_DAY == today:
            return
        _SNAP_RECORD_DAY = today
    try:
        store.save_snapshot(
            today,
            summary.get("market_value", 0) or 0,
            summary.get("cost_value", 0) or 0,
            summary.get("pnl", 0) or 0,
            summary.get("day_pnl", 0) or 0)
    except Exception:
        traceback.print_exc()


def _cached_analysis(max_age=180):
    with _ANALYSIS_LOCK:
        if _ANALYSIS_CACHE["data"] and (time.time() - _ANALYSIS_CACHE["ts"]) < max_age:
            return _ANALYSIS_CACHE["data"]
    data = macro.build_premarket_analysis()
    with _ANALYSIS_LOCK:
        _ANALYSIS_CACHE["data"] = data
        _ANALYSIS_CACHE["ts"] = time.time()
    return data


def _cached_news(max_age=120):
    with _NEWS_LOCK:
        if _NEWS_CACHE["data"] and (time.time() - _NEWS_CACHE["ts"]) < max_age:
            return _NEWS_CACHE["data"]
    data = news.get_news_digest()
    with _NEWS_LOCK:
        _NEWS_CACHE["data"] = data
        _NEWS_CACHE["ts"] = time.time()
    return data


class Handler(BaseHTTPRequestHandler):
    server_version = "USStockPortfolio/1.0"
    protocol_version = "HTTP/1.1"

    # ---------------- 响应工具 ----------------
    def _send_json(self, payload, status=200):
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        try:
            self.wfile.write(body)
        except Exception:
            pass

    def _send_bytes(self, data, content_type, status=200, download_name=None,
                    headers=None):
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(data)))
        if download_name:
            self.send_header("Content-Disposition",
                             'inline; filename="%s"' % download_name)
        for key, value in (headers or []):
            self.send_header(key, value)
        self.end_headers()
        try:
            self.wfile.write(data)
        except Exception:
            pass

    def _send_text(self, text, status=200, content_type="text/plain; charset=utf-8"):
        self._send_bytes(text.encode("utf-8"), content_type, status)

    def _read_json_body(self):
        try:
            length = int(self.headers.get("Content-Length") or 0)
        except Exception:
            length = 0
        if length <= 0:
            return {}
        raw = self.rfile.read(length)
        try:
            return json.loads(raw.decode("utf-8"))
        except Exception:
            return {}

    def log_message(self, fmt, *args):
        # 默认实现会把每个请求打到控制台，轮询场景下噪音太大
        return

    # ---------------- 静态文件 ----------------
    def _serve_static(self, rel_path):
        root = paths.web_dir()
        rel_path = rel_path.lstrip("/") or "index.html"
        safe = posixpath.normpath(rel_path).lstrip("./")
        if safe.startswith(".."):
            self._send_text("非法路径", 400)
            return
        full = os.path.join(root, safe.replace("/", os.sep))
        if not os.path.isfile(full):
            self._send_text("404 Not Found: %s" % rel_path, 404)
            return
        ctype = mimetypes.guess_type(full)[0] or "application/octet-stream"
        if ctype.startswith("text/") or ctype in ("application/javascript",
                                                  "application/json"):
            ctype += "; charset=utf-8"
        with open(full, "rb") as fh:
            self._send_bytes(fh.read(), ctype)

    def _serve_report(self, filename):
        directory = paths.reports_dir()
        safe = os.path.basename(filename)
        full = os.path.join(directory, safe)
        if not os.path.isfile(full):
            self._send_text("报告不存在：%s" % safe, 404)
            return
        with open(full, "rb") as fh:
            self._send_bytes(fh.read(), "text/html; charset=utf-8",
                             download_name=safe)

    # ---------------- 鉴权 / PWA ----------------
    def _is_protected(self, path):
        """需鉴权的路径：所有 /api/*（/api/ping 除外）与 /reports/*。"""
        if path.startswith("/api/") and path != "/api/ping":
            return True
        if path.startswith("/reports/"):
            return True
        return False

    def _authorized(self, parsed):
        """config.auth_token 为空=不鉴权；否则比对 X-Auth-Token 头或 ?token=。"""
        cfg = store.load_config()
        token = (cfg.get("auth_token") or "").strip()
        if not token:
            return True
        given = (self.headers.get("X-Auth-Token") or "").strip()
        if not given:
            query = urllib.parse.parse_qs(parsed.query)
            given = (query.get("token") or [""])[0]
        return given == token

    def _guard_auth(self, parsed, path):
        if self._is_protected(path) and not self._authorized(parsed):
            self._send_json({"ok": False, "error": "unauthorized"}, 401)
            return False
        return True

    def _serve_pwa_file(self, name):
        """PWA 根文件（manifest / sw / icons），sw.js 禁止浏览器长期缓存。"""
        full = os.path.join(paths.web_dir(), os.path.basename(name))
        if not os.path.isfile(full):
            self._send_text("404 Not Found", 404)
            return
        ctype = mimetypes.guess_type(full)[0] or "application/octet-stream"
        if ctype.startswith("text/") or "javascript" in ctype or "json" in ctype:
            ctype += "; charset=utf-8"
        with open(full, "rb") as fh:
            body = fh.read()
        headers = [("Cache-Control", "no-store")] if name == "sw.js" else None
        self._send_bytes(body, ctype, headers=headers)

    def _portfolio_state(self):
        """估值 + 每日自动记录净值快照，返回可直接发送的 payload。"""
        result = valuation.evaluate_portfolio()
        _maybe_record_snapshot(result)
        return {"ok": True, "data": result}

    # ---------------- GET ----------------
    def do_GET(self):
        parsed = urllib.parse.urlparse(self.path)
        path = parsed.path
        query = urllib.parse.parse_qs(parsed.query)

        try:
            if not self._guard_auth(parsed, path):
                return
            if path == "/" or path == "/index.html":
                self._serve_static("index.html")
            elif path.startswith("/static/"):
                self._serve_static(path[len("/static/"):])
            elif path.startswith("/reports/"):
                self._serve_report(path[len("/reports/"):])
            elif path in ("/manifest.webmanifest", "/sw.js", "/icon-192.png",
                          "/icon-512.png", "/apple-touch-icon.png"):
                self._serve_pwa_file(path[1:])
            elif path == "/api/state":
                self._send_json(self._portfolio_state())
            elif path == "/api/search":
                keyword = (query.get("q") or [""])[0]
                self._send_json({"ok": True,
                                 "data": datasource.search_symbols(keyword)})
            elif path == "/api/quote":
                symbol = (query.get("symbol") or [""])[0]
                quote = datasource.get_quote(symbol)
                self._send_json({"ok": bool(quote), "data": quote})
            elif path == "/api/premarket":
                force = (query.get("force") or ["0"])[0] == "1"
                data = macro.build_premarket_analysis() if force else _cached_analysis()
                digest = news.get_news_digest() if force else _cached_news()
                try:
                    data["brief"] = advisor.build_brief(data, digest)
                except Exception:
                    traceback.print_exc()
                    data["brief"] = None
                self._send_json({"ok": True, "data": data})
            elif path == "/api/news":
                force = (query.get("force") or ["0"])[0] == "1"
                data = news.get_news_digest() if force else _cached_news()
                self._send_json({"ok": True, "data": data})
            elif path == "/api/forecast":
                force = (query.get("force") or ["0"])[0] == "1"
                self._send_json(forecast.build_forecast(force=force))
            elif path == "/api/config":
                cfg = store.load_config()
                # 不把密码回传给前端
                safe_cfg = json.loads(json.dumps(cfg))
                if safe_cfg.get("email", {}).get("password"):
                    safe_cfg["email"]["password"] = "********"
                self._send_json({"ok": True, "data": safe_cfg})
            elif path == "/api/reports":
                self._send_json({"ok": True, "data": report.list_reports()})
            elif path == "/api/schedule":
                self._send_json({"ok": True, "data": {
                    "next": scheduler.scheduler.next_runs(),
                    "logs": scheduler.scheduler.get_logs(),
                }})
            elif path == "/api/snapshots":
                force = (query.get("force") or ["0"])[0] == "1"
                self._send_json(equity.build_equity_series(force=force))
            elif path == "/api/ping":
                self._send_json({"ok": True, "data": "pong"})
            else:
                self._send_text("404 Not Found", 404)
        except Exception as exc:
            traceback.print_exc()
            self._send_json({"ok": False, "error": str(exc)}, 500)

    # ---------------- POST ----------------
    def do_POST(self):
        parsed = urllib.parse.urlparse(self.path)
        path = parsed.path
        query = urllib.parse.parse_qs(parsed.query)
        body = self._read_json_body()

        try:
            if not self._guard_auth(parsed, path):
                return
            if path == "/api/position/add":
                store.add_position(
                    symbol=body.get("symbol"),
                    shares=body.get("shares"),
                    cost=body.get("cost"),
                    name=body.get("name", ""),
                    note=body.get("note", ""),
                    direction=body.get("direction", "long"))
                self._send_json(self._portfolio_state())
            elif path == "/api/position/update":
                store.update_position(body.get("id"),
                                      shares=body.get("shares"),
                                      cost=body.get("cost"),
                                      note=body.get("note"))
                self._send_json(self._portfolio_state())
            elif path == "/api/position/delete":
                store.delete_position(body.get("id"))
                self._send_json(self._portfolio_state())
            elif path == "/api/watch/add":
                store.add_watch(body.get("symbol"), body.get("name", ""))
                self._send_json(self._portfolio_state())
            elif path == "/api/watch/delete":
                store.delete_watch(body.get("symbol"))
                self._send_json(self._portfolio_state())
            elif path == "/api/config":
                patch = body or {}
                # 前端回传掩码时不要覆盖真实密码
                email = patch.get("email")
                if isinstance(email, dict) and email.get("password") == "********":
                    email.pop("password", None)
                cfg = store.save_config(patch)
                safe_cfg = json.loads(json.dumps(cfg))
                if safe_cfg.get("email", {}).get("password"):
                    safe_cfg["email"]["password"] = "********"
                self._send_json({"ok": True, "data": safe_cfg})
            elif path == "/api/report/generate":
                kind = (body.get("kind") or (query.get("kind") or ["daily"])[0])
                send_mail = bool(body.get("email"))
                if send_mail:
                    built = report.generate_and_send(kind=kind)
                else:
                    built = report.save_report(kind=kind)
                self._send_json({"ok": True, "data": {
                    "filename": built["filename"],
                    "url": "/reports/" + built["filename"],
                    "generated_at": built["generated_at"],
                    "email_ok": built.get("email_ok"),
                    "email_message": built.get("email_message"),
                }})
            elif path == "/api/email/test":
                ok, message = report.send_email(
                    "美股持仓平台 · 邮件配置测试",
                    "<h3>配置成功</h3><p>如果你看到这封邮件，说明 SMTP 配置正确，"
                    "日报可以正常推送。</p>")
                self._send_json({"ok": ok, "message": message})
            elif path == "/api/cache/clear":
                datasource.clear_cache()
                _ANALYSIS_CACHE["data"] = None
                _NEWS_CACHE["data"] = None
                self._send_json({"ok": True, "message": "缓存已清空"})
            else:
                self._send_text("404 Not Found", 404)
        except (ValueError, KeyError) as exc:
            self._send_json({"ok": False, "error": str(exc)}, 400)
        except Exception as exc:
            traceback.print_exc()
            self._send_json({"ok": False, "error": str(exc)}, 500)


def create_server(host="127.0.0.1", port=8756):
    """创建服务器，端口被占用时自动往后找。"""
    last_error = None
    for offset in range(0, 30):
        try:
            httpd = ThreadingHTTPServer((host, port + offset), Handler)
            httpd.daemon_threads = True
            return httpd, port + offset
        except OSError as exc:
            last_error = exc
            continue
    raise RuntimeError("无法绑定端口 %d-%d：%s" % (port, port + 29, last_error))
