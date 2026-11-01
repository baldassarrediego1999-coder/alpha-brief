"""
% di rilevanza (0-100) su 5 criteri da 20 punti + riconoscimento Huge Announcement.
Una sola chiamata a Gemini valuta in blocco tutte le voci del brief (risultati in cache 20 ore).
Se Gemini non risponde: stima automatica dalle regole, segnalata come tale.
"""
import json, re, time

import core, deepdive
from core import NOW, jload, jsave

CRIT = ("Forza narrativa", "Databilità catalyst", "Mappatura coin OG", "Segnali on-chain", "Tempestività")
KEYS = ("fn", "dc", "mc", "oc", "tp")
CACHE_H = 20


def band(p):
    return "🔴" if p >= 80 else "🟡" if p >= 60 else "🟢" if p >= 40 else "⚫"


def heuristic(score):
    return int(max(0, min(100, score * 5)))


PROMPT = """Valuta quanto ogni voce è rilevante per questa strategia: trading manuale di meme coin OG "morte" (market cap e volume quasi zero, prezzo molto sotto l'ATH) con narrativa forte e riconoscibile, catalyst IMMINENTE e DATATO e segnale on-chain di risveglio prima dell'hype. La tempestività è tutto: una news vecchia o già prezzata vale poco.
Per OGNI voce dai 5 voti interi 0-20:
fn = Forza narrativa (si spiega in una frase? riconoscibile? virale?)
dc = Databilità del catalyst (data precisa e imminente = alto; vago = basso; nessun catalyst = 0-4)
mc = Mappatura coin OG (se "coin_watchlist" non è vuoto fino a 20; altrimenti stima plausibile di meme OG esposte, massimo 12)
oc = Segnali on-chain (NON hai dati on-chain: assegna massimo 6 e solo se la voce stessa parla di whale/smart money/wallet)
tp = Tempestività (eta_ore bassa e catalyst futuro = alto; notizia vecchia, riesumata o già successa = basso)
hc = elenco (numeri) dei criteri "Huge Announcement" soddisfatti dalla voce, tra: 1 hard fork/mainnet/upgrade di una blockchain Tier 1-2 (anche lancio di funzioni importanti di Pump.fun, Base, Solana...) · 2 partnership strategica tra nomi Tier 1 · 3 listing su exchange Tier 1 (Binance, Coinbase, Kraken, OKX, Bybit) · 4 decisione regolatoria storica (ETF, leggi su crypto/stablecoin/CBDC) · 5 acquisizione o fusione tra aziende crypto rilevanti · 6 token unlock enorme (>5% supply) · 7 evento macro di settore (Fed, BCE, elezioni USA) · 8 personaggio Tier 1 con annuncio storico (CZ, Justin Sun, Musk, Trump, Netanyahu, Eisenkot, CEO BlackRock). Sii rigoroso: lista vuota se non soddisfa nulla.
cn = NEWS CONNECTOR: da 1 a 3 punti (stringhe italiane, max 110 caratteri ciascuna, formato "Etichetta: collegamento + motivo") che collegano la voce a ciò che conta per la strategia: narrativa in corso, catalyst/data, coin OG o watchlist esposte, già prezzato, red flag. Includi SOLO i punti pertinenti; se un dato non è nei campi forniti scrivi "non verificato" invece di inventarlo; se nessun collegamento è pertinente usa ["nessun collegamento rilevante"].
map = una catena sintetica di massimo 5 nodi separati da " → " (es. "Fed taglia tassi → più liquidità → rischio on → meme OG"); stringa vuota se non ha senso.
REGOLE: non inventare nulla; usa solo i campi forniti; "why" è una frase in italiano di massimo 140 caratteri che motiva il punteggio (cita il fattore decisivo).
Rispondi SOLO con un array JSON: [{"id":"...","fn":0,"dc":0,"mc":0,"oc":0,"tp":0,"hc":[],"why":"...","cn":["..."],"map":"..."}]
VOCI:
"""


def _call(entries):
    txt = deepdive.llm(PROMPT + json.dumps(entries, ensure_ascii=False), json_mode=True)
    txt = re.sub(r"^```(?:json)?|```$", "", txt.strip(), flags=re.M).strip()
    arr = json.loads(txt)
    out = {}
    for r in arr:
        v = [max(0, min(20, int(r.get(k, 0)))) for k in KEYS]
        hc = [int(x) for x in (r.get("hc") or []) if str(x).isdigit() and 1 <= int(x) <= 8]
        cn = [str(x)[:160] for x in (r.get("cn") or []) if str(x).strip()][:3] or ["nessun collegamento rilevante"]
        out[str(r["id"])] = {"crit": v, "pct": sum(v), "why": str(r.get("why", ""))[:200], "hc": hc,
                             "cn": cn, "map": str(r.get("map", ""))[:160]}
    return out


