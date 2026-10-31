"""
DEEP DIVE ON DEMAND: raccoglie fatti reali (notizie, fonti, dati coin) e fa scrivere il report a Gemini
usando SOLO quei fatti. Se manca un dato, il report deve dire "non verificato".
"""
import html, json, os, re, time
from datetime import datetime, timedelta, timezone

import requests

import core
from core import NOW, esc, jload, jsave

GEMINI_KEY = lambda: os.environ.get("GEMINI_API_KEY", "").strip()
PER_DAY = int(os.environ.get("DD_PER_DAY", "3"))
_model = {"name": None}

PATTERNS = [
    ("n", re.compile(r"^(?:approfondisci|approfondire)\s+#?(\d+)\s*$", re.I)),
    ("wl", re.compile(r"^deep\s*dive\s+watchlist\s+\$?(\S+)\s*$", re.I)),
    ("cmp", re.compile(r"^confronta\s+(.+?)\s+(?:vs|contro|versus)\s+(.+)$", re.I)),
    ("scn", re.compile(r"^scenario\s+(.+)$", re.I)),
    ("hist", re.compile(r"^storico\s+(.+)$", re.I)),
    ("who", re.compile(r"^chi\s+compra\s+\$?(\S+)\s*$", re.I)),
    ("dd", re.compile(r"^deep\s*dive(?:\s+su)?\s+(.+)$", re.I)),
]


def parse(text):
    t = text.strip()
    for kind, rx in PATTERNS:
        m = rx.match(t)
        if m:
            return kind, [g.strip() for g in m.groups()]
    return None


# ------------------------------------------------------------------ raccolta fatti
def _items_for(term, days=14, regional=True):
    """Notizie sul termine da Google/Bing + feed principali, deduplicate e ordinate."""
    jobs = [core._pool.submit(core.fetch_google, term, "en-US", "US", "US:en", "crypto", "en", "US", "Google News", days)]
    if regional:
        jobs += [core._pool.submit(core.fetch_google, term, "iw", "IL", "IL:iw", "politica", "he", "IL", "Google News IL", days),
                 core._pool.submit(core.fetch_google, term, "ko", "KR", "KR:ko", "crypto", "ko", "ASIA", "Google News KR", days),
                 core._pool.submit(core.fetch_google, term, "ja", "JP", "JP:ja", "crypto", "ja", "ASIA", "Google News JP", days),
                 core._pool.submit(core.fetch_google, term, "zh-CN", "CN", "CN:zh-Hans", "crypto", "zh", "ASIA", "Google News CN", days)]
    items = []
    for j in jobs:
        items += j.result() or []
    words = [w for w in re.findall(r"[a-z0-9$]{3,}", term.lower())]
    feeds = core.collect({})
    items += [i for i in feeds if words and all(w in i["title"].lower() for w in words[:2])]
    items.sort(key=lambda i: i["pub"])
    seen, out = set(), []
    for it in items:
        k = core.cluster_key(it["title"])
        if k in seen:
            continue
        seen.add(k)
        out.append(it)
    return out


def _fmt_items(items, limit=14):
    now = NOW()
    rows = []
    for it in sorted(items, key=lambda i: (i["tier"], -i["pub"].timestamp()))[:limit]:
        title = core.translate(it["title"], it["lang"]) if it["lang"] not in ("en", "it") else it["title"]
        rows.append({"data": it["pub"].strftime("%Y-%m-%d %H:%M UTC"), "da_quanto": core.humanize(it["pub"], now),
                     "titolo": title, "fonte": it["source"], "tier": it["tier"], "lingua": it["lang"], "link": it["link"]})
    return sorted(rows, key=lambda r: r["data"])


def coin_facts(q, chain=None):
    w = core.wl_load()
    key = q.upper().lstrip("$")
    c = w.get(key)
    d = core.dex_lookup((c or {}).get("address") or q, chain or (c or {}).get("chain"))
    if not d:
        return {"ticker": key, "trovata": False, "nota": "ticker non trovato su DexScreener (non verificato)"}
    out = {"ticker": d["ticker"], "nome": d["name"], "chain": d["chain"], "contratto": d["address"],
           "prezzo_usd": d["price"], "market_cap": d["mc"], "liquidita_usd": d["liq"], "volume_24h": d["vol24"],
           "variazione_24h_pct": d["ch24"], "dexscreener": d["url"], "in_watchlist": bool(c)}
    a = core.ath_info(d["chain"], d["pair"])
    if a:
        out.update({"ath_usd": a[0], "ath_data": a[1].strftime("%Y-%m-%d"), "drawdown_pct": round((1 - d["price"] / a[0]) * 100, 1),
                    "minimo_14g": a[2], "prezzo_vs_minimo_14g": round(d["price"] / a[2], 2)})
    else:
        out["ath"] = "non verificato (GeckoTerminal non disponibile per questo pool)"
    if c:
        out.update({"narrativa_tua": c.get("narrative"), "catalyst": c.get("catalyst_text"), "catalyst_data": c.get("catalyst_date")})
    if d["chain"] == "solana":
        out["holder_top"] = solana_holders(d["address"])
    return out


