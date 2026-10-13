# Kalshi context for FT Game Intelligence

Public GET-only market data, no Kalshi account or key. The existing Google service
account writes only the dedicated **Kalshi Feed** in the active FT workbook.
No writes to Evidence, Signals, Portfolio, Trade Log or allocations. No order API.

## Layout and ownership

- `A3:D3`: latest collection status and time (Toronto).
- `A7:AD27`: up to 20 currently selected contracts (`FTKalshiCurrent`).
- `A35:AD...`: chronological history (`FTKalshiHistory`). One observation per exact
  contract and Toronto date; same-day reruns update that observation. This is not
  an immutable intraday tick archive. Preserve all earlier dates.
- `AF7:AN...`: chronological run log (`FTKalshiRunLog`). Started, provisional Partial,
  and final status distinguish collection from verified write completion.

Run `python ft_kalshi_collector.py --output /tmp/kalshi.json` for a public read-only
check. Run `python tests/run_offline.py` for the network-disabled regression suite.
The writer requires the existing `GOOGLE_SERVICE_ACCOUNT_JSON` Actions secret.
Tables must be provisioned with the exact headers and bounds in the writer.

## Market selection

Verified allowlist: KXFEDDECISION (4), KXCPI (4), KXCPIYOY (3), KXPCECORE (2),
KXU3 (2), KXNEWTARIFFS (2), KXEIACRUDEW (3). Maximum 20 contracts, not 20 independent
events. Only open binary contracts with both close and expected expiry before
27 November 2026 are admitted. Later-settling markets are deliberately excluded.
The latest-expiration longstop is not confused with expected event resolution.
Series discovery fully paginates within a bounded budget; incomplete series are
excluded and logged. No available contract is recorded as a coverage gap, not
invented or treated as an adverse signal. Preserve previously watched in-horizon
contracts within quality tiers, then rank activity. Correlated CPI thresholds,
MoM/YoY and multiple Fed outcomes do not constitute independent confirmation.

## Evidence quality and movement

YES probability proxy = midpoint of valid YES bid and ask on a $1-payout contract.
Store both sides, spread, activity, displayed depth, exact YES meaning, rules,
close and expected expiry. Never substitute last trade price for a missing quote.
Support current fixed-point decimal API fields and explicit legacy cent fallback.
Invalid, missing, NaN and one-sided values do not become a zero probability.

Eligible context requires spread <=10 percentage points, at least 100 contracts
in 24h, 100 open interest, 10 contracts on each displayed side, non-provisional
status, rules/YES meaning and a verified non-block trade within 24h. These are
initial screening thresholds, not proof of calibration or protection from
manipulation. A recent observed trade is not necessarily the globally latest
trade. API `updated_time` is contract metadata, not a reliable quote timestamp.

A **RESEARCH MOVE** is an absolute change >=10 percentage points from the latest
prior-date capture of the exact same contract, no more than 96 hours earlier,
with the same rules/strike/deadlines hash and both observations eligible. The
prior capture time is explicit; the comparison is not labelled a 24h move.
First capture, changed rules, weak quality and missing baseline cannot flag a move.

The daily review must read A3:D3 and the newest run log before using current rows;
require a Complete run, capture age <=4h, current unexpired deadline, and eligible
context. Partial/Failed/Started/missing/stale means unavailable supplementary
context: continue with sufficient other evidence and retain independent risk trims.
A Complete run means collection/write completed, not that every market qualified.

Read the original event rules and corroborating Fed/BLS/BEA/EIA/government sources
before interpreting any flag for current holdings or the rotation bench. New tariff
action can specify implementation after market expiry. Inventory probabilities
are not direct oil-price or refining-margin forecasts. Price probabilities are
not stock-return probabilities. Do not add raw Kalshi rows to weighted Evidence;
no automatic signal score, allocation change, buy or sell follows from the feed.

## Schedule and verification

Weekdays at 13:00 Toronto (17:00 UTC in October, 18:00 UTC in November), before the
14:45 review. GitHub schedules may be delayed; the freshness gate still applies.
The date guard stops all collection and writes after 27 November 2026, including
future annual cron matches. Main-branch pushes affecting this collector also run
one live collection after the offline tests. Pull requests never get write secrets.

References:
- https://docs.kalshi.com/getting_started/quick_start_market_data
- https://docs.kalshi.com/api-reference/market/get-markets
- https://docs.kalshi.com/api-reference/market/get-trades
