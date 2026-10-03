"""بوت XAU/USD على GitHub Actions: سكان M5+M15، صفقة واحدة، إدارة صفقة وخروج مبكر عند انعكاس قوي + محرك انعكاس M30 (REV / EARLY / SWING)"""
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
MIN_SCORE = float(env("MIN_SCORE", "4"))       # أقل نقاط للإشارة (نزّلو لـ 4 لإشارات أكتر، ارفعو لـ 6 لصفقات أكتر انتقاء)
RR = float(env("RR", "1.5"))                   # أقل نسبة ربح/مخاطرة للهدف الأول
MIN_TP_PIPS = float(env("MIN_TP_PIPS", "50"))  # أقل هدف أول بالبيب (الذهب: 50 بيب = 5$)
FRAMES = [f.strip() for f in env("FRAMES", "1min,5min,15min").split(",") if f.strip()]
WANT_TP_PIPS = float(env("WANT_TP_PIPS", "100"))   # الهدف الأول المفضّل (100 بيب = 10$) والثاني 150؛ بيتقصّر قبل منطقة قوية لكن مو أقل من MIN_TP_PIPS
TREND_PENALTY = float(env("TREND_PENALTY", "0"))   # خصم نقاط لصفقة عكس اتجاه الفريم الأكبر (0 = بس تحذير، مو منع)
ZONE_MIN_N = int(env("ZONE_MIN_N", "2"))           # أقل عدد لمسات لتعتبر المنطقة مهمة
ENGINE = env("ENGINE", "all")                   # zones = كسر منطقة (تجميع/دعم/مقاومة) + إعادة اختبار؛ all = مع الاستراتيجيات القديمة كمان
SESSION_START = int(env("SESSION_START", "7"))  # بداية التداول UTC (افتتاح لندن)
SESSION_END = int(env("SESSION_END", "20"))     # نهاية التداول UTC (المساء)
NEAR_ATR15 = float(env("NEAR_ATR15", "10"))      # أبعد منطقة مقبولة = هالعدد × تذبذب M15 (حوالي 25-30$)
MODE_AR = {"break": "مع الكسر مباشرة", "retest": "بعد إعادة اختبار مؤكدة", "fakeout": "ارتداد بعد كسر وهمي", "forming": "نموذج قيد التشكل (قبل كسر العنق)"}
FRAME_EXTRA = {"1min": 0, "5min": 0, "15min": 0}         # نقاط زيادة مطلوبة على الفريمات الصغيرة (ضجيج أكتر)
STOP_ATR_FRAME = float(env("STOP_ATR_FRAME", "1.3"))  # أدنى ستوب = هالعدد × تذبذب الفريم (ATR)
STOP_ATR_M15 = float(env("STOP_ATR_M15", "1.0"))      # وأدنى ستوب = هالعدد × تذبذب M15 (حتى صفقات M1/M5 ما ينضربوا بضجيج)
SPREAD_PIPS = float(env("SPREAD_PIPS", "3"))          # سبريد تقريبي بيضيفو للستوب خلف المستويات
TFN = {"1min": "M1", "5min": "M5", "15min": "M15", "30min": "M30"}
MAX_PER_DAY = int(env("MAX_PER_DAY", "4"))     # أقصى عدد إشارات باليوم (0 = بدون حد)
MAX_LOSSES = int(env("MAX_LOSSES", "3"))       # بعد هالعدد من الخسائر بيوقف لباقي اليوم (0 = بدون حد)
COOLDOWN_MIN = int(env("COOLDOWN_MIN", "15"))   # أقل فاصل (دقايق) بين إشارتين لنفس الأداة
EXPIRE_HOURS = float(env("EXPIRE_HOURS", "4"))  # بدون نتيجة بعد هالمدة بتنسكّر الصفقة
HEARTBEAT_HOURS = float(env("HEARTBEAT_HOURS", "4"))  # إذا ما وصلت رسائل، بيبعت "البوت شغال" مع قراءة السوق
PIPS = {"XAU/USD": 0.1, "GBP/JPY": 0.01, "USD/JPY": 0.01, "EUR/USD": 0.0001,
        "GBP/USD": 0.0001, "BTC/USD": 1.0, "NDX": 1.0}
EXCELLENT_SCORE = float(env("EXCELLENT_SCORE", "9"))  # تقييم ممتازة
GOOD_SCORE = float(env("GOOD_SCORE", "7"))            # تقييم جيدة
MEDIUM_SCORE = float(env("MEDIUM_SCORE", "5"))        # تقييم متوسطة (وأقل منها ضعيفة بحجم صغير جداً)
LOOKBACK = {"1min": 8, "5min": 3, "15min": 2}          # كم شمعة لورا بيفحص كل تشغيل (حتى ما تفوت إشارة بين تشغيلين)

# ---------------- محرك الانعكاس (نفس منطق المؤشر) ----------------
REV_ON = env("REV_ON", "1") == "1"                     # تشغيل/إيقاف محرك الانعكاس كله
REV_WICK = float(env("REV_WICK", "0.40"))              # أقل نسبة ذيل لمدى الشمعة
REV_RANGE_ATR = float(env("REV_RANGE_ATR", "0.8"))     # أقل مدى شمعة (x ATR)
REV_SPIKE_ATR = float(env("REV_SPIKE_ATR", "1.5"))     # شمعة ضخمة (x ATR)
REV_SWEEP = env("REV_SWEEP", "0") == "1"               # اشتراط سحب قمة/قاع سابق (الشموع الضخمة معفاة)
REV_SWEEP_LEN = int(env("REV_SWEEP_LEN", "10"))
REV_EXT = env("REV_EXT", "1") == "1"                   # بس عند أعلى/أدنى سعر بآخر N شمعة
REV_EXT_LEN = int(env("REV_EXT_LEN", "20"))
REV_FAIL = env("REV_FAIL", "1") == "1"                 # شمعة ضخمة ثم شمعة عكسية تسكر جوا جسمها
REV_GAP = int(env("REV_GAP", "8"))                     # أقل فاصل (شموع M30) بين إشارتين بنفس الاتجاه والنوع
REV_SKIP = env("REV_SKIP", "1") == "1"                 # SWING ما بيكرر قمة غطّاها REV/EARLY
ES_ON = env("ES_ON", "1") == "1"                       # الإشارة المبكرة (Early Swing)
ES_BODY = float(env("ES_BODY", "0.50"))                # أقل جسم للشمعة العكسية
ES_BREAK = env("ES_BREAK", "0") == "1"                 # اشتراط إغلاق خلف قاع/قمة شمعة القمة
SW_ON = env("SW_ON", "1") == "1"                       # Swing المؤكد
SW_L = int(env("SW_L", "4"))
SW_R = int(env("SW_R", "1"))                           # شمعة تأكيد بعد القمة (1 = الأسرع)
SW_MIN_ATR = float(env("SW_MIN_ATR", "2.5"))           # أقل حجم حركة (x ATR)
SW_LOOK = int(env("SW_LOOK", "20"))
REV_MAX_SL_PIPS = float(env("REV_MAX_SL_PIPS", "150")) # أقصى ستوب (150 بيب = 15$)
REV_CHASE_ATR = float(env("REV_CHASE_ATR", "1.0"))     # إذا السعر ابتعد أكتر من هيك بنتجاهل الإشارة (فاتت)
REV_EXPIRE_HOURS = float(env("REV_EXPIRE_HOURS", "8"))
BAR_SEC = 1800                                          # طول شمعة M30 بالثواني

EXPIRE_BY_TF = {"1min": EXPIRE_HOURS * 0.4, "5min": EXPIRE_HOURS * 0.6, "15min": EXPIRE_HOURS, "30min": REV_EXPIRE_HOURS}
STATE_FILE = "state.json"


def prep(df):
    d = df.copy()
    d["EMA50"] = d["Close"].ewm(span=50, adjust=False).mean()
    d["EMA200"] = d["Close"].ewm(span=200, adjust=False).mean()
    ch = d["Close"].diff()
    up = ch.clip(lower=0).ewm(alpha=1 / 14, adjust=False).mean()
    dn = (-ch.clip(upper=0)).ewm(alpha=1 / 14, adjust=False).mean()
    d["RSI"] = 100 - 100 / (1 + up / dn)
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


