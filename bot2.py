# -*- coding: utf-8 -*-
"""bot2.py - محركات الجلسات (آسيا->لندن، لندن/أمس->نيويورك) + تأكيد دخول من شموع 5 دقايق + وضع Backtest.
بيشتغل فوق bot.py وما بيعدّل عليه. للرجوع للنسخة القديمة: بالـworkflow بدّل python bot2.py بـ python bot.py

المحرك: السعر بيسحب سيولة مستوى (قمة/قاع الآسيوية، قمة/قاع لندن، قمة/قاع أمس) ويرجع، ثم شمعة 5 دقايق
بتكسر آخر قمة/قاع صغير قبل السحب (كسر هيكل). الستوب خلف السحب، هدفين، صفقة وحدة بالوقت.
المحركات القديمة (سحب مناطق M15 + اندفاع وتراجع) بتضل شغالة بنفس الوقت."""
import sys
import time
from datetime import datetime, timezone

import numpy as np
import pandas as pd

import bot as B

# ---------------- الإعدادات (كلها قابلة للتغيير من الـ workflow) ----------------
SES_ON = B.env("SES_ON", "1") == "1"
A_START = B.envi("A_START", 7)            # نافذة آسيا->لندن (UTC)
A_END = B.envi("A_END", 12)
N_START = B.envi("N_START", 13)           # نافذة لندن/أمس->نيويورك (UTC)
N_END = B.envi("N_END", 18)
ASIA_END = B.envi("ASIA_END", 7)          # الجلسة الآسيوية 00:00 -> ASIA_END
LON_START = B.envi("LON_START", 7)        # جلسة لندن للمستويات
LON_END = B.envi("LON_END", 13)
SW_LOOK = B.envi("SES_SW_LOOK", 12)       # كم شمعة 5د ورا نفتش عن شمعة السحب
MSS_BARS = B.envi("SES_MSS_BARS", 4)      # كم شمعة قبل السحب بنعتبر قمتها/قاعها هيكل
P_MIN = B.envf("SES_PIERCE_MIN", 0.15)    # أقل عمق للسحب (x ATR15)
P_MAX = B.envf("SES_PIERCE_MAX", 2.0)     # أكبر عمق (بعدها كسر حقيقي مو سحب)
ACCEPT = B.envf("SES_ACCEPT", 0.25)       # أكبر إغلاق مسموح خلف المستوى (قبول السعر هناك = مو سحب)
SES_CONFIRM = B.envi("SES_MIN_CONFIRM", 1)  # من 3: اتجاه الساعة، 4 ساعات، منطقة قوية عند المستوى
SES_BONUS = B.envi("SES_BONUS", 30)
M5_SIZE = B.envi("M5_SIZE", 1000)
BT_DAYS = B.envi("BT_DAYS", 14)

KIND_NAMES = {"ASIA": "سحب آسيا/أمس عند لندن", "NYSW": "سحب لندن/أمس عند نيويورك"}
B.KIND_AR.update(KIND_NAMES)
B.DIAG_LABELS.update({
    "ses_raw": "جلسات: سحب + كسر هيكل مكتشف",
    "ses_no_confirm": "جلسات: مرفوض لعدم التأكيد (ضد الاتجاه وبدون منطقة)",
})
B.BOT_VERSION = "2026-11-04-sessions"


# ---------------- المستويات ----------------
def _rng(m5c, day0, a, b, min_n):
    idx = m5c.index
    w = m5c[(idx >= day0 + pd.Timedelta(hours=a)) & (idx < day0 + pd.Timedelta(hours=b))]
    return w if len(w) >= min_n else None


