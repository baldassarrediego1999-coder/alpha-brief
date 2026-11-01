"""
Motore del Brief: raccoglie notizie e calendari da fonti gratuite, deduplica,
classifica (tier, impatto, urgenza), mappa alla watchlist e costruisce il brief.
Niente viene inventato: quello che non si riesce a verificare e' scritto "non verificato".
"""
import hashlib, html, json, os, re, threading, time, urllib.parse
import xml.etree.ElementTree as ET
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime

import requests

HERE = os.path.dirname(os.path.abspath(__file__))
UA = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/131.0 Safari/537.36"}
NOW = lambda: datetime.now(timezone.utc)
_pool = ThreadPoolExecutor(12)
_lock = threading.Lock()


# ------------------------------------------------------------------ file di stato
DATA_DIR = os.environ.get("DATA_DIR", HERE)      # in cloud: cartella persistente
os.makedirs(DATA_DIR, exist_ok=True)


def _path(name):
    return os.path.join(DATA_DIR, name)


def jload(name, default):
    try:
        return json.load(open(_path(name), encoding="utf-8"))
    except Exception:
        return default


def jsave(name, data):
    with _lock:
        json.dump(data, open(_path(name), "w", encoding="utf-8"), ensure_ascii=False, indent=1)


# ------------------------------------------------------------------ fonti
# (nome, url, tier, categoria, lingua, regione)
FEEDS = [
    ("CoinDesk", "https://www.coindesk.com/arc/outboundfeeds/rss", 1, "crypto", "en", "US"),
    ("The Block", "https://www.theblock.co/rss.xml", 1, "crypto", "en", "US"),
    ("Decrypt", "https://decrypt.co/feed", 2, "crypto", "en", "US"),
    ("Cointelegraph", "https://cointelegraph.com/rss", 2, "crypto", "en", "US"),
    ("Times of Israel", "https://www.timesofisrael.com/feed/", 1, "politica", "en", "IL"),
    ("Jerusalem Post", "https://www.jpost.com/rss/rssfeedsheadlines.aspx", 2, "politica", "en", "IL"),
    ("Haaretz", "https://www.haaretz.com/srv/haaretz-latest-headlines", 1, "politica", "en", "IL"),
    ("Ynet", "https://www.ynet.co.il/Integration/StoryRss2.xml", 2, "politica", "he", "IL"),
    ("SCMP", "https://www.scmp.com/rss/91/feed", 1, "politica", "en", "ASIA"),
    ("Nikkei Asia", "https://asia.nikkei.com/rss/feed/nar", 1, "politica", "en", "ASIA"),
    ("BBC World", "https://feeds.bbci.co.uk/news/world/rss.xml", 1, "politica", "en", "WORLD"),
    ("Fed", "https://www.federalreserve.gov/feeds/press_all.xml", 1, "macro", "en", "US"),
    ("BCE", "https://www.ecb.europa.eu/rss/press.html", 1, "macro", "en", "EU"),
]
# ricerche Google News regionali (tradotte): (nome, query, hl, gl, ceid, categoria, lingua, regione)
REGIONAL = [
    ("Google News IL", "קריפטו OR ביטקוין", "iw", "IL", "IL:iw", "crypto", "he", "IL"),
    ("Google News IL", "נתניהו OR איזנקוט OR בחירות", "iw", "IL", "IL:iw", "politica", "he", "IL"),
    ("Google News KR", "비트코인 OR 암호화폐 OR 밈코인", "ko", "KR", "KR:ko", "crypto", "ko", "ASIA"),
    ("Google News JP", "暗号資産 OR ビットコイン", "ja", "JP", "JP:ja", "crypto", "ja", "ASIA"),
    ("Google News CN", "加密货币 OR 比特币 监管", "zh-CN", "CN", "CN:zh-Hans", "crypto", "zh", "ASIA"),
    ("Korea Herald", "site:koreaherald.com crypto OR bitcoin OR election", "en", "KR", "KR:en", "politica", "en", "ASIA"),
]
# ricerche tematiche crypto (catalyst, narrative, whale)
TOPIC_QUERIES = [
    ("meme coin OG narrative OR \"memecoin\" rally OR revival", "crypto"),
    ("hard fork OR mainnet launch OR testnet OR upgrade date crypto", "crypto"),
    ("token unlock OR airdrop OR \"token sale\" OR TGE date", "crypto"),
    ("Binance listing OR Coinbase listing OR Upbit listing OR Robinhood listing", "crypto"),
    ("whale OR \"smart money\" OR \"dormant wallet\" crypto", "crypto"),
    ("Trump crypto OR stablecoin OR CBDC OR SEC crypto regulation", "politica"),
    ("election OR vote OR summit OR sanctions OR ceasefire", "politica"),
]
TIER1_SRC = ("coindesk", "the block", "reuters", "associated press", "ap news", "bbc", "times of israel", "haaretz",
             "scmp", "south china morning", "nikkei", "korea herald", "bloomberg", "financial times", "wsj", "wall street journal")
