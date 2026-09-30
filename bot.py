"""
بوت تداول XAU/USD ومؤشرات/عملات على GitHub Actions
مسح الفريمات M5 و M15، إدارة صفقة تلقائية، خروج مبكر عند الانعكاس القوي، ونبض متابعة.
"""
import os
import sys
import json
import time
from datetime import datetime, timezone
import pandas as pd
import requests


def env(name, default=""):
    v = os.environ.get(name)
    return v if v not in (None, "") else default


TD_KEY = env("TWELVE_DATA_API_KEY")
TG_TOKEN = env("TELEGRAM_BOT_TOKEN")
TG_CHAT = env("TELEGRAM_CHAT_ID")
SYMBOLS = [s.strip() for s in env("SYMBOLS", "XAU/USD").split(",") if s.strip()]
MIN_SCORE = float(env("MIN_SCORE", "5"))          # أقل نقاط للإشارة على M15 (وM5 بيحتاج +1)
RR = float(env("RR", "1.5"))                      # نسبة الهدف الأول للمخاطرة
MAX_PER_DAY = int(env("MAX_PER_DAY", "5"))        # أقصى عدد إشارات باليوم
MAX_LOSSES = int(env("MAX_LOSSES", "3"))          # بعد هالعدد من الخسائر بيوقف لباقي اليوم
COOLDOWN_MIN = int(env("COOLDOWN_MIN", "20"))     # أقل فاصل بين إشارتين لنفس الأداة
EXPIRE_HOURS = float(env("EXPIRE_HOURS", "4"))     # بدون نتيجة بعد هالمدة بتنسكّر الصفقة
HEARTBEAT_HOURS = float(env("HEARTBEAT_HOURS", "4")) # إذا ما وصلت رسائل، بيبعت "البوت شغال" مع قراءة السوق
PIPS = {"XAU/USD": 0.1, "GBP/JPY": 0.01, "USD/JPY": 0.01, "EUR/USD": 0.0001,
        "GBP/USD": 0.0001, "BTC/USD": 1.0, "NDX": 1.0}
EXCELLENT_SCORE = float(env("EXCELLENT_SCORE", "9")) # تقييم ممتازة
GOOD_SCORE = float(env("GOOD_SCORE", "7"))           # تقييم جيدة
STATE_FILE = "state.json"


def prep(df):
    d = df.copy()
    d["EMA50"] = d["Close"].ewm(span=50, adjust=False).mean()
    d["EMA200"] = d["Close"].ewm(span=200, adjust=False).mean()
    ch = d["Close"].diff()
    up = ch.clip(lower=0).ewm(alpha=1 / 14, adjust=False).mean()
    dn = (-ch.clip(upper=0)).ewm(alpha=1 / 14, adjust=False).mean()
    d["RSI"] = 100 - 100 / (1 + up / dn.replace(0, 1e-9))
    tr = pd.concat([d["High"] - d["Low"], (d["High"] - d["Close"].shift()).abs(),
                    (d["Low"] - d["Close"].shift()).abs()], axis=1).max(axis=1)
    d["ATR"] = tr.ewm(alpha=1 / 14, adjust=False).mean()
    return d


def swings(d, n=3):
    h, l = d["High"].values, d["Low"].values
    H, L = [], []
    for i in range(n, len(d) - n):
        if h[i] == h[i - n:i + n + 1].max():
            H.append((i, float(h[i])))
        if l[i] == l[i - n:i + n + 1].min():
            L.append((i, float(l[i])))
    return H, L


def trend(df):
    d = prep(df)
    last = d.iloc[-1]
    H, L = swings(d)
    t = 1 if last["EMA50"] > last["EMA200"] else -1
    if len(H) >= 2 and len(L) >= 2:
        if H[-1][1] > H[-2][1] and L[-1][1] > L[-2][1]:
            t += 1
        elif H[-1][1] < H[-2][1] and L[-1][1] < L[-2][1]:
            t -= 1
    return (t > 0) - (t < 0)


def sr_zones(d, atr):
    H, L = swings(d, 3)
    pts = sorted(p for _, p in H + L)
    zs = []
    for p in pts:
        if zs and abs(p - zs[-1]["s"] / zs[-1]["n"]) <= 0.5 * atr:
            zs[-1]["s"] += p
            zs[-1]["n"] += 1
            zs[-1]["hi"] = p
        else:
            zs.append({"s": p, "n": 1, "lo": p, "hi": p})
    return [{"lvl": z["s"] / z["n"], "n": z["n"], "lo": z["lo"], "hi": z["hi"]} for z in zs if z["n"] >= 2]


def candle(d):
    c, p = d.iloc[-1], d.iloc[-2]
    body = abs(c["Close"] - c["Open"])
    rng = (c["High"] - c["Low"]) or 1e-9
    up = c["High"] - max(c["Close"], c["Open"])
    lo = min(c["Close"], c["Open"]) - c["Low"]
    bull = (lo >= 2 * body and lo >= 0.5 * rng) or (
        c["Close"] > c["Open"] and p["Close"] < p["Open"] and c["Close"] >= p["Open"] and c["Open"] <= p["Close"])
    bear = (up >= 2 * body and up >= 0.5 * rng) or (
        c["Close"] < c["Open"] and p["Close"] > p["Open"] and c["Close"] <= p["Open"] and c["Open"] >= p["Close"])
    return bool(bull), bool(bear)


def fvgs(d, look=80):
    sub = d.iloc[-look:]
    hi, lo = sub["High"].values, sub["Low"].values
    out = []
    for i in range(2, len(sub)):
        if lo[i] > hi[i - 2]:
            z = (float(hi[i - 2]), float(lo[i]))
            if all(lo[j] > z[0] for j in range(i + 1, len(sub))):
                out.append((1, z))
        if hi[i] < lo[i - 2]:
            z = (float(hi[i]), float(lo[i - 2]))
            if all(hi[j] < z[1] for j in range(i + 1, len(sub))):
                out.append((-1, z))
    return out[-4:]


