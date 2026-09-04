# -*- coding: utf-8 -*-
"""宏观仪表盘 + 盘前日内多空信号引擎。

思路：不做黑箱预测，而是把可量化的跨资产因子逐条打分并给出解释。
每个目标资产（原油 / 黄金 / 半导体）的最终得分 = 各因子加权分之和，
归一化到 -100 ~ +100，再映射成"做多 / 观望 / 做空"倾向。

因子来源全部是真实行情推导，不依赖任何写死的结论：
  · 自身趋势（均线排列、20 日区间位置）
  · 自身动能（1/3/5/10/20 日收益率）
  · 超买超卖（RSI14）
  · 波动率（ATR%）→ 用于给出日内合理波动区间
  · 跨资产确认或背离（能源股 / 金矿股 / 芯片龙头广度）
  · 利率环境（长债 ETF 反向代理收益率）
  · 风险偏好（纳指 100 vs 必需消费、VIX 期货 ETF）
  · 地缘风险溢价（军工 ETF）
"""

import concurrent.futures
import time

from . import datasource

# 需要参与计算的风向标（K 线）
SIGNAL_UNIVERSE = [
    # 原油链
    "USO", "BNO", "XLE", "XOP", "OIH", "XOM", "CVX", "SLB",
    # 黄金链
    "GLD", "IAU", "GDX", "GDXJ", "SLV",
    # 半导体链
    "SMH", "SOXX", "NVDA", "AMD", "MU", "AMAT", "LRCX", "AVGO", "TSM", "QCOM",
    # 利率 / 大盘 / 风险偏好
    "TLT", "IEF", "TBT", "QQQ", "SPY", "IWM",
    # 银行
    "XLF", "KBE", "KRE", "JPM", "GS",
    # 防御 / 地缘 / 波动率
    "KO", "PEP", "XLP", "ITA", "XAR", "VIXY",
]

# ETF 弹药库
ETF_LIBRARY = {
    "原油": {
        "long": [
            ("USO", "United States Oil Fund，跟踪 WTI 近月期货，日内流动性最好的原油多头工具"),
            ("BNO", "布伦特原油基金，中东供应扰动时通常比 WTI 更敏感"),
            ("XLE", "能源行业龙头股（埃克森美孚 / 雪佛龙为主），波动小于期货，适合过夜"),
            ("XOP", "油气勘探与生产，弹性大于 XLE，油价上行时 beta 更高"),
            ("UCO", "2 倍做多原油，仅适合日内，隔夜有衰减"),
            ("GUSH", "2 倍做多油气开采股，进攻性最强"),
        ],
        "short": [
            ("SCO", "2 倍做空原油，地缘缓和 / 增产落地时的反向工具"),
            ("DRIP", "2 倍做空油气开采股"),
        ],
    },
    "黄金": {
        "long": [
            ("GLD", "SPDR 黄金信托，规模最大、点差最小"),
            ("IAU", "iShares 黄金信托，费率比 GLD 低，适合中长期持有"),
            ("GDX", "金矿商 ETF，对金价有约 2 倍弹性，反弹时爆发力强"),
            ("GDXJ", "小型金矿，弹性大于 GDX，回撤也更凶"),
            ("NUGT", "2 倍做多金矿，纯日内工具"),
        ],
        "short": [
            ("DUST", "2 倍做空金矿商，实际利率快速上行时的反向工具"),
            ("GLL", "2 倍做空黄金"),
        ],
    },
    "半导体": {
        "long": [
            ("SMH", "VanEck 半导体，前十大权重集中（台积电 / 英伟达），跟踪最紧"),
            ("SOXX", "iShares 费城半导体，成分更分散，波动略低于 SMH"),
            ("SOXL", "3 倍做多半导体，波动极大，只做日内"),
        ],
        "short": [
            ("SOXS", "3 倍做空半导体，估值杀 / 利率冲击时的对冲工具"),
            ("SSG", "2 倍做空半导体，杠杆比 SOXS 温和"),
        ],
    },
    "利率": {
        "long": [("TLT", "20 年期以上美债，押注收益率下行"),
                 ("IEF", "7-10 年期美债，久期温和")],
        "short": [("TBT", "2 倍做空 20 年期以上美债，押注长端收益率继续上行"),
                  ("TTT", "3 倍做空长债，杠杆更高")],
    },
    "避险": {
        "long": [("XLP", "必需消费品，含可口可乐 / 宝洁，risk-off 时相对抗跌"),
                 ("ITA", "航空航天与国防，地缘冲突升级时的结构性受益板块"),
                 ("VIXY", "VIX 短期期货，纯对冲工具，长期持有必然衰减")],
        "short": [],
    },
}


