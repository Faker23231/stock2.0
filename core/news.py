# -*- coding: utf-8 -*-
"""财经快讯抓取与主题归类。

盘前判断除了量价，还需要消息面。国内网络能稳定访问的免费快讯源：
  · 华尔街见闻「全球」频道（海外宏观质量最高）
  · 东方财富 7x24 快讯（覆盖面最广）

抓回来后按用户关注的主题（战争 / 原油 / 黄金 / 美债 / 银行 / 半导体 /
美股大盘 / 防御消费 / 美联储政策）做关键词归类。

关键词分两级：
  strong —— 特异性高，命中即归类（如「霍尔木兹」「现货黄金」「英伟达」）
  weak   —— 容易误伤，必须同时出现海外语境词才归类（如「芯片」「石油」）
另有 exclude 规则，把明显是 A 股 / 港股本地新闻的条目剔掉，
避免「西子洁能」这类内容被塞进「原油能源」。
"""

import json
import re
import time

from . import datasource

# 强海外语境词：足以证明这条新闻的主体在海外市场
STRONG_OVERSEA = [
    "美股", "美国", "美联储", "纳指", "纳斯达克", "标普", "道指", "华尔街",
    "美债", "美东", "COMEX", "NYMEX", "布伦特", "WTI", "欧佩克", "OPEC",
    "特朗普", "白宫", "伊朗", "以色列", "美元指数",
]

# 一般海外语境词：weak 关键词需要它来确认这条新闻确实指向海外市场
OVERSEA_CONTEXT = STRONG_OVERSEA + [
    "隔夜", "国际", "全球", "伦敦", "纽约", "外盘", "欧洲", "欧元区",
    "日本", "韩国", "美元", "非农", "央行",
]

# 本地市场词：命中且没有海外语境时直接丢弃
LOCAL_ONLY = [
    "A股", "港股", "科创板", "北交所", "创业板", "沪指", "深证", "上证",
    "恒生", "北向", "涨停", "跌停", "新股", "可转债", "两市", "沪深",
    "证监会", "公募", "私募", "回购注销", "股东减持", "定增", "IPO",
    "中证", "深主板", "龙虎榜", "游资", "机构席位",
    "金股", "券商", "次新股", "新股申购",
    "期货开盘", "商品期货开盘", "主力合约", "沪金", "沪银", "内盘",
    "国内期货", "收盘国内",
]

# 标题级排除：这类「节目单 / 汇总」每期都会把关键词凑齐，污染所有主题
TITLE_SKIP = [
    "日内请重点关注", "提醒：", "早间要闻汇总", "见闻历", "今日要闻",
    "财经早餐", "晚报", "明日提醒",
]

# 主题级排除词：命中则该条不计入对应主题，用于压掉高频误伤
TOPIC_EXCLUDE = {
    "黄金贵金属": ["金饰", "克价", "足金", "紫金矿业", "金股", "金秋行情"],
    "美股大盘": ["纳斯达克上市的", "酷哇科技", "世界模型", "算力"],
    "半导体": ["算力租赁", "算力调度", "世界模型", "光传感", "机器人"],
    "美债利率": ["国债逆回购"],
}