TIER2_SRC = ("cointelegraph", "decrypt", "blockworks", "the defiant", "dl news", "cryptoslate", "jerusalem post",
             "cnbc", "forbes", "cryptonews", "coinmarketcap", "coingecko", "ynet", "yonhap", "japan times", "cnn", "fox")

# persone: (nome, query Google News, categoria, nota)
PERSON_MUST = {"Ansem (@blknoiz06)": r"ansem|blknoiz", "Halon": r"halon", "Fanfan": r"fanfan", "CZ": r"\bcz\b|changpeng",
               "Justin Sun": r"justin sun", "Elon Musk": r"musk", "Bonk Guy": r"bonk", "TheWhiteWhaleV2": r"whale",
               "Trump": r"trump", "Netanyahu": r"netanyahu|bibi", "Eisenkot": r"eisenkot|eizenkot",
               "Leader europei": r"macron|merz|starmer|meloni", "Leader asiatici": r"xi jinping|\bxi\b|takaichi|prime minister|lee jae"}
PERSONS = [
    ("Ansem (@blknoiz06)", "Ansem blknoiz06 crypto", "crypto", ""),
    ("Halon", "Halon crypto trader memecoin", "crypto", "handle X da verificare"),
    ("Fanfan", "Fanfan crypto trader memecoin", "crypto", "handle X da verificare"),
    ("CZ", "\"Changpeng Zhao\" OR \"CZ\" Binance", "crypto", ""),
    ("Justin Sun", "\"Justin Sun\"", "crypto", ""),
    ("Elon Musk", "Elon Musk crypto OR dogecoin OR bitcoin", "crypto", ""),
    ("Bonk Guy", "\"Bonk guy\" OR bonk Solana founder", "crypto", ""),
    ("TheWhiteWhaleV2", "TheWhiteWhaleV2 OR \"white whale\" crypto trader", "crypto", "handle X da verificare"),
    ("Trump", "Trump crypto OR bitcoin OR stablecoin", "politica", ""),
    ("Netanyahu", "Netanyahu", "politica", ""),
    ("Eisenkot", "Eisenkot", "politica", ""),
    ("Leader europei", "Macron OR Merz OR Starmer OR Meloni crypto OR regulation OR economy", "politica", ""),
    ("Leader asiatici", "Xi Jinping OR \"Japan prime minister\" OR \"South Korea president\" economy OR crypto", "politica", ""),
]

# ------------------------------------------------------------------ parole chiave
CRYPTO_RE = re.compile(r"crypto|bitcoin|\bbtc\b|ethereum|\beth\b|solana|stablecoin|cbdc|token|coin\b|blockchain|defi|"
                       r"binance|coinbase|airdrop|memecoin|meme coin|\bsec\b.*(crypto|etf)|etf", re.I)
HIGH_KW = re.compile(r"hard ?fork|mainnet|upgrade|listing|lists?\b|unlock|airdrop|tge|etf|approv|ban(s|ned)?\b|lawsuit|"
                     r"hack|exploit|rug|delist|whale|acquir|partnership|launch|tariff|sanction|war\b|ceasefire|election|"
                     r"rate (cut|hike)|interest rate|fomc|stablecoin|cbdc|executive order|resign|coalition|vote", re.I)