# --------------------------------------------------------------------------
# 数据准备
# --------------------------------------------------------------------------
def _load_packs(symbols, kline_count=90):
    """并发拉取日线并算指标，返回 {symbol: pack}。"""
    packs = {}
    with concurrent.futures.ThreadPoolExecutor(max_workers=8) as pool:
        futures = {pool.submit(datasource.build_indicator_pack, s, None): s
                   for s in symbols}
        for fut in concurrent.futures.as_completed(futures):
            sym = futures[fut]
            try:
                pack = fut.result()
            except Exception:
                pack = None
            if pack:
                packs[sym] = pack
    return packs


def _num(pack, key, default=0.0):
    """安全取指标值。"""
    if not pack:
        return default
    value = pack.get(key)
    return default if value is None else value


def _quote_pct(quotes, symbol, default=0.0):
    q = quotes.get(symbol)
    return q["change_pct"] if q else default


# --------------------------------------------------------------------------
# 评分工具
# --------------------------------------------------------------------------
class ScoreBoard:
    """收集因子得分和解释。"""

    def __init__(self, max_abs_per_factor=2.5):
        self.factors = []
        self.max_abs = max_abs_per_factor

    def add(self, name, score, detail, weight=1.0):
        score = max(-self.max_abs, min(self.max_abs, float(score)))
        self.factors.append({
            "name": name,
            "score": round(score * weight, 2),
            "detail": detail,
        })

    @property
    def total(self):
        return sum(f["score"] for f in self.factors)

    def normalized(self, denominator):
        """归一化到 -100 ~ 100。denominator 为理论最大绝对分。"""
        if denominator <= 0:
            return 0.0
        value = self.total / denominator * 100.0
        return round(max(-100.0, min(100.0, value)), 1)


def _stance(score):
    """得分映射到操作倾向。"""
    if score >= 35:
        return "偏多", "做多", "中高"
    if score >= 15:
        return "弱偏多", "轻仓做多 / 回踩再进", "中"
    if score > -15:
        return "中性", "观望，等日内方向确认", "低"
    if score > -35:
        return "弱偏空", "轻仓做空 / 反弹再进", "中"
    return "偏空", "做空", "中高"


def _fmt(value, digits=2, suffix=""):
    if value is None:
        return "—"
    return ("%." + str(digits) + "f%s") % (value, suffix)


def _trend_words(pack):
    if not pack:
        return "数据不足"
    if pack.get("ma_stack_up"):
        return "均线多头排列（MA5>MA10>MA20）"
    if pack.get("ma_stack_down"):
        return "均线空头排列（MA5<MA10<MA20）"
    return "均线纠缠，方向不明"