def build_levels(m5c, keys, now, atr):
    """مستويات السيولة الفعالة هلأ: [(اسم، سعر، 'high'|'low'، هدف معاكس أو None، محرك)]"""
    out = []
    h = now.hour
    day0 = now.normalize()
    in_a = A_START <= h < A_END
    in_n = N_START <= h < N_END
    if not (in_a or in_n):
        return out
    eng = "ASIA" if in_a else "NYSW"
    if in_a:
        asia = _rng(m5c, day0, 0, ASIA_END, 48)
        if asia is not None:
            ah, al = float(asia["High"].max()), float(asia["Low"].min())
            if ah - al >= 1.0 * atr:
                out.append(("قمة الآسيوية", ah, "high", al, eng))
                out.append(("قاع الآسيوية", al, "low", ah, eng))
    if in_n:
        lon = _rng(m5c, day0, LON_START, LON_END, 36)
        if lon is not None:
            lh, ll = float(lon["High"].max()), float(lon["Low"].min())
            if lh - ll >= 1.0 * atr:
                out.append(("قمة لندن", lh, "high", ll, eng))
                out.append(("قاع لندن", ll, "low", lh, eng))
    keys = keys or {}
    if "PDH" in keys:
        out.append(("قمة أمس", float(keys["PDH"]), "high", None, eng))
    if "PDL" in keys:
        out.append(("قاع أمس", float(keys["PDL"]), "low", None, eng))
    return out


# ---------------- كشف السحب + كسر الهيكل (شموع 5 دقايق مغلقة، الشمعة k هي شمعة التأكيد) ----------------
def detect_sweep(m5c, L, side, atr, k):
    if atr <= 0 or k < SW_LOOK + MSS_BARS or k >= len(m5c):
        return None
    o = m5c["Open"].values
    h = m5c["High"].values
    l = m5c["Low"].values
    c = m5c["Close"].values
    lo_i = k - SW_LOOK
    if side == "high":                 # سحب قمة -> بيع
        j = lo_i + int(np.argmax(h[lo_i:k]))
        ext = float(h[j])
        if h[k] > ext:
            return None
        pierce = ext - L
        if not (P_MIN * atr <= pierce <= P_MAX * atr):
            return None
        if c[max(0, j - 12):j].max() > L + ACCEPT * atr:       # السعر كان فوق المستوى من قبل = مو سحب
            return None
        if c[j + 1:k + 1].max() > L + ACCEPT * atr or c[k] >= L:   # بعد شمعة السحب لازم يرجع ويسكّر تحت المستوى
            return None
        ref = float(l[j - MSS_BARS:j].min())
        if not (c[k] < ref and c[k] < o[k]):
            return None
        if j + 1 < k and (c[j + 1:k] < ref).any():             # الكسر لازم يكون أول مرة (مو قديم)
            return None
    else:                              # سحب قاع -> شراء
        j = lo_i + int(np.argmin(l[lo_i:k]))
        ext = float(l[j])
        if l[k] < ext:
            return None
        pierce = L - ext
        if not (P_MIN * atr <= pierce <= P_MAX * atr):
            return None
        if c[max(0, j - 12):j].min() < L - ACCEPT * atr:
            return None
        if c[j + 1:k + 1].min() < L - ACCEPT * atr or c[k] <= L:
            return None
        ref = float(h[j - MSS_BARS:j].max())
        if not (c[k] > ref and c[k] > o[k]):
            return None
        if j + 1 < k and (c[j + 1:k] > ref).any():
            return None
    return {"ext": ext, "pierce": float(pierce), "ref": ref, "ci": float(c[k]), "k": k, "j": j}


