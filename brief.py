"""Costruisce il brief giornaliero (messaggi Telegram in HTML) usando core.py."""
import re
from datetime import datetime, timedelta, timezone

import calendar_view, core, deepdive, relevance
from core import NOW, esc, humanize, local, countdown, jload, jsave

LIMIT = 3800
PHASE_ORDER = {"fading": 0, "emerging": 1, "rising": 2, "peak": 3}


def chunk(blocks):
    msgs, cur = [], ""
    parts = []
    for b in blocks:                       # un blocco troppo lungo viene spezzato sulle righe
        while len(b) > LIMIT:
            cut = b.rfind("\n", 0, LIMIT)
            cut = cut if cut > 0 else LIMIT
            parts.append(b[:cut])
            b = b[cut:].lstrip("\n")
        parts.append(b)
    for b in parts:
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


def day_tag(c):
    return "🕒 di oggi" if c.get("day") == "oggi" else f"🗂️ già fuori ({c.get('why_out', 'ancora rilevante')})"


def pct_txt(x):
    return f'{"~" if x.get("auto") else ""}{x["pct"]}%'


def fmt_news(c, with_coin=True, country=False):
    now = NOW()
    title = c["title_it"]
    tag = "  ♻️ <i>recuperata</i>" if c.get("recovered") else ""
    n = num("news", c["title"], c["link"], c["source"])
    pct = c.get("pct")
    head = f'{relevance.band(pct)} <b>{pct_txt(c)}</b> · ' if pct is not None else f'{core.alert_level(c, now)} '
    lines = [f'{head}<b>#{n}</b> <b>{esc(title)}</b>{tag}',
             f'   {local(c["pub"])} · {humanize(c["pub"], now)} · chi: {esc(c["source"])} · {src_line(c)}',
             f'   {c["origin"]} · {day_tag(c) if c.get("day") else ""} · impatto <b>{c["impact"]}</b>']
    if c["cat"] == "politica":
        lines[-1] += f' · crypto: {c.get("crypto_impact", "nullo")}'
        if country:
            lines[-1] += f' · paese: {c["region"]}'
    if c.get("why"):
        lines.append(f'   💬 {esc(c["why"])}')
    if c.get("when"):
        lines.append(f'   ⏳ catalyst {c["when"].strftime("%d/%m")} (countdown {countdown(c["when"], now)})')
    if with_coin and not WL_EMPTY[0]:
        lines.append("   🪙 impatta: " + (", ".join(c["watch"]) if c["watch"] else "nessuna coin in watchlist"))
    lines += connector_lines(c, 2 if c["cat"] == "politica" else 3)
    return "\n".join(lines)


def connector_lines(c, n=3):
    """NEWS CONNECTOR sintetico: n punti pertinenti + catena visiva."""
    cn = c.get("cn") or []
    if not cn:
        return []
    out = ["   🧩 <b>CONNECTOR</b>"] + [f"    • {esc(x)}" for x in cn[:n]]
    if c.get("map"):
        out.append(f"    🔗 {esc(c['map'])}")
    return out


def fmt_huge(c, phase_by_title=None):
    """Scheda sintetica Huge Announcement."""
    now = NOW()
    n = num("huge", c["title"], c["link"], c["source"])
    cd = f'⏳ {c["when"].strftime("%d/%m")} · countdown <b>{countdown(c["when"], now)}</b>' if c.get("when") else "già avvenuto / senza data certa"
    priced = ", ".join(f"{t}: {core.priced_in(core.wl_load().get(t, {}))}" for t in c["watch"]) or "n/d (nessuna coin in watchlist)"
    verdict = "Da monitorare" if c["pct"] >= 40 else "Da evitare"
    red = "sì: " + "parole di rischio nel titolo" if c.get("red") else "nessuna rilevata dalle fonti"
    return (f'📢 {relevance.band(c["pct"])} <b>{pct_txt(c)}</b> · <b>#{n}</b> <b>{esc(c["title_it"])}</b>\n'
            f'   categoria: {esc(" + ".join(c["hc"]))} · chi: {esc(c["source"])} · {src_line(c)}\n'
            f'   {local(c["pub"])} · {humanize(c["pub"], now)} · {c["origin"]} · {day_tag(c)}\n'
            f'   💬 cosa cambia: {esc(c.get("why") or "non specificato")}\n'
            f'   {cd}\n'
            f'   🪙 mappatura coin: {esc(", ".join(c["watch"]) or "nessuna in watchlist (meme OG esposte: non determinabile gratis)")}\n'
            f'   già prezzato: {esc(priced)} · precedente storico: non verificato · red flag: {esc(red)}\n'
            f'   verdetto: <b>{verdict}</b> (servono 3 conferme: news + prezzo + on-chain)')