# ---------------- الاستراتيجيات ----------------
DIAG = {}
DIAG_LABELS = {
    "zones": "مناطق قوية قريبة (مرات الفحص)", "breaks": "كسور مكتشفة", "break_weak": "كسر بشمعة ضعيفة (مرفوض)",
    "break_entry_weak": "كسر مباشر بشمعة مو قوية كفاية أو بعيد", "rt_no_followthrough": "إعادة اختبار: السعر ما ابتعد بعد الكسر",
    "rt_level_lost": "إعادة اختبار: السعر رجع عكس المستوى", "rt_no_touch": "إعادة اختبار: ما لمس المستوى",
    "rt_no_rejection": "إعادة اختبار: ما في شمعة رفض", "rt_too_far": "إعادة اختبار: السعر بعيد عن المستوى",
    "rt_violent_pullback": "إعادة اختبار: الرجعة عنيفة", "setups": "إشارات مرشحة (كل العائلات)",
    "fakeouts": "كسر وهمي مكتشف (صفقة ارتداد)", "rt_strong_opposite": "إعادة اختبار: شمعة معاكسة قوية بعد الكسر (كسر وهمي محتمل)",
    "news_spike": "شمعة ضخمة (خبر): ما بندخل معها", "news_spike_wait": "شمعة ضخمة: ننتظر تثبيت 3 شموع",
    "dbl_forming": "قاع/قمة مزدوجة قيد التشكل (مكتشفة)", "news_candle_wait": "شمعة ضخمة مثل الخبر: البوت ينتظر",
    "low_score": "مرفوض بسبب النقاط", "chase": "مرفوض: السعر ابتعد (مطاردة)", "zone_used": "منطقة مستخدمة اليوم",
}


def dg(key, n=1):
    DIAG[key] = DIAG.get(key, 0) + n


def diag_text(d):
    items = sorted(d.items(), key=lambda kv: -kv[1])[:10]
    return "\n".join(f"- {DIAG_LABELS.get(k, k.replace('build:', 'مرفوض بالبناء: '))}: {v}" for k, v in items)


def find_zones(d, price, atr, levels, near):
    """مناطق مهمة وقريبة بس: تجميع (صناديق) حديث، دعم/مقاومة متكررة، قمة/قاع أمس ورينج آسيا"""
    n = len(d)
    hi, lo = d["High"].values, d["Low"].values
    zs = []
    for W in (14, 24, 36):
        for gap in range(2, 14, 3):
            e = n - gap
            if e - W < 5:
                continue
            bh, bl = float(hi[e - W:e].max()), float(lo[e - W:e].min())
            if not (0.8 * atr <= bh - bl <= 3.2 * atr):
                continue
            t_hi, t_lo = int((hi[e - W:e] >= bh - 0.2 * atr).sum()), int((lo[e - W:e] <= bl + 0.2 * atr).sum())
            if t_hi >= 2 and t_lo >= 2:
                zs.append({"lo": bl, "hi": bh, "kind": f"تجميع {W} شمعة", "n": t_hi + t_lo})
    for z in sr_zones(d.iloc[-250:], atr):
        zs.append({"lo": z["lo"], "hi": z["hi"], "kind": f"دعم/مقاومة ({z['n']} لمسات)", "n": z["n"]})
    for nm, v in (levels or {}).get("keys", {}).items():
        zs.append({"lo": float(v), "hi": float(v), "kind": nm, "n": 3})
    zs = [z for z in zs if abs((z["lo"] + z["hi"]) / 2 - price) <= near]
    zs.sort(key=lambda z: -z["n"])
    out = []
    for z in zs:
        mid = (z["lo"] + z["hi"]) / 2
        if all(abs(mid - (o["lo"] + o["hi"]) / 2) > 0.5 * atr for o in out):
            out.append(z)
    return out


def breakout_engine(d, price, atr, zones, a15=0.0):
    """كسر منطقة بشمعة قوية: إما دخول مع الكسر (شمعة الكسر هي آخر شمعة)، أو إعادة اختبار مؤكدة (رجعة هادية + رفض + المستوى صامد)"""
    out = []
    n = len(d)
    if n < 60:
        return out
    hi, lo, cl, op = (d[c].values for c in ("High", "Low", "Close", "Open"))
    rng = hi - lo
    avg_rng = float(rng[-41:-1].mean()) or 1e-9
    g = lambda x: f"{x:.5g}"
    tol = max(0.15 * atr, 0.08 * a15)   # سماحية بالسعر (مهمة بفريم الدقيقة حيث ATR صغير جداً)
    used = set()
    dg("zones", sum(1 for z in zones if z["n"] >= ZONE_MIN_N))
    for z in zones:
        if z["n"] < ZONE_MIN_N:
            continue
        for dr in (1, -1):
            lv = z["hi"] if dr == 1 else z["lo"]
            if (round(lv, 1), dr) in used:
                continue
            for k in range(max(n - 14, 2), n):
                body = abs(cl[k] - op[k])
                pos = (cl[k] - lo[k]) / (rng[k] or 1e-9)
                if dr == 1:
                    broke = cl[k] > lv + 0.15 * atr and cl[k - 1] <= lv + 0.05 * atr and pos >= 0.6
                else:
                    broke = cl[k] < lv - 0.15 * atr and cl[k - 1] >= lv - 0.05 * atr and pos <= 0.4
                if not broke:
                    continue
                dg("breaks")
                if body < 0.4 * atr or rng[k] < 0.8 * avg_rng:
                    dg("break_weak")
                    continue
                comp = float(rng[max(0, k - 12):k].mean()) <= 0.85 * avg_rng
                strong = 1 if z["n"] >= 5 else 0
                side = "أعلى" if dr == 1 else "أسفل"
                spike = rng[k] >= 3.0 * avg_rng   # شمعة ضخمة (شبه خبر): غالباً بترجع، ما بندخل معها ولا بنعتمد عليها بدون تأكيد
                if k == n - 1:
                    if abs(price - lv) <= 2.6 * atr and body >= 0.7 * atr and rng[k] >= 1.0 * avg_rng and not spike:
                        out.append({"name": "Breakout قوي", "dir": dr, "sl": float(lv - dr * 0.5 * atr),
                                    "score": 4 + (1 if comp else 0) + strong, "lvl": float(lv), "mode": "break",
                                    "why": f"كسر {z['kind']} {side} {g(lv)} بشمعة قوية" + (" بعد تجميع" if comp else "")})
                        used.add((round(lv, 1), dr))
                    else:
                        dg("news_spike" if spike else "break_entry_weak")
                elif k <= n - 3:
                    if dr == 1:
                        run = hi[k:n - 1].max() - lv
                        held = bool((cl[k + 1:] >= lv - tol).all())
                        touch = lo[-1] <= lv + 0.35 * atr and cl[-1] > lv
                        rej = cl[-1] > op[-1] or (min(cl[-1], op[-1]) - lo[-1]) >= abs(cl[-1] - op[-1])
                        close_ok = price - lv <= 1.5 * atr
                    else:
                        run = lv - lo[k:n - 1].min()
                        held = bool((cl[k + 1:] <= lv + tol).all())
                        touch = hi[-1] >= lv - 0.35 * atr and cl[-1] < lv
                        rej = cl[-1] < op[-1] or (hi[-1] - max(cl[-1], op[-1])) >= abs(cl[-1] - op[-1])
                        close_ok = lv - price <= 1.5 * atr
                    if dr == 1:
                        opp = bool(((op[k + 1:n - 1] - cl[k + 1:n - 1]) >= 1.0 * atr).any())   # شمعة هابطة قوية بعد الكسر = رفض/كسر وهمي
                    else:
                        opp = bool(((cl[k + 1:n - 1] - op[k + 1:n - 1]) >= 1.0 * atr).any())
                    acc = (not spike) or (n - 1 - k) >= 3   # شمعة الخبر لازم يتبعها تثبيت 3 شموع على الأقل
                    mid_b = abs(cl[k + 1:n - 1] - op[k + 1:n - 1])
                    quiet = len(mid_b) == 0 or float(mid_b.mean()) <= 1.0 * body   # الرجعة أضعف من شمعة الكسر = اختبار مش انعكاس
                    if run >= 0.5 * atr and held and touch and rej and close_ok and quiet and acc and not opp:
                        sl = (min(lo[-1], lv) - 0.3 * atr) if dr == 1 else (max(hi[-1], lv) + 0.3 * atr)
                        out.append({"name": "Break & Retest مؤكد", "dir": dr, "sl": float(sl),
                                    "score": 5 + (1 if comp else 0) + strong, "lvl": float(lv), "mode": "retest",
                                    "why": f"كسر {z['kind']} {side} {g(lv)} ثم إعادة اختباره برجعة هادية ورفض والمستوى صامد"})
                        used.add((round(lv, 1), dr))
                    else:
                        for ok_, nm_ in ((run >= 0.5 * atr, "rt_no_followthrough"), (held, "rt_level_lost"), (touch, "rt_no_touch"),
                                         (rej, "rt_no_rejection"), (close_ok, "rt_too_far"), (quiet, "rt_violent_pullback"),
                                         (not opp, "rt_strong_opposite"), (acc, "news_spike_wait")):
                            if not ok_:
                                dg(nm_)
                                break
                break
    return out