def session_cands(ctx, m5c, now, live):
    """مرشحي محركات الجلسات (نفس شكل مرشحي evaluate عشان try_open وmanage يشتغلوا عليهم)"""
    out = []
    if not SES_ON or m5c is None or len(m5c) < 300:
        return out
    h = now.hour
    if not (A_START <= h < A_END or N_START <= h < N_END):
        return out
    age = (now - (m5c.index[-1] + pd.Timedelta(minutes=5))).total_seconds() / 60
    if age > 20:
        B.dg("stale")
        return out
    sym, pip, atr = ctx["sym"], ctx["pip"], ctx["atr"]
    today = now.strftime("%Y-%m-%d")
    n = len(m5c)
    for name, L, side, opp, eng in build_levels(m5c, ctx["keys"], now, atr):
        dr = -1 if side == "high" else 1
        s = None
        for back in range(3):          # آخر 3 شموع مغلقة (لو الـcron تأخر)
            s = detect_sweep(m5c, L, side, atr, n - 1 - back)
            if s:
                break
        if not s:
            continue
        B.dg("ses_raw")
        h1ok, h4ok = ctx["h1d"] == dr, ctx["h4d"] == dr
        zok = B.near_zone(L, ctx["zones"], atr)
        n_ok = int(h1ok) + int(h4ok) + int(zok)
        if n_ok < SES_CONFIRM:
            B.dg("ses_no_confirm")
            continue
        cand = {"kind": eng, "dir": dr, "zone": None, "anchor": s["ext"]}
        swings = list(ctx["swings"]) + ([opp] if opp is not None else [])
        plan, why = B.make_plan(cand, live, atr, pip, ctx["zones"], B.SPREADS.get(sym, B.SPREAD_PIPS), ctx["keys"],
                                swings, B.MIN_TP_PIPS.get(sym, 0.0) * pip, B.MIN_TP1_PIPS.get(sym, 0.0) * pip)
        if plan is None:
            B.dg(why)
            continue
        moved = dr * (live - s["ci"])
        if moved > 0.4 * plan["risk"] or moved < -0.5 * plan["risk"]:
            B.dg("chase")
            continue
        up = side == "high"
        miss = ([] if h1ok else ["اتجاه الساعة"]) + ([] if h4ok else ["اتجاه 4 ساعات"]) + ([] if zok else ["منطقة قوية عند المستوى"])
        cand.update({
            "name": f"سحب {name} ثم كسر هيكل ({'بيع' if up else 'شراء'})",
            "why": [f"السعر سحب السيولة {'فوق' if up else 'تحت'} {name} ({L:,.2f}) بعمق {s['pierce'] / atr:.1f} ATR "
                    f"ورجع {'تحته' if up else 'فوقه'}",
                    f"شمعة 5 دقايق أغلقت {'تحت قاع' if up else 'فوق قمة'} ما قبل السحب ({s['ref']:,.2f}) = كسر هيكل مؤكد"],
            "ci": s["ci"], "info": "", "area": None, "off": 0, "plan": plan, "atr": atr, "n_ok": n_ok,
            "miss": miss, "quiet": False, "rank": n_ok * 100 + SES_BONUS,
            "key": f"{sym}|{eng}|{dr}|{name}|{today}", "zk": f"S|{sym}|{name}|{dr}"})
        out.append(cand)
    return out


# ---------------- رسالة الدخول (بتشمل السيناريو والسبب) ----------------
def entry_msg2(c, sym, pip):
    p = c["plan"]
    side = "شراء 🟢" if c["dir"] == 1 else "بيع 🔴"
    lines = [f"{side} — {sym}", f"السيناريو: {c['name']}"]
    if c.get("area"):
        lines.append(f"المنطقة: {B.fmt(c['area'][0], pip)} – {B.fmt(c['area'][1], pip)}")
    lines += ["- " + w for w in (c.get("why") or [])]
    lines += ["",
              f"الدخول: {B.fmt(p['entry'], pip)}",
              f"الستوب: {B.fmt(p['sl'], pip)} ({B.dist_txt(p['risk'], pip, sym)})",
              f"الهدف الأول: {B.fmt(p['tp1'], pip)} ({B.dist_txt(p['tp1_d'], pip, sym)})",
              f"الهدف الثاني: {B.fmt(p['tp2'], pip)} ({B.dist_txt(p['tp_d'], pip, sym)})"]
    if c.get("n_ok") is not None:
        lines.append(f"\nالتأكيد: {c['n_ok']}/3" + (f" (ناقص: {'، '.join(c['miss'])})" if c.get("miss") else ""))
    return "\n".join(lines)


