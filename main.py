# -*- coding: utf-8 -*-
"""美股持仓管理平台 —— 本地启动入口。

直接运行：python main.py
打包 exe：python build.py
"""

import argparse
import os
import sys
import threading
import time
import webbrowser

# 让 PyInstaller 打包后 / 源码运行都能 import core
if getattr(sys, "frozen", False):
    _ROOT = os.path.dirname(sys.executable)
    # PyInstaller windowed(无控制台) 模式下 stdout/stderr 为 None，
    # 重定向到日志文件，避免 print 抛错导致闪退
    if sys.stdout is None or sys.stderr is None:
        try:
            _log_dir = os.path.join(_ROOT, "logs")
            os.makedirs(_log_dir, exist_ok=True)
            _logf = open(os.path.join(_log_dir, "console.log"),
                         "a", encoding="utf-8", buffering=1)
            sys.stdout = sys.stderr = _logf
        except Exception:
            pass
else:
    _ROOT = os.path.dirname(os.path.abspath(__file__))
    if _ROOT not in sys.path:
        sys.path.insert(0, _ROOT)

from core import paths, scheduler, server, store  # noqa: E402

BANNER = r"""
 ==========================================================
   美股持仓管理平台  US Stock Portfolio & Premarket Signal
 ==========================================================
"""


def _open_browser_later(url, delay=1.2):
    def worker():
        time.sleep(delay)
        try:
            webbrowser.open(url)
        except Exception:
            pass
    threading.Thread(target=worker, daemon=True).start()


def main():
    parser = argparse.ArgumentParser(description="美股持仓管理平台")
    parser.add_argument("--port", type=int, default=8756, help="监听端口，默认 8756")
    parser.add_argument("--host", default="127.0.0.1", help="监听地址，默认仅本机")
    parser.add_argument("--no-browser", action="store_true", help="启动后不自动打开浏览器")
    parser.add_argument("--no-scheduler", action="store_true", help="不启动定时任务")
    args = parser.parse_args()

    print(BANNER)

    # 首次运行时把默认配置落盘，顺便把 data / reports 目录建出来
    cfg = store.load_config()
    store.save_config({})

    print(" 数据目录 : %s" % paths.data_dir())
    print(" 报告目录 : %s" % paths.reports_dir())
    print(" 静态资源 : %s" % paths.web_dir())

    if not os.path.isfile(os.path.join(paths.web_dir(), "index.html")):
        print("\n [错误] 找不到 web/index.html，静态资源缺失，无法启动界面。")
        input(" 按回车退出…")
        return 1

    httpd, port = server.create_server(args.host, args.port)
    url = "http://%s:%d/" % ("127.0.0.1" if args.host == "0.0.0.0" else args.host, port)

    if not args.no_scheduler:
        scheduler.scheduler.start()
        print(" 定时任务 : 每日日报 %s（%s）｜盘前报告 %s（%s）" % (
            cfg.get("daily_report_time"),
            "开" if cfg.get("auto_report") else "关",
            cfg.get("premarket_report_time"),
            "开" if cfg.get("auto_premarket") else "关"))
    else:
        print(" 定时任务 : 已禁用")

    print(" 服务地址 : %s" % url)
    print(" 关闭窗口或按 Ctrl+C 即可退出。\n")

    if not args.no_browser:
        _open_browser_later(url)

    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\n 正在退出…")
    finally:
        try:
            scheduler.scheduler.stop()
        except Exception:
            pass
        httpd.server_close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
