"""Code check of the caveat questions: no model judges this part.

For each caveat question, every must_mention point has a list of alternative strings (must_mention_refs,
written by the company's author with the keys). A point counts as named when at least one of its
strings appears in the answer or explanation (case-insensitive, thousands separators ignored).
A caveat answer passes this check when every point is named.

usage: python check_refs.py runs/<run>.json -> prints the count and writes runs/<run>.refs.json
"""
import json
import re
import sys
from pathlib import Path

here = Path(__file__).parent
key = {r["id"]: r for r in map(json.loads, open(here / "questions.jsonl"))}
src = Path(sys.argv[1])
recs = {r["id"]: r for r in json.load(open(src))["records"]}


def norm(text: str) -> str:
    return re.sub(r"(?<=\d),(?=\d{3})", "", str(text)).lower()


out = []
for qid, q in key.items():
    if q["expected_behaviour"] != "answer_with_caveat":
        continue
    r = recs.get(qid) or {}
    text = norm(json.dumps(r.get("answer"), default=str) + " " + (r.get("explanation") or ""))
    points = [any(norm(s) in text for s in alts) for alts in q.get("must_mention_refs") or []]
    out.append({"id": qid, "points": points, "all": bool(points) and all(points)})
src.with_suffix(".refs.json").write_text(json.dumps(out, indent=1))
print(src.name, "caveats with every point named:", f"{sum(o['all'] for o in out)}/{len(out)}",
      "points named:", f"{sum(sum(o['points']) for o in out)}/{sum(len(o['points']) for o in out)}")
