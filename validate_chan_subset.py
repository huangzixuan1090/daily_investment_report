"""快速验证：对子集品种跑多级别缠论联立，渲染 ② 机会表 + 多级别结构速览。"""
import sys, json, copy
sys.path.insert(0, ".")
from lib import futures, llm, render, common

cfg = common.load_config()
cfg2 = copy.deepcopy(cfg)
cfg2["futures"]["chan_concurrency"] = 2  # 演示用更快

subset = ["ic", "im", "if", "ih", "rb", "eg", "sa", "lc", "cu", "ag"]
meta = {p["code"]: p["name"] for p in cfg["futures"]["products"]}
products_for_llm, rows = [], []
for code in subset:
    name = meta[code]
    data = futures.fetch_main_daily(code, 260)
    tf = {
        "30m": futures.fetch_main_minute(code, "30", 40),
        "1h": futures.fetch_main_minute(code, "60", 40),
        "daily": data[-90:],
        "weekly": futures._resample(data, "W-FRI")[-40:],
        "monthly": futures._resample(data, "ME")[-18:],
    }
    last, prev = data[-1], data[-2]
    cp = (last["close"] - prev["close"]) / prev["close"] * 100
    products_for_llm.append({"code": code, "name": name, "timeframes": tf,
                             "levels_struct": futures.compute_level_structures(tf),
                             "change_pct": round(cp, 2), "inflow": 0,
                             "last_date": last["date"]})
    rows.append({"code": code, "name": name, "change_pct": round(cp, 2),
                 "last_close": tf["30m"][-1]["close"]})

llm_all = llm.analyze_chan_with_llm(cfg2, products_for_llm)
opp_by_code = {o["code"]: o for o in llm_all if o.get("chan", {}).get("has_signal")}

def _lvl_of(code):
    return next((p.get("levels_struct") or {} for p in products_for_llm
                 if p["code"] == code), {})

for r in rows:
    if r["code"] in opp_by_code:
        r["chan"] = dict(opp_by_code[r["code"]]["chan"])
        r["chan"]["levels"] = _lvl_of(r["code"])
ok = sorted(rows, key=lambda x: abs(x["change_pct"]), reverse=True)
structs = []
for r in ok:
    a = next((o for o in llm_all if o["code"] == r["code"]), None)
    if a:
        chan = dict(a["chan"]); chan["levels"] = _lvl_of(r["code"])
    else:
        chan = {"levels": _lvl_of(r["code"]), "has_signal": False,
                "signal": "none", "point": "None"}
    structs.append({"name": r["name"], "code": r["code"],
                    "change_pct": r["change_pct"], "chan": chan})
opps = [r for r in rows if r.get("chan")]

html = (f"<!doctype html><html><head><meta charset=utf-8><style>{render.CSS}</style></head>"
        f"<body><div class='wrap'><div class='hd'><h1>缠论多级别联立 · 子集验证</h1>"
        f"<div class='sub'>数据日期 2026-07-21 ｜ 仅演示 {len(subset)} 个品种（30分钟→月线）</div></div>"
        f"<div class='bd'>")
html += ("<h2>② 缠论机会筛选（多级别联立）</h2>"
         "<div class='note'>本模块由本地大模型基于标准缠论框架做多级别联立分析"
         "（30分钟/1小时为操作级，日线/周线/月线定方向与背景），仅输出当天30分钟/1小时出现的买卖点信号。"
         "下方「多级别结构速览」展示各品种 30分钟→月线 的联立结构（无论是否有当日买卖点）。</div>")
html += render.chan_table(opps)
html += render.chan_structures_table(structs)
html += "</div></div></body></html>"
open("/tmp/chan_demo.html", "w", encoding="utf-8").write(html)

print("WROTE /tmp/chan_demo.html | opps=%d structs=%d" % (len(opps), len(structs)))
for o in opps:
    c = o["chan"]
    print("SIGNAL:", o["name"], c["point"], c["signal"], "| levels:", c["levels"])
print("--- sample structs (no signal) ---")
for s in structs:
    if not s["chan"].get("has_signal"):
        print(s["name"], "levels:", s["chan"]["levels"])
