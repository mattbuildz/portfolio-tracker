# How to use

Cheat sheet for day-to-day use. Full technical documentation: [`app/README.md`](app/README.md).

## Starting the app

Double-click **Portfolio.app** on the desktop. That starts the server, fetches quotes, and
opens the page in your browser.

From the terminal, the same thing, from the repository root:

```bash
python3 app/server.py
```

The page lives at `http://127.0.0.1:8777/` — you can type it manually or bookmark it.

## Is the server running?

Most reliable — ask the server itself. `200` means it is up:

```bash
curl -s -o /dev/null -w "%{http_code}\n" --max-time 3 localhost:8777/api/state
```

Or check the process. It prints a line if running, nothing if not:

```bash
pgrep -fl "app/server.py"
```

What is happening inside — log from the last start, with every page request:

```bash
tail -20 ~/Library/Logs/portfolio-app.log
```

Stop it by hand:

```bash
pkill -f "app/server.py"
```

## How long the server stays up

It is a process, not a tab — closing the tab (⌘W) does not kill it by itself. So the open
page pings the server once a minute, and the server **shuts down on its own 30 minutes after
the last tab closed**. Nothing starts at login, nothing runs overnight. The next double-click
is up in a second.

Different idle time or no auto-shutdown:

```bash
PORTFOLIO_IDLE_MINUTES=120 python3 app/server.py   # longer leash
PORTFOLIO_IDLE_MINUTES=0   python3 app/server.py   # runs until you kill it
```

## Transactions — where the numbers come from

The app stores **only transactions**: buys, sells, and splits. Share count, average price,
and realised profit are not in the file — they are recomputed from that list every time you
open the app. That is why you no longer enter average price by hand: only a buy, a sell, or
a split changes it.

The **Transactions** tab is that list. Each row can be struck out with “×” — everything after
it is recalculated from scratch. That is how you undo mistakes.

Three things follow from the arithmetic, not from any special decision in the code:

- **A sale does not move average price.** What you paid for the shares you still hold does
  not change because of what you received for the ones you sold. The difference versus your
  average is realised profit and goes on its own tile.
- **A split does not move money.** Share count changes; average price divides itself.
- **Commission is counted separately, outside average price** — because in the XTB statement
  the share price is what XTB reports, and the average must match that.

The **Record transaction…** button under the list is for what **Buy…** and **Sell…** are not
for: a transaction from before you started logging, and splits. A transaction with an earlier
date is inserted into the middle of history and everything after it is recomputed.

## Sorting and expanding

**Every column header sorts** — in Overview, Holdings, and Transactions. First click sorts
from highest value (for text, A first), second click reverses, **third click restores the
original order**. That third click matters: the Overview table’s default order is by drift —
why you opened it. A row with no quote always sits at the bottom — missing is not the same as
small.

**Click a company in Overview to expand its transactions** — oldest first, with position
state after each one in the last two columns. You see how average price builds — from the
first buy to what you have today. The last row always matches the collapsed row — same ledger
replay, not a separate calculation.

## Import from XTB

An XTB statement (.xlsx) enters the app with one command, run from the repository root. Dry
run first — writes nothing, only shows what would happen and whether it matches XTB:

```bash
python3 scripts/import-xtb.py portfolio.xlsx
```

If the table at the end is all “ok”, you can save:

```bash
python3 scripts/import-xtb.py portfolio.xlsx --into "Main Portfolio"
```

The data file is copied to `.backup.json` first, and re-importing the same statement does not
duplicate — XTB position numbers travel with the rows and the app uses them to recognise the
same transactions.

Import **refuses to write** if its own recomputation does not match XTB totals. Differences
at the fourth decimal place are normal: XTB rounds its summary to cents.

## Where quotes come from

One request for the whole portfolio — all tickers at once — to CNBC’s public quote endpoint:

```
https://quote.cnbc.com/quote-html-webservice/restQuote/symbolType/symbol?symbols=AMD|NVDA|MSFT&output=json
```

From the response the app takes four things: price, currency, quote time, and error code (that
is how you tell a typo in the ticker from a dead network). No login, API key, or account —
for quotes, **only company symbols** go out, nothing about your positions. The one other
request the app makes is a transcript fetch, and only when you click for it: a ticker, a
quarter and your Alpha Vantage key (see below).

Yahoo is the first choice in code but needs `pip3 install curl_cffi`, because it rejects
clients that do not look like a browser. Without that, the app runs on CNBC alone — which is
usually enough. Order: Yahoo → CNBC → last known prices (with their date, never silently).

