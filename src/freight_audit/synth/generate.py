"""
Synthetic freight-document generator -- VALUES only (pure stdlib, no rendering).

Produces PII-free load bundles in the SAME JSON shape as samples/loads/*.json, so
they flow straight through JsonFixtureProvider and the MatchEngine. This is the
"PII-safe freight synthetics" track: every carrier/broker/place/name here is
invented (mirroring the shipped samples); nothing is real.

Money is generated in integer cents and emitted both as engine-ready dollar
strings (via cents_to_str) and as integer-cents ground truth (invoice_ground_truth),
so a benchmark never has to round-trip through floats.
"""
from __future__ import annotations

import random
from datetime import datetime, timedelta

from ..models import to_cents, cents_to_str

# All invented -- no real companies/people (matches the repo's synthetic samples).
CARRIERS = [
    "Ironwood Freight Inc", "Sunbelt Carriers LLC", "Blue Ridge Trucking Co",
    "Granite State Transport LLC", "Lone Star Logistics Inc", "Cascade Line Haul LLC",
    "Great Lakes Cartage Co", "Rio Grande Freightways Inc",
]
BROKERS = [
    "Cardinal Logistics Brokerage", "Keystone Freight Brokers LLC",
    "Meridian Transport Solutions", "Summit Load Partners Inc",
    "Anchor Freight Brokerage LLC",
]
LANES = [
    ("Atlanta, GA", "Tampa, FL"), ("Houston, TX", "New Orleans, LA"),
    ("Phoenix, AZ", "El Paso, TX"), ("Chicago, IL", "Indianapolis, IN"),
    ("Los Angeles, CA", "Las Vegas, NV"), ("Charlotte, NC", "Nashville, TN"),
    ("Denver, CO", "Salt Lake City, UT"), ("Newark, NJ", "Baltimore, MD"),
]
SIGNERS = ["T. Nguyen", "D. Salazar", "J. Pierre", "M. O'Connor", "R. Patel", "S. Kim"]


def _money(cents: int) -> str:
    return cents_to_str(cents)          # integer cents -> "$1,900.00"


def generate_load(seed=None, *, with_detention=None) -> dict:
    """Generate one internally-consistent, PII-free load bundle.

    Returns the samples/loads JSON shape: {load_id, rate_confirmation, invoice, pod}.
    With ``with_detention`` False the load is clean (auto-approvable); True bills
    POD-supported detention. Pass a ``seed`` for reproducibility.
    """
    rng = random.Random(seed)
    carrier = rng.choice(CARRIERS)
    broker = rng.choice(BROKERS)
    origin, dest = rng.choice(LANES)
    load_id = "L-" + str(rng.randint(100000, 999999))
    pickup = datetime(2026, rng.randint(1, 12), rng.randint(1, 28),
                      rng.randint(6, 16), rng.choice([0, 15, 30, 45]))

    # money in integer cents (whole-dollar, realistic magnitudes)
    linehaul = rng.randint(40, 90) * 25 * 100               # $1,000 - $2,250
    fuel = round(linehaul * rng.choice([0.18, 0.20, 0.22, 0.25]) / 100) * 100
    free_hours = 2.0
    detention_rate = rng.choice([7500, 8000, 9000])         # $75/$80/$90 per hour

    rc_lines = [{"description": "Linehaul", "amount": _money(linehaul)},
                {"description": "Fuel Surcharge", "amount": _money(fuel)}]
    inv_lines = [{"description": "Linehaul", "amount": _money(linehaul)},
                 {"description": "Fuel Surcharge", "amount": _money(fuel)}]
    approved: dict = {}

    if with_detention is None:
        with_detention = rng.random() < 0.5
    on_site_h = (rng.choice([3.25, 4.0, 5.0, 6.0]) if with_detention
                 else rng.choice([0.75, 1.0, 1.5, 2.0]))
    arrive = pickup.replace(hour=rng.randint(6, 12), minute=rng.choice([0, 15, 30, 45]))
    depart = arrive + timedelta(hours=on_site_h)

    billed_detention = 0
    if with_detention:
        billable = round((on_site_h - free_hours) / 0.25) * 0.25
        billed_detention = int(round(billable * detention_rate))
        approved["detention"] = None                       # approved, uncapped
        rc_lines.append({"description": "Detention", "amount": _money(0),
                         "rate": _money(detention_rate)})   # per-hour rate lives here
        inv_lines.append({"description": f"Detention ({billable:g} hrs)",
                          "amount": _money(billed_detention),
                          "quantity": billable, "rate": _money(detention_rate)})

    agreed_total = linehaul + fuel
    billed_total = linehaul + fuel + billed_detention
    prefix = "".join(w[0] for w in carrier.split()[:2]).upper()

    return {
        "load_id": load_id,
        "rate_confirmation": {
            "load_id": load_id, "broker_name": broker, "carrier_name": carrier,
            "origin": origin, "destination": dest,
            "agreed_total": _money(agreed_total),
            "pickup_date": pickup.strftime("%Y-%m-%d"),
            "free_time_hours": free_hours, "line_items": rc_lines,
            "approved_accessorials": approved,
        },
        "invoice": {
            "invoice_number": f"{prefix}-{rng.randint(1000, 9999)}",
            "load_id": load_id, "carrier_name": carrier,
            "billed_total": _money(billed_total),
            "invoice_date": (pickup + timedelta(days=2)).strftime("%Y-%m-%d"),
            "line_items": inv_lines,
        },
        "pod": {
            "load_id": load_id, "delivered": True,
            "arrival_time": arrive.strftime("%Y-%m-%d %H:%M"),
            "departure_time": depart.strftime("%Y-%m-%d %H:%M"),
            "signed_by": rng.choice(SIGNERS),
        },
    }


def sample_loads(n: int, seed: int = 0) -> list[dict]:
    """A reproducible batch of n load bundles."""
    rng = random.Random(seed)
    return [generate_load(seed=rng.randrange(1_000_000)) for _ in range(n)]


def invoice_ground_truth(bundle: dict) -> dict:
    """Integer-cents ground truth for the rendered invoice image (benchmark target)."""
    inv = bundle["invoice"]
    return {
        "carrier_name": inv["carrier_name"],
        "invoice_number": inv["invoice_number"],
        "billed_total_cents": to_cents(inv["billed_total"]),
        "line_amounts_cents": [to_cents(li["amount"]) for li in inv["line_items"]],
        "line_descriptions": [li["description"] for li in inv["line_items"]],
    }
