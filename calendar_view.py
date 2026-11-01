"""
Calendario completo: TUTTI i giorni del mese (anche vuoti), vista settimanale, eventi futuri a 60 giorni
e vista di oggi. Gli eventi visti dal brief vengono salvati in calendar.json, cosi' i giorni passati
restano consultabili con le notizie dello storico (history.json).
"""
import calendar as _cal
from datetime import datetime, timedelta, timezone

import core, relevance
from core import NOW, esc, jload, jsave, countdown

WD = ["lun", "mar", "mer", "gio", "ven", "sab", "dom"]
ROWS_PER_MSG = 8


def _day(dt):
    return dt.astimezone().strftime("%Y-%m-%d")


def save_events(evs):
    cal = jload("calendar.json", {})
    for e in evs:
        row = {"name": e["name"][:140], "cat": e["cat"], "pct": e.get("pct"), "link": e.get("link", ""),
               "source": e.get("source", ""), "t": e["date"].isoformat(), "what": (e.get("what") or "")[:200],
               "memes": (e.get("memes") or "")[:160], "tier": e.get("tier", 3)}
        lst = cal.setdefault(_day(e["date"]), [])
        ck = core.cluster_key(e["name"])
        for i, x in enumerate(lst):
            if core.cluster_key(x["name"]) == ck:
                if row["pct"] is None:
                    row["pct"] = x.get("pct")
                lst[i] = row
                break
        else:
            lst.append(row)
    cutoff = (NOW() - timedelta(days=120)).strftime("%Y-%m-%d")
    jsave("calendar.json", {k: v for k, v in cal.items() if k >= cutoff})


def all_events(with_macro=True):
    """Eventi salvati + inseriti a mano + macro della settimana (ForexFactory). Deduplicati, con data."""
    evs = []
    for k, lst in jload("calendar.json", {}).items():
        for x in lst:
            try:
                evs.append({**x, "date": datetime.fromisoformat(x["t"])})
            except Exception:
                continue
    evs += core.manual_events()
    if with_macro:
        try:
            evs += core.macro_events()
        except Exception:
            pass
    seen, out = set(), []
    for e in sorted(evs, key=lambda x: (x["date"], x.get("tier", 3))):
        k = (_day(e["date"]), core.cluster_key(e["name"]))
        if k in seen:
            continue
        seen.add(k)
        out.append(e)
    return out


def _catalysts(w):
    out = {}
    for t, c in (w or {}).items():
        if c.get("catalyst_date"):
            out.setdefault(c["catalyst_date"], []).append(f'${t}: {c.get("catalyst_text", "")}'.strip(": "))
    return out


def _index(evs, w):
    by = {}
    for e in evs:
        by.setdefault(_day(e["date"]), []).append(e)
    return by, jload("history.json", {}), _catalysts(w)


def _row(k, today_k, ev, news, cats, now, detail=2):
    d = datetime.strptime(k, "%Y-%m-%d")
    lab = f"{d.strftime('%d/%m')} {WD[d.weekday()]}"
    crypto = [e for e in ev if e["cat"] not in ("Macro", "Politica")]
    macro = [e for e in ev if e["cat"] in ("Macro", "Politica")]
    parts = []
    if crypto:
        parts.append("🪙 " + "; ".join(esc(e["name"][:55]) for e in crypto[:detail]))
    if macro:
        parts.append("🌍 " + "; ".join(esc(e["name"][:55]) for e in macro[:detail]))
    if news and k <= today_k:
        parts.append("📰 " + "; ".join(esc(n["title"][:60]) for n in news[:detail]))
    if cats:
        parts.append("🎯 " + "; ".join(esc(c[:60]) for c in cats[:detail]))
    pcts = [x["pct"] for x in ev + news if x.get("pct") is not None]
    mx = max(pcts) if pcts else None
    tail = ""
    if mx is not None:
        tail += f" · max {relevance.band(mx)}{mx}%"
    if k > today_k and (ev or cats):
        tail += f" · ⏳ {(d.date() - datetime.strptime(today_k, '%Y-%m-%d').date()).days}g"
    body = " | ".join(parts) if parts else "nessun evento"
    if k == today_k:
        return f"🔴 <b>{lab} OGGI</b> — {body}{tail}", mx
    return f"<b>{lab}</b> — {body}{tail}", mx


def _rows(days, w, evs=None, detail=2):
    now = NOW()
    today_k = datetime.now().strftime("%Y-%m-%d")
    evs = evs if evs is not None else all_events()
    by, hist, cats = _index(evs, w)
    return [(k,) + _row(k, today_k, by.get(k, []), hist.get(k, []), cats.get(k, []), now, detail) for k in days]


def _blocks(head, rows):
    out = [head]
    for i in range(0, len(rows), ROWS_PER_MSG):
        out.append("\n".join(r[1] for r in rows[i:i + ROWS_PER_MSG]))
    return out


