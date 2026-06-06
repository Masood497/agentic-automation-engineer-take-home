"""
eval.py — Order Enrichment Agent Evaluation Harness
====================================================
Runs all variants (baseline, A, B, C) against all 20 fixture orders
and produces a structured verdict: safe to ship or not, with evidence.

Usage:
    python eval.py                  # runs full eval, prints + writes report.md
    python eval.py --runs 5         # repeat each variant N times (handles randomness)
    python eval.py --json           # also write results.json for inspection

Design rationale: see DESIGN.md
"""

import argparse
import json
import statistics
import sys
import time
import traceback
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

# ── variant imports ────────────────────────────────────────────────────────────
from variants import baseline, variant_a, variant_b, variant_c

VARIANTS = {
    "baseline": baseline,
    "variant_a": variant_a,
    "variant_b": variant_b,
    "variant_c": variant_c,
}

FIXTURES_PATH = Path(__file__).parent / "fixtures" / "orders.jsonl"

# ── Required schema for a valid enriched order ─────────────────────────────────
# These are the fields every successful response MUST contain.
REQUIRED_TOP_LEVEL = {"order_id", "pricing", "shipping", "risk_assessment"}
REQUIRED_PRICING   = {"product_id", "unit_price", "quantity", "total", "currency"}
REQUIRED_SHIPPING  = {"street", "city", "country", "valid"}
REQUIRED_RISK      = {"risk_score", "risk_level", "recommendation"}
VALID_RISK_LEVELS  = {"low", "medium", "high", "unknown"}
VALID_RECOMMENDATIONS = {"approve", "review", "manual_review"}

# Summary field must not be excessively long (guards against padding regressions)
SUMMARY_MAX_CHARS = 400


# ── Data classes ───────────────────────────────────────────────────────────────

@dataclass
class CheckResult:
    name: str
    passed: bool
    detail: str = ""


@dataclass
class OrderResult:
    order_id: str
    variant: str
    run_index: int
    output: dict | None
    error: str | None
    latency_ms: float
    checks: list[CheckResult] = field(default_factory=list)

    @property
    def crashed(self) -> bool:
        return self.error is not None

    @property
    def all_passed(self) -> bool:
        return not self.crashed and all(c.passed for c in self.checks)

    @property
    def failed_checks(self) -> list[CheckResult]:
        return [c for c in self.checks if not c.passed]


# ── Checks ─────────────────────────────────────────────────────────────────────