def order_block(d):
    n = len(d)
    body = d["Close"] - d["Open"]
    for i in range(n - 4, max(n - 60, 2), -1):
        if abs(body.iloc[i]) >= 1.5 * float(d["ATR"].iloc[i]):
            p = d.iloc[i - 1]
            z = (float(p["Low"]), float(p["High"]))
            if body.iloc[i] > 0 and p["Close"] < p["Open"] and (d["Close"].iloc[i + 1:-1] > z[0]).all():
                return 1, z
            if body.iloc[i] < 0 and p["Close"] > p["Open"] and (d["Close"].iloc[i + 1:-1] < z[1]).all():
                return -1, z
            return None
    return None


def extra_setups(d, price, atr, H, L, tr):
    out = []
    n = len(d)
    hi, lo, cl, op = (d[c].values for c in ("High", "Low", "Close", "Open"))

    def add(name, dr, sl, base, why):
        out.append({"name": name, "dir": dr, "sl": float(sl), "score": base, "why": why})

    g = lambda x: f"{x:.5g}"

    done = set()
    for i in range(max(n - 12, 8), n - 1):
        for hidx, lv in H[-4:]:
            if 1 in done:
                break
            if hidx < i and hi[i] > lv and cl[i] < lv:
                level = lo[i - 6:i].min()
                ks = [k for k in range(i + 1, n) if cl[k] < level]
                if ks and ks[0] >= n - 3 and price <= level + 0.3 * atr and level - price <= 2 * atr:
                    add("Sweep + CHOCH بيع", -1, hi[i:].max() + 0.2 * atr, 4,
                        f"سحب سيولة فوق {g(lv)} ثم كسر هيكل هابط تحت {g(level)}")
                    done.add(1)
        for lidx, lv in L[-4:]:
            if 2 in done:
                break
            if lidx < i and lo[i] < lv and cl[i] > lv:
                level = hi[i - 6:i].max()
                ks = [k for k in range(i + 1, n) if cl[k] > level]
                if ks and ks[0] >= n - 3 and price >= level - 0.3 * atr and price - level <= 2 * atr:
                    add("Sweep + CHOCH شراء", 1, lo[i:].min() - 0.2 * atr, 4,
                        f"سحب سيولة تحت {g(lv)} ثم كسر هيكل صاعد فوق {g(level)}")
                    done.add(2)

    if len(H) >= 2 and len(L) >= 1 and H[-2][1] > H[-1][1]:
        lv = H[-1][1]
        if cl[-1] > lv and cl[-4:-1].min() <= lv and cl[-1] - lv <= 1.5 * atr:
            add("CHOCH صاعد", 1, L[-1][1] - 0.2 * atr, 2, f"كسر آخر قمة هابطة {g(lv)} (تغير طبيعة السوق)")
    if len(L) >= 2 and len(H) >= 1 and L[-2][1] < L[-1][1]:
        lv = L[-1][1]
        if cl[-1] < lv and cl[-4:-1].max() >= lv and lv - cl[-1] <= 1.5 * atr:
            add("CHOCH هابط", -1, H[-1][1] + 0.2 * atr, 2, f"كسر آخر قاع صاعد {g(lv)} (تغير طبيعة السوق)")

    W = 20
    if n > W + 4:
        seg = slice(n - W - 2, n - 2)
        rh, rl = hi[seg].max(), lo[seg].min()
        if 0.8 * atr <= rh - rl <= 3.5 * atr:
            body, mid = abs(cl[-1] - op[-1]), (rh + rl) / 2
            if cl[-1] > rh + 0.1 * atr and cl[-2] <= rh + 0.1 * atr and body >= 0.5 * atr:
                add("Range Breakout صاعد", 1, mid - 0.2 * atr, 3, f"كسر رينج [{g(rl)}-{g(rh)}] لأعلى")
            if cl[-1] < rl - 0.1 * atr and cl[-2] >= rl - 0.1 * atr and body >= 0.5 * atr:
                add("Range Breakout هابط", -1, mid + 0.2 * atr, 3, f"كسر رينج [{g(rl)}-{g(rh)}] لأسفل")

    if len(H) >= 2:
        (i1, p1), (i2, p2) = H[-2], H[-1]
        if i2 - i1 >= 8 and abs(p1 - p2) <= 0.35 * atr and n - 1 - i2 <= 30:
            neck = lo[i1:i2 + 1].min()
            if cl[-1] < neck and cl[-2] >= neck:
                add("Double Top", -1, max(p1, p2) + 0.2 * atr, 3, f"قمة مزدوجة ~{g(p2)} وكسر العنق {g(neck)}")
    if len(L) >= 2:
        (i1, p1), (i2, p2) = L[-2], L[-1]
        if i2 - i1 >= 8 and abs(p1 - p2) <= 0.35 * atr and n - 1 - i2 <= 30:
            neck = hi[i1:i2 + 1].max()
            if cl[-1] > neck and cl[-2] <= neck:
                add("Double Bottom", 1, min(p1, p2) - 0.2 * atr, 3, f"قاع مزدوج ~{g(p2)} وكسر العنق {g(neck)}")

    if len(H) >= 3:
        (i1, p1), (i2, p2), (i3, p3) = H[-3:]
        if p2 > max(p1, p3) + 0.5 * atr and abs(p1 - p3) <= 0.8 * atr and i3 - i1 <= 80 and n - 1 - i3 <= 30:
            neck = (lo[i1:i2 + 1].min() + lo[i2:i3 + 1].min()) / 2
            if cl[-1] < neck and cl[-2] >= neck:
                add("Head & Shoulders", -1, p3 + 0.2 * atr, 3, f"رأس وكتفين وكسر العنق {g(neck)}")
    if len(L) >= 3:
        (i1, p1), (i2, p2), (i3, p3) = L[-3:]
        if p2 < min(p1, p3) - 0.5 * atr and abs(p1 - p3) <= 0.8 * atr and i3 - i1 <= 80 and n - 1 - i3 <= 30:
            neck = (hi[i1:i2 + 1].max() + hi[i2:i3 + 1].max()) / 2
            if cl[-1] > neck and cl[-2] <= neck:
                add("Inverse H&S", 1, p3 - 0.2 * atr, 3, f"رأس وكتفين مقلوب وكسر العنق {g(neck)}")

    if len(H) >= 2 and len(L) >= 2:
        (a1, b1), (a2, b2) = H[-2], H[-1]
        (c1, d1), (c2, d2) = L[-2], L[-1]
        if a2 - a1 >= 6 and c2 - c1 >= 6 and max(a2, c2) - min(a1, c1) <= 100:
            su, sw = (b2 - b1) / (a2 - a1), (d2 - d1) / (c2 - c1)
            up = lambda x: b2 + su * (x - a2)
            lw = lambda x: d2 + sw * (x - c2)
            x0 = min(a1, c1)
            w0, wn = up(x0) - lw(x0), up(n - 1) - lw(n - 1)
            conv = wn < 0.85 * w0 and wn > 0.5 * atr and su <= 0.03 * atr and sw >= -0.03 * atr
            par = abs(su - sw) < 0.04 * atr and w0 > 0.8 * atr and wn > 0.8 * atr
            if conv or par:
                nm = "قناة سعرية"
                if conv:
                    nm = ("مثلث صاعد" if abs(su) <= 0.03 * atr and sw > 0.03 * atr else
                          "مثلث هابط" if abs(sw) <= 0.03 * atr and su < -0.03 * atr else "مثلث متماثل")
                mid = (up(n - 1) + lw(n - 1)) / 2
                base = 3 if conv else 2
                if cl[-1] > up(n - 1) + 0.1 * atr and cl[-2] <= up(n - 2) + 0.1 * atr:
                    add(f"كسر {nm} لأعلى", 1, mid - 0.2 * atr, base, f"كسر خط {nm} العلوي")
                if cl[-1] < lw(n - 1) - 0.1 * atr and cl[-2] >= lw(n - 2) - 0.1 * atr:
                    add(f"كسر {nm} لأسفل", -1, mid + 0.2 * atr, base, f"كسر خط {nm} السفلي")

    if len(H) >= 2 and len(L) >= 2:
        (a1, b1), (a2, b2) = H[-2], H[-1]
        (c1, e1), (c2, e2) = L[-2], L[-1]
        if a2 - a1 >= 6 and c2 - c1 >= 6 and max(a2, c2) - min(a1, c1) <= 100:
            su, sw = (b2 - b1) / (a2 - a1), (e2 - e1) / (c2 - c1)
            up = lambda x: b2 + su * (x - a2)
            lw = lambda x: e2 + sw * (x - c2)
            x0 = min(a1, c1)
            w0, wn = up(x0) - lw(x0), up(n - 1) - lw(n - 1)
            if wn < 0.85 * w0 and wn > 0.5 * atr:
                if su > 0.01 * atr and sw > su * 1.15 and cl[-1] < lw(n - 1) - 0.1 * atr and cl[-2] >= lw(n - 2) - 0.1 * atr:
                    add("Rising Wedge", -1, up(n - 1) + 0.2 * atr, 3, "كسر وتد صاعد لأسفل")
                if su < -0.01 * atr and sw < 0 and abs(sw) < abs(su) * 0.87 and cl[-1] > up(n - 1) + 0.1 * atr and cl[-2] <= up(n - 2) + 0.1 * atr:
                    add("Falling Wedge", 1, lw(n - 1) - 0.2 * atr, 3, "كسر وتد هابط لأعلى")

    if len(H) >= 3:
        vals = [p_ for _, p_ in H[-3:]]
        i1, i3 = H[-3][0], H[-1][0]
        if max(vals) - min(vals) <= 0.45 * atr and i3 - i1 >= 12 and n - 1 - i3 <= 40:
            neck = lo[i1:i3 + 1].min()
            if cl[-1] < neck and cl[-2] >= neck:
                add("Triple Top", -1, max(vals) + 0.2 * atr, 3, f"قمة ثلاثية ~{g(vals[-1])} وكسر العنق {g(neck)}")
    if len(L) >= 3:
        vals = [p_ for _, p_ in L[-3:]]
        i1, i3 = L[-3][0], L[-1][0]
        if max(vals) - min(vals) <= 0.45 * atr and i3 - i1 >= 12 and n - 1 - i3 <= 40:
            neck = hi[i1:i3 + 1].max()
            if cl[-1] > neck and cl[-2] <= neck:
                add("Triple Bottom", 1, min(vals) - 0.2 * atr, 3, f"قاع ثلاثي ~{g(vals[-1])} وكسر العنق {g(neck)}")

    if n >= 30:
        seg = d.iloc[-26:-11]
        move = float(seg["Close"].iloc[-1] - seg["Close"].iloc[0])
        aa = float(seg["ATR"].mean())
        top, bot_ = float(hi[-11:-1].max()), float(lo[-11:-1].min())
        if abs(move) > 2.0 * aa and (top - bot_) < 2.0 * aa:
            if move > 0 and cl[-1] > top and cl[-2] <= top:
                add("Bull Flag/Pennant", 1, bot_ - 0.15 * atr, 3, "اندفاع صاعد + تماسك ضيق ثم كسر لأعلى")
            elif move < 0 and cl[-1] < bot_ and cl[-2] >= bot_:
                add("Bear Flag/Pennant", -1, top + 0.15 * atr, 3, "اندفاع هابط + تماسك ضيق ثم كسر لأسفل")
    return out


