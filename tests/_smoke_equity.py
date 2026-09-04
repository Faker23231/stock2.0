# -*- coding: utf-8 -*-
"""冒烟: equity.build_equity_series() 重建序列的形态与锚点正确性。"""
import sys
sys.path.insert(0, "D:/zzkstudio/stock2.0")

from core import equity, store

res = equity.build_equity_series(force=True)
print("ok =", res["ok"])
data = res["data"]
items = data["items"]
print("rebuilt =", data["rebuilt"], "| note =", data["note"])
print("first/last =", data["first_date"], "~", data["last_date"],
      "| days =", len(items), "| anchors =", data["anchors"])

print("\n---- 头部 4 条 ----")
for it in items[:4]:
    print(it)
print("---- 锚点条目(actual=True) ----")
for it in items:
    if it.get("actual"):
        print(it)
print("---- 尾部 3 条 ----")
for it in items[-3:]:
    print(it)

dates = [it["date"] for it in items]
print("\n日期升序且无重复:", dates == sorted(dates) and len(set(dates)) == len(dates))
print("首点应为NVDA买点2025-09-24:", dates[0] == "2025-09-24")
last = items[-1]
print("末端实时锚点市值≈2220.55:", abs(last["market_value"] - 2220.55) < 0.02)
print("末端成本基准≈2149.20:", abs(last["cost_value"] - 2149.20) < 0.02)

# 区间完整性:锚点日期必须出现在序列中
snap = store.load_snapshots()["items"]
for s in snap:
    if s["date"] < data["last_date"]:
        print("快照 %s 是否在轴内:" % s["date"], s["date"] in set(dates))
