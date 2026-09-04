# -*- coding: utf-8 -*-
"""今日交易文字讲解：把盘前信号 + 实时快讯翻译成「该做什么 / 不该做什么」。

输入：
  · analysis —— macro.build_premarket_analysis() 的结果（environment / signals / recommendations）
  · digest   —— news.get_news_digest() 的结果（按主题归类的海外快讯）

输出（纯文本规则生成，无黑箱）：
  brief = {
    "headline":  一句话给今天定调,
    "lead":      展开说明资金主线与操作总纲,
    "do":        [{tag, asset, symbol, how, why, cite, score}],   该做什么
    "dont":      [{tag, why, cite}],                               不该做什么
    "watch":     [{asset, score, why, trigger}],                   观望 / 等确认
    "risk":      波动与仓位风险提示,
    "news":      {total, matched, generated_at},
    "generated_at": ...,
  }

设计原则：每条建议尽量挂一条真实快讯做佐证（cite 字段），
宁可少说，不说没依据的话。
"""

import time

# 资产 -> 可引用的话题名（按优先级）
ASSET_TOPICS = {
    "原油": ["原油能源", "战争地缘"],
    "黄金": ["黄金贵金属", "战争地缘"],
    "半导体": ["半导体", "美股大盘"],
    "利率": ["美债利率", "美联储政策"],
    "地缘": ["战争地缘", "原油能源"],
    "防御": ["防御消费", "美股大盘"],
    "避险": ["防御消费", "黄金贵金属"],
}

# 波动衰减类杠杆工具，出现即提示不要过夜
LEVERAGED = ("UCO", "SCO", "SOXL", "SOXS", "NUGT", "DUST",
             "GUSH", "DRIP", "TBT", "TTT", "GLL", "SSG")


def _num(value, default=0.0):
    try:
        v = float(value)
    except (TypeError, ValueError):
        return default
    return v


def _pct(value):
    v = _num(value)
    return ("+%.1f%%" % v) if v >= 0 else ("%.1f%%" % v)


def _clip(text, limit=46):
    text = " ".join(str(text or "").split())
    return text if len(text) <= limit else text[:limit - 1] + "…"


def _top_news(digest, topic_names, limit=2):
    """按话题优先级取最近的快讯作为佐证。"""
    if not digest or not topic_names:
        return []
    topics = digest.get("topics") or []
    name_set = set(topic_names)
    found = []
    for topic in topics:                      # topics 已按条数降序
        if topic.get("topic") not in name_set:
            continue
        for item in (topic.get("items") or []):
            found.append({
                "source": item.get("source") or "",
                "time": item.get("time") or "",
                "title": item.get("title") or item.get("digest") or "",
            })
            if len(found) >= limit:
                return found
    return found


def _news_mix(digest, topic_names, limit=2):
    items = _top_news(digest, topic_names, limit)
    return items, "｜".join(
        ("%s %s「%s」" % (n["source"], n["time"], _clip(n["title"], 40)))
        for n in items)


def _top_factors(sig, k=3):
    """信号里贡献最大的几个因子（按 |分| 降序）。"""
    factors = sorted(sig.get("factors") or [],
                     key=lambda f: abs(_num(f.get("score"))), reverse=True)
    return factors[:k]


def _build_do_items(signals, recs, env, digest):
    """按信号强度列出「今天值得动手」的条目。"""
    do = []
    # 主题级建议（利率 / 地缘 / 防御）优先取第一条非「观望」记录
    thematic = {}
    for rec in recs or []:
        if rec.get("side") in ("做多", "做空") and rec.get("asset") not in thematic:
            thematic[rec["asset"]] = rec

    order = sorted([s for s in signals if s],
                   key=lambda s: abs(_num(s.get("score"))), reverse=True)
    for sig in order:
        asset = sig.get("asset")
        score = _num(sig.get("score"))
        if abs(score) < 15:
            continue                      # 中性，交给 watch
        rec = thematic.pop(asset, None)
        if not rec:
            continue
        tag = rec.get("side")
        symbol = rec.get("symbol") or ""
        top = _top_factors(sig, 3)
        why_bits = ["%s（%s）" % (f.get("name"), _clip(f.get("detail"), 34))
                    for f in top]
        why = ("综合得分 %s（%s / 置信度 %s）｜ 主要依据：%s"
               % (("+%.1f" % score) if score >= 0 else ("%.1f" % score),
                  sig.get("stance") or "—", sig.get("confidence") or "—",
                  "；".join(why_bits) if why_bits else "多因子综合"))
        how = sig.get("action") or tag
        if tag == "做多":
            how += "；分批建仓别一把梭，回踩不破再补，止损放结构下方"
        elif tag == "做空":
            how += "；等日内反弹乏力再进，不追跌，止损放反弹高点上方"
        news, cite = _news_mix(digest, ASSET_TOPICS.get(asset, []))
        do.append({"tag": tag, "asset": asset, "symbol": symbol,
                   "how": how, "why": why, "cite": cite, "score": score,
                   "evidence_count": len(news)})

    # 剩余的主题建议（利率 / 地缘 / 防御 / 避险），补在信号之后
    for asset, rec in thematic.items():
        if rec.get("asset") in ("利率", "地缘", "防御", "避险"):
            symbol = rec.get("symbol") or ""
            do.append({
                "tag": rec.get("side"),
                "asset": asset,
                "symbol": symbol,
                "how": ("作为组合的对冲/防守腿参与，量控制在能安心过夜的仓位"
                        "；不要与上面信号方向冲突时同时重仓"),
                "why": "主题判断（%s，得分 %s）：%s"
                       % (env.get("rate_state") if asset == "利率" else
                          (env.get("geo_state") if asset == "地缘" else
                           (env.get("risk_state") if asset in ("防御", "避险")
                            else "跨资产环境")),
                          rec.get("score") if rec.get("score") is not None else "—",
                          rec.get("reason") or ""),
                "cite": _news_mix(digest, ASSET_TOPICS.get(asset, []))[1],
                "score": _num(rec.get("score")),
                "evidence_count": len(_top_news(digest, ASSET_TOPICS.get(asset, []))),
            })
    return do