TOPICS = [
    ("战争地缘", {
        "strong": ["伊朗", "以色列", "霍尔木兹", "革命卫队", "胡塞", "红海",
                   "中央司令部", "俄乌", "乌克兰", "空袭", "导弹袭击", "停火",
                   "油轮", "布雷", "水雷", "哈马斯", "真主党", "委内瑞拉",
                   "军事打击", "报复", "开战", "战事"],
        "weak": ["制裁", "地缘", "军事冲突", "无人机袭击", "战争", "海峡",
                 "弹道导弹"],
    }),
    ("原油能源", {
        "strong": ["WTI", "布伦特", "欧佩克", "OPEC", "原油", "油价",
                   "EIA库存", "汽油库存", "美油", "钻井数", "减产协议"],
        "weak": ["石油", "天然气", "炼油", "减产", "增产", "柴油", "能源价格",
                 "液化天然气", "LNG"],
    }),
    ("黄金贵金属", {
        "strong": ["现货黄金", "金价", "COMEX", "伦敦金", "黄金期货", "白银",
                   "贵金属", "黄金ETF", "钯金", "铂金", "金矿股"],
        "weak": ["黄金", "避险资产", "银价"],
    }),
    ("美债利率", {
        "strong": ["美债", "美国国债", "10年期美债", "30年期美债", "国债拍卖",
                   "收益率曲线", "期限溢价", "债市抛售"],
        "weak": ["国债收益率", "长端收益率", "债市", "久期"],
    }),
    ("银行金融", {
        "strong": ["摩根大通", "高盛", "花旗", "富国银行", "美国银行",
                   "摩根士丹利", "区域银行", "净息差", "硅谷银行", "信贷紧缩"],
        "weak": ["银行股", "金融股", "存款", "信贷"],
    }),
    ("半导体", {
        "strong": ["英伟达", "NVIDIA", "AMD", "台积电", "美光", "博通",
                   "高通", "英特尔", "应用材料", "泛林", "ASML", "阿斯麦",
                   "费城半导体", "HBM", "SK海力士", "三星电子", "闪迪",
                   "GPU", "算力芯片", "先进制程"],
        "weak": ["半导体", "芯片", "晶圆", "光刻", "存储芯片"],
    }),
    ("美股大盘", {
        "strong": ["纳指", "纳斯达克", "标普500", "标普指数", "道指", "美股",
                   "三大指数", "美股期货", "纳指期货", "美股盘前", "美股收盘",
                   "美股三大指数"],
        "weak": ["科技股", "大型科技", "七巨头"],
    }),
    ("防御消费", {
        "strong": ["可口可乐", "百事可乐", "宝洁", "必需消费", "沃尔玛",
                   "麦当劳", "好市多"],
        "weak": ["食品饮料", "日用消费"],
    }),
    ("美联储政策", {
        "strong": ["美联储", "FOMC", "鲍威尔", "沃什", "Warsh", "褐皮书",
                   "点阵图", "杰克逊霍尔", "非农", "ADP", "核心PCE",
                   "美国CPI", "联邦基金利率", "缩表", "议息"],
        "weak": ["加息", "降息", "利率决议", "通胀", "CPI", "PCE", "就业数据"],
    }),
    ("全球央行", {
        "strong": ["日本央行", "欧洲央行", "英国央行", "欧央行", "BOJ", "ECB",
                   "瑞士央行", "澳洲央行"],
        "weak": [],
    }),
]

_TAG_RE = re.compile(r"<[^>]+>")


def _clean(text):
    if not text:
        return ""
    text = _TAG_RE.sub(" ", str(text))
    for old, new in (("&nbsp;", " "), ("&amp;", "&"), ("&quot;", '"'),
                     ("&lt;", "<"), ("&gt;", ">")):
        text = text.replace(old, new)
    return " ".join(text.split()).strip()


def _fmt_ts(value):
    try:
        return time.strftime("%m-%d %H:%M", time.localtime(int(value)))
    except Exception:
        return _clean(value)[:16]


# --------------------------------------------------------------------------
# 数据源
# --------------------------------------------------------------------------
def fetch_wallstreetcn(limit=50):
    """华尔街见闻全球频道快讯。"""
    url = ("https://api-one.wallstcn.com/apiv1/content/lives?"
           "channel=global-channel&client=pc&limit=%d" % max(10, min(limit, 100)))
    text = datasource._http_get(url, referer="https://wallstreetcn.com/live/global",
                                encoding="utf-8")
    if not text:
        return []
    try:
        payload = json.loads(text)
    except Exception:
        return []
    items = []
    for row in ((payload.get("data") or {}).get("items") or [])[:limit]:
        title = _clean(row.get("title") or "")
        body = _clean(row.get("content_text") or row.get("content") or "")
        if not title:
            title = body[:60]
        items.append({
            "time": _fmt_ts(row.get("display_time")),
            "title": title,
            "digest": body[:400],
            "url": (row.get("uri") or ""),
            "source": "华尔街见闻",
        })
    return [i for i in items if i["title"]]


