# -*- coding: utf-8 -*-
"""日报生成（HTML）与邮件推送。

生成的报告落在 reports/ 目录，文件名形如 2026-09-02_daily.html。
配色遵循 A 股 / 中文用户习惯：涨红、跌绿。
"""

import html
import os
import smtplib
import time
import traceback
from email.header import Header
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText

from . import advisor, equity, macro, news, paths, store, valuation

_CSS = """
* { box-sizing: border-box; }
body { margin:0; padding:24px; background:#f4f6fa; color:#1c2333;
       font-family:"Microsoft YaHei","PingFang SC","Helvetica Neue",Arial,sans-serif;
       font-size:14px; line-height:1.6; }
.wrap { max-width:1080px; margin:0 auto; }
h1 { font-size:24px; margin:0 0 4px; }
h2 { font-size:18px; margin:28px 0 12px; padding-left:10px;
     border-left:4px solid #2b6cb0; }
h3 { font-size:15px; margin:18px 0 8px; color:#2d3748; }
.sub { color:#66708a; font-size:13px; margin-bottom:18px; }
.card { background:#fff; border:1px solid #e3e8f0; border-radius:10px;
        padding:16px 18px; margin-bottom:14px; }
.grid { display:flex; flex-wrap:wrap; gap:12px; }
.kpi { flex:1 1 150px; background:#fff; border:1px solid #e3e8f0;
       border-radius:10px; padding:14px 16px; }
.kpi .label { color:#66708a; font-size:12px; }
.kpi .value { font-size:20px; font-weight:600; margin-top:4px; }
table { width:100%; border-collapse:collapse; background:#fff;
        border:1px solid #e3e8f0; border-radius:8px; overflow:hidden; }
th,td { padding:8px 10px; text-align:right; border-bottom:1px solid #eef1f6;
        font-size:13px; white-space:nowrap; }
th { background:#f7f9fc; color:#48536b; font-weight:600; text-align:right; }
th:first-child, td:first-child, th:nth-child(2), td:nth-child(2) { text-align:left; }
tr:last-child td { border-bottom:none; }
.up { color:#d93025; font-weight:600; }
.down { color:#14804a; font-weight:600; }
.flat { color:#5a6478; }
.tag { display:inline-block; padding:2px 9px; border-radius:999px;
       font-size:12px; font-weight:600; }
.tag-long { background:#fdecea; color:#c5221f; }
.tag-short { background:#e6f4ea; color:#137333; }
.tag-neutral { background:#eef1f6; color:#48536b; }
.factor { display:flex; justify-content:space-between; gap:12px;
          padding:6px 0; border-bottom:1px dashed #eef1f6; }
.factor:last-child { border-bottom:none; }
.factor .fname { flex:0 0 110px; font-weight:600; color:#2d3748; }
.factor .fdetail { flex:1; color:#4a5568; }
.factor .fscore { flex:0 0 56px; text-align:right; font-weight:600; }
.levels { display:flex; flex-wrap:wrap; gap:8px 20px; margin-top:10px;
          padding-top:10px; border-top:1px solid #eef1f6; }
.levels div { font-size:12px; color:#4a5568; }
.levels b { color:#1c2333; }
ul.news { margin:6px 0 0; padding-left:18px; }
ul.news li { margin-bottom:4px; color:#3c4considerations; }
.news-time { color:#8b93a7; font-size:12px; margin-right:6px; }
.footer { margin-top:26px; padding-top:14px; border-top:1px solid #e3e8f0;
          color:#8b93a7; font-size:12px; }
.bar { height:8px; border-radius:4px; background:#eef1f6; position:relative;
       overflow:hidden; margin-top:6px; }
.bar span { position:absolute; top:0; bottom:0; }
.brief-lead { background:#eef4ff; border-left:3px solid #2b6cb0;
              padding:10px 14px; border-radius:0 6px 6px 0; margin-bottom:12px;
              line-height:1.7; }
.brief-lead b { color:#1c4480; font-size:14px; }
.bf { margin-bottom:16px; }
.bf h4 { margin:0 0 4px; font-size:13.5px; padding-left:8px;
         border-left:3px solid #d93025; }
.bf.bf-dont h4 { border-color:#e8a33d; }
.bf.bf-watch h4 { border-color:#8b93a7; }
.b-item { padding:8px 0 8px 8px; border-bottom:1px dashed #eef1f6;
          font-size:13px; line-height:1.65; }
.b-item:last-child { border-bottom:none; }
.b-item .b-how { color:#1c2333; }
.b-item .b-why, .b-item .b-trigger { color:#4a5568; font-size:12.5px; }
.b-item .b-cite { color:#8b93a7; font-size:12px; }
.b-risk { margin-top:10px; padding:8px 12px; background:#fdf6e8;
          border-radius:6px; color:#7a5b12; font-size:12.5px; line-height:1.7; }
"""

