"""Voight: a training ground for finance agents, built from the simulated company's real Odoo.

An episode is a fresh copy of the company's Odoo database with one task planted
in it (an unapplied payment, a credit note, an ambiguous remittance). The agent
works through a fixed set of tools. When it stops, a checker reads the copy's
database directly and scores the result, so the agent cannot score well by
saying it did the work. See voight/README.md.
"""