def run_checks(order_in: dict, result: dict) -> list[CheckResult]:
    """
    All checks are pure functions: order input + agent output → pass/fail + detail.
    Adding a new check here automatically applies it to every variant run.
    """
    checks = []

    # 1. Schema: required top-level keys present
    missing_top = REQUIRED_TOP_LEVEL - set(result.keys())
    checks.append(CheckResult(
        "schema.top_level_fields",
        passed=len(missing_top) == 0,
        detail=f"Missing: {sorted(missing_top)}" if missing_top else "all present",
    ))

    # 2. Schema: order_id echoed correctly
    checks.append(CheckResult(
        "schema.order_id_echoed",
        passed=result.get("order_id") == order_in.get("order_id"),
        detail=f"got {result.get('order_id')!r}, expected {order_in.get('order_id')!r}",
    ))

    # 3. Schema: pricing sub-fields
    pricing = result.get("pricing") or {}
    if isinstance(pricing, dict) and "error" not in pricing:
        missing_p = REQUIRED_PRICING - set(pricing.keys())
        checks.append(CheckResult(
            "schema.pricing_fields",
            passed=len(missing_p) == 0,
            detail=f"Missing: {sorted(missing_p)}" if missing_p else "ok",
        ))
        # 4. Pricing coherence: total ≈ unit_price × quantity
        try:
            expected_total = round(pricing["unit_price"] * pricing["quantity"], 2)
            actual_total   = pricing["total"]
            diff = abs(expected_total - actual_total)
            checks.append(CheckResult(
                "correctness.pricing_total_coherent",
                passed=diff < 0.10,
                detail=f"unit×qty={expected_total}, total={actual_total}, diff={diff:.4f}",
            ))
        except (KeyError, TypeError):
            checks.append(CheckResult("correctness.pricing_total_coherent", False, "could not compute"))
    else:
        checks.append(CheckResult("schema.pricing_fields", False, f"pricing is: {pricing!r}"))
        checks.append(CheckResult("correctness.pricing_total_coherent", False, "no pricing data"))

    # 5. Schema: shipping sub-fields
    shipping = result.get("shipping") or {}
    missing_s = REQUIRED_SHIPPING - set(shipping.keys())
    checks.append(CheckResult(
        "schema.shipping_fields",
        passed=len(missing_s) == 0,
        detail=f"Missing: {sorted(missing_s)}" if missing_s else "ok",
    ))

    # 6. Shipping address echoed (city should round-trip through normalisation)
    if shipping and order_in.get("shipping_address", {}).get("city"):
        input_city  = order_in["shipping_address"]["city"].strip().lower()
        output_city = shipping.get("city", "").strip().lower()
        checks.append(CheckResult(
            "correctness.shipping_city_preserved",
            passed=input_city == output_city,
            detail=f"input={input_city!r}, output={output_city!r}",
        ))

    # 7. Schema: risk_assessment present and well-formed
    risk = result.get("risk_assessment")
    if risk is None:
        checks.append(CheckResult("schema.risk_assessment_present", False, "field missing from output"))
        checks.append(CheckResult("correctness.risk_level_valid",   False, "no risk_assessment"))
        checks.append(CheckResult("correctness.risk_recommendation_valid", False, "no risk_assessment"))
    else:
        missing_r = REQUIRED_RISK - set(risk.keys())
        checks.append(CheckResult(
            "schema.risk_assessment_present",
            passed=len(missing_r) == 0,
            detail=f"Missing sub-fields: {sorted(missing_r)}" if missing_r else "ok",
        ))
        # 8. Risk level is a known value
        level = risk.get("risk_level")
        checks.append(CheckResult(
            "correctness.risk_level_valid",
            passed=level in VALID_RISK_LEVELS,
            detail=f"got {level!r}",
        ))
        # 9. Recommendation is a known value
        rec = risk.get("recommendation")
        checks.append(CheckResult(
            "correctness.risk_recommendation_valid",
            passed=rec in VALID_RECOMMENDATIONS,
            detail=f"got {rec!r}",
        ))
        # 10. If risk_level is NOT "unknown", score must be a real number
        if level != "unknown":
            score = risk.get("risk_score")
            checks.append(CheckResult(
                "correctness.risk_score_is_number",
                passed=isinstance(score, (int, float)) and 0.0 <= score <= 1.0,
                detail=f"score={score!r}",
            ))

    # 11. Risk must not always be unknown — if risk service is reachable,
    #     it should return a real score. Unknown every time = service bypassed.
    # NOTE: baseline legitimately falls back to unknown+None after 3 retries (~0.1% chance).
    # This check is only meaningful in aggregate (see verdict logic which treats it as hard
    # only when it fails consistently). We record the signal here; the verdict layer decides.
    risk = result.get("risk_assessment") or {}
    if isinstance(risk, dict):
        level = risk.get("risk_level")
        score = risk.get("risk_score")
        # "unknown" + None + has "error" key = legitimate retry-exhausted fallback
        is_retry_fallback = level == "unknown" and score is None and "error" in risk
        # "unknown" + None + no "error" key = risk service was never called
        is_bypassed = level == "unknown" and score is None and "error" not in risk
        checks.append(CheckResult(
            "correctness.risk_not_silently_bypassed",
            passed=not is_bypassed,
            detail=f"risk_level={level!r}, risk_score={score!r} — risk service appears bypassed" if is_bypassed else "ok",
        ))

    # 12. Summary quality: if present, must not be bloated
    summary = result.get("summary")
    if summary is not None:
        checks.append(CheckResult(
            "quality.summary_length",
            passed=len(summary) <= SUMMARY_MAX_CHARS,
            detail=f"length={len(summary)} (max {SUMMARY_MAX_CHARS})",
        ))

    # 12. No extra unknown top-level keys that indicate schema drift
    known_top = {
        "order_id", "format", "summary", "pricing", "shipping",
        "risk_assessment", "metadata", "processing_time_ms",
    }
    unexpected = set(result.keys()) - known_top
    checks.append(CheckResult(
        "schema.no_unexpected_top_level_keys",
        passed=len(unexpected) == 0,
        detail=f"Unexpected keys: {sorted(unexpected)}" if unexpected else "ok",
    ))

    return checks


