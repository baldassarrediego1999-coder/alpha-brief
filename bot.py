"""
Bot Telegram "Brief": consegna il brief giornaliero (08:00), il controllo urgenti (18:00)
e i comandi per watchlist ed eventi. Token in .env (TELEGRAM_BOT_TOKEN).
"""
import html, os, re, sys, threading, time, traceback

for _s in (sys.stdout, sys.stderr):
    try:
        _s.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass
from datetime import datetime, timezone

import requests

import brief, calendar_view, core, deepdive
from core import esc, jload, jsave

HERE = os.path.dirname(os.path.abspath(__file__))
for line in (open(os.path.join(HERE, ".env"), encoding="utf-8").read().splitlines() if os.path.exists(os.path.join(HERE, ".env")) else []):
    if "=" in line and not line.strip().startswith("#"):
        k, v = line.split("=", 1)
        os.environ.setdefault(k.strip(), v.strip())
TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "").strip()
API = f"https://api.telegram.org/bot{TOKEN}"
BRIEF_HOUR, URGENT_HOUR = 8, 18
CHAINS = {"solana", "ethereum", "bsc", "base", "arbitrum", "polygon", "avalanche", "optimism"}
_busy = threading.RLock()
BOT_NAME = [""]


def tg(method, **p):
    try:
        return requests.post(f"{API}/{method}", json=p, timeout=60 if method != "getUpdates" else 45).json()
    except Exception as e:
        print("tg errore:", e)
        return {}


KB = {"keyboard": [[{"text": "📰 Tutte le notizie"}, {"text": "🗞 Ultime notizie"}],
                   [{"text": "🚨 Urgenti"}, {"text": "👤 Persone"}],
                   [{"text": "📊 Watchlist"}, {"text": "📆 Calendario mese"}]],
      "resize_keyboard": True, "is_persistent": True}
LABELS = {"huge": "/huge", "big catalyst": "/huge", "brief": "/brief", "urgenti": "/urgenti", "ultime": "/ultime", "archivio": "/archivio", "fonti": "/fonti", "help": "/help",
          "start": "/start", "mese": "/mese", "tutte le notizie": "/brief", "ultime notizie": "/ultime", "urgenti": "/urgenti",
          "persone": "/persone", "watchlist": "/watchlist", "calendario mese": "/mese", "settimana": "/settimana",
          "futuri": "/futuri", "eventi futuri": "/futuri", "oggi": "/oggi"}
CN_PER_DAY, CN_GLOBAL = 10, 60
CONNECT_RX = re.compile(r"^(?:connetti|collega(?:\s+questa)?)\b\s*[:\-]?\s*(.*)$", re.I | re.S)


def cn_quota_take(user):
    """NEWS CONNECTOR: 10 al giorno a testa, 60 in totale (limite del free tier). True se disponibile."""
    st = jload("cn_quota.json", {})
    today = datetime.now().strftime("%Y-%m-%d")
    if st.get("day") != today:
        st = {"day": today, "users": {}}
    if st["users"].get(str(user), 0) >= CN_PER_DAY or sum(st["users"].values()) >= CN_GLOBAL:
        return False
    st["users"][str(user)] = st["users"].get(str(user), 0) + 1
    jsave("cn_quota.json", st)
    return True


def run_connector(chat, user, text):
    if not cn_quota_take(user):
        return send(chat, "Quota NEWS CONNECTOR esaurita per oggi (10 a testa, free tier). Riprova domani.")
    send(chat, "🧩 Collego la news (narrativa, catalyst, coin)…")
    try:
        card, conn = brief.text_cards(text)
        send_many(chat, conn)
    except Exception as e:
        traceback.print_exc()
        send(chat, f"⚠️ Errore connector: {esc(repr(e))[:300]}")


def send(chat, text):
    r = tg("sendMessage", chat_id=chat, text=text, parse_mode="HTML", disable_web_page_preview=True, reply_markup=KB)
    if not r.get("ok"):
        print("send fallita:", r.get("description"))
        tg("sendMessage", chat_id=chat, text=re.sub(r"<[^>]+>", "", html.unescape(text))[:4000], disable_web_page_preview=True)


def send_many(chat, msgs):
    for m in msgs:
        send(chat, m)
        time.sleep(0.4)


def owner():
    o = jload("owner.json", {})
    if not o.get("chat"):
        s = jload("subs.json", [])
        if s:
            o = {"chat": s[0]}
            jsave("owner.json", o)
    return o.get("chat")


WRITE_CMDS = {"/add", "/rm", "/kw", "/catalyst", "/evento"}