def scan(df, b4, b1d, crypto, levels=None):
    d = prep(df)
    last = d.iloc[-1]
    price, atr, rsi = float(last["Close"]), float(last["ATR"]), float(last["RSI"])
    ema50, ema200 = float(last["EMA50"]), float(last["EMA200"])
    H, L = swings(d)
    zones = sr_zones(d.iloc[-250:], atr)
    bull, bear = candle(d)
    tr = trend(d)
    out = []

    def add(name, dr, sl, base, why):
        out.append({"name": name, "dir": dr, "sl": float(sl), "score": base, "why": why})

    swl = L[-1][1] if L else price - 2 * atr
    swh = H[-1][1] if H else price + 2 * atr

    if tr == 1 and abs(price - ema50) <= 0.6 * atr and rsi < 60 and price > ema200:
        add("ارتداد EMA50 مع الاتجاه", 1, min(swl, price - 1.2 * atr) - 0.2 * atr, 3, "اتجاه صاعد وتراجع للمتوسط 50")
    if tr == -1 and abs(price - ema50) <= 0.6 * atr and rsi > 40 and price < ema200:
        add("ارتداد EMA50 مع الاتجاه", -1, max(swh, price + 1.2 * atr) + 0.2 * atr, 3, "اتجاه هابط وصعود للمتوسط 50")

    for z in zones:
        base = 2 + (1 if z["n"] >= 3 else 0)
        if bull and z["lo"] - 0.3 * atr <= price <= z["hi"] + 0.5 * atr:
            add("ارتداد من دعم", 1, z["lo"] - 0.3 * atr, base, f"دعم لمس {z['n']} مرات + شمعة رفض صاعدة")
        if bear and z["lo"] - 0.5 * atr <= price <= z["hi"] + 0.3 * atr:
            add("ارتداد من مقاومة", -1, z["hi"] + 0.3 * atr, base, f"مقاومة لمست {z['n']} مرات + شمعة رفض هابطة")

    rh, rl = float(d["High"].iloc[-3:].max()), float(d["Low"].iloc[-3:].min())
    for _, lv in H[-3:]:
        if rh > lv > price:
            add("سحب سيولة قمة", -1, rh + 0.2 * atr, 3, f"اختراق كاذب فوق {lv:.2f} وإغلاق تحته")
            break
    for _, lv in L[-3:]:
        if rl < lv < price:
            add("سحب سيولة قاع", 1, rl - 0.2 * atr, 3, f"اختراق كاذب تحت {lv:.2f} وإغلاق فوقه")
            break

    for dr, (lo, hi) in fvgs(d):
        if dr == 1 and tr >= 0 and lo - 0.2 * atr <= price <= hi:
            add("إعادة اختبار FVG صاعدة", 1, lo - 0.3 * atr, 2, f"السعر داخل فجوة [{lo:.2f}-{hi:.2f}]")
        if dr == -1 and tr <= 0 and lo <= price <= hi + 0.2 * atr:
            add("إعادة اختبار FVG هابطة", -1, hi + 0.3 * atr, 2, f"السعر داخل فجوة [{lo:.2f}-{hi:.2f}]")

    n = len(d)
    for i, lv in H[-3:]:
        if i < n - 12 and (d["Close"].iloc[-12:-1] > lv).any() and lv - 0.2 * atr <= price <= lv + 0.5 * atr:
            add("كسر وإعادة اختبار", 1, lv - 0.8 * atr, 2, f"كسر {lv:.2f} وعودة لاختباره")
            break
    for i, lv in L[-3:]:
        if i < n - 12 and (d["Close"].iloc[-12:-1] < lv).any() and lv - 0.5 * atr <= price <= lv + 0.2 * atr:
            add("كسر وإعادة اختبار", -1, lv + 0.8 * atr, 2, f"كسر {lv:.2f} وعودة لاختباره")
            break

    if H and L:
        (iH, pH), (iL, pL) = H[-1], L[-1]
        leg = abs(pH - pL)
        if leg > 1.5 * atr:
            if iL < iH and tr >= 0:
                lo, hi = pH - 0.786 * leg, pH - 0.5 * leg
                if lo - 0.2 * atr <= price <= hi + 0.2 * atr:
                    add("فيبوناتشي ذهبية", 1, pL - 0.1 * atr, 2, f"تصحيح داخل 0.5-0.786 [{lo:.2f}-{hi:.2f}]")
            if iH < iL and tr <= 0:
                lo, hi = pL + 0.5 * leg, pL + 0.786 * leg
                if lo - 0.2 * atr <= price <= hi + 0.2 * atr:
                    add("فيبوناتشي ذهبية", -1, pH + 0.1 * atr, 2, f"تصحيح داخل 0.5-0.786 [{lo:.2f}-{hi:.2f}]")

    near_zone = any(z["lo"] - 0.5 * atr <= price <= z["hi"] + 0.5 * atr for z in zones)
    if rsi <= 30 and near_zone:
        add("تشبع بيعي RSI عند دعم", 1, price - 1.5 * atr, 1, f"RSI={rsi:.0f} عند منطقة دعم")
    if rsi >= 70 and near_zone:
        add("تشبع شرائي RSI عند مقاومة", -1, price + 1.5 * atr, 1, f"RSI={rsi:.0f} عند منطقة مقاومة")

    ob = order_block(d)
    if ob:
        dr, (lo, hi) = ob
        if dr == 1 and lo - 0.2 * atr <= price <= hi + 0.2 * atr:
            add("Order Block صاعد", 1, lo - 0.3 * atr, 2, f"عودة لكتلة أوامر [{lo:.2f}-{hi:.2f}]")
        if dr == -1 and lo - 0.2 * atr <= price <= hi + 0.2 * atr:
            add("Order Block هابط", -1, hi + 0.3 * atr, 2, f"عودة لكتلة أوامر [{lo:.2f}-{hi:.2f}]")

    out += extra_setups(d, price, atr, H, L, tr)
    if levels:
        out += level_setups(d, price, atr, levels)

    hour = datetime.now(timezone.utc).hour
    session = crypto or 7 <= hour <= 20
    cnt = {1: len({s["name"] for s in out if s["dir"] == 1}), -1: len({s["name"] for s in out if s["dir"] == -1})}
    kv = []
    if levels:
        kv = list(levels["keys"].items()) + [(f"رقم دائري {r:g}", r) for r in levels["round"]]
    for s in out:
        dr, tags, warn = s["dir"], [], []
        for bias, label in ((b4, "الفريم الأكبر"), (b1d, "الفريم الأعلى")):
            if bias == dr:
                s["score"] += 1
                tags.append(f"مع اتجاه {label}")
            elif bias == -dr:
                s["score"] -= 1
                warn.append(f"عكس اتجاه {label} (خُصمت نقطة)")
        if (dr == 1 and bull) or (dr == -1 and bear):
            s["score"] += 1
            tags.append("شمعة تأكيد")
        if (dr == 1 and rsi < 50) or (dr == -1 and rsi > 50):
            s["score"] += 1
            tags.append("RSI داعم")
        if session:
            s["score"] += 1
            tags.append("جلسة نشطة")
        if cnt[dr] > 1:
            s["score"] += min(cnt[dr] - 1, 2)
            tags.append(f"تلاقي {cnt[dr]} استراتيجيات بنفس الاتجاه")
        if levels:
            if (dr == 1 and price > levels["open"]) or (dr == -1 and price < levels["open"]):
                s["score"] += 1
                tags.append(f"مع اتجاه اليوم (افتتاح {levels['open']:.5g})")
            near = [nm for nm, v in kv if abs(price - v) <= 0.6 * atr]
            if near:
                s["score"] += 1
                tags.append(f"عند مستوى مهم: {near[0]}")
        s["tags"], s["warn"] = tags, warn
    ctx = {"price": price, "atr": atr, "rsi": rsi, "trend": tr, "zones": zones}
    return out, ctx