# ── Runner ─────────────────────────────────────────────────────────────────────

def load_orders() -> list[dict]:
    with FIXTURES_PATH.open() as f:
        return [json.loads(line) for line in f if line.strip()]


def run_variant(variant_name: str, module, orders: list[dict], runs: int) -> list[OrderResult]:
    results = []
    for run_idx in range(runs):
        for order in orders:
            t0 = time.perf_counter()
            try:
                output = module.enrich_order(order)
                error  = None
            except Exception as e:
                output = None
                error  = f"{type(e).__name__}: {e}\n{traceback.format_exc()}"
            latency_ms = round((time.perf_counter() - t0) * 1000, 1)

            checks = run_checks(order, output) if output is not None else []

            results.append(OrderResult(
                order_id=order.get("order_id", "?"),
                variant=variant_name,
                run_index=run_idx,
                output=output,
                error=error,
                latency_ms=latency_ms,
                checks=checks,
            ))
    return results


# ── Aggregation ────────────────────────────────────────────────────────────────

@dataclass
class VariantSummary:
    name: str
    total_runs: int          # order × repetitions
    crash_count: int
    check_totals: dict       # check_name → {pass, fail}
    latencies: list[float]

    @property
    def crash_rate(self) -> float:
        return self.crash_count / self.total_runs if self.total_runs else 0.0

    @property
    def p50_ms(self) -> float:
        return statistics.median(self.latencies) if self.latencies else 0.0

    @property
    def p95_ms(self) -> float:
        if not self.latencies:
            return 0.0
        s = sorted(self.latencies)
        idx = int(len(s) * 0.95)
        return s[min(idx, len(s) - 1)]

    def pass_rate(self, check_name: str) -> float:
        totals = self.check_totals.get(check_name, {})
        total  = totals.get("pass", 0) + totals.get("fail", 0)
        return totals.get("pass", 0) / total if total else 0.0

    def overall_pass_rate(self) -> float:
        total_pass = sum(v.get("pass", 0) for v in self.check_totals.values())
        total_all  = sum(v.get("pass", 0) + v.get("fail", 0) for v in self.check_totals.values())
        return total_pass / total_all if total_all else 0.0


def aggregate(results: list[OrderResult]) -> VariantSummary:
    name         = results[0].variant
    crash_count  = sum(1 for r in results if r.crashed)
    latencies    = [r.latency_ms for r in results]
    check_totals: dict[str, dict] = {}

    for r in results:
        for c in r.checks:
            bucket = check_totals.setdefault(c.name, {"pass": 0, "fail": 0})
            if c.passed:
                bucket["pass"] += 1
            else:
                bucket["fail"] += 1

    return VariantSummary(
        name=name,
        total_runs=len(results),
        crash_count=crash_count,
        check_totals=check_totals,
        latencies=latencies,
    )


# ── Verdict logic ──────────────────────────────────────────────────────────────

HARD_FAIL_CHECKS = {
    # These checks failing means the variant is definitely unsafe
    "schema.top_level_fields",
    "schema.risk_assessment_present",
    "schema.order_id_echoed",
    "correctness.risk_level_valid",
    "correctness.risk_recommendation_valid",
    "correctness.risk_not_silently_bypassed",
}

SOFT_FAIL_THRESHOLD = 0.95   # checks below this pass-rate are flagged
CRASH_RATE_LIMIT    = 0.0    # any crash = unsafe (zero tolerance)


