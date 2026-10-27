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

Shown on its own card, apart from unrealised profit. The sum of the two has its own name, **overall profit**, and its own place on screen.

## Income

What a holding paid out without being sold: a dividend, or the interest on free funds, **net of the tax withheld**. A fourth kind of transaction, with an amount instead of shares and a price. Interest belongs to no company and has no ticker.

Imported from the broker's cash operations, one row per day and company. Not a deposit: money you pay into the account is not income, and is not recorded at all.

## Overall profit

Realised plus unrealised profit plus income: what the portfolio is worth today (open positions at current prices) plus the money sales took out plus what it paid out, minus everything ever paid for buys. It moves with every price fetch.

Earlier versions refused to add the two parts together; the sum answers a different question — how much richer than the money paid in — and is now reported, as a separate figure and never in place of its parts.

## Return and CAGR

**Return** is overall profit over everything paid for buys. **CAGR** is the yearly rate that turns each dated buy, sale and income payment into today's value (money-weighted, XIRR), so late purchases count only for the time they were held. Both are measured over a period: since the first purchase, or the last 1, 3 or 5 years. A window that reaches past the first purchase is the same as since-first-purchase; one that lands inside the history needs a past valuation the app does not store, so it shows nothing rather than a guess.

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
