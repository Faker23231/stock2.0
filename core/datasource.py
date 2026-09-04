# -*- coding: utf-8 -*-
"""行情数据源模块。

主源：腾讯财经（qt.gtimg.cn / smartbox.gtimg.cn / web.ifzq.gtimg.cn）
备源：东方财富（push2.eastmoney.com）

设计原则：
1. 仅依赖 Python 标准库，方便 PyInstaller 打成单文件 exe；
2. 所有网络异常都被吞掉并返回空结果，绝不让界面因为网络抖动崩溃；
3. 带 TTL 内存缓存，避免高频刷新把上游打爆。
"""

import json
import re
import ssl
import threading
import time
import urllib.parse
import urllib.request

_UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
       "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36")

# 部分企业网络会做 SSL 中间人，这里不校验证书以保证可用性
_SSL_CTX = ssl.create_default_context()
_SSL_CTX.check_hostname = False
_SSL_CTX.verify_mode = ssl.CERT_NONE


# --------------------------------------------------------------------------
# 基础工具
# --------------------------------------------------------------------------
class TTLCache:
    """极简线程安全 TTL 缓存。"""

    def __init__(self):
        self._data = {}
        self._lock = threading.Lock()

    def get(self, key):
        with self._lock:
            item = self._data.get(key)
            if not item:
                return None
            expire_at, value = item
            if time.time() > expire_at:
                self._data.pop(key, None)
                return None
            return value

    def set(self, key, value, ttl):
        with self._lock:
            self._data[key] = (time.time() + ttl, value)

    def clear(self):
        with self._lock:
            self._data.clear()


_cache = TTLCache()
# 记录 symbol -> 腾讯完整代码（如 SMH -> SMH.OQ），K 线接口需要带后缀
_full_code_map = {}
_full_code_lock = threading.Lock()


def _http_get(url, referer=None, encoding="gbk", timeout=12):
    """发起 GET 请求，返回文本；失败返回 None。"""
    headers = {"User-Agent": _UA, "Accept": "*/*"}
    if referer:
        headers["Referer"] = referer
    req = urllib.request.Request(url, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=timeout, context=_SSL_CTX) as resp:
            raw = resp.read()
    except Exception:
        return None
    for enc in (encoding, "utf-8", "gbk", "latin-1"):
        try:
            return raw.decode(enc)
        except Exception:
            continue
    return None


def _to_float(text, default=0.0):
    try:
        if text in (None, "", "-", "null"):
            return default
        return float(text)
    except Exception:
        return default


def normalize_symbol(symbol):
    """统一为大写、去空格的美股代码。"""
    return (symbol or "").strip().upper().replace(" ", "")


# --------------------------------------------------------------------------
# 实时行情（腾讯为主）
# --------------------------------------------------------------------------
# 腾讯美股返回字段（以 ~ 分隔）的下标含义
_T_NAME = 1
_T_CODE = 2
_T_PRICE = 3
_T_PREV = 4
_T_OPEN = 5
_T_VOLUME = 6
_T_TIME = 30
_T_CHANGE = 31
_T_PCT = 32
_T_HIGH = 33
_T_LOW = 34
_T_CURRENCY = 35
_T_AMOUNT = 37
_T_PE = 39
_T_AMPLITUDE = 43
_T_MKTCAP = 45
_T_EN_NAME = 46
_T_WEEK52_HIGH = 48
_T_WEEK52_LOW = 49