def verdict(summary: VariantSummary) -> tuple[str, list[str]]:
    """
    Returns ("SAFE" | "UNSAFE" | "DEGRADED", [reasons]).
    SAFE     = all hard checks pass 100% of the time, no crashes, no soft failures.
    DEGRADED = no hard failures or crashes, but some soft checks are below threshold.
    UNSAFE   = any hard check fails, or any crash observed.
    """
    reasons = []

    # Crashes
    if summary.crash_rate > CRASH_RATE_LIMIT:
        reasons.append(
            f"Crashed on {summary.crash_count}/{summary.total_runs} runs "
            f"({summary.crash_rate:.0%})"
        )

    # Hard-fail checks
    for check in sorted(HARD_FAIL_CHECKS):
        rate = summary.pass_rate(check)
        if rate < 1.0:
            runs_with_check = (
                summary.check_totals.get(check, {}).get("pass", 0)
                + summary.check_totals.get(check, {}).get("fail", 0)
            )
            fail_count = summary.check_totals.get(check, {}).get("fail", 0)
            reasons.append(
                f"Hard check '{check}' failed {fail_count}/{runs_with_check} times "
                f"({(1-rate):.0%} failure rate)"
            )

    if reasons:
        return "UNSAFE", reasons

    # Soft checks
    soft_issues = []
    for check_name, totals in sorted(summary.check_totals.items()):
        if check_name in HARD_FAIL_CHECKS:
            continue
        rate = summary.pass_rate(check_name)
        if rate < SOFT_FAIL_THRESHOLD:
            fail_count = totals.get("fail", 0)
            total = totals.get("pass", 0) + fail_count
            soft_issues.append(
                f"Soft check '{check_name}' failed {fail_count}/{total} times "
                f"({(1-rate):.0%} failure rate)"
            )

    if soft_issues:
        return "DEGRADED", soft_issues

    return "SAFE", ["All checks passed across all runs."]


# ── Report generation ──────────────────────────────────────────────────────────

VERDICT_EMOJI = {"SAFE": "✅", "DEGRADED": "⚠️", "UNSAFE": "❌"}


def build_report(
    summaries: dict[str, VariantSummary],
    verdicts: dict[str, tuple[str, list[str]]],
    runs: int,
    order_count: int,
) -> str:
    lines = []

    lines += [
        "# Order Enrichment Agent — Eval Report",
        "",
        f"**Orders evaluated:** {order_count}  ",
        f"**Runs per variant:** {runs}  ",
        f"**Total executions per variant:** {order_count * runs}  ",
        "",
        "---",
        "",
        "## Summary",
        "",
        "| Variant | Verdict | Crash rate | Overall pass rate | p50 latency | p95 latency |",
        "|---------|---------|------------|-------------------|-------------|-------------|",
    ]

    for name in ["baseline", "variant_a", "variant_b", "variant_c"]:
        s = summaries[name]
        v, _ = verdicts[name]
        emoji = VERDICT_EMOJI[v]
        lines.append(
            f"| {name} | {emoji} {v} | {s.crash_rate:.0%} | "
            f"{s.overall_pass_rate():.0%} | {s.p50_ms:.0f} ms | {s.p95_ms:.0f} ms |"
        )

    lines += ["", "---", ""]

    # Per-variant detail
    for name in ["variant_a", "variant_b", "variant_c"]:
        s = summaries[name]
        v, reasons = verdicts[name]
        emoji = VERDICT_EMOJI[v]
        baseline_s = summaries["baseline"]

        lines += [
            f"## {name.replace('_', ' ').title()} — {emoji} {v}",
            "",
            f"**Safe to ship?** {'Yes' if v == 'SAFE' else 'No' if v == 'UNSAFE' else 'With caution'}",
            "",
            "**Findings:**",
        ]
        for r in reasons:
            lines.append(f"- {r}")

        lines += ["", "**Check breakdown (pass rate across all runs):**", ""]
        lines += [
            "| Check | Baseline | This variant | Δ |",
            "|-------|----------|-------------|---|",
        ]

        all_checks = sorted(
            set(s.check_totals.keys()) | set(baseline_s.check_totals.keys())
        )
        for check in all_checks:
            b_rate = baseline_s.pass_rate(check)
            v_rate = s.pass_rate(check)
            delta  = v_rate - b_rate
            delta_str = f"{delta:+.0%}" if delta != 0 else "—"
            flag = " ⚠️" if v_rate < 1.0 and check in HARD_FAIL_CHECKS else (
                " ⚠️" if v_rate < SOFT_FAIL_THRESHOLD else ""
            )
            lines.append(
                f"| `{check}` | {b_rate:.0%} | {v_rate:.0%} | {delta_str}{flag} |"
            )

        lines += [
            "",
            f"**Latency:** p50 = {s.p50_ms:.0f} ms, p95 = {s.p95_ms:.0f} ms "
            f"(baseline p50 = {baseline_s.p50_ms:.0f} ms)",
            "",
            "---",
            "",
        ]

    # Baseline detail
    s = summaries["baseline"]
    v, reasons = verdicts["baseline"]
    lines += [
        "## Baseline (reference)",
        "",
        "Baseline is the production agent used as the reference for comparisons.",
        "",
        "**Check breakdown:**",
        "",
        "| Check | Pass rate |",
        "|-------|-----------|",
    ]
    for check in sorted(s.check_totals.keys()):
        rate = s.pass_rate(check)
        lines.append(f"| `{check}` | {rate:.0%} |")

    lines += [
        "",
        f"**Latency:** p50 = {s.p50_ms:.0f} ms, p95 = {s.p95_ms:.0f} ms",
        "",
        "---",
        "",
        "## What these verdicts mean",
        "",
        "- **✅ SAFE** — the variant passed all checks on every run. "
        "It is safe to deploy as a replacement for the baseline.",
        "- **⚠️ DEGRADED** — no hard failures or crashes, but one or more quality "
        "checks failed more than 5% of the time. Investigate before shipping.",
        "- **❌ UNSAFE** — the variant crashed, dropped a required field, or produced "
        "invalid data on at least one run. Do not ship.",
        "",
        "_Report generated by eval.py. See DESIGN.md for methodology._",
    ]

    return "\n".join(lines)