When it fetches:

- **on startup**, in the background — the page does not wait on the network;
- **after clicking Fetch prices** in the top right;
- **after adding a new ticker** — immediately, no extra click.

A failed fetch keeps the last prices and says so in red. A position with no quote is marked
“no quote”, never valued at zero.

Under each price you see the source and quote time. `close 17 Sep` means the closing price
from that day — because outside session hours there is nothing newer, and pretending otherwise
would be misleading.

## Company view and transcripts

Each holding's row has a button — **Profile** if that company has a profile, **View**
otherwise. It opens the **Company** tab: the position (price, weight vs target, drift,
profit), the company profile if `companies/<TICKER>.json` exists in the data folder, and
earnings-call transcripts for any ticker.

Transcripts come from Alpha Vantage and need a free key (alphavantage.co). The public `demo`
key only works for their sample tickers, such as IBM. Paste the key once into the form on the
Company tab; it is saved in `secrets.json` (readable only by you) and never shown back.

Pick a quarter, click **Fetch transcript**. Only then does anything go out: the ticker, the
quarter and the key. The transcript is saved in `transcripts/` and opens from disk next time
(**Open saved**). A quarter with no published transcript is remembered for 14 days, so the
small daily allowance is not spent checking it again. Both `secrets.json` and `transcripts/`
are git-ignored.

## Try the demo

```bash
python3 app/server.py --demo
```

A made-up portfolio from `demo/`, at `http://127.0.0.1:8778/` — its own port, so it can run
next to the real one. It works on a temporary copy that is deleted when the app stops (also
on idle shutdown), and writes no status export. Your key and fetched transcripts stay in the
normal data folder, so they are still there after the demo is gone.

## Daily workflow

| I want to | Where |
|---|---|
| enter a transaction (including an old one) or a split | **Transactions** tab → **Record transaction…** |
| see history and undo a mistake | **Transactions** tab — “×” on the row |
| add a company and set its weight before you buy | **Holdings** tab → **+ Add company** |
| buy more | **Buy…** — shows how average price will change before you confirm |
| sell | **Sell…** — shows realised profit on that sale before you confirm |
| set target weights | **Rebalance** tab — remaining weights adjust to 100% |
| see what to buy for a given amount | **Rebalance** → *Contribution amount* → *See result* |
| switch / add / remove a portfolio | portfolio name in the top left |
| refresh quotes | **Fetch prices** |
| read a company's profile or earnings call | **View** / **Profile** on its row |

Average price is computed from your transactions — you do not type it and cannot override it.
On **Holdings**, under each average you see how many transactions produced it. From the
network you only get the current quote.

**A buy** moves average price (weighted average of old and new); **a sell does not** — a sale
only reduces share count, and the gap between sale price and your average goes on the
**Realised profit** tile. Those two numbers — realised and unrealised — are never summed;
the sum would mean nothing useful: one is fixed, the other moves with every quote. Selling
the whole position removes the row, but the profit it made stays.

## Where data lives

```
data/portfolios.json
```

In the project directory, next to the code. Plain JSON — open, read, copy:

```bash
cat data/portfolios.json
```

It used to live in `~/Library/Application Support/InvestmentPortfolio/`. That is the usual
pattern and has one advantage: deleting the code directory does not take your data. It lost
to the hassle of pasting that path every time you wanted to peek at the portfolio. Now it is
the other way around — the file is at hand, but **deleting the project checkout also deletes
your transactions**, and git ignores it only via `.gitignore`. If the portfolio must survive
an accident, keep a copy somewhere else.

The path is resolved from `app/store.py`, not from the directory you start the server in —
the desktop icon and the terminal read the same file. To put it elsewhere:
`PORTFOLIO_DATA_DIR=/your/path python3 app/server.py`.

`secrets.json` (the Alpha Vantage key) and `transcripts/` sit in the same folder by default.
`PORTFOLIO_LOCAL_DIR=/your/path` moves only those two. In `--demo` it stays pointed at the
normal data folder.

Target weights sit in `portfolios[].meta` — one entry per company, `target` in percent:

```json
"meta": {
  "NVDA": { "name": "NVIDIA", "target": 60.0 },
  "MSFT": { "name": "Microsoft", "target": 40.0 }
}
```

You can edit them by hand with the server stopped — the total does not have to be 100; the
app shows whatever it is.