def _build_watch(signals):
    watch = []
    for sig in signals or []:
        score = _num(sig.get("score"))
        if abs(score) >= 15:
            continue
        asset = sig.get("asset")
        why = ("综合得分 %+.1f，正反因子互相抵消（%s）"
               % (score, sig.get("stance") or "中性"))
        trigger = ("等信号上穿 +15（转多头排列）或跌破 -15（转空头排列）再进场；"
                   "若美股大盘 / 美元指数先给出明确方向，可提前轻仓跟随")
        watch.append({"asset": asset, "score": score, "why": why,
                      "trigger": trigger})
    return watch


def _build_dont(signals, recs, env, watch, digest):
    """按环境与信号把最常见的亏钱姿势列出来。"""
    dont = []
    env_sig = dict(env or {})

    # 1) 杠杆 ETF 过夜
    lev_symbols = [r.get("symbol") for r in (recs or [])
                   if r.get("symbol") in LEVERAGED]
    if lev_symbols:
        dont.append({
            "tag": "杠杆ETF",
            "why": ("%s 这类 2x/3x 工具存在每日波动衰减：行情横盘或来回扫时，"
                    "方向看对也会亏钱。只做日内方向，尾盘必须减仓或离场，"
                    "绝不隔夜重仓") % "/".join(sorted(set(lev_symbols))),
        })

    # 2) 中性资产抢跑
    neutral_assets = [w["asset"] for w in watch]
    if neutral_assets:
        dont.append({
            "tag": "抢跑",
            "why": ("%s 的信号还在中性区（正反因子相抵），别凭感觉提前重仓押方向；"
                    "等分数突破 ±15 或大盘给出确认再动手。") % "、".join(neutral_assets),
        })

    # 3) risk-off 逆势追成长 / risk-on 逆势留空
    risk_state = str(env_sig.get("risk_state") or "")
    semi_long = any((r.get("asset") == "半导体" and r.get("side") == "做多")
                    for r in (recs or []))
    any_short = any((r.get("side") == "做空") for r in (recs or []))
    if risk_state.startswith("risk-off"):
        if semi_long:
            dont.append({
                "tag": "逆势追多",
                "why": ("资金整体在 risk-off（QQQ 跑输必需消费），即便半导体信号偏多"
                        "也只适合轻仓快打，不要满仓赌成长股逆大盘走强。"),
            })
        dont.append({
            "tag": "抢反弹",
            "why": ("risk-off 阶段下跌常有第二波，别一看到快速下探就抄底；"
                    "若 VIXY 五日还在 %s，等企稳信号（V 型反转 + 放量）再说。")
                  % _pct(env_sig.get("vix_mom5")),
        })
    elif risk_state.startswith("risk-on") and any_short:
        dont.append({
            "tag": "逆势留空",
            "why": ("风险偏好在回升（成长股领涨、波动率回落），空单只做日内脉冲，"
                    "不要隔夜持有，谨防低开高走打掉空头。"),
        })

    # 4) 地缘溢价追最后一棒
    geo = _num(env_sig.get("geo_score"))
    oil_gold_long = any(r.get("asset") in ("原油", "黄金") and r.get("side") == "做多"
                        for r in (recs or []))
    if geo >= 1.5 and oil_gold_long:
        dont.append({
            "tag": "追高地缘",
            "why": ("地缘分已达 %+.1f，原油 / 黄金价格里计入了较多地缘溢价；"
                    "停火、谈判类快讯随时可能让溢价瞬间回吐，"
                    "不要在消息最热时追最后一棒，冲高反而适合兑现。") % geo,
        })

    # 5) 防御失效（去杠杆而非轮动）
    ko = _num(env_sig.get("ko_chg5"))
    if ko < 0 and risk_state.startswith("risk-off"):
        dont.append({
            "tag": "迷信避险",
            "why": ("防御消费（KO 五日 %s）同步走弱，说明资金是整体去杠杆、"
                    "不是轮动避险——所谓「避风港」今天也不稳，别把 XLP 当无脑安全垫。")
                  % _pct(ko),
        })

    # 6) 兜底：消息面纪律
    total = _num(digest.get("total")) if digest else 0
    matched = _num(digest.get("matched")) if digest else 0
    dont.append({
        "tag": "消息面",
        "why": ("不要仅凭单条快讯追涨杀跌：同一条消息对不同资产方向相反（如油价上涨"
                "利多原油、利空航空与下游），先看信号模型各因子的方向是否一致。"
                + ("今日抓到 %d 条海外快讯、命中 %d 条相关主题，足够给结论做交叉验证。"
                   % (int(total), int(matched)) if digest and total else
                   "当前快讯源不可用，本页建议仅基于量价信号，请自行补查消息面。")),
    })
    return dont