def month_days(month=None):
    month = month or datetime.now().strftime("%Y-%m")
    y, m = int(month[:4]), int(month[5:7])
    n = _cal.monthrange(y, m)[1]
    return month, [f"{y:04d}-{m:02d}-{d:02d}" for d in range(1, n + 1)]


def month_blocks(w, month=None):
    try:
        month, days = month_days(month)
    except Exception:
        return ["Uso: <code>/mese AAAA-MM</code>"]
    rows = _rows(days, w)
    head = (f"<b>🗓️ CALENDARIO {month[5:7]}/{month[:4]} — VISTA COMPLETA</b> ({len(days)} giorni, anche quelli vuoti)\n"
            "🪙 crypto · 🌍 politica/macro · 📰 news uscite quel giorno (storico) · 🎯 catalyst watchlist · max = % più alta del giorno")
    out = _blocks(head, rows)
    out.append("<i>Giorni passati: dallo storico del bot (parte dal primo brief). Eventi futuri: solo quelli trovati nelle fonti o inseriti con /evento: "
               "un giorno vuoto significa «nessun evento trovato», non «nessun evento al mondo».</i>")
    return out


def month_extract(w, minpct=60):
    """Solo i giorni del mese con % max >= minpct (per il brief)."""
    month, days = month_days()
    rows = [r for r in _rows(days, w) if r[2] is not None and r[2] >= minpct]
    if not rows:
        return [f"<b>📆 CALENDARIO MENSILE — ESTRATTO</b>\nNessun giorno del mese con rilevanza ≥{minpct}%. Tabella completa di tutti i giorni: scrivi <code>mese</code>."]
    return ["<b>📆 CALENDARIO MENSILE — ESTRATTO</b> (giorni con rilevanza ≥%d%%; tutti i giorni: scrivi <code>mese</code>)\n" % minpct +
            "\n".join(r[1] for r in rows)]


def week_blocks(w):
    today = datetime.now().date()
    days = [(today + timedelta(days=i)).strftime("%Y-%m-%d") for i in range(7)]
    return _blocks("<b>🗓️ VISTA SETTIMANALE — prossimi 7 giorni</b>", _rows(days, w, detail=3))


def today_block(w):
    now = NOW()
    k = datetime.now().strftime("%Y-%m-%d")
    ev = [e for e in all_events() if _day(e["date"]) == k]
    cats = _catalysts(w).get(k, [])
    lines = ["<b>🗓️ OGGI NEL CALENDARIO</b>"]
    if not ev and not cats:
        lines.append("Nessun evento o catalyst datato per oggi.")
    for e in ev:
        pc = f'{relevance.band(e["pct"])} <b>{e["pct"]}%</b> · ' if e.get("pct") is not None else ""
        lines.append(f'{pc}<b>{esc(e["name"][:120])}</b> · {e["date"].astimezone().strftime("%H:%M")} · ⏳ {countdown(e["date"], now)} · {esc(e["cat"])}\n'
                     f'   cosa cambia: {esc(e.get("what") or "non specificato")} · meme: {esc(e.get("memes") or "nessuna in watchlist")} · fonte: {esc(e.get("source") or "n/d")}')
    for c in cats:
        lines.append(f"🎯 catalyst watchlist: {esc(c)}")
    return ["\n".join(lines)]


def upcoming_blocks(w, days=60):
    now = NOW()
    end = now + timedelta(days=days)
    ev = [e for e in all_events() if now - timedelta(hours=12) <= e["date"] <= end]
    items = [(e["date"], f'<b>{e["date"].astimezone().strftime("%d/%m")}</b> · ⏳ {countdown(e["date"], now)} · '
                         f'{(relevance.band(e["pct"]) + str(e["pct"]) + "% · ") if e.get("pct") is not None else ""}'
                         f'{esc(e["name"][:110])} · {esc(e["cat"])}'
                         + (f' · <a href="{esc(e["link"])}">{esc(e.get("source") or "fonte")}</a>' if e.get("link") else f' · {esc(e.get("source") or "n/d")}'))
             for e in ev]
    for dstr, lst in _catalysts(w).items():
        try:
            d = datetime.fromisoformat(dstr).replace(tzinfo=timezone.utc)
        except Exception:
            continue
        if now - timedelta(hours=12) <= d <= end:
            for c in lst:
                items.append((d, f'<b>{d.strftime("%d/%m")}</b> · ⏳ {countdown(d, now)} · 🎯 {esc(c[:110])}'))
    items.sort(key=lambda x: x[0])
    head = f"<b>🔮 EVENTI FUTURI — prossimi {days} giorni</b> (ordinati per data)"
    if not items:
        return [head + "\nNessun evento datato trovato nelle fonti o inserito con /evento."]
    out = [head]
    for i in range(0, len(items), 10):
        out.append("\n".join(x[1] for x in items[i:i + 10]))
    return out
