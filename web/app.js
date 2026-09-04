/* 美股持仓管理平台 —— 前端逻辑（无框架，纯原生） */
(function () {
"use strict";

// ------------------------------------------------------------------
// 工具
// ------------------------------------------------------------------
var $ = function (id) { return document.getElementById(id); };

function esc(s) {
    return String(s === null || s === undefined ? "" : s)
        .replace(/&/g, "&amp;").replace(/</g, "&lt;")
        .replace(/>/g, "&gt;").replace(/"/g, "&quot;");
}

function num(v, digits) {
    if (v === null || v === undefined || isNaN(v)) return "--";
    digits = digits === undefined ? 2 : digits;
    return Number(v).toLocaleString("en-US",
        { minimumFractionDigits: digits, maximumFractionDigits: digits });
}

function signed(v, digits, suffix) {
    if (v === null || v === undefined || isNaN(v)) return "--";
    var s = (v > 0 ? "+" : "") + num(v, digits);
    return s + (suffix || "");
}

// 中文习惯：涨红跌绿
function cls(v) {
    if (v === null || v === undefined || isNaN(v) || Number(v) === 0) return "flat";
    return Number(v) > 0 ? "up" : "down";
}

var toastTimer = null;
function toast(msg, kind) {
    var el = $("toast");
    el.textContent = msg;
    el.className = "toast show" + (kind ? " " + kind : "");
    clearTimeout(toastTimer);
    toastTimer = setTimeout(function () { el.className = "toast"; }, 3200);
}

// ------------------------------------------------------------------
// 访问口令（auth_token）：部署公网时必须开启，前端自动带 X-Auth-Token
// ------------------------------------------------------------------
var authWait = null;

function storedToken() {
    try { return localStorage.getItem("usd_token") || ""; } catch (e) { return ""; }
}

function askToken() {
    return new Promise(function (resolve) {
        var mask = $("auth-mask"), input = $("auth-input"),
            err = $("auth-err"), okBtn = $("auth-ok"), cancelBtn = $("auth-cancel");
        err.textContent = "";
        input.value = "";
        mask.style.display = "flex";
        setTimeout(function () { input.focus(); }, 60);
        function done(val) { mask.style.display = "none"; resolve(val); }
        function submit() {
            var v = input.value.trim();
            if (!v) { err.textContent = "口令不能为空"; input.focus(); return; }
            done(v);
        }
        okBtn.onclick = submit;
        cancelBtn.onclick = function () { done(null); };
        input.onkeydown = function (ev) {
            if (ev.key === "Enter") submit();
            else if (ev.key === "Escape") done(null);
        };
    });
}

function ensureToken() {
    var t = storedToken();
    if (t) return Promise.resolve(t);
    if (authWait) return authWait;
    authWait = askToken().then(function (v) {
        if (v) { try { localStorage.setItem("usd_token", v); } catch (e) {} }
        return v;
    });
    authWait.then(function () { authWait = null; }, function () { authWait = null; });
    return authWait;
}

function authedHref(url) {
    var t = storedToken();
    if (!t) return url;
    return url + (url.indexOf("?") >= 0 ? "&" : "?") + "token=" + encodeURIComponent(t);
}

function api(path, body) {
    var opt = { headers: { "Content-Type": "application/json" } };
    var t = storedToken();
    if (t) opt.headers["X-Auth-Token"] = t;
    if (body !== undefined) {
        opt.method = "POST";
        opt.body = JSON.stringify(body);
    }
    return fetch(path, opt).then(function (r) {
        return r.json().catch(function () { return {}; }).then(function (j) {
            if (r.status === 401 && (!j || j.error === "unauthorized")) {
                try { localStorage.removeItem("usd_token"); } catch (e) {}
                return ensureToken().then(function (tok) {
                    if (!tok) throw new Error("未授权：需要访问口令才能查看");
                    return api(path, body);   // 已带新口令重试
                });
            }
            return j;
        });
    });
}

function setConn(state, text) {
    $("conn-dot").className = "dot" + (state ? " " + state : "");
    $("conn-text").textContent = text;
}

// ------------------------------------------------------------------
// 标签页
// ------------------------------------------------------------------
var lazyLoaded = {};
document.querySelectorAll("nav.tabs button").forEach(function (btn) {
    btn.addEventListener("click", function () {
        document.querySelectorAll("nav.tabs button").forEach(function (b) {
            b.classList.remove("active");
        });
        btn.classList.add("active");
        var target = btn.getAttribute("data-page");
        document.querySelectorAll(".page").forEach(function (p) {
            p.classList.toggle("active", p.id === target);
        });
        // 首次进入时懒加载
        if (!lazyLoaded[target]) {
            lazyLoaded[target] = true;
            if (target === "page-signal") loadSignals(false);
            if (target === "page-news") loadNews(false);
            if (target === "page-portfolio") loadEquity();
            if (target === "page-report") { loadReports(); loadSchedule(); }
            if (target === "page-settings") { loadConfig(); }
        }
    });
});

// ------------------------------------------------------------------
// 持仓总览
// ------------------------------------------------------------------
var cfgCache = { refresh_seconds: 8, usd_cny: 7.1 };

function renderKpis(s) {
    var rate = cfgCache.usd_cny || 7.1;
    var cards = [
        ["总市值", "$" + num(s.market_value), "≈ ¥" + num(s.market_value * rate, 0), ""],
        ["总成本", "$" + num(s.cost_value), "", ""],
        ["累计盈亏", signed(s.pnl, 2), signed(s.pnl_pct, 2, "%"), cls(s.pnl)],
        ["当日盈亏", signed(s.day_pnl, 2), signed(s.day_pnl_pct, 2, "%"), cls(s.day_pnl)],
        ["持仓笔数", String(s.position_count),
            s.winner_count + " 盈 / " + s.loser_count + " 亏", ""],
        ["最强 / 最弱", (s.best || "-") + " / " + (s.worst || "-"), "按盈亏率", ""],
        ["今日最强", s.best_day || "-", "今日最弱 " + (s.worst_day || "-"), ""],
        ["行情异常", String((s.stale_symbols || []).length),
            (s.stale_symbols || []).join(" ") || "全部正常",
            (s.stale_symbols || []).length ? "up" : ""]
    ];
    $("kpi-row").innerHTML = cards.map(function (c) {
        return '<div class="kpi"><div class="label">' + esc(c[0]) + '</div>' +
            '<div class="value ' + c[3] + '">' + esc(c[1]) + '</div>' +
            '<div class="extra ' + c[3] + '">' + esc(c[2]) + '</div></div>';
    }).join("");
}

// ------------------------------------------------------------------
// 持仓明细：点击表头升/降序（默认按涨跌幅降序，轮询/删除后保持排序态）
// ------------------------------------------------------------------
var lastPosRows = [];
var posSort = { key: "change_pct", dir: -1 };   // dir: 1=升序 -1=降序

function posNum(v) {
    if (v === null || v === undefined || isNaN(v)) return null;
    return Number(v);
}

function applyPosSort(rows) {
    var key = posSort.key, dir = posSort.dir;
    var list = (rows || []).slice();
    list.sort(function (a, b) {
        var x = posNum(a[key]), y = posNum(b[key]);
        if (x === null && y === null) return 0;
        if (x === null) return 1;          // 空值永远排最后
        if (y === null) return -1;
        return (x < y ? -1 : x > y ? 1 : 0) * dir;
    });
    return list;
}

function renderSortHead() {
    var ths = document.querySelectorAll("#pos-thead th[data-key]");
    Array.prototype.forEach.call(ths, function (th) {
        var on = th.getAttribute("data-key") === posSort.key;
        th.classList.toggle("sort-on", on);
        var arr = th.querySelector(".sarr");
        if (arr) arr.textContent = on ? (posSort.dir > 0 ? "\u25B2" : "\u25BC") : "\u21C5";
    });
}

function renderPositions(rows) {
    lastPosRows = rows || [];
    var list = applyPosSort(lastPosRows);
    $("pos-count").textContent = list.length + " 笔";
    $("pos-empty").style.display = list.length ? "none" : "block";
    $("pos-body").innerHTML = list.map(function (r) {
        var dirCls = r.direction === "short" ? "short" : "long";
        var dirTxt = r.direction === "short" ? "做空" : "做多";
        return '<tr>' +
            '<td class="l"><span class="sym">' + esc(r.symbol) + '</span>' +
              '<div class="nm">' + esc(r.name) + '</div></td>' +
            '<td class="l"><span class="pill ' + dirCls + '">' + dirTxt + '</span></td>' +
            '<td class="num">' + num(r.shares, 0) + '</td>' +
            '<td class="num">' + num(r.cost) + '</td>' +
            '<td class="num">' + num(r.price) + '</td>' +
            '<td class="num ' + cls(r.change) + '">' + signed(r.change) + '</td>' +
            '<td class="num ' + cls(r.change_pct) + '">' + signed(r.change_pct, 2, "%") + '</td>' +
            '<td class="num">' + num(r.market_value) + '</td>' +
            '<td class="num ' + cls(r.pnl) + '">' + signed(r.pnl) + '</td>' +
            '<td class="num ' + cls(r.pnl_pct) + '">' + signed(r.pnl_pct, 2, "%") + '</td>' +
            '<td class="num ' + cls(r.day_pnl) + '">' + signed(r.day_pnl) + '</td>' +
            '<td class="num">' + num(r.weight, 1) + '%' +
              '<div class="wbar"><i style="width:' +
              Math.max(0, Math.min(100, r.weight)) + '%"></i></div></td>' +
            '<td class="l mute">' + esc(r.note || "") + '</td>' +
            '<td><button class="btn danger" data-del-pos="' + esc(r.id) + '">删除</button></td>' +
            '</tr>';
    }).join("");
    renderSortHead();
}

// 表头点击切换升/降序
Array.prototype.forEach.call(
    document.querySelectorAll("#pos-thead th[data-key]"), function (th) {
        th.addEventListener("click", function () {
            var key = th.getAttribute("data-key");
            if (posSort.key === key) {
                posSort.dir = -posSort.dir;
            } else {
                posSort.key = key;
                posSort.dir = -1;    // 新列默认降序（大值在前）
            }
            renderPositions(lastPosRows);
            var label = (th.childNodes[0] && th.childNodes[0].nodeValue
                         ? th.childNodes[0].nodeValue.trim() : key) || key;
            if (lastPosRows.length) {
                toast("持仓已按「" + label + "」" +
                      (posSort.dir > 0 ? "升序" : "降序") + "排列", "ok");
            }
        });
    });

function renderWatchlist(rows) {
    $("watch-count").textContent = rows.length + " 只";
    $("watch-empty").style.display = rows.length ? "none" : "block";
    $("watch-body").innerHTML = rows.map(function (r) {
        return '<tr>' +
            '<td class="l"><span class="sym">' + esc(r.symbol) + '</span>' +
              '<div class="nm">' + esc(r.name) + '</div></td>' +
            '<td class="num">' + num(r.price) + '</td>' +
            '<td class="num ' + cls(r.change) + '">' + signed(r.change) + '</td>' +
            '<td class="num ' + cls(r.change_pct) + '">' + signed(r.change_pct, 2, "%") + '</td>' +
            '<td class="num">' + num(r.high) + '</td>' +
            '<td class="num">' + num(r.low) + '</td>' +
            '<td class="l mute">' + esc(r.quote_time || "") + '</td>' +
            '<td><button class="btn danger" data-del-watch="' + esc(r.symbol) + '">移除</button></td>' +
            '</tr>';
    }).join("");
}

// ------------------------------------------------------------------
// 持仓走势前瞻
// ------------------------------------------------------------------
function fcProbCls(v) {
    v = Number(v);
    if (v >= 60) return "up";
    if (v <= 40) return "down";
    return "flat";
}

function fcHcell(h, pctKey) {
    var v = h[pctKey];
    return '<div class="fc-v ' + cls(v) + '">' + signed(v, 1, "%") + '</div>' +
        '<div class="fc-px">$' + num(h[pctKey + "_price"]) + '</div>';
}

function fcProbCell(p) {
    return '<div class="fc-v ' + fcProbCls(p) + '">' + num(p, 0) + "%</div>";
}

function fcCard(it) {
    if (!it.ok) {
        return '<div class="fc fc-err"><div class="fc-head">' +
            '<span class="sym">' + esc(it.symbol) + '</span>' +
            '<span class="fc-nm">' + esc(it.name || "") + '</span>' +
            '</div><div class="fc-err-txt">' + esc(it.error || "分析失败") + "</div></div>";
    }
    var dirCls = it.direction === "short" ? "short" : "long";
    var dirTxt = it.direction === "short" ? "做空" : "做多";
    var isShort = it.direction === "short";

    var hRows = [["p10", "悲观 · P10"], ["p50", "中性 · P50"], ["p90", "乐观 · P90"]];
    var rowsHtml = hRows.map(function (r) {
        return '<tr><td class="l">' + r[1] + "</td>" +
            "<td>" + fcHcell(it.horizons[0], r[0]) + "</td>" +
            "<td>" + fcHcell(it.horizons[1], r[0]) + "</td></tr>";
    }).join("");

    var up0 = it.horizons[0].up_prob, up1 = it.horizons[1].up_prob;
    rowsHtml += '<tr class="fc-prob-row"><td class="l">上涨概率（标的）</td>' +
        "<td>" + fcProbCell(up0) + "</td><td>" + fcProbCell(up1) + "</td></tr>";
    if (isShort) {
        rowsHtml += '<tr class="fc-prob-row"><td class="l">对你有利的概率（做空反算）</td>' +
            "<td>" + fcProbCell(100 - up0) + "</td><td>" + fcProbCell(100 - up1) +
            "</td></tr>";
    }

    var meta = [
        "年化波动率 " + num(it.ann_vol, 0) + "%",
        esc(it.vol_grade) + "波动",
        "单日95%回撤 ≈ " + num(it.var1d, 2) + "%",
        "样本 " + num(it.window, 0) + " 交易日",
        "数据至 " + esc(String(it.window_end).slice(0, 10))
    ].join(" · ");

    return '<div class="fc">' +
        '<div class="fc-head">' +
          '<span class="sym">' + esc(it.symbol) + '</span>' +
          '<span class="fc-nm">' + esc(it.name) + '</span>' +
          '<span class="pill ' + dirCls + '">' + dirTxt + '</span>' +
          '<span class="fc-sub mute">' + num(it.shares, 0) + " 股 · 成本 $" +
            num(it.cost) + " · 现价 $" + num(it.price) +
            " · 仓位 " + num(it.weight, 1) + "%</span>" +
        '</div>' +
        '<div class="fc-body">' +
          '<div class="fc-nums">' +
            '<table class="fc-tbl">' +
              '<thead><tr><th class="l">未来情景（标的价格）</th>' +
                '<th>' + esc(it.horizons[0].label) + '<div class="mute">' +
                    it.horizons[0].days + " 个交易日</div></th>" +
                '<th>' + esc(it.horizons[1].label) + '<div class="mute">' +
                    it.horizons[1].days + " 个交易日</div></th>" +
              '</tr></thead><tbody>' + rowsHtml + "</tbody></table>" +
            '<div class="fc-hint mute">表中涨跌幅与价位均为标的价格情景；' +
              (isShort ? "你是做空仓位，标的下跌才对你有利。" : "做多仓位涨跌幅即你的预期盈亏。") +
            "</div>" +
          "</div>" +
          '<div class="fc-talk">' +
            '<div class="fc-talk-h">文字讲解</div>' +
            '<p class="fc-note">' + esc(it.note || "") + "</p>" +
            '<div class="fc-meta mute">' + meta + "</div>" +
          "</div>" +
        "</div></div>";
}

function renderForecast(d) {
    var items = (d && d.items) || [];
    $("fc-time").textContent = d && d.generated_at
        ? "更新于 " + d.generated_at + " · " + num(d.elapsed, 1) + "s" : "未计算";
    var modelTxt = (d && d.model ? "模型：" + d.model + "　" : "") +
        (d && d.disclaimer ? "局限：" + d.disclaimer : "");
    $("fc-model").textContent = modelTxt;
    if (!items.length) {
        $("fc-wrap").innerHTML = "";
        $("fc-empty").style.display = "";
        $("fc-empty").innerHTML = "当前没有持仓。添加持仓后，这里会自动生成每只股票的" +
            "未来 1 个月 / 3 个月走势区间、上涨概率与文字讲解。";
        return;
    }
    $("fc-empty").style.display = "none";
    $("fc-wrap").innerHTML = items.map(fcCard).join("");
}

function loadForecast(force) {
    if (!$("fc-wrap")) return;
    if (force) {
        $("fc-time").textContent = "重算中（拉取一年日线，约 5~15 秒）…";
        $("btn-refresh-forecast").disabled = true;
    }
    api("/api/forecast" + (force ? "?force=1" : "")).then(function (res) {
        if ($("btn-refresh-forecast")) $("btn-refresh-forecast").disabled = false;
        if (!res.ok) { toast(res.error || "走势前瞻计算失败", "err"); return; }
        renderForecast(res.data);
    }).catch(function () {
        if ($("btn-refresh-forecast")) $("btn-refresh-forecast").disabled = false;
        $("fc-time").textContent = "失败";
        toast("走势前瞻计算失败，请检查网络", "err");
    });
}

function applyState(data) {
    renderKpis(data.summary);
    renderPositions(data.positions);
    renderWatchlist(data.watchlist);
    var s = data.summary;
    $("top-mv").textContent = "$" + num(s.market_value);
    $("top-pnl").textContent = signed(s.pnl);
    $("top-pnl").className = "k-value " + cls(s.pnl);
    $("top-day").textContent = signed(s.day_pnl);
    $("top-day").className = "k-value " + cls(s.day_pnl);
}

function loadState(silent) {
    if (!silent) setConn("load", "刷新中…");
    return api("/api/state").then(function (res) {
        if (!res.ok) throw new Error(res.error || "接口异常");
        applyState(res.data);
        setConn("", "已连接 · " + new Date().toLocaleTimeString("zh-CN"));
    }).catch(function (e) {
        setConn("err", "刷新失败：" + e.message);
    });
}

// 事件委托：删除
document.addEventListener("click", function (ev) {
    var t = ev.target;
    var pid = t.getAttribute && t.getAttribute("data-del-pos");
    if (pid) {
        api("/api/position/delete", { id: pid }).then(function (res) {
            if (res.ok) { applyState(res.data); toast("已删除该持仓", "ok"); }
            else toast(res.error || "删除失败", "err");
            loadForecast(false);
        });
        return;
    }
    var sym = t.getAttribute && t.getAttribute("data-del-watch");
    if (sym) {
        api("/api/watch/delete", { symbol: sym }).then(function (res) {
            if (res.ok) { applyState(res.data); toast("已移除自选", "ok"); }
        });
    }
});

// ------------------------------------------------------------------
// 搜索
// ------------------------------------------------------------------
var picked = null;
var searchTimer = null;

$("q-input").addEventListener("input", function () {
    var kw = this.value.trim();
    clearTimeout(searchTimer);
    if (kw.length < 1) { $("q-results").className = "search-results"; return; }
    searchTimer = setTimeout(function () { doSearch(kw); }, 320);
});

function doSearch(kw) {
    api("/api/search?q=" + encodeURIComponent(kw)).then(function (res) {
        var box = $("q-results");
        var list = (res.data || []);
        if (!list.length) {
            box.innerHTML = '<div class="search-item"><span class="s-nm">没找到，试试标准代码（如 NVDA）</span></div>';
            box.className = "search-results show";
            return;
        }
        box.innerHTML = list.map(function (it) {
            return '<div class="search-item" data-sym="' + esc(it.symbol) + '" data-nm="' +
                esc(it.name) + '">' +
                '<span class="s-sym">' + esc(it.symbol) + '</span>' +
                '<span class="s-nm">' + esc(it.name) + '</span>' +
                '<span class="s-ex">' + esc(it.exchange || "") + '</span></div>';
        }).join("");
        box.className = "search-results show";
    }).catch(function () { toast("搜索失败，检查网络", "err"); });
}

$("q-results").addEventListener("click", function (ev) {
    var item = ev.target.closest(".search-item");
    if (!item || !item.getAttribute("data-sym")) return;
    picked = { symbol: item.getAttribute("data-sym"), name: item.getAttribute("data-nm") };
    $("q-input").value = picked.symbol + "  " + picked.name;
    $("q-results").className = "search-results";
    $("picked-hint").textContent = "已选中：" + picked.symbol + " " + picked.name + "，正在取现价…";
    api("/api/quote?symbol=" + encodeURIComponent(picked.symbol)).then(function (res) {
        if (res.ok && res.data) {
            picked.price = res.data.price;
            $("picked-hint").innerHTML = "已选中：<b>" + esc(picked.symbol) + "</b> " +
                esc(res.data.name) + " · 现价 <b>" + num(res.data.price) + "</b> · " +
                '<span class="' + cls(res.data.change_pct) + '">' +
                signed(res.data.change_pct, 2, "%") + "</span>（成本价留空则按现价记入）";
        } else {
            $("picked-hint").textContent = "已选中：" + picked.symbol + "（暂未取到行情）";
        }
    });
});

document.addEventListener("click", function (ev) {
    if (!ev.target.closest(".search-box")) $("q-results").className = "search-results";
});

$("btn-add-pos").addEventListener("click", function () {
    if (!picked) { toast("请先在搜索框选中一个标的", "err"); return; }
    var shares = parseFloat($("f-shares").value);
    if (!shares || shares <= 0) { toast("请输入正确的股数", "err"); return; }
    var cost = parseFloat($("f-cost").value);
    if (!cost || cost <= 0) {
        if (!picked.price) { toast("未取到现价，请手工填写成本价", "err"); return; }
        cost = picked.price;
    }
    api("/api/position/add", {
        symbol: picked.symbol, name: picked.name, shares: shares, cost: cost,
        direction: $("f-direction").value, note: $("f-note").value
    }).then(function (res) {
        if (!res.ok) { toast(res.error || "添加失败", "err"); return; }
        applyState(res.data);
        $("f-shares").value = ""; $("f-cost").value = ""; $("f-note").value = "";
        toast("已加入持仓：" + picked.symbol, "ok");
        loadForecast(false);
    });
});

$("btn-add-watch").addEventListener("click", function () {
    if (!picked) { toast("请先在搜索框选中一个标的", "err"); return; }
    api("/api/watch/add", { symbol: picked.symbol, name: picked.name })
        .then(function (res) {
            if (!res.ok) { toast(res.error || "添加失败", "err"); return; }
            applyState(res.data);
            toast("已加入自选：" + picked.symbol, "ok");
        });
});

$("btn-refresh").addEventListener("click", function () { loadState(false); });
$("btn-refresh-forecast").addEventListener("click", function () { loadForecast(true); });
$("btn-clear-cache").addEventListener("click", function () {
    api("/api/cache/clear", {}).then(function () {
        toast("缓存已清空", "ok");
        loadState(false);
    });
});

// ------------------------------------------------------------------
// 盘前信号
// ------------------------------------------------------------------
var lastAnalysis = null;

function envCard(label, state, detail, colorVal) {
    return '<div class="env-item"><div class="e-label">' + esc(label) + '</div>' +
        '<div class="e-state ' + (colorVal === undefined ? "" : cls(colorVal)) + '">' +
        esc(state) + '</div>' +
        '<div class="e-detail">' + esc(detail) + '</div></div>';
}

function renderBrief(brief) {
    var wrap = $("brief-wrap");
    if (!brief || (!(brief.do || []).length && !(brief.watch || []).length)) {
        wrap.innerHTML = '<div class="empty">' +
            esc(brief && brief.headline ? brief.headline :
                "点击「重新计算」生成今日讲解（约 5~15 秒，需联网拉取行情与快讯）。") +
            '</div>';
        return;
    }

    function pill(tag) {
        var c = tag === "做多" ? "long" : (tag === "做空" ? "short" : "neutral");
        return '<span class="pill ' + c + '">' + esc(tag) + '</span>';
    }

    // —— 该做什么 ——
    var doHtml = (brief.do || []).map(function (it) {
        var cite = it.cite ? '<div class="b-cite">盘前快讯：' + esc(it.cite) + '</div>' : "";
        return '<div class="b-item">' +
            '<div class="b-title">' + pill(it.tag) + ' <b>' + esc(it.asset) + '</b>' +
            (it.symbol ? ' <span class="b-etf">' + esc(it.symbol) + '</span>' : "") + '</div>' +
            '<div class="b-how">怎么做：' + esc(it.how || "") + '</div>' +
            '<div class="b-why">依据：' + esc(it.why || "") + '</div>' + cite + '</div>';
    }).join("");

    // —— 不该做什么 ——
    var dontHtml = (brief.dont || []).map(function (it) {
        return '<div class="b-item">' +
            '<div class="b-title"><span class="b-tagx">' + esc(it.tag || "注意") + '</span></div>' +
            '<div class="b-why">' + esc(it.why || "") + '</div></div>';
    }).join("");

    // —— 观望 / 等确认 ——
    var watchHtml = (brief.watch || []).map(function (it) {
        return '<div class="b-item">' +
            '<div class="b-title"><b>' + esc(it.asset) + '</b>' +
            ' <span class="b-score">' + signed(it.score, 1) + ' 分</span></div>' +
            '<div class="b-why">' + esc(it.why || "") + '</div>' +
            '<div class="b-trigger">等确认：' + esc(it.trigger || "") + '</div></div>';
    }).join("");

    var col = function (cls, title, html) {
        if (!html) return "";
        return '<div class="b-col ' + cls + '"><h3>' + title + '</h3>' + html + '</div>';
    };
    var newsN = brief.news && brief.news.total !== undefined
        ? ' <span class="mute">（快讯 ' + brief.news.total + ' 条 / 命中 ' +
          (brief.news.matched || 0) + ' 条）</span>' : "";

    wrap.innerHTML =
        '<div class="b-lead"><div class="b-headline">' + esc(brief.headline || "") + '</div>' +
        '<div class="b-lead-text">' + esc(brief.lead || "") + newsN + '</div></div>' +
        '<div class="b-grid">' +
        col("b-do", "今天该做什么", doHtml) +
        col("b-dont", "今天不该做什么", dontHtml) +
        col("b-watch", "观望 · 等确认", watchHtml) +
        '</div>' +
        '<div class="b-risk">风险提示：' + esc(brief.risk || "") + '</div>';
}

function renderEnv(env) {
    var html = "";
    html += envCard("长端利率（TLT 反向代理）", env.rate_state,
        "利率上行压力 " + signed(env.rate_pressure) + " ｜ TLT 单日 " +
        signed(env.tlt_chg1, 2, "%") + "、五日 " + signed(env.tlt_chg5, 2, "%") +
        " ｜ IEF 五日 " + signed(env.ief_chg5, 2, "%"),
        env.rate_pressure);
    html += envCard("风险偏好（QQQ vs XLP）", env.risk_state,
        "五日超额 " + signed(env.risk_spread, 2, "%") + " ｜ QQQ 单日 " +
        signed(env.qqq_chg1, 2, "%") + "、五日 " + signed(env.qqq_chg5, 2, "%") +
        " ｜ VIXY 五日 " + signed(env.vix_mom5, 2, "%"),
        env.risk_spread);
    html += envCard("地缘溢价（军工 + 原油）", env.geo_state,
        "地缘分 " + num(env.geo_score) + " ｜ " + env.geo_detail, env.geo_score);
    html += envCard("银行 / 信用", env.bank_state,
        "XLF 五日 " + signed(env.bank_chg5, 2, "%") + " ｜ 区域银行 KRE 五日 " +
        signed(env.kre_chg5, 2, "%"), env.bank_chg5);
    html += envCard("防御消费（可口可乐）",
        env.ko_chg5 >= 0 ? "KO 相对稳健" : "KO 同步走弱",
        "KO 五日 " + signed(env.ko_chg5, 2, "%") +
        "（防御股走弱通常说明资金在整体去杠杆，而不是单纯避险轮动）",
        env.ko_chg5);
    $("env-grid").innerHTML = html;
}

function renderSignals(signals) {
    if (!signals || !signals.length) {
        $("signal-grid").innerHTML = '<div class="empty">未能计算出信号（行情数据不足）。</div>';
        return;
    }
    $("signal-grid").innerHTML = signals.map(function (s) {
        var c = cls(s.score);
        var pct = Math.min(100, Math.abs(s.score)) / 2;   // 半幅
        var bar = s.score >= 0
            ? '<i style="left:50%;width:' + pct + '%;background:var(--up)"></i>'
            : '<i style="right:50%;width:' + pct + '%;background:var(--down)"></i>';

        var factors = (s.factors || []).map(function (f) {
            return '<div class="factor">' +
                '<div class="f-name">' + esc(f.name) + '</div>' +
                '<div class="f-detail">' + esc(f.detail) + '</div>' +
                '<div class="f-score ' + cls(f.score) + '">' + signed(f.score) + '</div></div>';
        }).join("");

        var levels = Object.keys(s.levels || {}).map(function (k) {
            return '<div>' + esc(k) + '：<b>' + esc(s.levels[k]) + '</b></div>';
        }).join("");

        return '<div class="signal">' +
            '<div class="signal-head">' +
              '<div><div class="s-asset">' + esc(s.asset) + '</div>' +
              '<div class="s-proxy">' + esc(s.proxy) + '</div></div>' +
              '<div class="s-score"><div class="v ' + c + '">' + signed(s.score, 1) + '</div>' +
              '<div class="t">综合得分 (-100 ~ +100)</div></div>' +
            '</div>' +
            '<div class="score-bar">' + bar + '<span class="mid"></span></div>' +
            '<div class="signal-body">' +
              '<div class="action-line">倾向 <b class="' + c + '">' + esc(s.stance) + '</b>' +
              '· 操作 <b class="' + c + '">' + esc(s.action) + '</b>' +
              '· 置信度 <b>' + esc(s.confidence) + '</b></div>' +
              '<div class="factors">' + factors + '</div>' +
              '<div class="levels">' + levels + '</div>' +
            '</div></div>';
    }).join("");
}

function renderRecs(recs) {
    $("etf-empty").style.display = (recs && recs.length) ? "none" : "block";
    $("etf-body").innerHTML = (recs || []).map(function (r) {
        var sideCls = r.side === "做多" ? "long" : (r.side === "做空" ? "short" : "neutral");
        return '<tr>' +
            '<td class="l">' + esc(r.asset) + '</td>' +
            '<td class="l"><span class="pill ' + sideCls + '">' + esc(r.side) + '</span></td>' +
            '<td class="l"><span class="sym">' + esc(r.symbol) + '</span></td>' +
            '<td class="l mute">' + esc(r.priority) + '</td>' +
            '<td class="num ' + cls(r.score) + '">' + signed(r.score, 1) + '</td>' +
            '<td class="l mute" style="white-space:normal;max-width:520px">' +
              esc(r.reason) + '</td></tr>';
    }).join("");
}

function loadSignals(force) {
    $("sig-time").textContent = "计算中…";
    $("btn-load-sig").disabled = true;
    api("/api/premarket" + (force ? "?force=1" : "")).then(function (res) {
        $("btn-load-sig").disabled = false;
        if (!res.ok) { toast(res.error || "计算失败", "err"); return; }
        lastAnalysis = res.data;
        $("sig-time").textContent = res.data.generated_at + " · 载入 " +
            res.data.universe_loaded + " 只标的 · " + res.data.elapsed + "s";
        renderBrief(res.data.brief);
        renderEnv(res.data.environment);
        renderSignals(res.data.signals);
        renderRecs(res.data.recommendations);
        $("sig-disclaimer").textContent = res.data.disclaimer;
        lazyLoaded["page-etf"] = true;
    }).catch(function (e) {
        $("btn-load-sig").disabled = false;
        $("sig-time").textContent = "失败";
        toast("计算失败：" + e.message, "err");
    });
}
$("btn-load-sig").addEventListener("click", function () { loadSignals(true); });

// ------------------------------------------------------------------
// 消息面
// ------------------------------------------------------------------
function loadNews(force) {
    $("news-time").textContent = "抓取中…";
    api("/api/news" + (force ? "?force=1" : "")).then(function (res) {
        if (!res.ok) { toast("抓取失败", "err"); return; }
        var d = res.data;
        $("news-time").textContent = d.generated_at + " · 共 " + d.total +
            " 条，命中主题 " + d.matched + " 条";
        var html = d.topics.map(function (t) {
            var lis = t.items.map(function (it) {
                var title = it.url
                    ? '<a href="' + esc(it.url) + '" target="_blank">' + esc(it.title) + '</a>'
                    : esc(it.title);
                return '<li><span class="n-time">' + esc(it.time || "") + '</span>' +
                    '<span class="n-title">' + title + '</span></li>';
            }).join("");
            return '<div class="news-topic"><h3>' + esc(t.topic) +
                '<span class="cnt">' + t.count + ' 条</span></h3>' +
                '<ul class="news-list">' + lis + '</ul></div>';
        }).join("");
        if (!html) html = '<div class="empty">未匹配到相关主题快讯。</div>';
        $("news-wrap").innerHTML = html;
    }).catch(function (e) {
        $("news-time").textContent = "失败";
        toast("抓取失败：" + e.message, "err");
    });
}
$("btn-load-news").addEventListener("click", function () { loadNews(true); });

// ------------------------------------------------------------------
// 日报
// ------------------------------------------------------------------
function loadReports() {
    api("/api/reports").then(function (res) {
        var list = res.data || [];
        $("rep-count").textContent = list.length + " 份";
        $("rep-empty").style.display = list.length ? "none" : "block";
        $("rep-list").innerHTML = list.map(function (r) {
            return '<li><span class="r-name">' + esc(r.filename) + '</span>' +
                '<span class="mute">' + esc(r.modified || "") + ' · ' +
                num((r.size || 0) / 1024, 1) + ' KB</span>' +
                '<a href="' + authedHref("/reports/" + esc(r.filename)) +
                '" target="_blank">打开</a></li>';
        }).join("");
    }).catch(function () { $("rep-status").textContent = "报告列表加载失败"; });
}
$("btn-load-reports").addEventListener("click", loadReports);

function loadSchedule() {
    api("/api/schedule").then(function (res) {
        var d = res.data || {};
        var next = d.next || {};
        var label = { daily: "每日日报", premarket: "盘前报告" };
        var keys = Object.keys(next);
        var anyOn = keys.some(function (k) { return next[k].enabled; });
        $("sched-badge").textContent = anyOn ? "已启用" : "全部关闭";
        $("sched-next").innerHTML = keys.map(function (k) {
            var v = next[k];
            return (label[k] || k) + "：<b>" + esc(v.time || "--") + "</b>（" +
                (v.enabled ? "已开启" : "已关闭") +
                (v.done_today ? "，今日已执行" : "，今日待执行") + "）";
        }).join("<br>") || "定时任务未启用";
        var logs = d.logs || [];
        $("sched-logs").innerHTML = logs.length
            ? logs.slice().reverse().map(function (l) {
                return '<li><span class="mute">' + esc(l) + '</span></li>';
            }).join("")
            : '<li><span class="mute">暂无执行记录。</span></li>';
    }).catch(function () { $("sched-badge").textContent = "加载失败"; });
}

$("btn-gen-report").addEventListener("click", function () {
    var kind = $("rep-kind").value;
    var mail = $("rep-mail").value === "1";
    $("btn-gen-report").disabled = true;
    $("rep-status").textContent = "生成中，盘前报告需要拉取全量行情，约 5~15 秒…";
    api("/api/report/generate", { kind: kind, email: mail }).then(function (res) {
        $("btn-gen-report").disabled = false;
        if (!res.ok) { $("rep-status").textContent = "失败：" + (res.error || ""); return; }
        var d = res.data;
        var msg = "已生成 " + d.filename;
        if (mail) msg += "；邮件：" + (d.email_ok ? "发送成功" : "失败 - " + (d.email_message || ""));
        $("rep-status").innerHTML = esc(msg) +
            ' <a href="' + authedHref(d.url) + '" target="_blank">点击查看</a>';
        toast(msg, d.email_ok === false ? "err" : "ok");
        loadReports();
        loadEquity();
    }).catch(function (e) {
        $("btn-gen-report").disabled = false;
        $("rep-status").textContent = "失败：" + e.message;
    });
});

// ------------------------------------------------------------------
// 设置
// ------------------------------------------------------------------
function loadConfig() {
    api("/api/config").then(function (res) {
        var c = res.data || {};
        cfgCache = c;
        $("c-refresh").value = c.refresh_seconds;
        $("c-usdcny").value = c.usd_cny;
        $("c-daily").value = c.daily_report_time;
        $("c-pre").value = c.premarket_report_time;
        $("c-auto-daily").value = c.auto_report ? "1" : "0";
        $("c-auto-pre").value = c.auto_premarket ? "1" : "0";
        var m = c.email || {};
        $("c-mail-en").value = m.enabled ? "1" : "0";
        $("c-mail-ssl").value = m.use_ssl ? "1" : "0";
        $("c-mail-host").value = m.smtp_host || "";
        $("c-mail-port").value = m.smtp_port || 465;
        $("c-mail-user").value = m.username || "";
        $("c-mail-pass").value = m.password || "";
        $("c-mail-sender").value = m.sender || "";
        $("c-mail-to").value = (m.receivers || []).join(",");
    }).catch(function () { toast("设置读取失败：未授权或服务不可达", "err"); });
}

$("btn-save-cfg").addEventListener("click", function () {
    var patch = {
        refresh_seconds: parseInt($("c-refresh").value, 10) || 8,
        usd_cny: parseFloat($("c-usdcny").value) || 7.1,
        daily_report_time: $("c-daily").value.trim(),
        premarket_report_time: $("c-pre").value.trim(),
        auto_report: $("c-auto-daily").value === "1",
        auto_premarket: $("c-auto-pre").value === "1",
        email: {
            enabled: $("c-mail-en").value === "1",
            use_ssl: $("c-mail-ssl").value === "1",
            smtp_host: $("c-mail-host").value.trim(),
            smtp_port: parseInt($("c-mail-port").value, 10) || 465,
            username: $("c-mail-user").value.trim(),
            password: $("c-mail-pass").value,
            sender: $("c-mail-sender").value.trim(),
            receivers: $("c-mail-to").value.split(/[,，;；\s]+/).filter(Boolean)
        }
    };
    api("/api/config", patch).then(function (res) {
        if (!res.ok) { toast(res.error || "保存失败", "err"); return; }
        cfgCache = res.data;
        restartPolling();
        $("cfg-status").textContent = "已保存（定时任务会在下一分钟按新时间生效）";
        toast("设置已保存", "ok");
    });
});

$("btn-test-mail").addEventListener("click", function () {
    $("btn-test-mail").disabled = true;
    $("cfg-status").textContent = "正在连接 SMTP…";
    api("/api/email/test", {}).then(function (res) {
        $("btn-test-mail").disabled = false;
        $("cfg-status").textContent = res.message || "";
        toast(res.message || (res.ok ? "发送成功" : "发送失败"), res.ok ? "ok" : "err");
    }).catch(function (e) {
        $("btn-test-mail").disabled = false;
        $("cfg-status").textContent = "失败：" + e.message;
    });
});

// ------------------------------------------------------------------
// 资产净值走势（ECharts 本地版，离线可用）
// ------------------------------------------------------------------
var equityChart = null;
var equityAll = [];
var equityRangeDays = 90;   // 默认显示最近 90 天；0 = 全部
var EQCOL = {
    accent: "#4c9aff", up: "#f6465d", down: "#0ecb81",
    grid: "#26303f", split: "#1d2634", axis: "#6b7789",
    text: "#9aa7bb", bright: "#e8eef8", cost: "#b8c4d6",
    panel: "#141a24", border: "#2a3444"
};

function eqColor(v) {
    if (v === null || v === undefined || isNaN(v) || Number(v) === 0) return EQCOL.axis;
    return Number(v) > 0 ? EQCOL.up : EQCOL.down;
}

function renderEquityKpis(view) {
    var last = view[view.length - 1];
    var first = view[0];
    var chg = (last.market_value || 0) - (first.market_value || 0);
    var spanPct = first.market_value ? chg / first.market_value * 100 : 0;
    var pnl = last.pnl || 0;
    var cost = last.cost_value || 0;
    var pnlPct = cost ? pnl / cost * 100 : 0;
    var cards = [
        ["最新市值 (USD)", "$" + num(last.market_value), esc(String(last.date)), ""],
        ["区间市值变化", signed(chg), signed(spanPct, 2, "%"), cls(chg)],
        ["累计盈亏", signed(pnl), signed(pnlPct, 2, "%"), cls(pnl)],
        ["记录天数", String(view.length) + " 天",
            esc(String(first.date).slice(5)) + " → " + esc(String(last.date).slice(5)), ""]
    ];
    $("eq-kpis").innerHTML = cards.map(function (c) {
        return '<div class="kpi"><div class="label">' + esc(c[0]) + '</div>' +
            '<div class="value ' + c[3] + '">' + esc(c[1]) + '</div>' +
            '<div class="extra ' + c[3] + '">' + esc(c[2]) + '</div></div>';
    }).join("");
}

function buildEquityOption(view) {
    var dates = [], mv = [], cost = [], day = [];
    view.forEach(function (s) {
        dates.push(String(s.date).slice(5));        // MM-DD
        var mvv = Math.round((s.market_value || 0) * 100) / 100;
        if (s.actual) {
            // 真实快照锚点：画实心圆点，与重建值区分
            mv.push({ value: mvv, symbol: "circle", symbolSize: 6,
                      itemStyle: { color: EQCOL.accent,
                                   borderColor: "#0b1017", borderWidth: 1 } });
        } else {
            mv.push(mvv);
        }
        cost.push(Math.round((s.cost_value || 0) * 100) / 100);
        day.push(Math.round((s.day_pnl || 0) * 100) / 100);
    });
    return {
        backgroundColor: "transparent",
        animationDuration: 350,
        textStyle: { fontFamily: "inherit" },
        legend: {
            show: true, top: 2, right: 10, itemWidth: 14, itemHeight: 8,
            itemGap: 16, textStyle: { color: EQCOL.text, fontSize: 11 },
            data: ["市值", "成本基准"]
        },
        tooltip: {
            trigger: "axis",
            backgroundColor: EQCOL.panel,
            borderColor: EQCOL.border,
            borderWidth: 1,
            padding: [8, 12],
            textStyle: { color: EQCOL.bright, fontSize: 12 },
            axisPointer: {
                type: "cross",
                lineStyle: { color: "#3d4b61", type: "dashed" },
                label: { backgroundColor: "#212a3a", color: EQCOL.bright,
                         fontSize: 10, formatter: function (p) { return String(view[p.dataIndex].date).slice(0, 10); } }
            },
            formatter: function (ps) {
                if (!ps || !ps.length) return "";
                var s = view[ps[0].dataIndex];
                var costv = s.cost_value || 0;
                var pnlp = costv ? (s.pnl || 0) / costv * 100 : 0;
                return '<div style="margin-bottom:6px;color:' + EQCOL.text + '">' +
                        esc(String(s.date).slice(0, 10)) + '</div>' +
                    '市值：<b style="color:' + EQCOL.bright + '">$' + num(s.market_value) + '</b><br/>' +
                    '成本基准：$' + num(costv) + '<br/>' +
                    '累计盈亏：<span style="color:' + eqColor(s.pnl) + '">' +
                        signed(s.pnl) + " (" + signed(pnlp, 2, "%") + ')</span><br/>' +
                    '当日盈亏：<span style="color:' + eqColor(s.day_pnl) + '">' +
                        signed(s.day_pnl) + "</span>";
            }
        },
        axisPointer: { link: [{ xAxisIndex: "all" }] },
        grid: [
            { left: 78, right: 28, top: 34, height: "56%" },
            { left: 78, right: 28, top: "73%", height: "15%" }
        ],
        xAxis: [
            { type: "category", data: dates, boundaryGap: false,
              axisLine: { lineStyle: { color: EQCOL.grid } },
              axisTick: { show: false },
              axisLabel: { color: EQCOL.axis, fontSize: 10,
                           formatter: function (v, i) { return view.length > 45 && i % Math.ceil(view.length / 6) ? "" : v; } } },
            { type: "category", data: dates, gridIndex: 1, boundaryGap: true,
              axisLine: { lineStyle: { color: EQCOL.grid } },
              axisTick: { show: false }, axisLabel: { show: false } }
        ],
        yAxis: [
            { type: "value", scale: true, splitNumber: 4,
              axisLabel: { color: EQCOL.axis, fontSize: 10,
                           formatter: function (v) {
                               return "$" + (Math.abs(v) >= 1000 ? (v / 1000).toFixed(1) + "k" : num(v, 0));
                           } },
              splitLine: { lineStyle: { color: EQCOL.split } } },
            { type: "value", gridIndex: 1, splitNumber: 2,
              axisLabel: { show: false }, splitLine: { show: false } }
        ],
        series: [
            {
                name: "市值", type: "line", data: mv,
                smooth: 0.25, symbol: "none", z: 3,
                lineStyle: { width: 2, color: EQCOL.accent },
                areaStyle: {
                    color: {
                        type: "linear", x: 0, y: 0, x2: 0, y2: 1,
                        colorStops: [
                            { offset: 0, color: "rgba(76,154,255,0.32)" },
                            { offset: 1, color: "rgba(76,154,255,0.02)" }
                        ]
                    }
                }
            },
            {
                name: "成本基准", type: "line", data: cost,
                smooth: 0.1, symbol: "none", z: 2,
                lineStyle: { width: 1.2, type: "dashed", color: EQCOL.cost },
                itemStyle: { color: EQCOL.cost }
            },
            {
                name: "当日盈亏", type: "bar", xAxisIndex: 1, yAxisIndex: 1,
                data: day.map(function (v) {
                    return { value: v, itemStyle: { color: eqColor(v) } };
                }),
                barWidth: "55%", z: 5,
                animationDelay: function (idx) { return idx * 8; }
            }
        ]
    };
}

function loadEquity() {
    api("/api/snapshots").then(function (res) {
        var d = (res && res.data) || {};
        equityAll = d.items || [];
        var src = $("eq-src");
        if (src) {
            if (d.rebuilt && d.note) {
                src.innerHTML = "净值曲线已按确认买入时间<b>回溯重建</b>：" +
                    "<b>" + esc(d.first_date || "") + "</b> 起共 <b>" +
                    equityAll.length + "</b> 个交易日，" +
                    "<b>" + (d.anchors || 0) + "</b> 个真实快照锚点（蓝圈）。" +
                    (d.missing ? "；无K线标的按实时价计入：" + esc(d.missing) : "");
            } else if (d.note) {
                src.textContent = d.note;
            } else {
                src.textContent = "";
            }
        }
        var view = equityAll;
        if (equityRangeDays > 0) {
            var cutoff = new Date();
            cutoff.setDate(cutoff.getDate() - equityRangeDays);
            var cs = cutoff.getFullYear() + "-" +
                String(cutoff.getMonth() + 1).padStart(2, "0") + "-" +
                String(cutoff.getDate()).padStart(2, "0");
            view = equityAll.filter(function (s) {
                return String(s.date).slice(0, 10) >= cs;
            });
        }
        var wrap = $("equity-chart");
        if (view.length < 2) {
            wrap.innerHTML =
                '<div class="empty">至少需要 2 天的净值数据才能画曲线（当前 ' +
                view.length + ' 条）。添加持仓后，系统会按每笔确认的买入时间' +
                '回溯重建逐日净值。</div>';
            $("eq-kpis").innerHTML = "";
            if (equityChart) { equityChart.dispose(); equityChart = null; }
            return;
        }
        renderEquityKpis(view);
        if (equityChart) { equityChart.dispose(); equityChart = null; }
        wrap.innerHTML = "";   // 清掉 empty 占位，再交给 echarts 接管
        try {
            equityChart = echarts.init(wrap);
            equityChart.setOption(buildEquityOption(view));
        } catch (err) {
            wrap.innerHTML = '<div class="empty">图表渲染失败：' + esc(err.message) + '</div>';
        }
    }).catch(function (e) {
        var wrap = $("equity-chart");
        if (wrap) wrap.innerHTML = '<div class="empty">净值数据加载失败：' +
            esc((e && e.message) || e || "网络错误") + '</div>';
        $("eq-kpis").innerHTML = "";
    });
}

document.querySelectorAll("#eq-ranges .btn").forEach(function (btn) {
    btn.addEventListener("click", function () {
        document.querySelectorAll("#eq-ranges .btn").forEach(function (b) {
            b.classList.remove("active");
        });
        btn.classList.add("active");
        equityRangeDays = parseInt(btn.getAttribute("data-range"), 10) || 0;
        loadEquity();
    });
});

window.addEventListener("resize", function () {
    if (equityChart) equityChart.resize();
});

// ------------------------------------------------------------------
// 轮询
// ------------------------------------------------------------------
var pollTimer = null;
function restartPolling() {
    clearInterval(pollTimer);
    var sec = Math.max(3, parseInt(cfgCache.refresh_seconds, 10) || 8);
    pollTimer = setInterval(function () {
        if (document.hidden) return;
        loadState(true);
    }, sec * 1000);
}

// ------------------------------------------------------------------
// 启动
// ------------------------------------------------------------------
api("/api/config").then(function (res) {
    cfgCache = res.data || cfgCache;
    restartPolling();
}).catch(function () { /* 未授权/离线时静默，连接状态由 loadState 提示 */ });
loadState(false);
loadEquity();   // 持仓总览页默认可见，启动即渲染净值曲线
loadForecast(false);   // 持仓走势前瞻（服务端带缓存，持仓未变化时秒回）

// PWA：仅 HTTPS 或 localhost 注册 Service Worker（HTTP 局域网访问自动降级为普通网页）
if ("serviceWorker" in navigator &&
        (location.protocol === "https:" ||
         location.hostname === "localhost" || location.hostname === "127.0.0.1")) {
    window.addEventListener("load", function () {
        navigator.serviceWorker.register("/sw.js").catch(function () {});
    });
}

})();
