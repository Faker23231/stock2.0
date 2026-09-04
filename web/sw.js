/* Service Worker：应用壳缓存优先，API / 报告永不缓存（隐私数据）。
   仅在 HTTPS 或 localhost 下会被浏览器注册（HTTP 局域网访问自动降级为普通网页）。 */
"use strict";

var CACHE = "usstock-v1";
var APP_SHELL = [
    "/",
    "/static/style.css",
    "/static/app.js",
    "/static/vendor/echarts.min.js",
    "/manifest.webmanifest",
    "/icon-192.png",
    "/icon-512.png",
    "/apple-touch-icon.png"
];

self.addEventListener("install", function (event) {
    event.waitUntil(
        caches.open(CACHE).then(function (cache) {
            return cache.addAll(APP_SHELL);
        }).then(function () { return self.skipWaiting(); })
    );
});

self.addEventListener("activate", function (event) {
    event.waitUntil(
        caches.keys().then(function (keys) {
            return Promise.all(keys.filter(function (k) { return k !== CACHE; })
                .map(function (k) { return caches.delete(k); }));
        }).then(function () { return self.clients.claim(); })
    );
});

self.addEventListener("fetch", function (event) {
    var req = event.request;
    if (req.method !== "GET") return;
    var url = new URL(req.url);
    if (url.origin !== location.origin) return;              // 跨域(行情等)一律直连
    if (url.pathname.indexOf("/api/") === 0) return;          // 动态数据不走缓存
    if (url.pathname.indexOf("/reports/") === 0) return;      // 持仓报告隐私，不缓存

    // 静态壳：缓存优先，后台更新
    event.respondWith(
        caches.match(req).then(function (hit) {
            var fresh = fetch(req).then(function (res) {
                if (res && res.ok) {
                    var copy = res.clone();
                    caches.open(CACHE).then(function (c) { c.put(req, copy); });
                }
                return res;
            }).catch(function () { return hit; });
            return hit || fresh;
        })
    );
});
