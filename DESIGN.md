# DESIGN.md — Eval Harness Design Notes

## What I chose to measure, and why

The core question is: *does this variant produce output that is safe to use downstream?*
I split this into three tiers:

### 1. Schema checks (hard failures)
These assert that required fields exist and have the right shape. A downstream service
consuming the enriched order will crash or silently misbehave if these are missing.
Failing even once is enough to block a deployment — schema regressions are not
probabilistic bugs, they are design breaks.

Checks: `schema.top_level_fields`, `schema.order_id_echoed`, `schema.pricing_fields`,
`schema.shipping_fields`, `schema.risk_assessment_present`.

### 2. Correctness checks (hard failures)
These assert that the *values* produced are semantically valid — not just present.
A field that exists but contains garbage is worse than a missing field, because it
passes schema validation while silently corrupting downstream logic.

Key checks:
- `correctness.pricing_total_coherent` — total must equal unit_price × quantity (within
  rounding). This catches arithmetic bugs in pricing refactors.
- `correctness.risk_level_valid` / `correctness.risk_recommendation_valid` — enumerates
  known-good values; any other value indicates a contract break.
- `correctness.risk_not_silently_bypassed` — detects the pattern where `risk_level` is
  `"unknown"` *and* `risk_score` is `None`. This combination is the fingerprint of a
  variant that has quietly removed the risk-scoring step. A random `unknown` fallback
  after retries is acceptable; `unknown` on *every* order is not.
- `correctness.shipping_city_preserved` — the city in the output must match the city
  in the input after normalisation. Catches address-rewriting bugs.

### 3. Quality checks (soft failures — warn, don't block)
These catch degradations that don't break downstream systems immediately but
indicate a change in output character that a product owner would reject.

- `quality.summary_length` — summaries longer than 400 characters suggest prompt
  bloat or an inadvertent prefix injection. The threshold is intentionally generous
  (the longest normal summary is ~120 chars); this is a canary, not a tight spec.

---

## Why I run each variant N times (default: 10)

Two of the tools use `random`:
- `app.tools.risk` fails ~10% of the time to simulate a flaky upstream.
- `app.tools.pricing` and `app.tools.address` add random jitter.
- `variants.baseline._synthesise` picks one of three templates at random.
- `variants.variant_a` drops `risk_assessment` ~30% of the time.

A single pass over 20 orders would miss Variant A's bug roughly 0.7²⁰ ≈ 0.08% of
the time — unlikely but non-zero. 10 runs × 20 orders = 200 executions per variant,
which reduces the miss probability to negligible levels while keeping runtime short.

---

## What this eval would NOT catch

1. **Semantic drift in the summary text.** We check length but not content.
   If a variant's summary consistently says "approve" when the risk is high, we
   won't catch it unless we add an LLM-as-judge or cross-field coherence check
   (e.g. if `risk_level == "high"` then `summary` should not contain "low risk").

2. **Pricing currency regression.** We check the total arithmetic but not whether
   the currency field has changed from USD to something unexpected.

3. **Latency regression at scale.** The harness measures per-call latency but 20
   orders is too small to detect a 20% slowdown confidently. A production harness
   would need a load test or a statistical test with tighter SLOs.

4. **New fields silently added with wrong types.** We check for unexpected *top-level*
   keys but do not recursively type-check every nested field. A variant that changes
   `pricing.quantity` from `int` to `str` would pass today's checks.

5. **Interaction effects.** The eval runs variants in isolation. A variant that is safe
   on its own might interact badly with a concurrent change to a shared tool.

6. **Prompt injection via fixture data.** If the enrichment pipeline uses an LLM
   internally, an order with adversarial content in the `shipping_address` could
   manipulate the output. The current fixtures don't exercise this.

---

## Rough cost and runtime per run

| Item | Estimate |
|------|----------|
| Fixture orders | 20 |
| Runs per variant | 10 |
| Executions per variant | 200 |
| Avg latency per call (observed) | ~60 ms |
| Total wall time for all 4 variants | ~50 seconds |
| LLM API calls | 0 (all checks are deterministic) |
| Estimated cost | **$0.00** — no LLM calls made by the harness itself |

The harness is deliberately LLM-free. The variants themselves simulate LLM latency via
`time.sleep`, but the *eval logic* is pure Python. This makes runs cheap, fast, and
fully reproducible with a fixed random seed (which can be added if needed).

If an LLM judge were added for semantic checks (e.g. "does the summary accurately
reflect the risk level?"), cost would be approximately $0.002–$0.005 per run at
current Sonnet pricing, or ~$0.05 for the full suite.