def _parse_tencent_line(line):
    """解析单条 v_usXXX="..." 行。"""
    if "=" not in line or '"' not in line:
        return None
    left, right = line.split("=", 1)
    symbol = left.strip()
    if symbol.startswith("v_us"):
        symbol = symbol[4:]
    elif symbol.startswith("v_"):
        symbol = symbol[2:]
    fields = right.strip().strip(";").strip('"').split("~")
    if len(fields) < 36:
        return None
    price = _to_float(fields[_T_PRICE])
    if price <= 0:
        return None
    prev = _to_float(fields[_T_PREV])
    full_code = fields[_T_CODE]
    with _full_code_lock:
        if full_code:
            _full_code_map[symbol.upper()] = full_code
    return {
        "symbol": symbol.upper(),
        "full_code": full_code,
        "name": fields[_T_NAME],
        "en_name": fields[_T_EN_NAME] if len(fields) > _T_EN_NAME else "",
        "price": price,
        "prev_close": prev,
        "open": _to_float(fields[_T_OPEN]),
        "high": _to_float(fields[_T_HIGH]),
        "low": _to_float(fields[_T_LOW]),
        "volume": _to_float(fields[_T_VOLUME]),
        "amount": _to_float(fields[_T_AMOUNT]) if len(fields) > _T_AMOUNT else 0.0,
        "change": _to_float(fields[_T_CHANGE]),
        "change_pct": _to_float(fields[_T_PCT]),
        "amplitude": _to_float(fields[_T_AMPLITUDE]) if len(fields) > _T_AMPLITUDE else 0.0,
        "pe": _to_float(fields[_T_PE]) if len(fields) > _T_PE else 0.0,
        "market_cap": _to_float(fields[_T_MKTCAP]) if len(fields) > _T_MKTCAP else 0.0,
        "week52_high": _to_float(fields[_T_WEEK52_HIGH]) if len(fields) > _T_WEEK52_HIGH else 0.0,
        "week52_low": _to_float(fields[_T_WEEK52_LOW]) if len(fields) > _T_WEEK52_LOW else 0.0,
        "currency": fields[_T_CURRENCY] if len(fields) > _T_CURRENCY else "USD",
        "time": fields[_T_TIME],
        "source": "tencent",
    }


def _fetch_tencent_quotes(symbols):
    result = {}
    for i in range(0, len(symbols), 25):
        batch = symbols[i:i + 25]
        url = "https://qt.gtimg.cn/q=" + ",".join("us" + s for s in batch)
        text = _http_get(url, referer="https://gu.qq.com/")
        if not text:
            continue
        for line in text.split(";"):
            line = line.strip()
            if not line.startswith("v_"):
                continue
            item = _parse_tencent_line(line)
            if item:
                result[item["symbol"]] = item
    return result


_EM_MARKET_PREFIX = ("105", "106", "107")  # 纳斯达克 / 纽交所 / 美交所


def _fetch_eastmoney_quote(symbol):
    """东方财富单只兜底查询。"""
    fields = "f43,f44,f45,f46,f47,f48,f57,f58,f60,f168,f169,f170,f86,f116,f117,f162"
    for prefix in _EM_MARKET_PREFIX:
        url = ("https://push2.eastmoney.com/api/qt/stock/get?"
               "secid=%s.%s&fields=%s" % (prefix, symbol, fields))
        text = _http_get(url, referer="https://quote.eastmoney.com/", encoding="utf-8")
        if not text:
            continue
        try:
            data = json.loads(text).get("data")
        except Exception:
            continue
        if not data or not data.get("f43"):
            continue
        # 东财价格是放大 100 倍的整数
        scale = 100.0
        price = _to_float(data.get("f43")) / scale
        prev = _to_float(data.get("f60")) / scale
        if price <= 0:
            continue
        return {
            "symbol": symbol,
            "full_code": symbol,
            "name": data.get("f58") or symbol,
            "en_name": "",
            "price": price,
            "prev_close": prev,
            "open": _to_float(data.get("f46")) / scale,
            "high": _to_float(data.get("f44")) / scale,
            "low": _to_float(data.get("f45")) / scale,
            "volume": _to_float(data.get("f47")),
            "amount": _to_float(data.get("f48")),
            "change": _to_float(data.get("f169")) / scale,
            "change_pct": _to_float(data.get("f170")) / scale,
            "amplitude": 0.0,
            "pe": _to_float(data.get("f162")) / scale,
            "market_cap": _to_float(data.get("f116")) / 1e8,
            "week52_high": 0.0,
            "week52_low": 0.0,
            "currency": "USD",
            "time": "",
            "source": "eastmoney",
        }
    return None


