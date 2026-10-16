# RULE-015 / RULE-020 sector rotation

The existing price workflow now runs an independent price-risk review after
price collection, even if collection fails. It never places an order or writes
Portfolio, Trade Log, research intake, or the human Daily review.

## Signal and readiness

- Eleven sector ETFs and 22 industry/style groups; 3/5/10/20 matched trading
  sessions, relative strength against SPY, relative-strength acceleration.
- Breadth is a fixed three-company sample per sector, clearly labelled. It is
  not full index breadth and is not a prerequisite for an early warning.
- Initial divergence thresholds: 4 percentage points over three sessions OR
  6 points over five sessions, with a lagging group and leading sector versus
  SPY. Thresholds are editable at PRICE MONITOR U175:V187. Overlapping pair
  warnings are not independent events or automatic trade instructions.
- An alert immediately evaluates every actual holding. Missing data yields
  UNRESOLVED, not HOLD. A risk cut may go to cash without a replacement, subject
  to the existing allocation limits. Queued buys remain distinct from holdings;
  any cash headroom depending on them is conditional.
- Candidates are ranked across sectors against every actual holding and cash.
  READY requires current original-source research, a game-horizon catalyst,
  FT eligibility, numeric sourced valuation, price qualification and a cash-edge
  assessment. No-chase defaults: 3D >6%, 5D >8%, or >7% above the 20-session mean.
  These are initial risk controls, not empirically calibrated expected returns.

## Owned workbook blocks

| Area | Range |
|---|---|
| Sector/industry metrics | PRICE MONITOR A174:S207 |
| Settings | PRICE MONITOR U175:V187 |
| Completion receipt | PRICE MONITOR U195:V202 |
| Immutable first-warning evidence | PRICE MONITOR A220:S1400 |
| Append-only run receipts | PRICE MONITOR A1450:J2000 |
| Holding proposals | PRICE MONITOR A2050:Q2070 |
| Candidate derived ranking | CANDIDATE POOL AR4:BC104 |
| Human valuation qualification | CANDIDATE POOL BD4:BG104 |
| Cross-sector ranked bench and cash | ROTATION BENCH A140:P243 |

The original bench array formula remains intact. Human curated reserves may use
A245:P250. Historical replay tables below PRICE MONITOR row2100 are separate
from live first-warning records. A live first capture is never backdated.

Trade Desk remains DATE/DECISION only. `ft_trade_desk.py` versions the installed
formulas. B13 is owned by the persistence audit; B14 retains engine faults.

## Delivery and fallback

Existing schedules are preserved. Morning 08:10 Toronto and 14:05 recovery
attempts supplement 12:20/13:50 collection, 14:50 auditing and evening close
retries. The GitHub scheduler can be delayed; an observed start is not a saved
delivery. The engine's early verified prior-close proposals remain available
before the 14:45 deadline even when the human review or later job is delayed.
The audit preserves missed research/review deadlines after late recovery.

If the engine is unavailable, the independently scheduled Daily review can
still use verified archived closes. It must verify session/source/unit and
matched histories, save targets and frozen Decision History rows, and read
them back before marking Reviews H `VERIFIED PRIOR CLOSE FALLBACK`. Each
snapshot S needs `VERIFIED PRIOR CLOSE YYYY-MM-DD`, L the positive verified
close and D/E the actual current/queued weights. Trade Desk checks today's
timestamp, the expected prior US session, unique matching snapshot and current
weights. STARTED, future, stale or mismatched receipts cannot enable fallback.
This does not clear the engine error or certify a missed deadline as punctual.

Yahoo failures use an independent dated close source and then a verified
archive. Source-specific failures and stale/missing coverage remain visible.
Sheets requests are paced and quota-specific retries are bounded; ambiguous
network errors or permission failures are not blindly retried as writes.

## Replay and verification

Private data must remain outside this public repository. Export numeric
DAILY CLOSES A5:J rows as a JSON array or `{ "values": [...] }`. Optional OHLC
input is `{ "TICKER": { "bars": [["YYYY-MM-DD",open,high,low,close,source],...] } }`.

```sh
python tests/run_offline.py
node --test tests/test_ft_gmail_queue.cjs
python ft_rotation_replay.py --archive private-closes.json \
  --supplement private-extra-closes.json --ohlc private-ohlc.json \
  --start 2026-09-30 --end 2026-10-15 --output private-replay.json
```

Replay physically removes future prices before every signal calculation.
Pre-existing warnings at the left boundary are excluded from new episodes.
Outcomes are scored only afterwards; incomplete horizons are censored. The
historical universe and threshold selection are retrospective, so this is a
diagnostic replay, not an out-of-sample profitability demonstration. ETF price
returns do not include dividends. Illustrations exclude unverified game fills
and do not claim actual portfolio gains or losses.

The suite covers source failure, calendar/DST differences, delayed delivery,
stale data, cash/minimum-name constraints, duplicate archive keys, immutable
warning evidence, pending-order duplication and no-lookahead. Native Sheets
formula routing was also exercised on a temporary isolated test tab, then the
tab was removed. Verify live workflow steps and workbook receipts, not only a
green test suite. Research/review automation failures remain separate failures
even when price collection and the rotation engine succeed.
