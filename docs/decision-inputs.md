# Decision input repair

`ft_decision_inputs.py` is called by the existing sector-rotation job. It does
not collect data or create a separate decision process. Production weights
remain company 45%, price 35%, macro 20%. Financial-risk overlay weights are a
different, provisional diagnostic and do not replace these production weights.

Owned ranges:

- Decision Cards A65:AF104: matched prices, sector-relative returns, typed issuer
  calculations, eligible event IDs, missing-data explanations and input hash.
- Decision Cards A110:O124: sector price trend and fixed-sample earnings coverage.
- Decision Cards A4:AF39: existing score links and newly required ticker rows.
- PRICE MONITOR U216:V218: write/read-back receipt.

All recorded portfolio names, ROTATION BENCH C5:C15 and five research alternatives
are mapped by ticker. The worksheet refuses to overwrite the finance block at
row 40 if the upper table exceeds 36 names. Portfolio and Trade Log are never
written. Proposed targets still require the existing human review and entry,
cash, concentration, settlement and game-horizon checks.

Company eligibility follows each source's verified validity window, not an
additional seven-day truncation. Exact ticker tokens exclude broad macro rows.
Calendar-only zero-direction records do not create a neutral fundamental score.
Typed facts need an explicit `company_direction` (-1 to 1) and
`company_rationale` after an original-source review. No automated promotion of
Context or Unverified research is allowed. Canonical source URLs suppress repeat
newsletter links to the same event. Missing inputs remain blank, never zero.

Optional typed fields in Evidence V use the existing U:Z provenance gates:

```
income: {revenue, previous_revenue, operating_income,
         previous_operating_income, net_income, basis,
         period_end, period_months}
valuation: {denominator, basis, share_basis_verified}
company_direction: number
company_rationale: string
```

Cash conversion requires the same issuer period and duration as operating cash
flow. Cash FCF is OCF minus disclosed cash capex; issuer non-GAAP FCF is retained
separately. A forward/TTM/book multiple requires a verified matching share basis.
Quarterly EPS is never annualised into a purported forward multiple. Bank and
insurance ratios are not treated as industrial cash-capex ratios. Consensus
revisions, peer estimates and credit spreads stay unknown without verified data.

5/20/60-session returns use matched completed sessions. Missing 60-session history
does not disable 3/5-session RULE-015 warnings or existing price-loss controls.
Sector earnings coverage is a fixed sample, not sector-wide earnings or flows.
Price warnings never wait for primary-source research. Cash remains a valid
destination when a risk cut independently qualifies under existing game limits.

`--prepare-next-session` allows a real-time weekend calculation from the last
completed close. Its receipt is NEXT-SESSION PREPARATION. It never writes a
scheduled-intake receipt, Daily review, historical completion, order or fill.

The existing pipeline-health job independently verifies score mappings and the
45/35/20 arithmetic. Historical missed intake/review deadlines remain failures;
later repairs do not turn them into on-time successes.

`ft_rotation_replay.py --decision-history ...` also compares frozen 45/35/20 and
50/40/10 inputs. Incomplete rows are excluded. Late captures cannot become timely
signals. The report distinguishes arithmetic sensitivity from executable model
performance and leaves returns, drawdowns and trade false-positive rates unknown
without point-in-time execution and decision provenance. No weights are optimized.

Validation: `python tests/run_offline.py` and
`node --test tests/test_ft_gmail_queue.cjs`. No credentials or network are needed.

The persistence audit now distinguishes documented UNKNOWN issuer-assessment scores
from broken score links. It keeps composite scores blank, verifies the price and
macro links, and lists missing issuer analyses in BACKEND HEALTH coverage.
Fabricated zero/composite scores and broken links remain faults. A weekend
next-session preparation is checked at its real capture time; the separate
prewindow audit still preserves Friday's actual missed deadlines.