Inside are **transactions only**, plus target weights and company names. Share counts and
average prices are not stored — they come from transactions on every open.

Writes go through a temp file and replace, so a crash mid-write does not eat the previous
version. A corrupted file is moved aside as `portfolios.corrupt.json`, never overwritten. On
first run of a new version the old file is kept as `portfolios.v1.json`.

## Status export (Markdown)

The app can write a one-file status export — positions, weights, drift, P&L —
so you have the numbers in readable form beside your investing notes without opening
`data/portfolios.json` (that file is only the transaction ledger).

**You do not have to run anything.** The file saves itself:

- when you open the app and after **Fetch prices** (quotes change),
- after every portfolio change — buy, sell, split, striking a transaction,
  changing target weights, switching portfolio.

Default destination (override with `PORTFOLIO_SNAPSHOT_FILE`):

```
~/Documents/portfolio-status.md
```

Inside: one row per company — shares, average price, quote, value, weight now
vs target, drift, unrealised profit — plus frontmatter totals.

**A failed quote fetch does not overwrite the file.** Last known quotes stay;
an honest old file beats week-old prices stamped as fresh.

**A transaction overwrites even when the network is down.** Share count really
changed. Quote age sits in `prices_as_of`; `price_source` says when prices are
stale.

By hand, when the app is not running or the file should go elsewhere:

macOS / Linux:

```bash
python3 scripts/portfolio-snapshot.py
python3 scripts/portfolio-snapshot.py --stdout
python3 scripts/portfolio-snapshot.py -o /other/path.md
```

Windows:

```bat
python scripts/portfolio-snapshot.py
python scripts/portfolio-snapshot.py --stdout
python scripts/portfolio-snapshot.py -o C:\other\path.md
```

The file is **read-only** and fully overwritten on every export, so a hand edit
does not vanish silently without a fight. On macOS that is `chmod 444` plus the
`uchg` flag; on Linux only `chmod 444` (weaker). Unlock on macOS if you must:

```bash
chflags nouchg "$HOME/Documents/portfolio-status.md"
```

If the destination folder is missing, the app does **not** create it and does
not stop — it notes that on the price line.

Turn off: `PORTFOLIO_NO_SNAPSHOT=1` when starting the server.
Other path: `PORTFOLIO_SNAPSHOT_FILE=/path/file.md`.

Facts only — how much you hold and how far from target. No verdicts.

## App launcher

The first start of the server installs a double-clickable app for this checkout:

- **macOS:** `~/Applications/Portfolio.app`
- **Windows:** Start Menu + Desktop `Portfolio.bat`
- **Linux:** `~/.local/share/applications/portfolio.desktop`

Hand-build / refresh after moving the repo:

```bash
bash scripts/make-desktop-shortcut.sh
```

On Windows: `scripts\make-desktop-shortcut.bat`. Skip auto-install with
`PORTFOLIO_NO_APP_INSTALL=1`.


## What the other files do

| File | Role |
|---|---|
| `app/server.py` | server, API, automatic fetch on start, idle shutdown |
| `app/prices.py` | fetching prices and source order |
| `app/store.py` | read and safe write of the transaction file |
| `app/positions.py` | fold transactions into positions: shares, average price, realised profit |
| `app/snapshot.py` | the Markdown status export |
| `app/transcripts.py` | earnings-call transcripts: API key, fetch, local cache |
| `scripts/import-xtb.py` | import XTB statement |
| `app/static/index.html` | UI — structure and CSS |
| `app/static/app.js` | UI logic: donut, drift, table, rebalance, transaction ledger, Buy and Sell |
| `app/static/company.js` | Company tab: position, company profile, transcript reader |
| `scripts/make-desktop-shortcut.sh` | build `~/Applications/Portfolio.app` (macOS) |
| `scripts/make-desktop-shortcut.bat` | Start Menu + Desktop launcher (Windows) |
| `app/launcher.py` | auto-install of the launcher on first server start |

## When something breaks

| Symptom | What to do |
|---|---|
| double-click does nothing | `tail -20 ~/Library/Logs/portfolio-app.log` — the reason is there |
| page does not open | check if the server is up (above); if not — launch the icon |
| “Operation not permitted” in the log | `bash scripts/make-desktop-shortcut.sh` and approve the access prompt |
| quotes do not fetch | the app says whether it is network or a bad ticker — the message distinguishes them |
| Firefox password popup | not this app — Firefox updater; you can cancel |
