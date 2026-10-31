# Alpha Brief gratis su GitHub — funziona per tutti, anche con i PC spenti

Costo: 0 €. Serve solo un account GitHub gratuito (senza carta).

## 1. Crea l'account e il repository (3 minuti)
1. Vai su https://github.com/signup e crea l'account (email + password).
2. Clicca **New repository** → nome `alpha-brief` → **Public** → **Create repository**.
   (Pubblico perché i repository pubblici hanno minuti gratuiti illimitati. Nel codice **non** c'è il token.)

## 2. Carica i file
1. Nel repository clicca **Add file → Upload files**.
2. Trascina dentro **tutti i file di questa cartella** (`bot.py`, `brief.py`, `core.py`, `events.json`, `requirements.txt`).
3. **Commit changes**.
4. Poi **Add file → Create new file**, nel nome scrivi esattamente `.github/workflows/bot.yml`
   (scrivendo le `/` si creano le cartelle) e incolla il contenuto del file `.github/workflows/bot.yml` di questa cartella → **Commit changes**.

## 3. Metti il token del bot (segreto)
1. Repository → **Settings → Secrets and variables → Actions → New repository secret**.
2. Nome: `TELEGRAM_BOT_TOKEN`
3. Valore: il token di `@alpha_brief_sol_bot` (è nel file `brief-bot/.env` sul tuo PC, dopo `TELEGRAM_BOT_TOKEN=`).

## 4. Spegni il bot sul tuo PC e avvia quello online
1. Chiudi la finestra del bot sul tuo PC (due bot con lo stesso token non possono funzionare insieme).
2. Repository → **Actions** → abilita i workflow se chiede → **Alpha Brief bot → Run workflow**.
3. Dopo circa un minuto apri `@alpha_brief_sol_bot` su Telegram e scrivi /start.

Da quel momento il bot è sempre acceso: ogni 30 minuti GitHub riavvia il lavoro se si è fermato, e i dati
(watchlist, iscritti, storico) vengono salvati nella cache privata del repository.

## Cosa sapere
- Le risposte ai tasti arrivano in pochi secondi (il bot resta in ascolto per ~5,5 ore, poi il turno dopo riparte da solo; nel cambio possono passare 1-2 minuti).
- GitHub può bloccare Google News dai suoi server: in quel caso il bot usa Bing News e alcune fonti regionali/persone possono essere più povere.
- I dati salvati nella cache durano finché il bot gira ogni giorno (GitHub cancella le cache non usate da 7 giorni).
- GitHub non ama molto i bot "sempre accesi" sui suoi runner: se mai bloccassero il repository, si può spostare lo stesso codice su un server.
