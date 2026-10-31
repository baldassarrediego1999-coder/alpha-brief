"""Costruisce il brief giornaliero (messaggi Telegram in HTML) usando core.py."""
import re
from datetime import datetime, timedelta, timezone

import core, deepdive
from core import NOW, esc, humanize, local, countdown, jload, jsave

LIMIT = 3800
PHASE_ORDER = {"fading": 0, "emerging": 1, "rising": 2, "peak": 3}


def chunk(blocks):
    msgs, cur = [], ""
    for b in blocks:
        if len(cur) + len(b) + 2 > LIMIT:
            msgs.append(cur)
            cur = ""
        cur += b + "\n\n"
    if cur.strip():
        msgs.append(cur)
    return msgs


def src_line(c):
    names = []
    for s, l, t in c["sources"]:
        if s not in [n for n, _ in names]:
            names.append((s, l))
    main = f'<a href="{esc(c["link"])}">{esc(c["source"])}</a> (T{c["tier"]})'
    more = f' +{len(names) - 1} fonti ({esc(", ".join(n for n, _ in names[1:4]))})' if len(names) > 1 else ""
    return main + more


WL_EMPTY = [True]
INDEX = []          # voci numerate del brief: servono a "approfondisci N"


def num(kind, title, link, source):
    n = len(INDEX) + 1
    INDEX.append({"n": n, "kind": kind, "title": title, "link": link, "source": source})
    return n


def pick(cs, n, used):
    """Prende fino a n cluster non gia' mostrati, al massimo 2 per stessa storia (stesse parole chiave)."""
    out = []
    for c in cs:
        if c["key"] in used:
            continue
        same = sum(1 for o in out + [u for u in used.values()] if len(c["toks"] & o["toks"]) >= 3)
        if same >= 2:
            continue
        out.append(c)
        used[c["key"]] = c
        if len(out) >= n:
            break
    return out


def fmt_news(c, with_coin=True, country=False):
    now = NOW()
    lvl = core.alert_level(c, now)
    title = c["title_it"]
    tag = "  ♻️ <i>recuperata</i>" if c.get("recovered") else ""
    n = num("news", c["title"], c["link"], c["source"])
    lines = [f'{lvl} <b>#{n}</b> <b>{esc(title)}</b>{tag}',
             f'   {local(c["pub"])} · {humanize(c["pub"], now)} · chi: {esc(c["source"])} · {src_line(c)}',
             f'   {c["origin"]} · impatto <b>{c["impact"]}</b>']
    if c["cat"] == "politica":
        lines[-1] += f' · crypto: {c.get("crypto_impact", "nullo")}'
        if country:
            lines[-1] += f' · paese: {c["region"]}'
    if c.get("when"):
        lines.append(f'   ⏳ catalyst {c["when"].strftime("%d/%m")} (countdown {countdown(c["when"], now)})')
    if with_coin and not WL_EMPTY[0]:
        lines.append("   🪙 impatta: " + (", ".join(c["watch"]) if c["watch"] else "nessuna coin in watchlist"))
    return "\n".join(lines)


def fmt_event(e, now):
    meme = f'\n   🐸 meme: {esc(e["memes"])}' if e.get("memes") else ""
    tt = f' (T{e["tier"]})'
    link = f'<a href="{esc(e["link"])}">{esc(e["source"])}</a>{tt}' if e.get("link") else f'{esc(e["source"])}{tt}'
    n = num("evento", e["name"], e.get("link", ""), e.get("source", ""))
    return (f'📌 <b>#{n}</b> <b>{esc(e["name"][:140])}</b>\n   {e["date"].astimezone().strftime("%d/%m %H:%M")} · '
            f'⏳ <b>{countdown(e["date"], now)}</b> · {esc(e["cat"])}\n   cosa cambia: {esc(e["what"] or "non specificato")}'
            f'{meme}\n   {link}')


