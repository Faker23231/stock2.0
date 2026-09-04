# -*- coding: utf-8 -*-
"""后台定时任务：每天在指定时间自动生成日报 / 盘前报告并推送邮件。

实现刻意简单：一个守护线程每 20 秒检查一次「当前 HH:MM 是否等于配置时间」，
并用 data/schedule_state.json 记录当天是否已执行，避免重复触发。
"""

import json
import os
import threading
import time
import traceback

from . import paths, report, store

_STATE_FILE = "schedule_state.json"
_LOG_LIMIT = 60


class Scheduler(object):
    def __init__(self):
        self._thread = None
        self._stop = threading.Event()
        self.logs = []
        self._lock = threading.Lock()

    # ---------------- 状态持久化 ----------------
    def _state_path(self):
        return paths.data_file(_STATE_FILE)

    def _load_state(self):
        path = self._state_path()
        if not os.path.isfile(path):
            return {}
        try:
            with open(path, "r", encoding="utf-8") as fh:
                return json.load(fh)
        except Exception:
            return {}

    def _save_state(self, state):
        try:
            with open(self._state_path(), "w", encoding="utf-8") as fh:
                json.dump(state, fh, ensure_ascii=False, indent=2)
        except Exception:
            pass

    def _log(self, message):
        entry = {"time": time.strftime("%Y-%m-%d %H:%M:%S"), "message": message}
        with self._lock:
            self.logs.insert(0, entry)
            del self.logs[_LOG_LIMIT:]

    def get_logs(self):
        with self._lock:
            return list(self.logs)

    # ---------------- 主循环 ----------------
    def _run_job(self, kind, state_key, state):
        try:
            built = report.generate_and_send(kind=kind)
            note = "已生成 %s" % built["filename"]
            if built.get("email_ok"):
                note += "，邮件%s" % built.get("email_message", "已发送")
            elif built.get("email_message"):
                note += "，邮件未发送（%s）" % built["email_message"]
            self._log(note)
        except Exception as exc:
            self._log("任务失败：%s" % exc)
            traceback.print_exc()
        finally:
            state[state_key] = time.strftime("%Y-%m-%d")
            self._save_state(state)

    def _loop(self):
        self._log("定时任务已启动")
        while not self._stop.is_set():
            try:
                cfg = store.load_config()
                state = self._load_state()
                today = time.strftime("%Y-%m-%d")
                now_hm = time.strftime("%H:%M")

                if cfg.get("auto_report"):
                    target = (cfg.get("daily_report_time") or "").strip()
                    if target == now_hm and state.get("daily_done") != today:
                        self._log("触发每日日报（%s）" % target)
                        self._run_job("daily", "daily_done", state)

                if cfg.get("auto_premarket"):
                    target = (cfg.get("premarket_report_time") or "").strip()
                    if target == now_hm and state.get("premarket_done") != today:
                        self._log("触发盘前报告（%s）" % target)
                        self._run_job("premarket", "premarket_done", state)
            except Exception as exc:
                self._log("调度异常：%s" % exc)
            self._stop.wait(20)

    def start(self):
        if self._thread and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._loop, name="scheduler",
                                        daemon=True)
        self._thread.start()

    def stop(self):
        self._stop.set()

    def next_runs(self):
        cfg = store.load_config()
        state = self._load_state()
        today = time.strftime("%Y-%m-%d")
        return {
            "daily": {
                "enabled": bool(cfg.get("auto_report")),
                "time": cfg.get("daily_report_time"),
                "done_today": state.get("daily_done") == today,
            },
            "premarket": {
                "enabled": bool(cfg.get("auto_premarket")),
                "time": cfg.get("premarket_report_time"),
                "done_today": state.get("premarket_done") == today,
            },
        }


scheduler = Scheduler()