def fetch_eastmoney_flash(limit=80):
    """东方财富 7x24 快讯。"""
    url = ("https://newsapi.eastmoney.com/kuaixun/v1/"
           "getlist_102_ajaxResult_%d_1_.html" % max(20, min(limit, 100)))
    text = datasource._http_get(url, referer="https://kuaixun.eastmoney.com/",
                                encoding="utf-8")
    if not text or "{" not in text:
        return []
    payload = text[text.index("{"):].rstrip().rstrip(";")
    try:
        data = json.loads(payload)
    except Exception:
        return []
    items = []
    for row in (data.get("LivesList") or [])[:limit]:
        items.append({
            "time": _clean(row.get("showtime") or row.get("showTime") or ""),
            "title": _clean(row.get("title") or ""),
            "digest": _clean(row.get("digest") or "")[:400],
            "url": row.get("url_w") or row.get("url_m") or "",
            "source": "东方财富",
        })
    return [i for i in items if i["title"]]


# --------------------------------------------------------------------------
# 归类
# --------------------------------------------------------------------------
def _has_any(blob, words):
    for word in words:
        if word in blob:
            return word
    return None


def classify(items, per_topic=10):
    """按主题归类，返回 {topic: [item]}。"""
    buckets = {name: [] for name, _ in TOPICS}
    for item in items:
        blob = (item.get("title", "") + " " + item.get("digest", ""))
        # 例行节目单/汇总类标题直接跳过，避免关键词凑齐污染各主题
        if _has_any(blob, TITLE_SKIP):
            continue
        has_oversea = _has_any(blob, OVERSEA_CONTEXT) is not None
        has_strong_oversea = _has_any(blob, STRONG_OVERSEA) is not None
        local_hit = _has_any(blob, LOCAL_ONLY)
        # 本地市场新闻：除非明确带强海外语境，否则直接跳过
        if local_hit and not has_strong_oversea:
            continue

        hits = []
        for name, rules in TOPICS:
            matched = _has_any(blob, rules["strong"])
            if not matched and has_oversea:
                matched = _has_any(blob, rules["weak"])
            if not matched:
                continue
            # 主题级排除词优先，压掉已知的高频误伤
            if _has_any(blob, TOPIC_EXCLUDE.get(name, [])):
                continue
            hits.append((name, matched))

        if not hits:
            continue
        for name, matched in hits:
            if len(buckets[name]) < per_topic:
                enriched = dict(item)
                enriched["matched"] = matched
                enriched["topics"] = [h[0] for h in hits]
                buckets[name].append(enriched)
    return buckets


def get_news_digest(limit=100):
    """抓取并归类，供盘前分析与日报使用。"""
    items = []
    seen = set()
    for fetcher in (fetch_wallstreetcn, fetch_eastmoney_flash):
        try:
            for item in fetcher(limit):
                key = re.sub(r"\W+", "", item["title"])[:30]
                if not key or key in seen:
                    continue
                seen.add(key)
                items.append(item)
        except Exception:
            continue

    buckets = classify(items)
    topics = []
    for name, _ in TOPICS:
        if buckets.get(name):
            topics.append({"topic": name, "count": len(buckets[name]),
                           "items": buckets[name]})

    # 关注度最高的主题排前面
    topics.sort(key=lambda t: t["count"], reverse=True)

    classified_titles = set()
    for topic in topics:
        for item in topic["items"]:
            classified_titles.add(item["title"])

    return {
        "generated_at": time.strftime("%Y-%m-%d %H:%M:%S"),
        "total": len(items),
        "matched": len(classified_titles),
        "topics": topics,
        "latest": items[:30],
    }
