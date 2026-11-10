import os, json, time, subprocess
from datetime import datetime, timedelta, timezone
import requests

API_KEY = os.environ["TWELVE_API_KEY"]
TG_TOKEN = os.environ["TELEGRAM_TOKEN"]
TG_CHAT = os.environ["TELEGRAM_CHAT_ID"]

SYMBOL = "XAU/USD"
STATE_FILE = "state.json"
RUN_MINUTES = 55          # الجوب بيشتغل 55 دقيقة والتالي بيحل محلو
TRADE_POLL_SEC = 180      # متابعة الصفقة كل 3 دقايق
MAX_AGE_SEC = 240         # إذا الشمعة سكّرت من أكتر من 4 دقايق = إشارة متأخرة، تنتجاهل
MIN_RISK, MAX_RISK = 3.0, 12.0   # بالدولار
STOP_BUFFER = 0.30
WICK_EPS = 0.01
MAX_TRADE_HOURS = 12


def now_utc():
    return datetime.now(timezone.utc)


def send(text):
    try:
        r = requests.post(
            f"https://api.telegram.org/bot{TG_TOKEN}/sendMessage",
            data={"chat_id": TG_CHAT, "text": text}, timeout=20)
        print("telegram:", r.status_code, r.text[:200], flush=True)
    except Exception as e:
        print("telegram error:", e)


def td_series(interval, size):
    for _ in range(3):
        try:
            r = requests.get(
                "https://api.twelvedata.com/time_series",
                params={"symbol": SYMBOL, "interval": interval, "outputsize": size,
                        "timezone": "UTC", "apikey": API_KEY}, timeout=20)
            j = r.json()
            if "values" not in j:
                print("API:", j.get("message", j))
                time.sleep(5)
                continue
            out = []
            for v in reversed(j["values"]):
                t = datetime.strptime(v["datetime"], "%Y-%m-%d %H:%M:%S").replace(tzinfo=timezone.utc)
                out.append({"t": t, "o": float(v["open"]), "h": float(v["high"]),
                            "l": float(v["low"]), "c": float(v["close"])})
            return out
        except Exception as e:
            print("fetch error:", e)
            time.sleep(5)
    return []


# ---------- الحالة (بتنحفظ بالريبو حتى ما تضيع) ----------
def load_state():
    try:
        with open(STATE_FILE) as f:
            return json.load(f)
    except Exception:
        return {"trade": None, "last_signal": None}


def save_state(st):
    with open(STATE_FILE, "w") as f:
        json.dump(st, f)
    try:
        subprocess.run(["git", "config", "user.name", "gold-bot"], check=False)
        subprocess.run(["git", "config", "user.email", "gold-bot@users.noreply.github.com"], check=False)
        subprocess.run(["git", "add", STATE_FILE], check=False)
        subprocess.run(["git", "commit", "-m", "state"], check=False)
        subprocess.run(["git", "pull", "--rebase", "--autostash"], check=False)
        subprocess.run(["git", "push"], check=False)
    except Exception as e:
        print("git error:", e)


# ---------- هايكن آشي ----------
def heiken(c):
    ha = []
    for i, x in enumerate(c):
        hc = (x["o"] + x["h"] + x["l"] + x["c"]) / 4
        ho = (x["o"] + x["c"]) / 2 if i == 0 else (ha[-1]["o"] + ha[-1]["c"]) / 2
        ha.append({"t": x["t"], "o": ho, "c": hc,
                   "h": max(x["h"], ho, hc), "l": min(x["l"], ho, hc)})
    return ha


def colour(x):
    return 1 if x["c"] > x["o"] else -1


def no_wick(x, col):
    # شراء: بدون ذيل سفلي | بيع: بدون ذيل علوي
    return (x["o"] - x["l"] <= WICK_EPS) if col == 1 else (x["h"] - x["o"] <= WICK_EPS)


def detect(closed):
    """أول شمعة بدون ذيل بالاتجاه الجديد بعد تغيّر اللون."""
    ha = heiken(closed)
    last = ha[-1]
    col = colour(last)
    if not no_wick(last, col):
        return None
    i = len(ha) - 2
    while i >= 0 and colour(ha[i]) == col:
        if no_wick(ha[i], col):
            return None          # في شمعة بدون ذيل قبلها بنفس الموجة
        i -= 1
    if i < 0:
        return None
    return "BUY" if col == 1 else "SELL"


# ---------- الستوب والأهداف ----------
def swings(c, kind):
    out = []
    for i in range(2, len(c) - 2):
        nb = [c[j] for j in (i - 2, i - 1, i + 1, i + 2)]
        if kind == "high" and c[i]["h"] >= max(x["h"] for x in nb):
            out.append(c[i]["h"])
        if kind == "low" and c[i]["l"] <= min(x["l"] for x in nb):
            out.append(c[i]["l"])
    return out


