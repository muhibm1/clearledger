"""A local open model as a Voight agent, with every step's tokens kept for training.

Episodes run in threads, one per database slot. Each thread asks the shared
`Batcher` for its next tool call; the batcher waits until every live episode
has asked (or a short timeout passes), then generates all their replies in one
batched call. On a CPU that is several times faster than one episode at a time.

Every reply is recorded as (prompt token ids, completion token ids) under its
episode, which is what the trainer needs to compute log-probabilities later.
"""

from __future__ import annotations

import json
import re
import threading
import time

import torch

SYSTEM = ("You are a careful accounts-receivable clerk working through tools. Reply with the next tool "
          "call only, as one JSON object: {\"tool\": <name>, \"args\": {...}}. Follow the written policy "
          "exactly; when it says escalate, escalate.")
_local = threading.local()


def compact(result: dict) -> str:
    """A tool result as short JSON, so long histories fit a small model's context."""
    return json.dumps(result, separators=(",", ":"))


def messages(brief: str, history: list) -> list[dict]:
    """The chat the model sees: the brief, then each call and its result as turns."""
    msgs = [{"role": "system", "content": SYSTEM}, {"role": "user", "content": brief}]
    for call, result in history:
        shown = {"tool": call.get("tool"), "args": call.get("args") or {}}
        msgs.append({"role": "assistant", "content": json.dumps(shown, separators=(",", ":"))})
        msgs.append({"role": "user", "content": f"Result: {compact(result)}\nNext call?"})
    return msgs


def parse(text: str) -> dict | None:
    """The first JSON object in the reply with a `tool` key, or None."""
    for m in re.finditer(r"\{", text):
        depth = 0
        for j in range(m.start(), len(text)):
            depth += text[j] == "{"
            depth -= text[j] == "}"
            if depth == 0:
                try:
                    obj = json.loads(text[m.start():j + 1])
                except json.JSONDecodeError:
                    break
                if isinstance(obj, dict) and "tool" in obj:
                    return obj
                break
    return None


class Batcher:
    """Collects tool-call requests from episode threads and answers them in batches."""

    def __init__(self, model, tok, temperature: float = 1.0, max_new_tokens: int = 96, wait: float = 1.0):
        self.model, self.tok = model, tok
        self.temperature, self.max_new_tokens, self.wait = temperature, max_new_tokens, wait
        self.lock = threading.Condition()
        self.pending: list[dict] = []
        self.live = 0
        self.records: dict[str, list[tuple[list[int], list[int]]]] = {}
        self.stats = {"calls": 0, "batches": 0, "gen_seconds": 0.0, "unparsed": 0}
        self._stop = False
        self._worker = threading.Thread(target=self._loop, daemon=True)
        self._worker.start()

    def episode(self, key: str):
        """Marks the calling thread as running episode `key`; returns the callback that ends it."""
        _local.key = key
        with self.lock:
            self.live += 1
            self.records[key] = []

        def done():
            """Ends the episode, so the batcher stops waiting for it."""
            with self.lock:
                self.live -= 1
                self.lock.notify_all()
        return done

    def __call__(self, brief: str, history: list) -> dict:
        """The agent interface episode.run expects; blocks until this call's batch is generated."""
        req = {"key": _local.key, "msgs": messages(brief, history), "out": None}
        with self.lock:
            self.pending.append(req)
            self.lock.notify_all()
            while req["out"] is None:
                self.lock.wait()
        return req["out"]

    def _loop(self):
        """Generates for everything pending once all live episodes have asked, or after a short wait."""
        while not self._stop:
            with self.lock:
                self.lock.wait_for(lambda: self.pending or self._stop, timeout=1)
                if not self.pending:
                    continue
                deadline = time.time() + self.wait
                while len(self.pending) < self.live and time.time() < deadline:
                    self.lock.wait(timeout=max(0.0, deadline - time.time()))
                batch, self.pending = self.pending, []
            outs = self._generate([r["msgs"] for r in batch])
            with self.lock:
                for req, (prompt_ids, completion_ids, text) in zip(batch, outs):
                    self.records[req["key"]].append((prompt_ids, completion_ids))
                    call = parse(text)
                    if call is None:
                        self.stats["unparsed"] += 1
                        call = {"tool": "finish", "args": {"summary": "reply was not a tool call"},
                                "raw": text[:200]}
                    req["out"] = call
                self.lock.notify_all()

    @torch.no_grad()
    def _generate(self, chats: list[list[dict]]):
        """One batched, left-padded sampling call; returns prompt ids, completion ids and text per chat."""
        t = time.time()
        prompts = [self.tok.apply_chat_template(c, tokenize=False, add_generation_prompt=True) for c in chats]
        self.tok.padding_side = "left"
        enc = self.tok(prompts, return_tensors="pt", padding=True).to(self.model.device)
        sample = self.temperature > 0
        kwargs = {"do_sample": True, "temperature": self.temperature, "top_p": 1.0, "top_k": 0} if sample \
            else {"do_sample": False}
        out = self.model.generate(**enc, max_new_tokens=self.max_new_tokens,
                                  pad_token_id=self.tok.pad_token_id, **kwargs)
        width = enc["input_ids"].shape[1]
        eos = self.tok.eos_token_id
        results = []
        for i in range(len(chats)):
            prompt_ids = enc["input_ids"][i][enc["attention_mask"][i].bool()].tolist()
            comp = out[i, width:].tolist()
            if eos in comp:
                comp = comp[:comp.index(eos) + 1]
            else:
                comp = [x for x in comp if x != self.tok.pad_token_id]
            results.append((prompt_ids, comp, self.tok.decode(comp, skip_special_tokens=True)))
        self.stats["calls"] += len(chats)
        self.stats["batches"] += 1
        self.stats["gen_seconds"] += time.time() - t
        return results

    def close(self):
        """Stops the worker thread."""
        self._stop = True
        with self.lock:
            self.lock.notify_all()


DEVICE = "cuda" if torch.cuda.is_available() else "cpu"


def load(name: str, adapter: str | None = None, dtype=torch.float32):
    """A causal LM and its tokenizer, optionally with a LoRA adapter on top, on the GPU when there is one."""
    from transformers import AutoModelForCausalLM, AutoTokenizer
    tok = AutoTokenizer.from_pretrained(name)
    if tok.pad_token is None:
        tok.pad_token = tok.eos_token
    model = AutoModelForCausalLM.from_pretrained(name, torch_dtype=dtype).to(DEVICE)
    if adapter:
        from peft import PeftModel
        model = PeftModel.from_pretrained(model, adapter, is_trainable=True)
    model.eval()
    return model, tok