_CSS = _CSS.replace("#3c4considerations", "#3c4457")


def _esc(text):
    return html.escape(str(text if text is not None else ""))


def _cls(value):
    if value is None:
        return "flat"
    if value > 0:
        return "up"
    if value < 0:
        return "down"
    return "flat"


def _pct(value, digits=2):
    if value is None:
        return "—"
    return "%+.*f%%" % (digits, value)


def _money(value, digits=2):
    if value is None:
        return "—"
    return "%s%s" % ("-" if value < 0 else "", format(abs(value), ",.%df" % digits))


def _signed_money(value, digits=2):
    if value is None:
        return "—"
    sign = "+" if value > 0 else ("-" if value < 0 else "")
    return "%s%s" % (sign, format(abs(value), ",.%df" % digits))


def _stance_tag(score):
    if score >= 15:
        return '<span class="tag tag-long">偏多</span>'
    if score <= -15:
        return '<span class="tag tag-short">偏空</span>'
    return '<span class="tag tag-neutral">中性</span>'


def _score_bar(score):
    """把 -100~100 的得分画成一条居中发散的进度条。"""
    pct = max(-100.0, min(100.0, score)) / 2.0  # 半宽百分比
    if score >= 0:
        return ('<div class="bar"><span style="left:50%%;width:%.1f%%;'
                'background:#d93025"></span></div>' % pct)
    return ('<div class="bar"><span style="right:50%%;width:%.1f%%;'
            'background:#14804a"></span></div>' % abs(pct))


# --------------------------------------------------------------------------
# 各区块渲染
# --------------------------------------------------------------------------
def _render_summary(summary, cfg):
    rate = float(cfg.get("usd_cny") or 7.1)
    items = [
        ("总市值", "$" + _money(summary["market_value"]), "flat"),
        ("总成本", "$" + _money(summary["cost_value"]), "flat"),
        ("累计盈亏", "$" + _signed_money(summary["pnl"]), _cls(summary["pnl"])),
        ("累计收益率", _pct(summary["pnl_pct"]), _cls(summary["pnl_pct"])),
        ("当日盈亏", "$" + _signed_money(summary["day_pnl"]), _cls(summary["day_pnl"])),
        ("当日涨跌", _pct(summary["day_pnl_pct"]), _cls(summary["day_pnl_pct"])),
        ("折合人民币", "¥" + _money(summary["market_value"] * rate, 0), "flat"),
        ("持仓数量", "%d 只（盈 %d / 亏 %d）"
         % (summary["position_count"], summary["winner_count"], summary["loser_count"]),
         "flat"),
    ]
    cells = "".join(
        '<div class="kpi"><div class="label">%s</div>'
        '<div class="value %s">%s</div></div>' % (_esc(label), cls, _esc(value))
        for label, value, cls in items)
    return '<div class="grid">%s</div>' % cells