RED_KW = re.compile(r"\bhack(?:ed|s|ers?)?\b|exploit|rug ?pull|delist|unlock|lawsuit|sues|ban(s|ned)?\b|crackdown|investigation|"
                    r"sell-?off|dump|liquidat|insolven|bankrupt|freeze|seiz", re.I)
FUTURE_KW = re.compile(r"\b(will|plans? to|set to|to (launch|go live|activate|list|unlock|release|vote|hold)|"
                       r"scheduled|expected|upcoming|slated|ahead of|countdown|proposal|deadline|vote on|next (week|month))\b", re.I)
EVENT_CAT = [
    ("Protocollo", re.compile(r"hard ?fork|mainnet|testnet|upgrade|protocol|v\d+|activation|network update", re.I)),
    ("Listing", re.compile(r"\blist(ing|s|ed)?\b|will list|delist", re.I)),
    ("Unlock", re.compile(r"unlock|vesting", re.I)),
    ("Airdrop/TGE", re.compile(r"airdrop|\btge\b|token sale|\bido\b|\bico\b|snapshot", re.I)),
    ("Conferenza", re.compile(r"conference|summit|breakpoint|swell|meridian|blockchain week|devcon|accelerate|expo", re.I)),
    ("Politica", re.compile(r"election|vote|referendum|parliament|knesset|congress|senate|midterm|coalition", re.I)),
    ("Macro", re.compile(r"fomc|\bfed\b|ecb|\bboj\b|\bboe\b|rate decision|cpi|inflation|jobs report|nfp|gdp", re.I)),
]
THEMES = {
    "Meme / OG revival": re.compile(r"meme ?coin|\bmeme\b|\bpepe\b|\bdoge|shib|bonk|wif\b|pump\.fun|og coin", re.I),
    "Politica USA (Trump)": re.compile(r"trump|maga|white house|executive order|truth social", re.I),
    "Israele / Medio Oriente": re.compile(r"israel|netanyahu|eisenkot|gaza|iran|knesset|hezbollah|idf", re.I),
    "Privacy / Zcash": re.compile(r"zcash|privacy coin|\bzec\b|monero|zk\b", re.I),
    "AI": re.compile(r"\bai\b|artificial intelligence|agent|llm|openai|nvidia", re.I),
    "RWA / Stablecoin": re.compile(r"\brwa\b|tokeniz|stablecoin|usdc|usdt|cbdc", re.I),
    "ETF / Istituzionali": re.compile(r"\betf\b|blackrock|fidelity|grayscale|institutional|treasury", re.I),
    "Regolamentazione": re.compile(r"\bsec\b|regulat|clarity act|genius act|mica|lawsuit|compliance", re.I),
    "Solana / Pump.fun": re.compile(r"solana|pump\.fun|alpenglow|elysium|jupiter|raydium", re.I),
    "Cina / Asia": re.compile(r"china|hong kong|japan|korea|upbit|binance korea|asia", re.I),
}
STOP = set("the a an and or of to in on for with at by from as is are be this that will after before over into new "
           "says say said how why what when who its it's his her their than more less up down out about amid".split())


# ------------------------------------------------------------------ utilita'
def humanize(dt, now=None):
    now = now or NOW()
    s = max(0, int((now - dt).total_seconds()))
    if s < 3600:
        return f"{max(1, s // 60)} min fa"
    if s < 86400:
        return f"{s // 3600}h fa"
    return f"{s // 86400}g fa"


def local(dt):
    return dt.astimezone().strftime("%d/%m %H:%M")


def countdown(dt, now=None):
    now = now or NOW()
    d = dt - now
    if d.total_seconds() < -86400:
        return "passato"
    if d.total_seconds() < 0:
        return "oggi"
    if d.days >= 1:
        return f"{d.days}g {d.seconds // 3600}h"
    return f"{d.seconds // 3600}h {(d.seconds % 3600) // 60}m"