# ---------------- ربط مع bot.py (بدون تعديل ملفو) ----------------
_orig_fetch = B.fetch
_orig_eval = B.evaluate


def fetch2(symbol, interval, size):
    # bot.py بيطلب شموع الدقيقة لمتابعة الصفقات، هون بنبدلها بـ 5 دقايق (نفس عدد الطلبات، وبتخدم الجلسات)
    if interval == "1min":
        return _orig_fetch(symbol, "5min", M5_SIZE)
    return _orig_fetch(symbol, interval, size)


def eval2(ctx, off, now):
    res = _orig_eval(ctx, off, now)
    if off == 0:
        try:
            df5 = ctx.get("df1")
            if df5 is not None and len(df5):
                res = res + session_cands(ctx, B.closed(df5, 5, now), now, ctx["live"])
        except Exception as e:
            print("session engines failed:", repr(e))
    return res


# ---------------- Backtest ----------------
def sim_trade(cand, m5, t_entry):
    """بيمشي الصفقة على شموع M5 بعد الدخول. بيرجع (نتيجة، R، وقت النهاية) أو None إذا الداتا خلصت"""
    p, dr = cand["plan"], cand["dir"]
    r1, rr, part = p["rr1"], p["rr"], B.TP1_PART
    limit = t_entry + pd.Timedelta(hours=B.EXPIRE_HOURS)
    fut = m5[m5.index >= t_entry]
    if len(fut) == 0:
        return None
    stage, last = 0, None
    for ts, r in zip(fut.index, fut.itertuples()):
        if ts >= limit:
            break
        hi, lo = r.High, r.Low
        last = (ts, r.Close)
        hit_tp = (hi >= p["tp"]) if dr == 1 else (lo <= p["tp"])
        if stage == 0:
            if (lo <= p["sl"]) if dr == 1 else (hi >= p["sl"]):
                return "SL", -1.0, ts
            if (hi >= p["be"]) if dr == 1 else (lo <= p["be"]):
                stage = 1
                if hit_tp:
                    return "TP", part * r1 + (1 - part) * rr, ts
        else:
            if (lo <= p["entry"]) if dr == 1 else (hi >= p["entry"]):
                return "BE", part * r1, ts
            if hit_tp:
                return "TP", part * r1 + (1 - part) * rr, ts
    if last is None or fut.index[-1] + pd.Timedelta(minutes=5) < limit:
        return None                                   # الداتا خلصت قبل ما تنتهي الصفقة
    r_now = dr * (last[1] - p["entry"]) / p["risk"]
    return "EXP", (r_now if stage == 0 else part * r1 + (1 - part) * r_now), limit


def backtest(sym, df15, df5, now, days):
    m5all = B.closed(df5, 5, now)
    start = m5all.index[-1].normalize() - pd.Timedelta(days=days)
    pos = np.flatnonzero(m5all.index >= start)
    ctx, ctx_t, busy = None, None, None
    trades, used = [], set()
    dl, dc, seen_days = {}, {}, set()
    for kp in pos:
        t_end = m5all.index[kp] + pd.Timedelta(minutes=5)
        hh = t_end.hour
        if t_end.weekday() >= 5 or not (A_START <= hh < A_END or N_START <= hh < N_END):
            continue
        dkey = t_end.strftime("%Y-%m-%d")
        seen_days.add(dkey)
        if busy is not None and t_end < busy:
            continue
        if dl.get(dkey, 0) >= B.MAX_LOSSES or dc.get(dkey, 0) >= B.MAX_PER_DAY:
            continue
        m5c = m5all.iloc[:kp + 1]
        if ctx is None or (t_end - ctx_t) >= pd.Timedelta(minutes=60):
            ctx = B.build_ctx(sym, df15, m5c, t_end)
            ctx_t = t_end
        if ctx is None:
            continue
        live = float(m5c["Close"].iloc[-1])
        cands = [c for c in session_cands(ctx, m5c, t_end, live) if c["key"] not in used]
        if not cands:
            continue
        best = max(cands, key=lambda x: x["rank"])
        used.add(best["key"])
        r = sim_trade(best, m5all, t_end)
        if r is None:
            continue
        res, R, end = r
        busy = end
        dc[dkey] = dc.get(dkey, 0) + 1
        if R < -0.05:
            dl[dkey] = dl.get(dkey, 0) + 1
        trades.append({"eng": best["kind"], "name": best["name"], "dir": best["dir"], "res": res, "R": R,
                       "t": t_end, "n_ok": best["n_ok"]})
    return trades, len(seen_days)


