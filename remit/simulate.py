"""What following the playbook every day is worth, if customers respond the way this model says.

The company in Odoo is simulated, so its customers cannot be sent a reminder and
watched. This file plays 90 days forward from the warehouse's real open invoices,
twice per seed, with the same person on the team both times:

- manual: the process Remit replaces. Each Monday the person works the late
  list oldest first and reaches five customers.
- remit: Remit applies the playbook every morning (stages 0 to 3 by itself),
  and the same person spends the same five Monday calls only on the customers
  Remit escalated, oldest first.

The playbook side calls the same `plan_customer` Remit runs in production, so
the simulator measures Remit's own decisions. Both sides see the same customers,
the same new invoices and the same payment draws; the comparison is paired by seed.

Everything about customers here is an assumption, and the report prints it next
to the result:

- each customer gets a payment habit, drawn the way `edp/simulate.py` drew the
  company's year: prompt 55%, slow 20%, irregular 15% (the share that paid in
  part), never 10%;
- a habit is a daily chance of paying an open invoice once it is due;
- a reminder multiplies that chance for the next 10 days by its stage's lift;
- new invoices arrive at the company's own rate from the last 90 days, with
  amounts drawn from those invoices, due 30 days after issue.

The lift is the number that decides the size of the effect, and nobody has
measured it for this company. So the report also runs every seed with half the
lift, to show how much the result leans on it.
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import random
import statistics
from dataclasses import dataclass, replace
from decimal import Decimal

from .playbook import Evidence, Invoice, plan_customer, stage_for

HABITS = [("prompt", 0.55, 0.08), ("slow", 0.20, 0.025), ("irregular", 0.15, 0.03), ("never", 0.10, 0.003)]
LIFT = {0: 0.2, 1: 0.6, 2: 1.0, 3: 1.5, 4: 2.5}   # stage -> extra share of the daily chance
LIFT_DAYS = 10
DAYS = 90
SEEDS = tuple(range(1, 21))
MANUAL_PER_WEEK = 5
SLOTS = 24   # new invoices per day are drawn as 24 independent chances


@dataclass
class Open:
    """One open invoice during the simulation."""

    inv: Invoice
    issued: dt.date
    paid_on: dt.date | None = None


def habit(rng: random.Random) -> tuple[str, float]:
    """A customer's payment habit and its daily chance of paying a due invoice."""
    x, acc = rng.random(), 0.0
    for name, share, daily in HABITS:
        acc += share
        if x < acc:
            return name, daily
    return HABITS[-1][0], HABITS[-1][2]


def _as_of(inv: Invoice, day: dt.date) -> Invoice:
    return replace(inv, days_past_due=(day - inv.due_date).days)


def play(start: list[Open], history: list[Decimal], per_day: float, as_of: dt.date, seed: int,
         policy: str, lift_scale: float = 1.0) -> dict:
    """Plays DAYS days forward under one policy and returns the measurements."""
    rng = random.Random(seed)
    book = [Open(o.inv, o.issued) for o in start]
    customers = sorted({o.inv.customer_key for o in book})
    habits = {k: habit(rng) for k in customers}
    names = {o.inv.customer_key: o.inv.customer_name for o in book}
    reminded: dict[int, tuple[dt.date, int]] = {}   # customer -> (day, stage) of the last contact
    done: set[str] = set()
    escalated: set[int] = set()
    contacts = 0          # reminders and calls the customer receives, from anyone
    person_calls = 0      # the share of those a person made
    first_contact: dict[int, dt.date] = {}   # invoice id -> first day its customer was contacted while it was late
    next_id = 10**7
    for n in range(1, DAYS + 1):
        day = as_of + dt.timedelta(days=n)
        # New business, the same under both policies.
        arrivals = sum(1 for _ in range(SLOTS) if rng.random() < per_day / SLOTS)
        for _ in range(arrivals):
            key = rng.choice(customers)
            amt = rng.choice(history)
            next_id += 1
            inv = Invoice(next_id, f"SIM/{next_id}", key, names[key], key, day + dt.timedelta(days=30), -30,
                          "USD", amt, amt, Decimal(0), Decimal(0), False, False, f"sim/{next_id}")
            book.append(Open(inv, day))
        open_now = [o for o in book if o.paid_on is None]
        # Contacts.
        if policy == "remit":
            by_customer: dict[int, list[Invoice]] = {}
            for o in open_now:
                cur = _as_of(o.inv, day)
                if cur.days_past_due >= -3:
                    by_customer.setdefault(cur.customer_key, []).append(cur)
            for key, rows in by_customer.items():
                plan = plan_customer(rows, Evidence(), day, done)
                for a in plan.actions:
                    if a.kind == "reminder":
                        reminded[key] = (day, a.stage)
                        contacts += 1
                        for i in a.invoices:
                            if i.days_past_due > 0:
                                first_contact.setdefault(i.invoice_id, day)
                    elif a.kind == "escalate":
                        escalated.add(key)
                    done.update(a.keys)
        if day.weekday() == 0:
            late = sorted((o for o in open_now if (day - o.inv.due_date).days > 0
                           and (policy == "manual" or o.inv.customer_key in escalated)),
                          key=lambda o: o.inv.due_date)
            seen: list[int] = []
            for o in late:
                if o.inv.customer_key not in seen:
                    seen.append(o.inv.customer_key)
            for key in seen[:MANUAL_PER_WEEK]:
                worst = max((day - o.inv.due_date).days for o in late if o.inv.customer_key == key)
                reminded[key] = (day, stage_for(worst))
                contacts += 1
                person_calls += 1
                for o in late:
                    if o.inv.customer_key == key:
                        first_contact.setdefault(o.inv.invoice_id, day)
        # Payments.
        for o in open_now:
            # The same draw for this invoice on this day under both policies.
            draw = random.Random(f"{seed}:{o.inv.invoice_id}:{n}").random()
            if (day - o.inv.due_date).days < -5:
                continue
            name, daily = habits[o.inv.customer_key]
            chance = daily
            last = reminded.get(o.inv.customer_key)
            if last and (day - last[0]).days < LIFT_DAYS:
                chance *= 1 + LIFT[last[1]] * lift_scale
            if draw < chance:
                o.paid_on = day
    end = as_of + dt.timedelta(days=DAYS)
    overdue = sum((o.inv.amount_open_usd for o in book if o.paid_on is None and o.inv.due_date < end),
                  Decimal(0))
    late_days = [(o.paid_on - o.inv.due_date).days for o in book
                 if o.paid_on and o.paid_on > o.inv.due_date]
    # Invoices that fell due during the run: days from going late to the first contact about them.
    went_late = [o for o in book if as_of < o.inv.due_date < end - dt.timedelta(days=14)
                 and (o.paid_on is None or o.paid_on > o.inv.due_date)]
    waits = [((first_contact[o.inv.invoice_id] - o.inv.due_date).days if o.inv.invoice_id in first_contact
              else (min(o.paid_on or end, end) - o.inv.due_date).days) for o in went_late]
    return {"policy": policy, "seed": seed, "lift_scale": lift_scale,
            "person_calls": person_calls,
            "days_late_before_first_contact": round(statistics.mean(waits), 1) if waits else None,
            "overdue_usd_at_end": float(overdue),
            "avg_days_late_when_paid": round(statistics.mean(late_days), 1) if late_days else None,
            "paid": sum(1 for o in book if o.paid_on), "contacts": contacts}


