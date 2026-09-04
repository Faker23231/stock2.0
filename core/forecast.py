# -*- coding: utf-8 -*-
"""持仓未来走势前瞻 —— 基于历史日线的「移动块 Bootstrap」情景模拟。

为什么选这个算法：
1. 不试图“预测某天涨到哪”，而是把标的近一年的真实日涨跌重放成
   大量“如果历史重演”的未来路径，直接给出收益区间与上涨概率——
   短中期价格本身不可精确预测，但收益分布可以估计；
2. 采用块长 5 的移动块重抽样（block bootstrap），保留日收益的短期
   自相关（波动聚集、趋势惯性），比逐日独立重抽样更贴近真实序列；
3. 区间取自 5000 条路径的经验分位数，不假设正态分布，能体现肥尾，
   悲观/乐观情形不会像“均值 ± 2σ”那样被低估。

局限：纯价格统计模型，不含基本面/事件预期；方向命中率有限，
用途是辅助仓位决策与设置纪律性止损参考，不构成投资建议。
"""

import bisect
import math
import random
import statistics
import threading
import time
import zlib

from . import datasource, valuation

HORIZONS = (21, 63)        # 向前模拟的交易日数：约 1 个月 / 3 个月
HORIZON_LABELS = {21: "1 个月", 63: "3 个月"}
BLOCK = 5                  # 移动块长度（保留短期自相关）
N_SIMS = 5000              # 模拟路径条数
KLINE_COUNT = 320          # 拉取的日线根数（约 15 个月，够截取一年窗口）
MAX_RETS = 260             # 实际用于统计的日收益率上限（近一年交易日）
MIN_CLOSES = 60            # 少于该样本数不给区间，只提示数据不足

MODEL = ("对数收益 · 移动块 Bootstrap（块长 %d）× %d 条路径；"
         "样本 = 近一年日收益率经验分布，悲观/中性/乐观 = 路径 P10/P50/P90 分位，"
         "不假设正态分布，保留波动聚集与肥尾。" % (BLOCK, N_SIMS))

DISCLAIMER = ("以上为基于历史价格的统计情景（模拟区间），不含基本面与突发消息，"
              "短期方向命中率有限；请把它当作仓位与止损纪律的参考，而非买卖依据。")

_lock = threading.Lock()
_cache = {"key": None, "data": None, "ts": 0.0}


def _sig_pct(v):
    return (v * 100.0) if v else 0.0


def _build_key(positions):
    """持仓指纹：symbol+direction+shares+cost，变化即强制重算。"""
    parts = []
    for p in sorted(positions,
                    key=lambda x: (x.get("symbol") or "", x.get("direction", "long"))):
        parts.append("%s|%s|%s|%s" % (
            p.get("symbol"), p.get("direction", "long"),
            p.get("shares"), p.get("cost")))
    return ";".join(parts)


def _log_rets(closes):
    """日对数收益率序列；过滤脏点（数据源除权/拆股异常等）。"""
    out = []
    for prev, cur in zip(closes, closes[1:]):
        if prev > 0 and cur > 0:
            r = math.log(cur / prev)
            if -0.6 < r < 0.6:
                out.append(r)
    return out


def _simulate(rets, horizon, rng):
    """移动块 bootstrap：返回 N_SIMS 条未来 horizon 日的累计对数收益。"""
    n = len(rets)
    if n >= BLOCK * 3:
        blocks = [rets[i:i + BLOCK] for i in range(0, n - BLOCK + 1)]
    else:
        blocks = [[x] for x in rets]
    ends = []
    nb = len(blocks)
    blen = len(blocks[0]) if blocks else 0
    for _ in range(N_SIMS):
        left = horizon
        acc = 0.0
        while left > 0:
            b = blocks[rng.randrange(nb)]
            if left >= blen:
                if blen == 5:
                    acc += b[0] + b[1] + b[2] + b[3] + b[4]
                else:
                    acc += math.fsum(b)
                left -= blen
            else:
                acc += math.fsum(b[:left])
                left = 0
        ends.append(acc)
    return ends


def _q(sorted_vals, q):
    if not sorted_vals:
        return 0.0
    return sorted_vals[int(q * (len(sorted_vals) - 1))]


def _vol_grade(v):
    if v < 20:
        return "低"
    if v < 35:
        return "中"
    if v < 55:
        return "高"
    return "极高"