def _render_positions(rows):
    if not rows:
        return '<div class="card">当前没有持仓。在平台里搜索代码即可添加。</div>'
    head = ("<tr><th>代码</th><th>名称</th><th>方向</th><th>数量</th><th>成本价</th>"
            "<th>现价</th><th>日涨跌</th><th>市值</th><th>浮动盈亏</th>"
            "<th>收益率</th><th>当日盈亏</th><th>仓位</th></tr>")
    body = []
    for r in rows:
        body.append(
            "<tr><td><b>%s</b></td><td>%s</td><td>%s</td><td>%s</td><td>%s</td>"
            "<td>%s</td><td class='%s'>%s</td><td>%s</td><td class='%s'>%s</td>"
            "<td class='%s'>%s</td><td class='%s'>%s</td><td>%.1f%%</td></tr>"
            % (_esc(r["symbol"]), _esc(r["name"]),
               "做多" if r["direction"] == "long" else "做空",
               _money(r["shares"], 0), _money(r["cost"]), _money(r["price"]),
               _cls(r["change_pct"]), _pct(r["change_pct"]),
               _money(r["market_value"]),
               _cls(r["pnl"]), _signed_money(r["pnl"]),
               _cls(r["pnl_pct"]), _pct(r["pnl_pct"]),
               _cls(r["day_pnl"]), _signed_money(r["day_pnl"]),
               r["weight"]))
    return "<table>%s%s</table>" % (head, "".join(body))


def _render_watchlist(rows):
    if not rows:
        return ""
    head = "<tr><th>代码</th><th>名称</th><th>现价</th><th>日涨跌</th></tr>"
    body = "".join(
        "<tr><td><b>%s</b></td><td>%s</td><td>%s</td><td class='%s'>%s</td></tr>"
        % (_esc(r["symbol"]), _esc(r["name"]), _money(r["price"]),
           _cls(r["change_pct"]), _pct(r["change_pct"]))
        for r in rows)
    return "<h2>自选观察</h2><table>%s%s</table>" % (head, body)


def _render_brief(brief):
    """今日策略文字讲解：该做什么 / 不该做什么 / 观望。"""
    if not brief:
        return ""
    parts = ['<div class="brief-lead"><b>%s</b><br>%s</div>'
             % (_esc(brief.get("headline") or ""), _esc(brief.get("lead") or ""))]

    do_items = brief.get("do") or []
    if do_items:
        rows = []
        for it in do_items:
            tag = it.get("tag")
            tag_cls = "tag-long" if tag == "做多" else "tag-short"
            cite = it.get("cite")
            cite_html = ('<div class="b-cite">盘前快讯佐证：%s</div>'
                         % _esc(cite)) if cite else ""
            rows.append(
                '<div class="b-item"><div><span class="tag %s">%s</span> '
                '<b>%s（首选工具 %s）</b></div>'
                '<div class="b-how">怎么做：%s</div>'
                '<div class="b-why">为什么：%s</div>%s</div>'
                % (tag_cls, _esc(tag), _esc(it.get("asset") or ""),
                   _esc(it.get("symbol") or "—"), _esc(it.get("how") or ""),
                   _esc(it.get("why") or ""), cite_html))
        parts.append('<div class="bf bf-do"><h4>今天该做什么</h4>%s</div>'
                     % "".join(rows))

    dont_items = brief.get("dont") or []
    if dont_items:
        rows = "".join(
            '<div class="b-item"><div><span class="tag tag-neutral">%s</span></div>'
            '<div class="b-why">%s</div></div>'
            % (_esc(it.get("tag") or "注意"), _esc(it.get("why") or ""))
            for it in dont_items)
        parts.append('<div class="bf bf-dont"><h4>今天不该做什么</h4>%s</div>' % rows)

    watch_items = brief.get("watch") or []
    if watch_items:
        rows = "".join(
            '<div class="b-item"><b>%s</b>（%+.1f 分）：%s'
            '<div class="b-trigger">触发条件：%s</div></div>'
            % (_esc(it.get("asset") or ""), float(it.get("score") or 0),
               _esc(it.get("why") or ""), _esc(it.get("trigger") or ""))
            for it in watch_items)
        parts.append('<div class="bf bf-watch"><h4>观望 · 等确认</h4>%s</div>' % rows)

    parts.append('<div class="b-risk">风险提示：%s</div>'
                 % _esc(brief.get("risk") or ""))
    return "".join(parts)