# --------------------------------------------------------------------------
# 环境因子（三个资产共用）
# --------------------------------------------------------------------------
def build_environment(packs, quotes):
    """计算宏观环境状态。"""
    tlt = packs.get("TLT")
    ief = packs.get("IEF")
    qqq = packs.get("QQQ")
    xlp = packs.get("XLP")
    vixy = packs.get("VIXY")
    ita = packs.get("ITA")
    xar = packs.get("XAR")
    uso = packs.get("USO")
    xlf = packs.get("XLF")
    kre = packs.get("KRE")

    # TLT 下跌 = 长端收益率上行
    tlt_chg5 = _num(tlt, "chg5")
    tlt_chg1 = _num(tlt, "chg1")
    rate_pressure = -tlt_chg5  # 正数代表收益率上行压力
    if rate_pressure > 1.5:
        rate_state = "长端利率快速上行（债市抛售）"
    elif rate_pressure > 0.4:
        rate_state = "长端利率温和上行"
    elif rate_pressure < -1.5:
        rate_state = "长端利率快速下行（债市走强）"
    elif rate_pressure < -0.4:
        rate_state = "长端利率温和下行"
    else:
        rate_state = "长端利率横盘"

    # 风险偏好：纳指 100 相对必需消费的 5 日强弱
    risk_spread = _num(qqq, "chg5") - _num(xlp, "chg5")
    vix_mom = _num(vixy, "chg5")
    if risk_spread < -1.5 or vix_mom > 8:
        risk_state = "risk-off（资金撤出成长、涌向防御）"
    elif risk_spread > 1.5 and vix_mom < 0:
        risk_state = "risk-on（成长股领先、波动率回落）"
    else:
        risk_state = "风险偏好中性"

    # 地缘溢价：军工 + 原油同步走强
    geo_score = 0.0
    geo_bits = []
    for sym, pack in (("ITA", ita), ("XAR", xar)):
        chg5 = _num(pack, "chg5")
        if chg5 > 1.0:
            geo_score += 1
            geo_bits.append("%s 五日 +%.1f%%" % (sym, chg5))
        elif chg5 < -1.0:
            geo_score -= 0.5
            geo_bits.append("%s 五日 %.1f%%" % (sym, chg5))
    oil_chg5 = _num(uso, "chg5")
    if oil_chg5 > 4:
        geo_score += 1.5
        geo_bits.append("原油五日 +%.1f%%（供应溢价）" % oil_chg5)
    elif oil_chg5 > 1.5:
        geo_score += 0.5
        geo_bits.append("原油五日 +%.1f%%" % oil_chg5)
    if geo_score >= 1.5:
        geo_state = "地缘风险溢价明显抬升"
    elif geo_score > 0:
        geo_state = "地缘风险溢价小幅存在"
    else:
        geo_state = "地缘溢价不显著"

    # 银行：利率上行对银行是双刃剑，看相对表现
    bank_chg5 = _num(xlf, "chg5")
    regional_chg5 = _num(kre, "chg5")
    if bank_chg5 > 0.5 and regional_chg5 > 0:
        bank_state = "银行股扛住利率上行（净息差逻辑占优）"
    elif regional_chg5 < -2:
        bank_state = "区域银行走弱（信用/久期风险担忧升温）"
    else:
        bank_state = "银行股表现分化"

    return {
        "rate_pressure": round(rate_pressure, 2),
        "rate_state": rate_state,
        "tlt_chg1": round(tlt_chg1, 2),
        "tlt_chg5": round(tlt_chg5, 2),
        "ief_chg5": round(_num(ief, "chg5"), 2),
        "risk_spread": round(risk_spread, 2),
        "risk_state": risk_state,
        "vix_mom5": round(vix_mom, 2),
        "geo_score": round(geo_score, 2),
        "geo_state": geo_state,
        "geo_detail": "；".join(geo_bits) if geo_bits else "军工与原油动能均不突出",
        "bank_state": bank_state,
        "bank_chg5": round(bank_chg5, 2),
        "kre_chg5": round(regional_chg5, 2),
        "qqq_chg1": round(_num(qqq, "chg1"), 2),
        "qqq_chg5": round(_num(qqq, "chg5"), 2),
        "ko_chg5": round(_num(packs.get("KO"), "chg5"), 2),
    }


