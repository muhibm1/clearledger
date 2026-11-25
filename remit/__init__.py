"""Remit: the collections agent.

Each morning Remit reads the warehouse's overdue-invoice list, decides the playbook's next step for
every late customer, drafts the reminder and queues it for a person. Approved actions are written to Odoo
once, through an allowlist of three calls, with an audit line. See docs/collections/.

Code decides everything that matters; a model only drafts the wording, and code checks the draft.
"""