def _render_environment(env):
    rows = [
        ("利率环境", env["rate_state"],
         "TLT 日内 %s / 五日 %s，IEF 五日 %s"
         % (_pct(env["tlt_chg1"]), _pct(env["tlt_chg5"]), _pct(env["ief_chg5"]))),
        ("风险偏好", env["risk_state"],
         "纳指100 减必需消费五日差 %s，VIX 期货五日 %s"
         % (_pct(env["risk_spread"], 1), _pct(env["vix_mom5"], 1))),
        ("地缘溢价", env["geo_state"], env["geo_detail"]),
        ("银行金融", env["bank_state"],
         "XLF 五日 %s，KRE 五日 %s" % (_pct(env["bank_chg5"], 1), _pct(env["kre_chg5"], 1))),
        ("防御消费", "可口可乐五日 %s" % _pct(env["ko_chg5"], 1),
         "纳指100 日内 %s / 五日 %s" % (_pct(env["qqq_chg1"]), _pct(env["qqq_chg5"]))),
    ]
    body = "".join(
        '<div class="factor"><div class="fname">%s</div>'
        '<div class="fdetail"><b>%s</b><br><span style="color:#6b7590">%s</span></div>'
        '</div>' % (_esc(name), _esc(state), _esc(detail))
        for name, state, detail in rows)
    return '<div class="card">%s</div>' % body


def _render_signals(signals):
    blocks = []
    for sig in signals:
        factors = "".join(
            '<div class="factor"><div class="fname">%s</div>'
            '<div class="fdetail">%s</div>'
            '<div class="fscore %s">%+.2f</div></div>'
            % (_esc(f["name"]), _esc(f["detail"]), _cls(f["score"]), f["score"])
            for f in sig["factors"])
        levels = "".join('<div>%s：<b>%s</b></div>' % (_esc(k), _esc(v))
                         for k, v in sig["levels"].items())
        blocks.append(
            '<div class="card"><h3>%s %s　得分 <span class="%s">%+.1f</span>'
            '　建议：%s（信心 %s）</h3>%s%s<div class="levels">%s</div></div>'
            % (_esc(sig["asset"]), _stance_tag(sig["score"]), _cls(sig["score"]),
               sig["score"], _esc(sig["action"]), _esc(sig["confidence"]),
               _score_bar(sig["score"]), factors, levels))
    return "".join(blocks)


def _render_recommendations(recs):
    if not recs:
        return ""
    head = ("<tr><th>板块</th><th>方向</th><th>ETF</th><th>优先级</th>"
            "<th style='text-align:left'>推荐理由</th></tr>")
    body = []
    for rec in recs:
        side_cls = ("tag-long" if rec["side"] == "做多"
                    else ("tag-short" if rec["side"] == "做空" else "tag-neutral"))
        body.append(
            "<tr><td>%s</td><td><span class='tag %s'>%s</span></td>"
            "<td><b>%s</b></td><td>%s</td>"
            "<td style='text-align:left;white-space:normal'>%s</td></tr>"
            % (_esc(rec["asset"]), side_cls, _esc(rec["side"]),
               _esc(rec["symbol"]), _esc(rec["priority"]), _esc(rec["reason"])))
    return "<table>%s%s</table>" % (head, "".join(body))


def _render_news(digest):
    if not digest or not digest.get("topics"):
        return '<div class="card">未获取到快讯（可能是网络受限）。</div>'
    blocks = []
    for topic in digest["topics"][:8]:
        lis = "".join(
            '<li><span class="news-time">%s</span>%s</li>'
            % (_esc(item["time"]), _esc(item["title"][:110]))
            for item in topic["items"][:6])
        blocks.append('<div class="card"><h3>%s（%d 条）</h3><ul class="news">%s</ul></div>'
                      % (_esc(topic["topic"]), topic["count"], lis))
    return "".join(blocks)