def get_quotes(symbols, ttl=5):
    """批量取实时行情。返回 {SYMBOL: quote_dict}。"""
    symbols = [normalize_symbol(s) for s in symbols if normalize_symbol(s)]
    symbols = list(dict.fromkeys(symbols))  # 去重保序
    if not symbols:
        return {}

    result = {}
    missing = []
    for sym in symbols:
        cached = _cache.get("q:" + sym)
        if cached:
            result[sym] = cached
        else:
            missing.append(sym)

    if missing:
        fetched = _fetch_tencent_quotes(missing)
        for sym, item in fetched.items():
            _cache.set("q:" + sym, item, ttl)
            result[sym] = item
        # 腾讯没查到的，逐个走东财兜底
        for sym in missing:
            if sym in result:
                continue
            item = _fetch_eastmoney_quote(sym)
            if item:
                _cache.set("q:" + sym, item, ttl)
                result[sym] = item
    return result


def get_quote(symbol, ttl=5):
    return get_quotes([symbol], ttl=ttl).get(normalize_symbol(symbol))


# --------------------------------------------------------------------------
# 代码搜索
# --------------------------------------------------------------------------
_UNI_ESCAPE_RE = re.compile(r"\\u([0-9a-fA-F]{4})")


def _decode_unicode_escapes(text):
    """smartbox 返回的中文名是 \\uXXXX 字面量，需要还原成真正的汉字。"""
    if not text or "\\u" not in text:
        return text
    try:
        return _UNI_ESCAPE_RE.sub(lambda m: chr(int(m.group(1), 16)), text)
    except Exception:
        return text


def search_symbols(keyword, limit=15):
    """搜索美股代码。返回 [{symbol, name, exchange}]。"""
    keyword = (keyword or "").strip()
    if not keyword:
        return []
    cache_key = "s:" + keyword.lower()
    cached = _cache.get(cache_key)
    if cached is not None:
        return cached[:limit]

    results = []
    seen = set()

    url = "https://smartbox.gtimg.cn/s3/?q=%s&t=us" % urllib.parse.quote(keyword)
    text = _http_get(url, referer="https://gu.qq.com/")
    if text and '"' in text:
        payload = text.split('"')[1] if text.count('"') >= 2 else ""
        for chunk in payload.split("^"):
            parts = chunk.split("~")
            if len(parts) < 3:
                continue
            raw_code = parts[1].strip()
            if not raw_code:
                continue
            # raw_code 形如 ko.n / aapl.oq
            symbol = raw_code.split(".")[0].upper()
            exchange = raw_code.split(".")[1].upper() if "." in raw_code else ""
            if symbol in seen:
                continue
            seen.add(symbol)
            results.append({
                "symbol": symbol,
                "name": _decode_unicode_escapes(parts[2].strip()),
                "exchange": {"OQ": "NASDAQ", "N": "NYSE", "AM": "AMEX",
                             "PS": "OTC"}.get(exchange, exchange),
            })

    # 关键词本身就像代码时，直接补一条候选
    upper = normalize_symbol(keyword)
    if upper and upper not in seen and len(upper) <= 6 and upper.isalpha():
        quote = get_quote(upper, ttl=60)
        if quote:
            results.insert(0, {"symbol": upper, "name": quote["name"], "exchange": "US"})

    if results:
        _cache.set(cache_key, results, 300)
    return results[:limit]


# --------------------------------------------------------------------------
# 历史 K 线（多周期动能计算需要）
# --------------------------------------------------------------------------
_SUFFIX_CANDIDATES = (".OQ", ".N", ".AM", ".PS", "")


def get_daily_kline(symbol, count=90):
    """取日线。返回 [{date, open, close, high, low, volume}]，按时间升序。"""
    symbol = normalize_symbol(symbol)
    cache_key = "k:%s:%d" % (symbol, count)
    cached = _cache.get(cache_key)
    if cached is not None:
        return cached

    with _full_code_lock:
        known = _full_code_map.get(symbol)

    candidates = []
    if known:
        candidates.append(known)
    for suffix in _SUFFIX_CANDIDATES:
        code = symbol + suffix
        if code not in candidates:
            candidates.append(code)

    for code in candidates:
        url = ("https://web.ifzq.gtimg.cn/appstock/app/usfqkline/get?"
               "param=us%s,day,,,%d,qfq" % (code, count))
        text = _http_get(url, referer="https://gu.qq.com/", encoding="utf-8")
        if not text:
            continue
        try:
            payload = json.loads(text)
        except Exception:
            continue
        node = (payload.get("data") or {}).get("us" + code)
        if not node:
            continue
        rows = node.get("qfqday") or node.get("day") or []
        if len(rows) < 2:
            continue
        bars = []
        for row in rows:
            if len(row) < 6:
                continue
            bars.append({
                "date": row[0],
                "open": _to_float(row[1]),
                "close": _to_float(row[2]),
                "high": _to_float(row[3]),
                "low": _to_float(row[4]),
                "volume": _to_float(row[5]),
            })
        if bars:
            _cache.set(cache_key, bars, 900)
            return bars

    _cache.set(cache_key, [], 120)
    return []


