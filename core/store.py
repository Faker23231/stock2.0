# -*- coding: utf-8 -*-
"""持仓 / 自选 / 配置 / 净值快照的本地 JSON 存储。

所有写操作都加锁 + 原子替换，避免定时任务和用户操作同时写坏文件。
"""

import json
import os
import threading
import time
import uuid

from . import paths

_LOCK = threading.RLock()

DEFAULT_CONFIG = {
    "refresh_seconds": 8,          # 前端轮询间隔
    "usd_cny": 7.1,                # 汇率，仅用于展示折算
    "daily_report_time": "07:30",  # 本地时间，每日日报生成时刻
    "auto_report": True,
    "premarket_report_time": "20:30",  # 美股开盘前（夏令时 21:30 开盘）
    "auto_premarket": True,
    "email": {
        "enabled": False,
        "smtp_host": "smtp.qq.com",
        "smtp_port": 465,
        "use_ssl": True,
        "username": "",
        "password": "",          # QQ 邮箱请填授权码，不是登录密码
        "sender": "",
        "receivers": [],
    },
    "macro_symbols": {
        "原油": ["USO", "BNO", "XLE", "XOP", "OIH"],
        "黄金": ["GLD", "IAU", "GDX", "SLV"],
        "国债": ["TLT", "IEF", "SHY", "TBT"],
        "银行": ["XLF", "KBE", "KRE", "JPM", "GS"],
        "半导体": ["SMH", "SOXX", "NVDA", "AMD", "AVGO"],
        "纳指100": ["QQQ", "SPY", "IWM"],
        "防御消费": ["KO", "PEP", "XLP"],
        "地缘军工": ["ITA", "XAR", "LMT", "RTX"],
        "波动率": ["VIXY", "UVXY"],
    },
}


def _read_json(path, default):
    if not os.path.isfile(path):
        return json.loads(json.dumps(default))
    try:
        with open(path, "r", encoding="utf-8") as fh:
            return json.load(fh)
    except Exception:
        # 文件损坏时备份一份再返回默认值，避免用户数据被静默清空
        try:
            os.replace(path, path + ".broken.%d" % int(time.time()))
        except Exception:
            pass
        return json.loads(json.dumps(default))


def _write_json(path, payload):
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(payload, fh, ensure_ascii=False, indent=2)
    os.replace(tmp, path)


# --------------------------------------------------------------------------
# 配置
# --------------------------------------------------------------------------
def _config_path():
    return paths.data_file("config.json")


def _deep_merge(base, override):
    out = json.loads(json.dumps(base))
    for key, value in (override or {}).items():
        if isinstance(value, dict) and isinstance(out.get(key), dict):
            out[key] = _deep_merge(out[key], value)
        else:
            out[key] = value
    return out


def load_config():
    with _LOCK:
        stored = _read_json(_config_path(), {})
        return _deep_merge(DEFAULT_CONFIG, stored)


def save_config(patch):
    with _LOCK:
        current = load_config()
        merged = _deep_merge(current, patch or {})
        _write_json(_config_path(), merged)
        return merged


# --------------------------------------------------------------------------
# 持仓
# --------------------------------------------------------------------------
def _portfolio_path():
    return paths.data_file("portfolio.json")


def _default_portfolio():
    return {"positions": [], "watchlist": [], "updated_at": ""}


def load_portfolio():
    with _LOCK:
        data = _read_json(_portfolio_path(), _default_portfolio())
        data.setdefault("positions", [])
        data.setdefault("watchlist", [])
        return data


def _save_portfolio(data):
    data["updated_at"] = time.strftime("%Y-%m-%d %H:%M:%S")
    _write_json(_portfolio_path(), data)
    return data


def add_position(symbol, shares, cost, name="", note="", direction="long"):
    """新增或合并持仓。同一代码同方向自动按加权平均成本合并。"""
    symbol = (symbol or "").strip().upper()
    if not symbol:
        raise ValueError("代码不能为空")
    shares = float(shares)
    cost = float(cost)
    if shares <= 0:
        raise ValueError("数量必须大于 0")
    if cost < 0:
        raise ValueError("成本价不能为负")

    with _LOCK:
        data = load_portfolio()
        for pos in data["positions"]:
            if pos["symbol"] == symbol and pos.get("direction", "long") == direction:
                old_shares = float(pos["shares"])
                old_cost = float(pos["cost"])
                total = old_shares + shares
                pos["shares"] = total
                pos["cost"] = (old_shares * old_cost + shares * cost) / total if total else cost
                if note:
                    pos["note"] = note
                pos["updated_at"] = time.strftime("%Y-%m-%d %H:%M:%S")
                return _save_portfolio(data)

        data["positions"].append({
            "id": uuid.uuid4().hex[:12],
            "symbol": symbol,
            "name": name or symbol,
            "shares": shares,
            "cost": cost,
            "direction": direction,
            "note": note,
            "added_at": time.strftime("%Y-%m-%d %H:%M:%S"),
            "updated_at": time.strftime("%Y-%m-%d %H:%M:%S"),
        })
        return _save_portfolio(data)


def update_position(pos_id, **fields):
    with _LOCK:
        data = load_portfolio()
        for pos in data["positions"]:
            if pos["id"] == pos_id:
                for key in ("shares", "cost"):
                    if key in fields and fields[key] is not None:
                        pos[key] = float(fields[key])
                for key in ("note", "name", "direction"):
                    if key in fields and fields[key] is not None:
                        pos[key] = fields[key]
                pos["updated_at"] = time.strftime("%Y-%m-%d %H:%M:%S")
                return _save_portfolio(data)
        raise KeyError("持仓不存在: %s" % pos_id)


def delete_position(pos_id):
    with _LOCK:
        data = load_portfolio()
        before = len(data["positions"])
        data["positions"] = [p for p in data["positions"] if p["id"] != pos_id]
        if len(data["positions"]) == before:
            raise KeyError("持仓不存在: %s" % pos_id)
        return _save_portfolio(data)


def add_watch(symbol, name=""):
    symbol = (symbol or "").strip().upper()
    if not symbol:
        raise ValueError("代码不能为空")
    with _LOCK:
        data = load_portfolio()
        for item in data["watchlist"]:
            if item["symbol"] == symbol:
                return data
        data["watchlist"].append({"symbol": symbol, "name": name or symbol,
                                  "added_at": time.strftime("%Y-%m-%d %H:%M:%S")})
        return _save_portfolio(data)


def delete_watch(symbol):
    symbol = (symbol or "").strip().upper()
    with _LOCK:
        data = load_portfolio()
        data["watchlist"] = [w for w in data["watchlist"] if w["symbol"] != symbol]
        return _save_portfolio(data)


# --------------------------------------------------------------------------
# 每日净值快照（日报里做环比用）
# --------------------------------------------------------------------------
def _snapshot_path():
    return paths.data_file("snapshots.json")


def load_snapshots():
    with _LOCK:
        data = _read_json(_snapshot_path(), {"items": []})
        data.setdefault("items", [])
        return data


def save_snapshot(date_str, market_value, cost_value, pnl, day_pnl):
    with _LOCK:
        data = load_snapshots()
        items = [i for i in data["items"] if i.get("date") != date_str]
        items.append({
            "date": date_str,
            "market_value": round(market_value, 2),
            "cost_value": round(cost_value, 2),
            "pnl": round(pnl, 2),
            "day_pnl": round(day_pnl, 2),
            "saved_at": time.strftime("%Y-%m-%d %H:%M:%S"),
        })
        items.sort(key=lambda x: x["date"])
        data["items"] = items[-400:]
        _write_json(_snapshot_path(), data)
        return data