def dedupe_events(evs):
    seen, out = set(), []
    for e in sorted(evs, key=lambda x: (x["date"], x["tier"])):
        k = (e["date"].strftime("%m%d"), core.cluster_key(e["name"]))
        if k in seen:
            continue
        seen.add(k)
        out.append(e)
    return out


def translate_clusters(cs):
    jobs = [(c, core._pool.submit(core.translate, c["title"], c["lang"])) for c in cs if c["lang"] not in ("en", "it")]
    for c, f in jobs:
        c["title_it"] = f.result()
    core.jsave("translations.json", core._tr_cache)


def run(kind="full"):
    """kind: full | urgent. Ritorna (messaggi, riepilogo_dati)."""
    now = NOW()
    INDEX.clear()
    state = jload("state.json", {})
    seen = state.get("seen", {})
    last_run = datetime.fromisoformat(state["last_run"]) if state.get("last_run") else None
    report = {}
    items = core.collect(report)
    w = core.wl_load()
    if w:
        core.wl_refresh(w)
        core.wl_save(w)
    clusters = core.process(items, w, seen, last_run)
    top_pool = clusters[:60]
    translate_clusters(top_pool)

    clusters = [c for c in clusters if (now - c["pub"]).days <= 3]
    macro_news = [c for c in clusters if c["cat"] == "macro"]
    crypto = [c for c in clusters if c["cat"] == "crypto"]
    pol = [c for c in clusters if c["cat"] in ("politica", "macro")]

    evs = core.manual_events() + core.macro_events() + core.news_events(items)
    evs = [e for e in dedupe_events(evs) if now - timedelta(hours=12) <= e["date"] <= now + timedelta(days=30)]
    for e in evs:                                # mappatura narrativa -> coin in watchlist
        hits = core.wl_match({"title": e["name"] + " " + e["what"]}, w)
        if hits:
            e["memes"] = (e.get("memes") + " " if e.get("memes") else "") + "watchlist: " + ", ".join(hits)
        elif not e.get("memes"):
            e["memes"] = "nessuna in watchlist (aggiungi parole chiave con /kw)"

    urgent = [c for c in clusters if core.alert_level(c, now) == "🔴"]
    urgent_ev = [e for e in evs if (e["date"] - now).days <= 7 and e["cat"] != "Macro"]
    blocks = []
    used = {}
    WL_EMPTY[0] = not w
    today = datetime.now().strftime("%d/%m/%Y")

    # ---------------- URGENTE
    b = [f"<b>📅 BRIEF DEL GIORNO — {today}</b>" if kind == "full" else f"<b>🚨 CONTROLLO URGENTI — {today} {datetime.now().strftime('%H:%M')}</b>",
         "<b>🚨 URGENTE (leggi subito)</b>"]
    if not urgent and not urgent_ev:
        b.append("Nessuna news urgente oggi.")
    blocks.append("\n".join(b))
    for c in pick(urgent, 5, used):
        blocks.append(fmt_news(c))
    for e in urgent_ev[:6]:
        blocks.append("⏳ catalyst entro 7 giorni\n" + fmt_event(e, now))
    for extra in deepdive.brief_blocks():
        blocks.append(extra)
    if kind == "urgent":
        _finish(state, clusters, seen, now, items)
        jsave("brief_index.json", INDEX)
        return chunk(blocks), report

    # ---------------- TOP CRYPTO
    blocks.append("<b>🔥 TOP NEWS CRYPTO</b>")
    pick_c = pick([c for c in crypto if c["score"] >= 3], 8, used)
    if not pick_c:
        blocks.append("Nessuna news crypto rilevante oggi (oltre a quelle già in 🚨).")
    for c in pick_c:
        blocks.append(fmt_news(c))

    # ---------------- TOP POLITICA
    blocks.append("<b>🌍 TOP NEWS POLITICHE E MONDIALI</b>")
    pp = pick([c for c in pol if c["cat"] == "politica" and c["score"] >= 3], 8, used)
    for reg in ("IL", "ASIA"):                    # copertura regionale obbligatoria
        if not any(c["region"] == reg for c in pp):
            extra = next((c for c in clusters if c["region"] == reg and c["cat"] == "politica"), None) or \
                    next((c for c in clusters if c["region"] == reg), None)
            if extra:
                pp.append(extra)
    for c in macro_news[:2]:
        if c not in pp:
            pp.append(c)
    if not pp:
        blocks.append("Nessuna news politica rilevante oggi.")
    for c in pp[:12]:
        blocks.append(fmt_news(c, country=True))
    for reg, label in (("IL", "Israele"), ("ASIA", "Asia")):
        if not any(c["region"] == reg for c in pp):
            blocks.append(f"ℹ️ Nessuna news rilevante da fonti {label} oggi (o fonti non accessibili).")

    # ---------------- EVENTI
    blocks.append("<b>🗓️ EVENTI IN ARRIVO (30 giorni)</b>")
    shown = [e for e in evs if e["cat"] != "Macro"][:10] + [e for e in evs if e["cat"] == "Macro"][:6]
    shown.sort(key=lambda e: e["date"])
    if not shown:
        blocks.append("Nessun evento datato trovato nelle fonti.")
    if not any(e["cat"] == "Macro" for e in evs):
        blocks.append("ℹ️ Nessun evento macro ad alto impatto trovato (ForexFactory pubblica la settimana successiva nel weekend).")
    for e in shown:
        blocks.append(fmt_event(e, now))

    # ---------------- PERSONE
    blocks.append("<b>👤 PERSONE — ULTIME DICHIARAZIONI (ultimi 3 giorni, da notizie che le citano)</b>")
    pl = core.persons_latest()
    plines = []
    for p in pl:
        if p.get("item"):
            it = p["item"]
            imp = "alto" if core.HIGH_KW.search(it["title"]) else "basso"
            plines.append(f'• <b>{esc(p["name"])}</b>: <a href="{esc(it["link"])}">{esc(it["title"][:130])}</a>\n'
                          f'   {local(it["pub"])} · {humanize(it["pub"], now)} · {esc(it["source"])} (T{it["tier"]}) · impatto {imp}')
        else:
            plines.append(f'• <b>{esc(p["name"])}</b>: {p["status"]}' + (f' ({p["note"]})' if p["note"] else ""))
    plines.append("ℹ️ I post diretti su X non sono leggibili gratis: qui vedi solo le notizie che li riportano (non verificato sul post originale).")
    blocks.append("\n".join(plines))

    # ---------------- NARRATIVE
    blocks.append("<b>🧠 NARRATIVE DEL GIORNO</b> (fase stimata dalla frequenza delle notizie raccolte)")
    nar = core.narratives(clusters)
    prec = jload("precedents.json", {})
    nl = []
    for n in nar[:8]:
        coins = [t for t, cc in w.items() if re.search(re.escape(n["name"].split("/")[0].strip().split()[0]), " ".join(
            [cc.get("narrative", "")] + cc.get("keywords", []) + [t]), re.I)]
        priced = [f'{t}: {core.priced_in(w[t])}' for t in coins] or ["n/d"]
        nl.append(f'• <b>{n["name"]}</b> — fase <b>{n["phase"]}</b> ({n["n24"]} news/24h vs media {n["avg"]}/g)\n'
                  f'   precedente storico: {esc(prec.get(n["name"], "non verificato (aggiungilo in precedents.json)"))}\n'
                  f'   già prezzato: {esc(", ".join(priced))} · coin: {esc(", ".join(coins) or "nessuna in watchlist")}')
    if not nl:
        nl.append("Nessuna narrativa in movimento.")
    blocks.append("\n".join(nl))

    # ---------------- WATCHLIST
    blocks.append("<b>📊 WATCHLIST STATUS</b>")
    if not w:
        blocks.append("Watchlist vuota: la mappatura news→coin è disattiva finché non aggiungi coin. Aggiungi coin con <code>/add TICKER [chain] [narrativa]</code>.")
    for t, c in w.items():
        blocks.append(fmt_wl(t, c, clusters, now))

    # ---------------- CALENDARIO MENSILE
    blocks.append("<b>📆 CALENDARIO MENSILE</b> (ultime notizie importanti per giorno, vedi /mese per il mese intero)")
    blocks.append(month_text(limit_days=7))

    # ---------------- RED FLAG
    blocks.append("<b>⚠️ RED FLAG DEL GIORNO</b>")
    reds = pick([c for c in clusters if c["red"] and c["score"] >= 3 and c["key"] not in used], 4, {})
    for t, c in w.items():
        if c.get("market_ok") and c.get("liq") is not None and c["liq"] < 3000:
            reds.append({"_wl": f"{t}: liquidità molto bassa ({fmt(c['liq'])}) — slippage alto"})
    if not reds:
        blocks.append("Nessuna red flag oggi.")
    rl = []
    for c in reds:
        if "_wl" in c:
            rl.append("• " + esc(c["_wl"]))
        else:
            rl.append(f'• <b>{esc(c["title_it"][:140])}</b>\n   perché: parole di rischio (hack/unlock/ban/vendite) · {src_line(c)}')
    if rl:
        blocks.append("\n".join(rl))

    # ---------------- RECAP LUNEDI'
    if datetime.now().weekday() == 0:
        blocks.append(recap(state, clusters, nar, w))

    # ---------------- affidabilita' fonti
    bad = [n for n, v in report.items() if not any(v)]
    blocks.append("<i>Fonti non accessibili ora: " + (", ".join(bad) if bad else "nessuna") +
                  ". Dati non presenti = \"non verificato\". Nessuna previsione, solo contesto.</i>")
    blocks.append("💡 Rispondi con <code>approfondisci 3</code> (numero della voce #) o <code>deep dive su …</code> per un'analisi complessa su un singolo punto. Altri: confronta A vs B · scenario … · storico … · chi compra TICKER · deep dive watchlist TICKER.")
    jsave("brief_index.json", INDEX)
    state["narr"] = {n["name"]: n["phase"] for n in nar}
    _finish(state, clusters, seen, now, items, nar)
    return chunk(blocks), report


