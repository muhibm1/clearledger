"""Remit: the collections agent.

Each morning Remit reads the overdue-invoice list the warehouse builds, decides
the playbook's next step for every late customer, drafts the reminder, and
queues it for a person. Approved actions are written to Odoo once, through an
allowlist of three calls, with an audit line. See docs/collections/.

Code decides everything that matters: which invoices are late, which are held,
which stage applies, what may be written, and whether it already was. A model
only drafts the wording, and code checks the draft before anyone sees it.
"""