def fakeout_engine(d, price, atr, zones, a15=0.0):
    """كسر وهمي: السعر اخترق منطقة (حتى بشمعة خبر) ثم رجع وأغلق جواتها برفض قوي = صفقة ارتداد عكس الاختراق"""
    out = []
    n = len(d)
    if n < 40:
        return out
    hi, lo, cl, op = (d[c].values for c in ("High", "Low", "Close", "Open"))
    tol = max(0.2 * atr, 0.05 * a15)   # الاختراق لازم يكون ملحوظ (مو لمسة صغيرة)
    g = lambda x: f"{x:.5g}"
    for z in zones:
        if z["n"] < max(ZONE_MIN_N, 3):
            continue
        strong = 1 if z["n"] >= 5 else 0
        win = range(n - 3, n)
        # اختراق وهمي للأعلى (مقاومة) -> بيع
        lv = z["hi"]
        pierce = [j for j in win if hi[j] > lv + tol]
        if pierce:
            ext = max(hi[j] for j in pierce)
            wick_rej = any((hi[j] - max(cl[j], op[j])) >= max(abs(cl[j] - op[j]), 0.3 * atr) and cl[j] < lv for j in pierce)
            strong_back = cl[-1] < lv and cl[-1] < op[-1] and abs(cl[-1] - op[-1]) >= 0.5 * atr
            if cl[-1] < lv - 0.1 * atr and (wick_rej or strong_back) and lv - price <= 1.5 * atr:
                dg("fakeouts")
                out.append({"name": "كسر وهمي → ارتداد", "dir": -1, "sl": float(ext + 0.3 * atr), "score": 5 + strong,
                            "lvl": float(lv), "mode": "fakeout",
                            "why": f"اختراق وهمي فوق {z['kind']} {g(lv)} (القمة {g(ext)}) ثم رجوع تحت المنطقة برفض قوي"})
        # اختراق وهمي للأسفل (دعم) -> شراء
        lv = z["lo"]
        pierce = [j for j in win if lo[j] < lv - tol]
        if pierce:
            ext = min(lo[j] for j in pierce)
            wick_rej = any((min(cl[j], op[j]) - lo[j]) >= max(abs(cl[j] - op[j]), 0.3 * atr) and cl[j] > lv for j in pierce)
            strong_back = cl[-1] > lv and cl[-1] > op[-1] and abs(cl[-1] - op[-1]) >= 0.5 * atr
            if cl[-1] > lv + 0.1 * atr and (wick_rej or strong_back) and price - lv <= 1.5 * atr:
                dg("fakeouts")
                out.append({"name": "كسر وهمي → ارتداد", "dir": 1, "sl": float(ext - 0.3 * atr), "score": 5 + strong,
                            "lvl": float(lv), "mode": "fakeout",
                            "why": f"اختراق وهمي تحت {z['kind']} {g(lv)} (القاع {g(ext)}) ثم رجوع فوق المنطقة برفض قوي"})
    return out


def double_forming(d, price, atr, a15=0.0):
    """قاع/قمة مزدوجة وهي عم تتشكل: لمسة تانية قرب الأولى + شمعة رفض، بدون انتظار كسر العنق"""
    out = []
    n = len(d)
    if n < 60:
        return out
    hi, lo, cl, op = (d[c].values for c in ("High", "Low", "Close", "Open"))
    H, L = swings(d, 2)
    tol = max(0.35 * atr, 0.04 * a15)
    g = lambda x: f"{x:.5g}"
    rng_last = (hi[-1] - lo[-1]) or 1e-9
    body = abs(cl[-1] - op[-1])
    # قاع مزدوج -> شراء
    w = lo[-4:]
    cands = list(L[-3:]) + [(n - 4 + int(w.argmin()), float(w.min()))]
    best = None
    for ia, pa in cands:
        for ib, pb in cands:
            if 6 <= ib - ia <= 80 and abs(pa - pb) <= tol and n - 1 - ib <= 8:
                best = (ia, pa, ib, pb)
    if best:
        ia, pa, ib, pb = best
        ref = min(pa, pb)
        neck = float(hi[ia:ib + 1].max())
        rej = cl[-1] > op[-1] and ((cl[-1] - lo[-1]) / rng_last >= 0.6 or (min(cl[-1], op[-1]) - lo[-1]) >= body)
        if neck - ref >= 1.5 * atr and price - ref <= 2.2 * atr and rej:
            dg("dbl_forming")
            out.append({"name": "قاع مزدوج (قيد التشكل)", "dir": 1, "sl": float(ref - 0.3 * atr), "score": 5,
                        "lvl": float(ref), "mode": "forming",
                        "why": f"لمستين عند ~{g(ref)} مع شمعة رفض صاعدة قبل كسر العنق ({g(neck)})"})
    # قمة مزدوجة -> بيع
    w = hi[-4:]
    cands = list(H[-3:]) + [(n - 4 + int(w.argmax()), float(w.max()))]
    best = None
    for ia, pa in cands:
        for ib, pb in cands:
            if 6 <= ib - ia <= 80 and abs(pa - pb) <= tol and n - 1 - ib <= 8:
                best = (ia, pa, ib, pb)
    if best:
        ia, pa, ib, pb = best
        ref = max(pa, pb)
        neck = float(lo[ia:ib + 1].min())
        rej = cl[-1] < op[-1] and ((hi[-1] - cl[-1]) / rng_last >= 0.6 or (hi[-1] - max(cl[-1], op[-1])) >= body)
        if ref - neck >= 1.5 * atr and ref - price <= 2.2 * atr and rej:
            dg("dbl_forming")
            out.append({"name": "قمة مزدوجة (قيد التشكل)", "dir": -1, "sl": float(ref + 0.3 * atr), "score": 5,
                        "lvl": float(ref), "mode": "forming",
                        "why": f"لمستين عند ~{g(ref)} مع شمعة رفض هابطة قبل كسر العنق ({g(neck)})"})
    return out


def scan(df, b4, b1d, crypto, levels=None, tf="5min", a15=0.0):
    """عائلات الصفقات: نماذج فنية، كسر وإعادة اختبار حقيقي، ارتداد من دعم/مقاومة، تجميع ثم انفجار، سحب سيولة"""
    d = prep(df)
    last = d.iloc[-1]
    price, atr, rsi = float(last["Close"]), float(last["ATR"]), float(last["RSI"])
    H, L = swings(d)
    zones = sr_zones(d.iloc[-250:], atr)
    bull, bear = candle(d)
    tr = trend(d)
    out = []

    def add(name, dr, sl, base, why):
        out.append({"name": name, "dir": dr, "sl": float(sl), "score": base, "why": why})

    near = NEAR_ATR15 * a15 if a15 else 25 * atr
    zl = find_zones(d, price, atr, levels, near)
    out += breakout_engine(d, price, atr, zl, a15)
    out += fakeout_engine(d, price, atr, zl, a15)
    out += double_forming(d, price, atr, a15)
    if ENGINE != "zones":
        # 1) ارتداد من دعم/مقاومة (منطقة لُمست مرتين أو أكتر) مع شمعة رفض
        for z in zones:
            base = 2 + (1 if z["n"] >= 3 else 0)
            if bull and z["lo"] - 0.3 * atr <= price <= z["hi"] + 0.5 * atr:
                add("ارتداد من دعم", 1, z["lo"] - 0.3 * atr, base, f"دعم لمس {z['n']} مرات + شمعة رفض صاعدة")
            if bear and z["lo"] - 0.5 * atr <= price <= z["hi"] + 0.3 * atr:
                add("ارتداد من مقاومة", -1, z["hi"] + 0.3 * atr, base, f"مقاومة لمست {z['n']} مرات + شمعة رفض هابطة")

        # 2) كسر وإعادة اختبار حقيقي
        out += break_retest(d, price, atr, H, L)

        # 3) نماذج فنية، تجميع ثم انفجار، سحب سيولة + CHOCH
        out += extra_setups(d, price, atr, H, L, tr)
        if levels:
            out += level_setups(d, price, atr, levels)

    _r = (d["High"] - d["Low"]).values
    if _r[-1] >= 3.0 * (float(_r[-41:-1].mean()) or 1e-9):   # آخر شمعة ضخمة (مثل الخبر): ننتظر ما بندخل معها
        dg("news_candle_wait")
        out = [x for x in out if x.get("mode") in ("fakeout", "retest")]

    # ---- التقييم (Confluence) ----
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
                s["score"] -= TREND_PENALTY
                warn.append(f"ارتداد عكس اتجاه {label}: صفقة أقصر عمراً، صغّر الحجم")
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
    # لا تشتري تحت مقاومة قوية قريبة ولا تبيع فوق دعم قوي قريب
    for s in out:
        for z in zones:
            if z["n"] < 3:
                continue
            if s["dir"] == 1 and z["lo"] > price and z["lo"] - price <= 0.7 * atr:
                s["score"] -= 2
                s["warn"].append("مقاومة قوية قريبة فوق السعر (خُصمت نقطتين)")
                break
            if s["dir"] == -1 and z["hi"] < price and price - z["hi"] <= 0.7 * atr:
                s["score"] -= 2
                s["warn"].append("دعم قوي قريب تحت السعر (خُصمت نقطتين)")
                break
    ctx = {"price": price, "atr": atr, "rsi": rsi, "trend": tr, "zones": zones, "swings": [p_ for _, p_ in H] + [p_ for _, p_ in L], "zl": zl}
    return out, ctx