def tier_of(source, default):
    s = (source or "").lower()
    if any(x in s for x in TIER1_SRC):
        return 1
    if any(x in s for x in TIER2_SRC):
        return 2
    return default


def toks(title):
    return {w for w in re.findall(r"[a-z0-9$]{3,}", title.lower()) if w not in STOP}


def cluster_key(title):
    t = sorted(toks(title), key=lambda w: (-len(w), w))[:5]
    return hashlib.md5("|".join(sorted(t)).encode()).hexdigest()[:12]


def esc(s):
    return html.escape(s or "")


# ------------------------------------------------------------------ traduzione
_tr_cache = jload("translations.json", {})


def translate(text, lang):
    if lang in ("en", "it") or not text:
        return text
    if text in _tr_cache:
        return _tr_cache[text]
    try:
        r = requests.get("https://translate.googleapis.com/translate_a/single",
                         params={"client": "gtx", "sl": "auto", "tl": "it", "dt": "t", "q": text}, headers=UA, timeout=10)
        out = "".join(p[0] for p in r.json()[0] if p and p[0])
        _tr_cache[text] = out
        return out
    except Exception:
        return text + " (non tradotto)"


# ------------------------------------------------------------------ raccolta
def _parse(content, source, tier, cat, lang, region):
    out = []
    root = ET.fromstring(content)
    ns = "{http://www.w3.org/2005/Atom}"
    rows = []
    for it in root.iter("item"):
        src = it.find("source")
        rows.append((it.findtext("title"), it.findtext("link"), it.findtext("pubDate"), src.text if src is not None else None))
    for it in root.iter(ns + "entry"):
        ln = it.find(ns + "link")
        rows.append((it.findtext(ns + "title"), ln.get("href") if ln is not None else "",
                     it.findtext(ns + "published") or it.findtext(ns + "updated"), None))
    for title, link, date, real_src in rows:
        title = html.unescape(html.unescape((title or "").strip()))
        if not title:
            continue
        try:
            pub = datetime.fromisoformat(date.replace("Z", "+00:00")) if re.match(r"\d{4}-\d{2}-\d{2}T", date or "") else parsedate_to_datetime(date)
        except Exception:
            continue
        if pub.tzinfo is None:
            pub = pub.replace(tzinfo=timezone.utc)
        s = real_src or source
        if real_src and title.endswith(" - " + real_src):
            title = title[: -len(real_src) - 3]
        out.append({"title": title, "link": (link or "").strip(), "source": s, "tier": tier_of(s, tier if not real_src else 3),
                    "cat": cat, "lang": lang, "region": region, "pub": pub})
    return out


def fetch_feed(f):
    name, url, tier, cat, lang, region = f
    try:
        r = requests.get(url, headers=UA, timeout=15)
        if r.status_code == 200:
            return _parse(r.content, name, tier, cat, lang, region)
    except Exception:
        pass
    return None


def fetch_google(q, hl, gl, ceid, cat, lang, region, name="Google News", days=3):
    url = ("https://news.google.com/rss/search?q=" + urllib.parse.quote(f"{q} when:{days}d")
           + f"&hl={hl}&gl={gl}&ceid={ceid}")
    try:
        r = requests.get(url, headers=UA, timeout=15)
        if r.status_code == 200:
            return _parse(r.content, name, 3, cat, lang, region)
    except Exception:
        pass
    # Google blocca spesso gli IP dei server cloud: ripiego su Bing News (query semplice)
    try:
        base = re.sub(r"\(.*?\)", "", q).replace('"', "").split(" OR ")[0].strip()
        r = requests.get("https://www.bing.com/news/search?q=" + urllib.parse.quote(base) + "&format=rss&setmkt=en-US",
                         headers=UA, timeout=15)
        if r.status_code == 200:
            out = _parse(r.content, "Bing News", 3, cat, "en", region)
            return [i for i in out if (NOW() - i["pub"]).days <= days]
    except Exception:
        pass
    return None


