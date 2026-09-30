# Context

Glossary for this project. Domain terms only — no implementation decisions.

## Transaction

Something that happened in the brokerage account. A fact, not a state. Three kinds: **buy**, **sell**, **split**.

Transactions are the only source of truth about the portfolio. Everything else is derived from them.

## Position

The result of folding every transaction for one company together: how many shares are held, and at what average cost. A position is never stored — it is recomputed each time the app opens.

Distinguish from a transaction: a position is a *state*, a transaction is an *event*.

## Split

A share split — the number of shares held changes with no money moving. Entered by hand as its own kind of transaction.

Not a buy: a split changes neither the value of the position nor its total cost basis, only the share count and the per-share price.

## Commission

What the broker charged to execute a transaction. Recorded on the transaction and summed, but deliberately **kept out of the average price**.

A broker's statement reports the price of the share, and the average price in this app exists to be checked against that statement. A cost basis that quietly included fees would be marginally truer and impossible to reconcile.

## Current weight

A position's share of total portfolio value, measured at **current market prices**. The denominator is the sum of all position valuations; cash is not part of the model.

## Target weight

The share a company is *meant* to hold in the portfolio. Persisted, and set individually for each company. It expresses portfolio policy, not portfolio state.

## Drift

The gap between current weight and target weight. This is the number you open the app to see.

## Contribution amount

Money you intend to pay in — entered ad hoc when running a rebalance, never stored.

Deliberately **not** a cash balance: the app does not track cash in the account, because in practice there isn't any.

## Rebalance

Working out what to buy — and optionally what to sell — to bring current weights closer to target weights. Two modes:

- **contribute** — the default. Distributes the *contribution amount* and sells nothing.
- **full** — allows selling. Switched on deliberately, because selling is a taxable event.

## Unrealised profit

The gap between the current valuation of open positions and their cost basis. Moves with every price.

## Realised profit

Profit or loss locked in by a sale. It never changes again.

Never added to unrealised profit to form a single number — the result would be a figure nobody can interpret.

## Price snapshot

A company's price together with **the time it was fetched**. This app has no concept of a price without a timestamp: without one, you cannot tell a falling portfolio from a broken fetch.

## Status export

A portfolio written out as plain text at one moment: each position with its weight, drift and profit, and the age of the prices behind them. Derived from the transactions and prices: a copy of the numbers, not a source of them.

Distinguish from a price snapshot: a price snapshot is one company's price with its fetch time; a status export is the whole portfolio, and it carries the age of its price snapshots with it.

## Company profile

What is known and believed about one company: thesis, risks, business, financials, scorecard, earnings-call takeaways. Research, not bookkeeping: nothing in it comes from the transactions.

Optional. A company without a profile is still a full position; it just has nothing more to show.

Distinguish from a position: a position is what you hold, a profile is why you hold it.

## Transcript

The text of one earnings call — one company, one quarter — split into turns by speaker.

Distinguish from the profile's earnings-call takeaways: the transcript is the source, the takeaways are someone's reading of it.

## Demo portfolio

A made-up portfolio used to show the app without anyone's real data. Its transactions and amounts belong to no one.

Distinguish from a real portfolio: same shape — transactions, positions, target weights — but nothing in it happened in a brokerage account.