def fmt(v):
    return core.fmt_usd(v)


def fmt_wl(t, c, clusters, now):
    related = [x for x in clusters if t in x["watch"]]
    last = max((x["pub"] for x in related), default=None)
    dormant_days = int(c.get("dormant_days", 3))
    status = "attiva" if last and (now - last).days < dormant_days else \
        f"dormiente (nessuna news da ≥{dormant_days}g)"
    if c.get("market_ok"):
        ath = f'{c["ath"]:.6g} ({datetime.fromisoformat(c["ath_date"]).strftime("%m/%y")})' if c.get("ath") else "non verificato"
        dd = f'{(1 - c["price"] / c["ath"]) * 100:.0f}%' if c.get("ath") else "n/d"
        mk = (f'MC {fmt(c.get("mc"))} · liq {fmt(c.get("liq"))} · vol24 {fmt(c.get("vol24"))} · 24h {c.get("ch24", "n/d")}%')
    else:
        ath, dd, mk = "non verificato", "n/d", "dati di mercato non trovati (ticker/contratto?)"
    cat = ""
    if c.get("catalyst_date"):
        d = datetime.fromisoformat(c["catalyst_date"]).replace(tzinfo=timezone.utc)
        cat = f'\n   catalyst: {esc(c.get("catalyst_text", ""))} · {d.strftime("%d/%m")} · ⏳ <b>{countdown(d, now)}</b> · già prezzato: {core.priced_in(c)}'
    rel = ""
    if related:
        r = related[0]
        rel = f'\n   ultima news: <a href="{esc(r["link"])}">{esc(r["title_it"][:90])}</a> ({humanize(r["pub"], now)})'
    return (f'<b>${esc(t)}</b> · {esc(c.get("chain", "?"))} · {status}\n   ATH {ath} · drawdown {dd}\n   {mk}'
            f'\n   narrativa: {esc(c.get("narrative") or "—")}{cat}{rel}'
            f'\n   segnale on-chain: non verificato (nessuna fonte gratuita affidabile)')