def _render_equity_curve(snapshots):
    items = snapshots.get("items", [])[-60:]
    if len(items) < 2:
        return ""
    values = [i["market_value"] for i in items]
    low, high = min(values), max(values)
    span = (high - low) or 1.0
    width, height, pad = 1000, 180, 24
    step = (width - pad * 2) / float(len(values) - 1)
    points = []
    for idx, value in enumerate(values):
        x = pad + idx * step
        y = height - pad - (value - low) / span * (height - pad * 2)
        points.append("%.1f,%.1f" % (x, y))
    poly = " ".join(points)
    area = "%s %.1f,%.1f %.1f,%.1f" % (poly, pad + (len(values) - 1) * step,
                                       height - pad, pad, height - pad)
    return ('<h2>净值走势（近 %d 个记录日）</h2><div class="card">'
            '<svg viewBox="0 0 %d %d" style="width:100%%;height:200px">'
            '<polygon points="%s" fill="rgba(43,108,176,.12)"/>'
            '<polyline points="%s" fill="none" stroke="#2b6cb0" stroke-width="2"/>'
            '</svg><div style="color:#6b7590;font-size:12px">区间 $%s ~ $%s，'
            '起 %s 止 %s</div></div>'
            % (len(values), width, height, area, poly, _money(low, 0), _money(high, 0),
               _esc(items[0]["date"]), _esc(items[-1]["date"])))


# --------------------------------------------------------------------------
# 主流程
# --------------------------------------------------------------------------
def build_report_html(kind="daily", include_news=True, include_signals=True):
    """生成日报 HTML 字符串与元数据。"""
    cfg = store.load_config()
    port = valuation.evaluate_portfolio(ttl=3)
    summary = port["summary"]

    analysis = macro.build_premarket_analysis() if include_signals else None
    digest = news.get_news_digest() if include_news else None
    # 净值走势改用「按确认买入时间回溯重建」的逐日序列；失败时退回原始快照
    try:
        snapshots = equity.build_equity_series()["data"]
    except Exception:
        traceback.print_exc()
        snapshots = store.load_snapshots()

    now = time.strftime("%Y-%m-%d %H:%M:%S")
    title = "美股持仓日报" if kind == "daily" else "美股盘前策略报告"

    parts = ['<div class="wrap">',
             "<h1>%s</h1>" % _esc(title),
             '<div class="sub">生成时间 %s　|　行情来源：腾讯财经 / 东方财富　'
             '|　快讯来源：华尔街见闻 / 东方财富</div>' % _esc(now)]

    parts.append("<h2>持仓总览</h2>")
    parts.append(_render_summary(summary, cfg))
    if summary["stale_symbols"]:
        parts.append('<div class="card">以下代码未取到行情，已按成本价计入：%s</div>'
                     % _esc("、".join(summary["stale_symbols"])))

    parts.append("<h2>持仓明细</h2>")
    parts.append(_render_positions(port["positions"]))
    parts.append(_render_watchlist(port["watchlist"]))
    parts.append(_render_equity_curve(snapshots))

    if analysis:
        env = analysis["environment"]
        brief = None
        try:
            brief = advisor.build_brief(analysis, digest)
        except Exception:
            traceback.print_exc()
        if brief:
            parts.append("<h2>今日策略 · 该做什么 / 不该做什么</h2>")
            parts.append(_render_brief(brief))
        parts.append("<h2>宏观环境</h2>")
        parts.append(_render_environment(env))
        parts.append("<h2>日内多空信号（原油 / 黄金 / 半导体）</h2>")
        parts.append(_render_signals(analysis["signals"]))
        parts.append("<h2>ETF 操作建议</h2>")
        parts.append(_render_recommendations(analysis["recommendations"]))

    if digest:
        parts.append("<h2>消息面（按主题归类）</h2>")
        parts.append(_render_news(digest))

    parts.append('<div class="footer">%s<br>本报告由本地程序自动生成，'
                 '数据可能存在延迟，请以券商终端为准。</div>'
                 % _esc(analysis["disclaimer"] if analysis else
                        "本报告仅供研究参考，不构成投资建议。"))
    parts.append("</div>")

    doc = ("<!DOCTYPE html><html lang=\"zh-CN\"><head><meta charset=\"utf-8\">"
           "<meta name=\"viewport\" content=\"width=device-width,initial-scale=1\">"
           "<title>%s %s</title><style>%s</style></head><body>%s</body></html>"
           % (_esc(title), _esc(time.strftime("%Y-%m-%d")), _CSS, "".join(parts)))

    return {"html": doc, "title": title, "generated_at": now,
            "summary": summary, "analysis": analysis}