def subs():
    return jload("subs.json", [])


HELP = (
    "<b>📅 Brief — intelligence crypto, politica e macro</b>\n"
    "Ogni giorno alle 08:00 il brief completo, alle 18:00 il controllo urgenti.\n\n"
    "<b>Deep Analysis</b> (max 3 al giorno a testa): incolla qui qualsiasi news, titolo, evento o ticker (o scrivi il numero, es. <code>3</code>) e ricevi un punteggio di rilevanza /100 + report. Nei gruppi menziona il bot o rispondi a un suo messaggio. Anche: <code>approfondisci 3</code> · <code>deep dive su Ansem</code> · <code>deep dive watchlist PEPE</code> · <code>confronta A vs B</code> · <code>scenario …</code> · <code>storico …</code> · <code>chi compra TICKER</code> · /archivio\n\n"
    "<b>Comandi</b>\n"
    "/brief — brief completo adesso\n/urgenti — solo le cose 🔴\n/persone — ultime dichiarazioni\n"
    "/mese [AAAA-MM] — calendario di TUTTI i giorni del mese · /settimana — 7 giorni · /futuri — prossimi 60 giorni · /oggi\n"
    "/huge — big catalyst · <code>connetti [news]</code> — solo NEWS CONNECTOR (incollando una news arriva in automatico)\n/fonti — controlla quali fonti rispondono\n\n"
    "<b>Watchlist</b>\n"
    "<code>/add TICKER [chain] [narrativa]</code> oppure <code>/add INDIRIZZO</code>\n"
    "<code>/kw TICKER parola1, parola2</code> — parole per mappare le news alla coin\n"
    "<code>/catalyst TICKER AAAA-MM-GG testo</code> — catalyst datato\n"
    "<code>/rm TICKER</code> · /watchlist\n\n"
    "<b>Eventi tuoi</b>\n"
    "<code>/evento Nome | AAAA-MM-GG | categoria | cosa cambia | meme collegate | link</code>\n"
    "/on · /off — iscriviti/disiscriviti ai brief automatici"
)


_cache = {}          # kind -> (timestamp, messaggi)
CACHE_S = 600


def get_msgs(kind, builder):
    """Un solo calcolo alla volta; chi arriva dopo riceve lo stesso risultato (cache 10 min)."""
    with _busy:
        c = _cache.get(kind)
        if c and time.time() - c[0] < CACHE_S:
            return c[1]
        msgs = builder()
        _cache[kind] = (time.time(), msgs)
        return msgs


def run_brief(chat, kind, note=""):
    try:
        c = _cache.get(kind)
        if not (c and time.time() - c[0] < CACHE_S):
            send(chat, "⏳ Raccolgo le fonti, ci metto 30-60 secondi…" + note)
        msgs = get_msgs(kind, lambda: brief.run(kind)[0])
        send_many(chat, msgs)
    except Exception as e:
        traceback.print_exc()
        send(chat, f"⚠️ Errore nel brief: {esc(repr(e))[:300]}")


def cmd_add(chat, arg):
    parts = arg.split()
    if not parts:
        return send(chat, "Uso: <code>/add TICKER [chain] [narrativa]</code>")
    q = parts[0].lstrip("$")
    chain = None
    rest = parts[1:]
    if rest and rest[0].lower() in CHAINS:
        chain, rest = rest[0].lower(), rest[1:]
    d = core.dex_lookup(q, chain)
    w = core.wl_load()
    if not d:
        t = q.upper()
        w[t] = {"chain": chain or "?", "narrative": " ".join(rest), "keywords": [], "market_ok": False}
        core.wl_save(w)
        return send(chat, f"Aggiunta <b>${esc(t)}</b>, ma <b>non trovo i dati di mercato</b> (ticker ambiguo o troppo nuovo). "
                          "Rimandala col contratto: <code>/add INDIRIZZO</code>.")
    t = d["ticker"]
    w[t] = {**w.get(t, {}), "name": d["name"], "chain": d["chain"], "address": d["address"], "pair": d["pair"],
            "narrative": " ".join(rest) or w.get(t, {}).get("narrative", ""), "keywords": w.get(t, {}).get("keywords", []),
            "price": d["price"], "mc": d["mc"], "liq": d["liq"], "vol24": d["vol24"], "ch24": d["ch24"], "market_ok": True}
    a = core.ath_info(d["chain"], d["pair"])
    if a:
        w[t]["ath"], w[t]["ath_date"], w[t]["min14"] = a[0], a[1].isoformat(), a[2]
    core.wl_save(w)
    ath = f'ATH {a[0]:.6g} ({a[1].strftime("%m/%y")}), drawdown {(1 - d["price"] / a[0]) * 100:.0f}%' if a else "ATH non verificato"
    send(chat, f'✅ <b>${esc(t)}</b> ({esc(d["name"])}) su {esc(d["chain"])} aggiunta.\n'
               f'MC {core.fmt_usd(d["mc"])} · liq {core.fmt_usd(d["liq"])} · vol24 {core.fmt_usd(d["vol24"])} · {ath}\n'
               f'Aggiungi parole chiave con <code>/kw {esc(t)} parola1, parola2</code> e un catalyst con /catalyst.')