def build(side, closed):
    price = closed[-1]["c"]
    d = 1 if side == "BUY" else -1
    last3 = closed[-3:]
    if side == "BUY":
        stop = min(x["l"] for x in last3) - STOP_BUFFER
        risk = price - stop
    else:
        stop = max(x["h"] for x in last3) + STOP_BUFFER
        risk = stop - price
    if risk > MAX_RISK:
        print("risk too big, skip:", risk)
        return None
    if risk < MIN_RISK:
        risk = MIN_RISK
        stop = price - risk if side == "BUY" else price + risk

    lv = swings(closed[-96:], "high" if side == "BUY" else "low")
    ahead = sorted({round(x, 2) for x in lv if (x - price) * d > 0}, key=lambda x: abs(x - price))

    t1 = next((x for x in ahead if 0.8 * risk <= abs(x - price) <= 2.5 * risk), price + d * risk)
    t2 = next((x for x in ahead if abs(x - price) >= max(1.6 * risk, abs(t1 - price) + 1.5)), None)
    if t2 is None or abs(t2 - price) > 5 * risk:
        t2 = price + d * 2 * risk
    if abs(t2 - price) <= abs(t1 - price) + 1:
        t2 = price + d * (abs(t1 - price) + max(risk, 3.0))

    return {"side": side, "entry": round(price, 2), "stop": round(stop, 2),
            "tp1": round(t1, 2), "tp2": round(t2, 2),
            "tp1_hit": False, "opened": now_utc().isoformat(), "checked": None}


def pips(a, b):
    return round(abs(a - b) * 10)


def entry_text(tr):
    e = tr["entry"]
    head = "شراء 🟢" if tr["side"] == "BUY" else "بيع 🔴"
    return (f"{head} — XAU/USD\n\n"
            f"الدخول: {e:,.2f}\n"
            f"الستوب: {tr['stop']:,.2f} ({pips(e, tr['stop'])} بيب)\n"
            f"الهدف الأول: {tr['tp1']:,.2f} ({pips(e, tr['tp1'])} بيب)\n"
            f"الهدف الثاني: {tr['tp2']:,.2f} ({pips(e, tr['tp2'])} بيب)")


# ---------- البحث عن إشارة ----------
def check_signal(st, slot):
    want = slot - timedelta(minutes=15)      # بداية الشمعة اللي سكّرت هلق
    closed = None
    for _ in range(4):
        data = [x for x in td_series("15min", 120) if x["t"] <= want]
        if data and data[-1]["t"] == want:
            closed = data
            break
        time.sleep(10)
    if not closed:
        print("no fresh candle -> السوق سكران أو الداتا ما وصلت")
        return
    if (now_utc() - slot).total_seconds() > MAX_AGE_SEC:
        print("late, skip")
        return
    if st.get("last_signal") == want.isoformat():
        return
    side = detect(closed)
    if not side:
        print("no signal at", want)
        return
    tr = build(side, closed)
    if not tr:
        return
    tr["checked"] = slot.isoformat()
    st["trade"] = tr
    st["last_signal"] = want.isoformat()
    save_state(st)
    send(entry_text(tr))


# ---------- متابعة الصفقة ----------
def monitor(st):
    tr = st["trade"]
    d = 1 if tr["side"] == "BUY" else -1
    opened = datetime.fromisoformat(tr["opened"])
    since = datetime.fromisoformat(tr["checked"]) if tr.get("checked") else opened
    data = [x for x in td_series("1min", 40) if x["t"] >= since]
    e = tr["entry"]

    for x in data:
        worst = x["l"] if d == 1 else x["h"]
        best = x["h"] if d == 1 else x["l"]
        stop_hit = (worst - tr["stop"]) * d <= 0
        t1_hit = (best - tr["tp1"]) * d >= 0
        t2_hit = (best - tr["tp2"]) * d >= 0

        if stop_hit:
            if tr["tp1_hit"]:
                send("⛔ ضرب الستوب بعد الهدف الأول\nالصفقة انتهت")
            else:
                send(f"❌ ضرب الستوب (-{pips(e, tr['stop'])} بيب)\nالصفقة انتهت خاسرة")
            st["trade"] = None
            save_state(st)
            return
        if t2_hit:
            send(f"✅✅ الهدف الثاني تحقق (+{pips(e, tr['tp2'])} بيب)\nالصفقة انتهت رابحة")
            st["trade"] = None
            save_state(st)
            return
        if t1_hit and not tr["tp1_hit"]:
            tr["tp1_hit"] = True
            send(f"✅ الهدف الأول تحقق (+{pips(e, tr['tp1'])} بيب)\nننتظر الهدف الثاني")
            save_state(st)

    if data:
        tr["checked"] = data[-1]["t"].isoformat()

    if now_utc() - opened > timedelta(hours=MAX_TRADE_HOURS):
        send("⏱ الصفقة انتهت بدون هدف أو ستوب (مرّ عليها 12 ساعة)")
        st["trade"] = None
        save_state(st)
        return
    save_state(st)


# ---------- التشغيل ----------
def main():
    st = load_state()
    end = now_utc() + timedelta(minutes=RUN_MINUTES)
    last_slot = None
    last_mon = 0
    print("bot started", now_utc().isoformat(), "trade open:", bool(st.get("trade")), flush=True)
    test = td_series("15min", 3)
    print("data check:", len(test), "candles, last:", test[-1]["t"] if test else None, flush=True)
    if os.environ.get("GITHUB_EVENT_NAME") == "workflow_dispatch":
        send("✅ البوت اشتغل وعم يراقب الذهب")
    while now_utc() < end:
        n = now_utc()
        if st.get("trade"):
            if time.time() - last_mon >= TRADE_POLL_SEC:
                last_mon = time.time()
                monitor(st)
        else:
            slot = n.replace(minute=n.minute // 15 * 15, second=0, microsecond=0)
            age = (n - slot).total_seconds()
            if slot != last_slot and age >= 5:
                last_slot = slot
                if age <= MAX_AGE_SEC:
                    check_signal(st, slot)
                else:
                    print("slot too old, skip")
        time.sleep(5)


if __name__ == "__main__":
    main()
