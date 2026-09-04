# -*- coding: utf-8 -*-
"""持仓估值与盈亏计算。"""

from . import datasource, store


def _safe_div(a, b):
    return (a / b) if b else 0.0


def evaluate_portfolio(ttl=5):
    """把持仓 + 实时行情合并成可直接渲染的结构。

    支持做空（direction='short'）：做空时价格下跌为盈利。
    """
    data = store.load_portfolio()
    positions = data.get("positions", [])
    watchlist = data.get("watchlist", [])

    symbols = [p["symbol"] for p in positions] + [w["symbol"] for w in watchlist]
    quotes = datasource.get_quotes(symbols, ttl=ttl) if symbols else {}

    rows = []
    total_mv = 0.0
    total_cost = 0.0
    total_day_pnl = 0.0
    stale = []

    for pos in positions:
        sym = pos["symbol"]
        quote = quotes.get(sym)
        shares = float(pos.get("shares") or 0)
        cost = float(pos.get("cost") or 0)
        direction = pos.get("direction", "long")
        sign = -1.0 if direction == "short" else 1.0

        if not quote:
            stale.append(sym)
            price = cost
            prev_close = cost
            change = 0.0
            change_pct = 0.0
            name = pos.get("name") or sym
            quote_time = ""
            source = "-"
        else:
            price = quote["price"]
            prev_close = quote["prev_close"] or price
            change = quote["change"]
            change_pct = quote["change_pct"]
            name = quote["name"] or pos.get("name") or sym
            quote_time = quote["time"]
            source = quote["source"]

        cost_value = shares * cost
        market_value = shares * price
        # 做多：市值 - 成本；做空：成本 - 市值
        pnl = sign * (market_value - cost_value)
        day_pnl = sign * shares * change

        total_mv += market_value
        total_cost += cost_value
        total_day_pnl += day_pnl

        rows.append({
            "id": pos["id"],
            "symbol": sym,
            "name": name,
            "direction": direction,
            "shares": shares,
            "cost": cost,
            "price": price,
            "prev_close": prev_close,
            "change": change,
            "change_pct": change_pct,
            "cost_value": cost_value,
            "market_value": market_value,
            "pnl": pnl,
            "pnl_pct": _safe_div(pnl, abs(cost_value)) * 100.0,
            "day_pnl": day_pnl,
            "note": pos.get("note", ""),
            "quote_time": quote_time,
            "source": source,
            "week52_high": quote.get("week52_high") if quote else 0.0,
            "week52_low": quote.get("week52_low") if quote else 0.0,
        })

    for row in rows:
        row["weight"] = _safe_div(row["market_value"], total_mv) * 100.0

    # 持仓明细按当日涨跌幅降序（标的自身涨跌幅，与方向无关）
    rows.sort(key=lambda r: (r["change_pct"], r["market_value"]), reverse=True)

    watch_rows = []
    for item in watchlist:
        sym = item["symbol"]
        quote = quotes.get(sym)
        if not quote:
            watch_rows.append({"symbol": sym, "name": item.get("name") or sym,
                               "price": 0.0, "change": 0.0, "change_pct": 0.0,
                               "quote_time": "", "ok": False})
            continue
        watch_rows.append({
            "symbol": sym,
            "name": quote["name"],
            "price": quote["price"],
            "change": quote["change"],
            "change_pct": quote["change_pct"],
            "high": quote["high"],
            "low": quote["low"],
            "quote_time": quote["time"],
            "ok": True,
        })
    watch_rows.sort(key=lambda r: r["change_pct"], reverse=True)

    total_pnl = total_mv - total_cost
    # 分方向重算总盈亏（做空仓位需要反向）
    total_pnl = sum(r["pnl"] for r in rows)

    winners = [r for r in rows if r["pnl"] > 0]
    losers = [r for r in rows if r["pnl"] < 0]

    summary = {
        "market_value": total_mv,
        "cost_value": total_cost,
        "pnl": total_pnl,
        "pnl_pct": _safe_div(total_pnl, total_cost) * 100.0,
        "day_pnl": total_day_pnl,
        "day_pnl_pct": _safe_div(total_day_pnl, total_mv - total_day_pnl) * 100.0,
        "position_count": len(rows),
        "winner_count": len(winners),
        "loser_count": len(losers),
        "best": max(rows, key=lambda r: r["pnl_pct"])["symbol"] if rows else "",
        "worst": min(rows, key=lambda r: r["pnl_pct"])["symbol"] if rows else "",
        "best_day": max(rows, key=lambda r: r["day_pnl"])["symbol"] if rows else "",
        "worst_day": min(rows, key=lambda r: r["day_pnl"])["symbol"] if rows else "",
        "stale_symbols": stale,
    }

    return {"summary": summary, "positions": rows, "watchlist": watch_rows,
            "updated_at": data.get("updated_at", "")}