def summarize(trades, ndays, days):
    def line(label, ts):
        w = sum(1 for x in ts if x["R"] > 0.05)
        lo = sum(1 for x in ts if x["R"] < -0.05)
        flat = len(ts) - w - lo
        wr = (100 * w / (w + lo)) if (w + lo) else 0
        tot = sum(x["R"] for x in ts)
        return f"{label}: {len(ts)} صفقة | ✅{w} ❌{lo} ➖{flat} | نجاح {wr:.0f}% | صافي {tot:+.1f}R"
    out = [f"🧪 نتيجة Backtest آخر {days} يوم (محركات الجلسات فقط، {ndays} يوم تداول)"]
    if not trades:
        out.append("ما طلعت ولا صفقة بهالفترة.")
        return "\n".join(out)
    out.append(line("الكل", trades))
    out.append(f"المعدل: {len(trades) / max(ndays, 1):.1f} صفقة باليوم")
    for eng, nm in KIND_NAMES.items():
        ts = [x for x in trades if x["eng"] == eng]
        if ts:
            out.append(line(nm, ts))
    for lab, d in (("بيع", -1), ("شراء", 1)):
        ts = [x for x in trades if x["dir"] == d]
        if ts:
            out.append(line(lab, ts))
    return "\n".join(out)


def run_backtest():
    now = pd.Timestamp(datetime.now(timezone.utc).replace(tzinfo=None))
    msgs = []
    for sym in B.SYMBOLS:
        df15 = _orig_fetch(sym, "15min", 5000)
        time.sleep(1.5)
        df5 = _orig_fetch(sym, "5min", 5000)
        time.sleep(1.5)
        if df15 is None or df5 is None or len(df15) < 600 or len(df5) < 600:
            msgs.append(f"{sym}: ما قدرت أجيب بيانات كافية ({B.LAST_ERR['msg']})")
            continue
        trades, nd = backtest(sym, df15, df5, now, BT_DAYS)
        msgs.append(f"▪️ {sym}\n" + summarize(trades, nd, BT_DAYS))
    B.tg("\n\n".join(msgs) + "\n\n(تقدير تاريخي: مو ضمان للمستقبل. المحركات القديمة ما انحسبت هون، نتايجها الحقيقية عندك بالملخص اليومي.)")


def run():
    if B.env("BACKTEST") == "1":
        run_backtest()
        return
    B.fetch = fetch2
    B.evaluate = eval2
    B.entry_msg = entry_msg2
    B.main()


if __name__ == "__main__":
    try:
        run()
    except Exception:
        import traceback
        tb = traceback.format_exc()
        print(tb)
        try:
            st = B.load()
            now_ = pd.Timestamp(datetime.now(timezone.utc).replace(tzinfo=None))
            last_ = st.get("err_time")
            if not last_ or (now_ - pd.Timestamp(last_)).total_seconds() > 3600:
                B.tg("🚨 خطأ بالبوت (ابعت هالنص للمساعد):\n" + tb[-800:])
                st["err_time"] = now_.isoformat()
                B.save(st)
        except Exception as e2:
            print("failed to report error:", e2)
        sys.exit(0)
