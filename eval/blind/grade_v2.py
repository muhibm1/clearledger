"""Grader v2 for a blind company's run: Opus judges, with no truncation, one verdict per part, three votes.

Fixed before company L is scored. Changes from each company's grade_by_model.py:
- The whole answer and explanation are shown (v1 cut them at 800 and 700 characters).
- The grader returns one verdict for the figure (or the decline) and one per must-mention point, so a
  caveat answer is correct only if the figure and every point are; nothing rests on one overall call.
- Three independent votes; each verdict is the majority.
- The grader never sees which system or arm wrote the answer.

usage: python grade_v2.py <company folder> <run.json>  ->  <run>.graded2.json and a summary line
"""
import concurrent.futures as cf
import json
import subprocess
import sys
from pathlib import Path

VOTES = 3
company, src = Path(sys.argv[1]), Path(sys.argv[2])
key = {r["id"]: r for r in map(json.loads, open(company / "questions.jsonl"))}
recs = {r["id"]: r for r in json.load(open(src))["records"]}

PROMPT = """You grade one answer from a finance assistant against an answer key.

Question: {q}
Key (the correct figure or facts, from the books): {key}
Expected behaviour: {beh}
Points a careful answer must state (numbered): {points}

The assistant's reply.
Status: {status}
Answer: {got}
Explanation: {why}

Judge each part separately, in substance not wording:
- "figure": for behaviour "answer" or "answer_with_caveat", true if the answer states the key's figure or
  facts that the question asks for (numbers within 0.01, same entities; extra true detail is fine; a figure
  stated only as a "corrected" alternative while a different number is given as the answer is false; if the
  assistant declined, false). For behaviour "cannot_tell", true if the assistant declined or asked for
  clarification and did not give a made-up figure as the answer.
- "points": one true/false per numbered point, true if the answer or explanation states that point. Empty
  list if there are no points.
Reply with JSON only: {{"figure": true|false, "points": [true|false, ...], "reason": "<one short sentence>"}}"""


def vote(prompt: str, n_points: int) -> dict | None:
    for _ in range(3):
        out = subprocess.run(["claude", "-p", "--model", "opus", "--output-format", "text", "--tools", "",
                              "--setting-sources", "", "--no-session-persistence"],
                             input=prompt, capture_output=True, text=True, timeout=300).stdout
        try:
            j = json.loads(out[out.index("{"): out.rindex("}") + 1])
            pts = [bool(x) for x in j.get("points") or []][:n_points]
            return {"figure": bool(j.get("figure")), "points": pts + [False] * (n_points - len(pts)),
                    "reason": j.get("reason", "")}
        except Exception:
            pass
    return None


def one(qid: str) -> dict:
    q, r = key[qid], recs.get(qid) or {}
    points = q.get("must_mention") or [] if q["expected_behaviour"] == "answer_with_caveat" else []
    prompt = PROMPT.format(q=q["question"], key=q["expected_answer"], beh=q["expected_behaviour"],
                           points=json.dumps({i + 1: p for i, p in enumerate(points)}),
                           status=r.get("status"), got=json.dumps(r.get("answer"), default=str),
                           why=r.get("explanation") or "")
    votes = [v for v in (vote(prompt, len(points)) for _ in range(VOTES)) if v]
    if not votes:
        return {"id": qid, "behaviour": q["expected_behaviour"], "correct": False, "reason": "grader failed",
                "failed": True}
    maj = lambda xs: sum(xs) * 2 > len(xs)
    figure = maj([v["figure"] for v in votes])
    pts = [maj([v["points"][i] for v in votes]) for i in range(len(points))]
    return {"id": qid, "behaviour": q["expected_behaviour"], "figure": figure, "points": pts,
            "correct": figure and all(pts), "votes": len(votes), "reasons": [v["reason"] for v in votes]}


with cf.ThreadPoolExecutor(6) as pool:
    res = list(pool.map(one, list(key)))
src.with_name(src.stem + ".graded2.json").write_text(json.dumps(res, indent=1))
by = {}
for x in res:
    by.setdefault(x["behaviour"], []).append(x["correct"])
print(src.name, f"{sum(x['correct'] for x in res)}/{len(res)}",
      {k: f"{sum(v)}/{len(v)}" for k, v in sorted(by.items())},
      "grader failures:", sum(1 for x in res if x.get("failed")))
