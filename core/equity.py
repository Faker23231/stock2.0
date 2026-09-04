# -*- coding: utf-8 -*-
"""资产净值曲线回溯重建（按确认的买入时间）。

原来曲线只有稀疏的真实快照点（store.load_snapshots，通常几天一个），本模块在
快照之上按每支持仓的结构化 buy_date 从「最早建仓日」起逐交易日重算
market_value / cost_value / pnl / day_pnl，形成连续的净值序列：

- 市值      = Σ shares × 当日 qfq 收盘价（腾讯前复权日线，与实时估值同口径）
- 成本基准  = Σ 当日已持有 shares × 成本价（新买点出现时台阶式抬升）
- pnl       = 市值 - 成本基准（做空方向取反，与 valuation 一致）
- day_pnl   = 仅计价格波动：Σ 隔日已持有 shares×(今收-昨收)，买入首日记 0，
              避免「新增资金抬升市值」被误算成当日盈亏

真实快照（如 8/21、8/24）会按日期覆盖重建值作为锚点；最后一点用当天的
实时估值封口（valuation.evaluate_portfolio），这样刚买入的新标的也能入图。
"""

import threading
import time

from . import datasource, store, valuation

_LOCK = threading.Lock()
_CACHE = {"key": None, "data": None, "ts": 0.0}
_MAX_AGE = 60.0             # 重建结果短缓存，持仓/日期变化即失效
_KLINE_COUNT = 1000         # 约 4 年日线，足够覆盖最早建仓 2025-09-24

_FIELD_ORDER = ("date", "market_value", "cost_value", "pnl", "day_pnl",
                "actual", "saved_at")


def _fmt(value):
    return round(float(value or 0.0), 2)


def _today():
    return time.strftime("%Y-%m-%d")


def _sign_of(pos):
    return -1.0 if pos.get("direction") == "short" else 1.0


def _positions_fingerprint(positions):
    """持仓指纹：symbol/方向/股数/成本/buy_date/note 变化即强制重算。"""
    parts = []
    for p in sorted(positions,
                    key=lambda x: (x.get("symbol", ""), x.get("direction", "long"))):
        parts.append("%s|%s|%s|%s|%s|%s" % (
            p.get("symbol", ""),
            p.get("direction", "long"),
            repr(float(p.get("shares") or 0)),
            repr(float(p.get("cost") or 0)),
            p.get("buy_date", "") or "",
            (p.get("note") or "")[:60]))
    return "|".join(parts)


def _load_bars(symbol):
    """拉 qfq 日线 → (date->close 映射, 升序日期列表)；失败返回 (None, None)。"""
    rows = datasource.get_daily_kline(symbol, _KLINE_COUNT)
    if not rows:
        return None, None
    closes = {}
    for b in rows:
        if b.get("date") and b.get("close"):
            closes[b["date"]] = float(b["close"])
    dates = sorted(closes.keys())
    return (closes, dates) if dates else (None, None)


def _legacy_payload(snap_payload, today, reason):
    """重建不可行时退回原始快照（保持 /api/snapshots 旧契约）。"""
    items = snap_payload.get("items", [])
    return {
        "items": items,
        "rebuilt": False,
        "note": reason,
        "first_date": items[0]["date"] if items else "",
        "last_date": items[-1]["date"] if items else "",
        "as_of": today,
        "anchors": len(items),
    }