# --------------------------------------------------------------------------
# 原油信号
# --------------------------------------------------------------------------
def score_oil(packs, quotes, env):
    board = ScoreBoard()
    uso = packs.get("USO")
    if not uso:
        return None

    chg1, chg3, chg5 = _num(uso, "chg1"), _num(uso, "chg3"), _num(uso, "chg5")
    rsi14 = _num(uso, "rsi14", 50)
    pos20 = _num(uso, "pos_in_range20", 50)

    # F1 趋势结构
    if uso.get("ma_stack_up") and uso.get("above_ma20"):
        board.add("趋势结构", 2.0, "价格站上 MA20，%s，趋势偏多" % _trend_words(uso))
    elif uso.get("ma_stack_down") and not uso.get("above_ma20"):
        board.add("趋势结构", -2.0, "价格跌破 MA20，%s" % _trend_words(uso))
    elif uso.get("above_ma20"):
        board.add("趋势结构", 0.8, "价格在 MA20 上方但均线未完全发散")
    else:
        board.add("趋势结构", -0.8, "价格在 MA20 下方，反弹性质待确认")

    # F2 中期动能
    if chg5 >= 8:
        board.add("五日动能", 1.2,
                  "五日 +%.1f%%，趋势强但已进入加速段，追多性价比下降" % chg5)
    elif chg5 >= 3:
        board.add("五日动能", 2.0, "五日 +%.1f%%，动能健康" % chg5)
    elif chg5 <= -5:
        board.add("五日动能", -2.0, "五日 %.1f%%，下行动能明确" % chg5)
    elif chg5 <= -1.5:
        board.add("五日动能", -1.0, "五日 %.1f%%，偏弱" % chg5)
    else:
        board.add("五日动能", 0.2, "五日 %.1f%%，动能中性" % chg5)

    # F3 能源股确认 / 背离
    xle_chg1 = _num(packs.get("XLE"), "chg1")
    xop_chg1 = _num(packs.get("XOP"), "chg1")
    if chg1 > 1 and xop_chg1 > 0.5:
        board.add("能源股确认", 1.5,
                  "原油 +%.1f%% 且 XOP +%.1f%%，权益端跟涨，趋势可信度高" % (chg1, xop_chg1))
    elif chg1 > 2 and xop_chg1 < 0:
        board.add("能源股确认", -1.5,
                  "原油 +%.1f%% 但 XOP %.1f%% 背离，市场不认可涨价持续性" % (chg1, xop_chg1))
    elif chg1 < -1 and xle_chg1 < 0:
        board.add("能源股确认", -1.0,
                  "原油 %.1f%% 且 XLE %.1f%%，同步走弱" % (chg1, xle_chg1))
    else:
        board.add("能源股确认", 0.0,
                  "原油 %.1f%% / XLE %.1f%% / XOP %.1f%%，无明确共振"
                  % (chg1, xle_chg1, xop_chg1))

    # F4 油服背离（OIH 常领先反映资本开支预期）
    oih_chg1 = _num(packs.get("OIH"), "chg1")
    if chg1 > 2 and oih_chg1 < -0.5:
        board.add("油服背离", -1.2,
                  "OIH %.1f%% 与油价背离，说明这波被视为地缘脉冲而非需求改善" % oih_chg1)
    elif chg1 > 1 and oih_chg1 > 1:
        board.add("油服背离", 0.8, "OIH +%.1f%% 同步跟涨，产业链认可" % oih_chg1)
    else:
        board.add("油服背离", 0.0, "OIH %.1f%%，信号中性" % oih_chg1)

    # F5 超买超卖
    if rsi14 >= 78:
        board.add("超买超卖", -2.2, "RSI14=%.0f 严重超买，回踩概率高" % rsi14)
    elif rsi14 >= 70:
        board.add("超买超卖", -1.2, "RSI14=%.0f 超买，不宜直接追高" % rsi14)
    elif rsi14 <= 30:
        board.add("超买超卖", 1.5, "RSI14=%.0f 超卖，存在技术性反弹" % rsi14)
    else:
        board.add("超买超卖", 0.3, "RSI14=%.0f，处于中性区" % rsi14)

    # F6 地缘溢价
    if env["geo_score"] >= 1.5:
        board.add("地缘溢价", 2.0, "%s（%s），供应端风险主导定价"
                  % (env["geo_state"], env["geo_detail"]))
    elif env["geo_score"] > 0:
        board.add("地缘溢价", 0.8, env["geo_state"])
    else:
        board.add("地缘溢价", -0.5, "%s，缺少上行催化" % env["geo_state"])

    # F7 区间位置
    if pos20 >= 92:
        board.add("区间位置", -0.6, "已处于 20 日区间顶部 %.0f%% 分位，追高即接盘风险" % pos20)
    elif pos20 <= 10:
        board.add("区间位置", 0.6, "处于 20 日区间底部 %.0f%% 分位，下行空间收窄" % pos20)
    else:
        board.add("区间位置", 0.2, "位于 20 日区间 %.0f%% 分位" % pos20)

    score = board.normalized(denominator=11.0)
    stance, action, confidence = _stance(score)

    atr = _num(uso, "atr_pct", 2.0)
    last = _num(uso, "last_close")
    levels = {
        "参考标的": "USO",
        "最新收盘": round(last, 2),
        "MA20": round(_num(uso, "ma20"), 2),
        "MA50": round(_num(uso, "ma50"), 2),
        "20日高": round(_num(uso, "high20"), 2),
        "20日低": round(_num(uso, "low20"), 2),
        "日内波幅参考(ATR%)": round(atr, 2),
        "日内合理区间": "%.2f ~ %.2f" % (last * (1 - atr / 100.0), last * (1 + atr / 100.0)),
    }
    return {
        "asset": "原油",
        "proxy": "USO / BNO（WTI / 布伦特）",
        "score": score,
        "stance": stance,
        "action": action,
        "confidence": confidence,
        "factors": board.factors,
        "levels": levels,
        "etfs": ETF_LIBRARY["原油"],
    }