def fetch_cv():
    out = []
    try:
        j = requests.get("https://cryptocurrency.cv/api/breaking", headers=UA, timeout=15).json()
        for a in j.get("articles", []):
            try:
                pub = datetime.fromisoformat((a.get("pubDate") or a.get("publishedAt") or "").replace("Z", "+00:00"))
            except Exception:
                pub = NOW()
            if pub.tzinfo is None:
                pub = pub.replace(tzinfo=timezone.utc)
            src = a.get("source") or "cryptocurrency.cv"
            out.append({"title": a.get("title", ""), "link": a.get("link", ""), "source": src,
                        "tier": tier_of(src, 2), "cat": "crypto", "lang": "en", "region": "US", "pub": pub})
        return out
    except Exception:
        return None


def collect(report):
    """Scarica tutto in parallelo. report: dict nome-fonte -> ok/non accessibile."""
    jobs = [(f[0], _pool.submit(fetch_feed, f)) for f in FEEDS]
    jobs += [(r[0], _pool.submit(fetch_google, r[1], r[2], r[3], r[4], r[5], r[6], r[7], r[0])) for r in REGIONAL]
    jobs += [("Google News (temi)", _pool.submit(fetch_google, q, "en-US", "US", "US:en", c, "en", "US", "Google News", 3))
             for q, c in TOPIC_QUERIES]
    jobs += [("cryptocurrency.cv", _pool.submit(fetch_cv))]
    items = []
    for name, fut in jobs:
        res = fut.result()
        ok = res is not None
        report.setdefault(name, []).append(ok)
        items += res or []
    return items


# ------------------------------------------------------------------ eventi
def parse_dates(text, ref):
    """Date esplicite nel titolo (es. 'Nov. 9', '9 November'). Ritorna datetime UTC o None."""
    months = {m: i for i, m in enumerate(["jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec"], 1)}
    m = re.search(r"\b(jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)[a-z]*\.?\s+(\d{1,2})(?:st|nd|rd|th)?\b", text, re.I)
    if not m:
        m2 = re.search(r"\b(\d{1,2})(?:st|nd|rd|th)?\s+(?:of\s+)?(jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)[a-z]*\b", text, re.I)
        if not m2:
            return None
        day, mon = int(m2.group(1)), months[m2.group(2).lower()[:3]]
    else:
        day, mon = int(m.group(2)), months[m.group(1).lower()[:3]]
    try:
        d = datetime(ref.year, mon, day, 12, tzinfo=timezone.utc)
        if d < ref - timedelta(days=180):
            d = d.replace(year=ref.year + 1)
        return d
    except Exception:
        return None


def macro_events():
    out, now = [], NOW()
    for wk in ("thisweek", "nextweek"):
        try:
            for e in requests.get(f"https://nfs.faireconomy.media/ff_calendar_{wk}.json", headers=UA, timeout=15).json():
                if e.get("impact") != "High":
                    continue
                d = datetime.fromisoformat(e["date"]).astimezone(timezone.utc)
                if d < now - timedelta(hours=12):
                    continue
                prev = e.get("previous") or "-"
                fc = e.get("forecast") or "-"
                out.append({"name": f"{e['country']} · {e['title']}", "date": d, "cat": "Macro",
                            "what": f"atteso {fc} (precedente {prev})", "memes": "",
                            "source": "ForexFactory", "link": "https://www.forexfactory.com/calendar", "tier": 2})
        except Exception:
            pass
    return out


def manual_events():
    out = []
    try:                                         # eventi iniziali nel repository + quelli aggiunti con /evento (DATA_DIR)
        seed = json.load(open(os.path.join(HERE, "events.json"), encoding="utf-8"))
    except Exception:
        seed = []
    mine = jload("events.json", [])
    uniq = {(e.get("name"), e.get("date")): e for e in seed + mine}
    for e in uniq.values():
        try:
            d = datetime.fromisoformat(e["date"]).replace(tzinfo=timezone.utc)
            out.append({"name": e["name"], "date": d, "cat": e.get("cat", "Crypto"), "what": e.get("what", ""),
                        "memes": e.get("memes", ""), "source": e.get("source", "inserito da te"), "link": e.get("link", ""),
                        "tier": 1 if e.get("link") else 3})
        except Exception:
            continue
    return out