def save_report(kind="daily", include_news=True, include_signals=True):
    """生成并落盘，返回文件路径。"""
    built = build_report_html(kind=kind, include_news=include_news,
                             include_signals=include_signals)
    date_str = time.strftime("%Y-%m-%d")
    suffix = "daily" if kind == "daily" else "premarket"
    filename = "%s_%s.html" % (date_str, suffix)
    path = os.path.join(paths.reports_dir(), filename)
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(built["html"])

    summary = built["summary"]
    store.save_snapshot(date_str, summary["market_value"], summary["cost_value"],
                        summary["pnl"], summary["day_pnl"])
    built["path"] = path
    built["filename"] = filename
    return built


def list_reports():
    directory = paths.reports_dir()
    items = []
    for name in sorted(os.listdir(directory), reverse=True):
        if not name.endswith(".html"):
            continue
        full = os.path.join(directory, name)
        try:
            stat = os.stat(full)
        except Exception:
            continue
        items.append({"filename": name, "size": stat.st_size,
                      "modified": time.strftime("%Y-%m-%d %H:%M:%S",
                                                time.localtime(stat.st_mtime))})
    return items


# --------------------------------------------------------------------------
# 邮件
# --------------------------------------------------------------------------
def send_email(subject, html_body, cfg=None):
    """通过 SMTP 发送 HTML 邮件。返回 (ok, message)。"""
    cfg = cfg or store.load_config()
    mail = cfg.get("email") or {}
    if not mail.get("enabled"):
        return False, "邮件推送未启用"
    host = (mail.get("smtp_host") or "").strip()
    username = (mail.get("username") or "").strip()
    password = mail.get("password") or ""
    sender = (mail.get("sender") or username).strip()
    receivers = [r.strip() for r in (mail.get("receivers") or []) if r and r.strip()]
    if not (host and username and password and receivers):
        return False, "邮件配置不完整（需要 smtp_host / username / password / receivers）"

    port = int(mail.get("smtp_port") or (465 if mail.get("use_ssl") else 587))
    message = MIMEMultipart("alternative")
    message["Subject"] = Header(subject, "utf-8")
    message["From"] = sender
    message["To"] = ", ".join(receivers)
    message.attach(MIMEText(html_body, "html", "utf-8"))

    try:
        if mail.get("use_ssl"):
            server = smtplib.SMTP_SSL(host, port, timeout=25)
        else:
            server = smtplib.SMTP(host, port, timeout=25)
            server.starttls()
        try:
            server.login(username, password)
            server.sendmail(sender, receivers, message.as_string())
        finally:
            try:
                server.quit()
            except Exception:
                pass
    except Exception as exc:
        return False, "发送失败：%s" % exc
    return True, "已发送至 %s" % ", ".join(receivers)


def generate_and_send(kind="daily"):
    """生成报告并按配置推送邮件。"""
    built = save_report(kind=kind)
    subject = "%s %s" % (built["title"], time.strftime("%Y-%m-%d"))
    ok, message = send_email(subject, built["html"])
    built["email_ok"] = ok
    built["email_message"] = message
    return built