# --------------------------------------------------------------------------
# 黄金信号
# --------------------------------------------------------------------------
def score_gold(packs, quotes, env):
    board = ScoreBoard()
    gld = packs.get("GLD")
    if not gld:
        return None

    chg1, chg3, chg5 = _num(gld, "chg1"), _num(gld, "chg3"), _num(gld, "chg5")
    chg20 = _num(gld, "chg20")
    rsi14 = _num(gld, "rsi14", 50)
    pos20 = _num(gld, "pos_in_range20", 50)

    # F1 趋势结构
    if gld.get("ma_stack_up") and gld.get("above_ma20"):
        board.add("趋势结构", 2.0, "站上 MA20 且 %s" % _trend_words(gld))
    elif gld.get("ma_stack_down") and not gld.get("above_ma20"):
        board.add("趋势结构", -2.0, "跌破 MA20 且 %s" % _trend_words(gld))
    elif not gld.get("above_ma50"):
        board.add("趋势结构", -1.2, "已跌破 MA50，中期趋势转弱")
    else:
        board.add("趋势结构", 0.5, "MA50 之上震荡，中期趋势未破")

    # F2 实际利率（对黄金最核心的压制变量）
    rp = env["rate_pressure"]
    if rp > 1.5:
        board.add("利率环境", -2.5,
                  "%s（TLT 五日 %.1f%%），无息资产持有成本抬升，对金价最直接的压制"
                  % (env["rate_state"], env["tlt_chg5"]))
    elif rp > 0.4:
        board.add("利率环境", -1.2, "%s，对黄金构成温和压力" % env["rate_state"])
    elif rp < -1.5:
        board.add("利率环境", 2.0, "%s，实际利率回落利多黄金" % env["rate_state"])
    else:
        board.add("利率环境", 0.0, "%s，对黄金影响中性" % env["rate_state"])

    # F3 金矿股确认（金矿对金价有杠杆，常领先）
    gdx = packs.get("GDX")
    gdx_chg1 = _num(gdx, "chg1")
    gdx_chg5 = _num(gdx, "chg5")
    if gdx_chg1 < chg1 * 2 - 1.0 and chg1 < 0:
        board.add("金矿确认", -1.5,
                  "GDX %.1f%% 跌幅超过金价 %.1f%% 的正常杠杆，权益端在抢跑下跌"
                  % (gdx_chg1, chg1))
    elif gdx_chg1 > 0 and chg1 <= 0:
        board.add("金矿确认", 1.2,
                  "金价 %.1f%% 但 GDX +%.1f%% 逆势走强，出现底部分歧信号" % (chg1, gdx_chg1))
    elif gdx_chg5 > chg5 and chg5 > 0:
        board.add("金矿确认", 1.0, "GDX 五日 +%.1f%% 强于金价，杠杆正常发挥" % gdx_chg5)
    else:
        board.add("金矿确认", -0.3, "GDX 日内 %.1f%% / 五日 %.1f%%，未提供额外支撑"
                  % (gdx_chg1, gdx_chg5))

    # F4 白银确认（白银工业属性更强，同跌说明是流动性/利率驱动）
    slv_chg1 = _num(packs.get("SLV"), "chg1")
    if slv_chg1 < -1.5 and chg1 < -0.5:
        board.add("白银共振", -1.0,
                  "SLV %.1f%% 与黄金同跌，属贵金属板块整体去杠杆而非单一避险切换" % slv_chg1)
    elif slv_chg1 > 1.5 and chg1 > 0.5:
        board.add("白银共振", 1.0, "SLV +%.1f%% 同步走强，板块共振向上" % slv_chg1)
    else:
        board.add("白银共振", 0.0, "SLV %.1f%%，无明确共振" % slv_chg1)

    # F5 超买超卖
    if rsi14 <= 28:
        board.add("超买超卖", 2.0, "RSI14=%.0f 深度超卖，空头拥挤，反抽风险大" % rsi14)
    elif rsi14 <= 38:
        board.add("超买超卖", 1.0, "RSI14=%.0f 接近超卖区，继续追空需谨慎" % rsi14)
    elif rsi14 >= 72:
        board.add("超买超卖", -1.5, "RSI14=%.0f 超买" % rsi14)
    else:
        board.add("超买超卖", 0.0, "RSI14=%.0f 中性" % rsi14)

    # F6 避险需求
    if env["vix_mom5"] > 8 or env["risk_state"].startswith("risk-off"):
        board.add("避险需求", 1.0,
                  "%s，VIX 期货五日 %+.1f%%，避险资金存在回流黄金的可能"
                  % (env["risk_state"], env["vix_mom5"]))
    else:
        board.add("避险需求", -0.2, "%s，避险买盘不强" % env["risk_state"])

    # F7 动能崩坏
    if chg5 <= -4 and not gld.get("above_ma50"):
        board.add("动能崩坏", -2.0,
                  "五日 %.1f%% 且失守 MA50，属趋势性回撤而非普通洗盘" % chg5)
    elif chg5 <= -2:
        board.add("动能崩坏", -1.0, "五日 %.1f%%，短期动能偏空" % chg5)
    elif chg5 >= 3:
        board.add("动能崩坏", 1.2, "五日 +%.1f%%，动能转多" % chg5)
    else:
        board.add("动能崩坏", 0.0, "五日 %.1f%%，动能中性" % chg5)

    # F8 区间位置
    if pos20 <= 10:
        board.add("区间位置", 0.5, "20 日区间 %.0f%% 分位，已在箱体下沿" % pos20)
    elif pos20 >= 90:
        board.add("区间位置", -0.5, "20 日区间 %.0f%% 分位，位于箱体上沿" % pos20)
    else:
        board.add("区间位置", 0.0, "20 日区间 %.0f%% 分位" % pos20)

    score = board.normalized(denominator=12.5)
    stance, action, confidence = _stance(score)

    atr = _num(gld, "atr_pct", 1.5)
    last = _num(gld, "last_close")
    levels = {
        "参考标的": "GLD",
        "最新收盘": round(last, 2),
        "MA20": round(_num(gld, "ma20"), 2),
        "MA50": round(_num(gld, "ma50"), 2),
        "20日高": round(_num(gld, "high20"), 2),
        "20日低": round(_num(gld, "low20"), 2),
        "日内波幅参考(ATR%)": round(atr, 2),
        "日内合理区间": "%.2f ~ %.2f" % (last * (1 - atr / 100.0), last * (1 + atr / 100.0)),
        "二十日累计": "%.1f%%" % chg20,
    }
    return {
        "asset": "黄金",
        "proxy": "GLD / IAU（现货黄金）",
        "score": score,
        "stance": stance,
        "action": action,
        "confidence": confidence,
        "factors": board.factors,
        "levels": levels,
        "etfs": ETF_LIBRARY["黄金"],
    }


