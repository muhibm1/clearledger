"""A model as an episode agent: one structured reply per tool call.

It uses the analyst's `CommandLineModel` (the model's own command line, signed in
through the subscription, tools off, reply shaped by a JSON schema), so a
baseline run spends no API credit. Each step the model sees the brief and every
call and result so far, and answers with the next tool call.
"""

from __future__ import annotations

import json

from analyst.model import CommandLineModel, ModelError

from .tools import NAMES

SCHEMA = {"type": "object", "required": ["tool", "args"], "additionalProperties": False,
          "properties": {"thought": {"type": "string", "maxLength": 600},
                         "tool": {"type": "string", "enum": sorted(NAMES)},
                         "args": {"type": "object"}}}
SYSTEM = ("You are a careful accounts-receivable clerk working through tools. Reply with the next tool "
          "call only, as JSON. Follow the written policy exactly; when it says escalate, escalate.")


class ModelAgent:
    """Calls the model once per step; a failed call ends the episode as `finish`."""

    def __init__(self, model: str = "sonnet", usage_log=None, call=None):
        self.model = call or CommandLineModel(model, usage_log=usage_log)

    def __call__(self, brief: str, history: list) -> dict:
        steps = "\n".join(f"Call {i + 1}: {json.dumps(c)}\nResult: {json.dumps(r)}"
                          for i, (c, r) in enumerate(history)) or "(no calls yet)"
        prompt = f"{brief}\n\nSo far:\n{steps}\n\nWhat is the next call?"
        try:
            out = self.model(SYSTEM, prompt, SCHEMA)
        except ModelError as exc:
            return {"tool": "finish", "args": {"summary": f"model failed: {exc}"[:200]}}
        return {"tool": out.get("tool", ""), "args": out.get("args") or {}, "thought": out.get("thought", "")}