def build_brief(analysis, digest):
    """组装完整 brief。analysis / digest 可为 None（数据不足时仍给可读兜底）。"""
    analysis = analysis or {}
    env = analysis.get("environment") or {}
    signals = analysis.get("signals") or []
    recs = analysis.get("recommendations") or []

    # ---------- 定调 ----------
    risk_state = str(env.get("risk_state") or "")
    rate_pressure = _num(env.get("rate_pressure"))
    geo_score = _num(env.get("geo_score"))
    vix = _num(env.get("vix_mom5"))

    if risk_state.startswith("risk-off"):
        risk_short = "risk-off（避险资金主导）"
        mode = "防守优先"
    elif risk_state.startswith("risk-on"):
        risk_short = "risk-on（风险偏好回升）"
        mode = "顺势偏进攻"
    else:
        risk_short = "中性震荡（资金无明确主线）"
        mode = "中性——先看戏、等确认"

    rate_words = ("长端利率上行（债市被抛售）" if rate_pressure > 0.4 else
                  "长端利率回落（债市走强）" if rate_pressure < -0.4 else
                  "长端利率横盘")
    rate_short = ("债市被抛售、利率上行" if rate_pressure > 0.4 else
                  "债市走强、利率回落" if rate_pressure < -0.4 else
                  "债市震荡、利率横盘")
    geo_words = ("，地缘溢价明显抬升" if geo_score >= 1.5 else
                 "，地缘面平淡" if geo_score < 1 else "")

    headline = "今日资金面：%s；%s%s → 操作基调「%s」" % (
        risk_short, rate_words, geo_words, mode)

    tlt5 = _num(env.get("tlt_chg5"))
    qqq5 = _num(env.get("qqq_chg5"))
    risk_snapshot = ("QQQ 五日 %s" % _pct(qqq5)) if qqq5 else ""
    vix_snapshot = ("VIXY 五日 %s" % _pct(vix)) if vix else ""
    bank_txt = ("；银行：%s" % env.get("bank_state")) if env.get("bank_state") else ""
    lead = ("长端利率：TLT 五日 %s，%s；资金面：%s（%s%s%s）。"
            "今天的核心矛盾是「%s」。下面把信号和盘前快讯翻译成能直接执行的清单："
            "该做什么就做什么，不该做的一个都别碰。"
            % (_pct(tlt5), rate_short, risk_state,
               risk_snapshot, ("；" + vix_snapshot) if risk_snapshot and vix_snapshot else "",
               bank_txt, mode))

    # ---------- 组装三块 ----------
    do = _build_do_items(signals, recs, env, digest)
    watch = _build_watch(signals)
    dont = _build_dont(signals, recs, env, watch, digest)

    if not do and not watch:
        return {
            "headline": "行情数据不足，本次无法给出信号级别的文字讲解",
            "lead": "请检查网络后点击「重新计算」重试；若信号页已有得分，本模块会据此给出建议。",
            "do": [], "dont": [], "watch": [],
            "risk": "数据不足，本页不做任何建议。",
            "news": None,
            "generated_at": (analysis.get("generated_at")
                             or time.strftime("%Y-%m-%d %H:%M:%S")),
        }

    # ---------- 风险提示 ----------
    risk_parts = []
    if vix:
        if vix > 8:
            risk_parts.append("波动风险偏高（VIXY 五日 %s），点差大、假突破多" % _pct(vix))
        elif vix > 4:
            risk_parts.append("波动温和放大（VIXY 五日 %s）" % _pct(vix))
    if geo_score >= 1.5:
        risk_parts.append("地缘消息可能盘中突袭（%s）" % env.get("geo_state"))
    risk = ("；".join(risk_parts) + "。" if risk_parts else "今日整体波动风险可控。")
    risk += ("单笔止损建议控制在本金的 1%~2%，日内总仓位别超过一半可用资金；"
             "盘前与数据公布时段（CPI / 美联储讲话 / 库存）先把单子收紧。"
             "若上面某条建议与你的持仓方向冲突，先减仓降低暴露，再考虑对冲。")

    return {
        "headline": headline,
        "lead": lead,
        "do": do,
        "dont": dont,
        "watch": watch,
        "risk": risk,
        "news": ({"total": digest.get("total"), "matched": digest.get("matched"),
                  "generated_at": digest.get("generated_at")} if digest else None),
        "generated_at": (analysis.get("generated_at")
                         or time.strftime("%Y-%m-%d %H:%M:%S")),
    }
