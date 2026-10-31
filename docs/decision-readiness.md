# Decision readiness, 31 October 2026

The existing sector-rotation job calls ft_trade_desk.sync_views, which installs
and reads back live readiness formulas in TRADE DESK A25:B60. These formulas
are a view of the existing human decisions, not a second recommendation writer.
A1:B23, frozen Decision History, Portfolio and Trade Log are preserved.

Gates: an existing queued order takes precedence and says DO NOT RESUBMIT.
A completed review requires unique Daily key, numeric completion, five PASS
results and numeric checkpoint times between review start and completion.
Each holding additionally requires a unique matching frozen snapshot,
matching held/queued weights, positive stock/benchmark prices, evidence
references, recognized action and the current completed price session.
A new close makes an older assessed snapshot unqualified, even if the latest
price collector is healthy. Missing candidate research is not a held-name gate.
Missing CDS alone does not block an otherwise completed assessment.
Portfolio limits and displayed holding coverage are checked separately.
The per-holding panel is reserved for up to 19 names; overflow fails visibly.
Non-US held names fail the current US-session gate and require venue-specific
review, never silent currency conversion. Market time is a session-close
reference; archive collection and input calculation are distinct timestamps.

The late 30-Oct recovery stays separate from the original failed 14:45 handoff.
Snapshots A307:S327 remain immutable. XOM's later HOLD addendum in B23 does not
rewrite its original VERIFY snapshot. Monday must refresh the sole daily review.

Candidate work reuses CANDIDATE POOL rows 10 (NVDA), 27 (JPM), 41 (GEV)
and ROTATION BENCH A303:P309. Live matched-return comparisons are in
ROTATION BENCH A316:P319, linked to Decision Cards A65:AF104.
Three issuer reviews were saved with actual 31-Oct timestamps, source URLs,
countercases, financing limitations, cash/CB comparison and qualification gaps.
All remain RESERVE, not purchases. JPM P/TBV is refreshed; NVDA uses an
explicitly approximate trailing sum-of-period GAAP EPS proxy, not forward EPS.
GEV's previously cited multiple is not recertified without a verified denominator.
No scenario probabilities, issuer directional scores or Evidence weights were
invented. Primary-source research here is stored in the existing candidate rows;
the sole scheduled intake remains the owner of Evidence ingestion.

Process measures reuse existing records. Missing first-signal linkage,
continuous equity data and exact execution prices remain UNKNOWN.
Review runtime is not signal-to-decision delay. Existing PERFORMANCE proxy
batch outcomes are not actual realised FT profit or one-to-one replacement alpha.

Validation: python tests/run_offline.py; node --test tests/test_ft_gmail_queue.cjs.
TRADE DESK B24 also evaluates four synthetic gate cases in native Sheets:
completed HOLD, incomplete review, stale price and existing queued order.
Offline tests/manual readback do not certify a future scheduled run.

The live deployment exposed a separate weekend financing-audit clock mismatch.
ft_pipeline_health now checks current financing receipts at the actual audit
clock, matching the existing rotation/input checks. The prior trading-window
clock remains in use for delivery deadlines. Saturday receipts are not future
Friday receipts; genuinely future or stale financing receipts still fail.

A requested weekend refresh can have its own real-time Daily review and frozen
snapshots. The readiness view selects that explicitly recorded weekend review
(including Saturday's on Sunday if no Sunday review exists); an uncompleted or
duplicate selected review still fails its normal completion gates. Weekdays
continue to require that day's review. The deadline/recovery row independently
uses the last business-day review, so a weekend refresh cannot clear Friday's
missed deadline or certify Monday's scheduled handoff. Scheduled TODAY/intake
diagnostics retain their business-day clock.