def _build(positions, today, snap_payload):
    # ---- 1. 每只标的分辨进场起点（buy_date 之后的第一个交易日）----
    pos_infos = []
    any_bars = False
    missing_hist = []
    for p in positions:
        closes, dates = _load_bars(p["symbol"])
        if not dates:
            missing_hist.append(p["symbol"])
            continue
        any_bars = True
        buy_date = (p.get("buy_date") or today) or "9999-12-31"
        start = next((d for d in dates if d >= buy_date), None)
        if start is None:
            # 买点之后还没有 K 线（典型：今天刚买，当日未收盘）→ 交给实时锚点
            continue
        pos_infos.append({
            "symbol": p["symbol"],
            "shares": float(p.get("shares") or 0),
            "cost": float(p.get("cost") or 0),
            "sign": _sign_of(p),
            "start": start,
            "closes": closes,
            "dates": dates,
        })

    if not any_bars:
        return _legacy_payload(snap_payload, today,
                               "行情接口不可用，暂时显示原始快照")
    if not pos_infos:
        return _legacy_payload(snap_payload, today,
                               "当前持仓的买入日均晚于最近K线（如当日新建），"
                               "暂时显示原始快照")

    # ---- 2. 日期轴 = 已进场标的的交易日并集，从最早 buy 起点开始 ----
    all_dates = set()
    for pi in pos_infos:
        all_dates.update(pi["dates"])
    start0 = min(pi["start"] for pi in pos_infos)
    dates = sorted(d for d in all_dates if d >= start0)

    # ---- 3. 逐交易日重算（K线缺口用前收盘前向填充）----
    prev_close = {}          # symbol -> 上一交易日有效收盘
    active = {}              # symbol -> pos_info（到买入日才激活）
    rows_by_date = {}
    for d in dates:
        for pi in pos_infos:
            if pi["start"] <= d and pi["symbol"] not in active:
                active[pi["symbol"]] = pi
        mv = 0.0
        cost = 0.0
        pnl = 0.0
        day = 0.0
        for sym, pi in active.items():
            close = pi["closes"].get(d)
            if close is None:
                close = prev_close.get(sym)
            if close is None:
                continue
            prev = prev_close.get(sym)
            mv += pi["shares"] * close
            cost += pi["shares"] * pi["cost"]
            pnl += pi["sign"] * (pi["shares"] * close - pi["shares"] * pi["cost"])
            if prev is not None:
                day += pi["sign"] * pi["shares"] * (close - prev)
            prev_close[sym] = close
        rows_by_date[d] = {
            "date": d,
            "market_value": _fmt(mv),
            "cost_value": _fmt(cost),
            "pnl": _fmt(pnl),
            "day_pnl": _fmt(day),
            "actual": False,
            "saved_at": "",
        }

    # ---- 4. 真实快照锚点覆盖（严格早于今天；今天由实时封口接管）----
    anchors = 0
    for item in snap_payload.get("items", []):
        d = item.get("date")
        if not d or d >= today or d < start0:
            continue
        if d in rows_by_date:
            rows_by_date[d].update({
                "market_value": _fmt(item.get("market_value")),
                "cost_value": _fmt(item.get("cost_value")),
                "pnl": _fmt(item.get("pnl")),
                "day_pnl": _fmt(item.get("day_pnl")),
                "actual": True,
                "saved_at": item.get("saved_at", ""),
            })
            anchors += 1
        else:
            # 快照落在非交易日（罕见），单独插入
            rows_by_date[d] = {
                "date": d,
                "market_value": _fmt(item.get("market_value")),
                "cost_value": _fmt(item.get("cost_value")),
                "pnl": _fmt(item.get("pnl")),
                "day_pnl": _fmt(item.get("day_pnl")),
                "actual": True,
                "saved_at": item.get("saved_at", ""),
            }
            anchors += 1

    # ---- 5. 今天用实时估值封口（含当日新建、尚无 K 线的标的）----
    try:
        summary = valuation.evaluate_portfolio(ttl=5)["summary"]
        live = {
            "date": today,
            "market_value": _fmt(summary.get("market_value")),
            "cost_value": _fmt(summary.get("cost_value")),
            "pnl": _fmt(summary.get("pnl")),
            "day_pnl": _fmt(summary.get("day_pnl")),
            "actual": True,
            "saved_at": time.strftime("%Y-%m-%d %H:%M:%S"),
        }
        rows_by_date[today] = live
        anchors += 1
    except Exception:
        # 实时估值失败：今天若已在轴内（有当日K线）则保留重建值
        pass

    items = [rows_by_date[d] for d in sorted(rows_by_date)]
    if not items:
        return _legacy_payload(snap_payload, today, "未能生成净值序列")

    first_buy = min(pi["start"] for pi in pos_infos)
    note = ("按确认买入时间回溯重建：最早建仓 %s，共 %d 个交易日；"
            "圆点为真实快照锚点" % (first_buy, len(items)))
    if missing_hist:
        note += "；无K线仅按实时价计入：%s" % "、".join(missing_hist)

    return {
        "items": items,
        "rebuilt": True,
        "note": note,
        "first_date": items[0]["date"],
        "last_date": items[-1]["date"],
        "as_of": today,
        "anchors": anchors,
    }


def build_equity_series(force=False, max_age=_MAX_AGE):
    """入口：返回 {"ok": True, "data": {...items/rebuilt/note...}}。

    持仓指纹或日期变化时强制重算，否则复用最近 max_age 秒的结果，
    避免高频轮询反复拉一年 K 线。
    """
    data = store.load_portfolio()
    positions = [p for p in data.get("positions", [])
                 if float(p.get("shares") or 0) > 0]
    today = _today()
    snap_payload = store.load_snapshots()
    key = "%s|%s" % (_positions_fingerprint(positions), today)

    with _LOCK:
        if (not force and _CACHE["data"] and _CACHE["key"] == key
                and (time.time() - _CACHE["ts"]) < max_age):
            return {"ok": True, "data": _CACHE["data"]}

    payload = _build(positions, today, snap_payload)
    with _LOCK:
        _CACHE["key"] = key
        _CACHE["data"] = payload
        _CACHE["ts"] = time.time()
    return {"ok": True, "data": payload}