def build(s, price, atr, pip, rr, min_tp):
    dr, sl = s["dir"], s["sl"]
    if (dr == 1 and sl >= price) or (dr == -1 and sl <= price):
        return None, "ستوب بالاتجاه الغلط"
    risk = abs(price - sl)
    if risk < 0.7 * atr:
        sl, risk = price - dr * 0.7 * atr, 0.7 * atr
    if risk > 4 * atr:
        return None, "الستوب بعيد (أكبر من 4 ATR)"
    tp1_p = rr * risk / pip
    if min_tp and tp1_p < min_tp:
        return None, f"TP1 = {tp1_p:.0f} بيب، أقل من {min_tp:.0f}"
    return {"entry": price, "sl": sl, "tp1": price + dr * rr * risk, "tp2": price + dr * rr * 1.8 * risk,
            "risk_p": risk / pip, "tp1_p": tp1_p, "tp2_p": rr * 1.8 * risk / pip}, ""


def reversal_reasons(fr, t, live):
    d = prep(fr["df"])
    n = len(d)
    if n < 30:
        return []
    atr = float(d["ATR"].iloc[-1])
    cl, op, hi, lo = (d[c].values for c in ("Close", "Open", "High", "Low"))
    dr = t["dir"]
    risk = abs(t["entry"] - t["sl"]) or 1e-9
    R = dr * (live - t["entry"]) / risk
    H, L = swings(d)
    choch = False
    if dr == 1 and L:
        lv = L[-1][1]
        choch = lv > t["sl"] and cl[-1] < lv and cl[-4:-1].max() >= lv
    if dr == -1 and H:
        lv = H[-1][1]
        choch = lv < t["sl"] and cl[-1] > lv and cl[-4:-1].min() <= lv
    if not choch:
        return []
    reasons = ["كسر هيكل عكس الصفقة (CHOCH) بإغلاق شمعة مكتملة"]
    rng = (hi[-1] - lo[-1]) or 1e-9
    if abs(cl[-1] - op[-1]) >= atr and (
            (dr == 1 and cl[-1] < op[-1] and (cl[-1] - lo[-1]) / rng <= 0.25) or
            (dr == -1 and cl[-1] > op[-1] and (hi[-1] - cl[-1]) / rng <= 0.25)):
        reasons.append("شمعة زخم قوية عكس الصفقة")
    setups, _ = scan(fr["df"], fr["b"][0], fr["b"][1], False, fr.get("lv"))
    opp = [s for s in setups if s["dir"] == -dr and s["score"] >= 6]
    if opp:
        reasons.append(f"إشارة معاكسة قوية: {opp[0]['name']} (نقاط {opp[0]['score']:.0f})")
    if R <= -0.5:
        reasons.append(f"السعر قطع نص المسافة نحو الستوب ({R:+.2f}R)")
    return reasons if len(reasons) >= 2 else []