def build(s, price, atr, pip, rr, min_tp=0, blockers=(), min_risk=0.0, struct=(), buf=0.0, want_tp=0.0):
    """الستوب بيتحدد من السوق: نقطة إبطال النمط/المنطقة، ثم أدنى حد حسب تذبذب السوق الحالي، ثم خلف أقرب مستوى سيولة"""
    dr, sl = s["dir"], s["sl"]
    if (dr == 1 and sl >= price) or (dr == -1 and sl <= price):
        return None, "ستوب بالاتجاه الغلط"
    atr = atr or 1e-9
    notes = ["خلف نقطة إبطال النمط/المنطقة"]
    floor = max(0.25 * atr, min_risk)
    risk = abs(price - sl)
    if risk < floor:
        sl, risk = price - dr * floor, floor
        notes = [f"وُسّع لـ {floor / atr:.1f}×تذبذب الفريم ({floor / pip:.0f} بيب) حتى ما ينضرب بالتذبذب الطبيعي"]
    for lv in struct:   # الستوب ما بيقعد على مغناطيس سيولة (قمة/قاع/منطقة): بنحطو ورا المستوى
        if dr == 1 and sl - 0.25 * atr <= lv <= sl + 0.25 * atr:
            sl = min(sl, lv - buf)
            notes.append(f"نُقل خلف مستوى سيولة {lv:.5g}")
            break
        if dr == -1 and sl - 0.25 * atr <= lv <= sl + 0.25 * atr:
            sl = max(sl, lv + buf)
            notes.append(f"نُقل خلف مستوى سيولة {lv:.5g}")
            break
    risk = abs(price - sl)
    if risk > max(4 * atr, 2.5 * floor):
        return None, "الستوب المنطقي بعيد"
    dist = max(rr * risk, (want_tp or min_tp or 0) * pip)
    tp1 = price + dr * dist
    trim = None
    for lv in blockers:   # منطقة قوية قدام الهدف: إما نرفض الصفقة أو نقصّر الهدف قبلها
        d0 = (lv - price) if dr == 1 else (price - lv)
        if d0 <= 0:
            continue
        if 0.05 * dist < d0 < 0.35 * dist:
            return None, "عائق قبل الهدف"
        if 0.35 * dist <= d0 < 1.05 * dist:
            trim = d0 - 0.15 * atr if trim is None else min(trim, d0 - 0.15 * atr)
    if trim is not None:
        if trim < max(1.0 * risk, (min_tp or 0) * pip):
            return None, "هدف قصير قبل منطقة"
        dist = trim
        tp1 = price + dr * dist
    return {"entry": price, "sl": sl, "tp1": tp1, "tp2": price + dr * dist * 1.5,
            "risk_p": risk / pip, "tp1_p": dist / pip, "tp2_p": dist * 1.5 / pip,
            "sl_note": " | ".join(notes)}, ""


def fmt(x, pip):
    return f"{x:.2f}" if pip >= 0.1 else f"{x:.5f}" if pip < 0.01 else f"{x:.3f}"


# ---------------- استراتيجيات إضافية: Sweep+CHOCH، Range Breakout، نماذج فنية ----------------
def extra_setups(d, price, atr, H, L, tr):
    out = []
    n = len(d)
    hi, lo, cl, op = (d[c].values for c in ("High", "Low", "Close", "Open"))

    def add(name, dr, sl, base, why):
        out.append({"name": name, "dir": dr, "sl": float(sl), "score": base, "why": why})

    g = lambda x: f"{x:.5g}"

    # 1) Liquidity Sweep + CHOCH: سحب سيولة ثم كسر هيكل مصغّر عكسي (خلال آخر 3 شموع)
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

    # 2) تجميع (ضغط تذبذب) ثم انفجار بشمعة قوية
    W = 14
    if n > 80:
        rh, rl = hi[n - W - 1:n - 1].max(), lo[n - W - 1:n - 1].min()
        a_ref = float(d["ATR"].iloc[n - 70:n - W - 1].mean())
        a_win = float(d["ATR"].iloc[n - W - 1:n - 1].mean())
        body, rng_last = abs(cl[-1] - op[-1]), (hi[-1] - lo[-1]) or 1e-9
        if a_win <= 0.8 * a_ref and (rh - rl) <= 2.5 * a_ref and body >= 1.2 * a_win and body / rng_last >= 0.6:
            mid = (rh + rl) / 2
            if cl[-1] > rh + 0.1 * a_ref and cl[-2] <= rh:
                add("Accumulation Breakout صاعد", 1, mid - 0.2 * a_ref, 4,
                    f"تجميع ضيق [{g(rl)}-{g(rh)}] ثم انفجار صاعد بشمعة قوية")
            if cl[-1] < rl - 0.1 * a_ref and cl[-2] >= rl:
                add("Accumulation Breakout هابط", -1, mid + 0.2 * a_ref, 4,
                    f"تجميع ضيق [{g(rl)}-{g(rh)}] ثم انفجار هابط بشمعة قوية")

    # 4) قمة/قاع مزدوج
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

    # 5) رأس وكتفين / مقلوب
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

    # 6) مثلثات وأقنية سعرية (خطوط اتجاه من آخر قمتين وقاعين)
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
                if cl[-1] < lw(n - 1) - 0.1 * atr and cl[-2] >= lw(n - 2) + 0.0 - 0.1 * atr:
                    add(f"كسر {nm} لأسفل", -1, mid + 0.2 * atr, base, f"كسر خط {nm} السفلي")

    # 7) وتد صاعد / هابط (Wedge)
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

    # 8) قمم / قيعان ثلاثية
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

    # 9) علم / راية (Flag / Pennant)
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


# ---------------- إدارة الصفقة: انعكاس قوي فقط ----------------
def reversal_reasons(fr, t, live):
    """أسباب انعكاس قوي ضد الصفقة. قائمة فارغة = ما في انعكاس قوي (الإشارة الضعيفة لا تكفي)"""
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


