# Order Enrichment Agent — Eval Report

**Orders evaluated:** 20  
**Runs per variant:** 5  
**Total executions per variant:** 100  

---

## Summary

| Variant | Verdict | Crash rate | Overall pass rate | p50 latency | p95 latency |
|---------|---------|------------|-------------------|-------------|-------------|
| baseline | ✅ SAFE | 0% | 100% | 63 ms | 96 ms |
| variant_a | ❌ UNSAFE | 0% | 91% | 65 ms | 95 ms |
| variant_b | ❌ UNSAFE | 0% | 92% | 60 ms | 97 ms |
| variant_c | ⚠️ DEGRADED | 0% | 92% | 66 ms | 97 ms |

---

## Variant A — ❌ UNSAFE

**Safe to ship?** No

**Findings:**
- Hard check 'correctness.risk_level_valid' failed 29/100 times (29% failure rate)
- Hard check 'correctness.risk_recommendation_valid' failed 29/100 times (29% failure rate)
- Hard check 'schema.risk_assessment_present' failed 29/100 times (29% failure rate)
- Hard check 'schema.top_level_fields' failed 29/100 times (29% failure rate)

**Check breakdown (pass rate across all runs):**

| Check | Baseline | This variant | Δ |
|-------|----------|-------------|---|
| `correctness.pricing_total_coherent` | 100% | 100% | — |
| `correctness.risk_level_valid` | 100% | 71% | -29% ⚠️ |
| `correctness.risk_not_silently_bypassed` | 100% | 100% | — |
| `correctness.risk_recommendation_valid` | 100% | 71% | -29% ⚠️ |
| `correctness.risk_score_is_number` | 100% | 100% | — |
| `correctness.shipping_city_preserved` | 100% | 100% | — |
| `quality.summary_length` | 100% | 100% | — |
| `schema.no_unexpected_top_level_keys` | 100% | 100% | — |
| `schema.order_id_echoed` | 100% | 100% | — |
| `schema.pricing_fields` | 100% | 100% | — |
| `schema.risk_assessment_present` | 100% | 71% | -29% ⚠️ |
| `schema.shipping_fields` | 100% | 100% | — |
| `schema.top_level_fields` | 100% | 71% | -29% ⚠️ |

**Latency:** p50 = 65 ms, p95 = 95 ms (baseline p50 = 63 ms)

---

## Variant B — ❌ UNSAFE

**Safe to ship?** No

**Findings:**
- Hard check 'correctness.risk_not_silently_bypassed' failed 100/100 times (100% failure rate)

**Check breakdown (pass rate across all runs):**

| Check | Baseline | This variant | Δ |
|-------|----------|-------------|---|
| `correctness.pricing_total_coherent` | 100% | 100% | — |
| `correctness.risk_level_valid` | 100% | 100% | — |
| `correctness.risk_not_silently_bypassed` | 100% | 0% | -100% ⚠️ |
| `correctness.risk_recommendation_valid` | 100% | 100% | — |
| `correctness.risk_score_is_number` | 100% | 0% | -100% ⚠️ |
| `correctness.shipping_city_preserved` | 100% | 100% | — |
| `quality.summary_length` | 100% | 100% | — |
| `schema.no_unexpected_top_level_keys` | 100% | 100% | — |
| `schema.order_id_echoed` | 100% | 100% | — |
| `schema.pricing_fields` | 100% | 100% | — |
| `schema.risk_assessment_present` | 100% | 100% | — |
| `schema.shipping_fields` | 100% | 100% | — |
| `schema.top_level_fields` | 100% | 100% | — |

**Latency:** p50 = 60 ms, p95 = 97 ms (baseline p50 = 63 ms)

---

## Variant C — ⚠️ DEGRADED

**Safe to ship?** With caution

**Findings:**
- Soft check 'quality.summary_length' failed 100/100 times (100% failure rate)

**Check breakdown (pass rate across all runs):**

| Check | Baseline | This variant | Δ |
|-------|----------|-------------|---|
| `correctness.pricing_total_coherent` | 100% | 100% | — |
| `correctness.risk_level_valid` | 100% | 100% | — |
| `correctness.risk_not_silently_bypassed` | 100% | 100% | — |
| `correctness.risk_recommendation_valid` | 100% | 100% | — |
| `correctness.risk_score_is_number` | 100% | 100% | — |
| `correctness.shipping_city_preserved` | 100% | 100% | — |
| `quality.summary_length` | 100% | 0% | -100% ⚠️ |
| `schema.no_unexpected_top_level_keys` | 100% | 100% | — |
| `schema.order_id_echoed` | 100% | 100% | — |
| `schema.pricing_fields` | 100% | 100% | — |
| `schema.risk_assessment_present` | 100% | 100% | — |
| `schema.shipping_fields` | 100% | 100% | — |
| `schema.top_level_fields` | 100% | 100% | — |

**Latency:** p50 = 66 ms, p95 = 97 ms (baseline p50 = 63 ms)

---

## Baseline (reference)

Baseline is the production agent used as the reference for comparisons.

**Check breakdown:**

| Check | Pass rate |
|-------|-----------|
| `correctness.pricing_total_coherent` | 100% |
| `correctness.risk_level_valid` | 100% |
| `correctness.risk_not_silently_bypassed` | 100% |
| `correctness.risk_recommendation_valid` | 100% |
| `correctness.risk_score_is_number` | 100% |
| `correctness.shipping_city_preserved` | 100% |
| `quality.summary_length` | 100% |
| `schema.no_unexpected_top_level_keys` | 100% |
| `schema.order_id_echoed` | 100% |
| `schema.pricing_fields` | 100% |
| `schema.risk_assessment_present` | 100% |
| `schema.shipping_fields` | 100% |
| `schema.top_level_fields` | 100% |

**Latency:** p50 = 63 ms, p95 = 96 ms

---

## What these verdicts mean

- **✅ SAFE** — the variant passed all checks on every run. It is safe to deploy as a replacement for the baseline.
- **⚠️ DEGRADED** — no hard failures or crashes, but one or more quality checks failed more than 5% of the time. Investigate before shipping.
- **❌ UNSAFE** — the variant crashed, dropped a required field, or produced invalid data on at least one run. Do not ship.

_Report generated by eval.py. See DESIGN.md for methodology._