def session_name(hour):
    if hour < 7:
        return "آسيا"
    if hour < 12:
        return "لندن"
    if hour < 16:
        return "تداخل لندن/نيويورك"
    if hour < 21:
        return "نيويورك"
    return "خارج الجلسات"


def gold_levels(df, now, round_step=0.0):
    days = list(df.index.normalize().unique())
    today = days[-1]
    keys = {}
    for dday in reversed(days[:-1]):
        dp = df[(df.index >= dday) & (df.index < dday + pd.Timedelta(days=1))]
        if len(dp) >= 100:
            keys["قمة أمس"], keys["قاع أمس"] = float(dp["High"].max()), float(dp["Low"].min())
            break
    dt = df[df.index >= today]
    asia = dt[dt.index.hour < 7]
    if len(asia) >= 6 and now.hour >= 7:
        keys["قمة آسيا"], keys["قاع آسيا"] = float(asia["High"].max()), float(asia["Low"].min())
    op = float(dt["Open"].iloc[0]) if len(dt) else float(df["Open"].iloc[-1])
    px = float(df["Close"].iloc[-1])
    rnd = []
    if round_step:
        b = round(px / round_step) * round_step
        rnd = [b - round_step, b, b + round_step]
    return {"keys": keys, "open": op, "round": rnd}