# --------------------------------------------------------------------------
# 半导体信号
# --------------------------------------------------------------------------
_CHIP_LEADERS = ["NVDA", "AMD", "MU", "AMAT", "LRCX", "AVGO", "TSM", "QCOM"]


def score_semis(packs, quotes, env):
    board = ScoreBoard()
    smh = packs.get("SMH")
    if not smh:
        return None

    chg1, chg3, chg5 = _num(smh, "chg1"), _num(smh, "chg3"), _num(smh, "chg5")
    rsi14 = _num(smh, "rsi14", 50)
    pos20 = _num(smh, "pos_in_range20", 50)

    # F1 趋势结构
    if smh.get("ma_stack_up") and smh.get("above_ma20"):
        board.add("趋势结构", 2.0, "站上 MA20 且 %s" % _trend_words(smh))
    elif smh.get("ma_stack_down") and not smh.get("above_ma20"):
        board.add("趋势结构", -2.0, "跌破 MA20 且 %s" % _trend_words(smh))
    elif smh.get("above_ma50"):
        board.add("趋势结构", 0.5, "MA50 之上震荡，中期上行结构未破")
    else:
        board.add("趋势结构", -1.2, "失守 MA50，中期结构转弱")

    # F2 利率压制（半导体是典型长久期成长资产）
    rp = env["rate_pressure"]
    if rp > 1.5:
        board.add("利率压制", -2.0,
                  "%s（TLT 五日 %.1f%%），高估值成长股的贴现率冲击最直接"
                  % (env["rate_state"], env["tlt_chg5"]))
    elif rp > 0.4:
        board.add("利率压制", -1.0, "%s，对估值构成温和压力" % env["rate_state"])
    elif rp < -1.0:
        board.add("利率压制", 1.5, "%s，利好成长股估值修复" % env["rate_state"])
    else:
        board.add("利率压制", 0.0, "%s" % env["rate_state"])

    # F3 油价通胀传导（油价飙升 → 通胀预期 → 加息预期 → 杀估值）
    oil_chg5 = _num(packs.get("USO"), "chg5")
    if oil_chg5 > 6:
        board.add("油价通胀传导", -1.5,
                  "原油五日 +%.1f%%，强化通胀与紧缩预期，对成长股形成二次压制" % oil_chg5)
    elif oil_chg5 > 3:
        board.add("油价通胀传导", -0.8, "原油五日 +%.1f%%，通胀预期小幅抬升" % oil_chg5)
    elif oil_chg5 < -3:
        board.add("油价通胀传导", 0.8, "原油五日 %.1f%%，通胀压力缓解" % oil_chg5)
    else:
        board.add("油价通胀传导", 0.0, "原油五日 %.1f%%，传导影响有限" % oil_chg5)

    # F4 龙头广度
    up, down, detail_bits = 0, 0, []
    for sym in _CHIP_LEADERS:
        pack = packs.get(sym)
        if not pack:
            continue
        value = _num(pack, "chg1")
        if value > 0:
            up += 1
        elif value < 0:
            down += 1
        detail_bits.append("%s %+.1f%%" % (sym, value))
    total_leaders = up + down
    if total_leaders:
        breadth = up / float(total_leaders)
        if breadth >= 0.75:
            board.add("龙头广度", 2.0, "%d/%d 家龙头上涨，板块广度健康（%s）"
                      % (up, total_leaders, "，".join(detail_bits[:5])))
        elif breadth <= 0.25:
            board.add("龙头广度", -2.0, "仅 %d/%d 家龙头上涨，板块普跌（%s）"
                      % (up, total_leaders, "，".join(detail_bits[:5])))
        else:
            board.add("龙头广度", 0.0, "%d/%d 家龙头上涨，内部分化（%s）"
                      % (up, total_leaders, "，".join(detail_bits[:5])))
    else:
        board.add("龙头广度", 0.0, "龙头数据不足")

    # F5 大盘环境
    qqq_chg1 = env["qqq_chg1"]
    qqq_chg5 = env["qqq_chg5"]
    if qqq_chg5 < -2:
        board.add("大盘环境", -1.5, "纳指100 五日 %.1f%%，大盘 beta 拖累" % qqq_chg5)
    elif qqq_chg5 > 2:
        board.add("大盘环境", 1.5, "纳指100 五日 +%.1f%%，大盘顺风" % qqq_chg5)
    else:
        board.add("大盘环境", qqq_chg1 * 0.3,
                  "纳指100 日内 %.1f%% / 五日 %.1f%%，环境中性" % (qqq_chg1, qqq_chg5))

    # F6 相对强弱
    rel = chg5 - qqq_chg5
    if rel > 2:
        board.add("相对强弱", 1.5, "SMH 五日跑赢纳指100 %.1f 个点，资金偏好半导体" % rel)
    elif rel < -2:
        board.add("相对强弱", -1.5, "SMH 五日跑输纳指100 %.1f 个点，被资金抛弃" % abs(rel))
    else:
        board.add("相对强弱", 0.0, "SMH 与纳指100 五日差 %.1f 个点，同步" % rel)

    # F7 超买超卖
    if rsi14 <= 30:
        board.add("超买超卖", 1.8, "RSI14=%.0f 超卖，日内存在超跌反弹" % rsi14)
    elif rsi14 <= 40:
        board.add("超买超卖", 0.8, "RSI14=%.0f 偏弱但接近超卖" % rsi14)
    elif rsi14 >= 72:
        board.add("超买超卖", -1.5, "RSI14=%.0f 超买，短线追多风险高" % rsi14)
    else:
        board.add("超买超卖", 0.0, "RSI14=%.0f 中性" % rsi14)

    # F8 风险偏好
    if env["risk_state"].startswith("risk-off"):
        board.add("风险偏好", -1.2, "%s，高 beta 板块首当其冲" % env["risk_state"])
    elif env["risk_state"].startswith("risk-on"):
        board.add("风险偏好", 1.2, "%s，高 beta 板块受益" % env["risk_state"])
    else:
        board.add("风险偏好", 0.0, env["risk_state"])

    score = board.normalized(denominator=12.7)
    stance, action, confidence = _stance(score)

    atr = _num(smh, "atr_pct", 2.5)
    last = _num(smh, "last_close")
    levels = {
        "参考标的": "SMH",
        "最新收盘": round(last, 2),
        "MA20": round(_num(smh, "ma20"), 2),
        "MA50": round(_num(smh, "ma50"), 2),
        "20日高": round(_num(smh, "high20"), 2),
        "20日低": round(_num(smh, "low20"), 2),
        "日内波幅参考(ATR%)": round(atr, 2),
        "日内合理区间": "%.2f ~ %.2f" % (last * (1 - atr / 100.0), last * (1 + atr / 100.0)),
    }
    return {
        "asset": "半导体",
        "proxy": "SMH / SOXX（费城半导体）",
        "score": score,
        "stance": stance,
        "action": action,
        "confidence": confidence,
        "factors": board.factors,
        "levels": levels,
        "etfs": ETF_LIBRARY["半导体"],
    }