def _horizon_block(rets, horizon, rng):
    """对单个 horizon 做模拟并返回展示用的数字。"""
    paths = _simulate(rets, horizon, rng)
    paths.sort()
    p5 = _q(paths, 0.05)
    p10 = _q(paths, 0.10)
    p50 = _q(paths, 0.50)
    p90 = _q(paths, 0.90)
    p95 = _q(paths, 0.95)
    up = bisect.bisect_right(paths, 0.0) / float(len(paths)) * 100.0
    return {
        "days": horizon,
        "label": HORIZON_LABELS.get(horizon, "%d 天" % horizon),
        "p5": _sig_pct(p5), "p10": _sig_pct(p10), "p50": _sig_pct(p50),
        "p90": _sig_pct(p90), "p95": _sig_pct(p95),
        "up_prob": up,
    }


def _trend_pack(closes):
    """返回技术面短标签 + 方向。"""
    last = closes[-1]
    ma20 = datasource.sma(closes, 20)
    ma50 = datasource.sma(closes, 50)
    chg20 = (closes[-1] / closes[-1 - 20] - 1.0) * 100.0 if len(closes) > 20 else None
    rsi = datasource.rsi(closes, 14)
    parts = []
    above20 = bool(ma20 and last >= ma20)
    above50 = bool(ma50 and last >= ma50)
    up_struct = bool(ma20 and ma50 and ma20 > ma50)
    down_struct = bool(ma20 and ma50 and ma20 < ma50)
    if up_struct:
        parts.append("多头排列、价在 MA20 上方" if above20 else "均线多头但价回落至 MA20 下")
    elif down_struct:
        parts.append("空头排列、价在 MA50 下方" if not above50 else "空头结构中的反弹（价在 MA50 上）")
    else:
        parts.append("均线纠缠，" + ("价在 MA50 上方偏强" if above50 else "价在 MA50 下方偏弱"))
    if rsi is not None:
        if rsi >= 70:
            parts.append("短线超买（RSI %.0f）" % rsi)
        elif rsi <= 30:
            parts.append("短线超卖（RSI %.0f）" % rsi)
        else:
            parts.append("RSI %.0f" % rsi)
    if chg20 is not None:
        parts.append("近 20 日 %+.1f%%" % chg20)
    if above20 and above50:
        direction = 1
    elif (not above20) and (not above50):
        direction = -1
    else:
        direction = 0
    return "；".join(parts), direction


def _price_band(price, pct):
    return price * (1.0 + pct / 100.0)


def _note(row, res):
    """文字讲解：把数字翻译成“接下来该注意什么”。"""
    long_pos = (row.get("direction", "long") != "short")
    word = "做多" if long_pos else "做空"
    sym = row["symbol"]
    name = row.get("name") or sym
    price = float(row.get("price") or 0)
    weight = float(row.get("weight") or 0)
    h1, h2 = res["horizons"][0], res["horizons"][1]

    note = []
    note.append(
        "%s（%s）现价 %.2f。把近 %d 个交易日的真实日涨跌重放 %d 条路径后："
        "未来 1 个月中性情形约 %+.1f%%，10%% 分位的悲观情形 %+.1f%%"
        "（对应约 $%.2f），乐观情形 %+.1f%%（约 $%.2f），上涨概率 %.0f%%；"
        "3 个月中性 %+.1f%%，区间 %+.1f%% ~ %+.1f%%，上涨概率 %.0f%%。"
        % (name, sym, price, res["window"], N_SIMS,
           h1["p50"], h1["p10"], _price_band(price, h1["p10"]),
           h1["p90"], _price_band(price, h1["p90"]), h1["up_prob"],
           h2["p50"], h2["p10"], h2["p90"], h2["up_prob"]))

    if not long_pos:
        note.append("你是做空仓位：上述都是标的价格情景，标的下跌才对你不利反向有利，"
                    "对应到你的盈亏方向要整体反过来看。")

    note.append(
        "波动画像：年化波动率约 %.0f%%（%s波动），历史上单日 95%% 情形回撤不超过约 %.2f%%。"
        % (res["ann_vol"], res["vol_grade"], res["var1d"]))

    if res["trend_txt"]:
        note.append("技术面：%s。" % res["trend_txt"])

    # 纪律参考线（只给一条最实际的）
    if long_pos:
        ref_pct = h1["p10"]
        ref_px = _price_band(price, h1["p10"])
        note.append("参考纪律：未来 1 个月若跌破悲观价 $%.2f（约 %+.1f%%），"
                    "说明模拟区间失效，可考虑按计划减仓而不是继续补仓摊薄。" % (ref_px, ref_pct))
    else:
        ref_pct = h1["p90"]
        ref_px = _price_band(price, h1["p90"])
        note.append("参考纪律：做空仓位若遇到标的 1 个月涨过乐观价 $%.2f（约 %+.1f%%），"
                    "说明反向行情启动，应优先执行止损。" % (ref_px, ref_pct))

    if weight >= 30:
        note.append("注意集中度：该持仓占总仓位 %.0f%%，单一标的波动对组合影响大，"
                    "建议分批了结把权重压回 30%% 以内。" % weight)
    return "".join(note)


