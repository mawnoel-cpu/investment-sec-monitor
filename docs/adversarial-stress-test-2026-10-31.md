# Isolated stress test — 31 October 2026

## Result and scope

The combined failure case passed: a stale holding price is UNRESOLVED, absent
issuer facts stay UNKNOWN, an existing order is retained without resubmission,
and the incomplete review cannot receive a saved-complete certificate.

The baseline suite passed 236 tests. Eighteen additional adversarial tests
exposed four groups of weaknesses. With the proposed fixes, all 254 Python
tests and 12 unchanged Gmail queue tests pass. These are software and
retrospective diagnostics, not evidence of investment profitability.

All fault injection used synthetic fixtures with live workbook authentication
disabled. Workbook access for the historical assessment was read-only.
No production branch, workbook cell, holding, order, Gmail message or task
instruction was modified by this stress-test change. Fixes belong on the
review branch until explicitly deployed.

## Defects reproduced and fixed

| Area | Before | Proposed behaviour |
| --- | --- | --- |
| Portfolio integrity | Invalid numeric weights could be silently omitted; duplicate ticker rows could enter the engine | Reject the input with its exact row; reject duplicates, missing tickers with weights, nonfinite/text/boolean/negative weights and weights above 25% |
| Partial orders | A status explicitly saying partially executed was not recognized as pending if weights already agreed | Preserve the instruction and require reconciliation; cancelled and fully executed statuses remain distinguishable |
| Checkpoint consistency | Live readiness formulas checked stage timestamps individually but did not require stage order; an empty telemetry-shaped row could be classified as legacy | Require ordered checkpoints in the live formula; a modern empty-stage row is incomplete; preserve historical A:N legacy behaviour |
| Session validity | A weekday on a configured exchange holiday could enter the verified archive | Quarantine that row according to its venue's existing holiday calendar |

The partial-order test is a defensive parser case, not a claim that FT offers
partial fills or that any actual user trade partially executed.

## Other fault tests

Passed: each interrupted checkpoint; write interruption and mismatched readback;
changed holdings after proposal capture; unqualified replacement candidate;
cash ceiling and minimum holdings under simultaneous losses; idempotent warning
reruns; opposing issuer facts versus favourable prices; newer evidence requiring
review refresh; DST, holiday and game-end execution; future prices not changing
earlier signals. Candidate and holding qualification remain separate.

The generated Sheets formulas were checked for balanced syntax and required
guards offline. This isolated run did not execute altered formulas in Google
Sheets, simulate an actual scheduled Chat interruption, or certify a future
scheduled run. The previous native gate tests are not substituted for these
limits. A deployment should verify effective cells and the existing scheduled
handoff before claiming end-to-end completion.

## Historical evidence and sensitivity

Read-only source: the existing FT Game Intelligence workbook, using
DAILY CLOSES A5:J12000, Decision History A5:S400, Reviews A8:Z40,
Trade Log A8:AA70 and Evidence A8:Z420.
Raw private extracts remain temporary and are not committed to this repository.

The archive contained 7,880 rows and 102 symbols, with no archive schema faults.
The diagnostic window 30 September–15 October contains 12 US sessions.
Earliest archive capture for prices in this window is 14 October: historical
prices do not prove the production feed or research existed at the earlier time.
The fixed retrospective universe and overlapping sector pairs also limit inference.

| Warning thresholds | Distinct pair episodes | Sessions whose pair set changed versus baseline |
| --- | ---: | ---: |
| 20% lower: 3.2pp / 4.8pp | 254 | 12/12 |
| Existing: 4pp / 6pp | 180 | 0/12 |
| 20% higher: 4.8pp / 7.2pp | 124 | 12/12 |

Alert composition is sensitive. These are correlated warning episodes, not
trades; this test does not identify an optimal threshold or justify changing it.

The retrospective SOXX→XLV warning first appears on 8 October, implying
9 October review and 12 October earliest next-open execution under the current
rules. This is not proof an eligible, fully researched stock rotation was
available then. Signal-close outcomes must not be called execution returns.

| Signal-close diagnostic horizon | Matured pairs | Relative failures | Censored |
| --- | ---: | ---: | ---: |
| 1 session | 165 | 77 | 15 |
| 5 sessions | 70 | 5 | 110 |
| 10 sessions | 4 | 3 | 176 |

A relative failure means the leading group did not outperform the lagging group
between the signal close and horizon close. Correlation, censoring, universe
selection and execution lag prevent interpreting these as trade win rates.

Of 133 frozen snapshots captured in the window, only 13 contain all three model
inputs. Removing the company, price and macro contributions changes 2, 3 and 0
of these 13 diagnostic bands respectively; no weights were renormalised.
The existing 45/35/20 versus 50/40/10 comparison changes none. Neither result
establishes robustness: the sample is small and incomplete. Zero cases satisfy
the replay's current strict price-provenance/14:20–14:45 criterion; historical
formats and review schedules may differ, so this is not a finding that all
historical reviews were late.

The financing replay has 68 held-position cases and zero contemporaneous
qualifying issuer-finance records/frozen finance assessments in that window.
It cannot measure the financing overlay's historical incremental value.
Across the read execution log, none of 55 executed records has both numeric
execution date/time and a positive fill price. Actual portfolio return,
drawdown and executable rotation contribution therefore remain uncomputed.
No hypothetical fill or hindsight replacement was substituted.

## Reproduce without live writes

Run `python tests/run_offline.py` and
`node --test tests/test_ft_gmail_queue.cjs`.

The existing read-only replay now accepts `--stress-diagnostics`:

```sh
python ft_rotation_replay.py --archive PRIVATE_CLOSES.json \
  --decision-history PRIVATE_HISTORY.json --financial-evidence PRIVATE_EVIDENCE.json \
  --start 2026-09-30 --end 2026-10-15 --stress-diagnostics --output PRIVATE_RESULT.json
```

Keep those private inputs/outputs outside the repository. No collector or
schedule was added. Production thresholds and model weights are unchanged.

## Next evidence needed

Use the existing daily/weekly processes to preserve first-signal and first
recommendation links, contemporaneous eligibility/source checks and actual
execution receipts when available. Review holding-relevant alert clusters to
reduce presentation noise without tuning thresholds to this short sample.
Until matched executions and complete point-in-time decisions exist, treat
investment-edge claims as unproven even when software tests pass.