def level_setups(d, price, atr, lv):
    out = []
    hi, lo = d["High"].values, d["Low"].values
    cl = d["Close"].values
    rh, rl = hi[-3:].max(), lo[-3:].min()
    bull, bear = candle(d)
    g = lambda x: f"{x:.5g}"
    for nm, v in lv["keys"].items():
        if rh > v > cl[-1] and v - price <= 1.5 * atr:
            out.append({"name": f"سحب سيولة {nm}", "dir": -1, "sl": float(rh + 0.2 * atr), "score": 4,
                        "why": f"السعر سحب سيولة فوق {nm} ({g(v)}) ورجع تحته"})
        elif rl < v < cl[-1] and price - v <= 1.5 * atr:
            out.append({"name": f"سحب سيولة {nm}", "dir": 1, "sl": float(rl - 0.2 * atr), "score": 4,
                        "why": f"السعر سحب سيولة تحت {nm} ({g(v)}) ورجع فوقه"})
        elif abs(price - v) <= 0.4 * atr:
            if bull and lo[-1] <= v + 0.2 * atr:
                out.append({"name": f"ارتداد من {nm}", "dir": 1, "sl": float(min(lo[-1], v) - 0.3 * atr), "score": 3,
                            "why": f"رفض صاعد عند {nm} ({g(v)})"})
            if bear and hi[-1] >= v - 0.2 * atr:
                out.append({"name": f"ارتداد من {nm}", "dir": -1, "sl": float(max(hi[-1], v) + 0.3 * atr), "score": 3,
                            "why": f"رفض هابط عند {nm} ({g(v)})"})
    return out


def grade(score):
    if score >= EXCELLENT_SCORE:
        return "ممتازة ⭐⭐⭐", "ممتازة", 1.0
    if score >= GOOD_SCORE:
        return "جيدة ⭐⭐", "جيدة", 0.5
    return "متوسطة ⭐", "متوسطة", 0.25


def market_read(px, fr, lv, hour):
    tr = lambda t: "صاعد" if t > 0 else "هابط" if t < 0 else "عرضي"
    b_h1, b_h4 = fr["15min"]["b"]
    lines = [f"الجلسة: {session_name(hour)}", f"السعر: {px:.5g}  | افتتاح اليوم: {lv['open']:.5g}",
             f"اتجاه H4: {tr(b_h4)} | H1: {tr(b_h1)}"]
    near = sorted((abs(px - v), nm, v) for nm, v in lv["keys"].items())[:3]
    if near:
        lines.append("أقرب مستويات: " + " | ".join(f"{nm} {v:.5g}" for _, nm, v in near))
    b = b_h4 or b_h1
    if b > 0:
        lines.append("خطتي: أفضّل الشراء مع الاتجاه. أنتظر سحب سيولة تحت قاع قريب ثم CHOCH صاعد، أو ارتداد من مستوى مهم.")
    elif b < 0:
        lines.append("خطتي: أفضّل البيع مع الاتجاه. أنتظر سحب سيولة فوق قمة قريبة ثم CHOCH هابط، أو ارتداد من مستوى مهم.")
    else:
        lines.append("خطتي: السوق عرضي. أنتظر كسر رينج أو سحب سيولة عند حدود الرينج قبل أي دخول.")
    return "\n".join(lines)


SENT = []
LAST_ERR = {"msg": ""}


def fetch(symbol):
    try:
        j = requests.get("https://api.twelvedata.com/time_series", timeout=30, params={
            "symbol": symbol, "interval": "5min", "outputsize": 5000,
            "timezone": "UTC", "apikey": TD_KEY}).json()
    except Exception as e:
        LAST_ERR["msg"] = str(e)[:200]
        return None
    if "values" not in j:
        LAST_ERR["msg"] = str(j.get("message", j))[:200]
        print(symbol, "ERROR:", LAST_ERR["msg"])
        return None
    df = pd.DataFrame(j["values"])
    df["datetime"] = pd.to_datetime(df["datetime"])
    for c in ["open", "high", "low", "close"]:
        df[c] = df[c].astype(float)
    df = df.sort_values("datetime").set_index("datetime")
    df.columns = [c.capitalize() for c in df.columns]
    return df[["Open", "High", "Low", "Close"]]


def rs(df, rule):
    if rule == "5min":
        return df
    return df.resample(rule).agg({"Open": "first", "High": "max", "Low": "min", "Close": "last"}).dropna()