def _analyze(row):
    """对单只持仓做完整分析；行情/样本不足时返回 ok=False 的说明项。"""
    sym = row["symbol"]
    bars = datasource.get_daily_kline(sym, KLINE_COUNT)
    closes = [b["close"] for b in (bars or []) if (b.get("close") or 0) > 0]
    if not closes:
        return {"ok": False, "symbol": sym, "error": "未取到日线行情（网络或代码不可用）"}
    if len(closes) < MIN_CLOSES:
        return {"ok": False, "symbol": sym,
                "error": "历史日线仅 %d 根（不足 %d 根），样本太少，暂不给出模拟区间。"
                         % (len(closes), MIN_CLOSES)}
    rets = _log_rets(closes)
    if len(rets) < MIN_CLOSES - 1:
        return {"ok": False, "symbol": sym, "error": "有效日收益率样本不足。"}
    rets = rets[-MAX_RETS:]
    window = len(rets)

    seed = zlib.crc32(("%s|%s|%s" % (sym, window, bars[-1]["date"])).encode("utf-8"))
    rng = random.Random(seed)
    horizons = [_horizon_block(rets, h, rng) for h in HORIZONS]

    daily_sigma = statistics.stdev(rets) if len(rets) > 1 else 0.0
    ann_vol = daily_sigma * math.sqrt(252) * 100.0
    var1d = daily_sigma * 1.645 * 100.0
    trend_txt, trend_dir = _trend_pack(closes)

    # 把价格区间一起算好（以实时价/最新估值为基准）
    price = float(row.get("price") or closes[-1])
    for h in horizons:
        h["p5_price"] = _price_band(price, h["p5"])
        h["p10_price"] = _price_band(price, h["p10"])
        h["p50_price"] = _price_band(price, h["p50"])
        h["p90_price"] = _price_band(price, h["p90"])
        h["p95_price"] = _price_band(price, h["p95"])

    res = {
        "ok": True, "symbol": sym, "name": row.get("name") or sym,
        "direction": row.get("direction", "long"),
        "shares": float(row.get("shares") or 0),
        "cost": float(row.get("cost") or 0),
        "price": price,
        "weight": float(row.get("weight") or 0),
        "window": window,
        "window_end": bars[-1]["date"],
        "ann_vol": ann_vol,
        "vol_grade": _vol_grade(ann_vol),
        "var1d": var1d,
        "horizons": horizons,
        "trend_txt": trend_txt,
        "trend_dir": trend_dir,
    }
    res["note"] = _note(row, res)
    return res


def build_forecast(force=False, max_age=600):
    """入口：对当前持仓逐只生成前瞻报告。

    force=True 或持仓发生变化时全量重算；否则复用最近 max_age 秒内的结果，
    避免每次打开页面都去拉一年 K 线。
    """
    result = valuation.evaluate_portfolio(ttl=30)
    rows = result.get("positions", [])
    key = _build_key(rows)
    now = time.time()
    with _lock:
        if (not force and _cache["data"] and _cache["key"] == key
                and (now - _cache["ts"]) < max_age):
            return {"ok": True, "data": _cache["data"]}

    t0 = time.time()
    items = []
    for row in rows:
        try:
            item = _analyze(row)
        except Exception as exc:  # 单只失败不拖垮整体
            item = {"ok": False, "symbol": row.get("symbol"),
                    "name": row.get("name"), "error": "分析异常：" + str(exc)[:120]}
        items.append(item)

    data = {
        "empty": not rows,
        "generated_at": time.strftime("%Y-%m-%d %H:%M:%S"),
        "model": MODEL,
        "disclaimer": DISCLAIMER,
        "elapsed": round(time.time() - t0, 2),
        "items": items,
    }
    with _lock:
        _cache["key"] = key
        _cache["data"] = data
        _cache["ts"] = time.time()
    return {"ok": True, "data": data}