def break_retest(d, price, atr, H, L):
    """كسر حقيقي (إغلاق قوي + ابتعاد السعر) ثم عودة لاختبار المستوى بشمعة رفض. الكسر الوهمي (رجوع للداخل) مرفوض."""
    out = []
    n = len(d)
    if n < 60:
        return out
    hi, lo, cl, op = (d[c].values for c in ("High", "Low", "Close", "Open"))
    g = lambda x: f"{x:.5g}"
    body = abs(cl[-1] - op[-1])
    done = set()
    for i, lv in reversed(H[-6:]):
        if 1 in done or i >= n - 8:
            continue
        for k in range(max(i + 1, n - 16), n - 2):
            if cl[k] > lv + 0.15 * atr and cl[k - 1] <= lv + 0.05 * atr and abs(cl[k] - op[k]) >= 0.4 * atr:
                if (cl[k + 1:] >= lv - 0.15 * atr).all() and hi[k:n - 1].max() - lv >= 0.6 * atr:
                    rej = cl[-1] > op[-1] or (min(cl[-1], op[-1]) - lo[-1]) >= body
                    if lo[-1] <= lv + 0.35 * atr and cl[-1] > lv and rej and price - lv <= 0.8 * atr:
                        out.append({"name": "Break & Retest صاعد", "dir": 1, "sl": float(min(lo[-1], lv) - 0.3 * atr),
                                    "score": 4, "why": f"كسر حقيقي فوق {g(lv)} وعودة لاختباره برفض صاعد"})
                        done.add(1)
                break
    for i, lv in reversed(L[-6:]):
        if 2 in done or i >= n - 8:
            continue
        for k in range(max(i + 1, n - 16), n - 2):
            if cl[k] < lv - 0.15 * atr and cl[k - 1] >= lv - 0.05 * atr and abs(cl[k] - op[k]) >= 0.4 * atr:
                if (cl[k + 1:] <= lv + 0.15 * atr).all() and lv - lo[k:n - 1].min() >= 0.6 * atr:
                    rej = cl[-1] < op[-1] or (hi[-1] - max(cl[-1], op[-1])) >= body
                    if hi[-1] >= lv - 0.35 * atr and cl[-1] < lv and rej and lv - price <= 0.8 * atr:
                        out.append({"name": "Break & Retest هابط", "dir": -1, "sl": float(max(hi[-1], lv) + 0.3 * atr),
                                    "score": 4, "why": f"كسر حقيقي تحت {g(lv)} وعودة لاختباره برفض هابط"})
                        done.add(2)
                break
    return out


# ---------------- سياق الذهب: جلسات، مستويات مهمة، تقييم الصفقة ----------------
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
    """قمة/قاع أمس، رينج آسيا، افتتاح اليوم، وأرقام دائرية قريبة"""
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
    """سحب سيولة ورفض عند المستويات المهمة (قمة/قاع أمس، رينج آسيا)"""
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
    if score >= MEDIUM_SCORE:
        return "متوسطة ⭐", "متوسطة", 0.25
    return "ضعيفة ⚠️", "ضعيفة", 0.15


def market_read(px, fr, lv, hour):
    tr = lambda t: "صاعد" if t > 0 else "هابط" if t < 0 else "عرضي"
    b_h1, b_h4 = fr["15min"]["b"]
    lines = [f"الجلسة: {session_name(hour)}", f"السعر: {px:.5g}  | افتتاح اليوم: {lv['open']:.5g}",
             f"اتجاه H4: {tr(b_h4)} | H1: {tr(b_h1)}"]
    near = sorted((abs(px - v), nm, v) for nm, v in lv["keys"].items())[:3]
    if near:
        lines.append("أقرب مستويات: " + " | ".join(f"{nm} {v:.5g}" for _, nm, v in near))
    b = b_h4 or b_h1
    side = "صاعد" if b > 0 else "هابط" if b < 0 else "عرضي"
    lines.append(f"خطتي: الاتجاه الأكبر {side}. بدور على صفقات مع الاتجاه، وكمان ارتدادات قصيرة عكسو (تنبيه + حجم أصغر) على الفريمات الصغيرة، "
                 "مع كسر وإعادة اختبار ونماذج وسحب سيولة عند المناطق القريبة.")
    return "\n".join(lines)


# ---------------- محرك الانعكاس M30: REV + EARLY + SWING (نفس منطق المؤشر) ----------------
def rev_plan(dr, px, a, live, sig_close, pip):
    """خطة الدخول: بسعر السوق، الستوب خلف القمة/القاع، الأهداف ثابتة"""
    if dr * (live - sig_close) > REV_CHASE_ATR * a:
        return None   # السعر ابتعد (فاتت الإشارة)
    sl = px - dr * (0.25 * a + SPREAD_PIPS * pip)
    if (dr == 1 and sl >= live) or (dr == -1 and sl <= live):
        return None   # السعر صار خلف الستوب
    risk = abs(live - sl)
    cap = REV_MAX_SL_PIPS * pip
    if risk > cap:
        risk, sl = cap, live - dr * cap
    if risk < 0.5 * a:
        risk, sl = 0.5 * a, live - dr * 0.5 * a
    d1 = max(WANT_TP_PIPS, MIN_TP_PIPS) * pip
    return {"entry": live, "sl": sl, "tp1": live + dr * d1, "tp2": live + dr * d1 * 1.5,
            "risk_p": risk / pip, "tp1_p": d1 / pip}


def rev_signals(d, sym, st, live, pip):
    """بيفحص شموع M30 المكتملة الجديدة بس. أولوية: REV > EARLY > SWING (إشارة وحدة لكل شمعة)"""
    n = len(d)
    out = []
    warm = max(REV_EXT_LEN, SW_LOOK, REV_SWEEP_LEN, SW_L + SW_R) + 3
    if n < warm + 10:
        return out
    done = st["rv_done"].get(sym)
    st["rv_done"][sym] = d.index[-1].isoformat()
    if done:
        dt = pd.Timestamp(done)
        idxs = [i for i in range(max(n - 4, warm), n) if d.index[i] > dt]
    else:
        idxs = [n - 1]   # أول تشغيل: آخر شمعة بس (بدون إغراق بإشارات قديمة)
    if not idxs:
        return out

    hi, lo, cl, op = (d[c].values for c in ("High", "Low", "Close", "Open"))
    atr = prep(d)["ATR"].values
    hN = d["High"].rolling(REV_EXT_LEN).max().values
    lN = d["Low"].rolling(REV_EXT_LEN).min().values
    hP = d["High"].rolling(REV_SWEEP_LEN).max().shift(1).values
    lP = d["Low"].rolling(REV_SWEEP_LEN).min().shift(1).values
    sH = d["High"].rolling(SW_LOOK).max().values
    sL = d["Low"].rolling(SW_LOOK).min().values

    def last_of(typ, dr):
        return st["rv_last"].get(f"{sym}|{typ}|{dr}")

    def gap_ok(typ, dr, ti, px, a):
        L = last_of(typ, dr)
        if not L:
            return True
        bars = (ti - pd.Timestamp(L["t"])).total_seconds() / BAR_SEC
        return bars >= REV_GAP or (dr == -1 and px > L["px"] + 0.5 * a) or (dr == 1 and px < L["px"] - 0.5 * a)

    def covered(typ, dr, ti, within):
        L = last_of(typ, dr)
        return bool(L) and 0 <= (ti - pd.Timestamp(L["t"])).total_seconds() / BAR_SEC <= within

    for i in idxs:
        a = float(atr[i])
        if a != a or a <= 0:
            continue
        ti = d.index[i]
        r = (hi[i] - lo[i]) or 1e-9
        upr = (hi[i] - max(cl[i], op[i])) / r
        lor = (min(cl[i], op[i]) - lo[i]) / r
        mid1 = (op[i - 1] + cl[i - 1]) / 2
        sel = None   # (نوع، اتجاه، أقصى سعر)

        # ---- REV: ذيل طويل عند أعلى/أدنى سعر + شمعة ضخمة ثم انعكاس ----
        if REV_ON:
            big = r >= a * REV_RANGE_ATR
            spike = r >= a * REV_SPIKE_ATR
            wS = big and upr >= REV_WICK and (not REV_SWEEP or (hi[i] > hP[i] and cl[i] < hP[i]) or spike) and (not REV_EXT or hi[i] >= hN[i])
            wB = big and lor >= REV_WICK and (not REV_SWEEP or (lo[i] < lP[i] and cl[i] > lP[i]) or spike) and (not REV_EXT or lo[i] <= lN[i])
            fS = REV_FAIL and hi[i - 1] >= hN[i - 1] and (hi[i - 1] - lo[i - 1]) >= a * REV_SPIKE_ATR and cl[i] < op[i] and cl[i] < mid1
            fB = REV_FAIL and lo[i - 1] <= lN[i - 1] and (hi[i - 1] - lo[i - 1]) >= a * REV_SPIKE_ATR and cl[i] > op[i] and cl[i] > mid1
            px_s = hi[i] if wS else max(hi[i], hi[i - 1])
            px_b = lo[i] if wB else min(lo[i], lo[i - 1])
            s_ok = (wS or fS) and gap_ok("REV", -1, ti, px_s, a)
            b_ok = (wB or fB) and gap_ok("REV", 1, ti, px_b, a)
            if b_ok and s_ok:
                sel = ("REV", 1 if lor >= upr else -1, px_b if lor >= upr else px_s)
            elif b_ok:
                sel = ("REV", 1, px_b)
            elif s_ok:
                sel = ("REV", -1, px_s)

        # ---- EARLY: أول شمعة عكسية قوية بعد القمة/القاع ----
        if not sel and ES_ON:
            body = abs(cl[i] - op[i]) / r
            eS = hi[i - 1] >= sH[i - 1] and hi[i - 1] - sL[i - 1] >= a * SW_MIN_ATR and cl[i] < op[i] and cl[i] < mid1 and body >= ES_BODY and (not ES_BREAK or cl[i] < lo[i - 1])
            eB = lo[i - 1] <= sL[i - 1] and sH[i - 1] - lo[i - 1] >= a * SW_MIN_ATR and cl[i] > op[i] and cl[i] > mid1 and body >= ES_BODY and (not ES_BREAK or cl[i] > hi[i - 1])
            pxs, pxb = max(hi[i], hi[i - 1]), min(lo[i], lo[i - 1])
            eS = eS and not (REV_SKIP and covered("REV", -1, ti, 3)) and gap_ok("EARLY", -1, ti, pxs, a)
            eB = eB and not (REV_SKIP and covered("REV", 1, ti, 3)) and gap_ok("EARLY", 1, ti, pxb, a)
            if eS:
                sel = ("EARLY", -1, pxs)
            elif eB:
                sel = ("EARLY", 1, pxb)

        # ---- SWING: قمة/قاع مؤكد (بيتحدّث دايماً، وبيطلع إشارة إذا ما غطّاه REV/EARLY) ----
        if SW_ON:
            p = i - SW_R
            if p - SW_L >= 0:
                S = st["rv_sw"].setdefault(sym, {"type": 0, "px": None})
                sw = None
                if hi[p] >= hi[p - SW_L:p].max() and hi[p] > hi[p + 1:i + 1].max():
                    if hi[p] - sL[i] >= a * SW_MIN_ATR and (S["type"] != 1 or S["px"] is None or hi[p] > S["px"] + 0.5 * a):
                        if not (REV_SKIP and (covered("REV", -1, ti, SW_R + 2) or covered("EARLY", -1, ti, SW_R + 2))):
                            sw = ("SWING", -1, float(hi[p]))
                        S["type"], S["px"] = 1, float(hi[p])
                if lo[p] <= lo[p - SW_L:p].min() and lo[p] < lo[p + 1:i + 1].min():
                    if sH[i] - lo[p] >= a * SW_MIN_ATR and (S["type"] != -1 or S["px"] is None or lo[p] < S["px"] - 0.5 * a):
                        if not sw and not (REV_SKIP and (covered("REV", 1, ti, SW_R + 2) or covered("EARLY", 1, ti, SW_R + 2))):
                            sw = ("SWING", 1, float(lo[p]))
                        S["type"], S["px"] = -1, float(lo[p])
                if not sel and sw:
                    sel = sw

        if sel:
            typ, dr, px = sel
            plan = rev_plan(dr, float(px), a, live, float(cl[i]), pip)
            if plan:
                st["rv_last"][f"{sym}|{typ}|{dr}"] = {"t": ti.isoformat(), "px": float(px)}
                out.append({"type": typ, "dir": dr, **plan})
    return out