def month_text(limit_days=None, month=None):
    h = jload("history.json", {})
    month = month or datetime.now().strftime("%Y-%m")
    days = sorted((d for d in h if d.startswith(month)), reverse=True)
    if limit_days:
        days = days[:limit_days]
    if not days:
        return "Storico ancora vuoto: si riempie a ogni brief."
    lines = []
    for d in days:
        for n in h[d]:
            lines.append(f'<b>{d[8:]}/{d[5:7]}</b> · {esc(n["title"][:100])} · {esc(n["who"])} · {n["cat"]} · T{n["tier"]}')
    return "\n".join(lines)


def recap(state, clusters, nar, w):
    h = jload("history.json", {})
    stats = jload("stats.json", {})
    week = sorted(stats)[-7:]
    tot = {"crypto": 0, "politica": 0, "macro": 0}
    for d in week:
        for k in tot:
            tot[k] += stats[d].get(k, 0)
    prev = state.get("narr", {})
    up = [f'{n["name"]} ({prev[n["name"]]}→{n["phase"]})' for n in nar
          if n["name"] in prev and PHASE_ORDER[n["phase"]] > PHASE_ORDER[prev[n["name"]]]]
    mov = [f'${t} ({c.get("ch24")}% 24h)' for t, c in w.items() if c.get("ch24") not in (None, 0) and abs(float(c["ch24"])) >= 20]
    return ("<b>📌 RECAP SETTIMANALE</b>\n"
            f'news raccolte (7 giorni): crypto {tot["crypto"]} · politica {tot["politica"]} · macro {tot["macro"]}\n'
            f'narrative salite di fase: {esc(", ".join(up) or "nessuna")}\n'
            f'watchlist mossa ≥20% nelle 24h: {esc(", ".join(mov) or "nessuna")}')