def handle(upd):
    msg = upd.get("message") or {}
    text = (msg.get("text") or "").strip()
    chat = (msg.get("chat") or {}).get("id")
    if not text or chat is None:
        return
    print(time.strftime("%H:%M:%S"), "cmd:", text[:80])
    user = (msg.get("from") or {}).get("id", chat)
    is_group = (msg.get("chat") or {}).get("type") in ("group", "supergroup")
    plain = re.sub(r"[^a-zà-ù ]", "", text.lower()).strip()
    if is_group and not text.startswith("/"):
        # nei gruppi il Deep Analysis parte solo se menzioni il bot o rispondi a un suo messaggio
        me = "@" + (BOT_NAME[0] or "").lower()
        replied_to_bot = ((msg.get("reply_to_message") or {}).get("from") or {}).get("username", "").lower() == (BOT_NAME[0] or "").lower()
        if me in text.lower() or replied_to_bot:
            text = re.sub(re.escape(me), "", text, flags=re.I).strip()
            plain = re.sub(r"[^a-zà-ù ]", "", text.lower()).strip()
        else:
            return
    if plain in LABELS:
        text = LABELS[plain]
    mc = CONNECT_RX.match(text) if not text.startswith("/") else None
    if mc:
        body = mc.group(1).strip() or ((msg.get("reply_to_message") or {}).get("text") or "").strip()
        if len(body) < 3:
            return send(chat, "Uso: <code>connetti [news incollata]</code> oppure rispondi a una news del brief con <code>collega questa</code>.")
        threading.Thread(target=run_connector, args=(chat, user, body[:3000]), daemon=True).start()
        return
    pend = jload("pending.json", {})
    if plain in ("si", "sì", "yes", "ok vai", "vai") and str(user) in pend and time.time() - pend[str(user)]["ts"] < 1800:
        text = pend.pop(str(user))["text"]
        jsave("pending.json", pend)
        dd = ("auto", [text])
    else:
        dd = None if text.startswith("/") else deepdive.parse(text)
    skip_huge = str(user) in pend and dd and dd[0] == "auto" and dd[1][0] == pend[str(user)]["text"]
    if skip_huge:
        pend.pop(str(user), None)
        jsave("pending.json", pend)
    if dd:
        def go_dd(kind=dd[0], args=dd[1], check_huge=(dd[0] == "auto" and not skip_huge)):
            if check_huge:
                if not cn_quota_take(user):
                    return send(chat, "Quota NEWS CONNECTOR esaurita per oggi (10 a testa). Per un Deep Analysis diretto scrivi <code>deep dive su ...</code>.")
                send(chat, "🔍 Controllo huge announcement e collego la news…")
                try:
                    card, conn = brief.text_cards(args[0])
                except Exception:
                    traceback.print_exc()
                    card, conn = None, None
                if conn:
                    if card:
                        send_many(chat, card)
                    send_many(chat, conn)
                    p = jload("pending.json", {})
                    p[str(user)] = {"text": args[0], "ts": time.time()}
                    jsave("pending.json", p)
                    send(chat, "Vuoi un <b>Deep Analysis completo</b> su questo? Rispondi <b>sì</b> (valido 30 minuti).")
                    return
            if deepdive.quota_remaining(user) <= 0:
                return send(chat, "Quota Deep Analysis esaurita per oggi, riprova domani o usa il brief standard.")
            send(chat, "🔍 Deep Analysis in corso (raccolgo fonti e scrivo il report, anche 2-5 minuti)…")
            try:
                send_many(chat, deepdive.run(kind, args, user))
            except Exception as e:
                traceback.print_exc()
                send(chat, f"⚠️ Errore deep analysis: {esc(repr(e))[:300]}")
        threading.Thread(target=go_dd, daemon=True).start()
        return
    cmd, _, arg = text.partition(" ")
    cmd = cmd.split("@")[0].lower()
    arg = arg.strip()
    w = core.wl_load()
    if cmd in WRITE_CMDS and chat != owner():
        return send(chat, "🔒 La watchlist e gli eventi li gestisce solo il proprietario del bot. Tu puoi usare i tasti in basso.")
    if cmd in ("/start", "/help"):
        s = subs()
        if chat not in s:
            s.append(chat)
            jsave("subs.json", s)
        send(chat, HELP + "\n\n✅ Sei iscritto ai brief automatici.")
    elif cmd == "/on":
        s = subs()
        if chat not in s:
            s.append(chat)
            jsave("subs.json", s)
        send(chat, "✅ Brief automatici attivi (08:00 e 18:00).")
    elif cmd == "/off":
        jsave("subs.json", [c for c in subs() if c != chat])
        send(chat, "🔕 Brief automatici spenti.")
    elif cmd == "/brief":
        threading.Thread(target=run_brief, args=(chat, "full"), daemon=True).start()
    elif cmd == "/archivio":
        send(chat, deepdive.archive_text())
    elif cmd == "/ultime":
        def go():
            try:
                c = _cache.get("latest")
                if not (c and time.time() - c[0] < CACHE_S):
                    send(chat, "⏳ Raccolgo le ultime notizie…")
                send_many(chat, get_msgs("latest", brief.latest))
            except Exception as e:
                traceback.print_exc()
                send(chat, f"⚠️ Errore: {esc(repr(e))[:300]}")
        threading.Thread(target=go, daemon=True).start()
    elif cmd == "/huge":
        threading.Thread(target=run_brief, args=(chat, "huge"), daemon=True).start()
    elif cmd == "/urgenti":
        threading.Thread(target=run_brief, args=(chat, "urgent"), daemon=True).start()
    elif cmd == "/persone":
        def go():
            now = core.NOW()
            lines = ["<b>👤 PERSONE — ultime dichiarazioni (3 giorni)</b>"]
            for p in core.persons_latest():
                if p.get("item"):
                    it = p["item"]
                    lines.append(f'• <b>{esc(p["name"])}</b>: <a href="{esc(it["link"])}">{esc(it["title"][:130])}</a>\n'
                                 f'   {core.local(it["pub"])} · {core.humanize(it["pub"], now)} · {esc(it["source"])} (T{it["tier"]})')
                else:
                    lines.append(f'• <b>{esc(p["name"])}</b>: {p["status"]}' + (f' ({p["note"]})' if p["note"] else ""))
            send_many(chat, brief.chunk(["\n".join(lines)]))
        threading.Thread(target=go, daemon=True).start()
    elif cmd in ("/mese", "/settimana", "/futuri", "/oggi"):
        def go(cmd=cmd, arg=arg):
            try:
                ww = core.wl_load()
                if cmd == "/mese":
                    blocks = calendar_view.month_blocks(ww, arg or None)
                elif cmd == "/settimana":
                    blocks = calendar_view.week_blocks(ww)
                elif cmd == "/oggi":
                    blocks = calendar_view.today_block(ww)
                else:
                    blocks = calendar_view.upcoming_blocks(ww)
                send_many(chat, blocks)
            except Exception as e:
                traceback.print_exc()
                send(chat, f"⚠️ Errore calendario: {esc(repr(e))[:300]}")
        threading.Thread(target=go, daemon=True).start()
    elif cmd == "/fonti":
        def go():
            rep = {}
            core.collect(rep)
            ok = [n for n, v in rep.items() if any(v)]
            bad = [n for n, v in rep.items() if not any(v)]
            send(chat, "✅ Accessibili: " + esc(", ".join(ok)) + "\n❌ Non accessibili: " + esc(", ".join(bad) or "nessuna") +
                 "\n\nNon coperti gratis: post diretti su X, Coindar/Unlock Pulse (bloccati), on-chain smart money.")
        threading.Thread(target=go, daemon=True).start()
    elif cmd == "/add":
        threading.Thread(target=cmd_add, args=(chat, arg), daemon=True).start()
    elif cmd == "/rm":
        t = arg.lstrip("$").upper()
        if t in w:
            del w[t]
            core.wl_save(w)
            send(chat, f"🗑 ${esc(t)} rimossa.")
        else:
            send(chat, "Coin non in watchlist.")
    elif cmd == "/kw":
        t, _, kws = arg.partition(" ")
        t = t.lstrip("$").upper()
        if t not in w:
            return send(chat, "Prima aggiungi la coin con /add.")
        w[t]["keywords"] = [k.strip() for k in kws.split(",") if k.strip()]
        core.wl_save(w)
        send(chat, f"✅ Parole chiave di ${esc(t)}: {esc(', '.join(w[t]['keywords']) or 'nessuna')}")
    elif cmd == "/catalyst":
        m = re.match(r"\$?(\w+)\s+(\d{4}-\d{2}-\d{2})\s*(.*)", arg)
        if not m or m.group(1).upper() not in w:
            return send(chat, "Uso: <code>/catalyst TICKER AAAA-MM-GG testo</code> (coin già in watchlist)")
        t = m.group(1).upper()
        w[t]["catalyst_date"], w[t]["catalyst_text"] = m.group(2), m.group(3)
        core.wl_save(w)
        send(chat, f"✅ Catalyst di ${esc(t)}: {esc(m.group(3))} · {m.group(2)}")
    elif cmd == "/watchlist":
        def go():
            ww = core.wl_load()
            if not ww:
                return send(chat, "Watchlist vuota. Usa /add.")
            core.wl_refresh(ww)
            core.wl_save(ww)
            now = core.NOW()
            send_many(chat, brief.chunk([brief.fmt_wl(t, c, [], now) for t, c in ww.items()]))
        threading.Thread(target=go, daemon=True).start()
    elif cmd == "/evento":
        p = [x.strip() for x in arg.split("|")]
        try:
            datetime.fromisoformat(p[1])
        except Exception:
            return send(chat, "Uso: <code>/evento Nome | AAAA-MM-GG | categoria | cosa cambia | meme | link</code>")
        ev = jload("events.json", [])
        ev.append({"name": p[0], "date": p[1], "cat": p[2] if len(p) > 2 else "Crypto", "what": p[3] if len(p) > 3 else "",
                   "memes": p[4] if len(p) > 4 else "", "link": p[5] if len(p) > 5 else ""})
        jsave("events.json", ev)
        send(chat, f"✅ Evento salvato: {esc(p[0])} ({p[1]})")


