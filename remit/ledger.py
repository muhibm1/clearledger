"""Remit's own records, in the warehouse database under the `remit` schema.

- `proposals`: every action Remit proposed, with its evidence, its draft and its
  status: shadow (never to be applied), pending, approved, agreed (approved in
  suggest mode, so never written), rejected, applying, applied, skipped or failed.
- `done`: one row per thing done to an invoice (a stage's reminder, a flag on a
  version). Its primary key is what makes "never twice" hold across runs, crashes
  and two people approving at once.
- `audit`: append-only. Every status change, with who and why.
"""

from __future__ import annotations

import uuid

import psycopg
from psycopg.types.json import Jsonb

DDL = """
create schema if not exists remit;
create table if not exists remit.runs (
    run_id text primary key, as_of date not null, mode text not null
        check (mode in ('shadow', 'suggest', 'act')),
    drafter text not null, started_at timestamptz not null default now());
create table if not exists remit.proposals (
    proposal_id text primary key, run_id text not null references remit.runs,
    customer_key int not null, customer_name text not null,
    kind text not null check (kind in ('reminder', 'follow_up', 'flag', 'escalate')),
    stage int, reason text not null, target_invoice_id int not null,
    invoice_ids int[] not null, source_refs text[] not null, done_keys text[] not null,
    idempotency_key text not null, follow_up_on date,
    subject text, body text, drafted_by text, draft_problems text[] not null default '{}',
    notes text[] not null default '{}', evidence jsonb not null default '{}',
    status text not null check (status in ('shadow', 'pending', 'approved', 'agreed', 'rejected',
        'applying', 'applied', 'skipped', 'failed')),
    decided_by text, decided_at timestamptz, decision_note text,
    applied_at timestamptz, created_at timestamptz not null default now());
-- At most one live proposal per action: a rerun does not queue the same reminder again.
create unique index if not exists proposals_one_live on remit.proposals (idempotency_key)
    where status in ('pending', 'approved', 'applying');
create table if not exists remit.done (
    done_key text primary key, proposal_id text not null references remit.proposals,
    done_at timestamptz not null default now());
create table if not exists remit.audit (
    seq bigserial primary key, at timestamptz not null default now(),
    proposal_id text, event text not null, actor text not null, detail jsonb not null default '{}');
create or replace function remit.no_change() returns trigger language plpgsql as
    $$ begin raise exception 'remit.audit is append-only'; end $$;
drop trigger if exists audit_append_only on remit.audit;
create trigger audit_append_only before update or delete on remit.audit
    for each row execute function remit.no_change();
"""