def fmt_event(e, now):
    meme = f'\n   🐸 meme: {esc(e["memes"])}' if e.get("memes") else ""
    tt = f' (T{e["tier"]})'
    link = f'<a href="{esc(e["link"])}">{esc(e["source"])}</a>{tt}' if e.get("link") else f'{esc(e["source"])}{tt}'
    n = num("evento", e["name"], e.get("link", ""), e.get("source", ""))
    pc = f' {relevance.band(e["pct"])} <b>{pct_txt(e)}</b>' if e.get("pct") is not None else ""
    why = f'\n   💬 {esc(e["why"])}' if e.get("why") else ""
    return (f'📌{pc} <b>#{n}</b> <b>{esc(e["name"][:140])}</b>{why}\n   {e["date"].astimezone().strftime("%d/%m %H:%M")} · '
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


def _entries(cands, evs, pl, w, now):
    ents = []
    for i, c in enumerate(cands):
        age_h = int((now - c["pub"]).total_seconds() / 3600)
        ents.append({"id": f"c{i}", "key": c["key"], "h": c["score"], "titolo": c["title_it"][:200], "fonte": c["source"],
                     "tier": c["tier"], "eta_ore": age_h, "categoria": c["cat"], "n_fonti": len(c["sources"]),
                     "origine": c["origin"], "catalyst": c["when"].strftime("%Y-%m-%d") if c.get("when") else None,
                     "coin_watchlist": c["watch"],
                     "temi": [n for n, rx in core.THEMES.items() if rx.search(c["title"])][:3]})
    for i, e in enumerate(evs):
        days = (e["date"] - now).days
        ents.append({"id": f"e{i}", "key": "ev:" + core.cluster_key(e["name"]) + e["date"].strftime("%m%d"), "h": 6 if days <= 7 else 3,
                     "titolo": e["name"][:200], "fonte": e["source"], "tier": e["tier"], "categoria": "evento " + e["cat"],
                     "countdown_giorni": days, "coin_watchlist": []})
    for i, p in enumerate(pl):
        if p.get("item"):
            it = p["item"]
            ents.append({"id": f"p{i}", "key": "pe:" + core.cluster_key(p["name"] + it["title"]), "h": 3,
                         "titolo": f'{p["name"]}: {it["title"][:160]}', "fonte": it["source"], "tier": it["tier"],
                         "eta_ore": int((now - it["pub"]).total_seconds() / 3600), "categoria": "persona", "coin_watchlist": []})
    for t, c in w.items():
        if c.get("catalyst_date"):
            d = datetime.fromisoformat(c["catalyst_date"]).replace(tzinfo=timezone.utc)
            ents.append({"id": f"w{t}", "key": f"wl:{t}:{c['catalyst_date']}", "h": 8,
                         "titolo": f'Catalyst di ${t}: {c.get("catalyst_text", "")}', "fonte": "watchlist", "tier": 3,
                         "categoria": "catalyst watchlist", "countdown_giorni": (d - now).days, "coin_watchlist": [t]})
    return ents


def run(kind="full"):
    """kind: full | urgent | huge. Ritorna (messaggi, riepilogo_dati)."""
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
    clusters_all = core.process(items, w, seen, last_run)
    clusters = [c for c in clusters_all if (now - c["pub"]).days <= 10]
    cands = sorted(clusters, key=lambda c: -c["score"])[:34]
    translate_clusters(cands)

    evs_all = dedupe_events(core.manual_events() + core.macro_events() + core.news_events(items))
    evs = [e for e in evs_all if now - timedelta(hours=12) <= e["date"] <= now + timedelta(days=30)]
    for e in evs:                                # mappatura narrativa -> coin in watchlist
        hits = core.wl_match({"title": e["name"] + " " + e["what"]}, w)
        if hits:
            e["memes"] = (e.get("memes") + " " if e.get("memes") else "") + "watchlist: " + ", ".join(hits)
        elif not e.get("memes"):
            e["memes"] = "nessuna in watchlist (aggiungi parole chiave con /kw)"
    evs_s = [e for e in evs if e["cat"] != "Macro"][:8] + [e for e in evs if e["cat"] == "Macro"][:4]
    pl = core.persons_latest() if kind == "full" else []

    # ---------------- % di rilevanza (una sola chiamata a Gemini per tutto il brief)
    ents = _entries(cands, evs_s, pl, w, now)
    res, llm_ok = relevance.score(ents)
    for i, c in enumerate(cands):
        r = res[f"c{i}"]
        age_h = (now - c["pub"]).total_seconds() / 3600
        c.update({"pct": r["pct"], "why": r["why"], "auto": r.get("auto", False), "crit": r.get("crit"),
                  "cn": r.get("cn"), "map": r.get("map", ""),
                  "hc": relevance.huge_of(c["title"], r), "day": "oggi" if age_h < 48 else "fuori"})
        if c["day"] == "fuori":
            c["why_out"] = ("catalyst ancora futuro" if c.get("when") and c["when"] > now else "riesumata" if c["origin"] == "Riesumata"
                            else "coin in watchlist" if c["watch"] else "rilevanza alta")
            c["keep_out"] = bool((c.get("when") and c["when"] > now) or c["origin"] == "Riesumata" or c["watch"] or c["pct"] >= 60)
    for i, e in enumerate(evs_s):
        r = res[f"e{i}"]
        e.update({"pct": r["pct"], "why": r["why"], "auto": r.get("auto", False)})
    wl_pct = {t: res[f"w{t}"] for t in w if f"w{t}" in res}
    try:                                         # il calendario completo ricorda gli eventi (anche a 60-90 giorni)
        calendar_view.save_events(evs_all)
    except Exception as ex:
        print("calendar save errore:", repr(ex)[:150], flush=True)
    auto_any = any(c.get("auto") for c in cands)

    crypto = [c for c in cands if c["cat"] == "crypto"]
    pol = [c for c in cands if c["cat"] in ("politica", "macro")]
    urgent = sorted([c for c in cands if c["pct"] >= 80], key=lambda c: -c["pct"])
    huge = sorted([c for c in cands if len(c["hc"]) >= 2 and (c["day"] == "oggi" or c.get("keep_out"))], key=lambda c: -c["pct"])
    blocks = []
    used = {}
    WL_EMPTY[0] = not w
    today = datetime.now().strftime("%d/%m/%Y")
    head = {"full": f"<b>📅 BRIEF DEL GIORNO — {today}</b>", "urgent": f"<b>🚨 CONTROLLO URGENTI — {today} {datetime.now().strftime('%H:%M')}</b>",
            "huge": f"<b>📢 HUGE ANNOUNCEMENTS — {today}</b>"}[kind]
    blocks.append(head)
    if auto_any:
        blocks.append("ℹ️ <i>% con «~» = stima automatica dalle regole: la valutazione di Gemini non era disponibile ora.</i>")

    # ---------------- HUGE (anche comando "huge")
    def huge_blocks():
        out = ["<b>📢 HUGE ANNOUNCEMENTS (i big catalyst, almeno 2 criteri su 8)</b>"]
        if not huge:
            out.append("Nessun huge announcement oggi.")
        for c in pick(huge, 6, used):
            out.append(fmt_huge(c))
        return out

    if kind == "huge":
        blocks += huge_blocks()
        jsave("brief_index.json", INDEX)
        return chunk(blocks), report

    # ---------------- URGENTE (solo % >= 80)
    blocks.append("<b>🚨 URGENTE (rilevanza ≥80%, leggi subito)</b>")
    pu = pick(urgent, 6, used)
    if not pu:
        blocks.append("Nessuna news urgente (nessuna voce ≥80%).")
    for c in pu:
        blocks.append(fmt_news(c, country=True))
    for extra in deepdive.brief_blocks():
        blocks.append(extra)
    if kind == "urgent":
        jsave("brief_index.json", INDEX)
        _finish(state, clusters_all, seen, now, items)
        return chunk(blocks), report

    blocks += huge_blocks()

    # ---------------- NEWS DI OGGI (crypto, ultime 48h)
    blocks.append("<b>🔥 NEWS DI OGGI (ultime 24-48 ore, in anticipo)</b>")
    today_c = pick(sorted([c for c in crypto if c["day"] == "oggi" and c["score"] >= 3], key=lambda c: -c["pct"]), 8, used)
    if not today_c:
        blocks.append("Nessuna news crypto rilevante di oggi (oltre a quelle già sopra).")
    for c in today_c:
        blocks.append(fmt_news(c))

    # ---------------- NEWS GIA' FUORI
    blocks.append("<b>🗂️ NEWS GIÀ FUORI (2+ giorni, ancora rilevanti: catalyst futuro, riesumate o in watchlist)</b>")
    out_c = pick(sorted([c for c in crypto if c["day"] == "fuori" and c.get("keep_out")], key=lambda c: -c["pct"]), 6, used)
    if not out_c:
        blocks.append("Nessuna news già fuori ancora rilevante.")
    for c in out_c:
        blocks.append(fmt_news(c))
    dropped = [c for c in crypto + pol if c["day"] == "fuori" and not c.get("keep_out") and c["key"] not in used][:6]
    if dropped:
        blocks.append("<i>News scartate oggi (già fuori, nessun catalyst futuro, rilevanza bassa): " +
                      esc("; ".join(f'{c["title_it"][:50]} ({c["pct"]}%)' for c in dropped)) + "</i>")

    # ---------------- POLITICA (di oggi + già fuori)
    blocks.append("<b>🌍 TOP NEWS POLITICHE E MONDIALI (di oggi + già fuori)</b>")
    pp = pick(sorted([c for c in pol if c["cat"] == "politica" and (c["day"] == "oggi" or c.get("keep_out"))], key=lambda c: -c["pct"]), 8, used)
    for reg in ("IL", "ASIA"):                    # copertura regionale obbligatoria
        if not any(c["region"] == reg for c in pp):
            extra = next((c for c in cands if c["region"] == reg and c["cat"] == "politica"), None) or \
                    next((c for c in cands if c["region"] == reg), None)
            if extra and extra not in pp:
                pp.append(extra)
    for c in [c for c in pol if c["cat"] == "macro"][:2]:
        if c not in pp:
            pp.append(c)
    if not pp:
        blocks.append("Nessuna news politica rilevante oggi.")
    for c in pp[:12]:
        blocks.append(fmt_news(c, country=True))
    for reg, label in (("IL", "Israele"), ("ASIA", "Asia")):
        if not any(c["region"] == reg for c in pp):
            blocks.append(f"ℹ️ Nessuna news rilevante da fonti {label} oggi (o fonti non accessibili).")

    # ---------------- OGGI NEL CALENDARIO (vista giornaliera)
    blocks += calendar_view.today_block(w)

    # ---------------- EVENTI
    blocks.append("<b>🗓️ EVENTI IN ARRIVO (30 giorni)</b>")
    shown = sorted(evs_s, key=lambda e: (-(e.get("pct") or 0), e["date"]))[:12]
    if not shown:
        blocks.append("Nessun evento datato trovato nelle fonti.")
    if not any(e["cat"] == "Macro" for e in evs):
        blocks.append("ℹ️ Nessun evento macro ad alto impatto trovato (ForexFactory pubblica la settimana successiva nel weekend).")
    for e in shown:
        blocks.append(fmt_event(e, now))

    # ---------------- PERSONE
    blocks.append("<b>👤 PERSONE — ULTIME DICHIARAZIONI (ultimi 3 giorni, da notizie che le citano)</b>")
    plines = []
    for i, p in enumerate(pl):
        if p.get("item"):
            it = p["item"]
            r = res.get(f"p{i}", {"pct": 0, "auto": True})
            plines.append(f'• {relevance.band(r["pct"])} <b>{pct_txt(r)}</b> <b>{esc(p["name"])}</b>: '
                          f'<a href="{esc(it["link"])}">{esc(it["title"][:130])}</a>\n'
                          f'   {local(it["pub"])} · {humanize(it["pub"], now)} · {esc(it["source"])} (T{it["tier"]}) · {esc(r.get("why", ""))}')
        else:
            plines.append(f'• <b>{esc(p["name"])}</b>: {p["status"]}' + (f' ({p["note"]})' if p["note"] else ""))
    plines.append("ℹ️ I post diretti su X non sono leggibili gratis: qui vedi solo le notizie che li riportano (non verificato sul post originale).")
    blocks.append("\n".join(plines))

    # ---------------- NARRATIVE (con % = media delle % delle news collegate)
    blocks.append("<b>🧠 NARRATIVE DEL GIORNO</b> (fase stimata dalla frequenza delle notizie raccolte)")
    nar = core.narratives(clusters)
    prec = jload("precedents.json", {})
    nl = []
    for n in nar[:8]:
        rel = sorted([c["pct"] for c in cands if core.THEMES[n["name"]].search(c["title"])], reverse=True)[:3]
        npct = f'{sum(rel) // len(rel)}%' if rel else "n/d"
        coins = [t for t, cc in w.items() if re.search(re.escape(n["name"].split("/")[0].strip().split()[0]), " ".join(
            [cc.get("narrative", "")] + cc.get("keywords", []) + [t]), re.I)]
        priced = [f'{t}: {core.priced_in(w[t])}' for t in coins] or ["n/d"]
        nl.append(f'• <b>{n["name"]}</b> — rilevanza <b>{npct}</b> · fase <b>{n["phase"]}</b> ({n["n24"]} news/24h vs media {n["avg"]}/g)\n'
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
        extra = f'\n   rilevanza catalyst: <b>{pct_txt(wl_pct[t])}</b> · {esc(wl_pct[t].get("why", ""))}' if t in wl_pct else ""
        blocks.append(fmt_wl(t, c, clusters, now) + extra)

    # ---------------- CALENDARIO MENSILE
    blocks += calendar_view.month_extract(w)

    # ---------------- RED FLAG
    blocks.append("<b>⚠️ RED FLAG DEL GIORNO</b>")
    reds = pick(sorted([c for c in cands if c["red"] and c["key"] not in used and (c["day"] == "oggi" or c.get("keep_out"))],
                       key=lambda c: -c["pct"]), 4, {})
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
            rl.append(f'• {pct_txt(c)} <b>{esc(c["title_it"][:140])}</b>\n   perché: parole di rischio (hack/unlock/ban/vendite) · {src_line(c)}')
    if rl:
        blocks.append("\n".join(rl))

    # ---------------- RECAP LUNEDI'
    avg = sum(c["pct"] for c in cands) // max(1, len(cands))
    n_oggi = sum(1 for c in cands if c["day"] == "oggi")
    stats_extra = {"pct_avg": avg, "huge": len(huge), "oggi": n_oggi, "fuori": len(cands) - n_oggi}
    if datetime.now().weekday() == 0:
        blocks.append(recap(state, clusters, nar, w))

    # ---------------- affidabilita' fonti + nota finale
    bad = [n for n, v in report.items() if not any(v)]
    blocks.append("<i>Fonti non accessibili ora: " + (", ".join(bad) if bad else "nessuna") +
                  ". Dati non presenti = \"non verificato\". Nessuna previsione, solo contesto.</i>")
    blocks.append("📎 Copia e incolla qui qualsiasi news, titolo, evento o ticker (o scrivi il numero, es. <code>3</code>) per un <b>Deep Analysis con punteggio di rilevanza</b>. Comandi: <code>huge</code> big catalyst · <code>mese</code> calendario di tutti i giorni · <code>settimana</code> 7 giorni · <code>futuri</code> prossimi 60 giorni · <code>connetti [news]</code> solo NEWS CONNECTOR.")
    jsave("brief_index.json", INDEX)
    state["narr"] = {n["name"]: n["phase"] for n in nar}
    _finish(state, clusters_all, seen, now, items, nar, stats_extra)
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


def _avg(stats, week):
    v = [stats[d]["pct_avg"] for d in week if "pct_avg" in stats[d]]
    return sum(v) // len(v) if v else "n/d"


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
            f'watchlist mossa ≥20% nelle 24h: {esc(", ".join(mov) or "nessuna")}\n'
            f'rilevanza media: {_avg(stats, week)}% · huge announcements: {sum(stats[d].get("huge", 0) for d in week)} · '
            f'news di oggi vs già fuori: {sum(stats[d].get("oggi", 0) for d in week)} / {sum(stats[d].get("fuori", 0) for d in week)}')


def _finish(state, clusters, seen, now, items, nar=None, extra=None):
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
    if extra:
        stats[day].update(extra)
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


def fmt_connector(text, r, ent, when, now):
    """NEWS CONNECTOR completo per un testo incollato."""
    head = f'🧩 <b>NEWS CONNECTOR</b> — {relevance.band(r["pct"])} <b>{pct_txt(r)}</b> rilevanza'
    cn = "\n".join(f"• {esc(x)}" for x in (r.get("cn") or ["nessun collegamento rilevante"]))
    cd = f'⏳ catalyst {when.strftime("%d/%m")} · countdown <b>{countdown(when, now)}</b>' if when else "⏳ nessuna data certa nel testo (non verificato)"
    wl = ", ".join(ent["coin_watchlist"]) or "nessuna in watchlist"
    mp = f'\n🔗 {esc(r["map"])}' if r.get("map") else ""
    auto = "\n<i>Stima automatica: Gemini non era disponibile.</i>" if r.get("auto") else ""
    return (f'{head}\n<i>{esc(text[:140])}</i>\n{cn}{mp}\n💬 {esc(r.get("why", ""))}\n{cd}\n🪙 coin: {esc(wl)}{auto}')


def text_cards(text):
    """Testo incollato -> (scheda huge o None, [messaggio NEWS CONNECTOR]). Una sola valutazione Gemini."""
    now = NOW()
    w = core.wl_load()
    INDEX.clear()
    base = relevance.huge_criteria(text)
    ent = {"id": "x", "key": "txt:" + core.cluster_key(text), "h": 8, "titolo": text[:300], "categoria": "incollato",
           "eta_ore": 0, "coin_watchlist": core.wl_match({"title": text}, w),
           "temi": [n for n, rx in core.THEMES.items() if rx.search(text)][:3]}
    res, _ = relevance.score([ent])
    r = res["x"]
    hc = relevance.huge_of(text, r)
    d = core.parse_dates(text, now)
    when = d if d and d > now else None
    WL_EMPTY[0] = not w
    card = None
    if len(hc) >= 2 or len(base) >= 2:
        c = {"title": text, "title_it": text[:200], "link": "", "source": "testo incollato", "tier": 3,
             "sources": [("testo incollato", "", 3)], "pub": now, "origin": "Originale", "when": when,
             "watch": ent["coin_watchlist"], "red": bool(core.RED_KW.search(text)), "pct": r["pct"], "why": r["why"],
             "auto": r.get("auto", False), "hc": hc if len(hc) >= 2 else base, "day": "oggi", "cat": "crypto", "impact": "n/d"}
        card = [fmt_huge(c)]
    return card, [fmt_connector(text, r, ent, when, now)]


def huge_card_for_text(text):
    """Se il testo incollato è un huge announcement (>=2 criteri) ritorna la scheda sintetica, altrimenti None."""
    now = NOW()
    w = core.wl_load()
    INDEX.clear()
    base = relevance.huge_criteria(text)
    ent = {"id": "x", "key": "txt:" + core.cluster_key(text), "h": 8, "titolo": text[:300], "categoria": "incollato",
           "eta_ore": 0, "coin_watchlist": core.wl_match({"title": text}, w)}
    res, _ = relevance.score([ent])
    r = res["x"]
    hc = relevance.huge_of(text, r)
    if len(hc) < 2 and len(base) < 2:
        return None
    d = core.parse_dates(text, now)
    c = {"title": text, "title_it": text[:200], "link": "", "source": "testo incollato", "tier": 3,
         "sources": [("testo incollato", "", 3)], "pub": now, "origin": "Originale", "when": d if d and d > now else None,
         "watch": ent["coin_watchlist"], "red": bool(core.RED_KW.search(text)), "pct": r["pct"], "why": r["why"],
         "auto": r.get("auto", False), "hc": hc if len(hc) >= 2 else base, "day": "oggi", "cat": "crypto", "impact": "n/d"}
    WL_EMPTY[0] = not w
    return [fmt_huge(c)]
