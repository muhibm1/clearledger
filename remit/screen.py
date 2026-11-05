"""Screens free text from Odoo for instructions aimed at an agent.

Invoice notes, customer names and chatter are written by anyone with access to
Odoo, so Remit reads them as data. An invoice whose text tells an agent, or
anyone, to waive, write off or skip collection is held for a person. This is a
tripwire, not the defence: even if it misses, the model never decides an action
and the allowlist has no call that could write anything off.
"""

import re
import unicodedata

# Look-alike characters an attacker might use to slip past the patterns.
_CONFUSABLES = str.maketrans({"а": "a", "е": "e", "о": "o", "р": "p",
                              "с": "c", "х": "x", "і": "i", "​": "",
                              "‌": "", "‍": "", "⁠": ""})
_PATTERNS = [
    r"\b(write|writing|written)[\s-]*(it|this|the balance|the invoice)?\s*off\b",
    r"\bwaive[sd]?\b", r"\bforgive\b", r"\bcancel (the|this) (invoice|balance|debt)\b",
    r"\bmark (it |this |the invoice )?(as )?paid\b", r"\bset (the )?balance to (zero|0)\b",
    r"\b(ignore|disregard) (all |any |the |your )?(previous |prior |above )?(instructions|rules|playbook)\b",
    r"\b(do not|don't|stop) (send|contact|remind|chase)\b",
    r"\b(agent|assistant|ai|bot|remit)\s*[:,]",
    r"\bapproved by (the )?(cfo|ceo|finance|controller)\b",
]
_RX = re.compile("|".join(_PATTERNS), re.IGNORECASE)


def normalise(text: str) -> str:
    """Folds look-alike letters and invisible characters, then compatibility forms."""
    return unicodedata.normalize("NFKC", text.translate(_CONFUSABLES))


def instructions_in(text: str) -> list[str]:
    """The phrases in the text that read as an instruction; empty when there are none."""
    if not text:
        return []
    return [m.group(0) for m in _RX.finditer(normalise(text))]
