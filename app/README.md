# The app

    python3 app/server.py

Opens <http://127.0.0.1:8777/> in your browser. Standard library only — no
`pip install`, no build step.

## The screen

It is prototype 0004, wired to real data — the same topbar, KPI row, allocation
donut, drift digest and stacked-pair table, in the same palette. Four tabs:

- **Overview** — the holdings table, with the columns a broker shows: shares,
  the average price you paid, the last price, current value, and profit against
  that average price, plus weight and drift against your targets.
- **Rebalance** — the prototype's target-weight editor and its plan, computing
  from real prices. Weight edits are saved.
- **Holdings** — one row per company, and the only thing typed on it is the
  target weight. Shares and average price are shown with the number of
  transactions they were folded out of, not offered as fields.
- **Transactions** — the ledger. Every buy, sell and split, newest first, with
  what each sale realised. Striking a row recomputes everything after it.

A fifth tab, **Company**, appears once you open one company from its row — see
[The company view](#the-company-view).

Every column heading in all three tables sorts. One click takes the order you
want first — largest number, or A first for text — a second reverses it, and a
third puts the table back to the order it arranged itself in. That third click
is the point: the Overview table's own order is by drift, which is the thing it
exists to show, and a sort you could not undo would cost you it. A row with no
quote sorts to the bottom either way, because a missing number is not a small
one.

Clicking a company on Overview expands it into its own transactions, oldest
first, with the share count and average price **after each one** — the average
building up rather than only its final value. Those two columns come from the
same replay as every other figure, so the last line of an expanded company
always equals its collapsed row. They are ordered by the fold's own sequence
rather than by date: two trades share a date often enough, and ordering on the
date alone would put a running total beside the wrong row.

What the prototype could not show, because its data was hard-coded, is here:
a position with no quote is marked, never valued at zero; a price that came
from a session close says so rather than claiming to be live.

## What happens when it starts

1. The portfolio file is read from `data/portfolios.json`, beside this
   checkout (first run creates it, with one empty portfolio). The path is
   resolved from `store.py`'s own location, so it does not depend on the
   working directory the server was started in; `PORTFOLIO_DATA_DIR`
   overrides it.
2. **Prices are fetched automatically**, on a background thread, for every
   ticker in every portfolio. The page never waits for the network; the
   snapshot line in the top right says how old the prices are.
   Every successful fetch also writes the Markdown status export
   (`app/snapshot.py`, default `~/Documents/portfolio-status.md`, and only if
   that folder already exists — it never creates it). A failed
   fetch writes nothing — the cache keeps the last good prices, and restamping
   those with today's date is the one thing a status export must never do.
   `PORTFOLIO_NO_SNAPSHOT=1` turns it off; `PORTFOLIO_SNAPSHOT_FILE` redirects it.
3. The **Fetch prices** button runs the same fetch again, for the portfolio you
   are looking at. Adding a new ticker also prices it on the spot — no button
   press needed.

A failed fetch keeps the previous prices and says so. It never shows a price
without saying when it was taken.

## The ledger

**Transactions are the only thing stored.** Shares, average price and realised
profit are not in the file at all — they are folded out of the ledger by
`positions.py` every time they are asked for. `CONTEXT.md` always said the app
worked this way; until recently the file did not, and it cost the things a
stored state cannot give you: no history, no way to split realised profit
across tax years, no undo, nowhere to put a share split.

The fold is one pass, in `positions.replay`, and everything else reads it:

| Kind | Shares | Cost basis | Realised |
|---|---|---|---|
| buy | `+ qty` | `+ qty × price` | — |
| sell | `− qty` | `− qty × average` | `+ qty × (price − average) − fee` |
| split | `× ratio` | unchanged | — |

Two consequences fall out of that table rather than being coded anywhere:

- **A sale does not move the average price.** Shares and cost basis fall in the
  same proportion, so the quotient does not move. What you paid for the shares
  that stay is not changed by what you got for the ones that left.
- **A split moves no money.** Only the count changes; the average price falls
  out of the division.

Commissions are summed but **kept out of the average price** — a broker's
statement reports the price of the share, and matching it is worth more here
than a marginally truer cost basis.

Transactions on the same day fold in the order they were entered. For a buy and
a sell of one company on one day, the other order would produce a different
realised profit.

### Migrating from the old file

A file from before the ledger held only holdings. Each becomes one opening buy
with **no date** — it is a starting balance, and stamping it with a day it did
not happen on would be the app inventing history. The Transactions tab shows
these as `opening balance`. The old file is kept beside the new one as
`portfolios.v1.json`, and the old realised total, which cannot be turned back
into sales, is carried verbatim in `realised_opening`.

### Importing from XTB

    python3 scripts/import-xtb.py portfolio.xlsx                 # show, write nothing
    python3 scripts/import-xtb.py portfolio.xlsx --into "Main Portfolio"

Reads an XTB account statement with the standard library alone. It takes both
sheets, and the second one is the one worth knowing about: *Open positions*
lists only the shares still held, so a lot that was partly sold appears already
shrunk. The history of the sold part is in *Closed positions*, where each row is
really two transactions — the buy that opened it and the sell that closed it.
Import only the first sheet and the sale vanishes, taking its realised profit
with it.

Nothing is written without `--into`, the data file is copied to `.backup.json`
first, and re-running the same statement does not double the portfolio: XTB's
position ids travel in each row's note and are what make it unique.

The import refuses to write unless its own fold agrees with XTB's summary
figures, and prints the comparison either way:

    AMD         2.4815      2.4815    120.4237    120.42  ok
    NVDA        8.0000      8.0000    150.0000    150.00  ok

    realised profit: 112.50 USD   XTB: 112.50 USD   ok

(The two columns differ in the fourth decimal because XTB rounds its summary to
the cent, not because the arithmetic differs.)

## Where the numbers come from

- **Average price** — folded out of your transactions, never typed and never
  guessed.
- **Last price** — fetched. The source and the venue timestamp are printed
  under every price.

Price sources, in order (see `../research/price-source.md`):

| Order | Source | Needs |
|---|---|---|
| 1 | Yahoo chart endpoint | `pip install curl_cffi` — Yahoo refuses stdlib clients |
| 2 | CNBC quote endpoint | nothing; one batched call for all tickers |
| 3 | last known prices | — |

Without `curl_cffi` the app runs on CNBC alone, which is what most runs use
today. Install it to get the Yahoo path as well:

    pip3 install curl_cffi

## Buying and selling

**Buy…** in the portfolio header takes a ticker, a quantity, the price you paid
and a date, and appends one buy to the ledger. The position that results has
the shares added up and the average price as the weighted average of what you
had paid and what you just paid.
The dialog shows that arithmetic before you commit to it —

    Paying $500.00 for 2.0000 NVDA at $250.00.
    Shares          8.0000 → 10.0000
    Average price   $150.00 → $170.00

— because an average price that moved without being seen to move is how a
portfolio quietly stops matching the broker. A ticker you do not hold yet
starts a new position and is priced on the spot.

**Sell…** sits next to it and takes a ticker, a quantity and the price you were
paid. Its ticker field is a dropdown of what you actually hold, because a sale
of something you do not own is not an order, it is a typo; asking for more
shares than you have is refused by the dialog and again by the server, which
names the quantity held.

A sale is not the mirror image of a purchase. **It does not move the average
price.** What you paid for the shares that remain is not changed by what you
got for the ones that went — only the share count falls. The difference between
the two prices is profit, and it is *realised*: locked in, and never changing
again. The dialog shows all of it before you commit —

    Receiving $750.00 for 3.0000 NVDA at $250.00.
    Realised on this sale  +$450.00  (+150.00% against the $100.00 average)
    Shares  7.0000 → 4.0000 · average price unchanged at $100.00
    Realised total  +$350.00 → +$800.00

Sell the whole position and the row leaves the portfolio — but the realised
profit it produced stays in the total, and the dialog says so before you do it.

**Record transaction…** on the Transactions tab is for what the other two
dialogs are not: a trade from last year that was never entered, and splits,
which are neither a buy nor a sell and have no price at all. A back-dated entry
lands in the middle of the history and everything after it is recomputed — so
that dialog says that rather than promising an average price the fold might
contradict.

Realised profit is reported on its own KPI card, beside unrealised profit and
never inside it. This is the rule `CONTEXT.md` is most insistent about: adding
money you have locked in to paper gains that move every time the market does
produces a figure that answers no question anyone has. The two are kept apart
on screen, and are kept apart in the file — the realised total is stored per
portfolio, because once a position is closed there is no row left to compute it
from — with one exception. Realised profit is now recomputed from the sales in
the ledger like everything else; only a total carried over from a pre-ledger
file is stored, because the sales behind it are gone.

That card replaced **Invested**, whose number now reads off the Unrealised
profit card as the cost basis that figure is measured against — the only way it
was ever used.

## The company view

Each holding's row has a button: **Profile** when that company has a profile,
**View** otherwise. Either opens the **Company** tab for that one ticker, in
three parts:

- **Position** — price, weight against target, drift and profit, from the same
  replay as every other figure, and updated when prices move.
- **Company profile** — shown only if `companies/<TICKER>.json` exists in the
  data folder (the demo ships `AVGO.json`, which is also the reference for the
  shape). The app only reads and displays it; it is written elsewhere, by hand
  or by a research workflow, so every string from it is escaped before it
  reaches the page. Charts are plain SVG drawn in the page, nothing fetched.
- **Earnings-call transcripts** — for any ticker, profile or not.

### Transcripts

`app/transcripts.py`. A transcript is fetched only when you click **Fetch
transcript**, one quarter at a time, from Alpha Vantage's
`EARNINGS_CALL_TRANSCRIPT`. That needs a free API key; the public `demo` key
only works for Alpha Vantage's sample tickers, such as IBM. There is no
"latest" to ask for, so you pick the quarter, and the app never guesses.

What is sent: the ticker, the quarter and the key. What comes back is kept as
`transcripts/<TICKER>-<QUARTER>.json` in the local folder, so a quarter is
fetched once and opens from disk after that (**Open saved**). A quarter with no
published transcript is remembered for 14 days, so the small daily allowance is
not spent asking again. The reader splits the call by speaker and searches it.

The key is typed once into the form on that tab and stored in `secrets.json`
beside the transcripts, with file mode 0600. It is never sent back to the page:
the page learns only whether a key exists. Both are git-ignored.

The local folder is the data folder unless `PORTFOLIO_LOCAL_DIR` says
otherwise.

## Demo

    python3 app/server.py --demo

Runs the made-up portfolio in `demo/` on port 8778, so it can sit next to the
real app. It works on a temporary copy, deleted when the server stops, idle
shutdown included, and it installs no launcher.

What makes a data folder a demo is a marker file, `DEMO`, inside it (`demo/`
ships one). With the marker present there is no status export to the notes
folder, whatever else is set; and if the server is pointed straight at the
repository's own `demo/` folder, it still works on a temporary copy, so tracked
files are never edited.

`PORTFOLIO_LOCAL_DIR` stays pointed at the normal data folder in `--demo`, so
the API key and fetched transcripts outlive the throwaway copy.

## Portfolios

The name in the top left is a switcher: pick another portfolio, rename this
one, or create a new one. Deleting a portfolio, or a row, always asks first.
The last remaining portfolio cannot be deleted.

## Data

One JSON file, `data/portfolios.json`, holding the transaction ledger and the
policy that cannot be derived from it: each company's target weight and display
name. No position is stored.

What leaves the machine: ticker symbols, sent to the price source; and, only
when you fetch a transcript, a ticker, a quarter and your API key, sent to
Alpha Vantage. No amount, share count or price paid is sent anywhere.

The server listens on 127.0.0.1 only and refuses POST requests from other
origins, so another web page open in the same browser cannot write to the
portfolio or spend the API key.

Point it somewhere else with `PORTFOLIO_DATA_DIR=/some/path python3 app/server.py`.
`PORTFOLIO_LOCAL_DIR=/some/path` moves only the machine-local files that are
not the portfolio — `secrets.json` and `transcripts/`; by default they sit in
the data folder.

## App launcher

The first `python3 app/server.py` (or `python app/server.py` on Windows) installs
a double-clickable launcher that points at this checkout — same idea on every
OS, so a fresh clone becomes an app without a separate setup step:

- **macOS:** `~/Applications/Portfolio.app` (Launchpad / Applications). Also
  refreshes `~/Desktop/Portfolio.app` if one is already there with a stale path.
- **Windows:** Start Menu and Desktop `Portfolio.bat`.
- **Linux:** `~/.local/share/applications/portfolio.desktop`.

Skip with `PORTFOLIO_NO_APP_INSTALL=1`. Hand-build the same thing:

    bash scripts/make-desktop-shortcut.sh                  # macOS → Applications
    bash scripts/make-desktop-shortcut.sh ~/Desktop/Portfolio.app
    scripts\make-desktop-shortcut.bat                     # Windows → Start Menu + Desktop

On macOS the script burns in the absolute `python3` path and ad-hoc-signs the
bundle so Finder permission prompts work. On Windows it burns in `py -3` /
`python`. Logs: `~/Library/Logs/portfolio-app.log` (macOS),
`%LOCALAPPDATA%\Portfolio\portfolio-app.log` (Windows).

## How long it stays running

It is a server, not a web page: closing the tab with ⌘W cannot stop a process.
So the open page checks in once a minute, and the server exits on its own
**30 minutes after the last page closes**. Nothing runs overnight, nothing
starts at login, and the next double-click starts it again in about a second.

Change the window, or switch it off, with an environment variable:

    PORTFOLIO_IDLE_MINUTES=120 python3 app/server.py   # longer leash
    PORTFOLIO_IDLE_MINUTES=0   python3 app/server.py   # runs until killed

To stop it now rather than in half an hour (macOS / Linux):

    pkill -f app/server.py