def solana_holders(mint):
    """Top 10 holder dal RPC pubblico di Solana (gratis). Non distingue smart money: dati grezzi."""
    try:
        rpc = "https://api.mainnet-beta.solana.com"
        a = requests.post(rpc, json={"jsonrpc": "2.0", "id": 1, "method": "getTokenLargestAccounts", "params": [mint]}, timeout=20).json()
        s = requests.post(rpc, json={"jsonrpc": "2.0", "id": 2, "method": "getTokenSupply", "params": [mint]}, timeout=20).json()
        supply = float(s["result"]["value"]["uiAmount"])
        top = [{"account": x["address"][:6] + "…" + x["address"][-4:], "pct_supply": round(float(x["uiAmount"]) / supply * 100, 2)}
               for x in a["result"]["value"][:10]]
        return {"top10": top, "somma_top10_pct": round(sum(t["pct_supply"] for t in top), 1),
                "nota": "include pool di liquidità e wallet exchange: non è una lista di smart money"}
    except Exception as e:
        return f"non verificato (RPC non raggiungibile: {type(e).__name__})"


def gather(kind, args, idx):
    f = {"tipo": kind, "oggi": datetime.now().strftime("%Y-%m-%d %H:%M")}
    if kind == "n":
        n = int(args[0])
        it = next((x for x in idx if x["n"] == n), None)
        if not it:
            return None, f"Nel brief di oggi non c'è la voce #{n}. Chiedi /brief per rigenerarlo."
        f["voce_brief"] = it
        term = it["title"]
        f["notizie"] = _fmt_items(_items_for(" ".join(re.findall(r"[A-Za-z0-9$]{3,}", term)[:8])))
        for t in set(re.findall(r"\$([A-Za-z]{2,10})\b", term)):
            f.setdefault("coin", []).append(coin_facts(t))
    elif kind == "wl":
        f["coin"] = [coin_facts(args[0])]
        f["notizie"] = _fmt_items(_items_for(f["coin"][0].get("nome") or args[0]))
    elif kind in ("dd", "scn", "hist"):
        term = args[0]
        f["oggetto"] = term
        f["notizie"] = _fmt_items(_items_for(term, days=30 if kind == "hist" else 14))
        w = core.wl_load()
        tk = [t for t in w if re.search(r"\b" + re.escape(t) + r"\b", term, re.I)]
        if tk or re.fullmatch(r"\$?[A-Za-z]{2,10}", term):
            f["coin"] = [coin_facts(tk[0] if tk else term)]
        f["watchlist"] = {t: {k: v for k, v in c.items() if k in ("chain", "narrative", "keywords", "catalyst_text", "catalyst_date")}
                          for t, c in w.items()}
    elif kind == "cmp":
        f["oggetto_A"], f["oggetto_B"] = args
        f["notizie_A"] = _fmt_items(_items_for(args[0]), 10)
        f["notizie_B"] = _fmt_items(_items_for(args[1]), 10)
    elif kind == "who":
        f["coin"] = [coin_facts(args[0])]
        f["notizie"] = _fmt_items(_items_for(args[0], regional=False), 8)
    return f, None