def close_rev(st, o, live, rday):
    """خروج على إشارة معاكسة"""
    pip = PIPS.get(o["sym"], 0.0001)
    side = "شراء" if o["dir"] == 1 else "بيع"
    risk = abs(o["entry"] - o["sl"]) or 1e-9
    R = o["dir"] * (live - o["entry"]) / risk
    rday["early"] += 1
    rday["r"] += R
    if R > 0.05:
        rday["wins"] += 1
    elif R < -0.05:
        rday["losses"] += 1
    if o in st["rev_open"]:
        st["rev_open"].remove(o)
    tg(f"🔄 خروج — {o['sym']} ({side}) {o['name']}\nإشارة معاكسة | {R:+.2f}R | السعر {fmt(live, pip)}")


def run_rev(st, sym, d30, df1, rday):
    pip = PIPS.get(sym, 0.0001)
    live = float(df1["Close"].iloc[-1])
    for s in rev_signals(d30, sym, st, live, pip):
        dr = s["dir"]
        for o in [x for x in st["rev_open"] if x["sym"] == sym and x["dir"] == -dr]:
            close_rev(st, o, live, rday)
        side = "شراء 🟢" if dr == 1 else "بيع 🔴"
        ic = {"REV": "⚡", "EARLY": "⏩", "SWING": "🔔"}.get(s["type"], "⚡")
        tg(f"{ic} {side} — {sym} | {s['type']} M30\n"
           f"الدخول: {fmt(s['entry'], pip)}\n"
           f"SL: {fmt(s['sl'], pip)} ({s['risk_p']:.0f} بيب)\n"
           f"TP1: {fmt(s['tp1'], pip)} | TP2: {fmt(s['tp2'], pip)}")
        st["rev_open"].append({"sym": sym, "dir": dr, "entry": s["entry"], "sl": s["sl"], "tp1": s["tp1"], "tp2": s["tp2"],
                               "rr": s["tp1_p"] / max(s["risk_p"], 1e-9), "name": s["type"], "tf": "30min",
                               "time": df1.index[-1].isoformat(), "be": False, "gk": "REV", "lvl": None, "rv": True})
        rday["sent"] += 1


# ---------------- البيانات ----------------
SENT = []
LAST_ERR = {"msg": ""}