def make_frames(df, now, sym):
    m15, h1, h4 = (rs(df, r).iloc[:-1] for r in ("15min", "1h", "4h"))
    tr = lambda x, k: trend(x) if len(x) >= k else 0
    lv = gold_levels(df, now, 10.0 if sym == "XAU/USD" else 0.0)
    return {
        "5min": {"df": df.iloc[:-1], "b": (tr(m15, 60), tr(h1, 60)), "lv": lv},
        "15min": {"df": m15, "b": (tr(h1, 60), tr(h4, 40)), "lv": lv},
    }


def load():
    try:
        with open(STATE_FILE, encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {"open": [], "days": {}, "last": {}}


def save(st):
    with open(STATE_FILE, "w", encoding="utf-8") as f:
        json.dump(st, f, ensure_ascii=False, indent=1)


def tg(text):
    print(text)
    SENT.append(text)
    if not (TG_TOKEN and TG_CHAT):
        return
    try:
        requests.post(f"https://api.telegram.org/bot{TG_TOKEN}/sendMessage",
                      json={"chat_id": TG_CHAT, "text": text}, timeout=15)
    except Exception as e:
        print("telegram failed:", e)


def check_open(st, m5s, frames, now, day):
    still = []
    for t in st["open"]:
        sym = t["sym"]
        df = m5s.get(sym)
        if df is None:
            still.append(t)
            continue
        t.setdefault("tf", "15min")
        t0 = pd.Timestamp(t["time"])
        res = None
        for _, c in df[df.index > t0].iterrows():
            if t["dir"] == 1:
                sl_hit, tp_hit = c["Low"] <= t["sl"], c["High"] >= t["tp1"]
            else:
                sl_hit, tp_hit = c["High"] >= t["sl"], c["Low"] <= t["tp1"]
            if sl_hit:
                res = "SL"
                break
            if tp_hit:
                res = "TP1"
                break
        if res is None and (now - t0).total_seconds() > EXPIRE_HOURS * 3600:
            res = "EXPIRED"
        side = "شراء" if t["dir"] == 1 else "بيع"
        live = float(df["Close"].iloc[-1])
        risk = abs(t["entry"] - t["sl"]) or 1e-9
        R = t["dir"] * (live - t["entry"]) / risk

        gs = day.setdefault("g", {}).setdefault(t.get("gk", "؟"), [0, 0])
        if res == "TP1":
            gs[0] += 1
            day["wins"] += 1
            day["r"] += t["rr"]
            tg(f"✅ تحقق الهدف الأول\n{sym} ({side}) - {t['name']}\nانقل الستوب لسعر الدخول {t['entry']:.5g}، والهدف الثاني {t['tp2']:.5g}\nالبوت رجع يدوّر على Setup جديد.")
        elif res == "SL":
            gs[1] += 1
            day["losses"] += 1
            day["r"] -= 1
            tg(f"❌ ضرب الستوب\n{sym} ({side}) - {t['name']}\nالستوب {t['sl']:.5g}\nالبوت رجع يدوّر على Setup جديد.")
        elif res == "EXPIRED":
            tg(f"⌛ انتهت الصفقة بدون نتيجة بعد {EXPIRE_HOURS:g} ساعات\n{sym} ({side}) - {t['name']}\nالبوت رجع يدوّر على Setup جديد.")
        else:
            fr = frames.get(sym, {}).get(t["tf"])
            reasons = []
            if fr is not None and (fr["df"].index > t0).sum() >= 2:
                reasons = reversal_reasons(fr, t, live)
            if reasons:
                day["early"] += 1
                day["r"] += R
                if R > 0.05:
                    day["wins"] += 1
                    gs[0] += 1
                elif R < -0.05:
                    day["losses"] += 1
                    gs[1] += 1
                tg(f"🚨 EARLY EXIT — {sym} ({side})\n"
                   f"الدخول: {t['entry']:.5g} | السعر الحالي: {live:.5g}\n"
                   f"النتيجة الحالية: {R:+.2f}R\n"
                   f"سبب الإغلاق المقترح:\n- " + "\n- ".join(reasons) + "\n"
                   f"يُفضّل إغلاق الصفقة الآن. البوت سكّرها عندو وبيدوّر على Setup جديد.")
                continue
            if R >= 1.0 and not t.get("be"):
                t["be"] = True
                tg(f"🛡️ {sym} ({side}) حققت {R:.1f}R\nانقل الستوب لسعر الدخول {t['entry']:.5g} لتأمين الصفقة.")
            still.append(t)
    st["open"] = still


def main():
    if not TD_KEY:
        print("TWELVE_DATA_API_KEY غير موجود")
        sys.exit(0)
    if env("TEST_MSG") == "1":
        tg("✅ البوت شغال وبيوصلك على تيليجرام.")
        return
    now = pd.Timestamp(datetime.now(timezone.utc).replace(tzinfo=None))
    today = now.strftime("%Y-%m-%d")
    st = load()
    for k, dflt in (("open", []), ("days", {}), ("last", {}), ("sig", {})):
        if not isinstance(st.get(k), type(dflt)):
            st[k] = dflt
    day = st["days"].setdefault(today, {})
    for k, v in (("sent", 0), ("wins", 0), ("losses", 0), ("r", 0.0), ("summary", False), ("early", 0)):
        day.setdefault(k, v)
    for k in list(st["days"].keys())[:-14]:
        del st["days"][k]
    st["open"] = [t for t in st["open"] if isinstance(t, dict) and t.get("sym") in SYMBOLS]
    st.setdefault("last_msg", now.isoformat())

    m5s = {}
    for sym in SYMBOLS:
        df = fetch(sym)
        if df is not None and len(df) > 500:
            m5s[sym] = df
        time.sleep(1.5)
    if not m5s:
        last = st.get("err_time")
        if not last or (now - pd.Timestamp(last)).total_seconds() > 3600:
            tg(f"⚠️ البوت ما قدر يجيب بيانات Twelve Data:\n{LAST_ERR['msg']}")
            st["err_time"] = now.isoformat()
        st["last_msg"] = now.isoformat() if SENT else st["last_msg"]
        save(st)
        return

    frames = {sym: make_frames(df, now, sym) for sym, df in m5s.items()}
    check_open(st, m5s, frames, now, day)

    reason = "ما في Setup مطابق حالياً"
    if day["losses"] >= MAX_LOSSES:
        reason = f"وصل حد الخسائر اليومي ({MAX_LOSSES}) وموقّف لباقي اليوم"
    elif day["sent"] >= MAX_PER_DAY:
        reason = f"وصل حد الإشارات اليومي ({MAX_PER_DAY})"
    else:
        open_syms = {t["sym"] for t in st["open"]}
        if open_syms:
            reason = "في صفقة مفتوحة عم يراقبها: " + ", ".join(sorted(open_syms))
        best = {}
        for sym, fr in frames.items():
            last = st["last"].get(sym)
            if sym in open_syms or (last and (now - pd.Timestamp(last)).total_seconds() < COOLDOWN_MIN * 60):
                continue
            live, pip = float(m5s[sym]["Close"].iloc[-1]), PIPS.get(sym, 0.0001)
            for tf, f in fr.items():
                if len(f["df"]) < 80:
                    continue
                setups, ctx = scan(f["df"], f["b"][0], f["b"][1], "BTC" in sym, f["lv"])
                need = MIN_SCORE + (1 if tf == "5min" else 0)
                for s in setups:
                    key = f"{sym}|{s['name']}|{s['dir']}"
                    if key in st["sig"] and (now - pd.Timestamp(st["sig"][key])).total_seconds() < 7200:
                        continue
                    t, _ = build(s, live, ctx["atr"], pip, RR, 0)
                    if t and s["score"] >= need and (sym not in best or s["score"] > best[sym]["score"]):
                        s.update(t)
                        s.update(sym=sym, tf=tf, key=key, ctx_time=m5s[sym].index[-1].isoformat())
                        best[sym] = s
        for s in sorted(best.values(), key=lambda x: -x["score"]):
            if day["sent"] >= MAX_PER_DAY:
                break
            side = "شراء 🟢" if s["dir"] == 1 else "بيع 🔴"
            gl, gk, risk_pct = grade(s["score"])
            s["gk"] = gk
            body = "".join(f"- {x}\n" for x in [s["why"]] + s.get("tags", []))
            warn = "".join(f"⚠️ {x}\n" for x in s.get("warn", []))
            tg(f"⚡ {side} — {s['sym']}\n"
               f"التقييم: {gl} (نقاط {s['score']:.0f})\n"
               f"الجلسة: {session_name(now.hour)} | فريم {s['tf']}\n"
               f"الاستراتيجية: {s['name']}\n\n"
               f"الدخول: {s['entry']:.5g}\n"
               f"الستوب: {s['sl']:.5g} ({s['risk_p']:.0f} بيب)\n"
               f"TP1: {s['tp1']:.5g} ({s['tp1_p']:.0f} بيب)\n"
               f"TP2: {s['tp2']:.5g} ({s['tp2_p']:.0f} بيب)\n"
               f"المخاطرة المقترحة: {risk_pct:g}% من الحساب حسب التقييم\n\n"
               f"ليش هالصفقة:\n{body}{warn}\n"
               f"الخطة: عند TP1 سكّر نص الحجم وانقل الستوب للدخول وكمّل للـ TP2. "
               f"الصفقة بتبطل عند الستوب. راجع السعر عند وسيطك قبل الدخول، والبوت بيراقبها وبيبعتلك EARLY EXIT عند انعكاس قوي.")
            st["open"].append({"sym": s["sym"], "dir": s["dir"], "entry": s["entry"], "sl": s["sl"],
                               "tp1": s["tp1"], "tp2": s["tp2"], "rr": RR, "name": s["name"],
                               "tf": s["tf"], "time": s["ctx_time"], "be": False, "gk": s["gk"]})
            st["last"][s["sym"]] = now.isoformat()
            st["sig"][s["key"]] = now.isoformat()
            day["sent"] += 1
        st["sig"] = {k: v for k, v in st["sig"].items() if (now - pd.Timestamp(v)).total_seconds() < 86400}

    if now.hour >= 21 and not day["summary"]:
        tg(f"📊 ملخص {today} (UTC)\nإشارات: {day['sent']} | رابحة: {day['wins']} | "
           f"خاسرة: {day['losses']} | خروج مبكر: {day['early']} | صافي: {day['r']:+.1f}R\n"
           + "حسب التقييم: " + (" | ".join(f"{k} {v[0]}✅/{v[1]}❌" for k, v in day.get("g", {}).items()) or "لا يوجد"))
        day["summary"] = True

    if not SENT and (now - pd.Timestamp(st["last_msg"])).total_seconds() >= HEARTBEAT_HOURS * 3600:
        sym0 = next(iter(m5s))
        px0 = float(m5s[sym0]["Close"].iloc[-1])
        tg(f"🫀 البوت شغال ({now:%H:%M} UTC) - {sym0}\nما وصلت إشارات منذ {HEARTBEAT_HOURS:g} ساعات.\n"
           f"السبب: {reason}\n\n{market_read(px0, frames[sym0], frames[sym0]['15min']['lv'], now.hour)}\n\n"
           f"اليوم: إشارات {day['sent']} | رابحة {day['wins']} | خاسرة {day['losses']} | صافي {day['r']:+.1f}R")
    if SENT:
        st["last_msg"] = now.isoformat()
    save(st)


if __name__ == "__main__":
    main()