def news_events(items):
    """Eventi futuri ricavati dai titoli: richiedono una data esplicita e un verbo di futuro/evento."""
    out, now = [], NOW()
    for it in items:
        if it["cat"] == "macro":
            continue
        d = parse_dates(it["title"], it["pub"])
        if not d or d < now - timedelta(hours=12) or d > now + timedelta(days=90):
            continue
        cat = next((c for c, rx in EVENT_CAT if rx.search(it["title"])), None)
        if not cat or not (FUTURE_KW.search(it["title"]) or cat in ("Conferenza", "Politica")):
            continue
        if it["tier"] > 2:
            continue
        out.append({"name": it["title"], "date": d, "cat": cat, "what": "da titolo (verifica la fonte)", "memes": "",
                    "source": it["source"], "link": it["link"], "tier": it["tier"], "lang": it["lang"]})
    return out


# ------------------------------------------------------------------ watchlist
def wl_load():
    return jload("watchlist.json", {})


def wl_save(w):
    jsave("watchlist.json", w)


GT_NET = {"solana": "solana", "ethereum": "eth", "bsc": "bsc", "base": "base", "arbitrum": "arbitrum",
          "polygon": "polygon_pos", "avalanche": "avax", "optimism": "optimism"}


def dex_lookup(query, chain=None):
    """Ritorna dati coin da DexScreener (coppia con piu' liquidita')."""
    q = query.strip()
    if len(q) > 30:
        url = "https://api.dexscreener.com/latest/dex/tokens/" + q
    else:
        url = "https://api.dexscreener.com/latest/dex/search?q=" + urllib.parse.quote(q)
    try:
        pairs = requests.get(url, headers=UA, timeout=15).json().get("pairs") or []
    except Exception:
        return None
    if len(q) <= 30:
        pairs = [p for p in pairs if (p.get("baseToken") or {}).get("symbol", "").upper() == q.upper().lstrip("$")]
    if chain:
        pairs = [p for p in pairs if p.get("chainId") == chain.lower()]
    pairs = [p for p in pairs if p.get("priceUsd")]
    if not pairs:
        return None
    p = max(pairs, key=lambda x: (x.get("liquidity") or {}).get("usd") or 0)
    return {"ticker": p["baseToken"]["symbol"].upper(), "name": p["baseToken"].get("name", ""), "chain": p["chainId"],
            "address": p["baseToken"]["address"], "pair": p["pairAddress"], "price": float(p["priceUsd"]),
            "mc": p.get("marketCap") or p.get("fdv"), "liq": (p.get("liquidity") or {}).get("usd"),
            "vol24": (p.get("volume") or {}).get("h24"), "ch24": (p.get("priceChange") or {}).get("h24"),
            "created": p.get("pairCreatedAt"), "url": p.get("url")}


def ath_info(chain, pair):
    """ATH dalle candele giornaliere di GeckoTerminal (gratis). Ritorna (ath, data, prezzo_min_14g) o None."""
    net = GT_NET.get(chain)
    if not net:
        return None
    try:
        r = requests.get(f"https://api.geckoterminal.com/api/v2/networks/{net}/pools/{pair}/ohlcv/day",
                         params={"limit": 1000, "currency": "usd"}, headers=UA, timeout=20)
        if r.status_code != 200:
            return None
        rows = r.json()["data"]["attributes"]["ohlcv_list"]  # [ts, o, h, l, c, v]
        if not rows:
            return None
        top = max(rows, key=lambda x: x[2])
        recent = sorted(rows, key=lambda x: -x[0])[:14]
        return top[2], datetime.fromtimestamp(top[0], timezone.utc), min(x[3] for x in recent if x[3] > 0)
    except Exception:
        return None