def compare(start, history, per_day, as_of) -> dict:
    """Every seed under both policies, at the full lift and at half of it, paired by seed."""
    runs = [play(start, history, per_day, as_of, s, p, scale)
            for scale in (1.0, 0.5) for s in SEEDS for p in ("manual", "remit")]

    def spread(vals):
        vals = sorted(vals)
        return {"mean": round(statistics.mean(vals), 1), "median": round(statistics.median(vals), 1),
                "low": vals[0], "high": vals[-1]}

    table = {}
    for scale in (1.0, 0.5):
        pick = {p: {r["seed"]: r for r in runs if r["policy"] == p and r["lift_scale"] == scale}
                for p in ("manual", "remit")}
        row = {}
        for f in ("overdue_usd_at_end", "avg_days_late_when_paid", "days_late_before_first_contact",
                  "contacts", "person_calls"):
            seeds = [s for s in SEEDS if pick["manual"][s][f] is not None and pick["remit"][s][f] is not None]
            row[f] = {p: spread([pick[p][s][f] for s in seeds]) for p in pick}
            diffs = [pick["remit"][s][f] - pick["manual"][s][f] for s in seeds]
            row[f]["remit_minus_manual"] = spread(diffs)
            row[f]["seeds_remit_lower"] = sum(d < 0 for d in diffs)
        table[f"lift x{scale}"] = row
    return {"as_of": as_of.isoformat(), "days": DAYS, "seeds": len(SEEDS), "habits": HABITS,
            "lift": LIFT, "lift_days": LIFT_DAYS, "manual_per_week": MANUAL_PER_WEEK,
            "start_open_invoices": len(start), "new_invoices_per_day": round(per_day, 3),
            "summary": table, "runs": runs}


def from_warehouse(conn, as_of: dt.date):
    """The open invoices and the recent invoice rate, read from the warehouse."""
    rows = conn.execute(
        """select i.invoice_id, i.invoice_ref, i.customer_key, i.customer_name, i.partner_id,
                  i.due_date, i.invoice_date, i.amount_open
           from intermediate.int_invoices i
           where i.move_type = 'out_invoice' and i.amount_open > 0 order by i.invoice_id""").fetchall()
    start = [Open(Invoice(r[0], r[1], r[2], r[3], r[4], r[5], (as_of - r[5]).days, "USD", r[7], r[7],
                          Decimal(0), Decimal(0), False, False, f"account.move/{r[0]}"), r[6])
             for r in rows]
    recent = conn.execute(
        """select amount from intermediate.int_invoices
           where move_type = 'out_invoice' and invoice_date > %s::date - 90 and invoice_date <= %s""",
        (as_of, as_of)).fetchall()
    history = [r[0] for r in recent] or [r.inv.amount_open_usd for r in start]
    return start, history, len(recent) / 90


def main(argv=None) -> int:
    import os

    import psycopg

    from edp.ask import AS_OF
    ap = argparse.ArgumentParser(prog="python -m remit.simulate")
    ap.add_argument("--out", default="remit/results/simulation.json")
    args = ap.parse_args(argv)
    as_of = dt.date.fromisoformat(AS_OF)
    with psycopg.connect(os.environ.get("EDP_DSN", "host=/tmp dbname=warehouse user=odoo")) as conn:
        start, history, per_day = from_warehouse(conn, as_of)
    result = compare(start, history, per_day, as_of)
    os.makedirs(os.path.dirname(args.out), exist_ok=True)
    with open(args.out, "w") as f:
        json.dump(result, f, indent=2, default=str)
    print(json.dumps(result["summary"], indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