# ------------------------------------------------------------------ LLM
PROMPT = """Sei un analista crypto/politico che scrive per un trader di meme coin OG "morte" (market cap quasi zero, molto sotto l'ATH, narrativa forte, catalyst datato). Rispondi SOLO in italiano. Tono diretto e operativo.
REGOLE ASSOLUTE:
- Usa ESCLUSIVAMENTE i FATTI forniti sotto (JSON). Non inventare date, fonti, dichiarazioni, contract address, percentuali, precedenti storici. Se un dato non è nei fatti scrivi "non verificato" e perché.
- Cita le fonti con nome e tier e link presi dai fatti. Se le fonti si contraddicono riporta entrambe e dì quale ha tier migliore.
- Distingui annuncio ORIGINALE da RIESUMATO guardando le date.
- Nessuna previsione certa e nessun consiglio finanziario: scenari e probabilità qualitative con motivazione.
- Segnali on-chain: dai solo quelli presenti nei fatti; per il resto indica dove guardare gratis (GMGN, Arkham free, Solscan, DEXScreener) senza inventare valori.
RICHIESTA: {richiesta}
STRUTTURA OBBLIGATORIA (titoli in markdown ##):
## 🔍 DEEP DIVE — oggetto — data
Sintesi in 3 righe con verdetto preliminare.
## Cronologia (tabella: data | cosa | chi | fonte | tier; segna originale/riesumato)
## Stato attuale (catalyst: data, countdown, cosa cambia, probabilità alta/media/bassa motivata; persona: ultime dichiarazioni e pattern; coin: ATH, drawdown, MC, liquidità, volume, holder, segnali)
## Mappatura narrativa→coin (ticker | chain | ATH | drawdown | MC | liquidità | catalyst | verdetto) — se nessuna coin è esposta, scrivilo
## Segnali di risveglio da monitorare (supply top holder, afflussi smart money, volume) e dove vederli gratis
## Precedente storico (solo se nei fatti, altrimenti "non verificato")
## Catalyst già prezzato? (Sì/No/Parzialmente; se sì "late")
## Fase del ciclo narrativo (emerging/rising/peak/fading) con motivazione
## Scenari (bull / base / bear) con impatto, tempistiche, segnali
## Red flag (unlock, dev, liquidità sottile, già prezzato, narrativa non datata, fonte inaffidabile)
## Verdetto finale
Scrivi su una riga: VERDETTO: Da monitorare | Pronta (3 conferme) | Da evitare — poi 3 righe di motivazione. Se "Pronta" indica le 3 conferme (news + movimento prezzo + conferma on-chain) e time stop 1-2 settimane.
## Fonti (elenco con link, tier, data)
Ultima riga, da sola: CORREZIONE: <testo se una news del brief è risultata inaffidabile/smentita, altrimenti "nessuna">
FATTI (JSON):
{fatti}
"""

REQ = {"n": "Approfondisci la voce #{0} del brief di oggi.", "wl": "Deep dive sulla coin in watchlist {0}.",
       "dd": "Deep dive su: {0}.", "scn": "Analisi scenari (bull/base/bear) su: {0}. Dai più peso alla sezione Scenari.",
       "hist": "Backtesting narrativo su: {0}: quante volte ha pompato, su quali coin, con quali %, SOLO se presente nei fatti.",
       "cmp": "Confronta A='{0}' vs B='{1}': data, impatto, coin mappate, benzina narrativa, quale è più interessante per la strategia.",
       "who": "Analisi dei wallet che comprano/vendono {0}: usa solo i dati holder forniti e dì chiaramente cosa non si può sapere gratis."}


def pick_models(key):
    """Elenco ordinato di modelli flash disponibili (il piu' recente prima), per riprovare se uno e' sovraccarico."""
    if _model.get("list"):
        return _model["list"]
    names = []
    try:
        r = requests.get("https://generativelanguage.googleapis.com/v1beta/models", params={"key": key, "pageSize": 100}, timeout=20).json()
        names = [m["name"].split("/")[-1] for m in r.get("models", [])
                 if "generateContent" in m.get("supportedGenerationMethods", []) and "flash" in m["name"]
                 and not any(x in m["name"] for x in ("image", "tts", "live", "thinking", "exp", "preview", "audio"))]
        names.sort(key=lambda n: ([int(x) for x in re.findall(r"\d+", n)], "lite" not in n), reverse=True)
    except Exception:
        pass
    _model["list"] = (names[:4] or ["gemini-2.0-flash"])
    return _model["list"]


def llm(prompt):
    key = GEMINI_KEY()
    if not key:
        raise RuntimeError("NO_KEY")
    last = ""
    for model in pick_models(key):
        for attempt in range(2):
            try:
                r = requests.post(f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent",
                                  params={"key": key},
                                  json={"contents": [{"parts": [{"text": prompt}]}],
                                        "generationConfig": {"temperature": 0.25, "maxOutputTokens": 8192}}, timeout=180)
            except Exception as e:
                last = f"{model}: {type(e).__name__}"
                time.sleep(4)
                continue
            if r.status_code == 200:
                try:
                    return "".join(p.get("text", "") for p in r.json()["candidates"][0]["content"]["parts"])
                except Exception:
                    last = f"{model}: risposta vuota"
                    break
            if r.status_code == 429:
                last = f"{model}: quota"
                break                      # prova il modello successivo (ogni modello ha la sua quota)
            last = f"{model}: {r.status_code}"
            if r.status_code in (500, 502, 503, 504):
                time.sleep(6)
                continue
            break
    if "quota" in last:
        raise RuntimeError("QUOTA")
    raise RuntimeError("Gemini non disponibile adesso (" + last + "). Riprova tra qualche minuto: la quota di oggi non è stata consumata.")