def fallback_cn(e):
    """NEWS CONNECTOR a regole (senza Gemini): usa solo i campi che il brief conosce."""
    pts = []
    if e.get("temi"):
        pts.append("Narrativa: " + ", ".join(e["temi"][:2]) + " (da parole chiave del titolo)")
    if e.get("catalyst"):
        pts.append(f"Catalyst: {e['catalyst']} (data nel titolo, non verificata su fonte primaria)")
    elif e.get("countdown_giorni") is not None:
        pts.append(f"Catalyst: tra {e['countdown_giorni']} giorni")
    if e.get("coin_watchlist"):
        pts.append("Coin watchlist: " + ", ".join(e["coin_watchlist"]))
    return pts[:3] or ["nessun collegamento rilevante verificabile (Gemini non disponibile)"]


def score(entries):
    """entries: [{id,key,...}] -> {id: {pct, crit, why, auto}}; salva in cache per cluster key."""
    cache = jload("scores.json", {})
    now = time.time()
    res, todo = {}, []
    for e in entries:
        c = cache.get(e["key"])
        if c and now - c["ts"] < CACHE_H * 3600 and c.get("pct") is not None and not c.get("auto") and c.get("cn"):
            res[e["id"]] = c
        else:
            todo.append(e)
    llm_ok = None
    if todo and deepdive.GEMINI_KEY():
        todo.sort(key=lambda e: -e.get("h", 0))
        for i in range(0, min(len(todo), 60), 30):          # fino a 2 chiamate da 30 voci
            batch = todo[i:i + 30]
            try:
                out = _call([{k: v for k, v in e.items() if k not in ("key", "h")} for e in batch])
                llm_ok = True
                for e in batch:
                    r = out.get(e["id"])
                    if r:
                        r["ts"] = now
                        cache[e["key"]] = r
                        res[e["id"]] = r
            except Exception as ex:
                llm_ok = False
                print("relevance LLM errore:", repr(ex)[:200], flush=True)
                break
    for e in entries:
        if e["id"] not in res:
            res[e["id"]] = {"pct": heuristic(e.get("h", 0)), "crit": None, "auto": True, "cn": fallback_cn(e), "map": "",
                            "why": "stima automatica dalle regole (valutazione Gemini non disponibile)"}
    keep = {k: v for k, v in cache.items() if now - v.get("ts", 0) < 3 * 86400}
    jsave("scores.json", keep)
    return res, llm_ok


# ------------------------------------------------------------------ huge announcements
T1 = (r"ethereum|bitcoin|solana|cardano|stellar|zcash|chainlink|ripple|xrp|layerzero|unichain|chia|\bbase\b|pump\.?fun|"
      r"jupiter|binance|coinbase|kraken|okx|bybit|blackrock|fidelity|visa|stripe|robinhood|jpmorgan|circle|tether|paypal")
HUGE = [
    ("hard fork / mainnet / upgrade", re.compile(r"hard ?fork|mainnet|protocol upgrade|network upgrade|\bupgrade\b", re.I), True),
    ("partnership Tier 1", re.compile(r"partner|integrat|collaborat|teams? up", re.I), True),
    ("listing exchange Tier 1", re.compile(r"\blist(?:s|ing|ed)?\b|will list", re.I), True),
    ("decisione regolatoria", re.compile(r"\betf\b|approv|\bsec\b|\bbill\b|\blaw\b|regulat|\bban\b|cbdc|stablecoin act|\bmica\b|executive order", re.I), False),
    ("acquisizione / fusione", re.compile(r"acquir|acquisition|merger|takeover|buys\b", re.I), False),
    ("token unlock enorme", re.compile(r"token unlock|unlock(?:s|ing)? .{0,25}(?:token|supply)|(?:token|supply).{0,25}unlock", re.I), False),
    ("evento macro", re.compile(r"fomc|\bfed\b|\becb\b|rate (?:cut|hike|decision)|election|midterm|elezioni", re.I), False),
    ("personaggio Tier 1", re.compile(r"\bcz\b|changpeng|justin sun|\bmusk\b|\btrump\b|netanyahu|eisenkot|larry fink", re.I), False),
]
T1_RX = re.compile(T1, re.I)


def huge_criteria(title):
    hit = []
    if not (core.CRYPTO_RE.search(title) or T1_RX.search(title)):
        return hit                      # un huge deve riguardare il mondo crypto
    for name, rx, needs_t1 in HUGE:
        if rx.search(title) and (not needs_t1 or T1_RX.search(title)):
            hit.append(name)
    return hit


HC_NAMES = ["", "hard fork / mainnet / upgrade", "partnership Tier 1", "listing exchange Tier 1", "decisione regolatoria",
            "acquisizione / fusione", "token unlock enorme", "evento macro", "personaggio Tier 1"]


def huge_of(title, sc):
    """Lista criteri huge soddisfatti: giudizio Gemini se disponibile, altrimenti regole sul titolo."""
    if sc and not sc.get("auto") and sc.get("hc") is not None:
        return [HC_NAMES[i] for i in sc["hc"]]
    return huge_criteria(title)
