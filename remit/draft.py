"""Drafts the reminder text, and checks every draft in code before a person sees it.

The model gets the customer's name, the invoices and the tone for the stage. It
never sees notes, chatter or anything that decides an action. Whatever it
returns, `check_draft` must pass: every invoice and its exact amount named, no
other amount, nothing that offers a discount, a write-off or a new deadline the
playbook did not set, and no links. A draft that fails twice is replaced by the
playbook's own template, and the proposal says so.
"""

from __future__ import annotations

import re
from decimal import Decimal

from .playbook import Action, STAGE_NAMES

SYMBOLS = {"USD": "$", "EUR": "€", "GBP": "£"}
TONE = {
    0: "A friendly heads-up that these invoices fall due soon. No pressure, no mention of lateness.",
    1: "A friendly reminder that these invoices are now past due. Assume it was missed.",
    2: "A firm, polite reminder. Ask for payment or a payment date this week.",
    3: "A final notice. Say that new orders go on hold if payment has not arrived by the hold date.",
}
FORBIDDEN = re.compile(
    r"(?i)\b(discount|write[\s-]*off|waive|forgive|settle(ment)? for|legal action|lawyer|court|"
    r"collections agency|credit (score|report)|interest|penalt(y|ies)|late fee)s?\b")
SCHEMA = {"type": "object", "required": ["subject", "body"], "additionalProperties": False,
          "properties": {"subject": {"type": "string", "maxLength": 120},
                         "body": {"type": "string", "maxLength": 2000}}}
SYSTEM = ("You write short accounts-receivable emails for a small consulting company. "
          "Use only the invoices and amounts given, exactly as written. Never offer a discount, "
          "a payment plan, a write-off or a new deadline. No links. Plain text, under 150 words. "
          "Sign off as 'Accounts receivable'.")


def money(amount: Decimal, currency: str) -> str:
    """An amount as the customer's invoice shows it: €1,234.50 or $1,234.50."""
    sym = SYMBOLS.get(currency)
    text = f"{amount:,.2f}"
    return f"{sym}{text}" if sym else f"{text} {currency}"


def facts(action: Action) -> list[tuple[str, str, str]]:
    """(invoice reference, amount owed as shown to the customer, due date) per invoice."""
    return [(i.invoice_ref, money(i.amount_open_invoice_currency, i.currency), i.due_date.isoformat())
            for i in action.invoices]


def prompt_for(action: Action) -> str:
    """The only thing the model is shown."""
    lines = "\n".join(f"- {ref}: {amt} owed, due {due}" for ref, amt, due in facts(action))
    extra = (f"\nHold date for new orders: {action.follow_up_on.isoformat()}."
             if action.stage == 3 and action.follow_up_on else "")
    return (f"Customer: {action.customer_name}\nTone: {TONE[action.stage]}\n"
            f"Invoices:\n{lines}{extra}\n\nWrite the email.")


def template(action: Action) -> dict:
    """The playbook's own wording. Used when there is no model, or the model's draft fails."""
    rows = "\n".join(f"  {ref}  {amt}  due {due}" for ref, amt, due in facts(action))
    opening = {
        0: "A quick note that the invoices below fall due in the next few days.",
        1: "Our records show the invoices below are now past due. If payment is already on its way, thank you, and please ignore this note.",
        2: "The invoices below are past due. Could you send payment, or let us know the date it will be made, this week?",
        3: "This is a final notice for the invoices below.",
    }[action.stage]
    closing = (f" If payment has not arrived by {action.follow_up_on.isoformat()}, new orders will be put on hold."
               if action.stage == 3 and action.follow_up_on else "")
    subject = {0: "Upcoming invoices", 1: "Payment reminder", 2: "Past-due invoices",
               3: "Final notice"}[action.stage] + f" for {action.customer_name}"
    body = (f"Hello {action.customer_name} team,\n\n{opening}\n\n{rows}\n\n"
            f"Thank you.{closing}\n\nAccounts receivable")
    return {"subject": subject, "body": body}


_AMOUNT = re.compile(r"(?:[$€£]\s?\d[\d,]*(?:\.\d+)?|\d[\d,]*\.\d{2}\s?(?:USD|EUR|GBP)\b)")


def check_draft(draft: dict, action: Action) -> list[str]:
    """Every reason the draft may not be shown; empty means it passed."""
    problems = []
    text = f"{draft.get('subject', '')}\n{draft.get('body', '')}"
    if not draft.get("body", "").strip():
        problems.append("empty body")
    allowed = set()
    for ref, amt, _ in facts(action):
        if ref not in text:
            problems.append(f"does not name invoice {ref}")
        if amt not in text:
            problems.append(f"does not state {amt} for {ref}")
        allowed.add(amt.replace(" ", ""))
    for found in _AMOUNT.findall(text):
        if found.replace(" ", "") not in allowed:
            problems.append(f"names an amount that is not owed: {found}")
    if m := FORBIDDEN.search(text):
        problems.append(f"uses a forbidden term: {m.group(0)}")
    if re.search(r"(?i)https?://|www\.", text):
        problems.append("contains a link")
    if len(draft.get("body", "")) > 2000:
        problems.append("too long")
    return problems


def write(action: Action, model=None) -> tuple[dict, str, list[str]]:
    """The draft to show a person, where it came from, and the problems seen on the way."""
    if action.kind != "reminder":
        raise ValueError(f"only reminders are drafted, not {action.kind}")
    if model is None:
        return template(action), "template", []
    seen: list[str] = []
    for _ in range(2):
        draft = model(SYSTEM, prompt_for(action), SCHEMA)
        problems = check_draft(draft, action)
        if not problems:
            return draft, "model", seen
        seen += problems
    return template(action), "template after the model's draft failed twice", seen
