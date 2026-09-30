# Investment Portfolio

A local portfolio app for people who already track positions at a broker and
need three things the broker does badly:

- **Rebalance against target weights.** Set the weight you want for each
  company; the app shows the drift and a plan to close it.
- **A clean ledger** that matches the statement line for line, so the same
  numbers can sit next to your company research notes.
- **A company view** that puts what you hold next to why you hold it: a
  thesis, charts and earnings-call transcripts, one company at a time.

![The demo portfolio: buy, sell, add a company, rebalance, and the company view](docs/demo.gif)

The same walkthrough as a [full-resolution video](docs/demo.mp4). Everything in
it is the [demo portfolio](#try-the-demo): made-up trades, real prices.

It runs on your machine, on the Python standard library alone — no `pip
install`, no build step, no npm, no account, no server but yours.

macOS / Linux:

```bash
python3 app/server.py
```

Windows:

```bat
python app/server.py
```

Opens <http://127.0.0.1:8777/>.

What leaves the machine: ticker symbols, to ask what they cost, and — only when
you click for one — a ticker, a quarter and your Alpha Vantage API key, to fetch
an earnings-call transcript. The ledger stays on disk, and no amount is ever
sent anywhere.

## Try the demo

A made-up portfolio of five AI-hardware and data companies, with a company
profile for Broadcom:

```bash
python3 app/server.py --demo
```

Opens <http://127.0.0.1:8778/>, on its own port, so it never collides with
your real portfolio. It runs on a temporary copy of [`demo/`](demo/), which is
deleted when the app stops — by hand or on its idle shutdown — so every trade
you record in it goes with it, and the files in the repository are never
edited. It writes no status export. An Alpha Vantage key and any transcripts
fetched in the demo are kept in your normal data folder, so they outlast it.

## Status

Version 1, in use on a real portfolio. What works today: the ledger (buys,
sells, splits), positions replayed from it, live quotes with a fallback chain,
drift against target weights, contribute-only and full rebalance plans, XTB
statement import, a Markdown status export, and a double-click launcher on
macOS, Windows and Linux.

The **company view** — click *View* on any holding (*Profile* when the company
has one):

- **Position** — price, weight against target, drift and profit for that one
  company.
- **Company profile**, when a company has one: thesis, the assumption hidden
  in management's guidance, revenue, segment, margin and debt charts, a
  scorecard, the conditions that would break the thesis, risks, and the
  earnings call in short. A profile is a JSON file, `companies/<TICKER>.json`,
  in the data folder; the demo ships one for AVGO, and the app only displays it.
- **Earnings-call transcripts** for any ticker, fetched only when you click,
  from [Alpha Vantage](https://www.alphavantage.co/support/#api-key) with a free
  key (the public `demo` key only works for its sample tickers, such as IBM),
  split by speaker and searchable. Each quarter is fetched once and kept in
  `transcripts/` on your machine; a quarter with no published transcript is not
  asked for again for 14 days, so the small daily allowance is not spent
  re-checking it. The key sits in `secrets.json`, readable only by you, and is
  never sent back to the page. Both are git-ignored.

What it does not do: no AI inside the app, and no advice. It never says what
to buy. It shows how far you are from the weights you chose, and what is known
about the companies behind them.

## Where it is heading

This app is the ledger and display piece of a larger setup. The other pieces
exist separately: a [news digest](https://github.com/mattbuildz/stock-market-news-digest)
that filters the week's news down to what touches each holding's thesis, and a
notes folder where each company's thesis and earnings analyses are written.

The company view is the first step of joining them: the profile you see there is
a file that something else wrote. Next is feeding it automatically, so that next
to each position's drift you see whether this week's news and the last earnings
call confirm or weaken the reason you hold it. Two rules carry over: the part
that talks to a model gets tickers, weights and theses, not amounts; and the
decision stays with the investor.

## Why this exists

Broker apps are fine at “what do I hold?”. They are awkward at “how far am I
from the weights I chose?” and useless at “what do I believe about this
company?”. This project is the join between those:

| Layer | Where it lives | Job |
|---|---|---|
| **Ledger & rebalance** | this app | buys, sells, splits → shares, average price, drift, contribute-only or full rebalance plan |
| **Status export** | Markdown file (`portfolio-status.md`) | current weights and P&L as plain text next to your notes |
| **Company knowledge** | your investing notes folder (e.g. one directory per ticker) | thesis, filings, verdicts — written and updated by your research workflow / assistant skills |
| **Company view** | this app | shows a company profile file from that knowledge, next to the position, plus earnings-call transcripts |

That is a different job from a **research terminal** (fundamentals, segments,
screeners, AI over a market database). This app is strong at **your book and
your targets**. It does not research anything: the analysis of a single name is
written elsewhere, and the app displays the result. That keeps the price
fetcher and the ledger free of opinions, and lets the profile come from
whichever workflow you use.

## What the app does

**Transactions are the only thing stored.** Buys, sells and splits. Share
counts, average prices and realised profit are not saved anywhere — they are
replayed from that ledger every time the app opens. So striking a mistyped
trade recomputes everything after it, and a transaction backdated into the
middle of the history lands in the right place.

- **Overview** — holdings with the columns a broker shows: shares, your average
  price, the last price, current value, profit against that average, plus
  weight and drift against your targets. Clicking a company expands it into its
  own transactions with the average price building up after each one.
- **Rebalance** — target weight per company, and a plan to reach it. Two modes:
  contribute-only, which never sells, and full.
- **Holdings** — one row per company; the only thing you type is the target
  weight.
- **Transactions** — the ledger, newest first, with what each sale realised.
- **Company** — opens from a holding: the position, its company profile if it
  has one, and earnings-call transcripts (see [Status](#status)).

Realised and unrealised profit are reported separately and never summed: one
is settled, the other moves with every quote.

**Prices** come from Yahoo, then CNBC, then the last known price — always
labelled with where it came from and when it was taken. A failed fetch keeps
the previous prices and says so. A position with no quote is marked, never
valued at zero.

**Commission** is recorded and summed, but deliberately kept out of the average
price, so that average can be checked line-for-line against a broker statement.
[`CONTEXT.md`](CONTEXT.md) is the glossary and explains the reasoning behind
this and the other domain terms.

**Status export** — after prices and after every portfolio change the app can
write a read-only Markdown file of positions, weights and drift (see
[`how-to-use.md`](how-to-use.md)). That file is meant to sit in your investing
notes so research tools and assistants read the same numbers the screen shows.
It is written to its default path only if that folder already exists; the app
never creates folders for it.

## Where your data lives

```
data/portfolios.json
```

Next to the code, in plain JSON you can read, copy and edit by hand. Target
weights are in each portfolio's `meta`; everything else in the file is the
transaction ledger. Company profiles go in `data/companies/`.

It lives here rather than in a hidden system folder so that reading or editing
the portfolio never means pasting a long path. The cost is that `.gitignore` is
now the only thing keeping the file out of commits, and that deleting this
directory deletes your data with it. Keep a copy somewhere else if it matters
to you.

Put it somewhere else with `PORTFOLIO_DATA_DIR`:

macOS / Linux:

```bash
PORTFOLIO_DATA_DIR=/some/path python3 app/server.py
```

Windows (cmd):

```bat
set PORTFOLIO_DATA_DIR=C:\some\path
python app/server.py
```

Windows (PowerShell):

```powershell
$env:PORTFOLIO_DATA_DIR = "C:\some\path"
python app/server.py
```

Your Alpha Vantage key (`secrets.json`) and fetched transcripts (`transcripts/`)
sit in the data folder too. `PORTFOLIO_LOCAL_DIR` moves just those two, set the
same way as `PORTFOLIO_DATA_DIR`. With `--demo` it stays pointed at your normal
data folder, so the key and transcripts survive the throwaway demo copy.

## Getting started

1. Start the server from the repository root.

macOS / Linux:

```bash
python3 app/server.py
```

Windows:

```bat
python app/server.py
```

2. **Holdings → + Add company** to add a ticker and its target weight.
3. **Buy…** / **Sell…** to record trades, or **Transactions → Record
   transaction…** for an old trade or a split.
4. Optional: **View** on a holding, then paste a free Alpha Vantage key to fetch
   earnings-call transcripts. To give a company a profile, put
   `companies/<TICKER>.json` in the data folder; copy
   [`demo/companies/AVGO.json`](demo/companies/AVGO.json) for the shape.

The first start also installs a double-clickable launcher for this checkout —
so the next time you can open it like any other app:

| OS | Launcher |
|---|---|
| macOS | `~/Applications/Portfolio.app` |
| Windows | Start Menu + Desktop `Portfolio.bat` |
| Linux | `~/.local/share/applications/portfolio.desktop` |

Skip the auto-install:

macOS / Linux:

```bash
PORTFOLIO_NO_APP_INSTALL=1 python3 app/server.py
```

Windows (cmd):

```bat
set PORTFOLIO_NO_APP_INSTALL=1
python app/server.py
```

Windows (PowerShell):

```powershell
$env:PORTFOLIO_NO_APP_INSTALL = "1"
python app/server.py
```

Hand-build / refresh the launcher after moving the repo:

macOS:

```bash
bash scripts/make-desktop-shortcut.sh
```

Windows:

```bat
scripts\make-desktop-shortcut.bat
```

Linux: first start of the server writes the `.desktop` file; there is no
separate script.

Already have an account statement from XTB?

macOS / Linux:

```bash
python3 scripts/import-xtb.py statement.xlsx
python3 scripts/import-xtb.py statement.xlsx --into "Main Portfolio"
```

Windows:

```bat
python scripts/import-xtb.py statement.xlsx
python scripts/import-xtb.py statement.xlsx --into "Main Portfolio"
```

The dry run (no `--into`) shows what it would import without writing anything;
`--into "Main Portfolio"` commits it.

The server shuts itself down 30 minutes after you close the last tab — nothing
runs at login, nothing runs overnight.

## Requirements

Python 3.9+ on macOS, Linux, or Windows. Optional Yahoo price source (without
it the app runs on CNBC alone):

macOS / Linux:

```bash
pip3 install curl_cffi
```

Windows:

```bat
pip install curl_cffi
```

## Documentation

- [`app/README.md`](app/README.md) — how the app is built and why
- [`how-to-use.md`](how-to-use.md) — day-to-day cheat sheet
- [`CONTEXT.md`](CONTEXT.md) — domain glossary
- [`research/price-source.md`](research/price-source.md) — why the price
  sources are ordered the way they are

## Licence

GPL v3. See [LICENSE](LICENSE).
