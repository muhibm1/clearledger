"""The investigating review: it may check the books a few times, then gives caveats and a status."""

from analyst import review


def _found():
    return {"issues": "", "names": [], "wide": False, "records": ""}


def test_checks_run_through_the_given_runner_and_stop_at_the_limit():
    ran = []

    def model(system, prompt, schema):
        return {"action": "check", "sql": "select 1", "why": "look", "status": "keep", "caveats": [], "answer": ""}

    def run(sql):
        ran.append(sql)
        return {"ok": True, "rows": [{"x": 1}], "error": None}

    out = review.investigate(None, None, model, "sys", "Q?", "select 2", [], 5, "", "answer", _found(), [], run)
    assert len(ran) == review.MAX_CHECKS and len(out["checks"]) == review.MAX_CHECKS


def test_the_final_word_carries_its_caveats_and_status_and_sees_earlier_checks():
    seen = []

    def model(system, prompt, schema):
        seen.append(prompt)
        if len(seen) == 1:
            return {"action": "check", "sql": "select 1", "why": "currency of the payment", "status": "keep",
                    "caveats": [], "answer": ""}
        return {"action": "final", "sql": "", "why": "", "status": "not_answerable",
                "caveats": ["Only a bank statement shows what arrived."], "answer": ""}

    out = review.investigate(None, None, model, "sys", "Q?", "select 2", [], 5, "", "answer", _found(),
                             ["bank statements"], lambda s: {"ok": False, "rows": [], "error": "boom"})
    assert out["status"] == "not_answerable" and out["caveats"] == ["Only a bank statement shows what arrived."]
    assert "currency of the payment" in seen[1] and "ERROR: boom" in seen[1] and "bank statements" in seen[1]