# ── CLI ────────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description="Order enrichment agent eval harness")
    parser.add_argument("--runs",  type=int, default=5,
                        help="How many times to run each variant over the fixture set (default: 5)")
    parser.add_argument("--json",  action="store_true",
                        help="Also write raw results to results.json")
    args = parser.parse_args()

    orders = load_orders()
    print(f"Loaded {len(orders)} orders from fixtures.")
    print(f"Running each variant {args.runs}× → {len(orders) * args.runs} executions each.\n")

    all_results: dict[str, list[OrderResult]] = {}
    summaries:   dict[str, VariantSummary]    = {}
    verdicts_:   dict[str, tuple]              = {}

    for name, module in VARIANTS.items():
        print(f"  Running {name}...", end="", flush=True)
        results = run_variant(name, module, orders, runs=args.runs)
        all_results[name] = results
        summaries[name]   = aggregate(results)
        v, reasons        = verdict(summaries[name])
        verdicts_[name]   = (v, reasons)
        emoji             = VERDICT_EMOJI[v]
        print(f" {emoji} {v}  (crash rate: {summaries[name].crash_rate:.0%}, "
              f"pass rate: {summaries[name].overall_pass_rate():.0%})")

    print()

    # Print per-variant summary to stdout
    for name in ["variant_a", "variant_b", "variant_c"]:
        v, reasons = verdicts_[name]
        emoji = VERDICT_EMOJI[v]
        print(f"{name}: {emoji} {v}")
        for r in reasons:
            print(f"  • {r}")
    print()

    # Write report.md
    report = build_report(summaries, verdicts_, args.runs, len(orders))
    report_path = Path(__file__).parent / "report.md"
    report_path.write_text(report)
    print(f"Report written → {report_path}")

    # Optionally write JSON
    if args.json:
        raw = {}
        for name, results in all_results.items():
            raw[name] = [
                {
                    "order_id":   r.order_id,
                    "run":        r.run_index,
                    "crashed":    r.crashed,
                    "error":      r.error,
                    "latency_ms": r.latency_ms,
                    "checks": [
                        {"name": c.name, "passed": c.passed, "detail": c.detail}
                        for c in r.checks
                    ],
                }
                for r in results
            ]
        json_path = Path(__file__).parent / "results.json"
        json_path.write_text(json.dumps(raw, indent=2))
        print(f"Raw results   → {json_path}")

    # Exit code: non-zero if any candidate variant is unsafe
    candidate_verdicts = [verdicts_[n][0] for n in ["variant_a", "variant_b", "variant_c"]]
    if "UNSAFE" in candidate_verdicts:
        sys.exit(1)
    sys.exit(0)


if __name__ == "__main__":
    main()
