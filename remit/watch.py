"""Checks that Remit is doing its job, for the daily run and for whoever is on call.

- last_run       Remit ran in the last 26 hours
- stalled_queue  nothing has waited for a decision longer than REMIT_STALE_HOURS (48 by default):
                 a queue nobody works means late customers hear nothing, which is the failure
                 a collections tool is there to prevent
- stuck_apply    nothing has sat half-written for more than an hour: a crash `apply` did not recover
- failed_apply   no write to Odoo failed in the last 24 hours

Results use the platform's monitor format and go to the same alerts file and webhook.
"""

from __future__ import annotations

import os

from edp.monitor import Result

STALE_HOURS = float(os.environ.get("REMIT_STALE_HOURS", "48"))


def check(conn) -> list[Result]:
    """Every check, worst first."""
    one = lambda sql, *a: conn.execute(sql, a).fetchone()  # noqa: E731
    out: list[Result] = []
    last = one("select max(started_at), now() - max(started_at) > interval '26 hours' from remit.runs")
    if last[0] is None or last[1]:
        out.append(Result("last_run", "fail", f"Remit has not run since {last[0] or 'ever'}"))
    else:
        out.append(Result("last_run", "ok", f"last run {last[0]:%Y-%m-%d %H:%M}"))
    n, oldest = one("select count(*), min(created_at) from remit.proposals where status = 'pending' "
                    "and created_at < now() - make_interval(hours => %s)", int(STALE_HOURS))
    out.append(Result("stalled_queue", "warn", f"{n} proposals have waited more than {STALE_HOURS:g} hours "
                      f"for a decision, the oldest since {oldest:%Y-%m-%d}") if n else
               Result("stalled_queue", "ok", "the queue is being worked"))
    (stuck,) = one("select count(*) from remit.proposals p where status = 'applying' and exists ("
                   "select 1 from remit.audit a where a.proposal_id = p.proposal_id and a.event = 'applying' "
                   "and a.at < now() - interval '1 hour')")
    out.append(Result("stuck_apply", "fail", f"{stuck} proposals were half written and not recovered; "
                      "run `python -m remit apply`") if stuck else
               Result("stuck_apply", "ok", "nothing half written"))
    (failed,) = one("select count(*) from remit.audit where event = 'failed' and at > now() - interval '24 hours'")
    out.append(Result("failed_apply", "fail", f"{failed} writes to Odoo failed in the last 24 hours; "
                      "see remit.audit") if failed else
               Result("failed_apply", "ok", "no failed writes"))
    order = {"fail": 0, "warn": 1, "ok": 2}
    return sorted(out, key=lambda r: order[r.status])