def fmt_usd(v):
    if v is None:
        return "n/d"
    v = float(v)
    for lim, suf in ((1e9, "B"), (1e6, "M"), (1e3, "k")):
        if v >= lim:
            return f"${v / lim:.1f}{suf}"
    return f"${v:.0f}"


def wl_refresh(w):
    """Aggiorna i dati di mercato di ogni coin in watchlist."""
    def one(t):
        c = w[t]
        d = dex_lookup(c.get("address") or t, c.get("chain"))
        if not d:
            c["market_ok"] = False
            return
        c.update({k: d[k] for k in ("chain", "address", "pair", "price", "mc", "liq", "vol24", "ch24", "name")})
        c["market_ok"] = True
        a = ath_info(d["chain"], d["pair"])
        if a:
            c["ath"], c["ath_date"], c["min14"] = a[0], a[1].isoformat(), a[2]
        c["updated"] = NOW().isoformat()
    list(_pool.map(one, list(w)))
    return w


def priced_in(c):
    """Il catalyst e' gia' prezzato? Confronta prezzo attuale col minimo 14g (solo se c'e' un catalyst datato)."""
    if not c.get("catalyst_date"):
        return "n/d (nessun catalyst)"
    if not c.get("min14") or not c.get("price"):
        return "non verificato"
    r = c["price"] / c["min14"]
    return "Sì → late" if r >= 2 else "Parzialmente" if r >= 1.3 else "No"


def wl_match(item, w):
    """Quali coin in watchlist impatta la news (ticker, nome o keyword)."""
    hits = []
    t = item["title"].lower()
    for tk, c in w.items():
        words = [tk.lower(), (c.get("name") or "").lower()] + [k.lower() for k in c.get("keywords", [])]
        words = [x for x in words if len(x) >= 3]
        for x in words:
            if re.search(r"(?<![a-z0-9])" + re.escape(x) + r"(?![a-z0-9])", t):
                hits.append(tk)
                break
    return hits


# ------------------------------------------------------------------ elaborazione notizie
def process(items, w, seen, last_run):
    """Deduplica, classifica, mappa. Ritorna lista di cluster ordinati per importanza."""
    now = NOW()
    items = [i for i in items if i["pub"] <= now + timedelta(hours=2) and "gwei" not in i["title"].lower()]
    items.sort(key=lambda i: i["pub"])
    clusters = []
    for it in items:
        ts = toks(it["title"])
        placed = False
        for c in clusters:
            if it["cat"] != c["cat"] and not (it["cat"] != "macro" and c["cat"] != "macro"):
                continue
            inter = len(ts & c["toks"])
            if inter >= 4 and inter / max(1, min(len(ts), len(c["toks"]))) >= 0.55 or \
               (inter >= 3 and inter / max(1, len(ts | c["toks"])) >= 0.6):
                c["sources"].append((it["source"], it["link"], it["tier"]))
                c["pubs"].append(it["pub"])
                if it["tier"] < c["tier"]:
                    c["tier"], c["link"], c["source"] = it["tier"], it["link"], it["source"]
                placed = True
                break
        if not placed:
            clusters.append({"title": it["title"], "lang": it["lang"], "cat": it["cat"], "region": it["region"],
                             "toks": ts, "sources": [(it["source"], it["link"], it["tier"])], "pubs": [it["pub"]],
                             "tier": it["tier"], "link": it["link"], "source": it["source"]})
    out = []
    for c in clusters:
        c["pub"] = min(c["pubs"])
        c["key"] = cluster_key(c["title"])
        t = c["title"]
        c["watch"] = wl_match(c, w)
        first = seen.get(c["key"])
        age_h = (now - c["pub"]).total_seconds() / 3600
        down_h = (now - last_run).total_seconds() / 3600 if last_run else 0
        c["recovered"] = bool(last_run and not first and (c["pub"] < last_run - timedelta(minutes=30) or
                                                            (down_h > 14 and age_h > 6)))
        if first and now.timestamp() - first > 3 * 86400 and (now - max(c["pubs"])).total_seconds() < 86400:
            c["origin"] = "Riesumata"
        elif not first and age_h > 72:
            c["origin"] = "Riesumata"
        else:
            c["origin"] = "Originale"
        sc = 0
        sc += 2 * len(HIGH_KW.findall(t))
        sc += {1: 2, 2: 1, 3: 0}.get(c["tier"], 0)
        sc += min(len(c["sources"]) - 1, 3)
        sc += 4 if age_h < 6 else 2 if age_h < 24 else 0
        sc += 6 * len(c["watch"])
        if CRYPTO_RE.search(t):
            sc += 1
        d = parse_dates(t, c["pub"])
        c["when"] = d if d and d > now - timedelta(hours=12) else None
        if c["when"] and (c["when"] - now).days <= 7:
            sc += 5
        c["score"] = sc
        c["impact"] = "alto" if sc >= 9 else "medio" if sc >= 5 else "basso"
        c["red"] = bool(RED_KW.search(t))
        if c["cat"] == "politica":
            c["crypto_impact"] = "diretto" if CRYPTO_RE.search(t) else \
                "indiretto" if HIGH_KW.search(t) else "nullo"
        c["title_it"] = t
        out.append(c)
    out.sort(key=lambda c: (-c["score"], c["pub"]))
    return out