def scheduler():
    """08:00 brief, 18:00 urgenti. Se il PC era spento recupera appena si riaccende."""
    while True:
        try:
            now = datetime.now()
            day = now.strftime("%Y-%m-%d")
            sent = jload("sent.json", {})
            for slot, hour, kind in (("brief", BRIEF_HOUR, "full"), ("urgent", URGENT_HOUR, "urgent")):
                if now.hour >= hour and sent.get(slot) != day and subs():
                    late = now.hour > hour or now.minute > 20
                    sent[slot] = day
                    jsave("sent.json", sent)
                    for chat in subs():
                        run_brief(chat, kind, " (recupero: il PC era spento all'orario previsto)" if late else "")
                    _cache.pop(kind, None)
        except Exception:
            traceback.print_exc()
        time.sleep(30)


def health_server():
    """Mini server HTTP per le piattaforme cloud che lo richiedono (PORT)."""
    port = os.environ.get("PORT")
    if not port:
        return
    from http.server import BaseHTTPRequestHandler, HTTPServer

    class H(BaseHTTPRequestHandler):
        def do_GET(self):
            self.send_response(200)
            self.end_headers()
            self.wfile.write(b"ok")

        def log_message(self, *a):
            pass
    threading.Thread(target=HTTPServer(("0.0.0.0", int(port)), H).serve_forever, daemon=True).start()


def main():
    health_server()
    if not TOKEN:
        sys.exit("Manca TELEGRAM_BOT_TOKEN nel file .env")
    me = tg("getMe").get("result", {})
    BOT_NAME[0] = me.get("username", "")
    print("Bot avviato come @" + str(me.get("username")), flush=True)
    st = jload("tgstate.json", {"offset": 0})
    threading.Thread(target=scheduler, daemon=True).start()
    run_s = int(os.environ.get("RUN_SECONDS", "0"))      # in cloud (GitHub Actions): esce da solo dopo N secondi
    t_end = time.time() + run_s if run_s else None
    while not (t_end and time.time() > t_end):
        r = tg("getUpdates", offset=st["offset"], timeout=25, allowed_updates=["message"])
        for u in r.get("result", []):
            st["offset"] = u["update_id"] + 1
            try:
                handle(u)
            except Exception:
                traceback.print_exc()
        jsave("tgstate.json", st)
        if not r:
            time.sleep(3)
    print("Tempo massimo raggiunto: esco (il prossimo turno riparte da solo).", flush=True)


if __name__ == "__main__":
    main()