def fetch(symbol):
    try:
        j = requests.get("https://api.twelvedata.com/time_series", timeout=30, params={
            "symbol": symbol, "interval": "1min", "outputsize": 5000,
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
    if rule == "1min":
        return df
    return df.resample(rule).agg({"Open": "first", "High": "max", "Low": "min", "Close": "last"}).dropna()


def make_frames(df, now, sym):
    m5, m15, m30, h1, h4 = (rs(df, r).iloc[:-1] for r in ("5min", "15min", "30min", "1h", "4h"))
    tr = lambda x, k: trend(x) if len(x) >= k else 0
    lv = gold_levels(df, now, 10.0 if sym == "XAU/USD" else 0.0)
    return {
        "1min": {"df": df.iloc[:-1].iloc[-700:], "b": (tr(m5, 60), tr(m15, 60)), "lv": lv},
        "5min": {"df": m5.iloc[-500:], "b": (tr(m15, 60), tr(h1, 60)), "lv": lv},
        "15min": {"df": m15.iloc[-350:], "b": (tr(h1, 60), tr(h4, 40)), "lv": lv},
        "30min": {"df": m30.iloc[-500:], "b": (0, 0), "lv": lv},
    }


# ---------------- الحالة وتيليجرام ----------------
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


# ---------------- متابعة الصفقات: TP / SL / EARLY EXIT / BE ----------------
def warn_text(t, side, sym, live, R, stage):
    pip = PIPS.get(sym, 0.0001)
    left = abs(live - t["sl"]) / pip
    lv = t.get("lvl")
    lv_txt = ""
    if lv is not None:
        held = (live > lv) if t["dir"] == 1 else (live < lv)
        lv_txt = f"\nالمستوى {lv:.5g}: " + ("لسا صامد (السعر على جهتك)" if held else "السعر رجع عكسو (ضعف واضح)")
    head = "⚠️ تحذير: الصفقة عم ترتد ضدك" if stage == 1 else "🔴 تحذير: السعر قريب من الستوب"
    return (f"{head}\n{sym} ({side}) - {t['name']}\n"
            f"الدخول: {t['entry']:.5g} | السعر الحالي: {live:.5g}\n"
            f"قطع {min(100, max(0, -R * 100)):.0f}% من المسافة نحو الستوب، وباقي {left:.0f} بيب للستوب {t['sl']:.5g}.{lv_txt}\n\n"
            f"شو تعمل: إذا بدك تقلل الخسارة سكّر هلأ. وإذا لسا مقتنع بالصفقة استنى الستوب (البوت لسا بيراقبها).")


def check_open(st, key, m5s, frames, now, day):
    still = []
    for t in st[key]:
        if not all(k in t for k in ("sym", "dir", "entry", "sl", "tp1", "time")):
            print("تم حذف صفقة قديمة ناقصة البيانات:", t)
            continue
        t.setdefault("name", "؟")
        t.setdefault("tp2", t["tp1"])
        t.setdefault("rr", 1.0)
        t.setdefault("gk", "؟")
        sym = t["sym"]
        df = m5s.get(sym)
        if df is None:
            still.append(t)
            continue
        t.setdefault("tf", "15min")
        rv = t.get("rv")
        pip_ = PIPS.get(sym, 0.0001)
        t0 = pd.Timestamp(t["time"])
        res = None
        for _, c in df[df.index > t0].iterrows():
            if t["dir"] == 1:
                sl_hit, tp_hit = c["Low"] <= t["sl"], c["High"] >= t["tp1"]
            else:
                sl_hit, tp_hit = c["High"] >= t["sl"], c["Low"] <= t["tp1"]
            if sl_hit:               # لو الاثنين بنفس الشمعة نعتبر الستوب أول (تحفظاً)
                res = "SL"
                break
            if tp_hit:
                res = "TP1"
                break
        if res is None and (now - t0).total_seconds() > EXPIRE_BY_TF.get(t["tf"], EXPIRE_HOURS) * 3600:
            res = "EXPIRED"
        side = "شراء" if t["dir"] == 1 else "بيع"
        live = float(df["Close"].iloc[-1])
        risk = abs(t["entry"] - t["sl"]) or 1e-9
        R = t["dir"] * (live - t["entry"]) / risk

        gs = day.setdefault("g", {}).setdefault(t.get("gk", "؟"), [0, 0])
        ss = day.setdefault("s", {}).setdefault(t["name"], [0, 0])
        if res == "TP1":
            gs[0] += 1; ss[0] += 1
            day["wins"] += 1
            day["r"] += t["rr"]
            if rv:
                tg(f"✅ TP1 — {sym} ({side}) {t['name']}\nانقل الستوب للدخول | TP2: {fmt(t['tp2'], pip_)}")
            else:
                tg(f"✅ تحقق الهدف الأول\n{sym} ({side}) - {t['name']}\nانقل الستوب لسعر الدخول {t['entry']:.5g}، والهدف الثاني {t['tp2']:.5g}\nالبوت رجع يدوّر على Setup جديد.")
        elif res == "SL":
            gs[1] += 1; ss[1] += 1
            day["losses"] += 1
            day["r"] -= 1
            if rv:
                tg(f"❌ SL — {sym} ({side}) {t['name']}")
            else:
                tg(f"❌ ضرب الستوب\n{sym} ({side}) - {t['name']}\nالستوب {t['sl']:.5g}\n"
                   + ("" if t.get("w1") else "ملاحظة: ما وصلك تحذير قبل الستوب لأنو الحركة كانت أسرع من فترة الفحص (5 دقايق).\n")
                   + "البوت رجع يدوّر على Setup جديد.")
        elif res == "EXPIRED":
            if rv:
                tg(f"⌛ انتهت — {sym} ({side}) {t['name']}")
            else:
                tg(f"⌛ انتهت الصفقة بدون نتيجة بعد {EXPIRE_BY_TF.get(t['tf'], EXPIRE_HOURS):.1f} ساعات\n{sym} ({side}) - {t['name']}\nالبوت رجع يدوّر على Setup جديد.")
        else:
            if rv:   # صفقات الانعكاس: بس نقل ستوب عند 1R، والخروج بإشارة معاكسة (بالمحرك)
                if R >= 1.0 and not t.get("be"):
                    t["be"] = True
                    tg(f"🛡️ {sym} ({side}) {R:.1f}R — انقل الستوب للدخول {fmt(t['entry'], pip_)}")
                still.append(t)
                continue
            reasons = []
            for ftf in [t["tf"]] + (["5min"] if t["tf"] == "15min" else []):   # صفقات M15 بتنراقب كمان على M5 (أسرع)
                fr = frames.get(sym, {}).get(ftf)
                if fr is None:
                    continue
                if t.get("lvl") is not None and (fr["df"].index > t0).sum() >= 1:
                    lastc = fr["df"].iloc[-1]
                    fa = float(prep(fr["df"].iloc[-60:])["ATR"].iloc[-1])
                    if (t["dir"] == 1 and lastc["Close"] < t["lvl"] - 0.15 * fa) or (t["dir"] == -1 and lastc["Close"] > t["lvl"] + 0.15 * fa):
                        reasons = [f"شمعة مكتملة أغلقت عكس المستوى {t['lvl']:.5g} (المنطقة/النموذج انكسر ضدك)"]
                        break
                if (fr["df"].index > t0).sum() >= 2:
                    rr_ = reversal_reasons(fr, t, live)
                    if rr_:
                        reasons = rr_
                        break
            if reasons:
                day["early"] += 1
                day["r"] += R
                if R > 0.05:
                    day["wins"] += 1
                    gs[0] += 1; ss[0] += 1
                elif R < -0.05:
                    day["losses"] += 1
                    gs[1] += 1; ss[1] += 1
                tg(f"🚨 EARLY EXIT — {sym} ({side})\n"
                   f"الدخول: {t['entry']:.5g} | السعر الحالي: {live:.5g}\n"
                   f"النتيجة الحالية: {R:+.2f}R\n"
                   f"سبب الإغلاق المقترح:\n- " + "\n- ".join(reasons) + "\n"
                   f"يُفضّل إغلاق الصفقة الآن. البوت سكّرها عندو وبيدوّر على Setup جديد.")
                continue
            if R <= -0.35 and not t.get("w1"):
                t["w1"] = True
                tg(warn_text(t, side, sym, live, R, 1))
            if R <= -0.7 and not t.get("w2"):
                t["w2"] = True
                tg(warn_text(t, side, sym, live, R, 2))
            if R >= 1.0 and not t.get("be"):
                t["be"] = True
                tg(f"🛡️ {sym} ({side}) حققت {R:.1f}R\nانقل الستوب لسعر الدخول {t['entry']:.5g} لتأمين الصفقة.")
            still.append(t)
    st[key] = still


# ---------------- التشغيل ----------------
def main():
    if not TD_KEY:
        print("TWELVE_DATA_API_KEY غير موجود")
        sys.exit(0)
    if env("TEST_MSG") == "1":
        tg("✅ البوت شغال وبيوصلك على تيليجرام.")
        return
    DIAG.clear()
    now = pd.Timestamp(datetime.now(timezone.utc).replace(tzinfo=None))
    today = now.strftime("%Y-%m-%d")
    st = load()
    for k, dflt in (("open", []), ("days", {}), ("last", {}), ("sig", {}),
                    ("rev_open", []), ("rv_done", {}), ("rv_last", {}), ("rv_sw", {})):
        if not isinstance(st.get(k), type(dflt)):
            st[k] = dflt
    day = st["days"].setdefault(today, {})
    for k, v in (("sent", 0), ("wins", 0), ("losses", 0), ("r", 0.0), ("summary", False), ("early", 0)):
        day.setdefault(k, v)
    rday = day.setdefault("rv", {})
    for k, v in (("sent", 0), ("wins", 0), ("losses", 0), ("r", 0.0), ("early", 0)):
        rday.setdefault(k, v)
    for k in list(st["days"].keys())[:-14]:
        del st["days"][k]
    st["open"] = [t for t in st["open"] if isinstance(t, dict) and t.get("sym") in SYMBOLS]   # حذف الصفقات القديمة/المشوهة
    st["rev_open"] = [t for t in st["rev_open"] if isinstance(t, dict) and t.get("sym") in SYMBOLS]
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
    check_open(st, "open", m5s, frames, now, day)
    check_open(st, "rev_open", m5s, frames, now, rday)

    # محرك الانعكاس: قناة منفصلة بدون حدود الإشارات اليومية/الجلسة/الصفقة الواحدة
    if REV_ON:
        for sym in m5s:
            run_rev(st, sym, frames[sym]["30min"]["df"], m5s[sym], rday)

    reason = "ما في Setup مطابق حالياً"
    if MAX_LOSSES and day["losses"] >= MAX_LOSSES:
        reason = f"وصل حد الخسائر اليومي ({MAX_LOSSES}) وموقّف لباقي اليوم"
    elif MAX_PER_DAY and day["sent"] >= MAX_PER_DAY:
        reason = f"وصل حد الإشارات اليومي ({MAX_PER_DAY})"
    elif not (SESSION_START <= now.hour < SESSION_END):
        reason = f"خارج ساعات التداول (من {SESSION_START}:00 لـ {SESSION_END}:00 UTC: لندن → المساء)"
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
            a15 = float(prep(fr["15min"]["df"])["ATR"].iloc[-1])
            a15 = a15 if a15 == a15 else 0.0
            for tf, f in fr.items():
                if tf not in FRAMES:
                    continue
                full = f["df"]
                for off in range(LOOKBACK.get(tf, 1)):
                    d = full.iloc[:len(full) - off]
                    if len(d) < 80:
                        break
                    setups, ctx = scan(d, f["b"][0], f["b"][1], "BTC" in sym, f["lv"], tf, a15)
                    dg("setups", len(setups))
                    need = MIN_SCORE + FRAME_EXTRA.get(tf, 0)
                    blockers = [e for z in ctx["zl"] if z["n"] >= 3 for e in (z["lo"], z["hi"])] + [z["lvl"] for z in ctx["zones"] if z["n"] >= 3] + list(f["lv"]["keys"].values())
                    struct = blockers + ctx["swings"] + [e for z in ctx["zones"] if z["n"] >= 2 for e in (z["lo"], z["hi"])]
                    min_risk = max(STOP_ATR_FRAME * ctx["atr"], STOP_ATR_M15 * a15)
                    buf = 0.3 * ctx["atr"] + SPREAD_PIPS * pip
                    ctime = d.index[-1].isoformat()
                    for s in setups:
                        key = f"{sym}|{tf}|{s['name']}|{s['dir']}|{ctime}"
                        if key in st["sig"]:
                            continue
                        lvl = s.get("lvl")
                        zk = None
                        if lvl is not None:   # كل منطقة بتنأخد مرة وحدة باليوم (إما مع الكسر أو بإعادة الاختبار)
                            zk = f"Z|{sym}|{round(lvl / max(0.5 * a15, 0.5 * ctx['atr'], 0.01))}|{s['dir']}|{today}"
                            if zk in st["sig"]:
                                dg("zone_used")
                                continue
                        own = [b_ for b_ in blockers if lvl is None or abs(b_ - lvl) > 0.5 * ctx["atr"]]
                        t, why_b = build(s, live, ctx["atr"], pip, RR, MIN_TP_PIPS, own, min_risk, struct, buf, WANT_TP_PIPS)
                        if not t:
                            dg("build:" + why_b)
                            continue
                        if s["score"] < need:
                            dg("low_score")
                            continue
                        moved, rk = s["dir"] * (live - ctx["price"]), abs(t["entry"] - t["sl"])
                        if not (-0.5 * rk <= moved <= 0.4 * rk):
                            dg("chase")
                            continue   # السعر ابتعد عن نقطة الإشارة (مطاردة) أو رجع ضدها
                        if sym not in best or s["score"] > best[sym]["score"]:
                            s.update(t)
                            s.update(sym=sym, tf=tf, key=key, zk=zk, ctx_time=m5s[sym].index[-1].isoformat())
                            best[sym] = s
        for s in sorted(best.values(), key=lambda x: -x["score"]):
            if MAX_PER_DAY and day["sent"] >= MAX_PER_DAY:
                break
            side = "شراء 🟢" if s["dir"] == 1 else "بيع 🔴"
            gl, gk, risk_pct = grade(s["score"])
            s["gk"] = gk
            body = "".join(f"- {x}\n" for x in [s["why"]] + s.get("tags", []))
            warn = "".join(f"⚠️ {x}\n" for x in s.get("warn", []))
            tg(f"⚡ {side} — {s['sym']}\n"
               f"التقييم: {gl} (نقاط {s['score']:.0f})\n"
               f"الجلسة: {session_name(now.hour)} | فريم {TFN.get(s['tf'], s['tf'])}\n"
               f"الاستراتيجية: {s['name']}\n"
               f"نوع الدخول: {MODE_AR.get(s.get('mode'), 'حسب النمط')}\n\n"
               f"الدخول: {s['entry']:.5g}\n"
               f"الستوب: {s['sl']:.5g} ({s['risk_p']:.0f} بيب)\n"
               f"منطق الستوب: {s['sl_note']}\n"
               f"TP1: {s['tp1']:.5g} ({s['tp1_p']:.0f} بيب)\n"
               f"TP2: {s['tp2']:.5g} ({s['tp2_p']:.0f} بيب)\n"
               f"المخاطرة المقترحة: {risk_pct:g}% من الحساب (اضبط اللوت حسب بعد الستوب)\n\n"
               f"ليش هالصفقة:\n{body}{warn}\n"
               f"الخطة: عند TP1 سكّر نص الحجم وانقل الستوب للدخول وكمّل للـ TP2. "
               f"الصفقة بتبطل عند الستوب. راجع السعر عند وسيطك قبل الدخول، والبوت بيراقبها وبيبعتلك EARLY EXIT عند انعكاس قوي.")
            st["open"].append({"sym": s["sym"], "dir": s["dir"], "entry": s["entry"], "sl": s["sl"],
                               "tp1": s["tp1"], "tp2": s["tp2"], "rr": s["tp1_p"] / max(s["risk_p"], 1e-9), "name": s["name"],
                               "tf": s["tf"], "time": s["ctx_time"], "be": False, "gk": s["gk"], "lvl": s.get("lvl")})
            st["last"][s["sym"]] = now.isoformat()
            st["sig"][s["key"]] = now.isoformat()
            if s.get("zk"):
                st["sig"][s["zk"]] = now.isoformat()
            day["sent"] += 1
        st["sig"] = {k: v for k, v in st["sig"].items() if (now - pd.Timestamp(v)).total_seconds() < 86400}

    if now.hour >= 21 and not day["summary"]:
        tg(f"📊 ملخص {today} (UTC)\nإشارات: {day['sent']} | رابحة: {day['wins']} | "
           f"خاسرة: {day['losses']} | خروج مبكر: {day['early']} | صافي: {day['r']:+.1f}R\n"
           + "حسب التقييم: " + (" | ".join(f"{k} {v[0]}✅/{v[1]}❌" for k, v in day.get("g", {}).items()) or "لا يوجد")
           + "\nحسب الاستراتيجية: " + (" | ".join(f"{k} {v[0]}✅/{v[1]}❌" for k, v in day.get("s", {}).items()) or "لا يوجد")
           + f"\n⚡ الانعكاس: إشارات {rday['sent']} | ✅ {rday['wins']} | ❌ {rday['losses']} | خروج {rday['early']} | صافي {rday['r']:+.1f}R"
           + "\nحسب النوع: " + (" | ".join(f"{k} {v[0]}✅/{v[1]}❌" for k, v in rday.get("s", {}).items()) or "لا يوجد"))
        day["summary"] = True

    print("DIAG", DIAG)
    dd = st.setdefault("diag", {})
    for k_, v_ in DIAG.items():
        dd[k_] = dd.get(k_, 0) + v_

    # نبض: إذا ما وصلت أي رسالة من HEARTBEAT_HOURS ساعة، بيبعت حالة البوت
    if not SENT and (now - pd.Timestamp(st["last_msg"])).total_seconds() >= HEARTBEAT_HOURS * 3600:
        sym0 = next(iter(m5s))
        px0 = float(m5s[sym0]["Close"].iloc[-1])
        tg(f"🫀 البوت شغال ({now:%H:%M} UTC) - {sym0}\nما وصلت إشارات منذ {HEARTBEAT_HOURS:g} ساعات.\n"
           f"السبب: {reason}\n\n{market_read(px0, frames[sym0], frames[sym0]['15min']['lv'], now.hour)}\n\n"
           f"اليوم: إشارات {day['sent']} | رابحة {day['wins']} | خاسرة {day['losses']} | صافي {day['r']:+.1f}R"
           + (f"\n\nليش ما في إشارات (آخر الفترة):\n{diag_text(dd)}" if dd else ""))
        st["diag"] = {}
    if SENT:
        st["last_msg"] = now.isoformat()
    save(st)


if __name__ == "__main__":
    try:
        main()
    except Exception:
        import traceback
        tb = traceback.format_exc()
        print(tb)
        try:
            st = load()
            now_ = pd.Timestamp(datetime.now(timezone.utc).replace(tzinfo=None))
            last_ = st.get("err_time")
            if not last_ or (now_ - pd.Timestamp(last_)).total_seconds() > 3600:
                tg("🚨 خطأ بالبوت (ابعت هالنص للمساعد):\n" + tb[-800:])
                st["err_time"] = now_.isoformat()
                save(st)
        except Exception as e2:
            print("failed to report error:", e2)
        sys.exit(0)