class Ledger:
    """Reads and writes Remit's records. One connection, explicit transactions."""

    def __init__(self, conn: psycopg.Connection):
        self.conn = conn
        conn.execute(DDL)
        conn.commit()

    def audit(self, proposal_id: str | None, event: str, actor: str, **detail) -> None:
        self.conn.execute("insert into remit.audit (proposal_id, event, actor, detail) "
                          "values (%s, %s, %s, %s)", (proposal_id, event, actor, Jsonb(detail)))

    def done_keys(self) -> set[str]:
        """Everything already done, plus everything already waiting for a decision."""
        rows = self.conn.execute(
            "select done_key from remit.done union "
            "select unnest(done_keys) from remit.proposals "
            "where status in ('pending', 'approved', 'applying')").fetchall()
        return {r[0] for r in rows}

    def start_run(self, as_of, mode: str, drafter: str) -> str:
        run_id = f"{as_of.isoformat()}-{uuid.uuid4().hex[:8]}"
        self.conn.execute("insert into remit.runs values (%s, %s, %s, %s)", (run_id, as_of, mode, drafter))
        self.audit(None, "run_started", "remit", run_id=run_id, mode=mode, as_of=as_of.isoformat())
        return run_id

    def propose(self, run_id: str, mode: str, action, plan, draft: dict | None, drafted_by: str | None,
                problems: list[str], evidence: dict) -> str | None:
        """Records one proposal. Returns its id, or None when the same action is already live."""
        pid = f"p-{uuid.uuid4().hex[:12]}"
        status = "shadow" if mode == "shadow" else "pending"
        with self.conn.transaction():
            cur = self.conn.execute(
                """insert into remit.proposals (proposal_id, run_id, customer_key, customer_name, kind,
                       stage, reason, target_invoice_id, invoice_ids, source_refs, done_keys,
                       idempotency_key, follow_up_on, subject, body, drafted_by, draft_problems,
                       notes, evidence, status)
                   values (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                   on conflict do nothing returning proposal_id""",
                (pid, run_id, action.customer_key, action.customer_name, action.kind, action.stage,
                 action.reason, action.target_invoice_id, [i.invoice_id for i in action.invoices],
                 [i.source_ref for i in action.invoices], action.keys, action.idempotency_key,
                 action.follow_up_on, (draft or {}).get("subject"), (draft or {}).get("body"),
                 drafted_by, problems, plan.notes, Jsonb(evidence), status))
            if cur.fetchone() is None:
                return None
            self.audit(pid, "proposed", "remit", kind=action.kind, status=status, run_id=run_id)
        return pid

    def decide(self, proposal_id: str, approve: bool, actor: str, note: str = "",
               body: str | None = None) -> None:
        """A person approves (optionally with an edited body) or rejects a pending proposal."""
        with self.conn.transaction():
            row = self.conn.execute(
                "select p.status, p.kind, r.mode from remit.proposals p join remit.runs r using (run_id) "
                "where proposal_id = %s for update of p", (proposal_id,)).fetchone()
            if row is None:
                raise KeyError(proposal_id)
            # In suggest mode a yes is recorded as agreed: it measures the drafts, and is never written.
            yes = "approved" if row[2] == "act" else "agreed"
            if row[0] != "pending":
                raise ValueError(f"{proposal_id} is {row[0]}, not pending")
            if body is not None and row[1] != "reminder":
                raise ValueError("only a reminder's text can be edited")
            self.conn.execute(
                "update remit.proposals set status = %s, decided_by = %s, decided_at = now(), "
                "decision_note = %s, body = coalesce(%s, body) where proposal_id = %s",
                (yes if approve else "rejected", actor, note, body, proposal_id))
            self.audit(proposal_id, yes if approve else "rejected", actor, note=note,
                       edited=body is not None)
            if row[1] == "reminder":
                # A reminder's follow-up goes with it: approved or rejected together.
                linked = self.conn.execute(
                    """update remit.proposals f set status = %s, decided_by = %s, decided_at = now(),
                           decision_note = 'with its reminder'
                       from remit.proposals r
                       where r.proposal_id = %s and f.run_id = r.run_id and f.customer_key = r.customer_key
                         and f.kind = 'follow_up' and f.status = 'pending' returning f.proposal_id""",
                    (yes if approve else "rejected", actor, proposal_id)).fetchall()
                for (fid,) in linked:
                    self.audit(fid, yes if approve else "rejected", actor, note="with its reminder")

    def proposals(self, status: str | None = None, run_id: str | None = None) -> list[dict]:
        sql = "select * from remit.proposals where true"
        args: list = []
        if status:
            sql += " and status = %s"
            args.append(status)
        if run_id:
            sql += " and run_id = %s"
            args.append(run_id)
        cur = self.conn.execute(sql + " order by created_at, proposal_id", args)
        cols = [c.name for c in cur.description]
        return [dict(zip(cols, r)) for r in cur.fetchall()]

    def claim(self, proposal_id: str) -> list[str] | None:
        """Moves an approved proposal to applying and records its done keys, in one transaction.

        Returns the keys, or None when any of them is already done: someone else
        applied the same thing first, and this one is marked skipped.
        """
        with self.conn.transaction():
            row = self.conn.execute("select status, done_keys from remit.proposals where proposal_id = %s "
                                    "for update", (proposal_id,)).fetchone()
            if row is None or row[0] != "approved":
                return None
            taken = self.conn.execute("select done_key from remit.done where done_key = any(%s)",
                                      (row[1],)).fetchall()
            if taken:
                self.conn.execute("update remit.proposals set status = 'skipped' where proposal_id = %s",
                                  (proposal_id,))
                self.audit(proposal_id, "skipped", "remit", already_done=[t[0] for t in taken])
                return None
            for k in row[1]:
                self.conn.execute("insert into remit.done values (%s, %s)", (k, proposal_id))
            self.conn.execute("update remit.proposals set status = 'applying' where proposal_id = %s",
                              (proposal_id,))
            self.audit(proposal_id, "applying", "remit")
        return row[1]

    def finish(self, proposal_id: str, ok: bool, detail: str = "", recovered: bool = False) -> None:
        with self.conn.transaction():
            self.conn.execute("update remit.proposals set status = %s, applied_at = case when %s "
                              "then now() end where proposal_id = %s",
                              ("applied" if ok else "failed", ok, proposal_id))
            if not ok:
                # A failed write did not happen, so the invoice is not done.
                self.conn.execute("delete from remit.done where proposal_id = %s", (proposal_id,))
            self.audit(proposal_id, "applied" if ok else "failed", "remit", detail=detail,
                       recovered=recovered)
