"""Illustrative Tier 1 Cloud Run request-based compute costs, not a total bill.

Rates checked 2026-10-21: https://cloud.google.com/run/pricing
All billed active seconds (including startup) must be included. The default Tokyo
region uses Tier 1 pricing. Other projects can consume the same free allowance.
"""

import argparse
import json


def estimate(active_seconds: float, requests: int, *, other_costs: float = 0, credit: float = 10,
             free_tier: bool = True) -> dict:
    cpu = max(0, active_seconds * 2 - (180_000 if free_tier else 0)) * 0.000024
    memory = max(0, active_seconds * 8 - (360_000 if free_tier else 0)) * 0.0000025
    calls = max(0, requests - (2_000_000 if free_tier else 0)) * 0.40 / 1_000_000
    total = cpu + memory + calls + other_costs
    return dict(active_seconds=active_seconds, cloud_run_usd=round(cpu + memory + calls, 4),
                other_google_cloud_usd=other_costs, before_credit_usd=round(total, 4),
                after_pro_credit_usd=round(max(0, total - credit), 4))


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--receipts", type=int, default=1000)
    parser.add_argument("--seconds-per-receipt", type=float, default=30,
                        help="Illustrative billed seconds including cold starts, not a measured Cloud Run speed")
    parser.add_argument("--other-active-seconds", type=float, default=0)
    parser.add_argument("--other-google-costs", type=float, default=0,
                        help="Builds, registry, storage, secrets, logs and network; excluded unless supplied")
    parser.add_argument("--credit", type=float, default=10)
    parser.add_argument("--no-free-tier", action="store_true")
    args = parser.parse_args()
    if min(args.receipts, args.seconds_per_receipt, args.other_active_seconds, args.other_google_costs, args.credit) < 0:
        parser.error("Inputs must not be negative")
    result = estimate(args.receipts * args.seconds_per_receipt + args.other_active_seconds, args.receipts,
                      other_costs=args.other_google_costs, credit=args.credit, free_tier=not args.no_free_tier)
    print(json.dumps(result, indent=2))
    print("Illustration only: excludes taxes and non-Google costs. Not a spending cap or free-operation guarantee.")


if __name__ == "__main__":
    main()