def md_to_html(t):
    t = html.escape(t)
    t = re.sub(r"^#{1,4}\s*(.+)$", r"<b>\1</b>", t, flags=re.M)
    t = re.sub(r"\*\*(.+?)\*\*", r"<b>\1</b>", t)
    t = re.sub(r"(?<!\*)\*(?!\s)(.+?)(?<!\s)\*(?!\*)", r"<i>\1</i>", t)
    return t


# ------------------------------------------------------------------ quota e archivio
def quota_use():
    st = jload("dd_quota.json", {})
    day = datetime.now().strftime("%Y-%m-%d")
    st = {day: st.get(day, 0) + 1}
    jsave("dd_quota.json", st)


def quota_remaining():
    st = jload("dd_quota.json", {})
    return PER_DAY - st.get(datetime.now().strftime("%Y-%m-%d"), 0)


def run(kind, args):
    """Ritorna lista di messaggi HTML per Telegram."""
    if not GEMINI_KEY():
        return ["⚠️ Deep dive non attivo: manca la chiave <code>GEMINI_API_KEY</code> (gratuita su aistudio.google.com)."]
    if quota_remaining() <= 0:
        return ["Quota deep dive esaurita per oggi, riprova domani o usa il brief standard."]
    idx = jload("brief_index.json", [])
    facts, err = gather(kind, args, idx)
    if err:
        return [err]
    req = REQ[kind].format(*args)
    try:
        text = llm(PROMPT.format(richiesta=req, fatti=json.dumps(facts, ensure_ascii=False, default=str)[:60000]))
    except RuntimeError as e:
        if str(e) == "QUOTA":
            return ["Quota deep dive esaurita per oggi (limite Gemini), riprova domani o usa il brief standard."]
        return [f"⚠️ Errore deep dive: {esc(str(e))[:300]}"]
    quota_use()
    m = re.search(r"VERDETTO:\s*(Da monitorare|Pronta[^\n]*|Da evitare)", text, re.I)
    verdict = m.group(1).strip() if m else "n/d"
    corr = re.search(r"CORREZIONE:\s*(.+)$", text.strip(), re.I | re.M)
    corr = corr.group(1).strip() if corr and not corr.group(1).strip().lower().startswith("nessuna") else ""
    obj = " / ".join(args)
    arch = jload("deepdives.json", [])
    arch.append({"data": datetime.now().strftime("%Y-%m-%d %H:%M"), "oggetto": obj, "tipo": kind, "verdetto": verdict,
                 "correzione": corr, "report": text})
    jsave("deepdives.json", arch[-60:])
    body = md_to_html(re.sub(r"\n?CORREZIONE:.*$", "", text.strip(), flags=re.I))
    out, cur = [], ""
    for para in body.split("\n\n"):
        if len(cur) + len(para) + 2 > 3800:
            out.append(cur)
            cur = ""
        cur += para + "\n\n"
    out.append(cur)
    out[-1] += f"\n<i>Deep dive salvato · verdetto: {esc(verdict)} · restanti oggi: {quota_remaining()}</i>"
    return out


def archive_text(n=10):
    arch = jload("deepdives.json", [])[-n:]
    if not arch:
        return "Nessun deep dive salvato."
    return "<b>📚 Ultimi deep dive</b>\n" + "\n".join(f'• {a["data"]} · <b>{esc(a["oggetto"][:60])}</b> → {esc(a["verdetto"])}' for a in reversed(arch))


def brief_blocks():
    """Per il brief: deep dive 'Pronta' e correzioni degli ultimi 3 giorni."""
    arch = jload("deepdives.json", [])
    cutoff = (datetime.now() - timedelta(days=3)).strftime("%Y-%m-%d %H:%M")
    ready = [a for a in arch if a["data"] >= cutoff and a["verdetto"].lower().startswith("pronta")]
    corr = [a for a in arch if a["data"] >= cutoff and a.get("correzione")]
    out = []
    for a in ready:
        out.append(f'🚨 <b>Deep dive «Pronta»:</b> {esc(a["oggetto"][:80])} ({a["data"][:10]}) — rileggi il report con /archivio. Aggiorna il countdown.')
    for a in corr:
        out.append(f'✏️ <b>Correzione:</b> {esc(a["correzione"][:200])} (da deep dive del {a["data"][:10]})')
    return out