def _finish(state, clusters, seen, now, items, nar=None):
    for c in clusters:
        seen.setdefault(c["key"], now.timestamp())
    cutoff = (now - timedelta(days=30)).timestamp()
    state["seen"] = {k: v for k, v in seen.items() if v >= cutoff}
    state["last_run"] = now.isoformat()
    jsave("state.json", state)
    core.history_add(clusters)
    stats = jload("stats.json", {})
    day = datetime.now().strftime("%Y-%m-%d")
    stats[day] = {k: sum(1 for c in clusters if c["cat"] == k and (now - c["pub"]).days < 1) for k in ("crypto", "politica", "macro")}
    jsave("stats.json", stats)


def latest(n=25):
    """Tutte le ultime notizie in ordine cronologico (le piu' recenti prima), senza filtro di importanza."""
    state = jload("state.json", {})
    last_run = datetime.fromisoformat(state["last_run"]) if state.get("last_run") else None
    items = core.collect({})
    w = core.wl_load()
    cs = core.process(items, w, state.get("seen", {}), last_run)
    now = NOW()
    INDEX.clear()
    cs = sorted([c for c in cs if (now - c["pub"]).days <= 2], key=lambda c: c["pub"], reverse=True)[:n]
    translate_clusters(cs)
    WL_EMPTY[0] = not w
    head = f"<b>🗞 ULTIME NOTIZIE — {datetime.now().strftime('%d/%m %H:%M')}</b> (ordine cronologico, {len(cs)} notizie)"
    return chunk([head] + [fmt_news(c, country=True) for c in cs])