# --------------------------------------------------------------------------
# ETF 推荐
# --------------------------------------------------------------------------
def recommend_etfs(signals, env):
    """按信号强度给出最终 ETF 建议清单。"""
    recs = []
    for sig in signals:
        if not sig:
            continue
        asset = sig["asset"]
        score = sig["score"]
        pool = sig["etfs"]
        if score >= 15:
            side, candidates = "做多", pool.get("long", [])
        elif score <= -15:
            side, candidates = "做空", pool.get("short", [])
        else:
            side, candidates = "观望", pool.get("long", [])[:2]
        for rank, (symbol, reason) in enumerate(candidates[:3]):
            recs.append({
                "asset": asset,
                "side": side,
                "symbol": symbol,
                "reason": reason,
                "priority": "首选" if rank == 0 else ("次选" if rank == 1 else "备选"),
                "score": score,
            })

    # 利率环境衍生建议
    if env["rate_pressure"] > 1.0:
        recs.append({
            "asset": "利率", "side": "做多", "symbol": "TBT",
            "reason": "长端收益率上行趋势中，做空长债的顺势工具（%s）" % env["rate_state"],
            "priority": "主题", "score": round(env["rate_pressure"] * 20, 1),
        })
    elif env["rate_pressure"] < -1.0:
        recs.append({
            "asset": "利率", "side": "做多", "symbol": "TLT",
            "reason": "长端收益率回落，久期资产受益（%s）" % env["rate_state"],
            "priority": "主题", "score": round(-env["rate_pressure"] * 20, 1),
        })

    # 地缘 / 避险衍生建议
    if env["geo_score"] >= 1.5:
        recs.append({
            "asset": "地缘", "side": "做多", "symbol": "ITA",
            "reason": "地缘冲突升级期，航空航天与国防具备结构性订单逻辑",
            "priority": "主题", "score": round(env["geo_score"] * 25, 1),
        })
    if env["risk_state"].startswith("risk-off"):
        recs.append({
            "asset": "防御", "side": "做多", "symbol": "XLP",
            "reason": "risk-off 环境下必需消费（含可口可乐）相对抗跌，可作为仓位避风港",
            "priority": "主题", "score": 30.0,
        })
    return recs


# --------------------------------------------------------------------------
# 主入口
# --------------------------------------------------------------------------
def build_premarket_analysis():
    """生成完整的盘前分析结果。"""
    started = time.time()
    packs = _load_packs(SIGNAL_UNIVERSE)
    quotes = datasource.get_quotes(sorted(SIGNAL_UNIVERSE), ttl=20)
    env = build_environment(packs, quotes)

    signals = [score_oil(packs, quotes, env),
               score_gold(packs, quotes, env),
               score_semis(packs, quotes, env)]
    signals = [s for s in signals if s]

    recs = recommend_etfs(signals, env)

    return {
        "generated_at": time.strftime("%Y-%m-%d %H:%M:%S"),
        "elapsed": round(time.time() - started, 1),
        "environment": env,
        "signals": signals,
        "recommendations": recs,
        "universe_loaded": len(packs),
        "disclaimer": ("本结果由量价与跨资产因子模型自动生成，仅供研究参考，"
                       "不构成投资建议。杠杆 ETF 存在波动衰减，请勿隔夜重仓持有。"),
    }