# --------------------------------------------------------------------------
# 技术指标
# --------------------------------------------------------------------------
def sma(values, period):
    if len(values) < period or period <= 0:
        return None
    return sum(values[-period:]) / float(period)


def rsi(values, period=14):
    """经典 Wilder RSI。"""
    if len(values) < period + 1:
        return None
    gains, losses = 0.0, 0.0
    for i in range(1, period + 1):
        diff = values[i] - values[i - 1]
        if diff >= 0:
            gains += diff
        else:
            losses -= diff
    avg_gain = gains / period
    avg_loss = losses / period
    for i in range(period + 1, len(values)):
        diff = values[i] - values[i - 1]
        gain = diff if diff > 0 else 0.0
        loss = -diff if diff < 0 else 0.0
        avg_gain = (avg_gain * (period - 1) + gain) / period
        avg_loss = (avg_loss * (period - 1) + loss) / period
    if avg_loss == 0:
        return 100.0
    rs = avg_gain / avg_loss
    return 100.0 - (100.0 / (1.0 + rs))


def atr_pct(bars, period=14):
    """ATR 占收盘价百分比，用于衡量波动率。"""
    if len(bars) < period + 1:
        return None
    trs = []
    for i in range(1, len(bars)):
        high, low = bars[i]["high"], bars[i]["low"]
        prev_close = bars[i - 1]["close"]
        trs.append(max(high - low, abs(high - prev_close), abs(low - prev_close)))
    if len(trs) < period:
        return None
    value = sum(trs[-period:]) / float(period)
    last_close = bars[-1]["close"]
    if last_close <= 0:
        return None
    return value / last_close * 100.0


def momentum_pct(bars, lookback):
    """N 日动能（收益率 %）。"""
    if len(bars) < lookback + 1:
        return None
    old = bars[-1 - lookback]["close"]
    new = bars[-1]["close"]
    if old <= 0:
        return None
    return (new - old) / old * 100.0


def build_indicator_pack(symbol, bars=None):
    """汇总一只标的的技术画像，供信号引擎使用。"""
    bars = bars if bars is not None else get_daily_kline(symbol, 90)
    if not bars:
        return None
    closes = [b["close"] for b in bars]
    last = closes[-1]
    ma5, ma10, ma20, ma50 = (sma(closes, 5), sma(closes, 10),
                             sma(closes, 20), sma(closes, 50))
    pack = {
        "symbol": symbol,
        "last_close": last,
        "bars": len(bars),
        "chg1": momentum_pct(bars, 1),
        "chg3": momentum_pct(bars, 3),
        "chg5": momentum_pct(bars, 5),
        "chg10": momentum_pct(bars, 10),
        "chg20": momentum_pct(bars, 20),
        "ma5": ma5,
        "ma10": ma10,
        "ma20": ma20,
        "ma50": ma50,
        "rsi14": rsi(closes, 14),
        "atr_pct": atr_pct(bars, 14),
        "above_ma20": (last > ma20) if ma20 else None,
        "above_ma50": (last > ma50) if ma50 else None,
        "ma_stack_up": bool(ma5 and ma10 and ma20 and ma5 > ma10 > ma20),
        "ma_stack_down": bool(ma5 and ma10 and ma20 and ma5 < ma10 < ma20),
    }
    highs = [b["high"] for b in bars]
    lows = [b["low"] for b in bars]
    window = min(20, len(bars))
    pack["high20"] = max(highs[-window:])
    pack["low20"] = min(lows[-window:])
    if pack["high20"] > pack["low20"]:
        pack["pos_in_range20"] = (last - pack["low20"]) / (pack["high20"] - pack["low20"]) * 100.0
    else:
        pack["pos_in_range20"] = 50.0
    return pack


def clear_cache():
    _cache.clear()