def alert_level(c, now=None):
    now = now or NOW()
    if c["watch"] or c.get("red") and c["score"] >= 5:
        return "🔴"
    if c.get("when") and (c["when"] - now).days <= 7 and c["score"] >= 5:
        return "🔴"
    if c["score"] >= 5:
        return "🟡"
    return "🟢"


def narratives(clusters_all):
    """Fase da frequenza delle menzioni: ultime 24h vs media dei 3 giorni precedenti."""
    now, res = NOW(), []
    for name, rx in THEMES.items():
        n1 = n0 = 0
        mentions = []
        for c in clusters_all:
            if rx.search(c["title"]):
                age = (now - c["pub"]).total_seconds() / 86400
                if age <= 1:
                    n1 += 1
                    mentions.append(c)
                elif age <= 4:
                    n0 += 1
        base = n0 / 3
        if n1 == 0:
            continue
        if base < 0.5 and n1 >= 2:
            ph = "emerging"
        elif n1 >= 8 and n1 >= 3 * base:
            ph = "peak"
        elif n1 > 1.5 * base:
            ph = "rising"
        elif n1 < 0.6 * base:
            ph = "fading"
        else:
            ph = "rising" if n1 >= 4 else "emerging"
        res.append({"name": name, "phase": ph, "n24": n1, "avg": round(base, 1), "top": mentions[:1]})
    res.sort(key=lambda r: -r["n24"])
    return res


# ------------------------------------------------------------------ persone
def persons_latest():
    jobs = [(n, nota, cat, _pool.submit(fetch_google, q, "en-US", "US", "US:en", cat, "en", "US", "Google News", 3))
            for n, q, cat, nota in PERSONS]
    out = []
    for n, nota, cat, fut in jobs:
        res = fut.result()
        if res is None:
            out.append({"name": n, "note": nota, "status": "fonte non accessibile"})
        elif not res:
            out.append({"name": n, "note": nota, "status": "nessuna dichiarazione rilevante negli ultimi 3 giorni"})
        else:
            rx = re.compile(PERSON_MUST.get(n, n), re.I)
            res = [x for x in res if rx.search(x["title"])]
            if not res:
                out.append({"name": n, "note": nota, "status": "nessuna dichiarazione rilevante negli ultimi 3 giorni"})
                continue
            best = max(sorted(res, key=lambda x: x["tier"])[:8], key=lambda x: x["pub"])
            out.append({"name": n, "note": nota, "item": best})
    return out


# ------------------------------------------------------------------ storico mensile
def history_add(clusters):
    h = jload("history.json", {})
    day = datetime.now().strftime("%Y-%m-%d")
    top = [c for c in clusters if c["score"] >= 5][:6]
    h[day] = [{"title": c["title"], "who": c["source"], "cat": c["cat"], "tier": c["tier"], "link": c["link"], "pct": c.get("pct")} for c in top]
    jsave("history.json", h)
