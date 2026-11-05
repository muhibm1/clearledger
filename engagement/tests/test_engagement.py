"""The engagement demo passes, and the pack keeps its own writing rules."""

import re
from pathlib import Path

from engagement import demo

HERE = Path(__file__).resolve().parent.parent
PAGES = sorted(HERE.glob("*.md"))
BANNED = re.compile(r"(?i)kubernetes|databricks|fine[- ]?tun|mlops|clearance|paddock|workhorse|"
                    r"tannhauser|claude code|anthropic")


def test_every_seeded_failure_is_handled():
    lines = []
    result = demo.run(say=lines.append)
    failed = [t["trap"] for t in result["traps"] if not t["passed"]]
    assert failed == []
    assert result["change_guard"]["passed"] is not False
    assert result["passed"] == result["total"] >= 11
    assert "SIMULATED ENGAGEMENT" in lines[0]


def test_recorded_results_are_the_ones_the_pack_quotes():
    sim = demo.recorded_simulation()["full"]
    assert sim["days_late_before_first_contact"]["manual"] == 5.0
    assert sim["days_late_before_first_contact"]["remit"] == 1.0
    assert sim["person_calls"]["seeds_remit_lower"] == 20
    assert sim["overdue_usd_at_end"]["difference"] == 0.0
    _, drills = demo.recorded_drills()
    assert all(drills.values()) and len(drills) == 5


def test_every_page_says_simulated_on_its_first_screen():
    assert len(PAGES) == 9
    for page in PAGES:
        head = "\n".join(page.read_text().splitlines()[:6])
        assert "Simulated engagement" in head, page.name


def test_no_dashes_and_no_banned_claims():
    texts = {p.name: p.read_text() for p in PAGES}
    texts["demo.py"] = (HERE / "demo.py").read_text()
    for name, text in texts.items():
        assert "—" not in text and "–" not in text, name
        assert not BANNED.search(text), (name, BANNED.search(text))
