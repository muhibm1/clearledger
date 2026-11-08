# 90-second video script

> **Simulated engagement.** The first frame says so, on screen, for its whole duration.

About 210 spoken words, read at a calm pace. Screen recording of a terminal and a few pages from
this folder; no music needed.

| Time | On screen | Voice |
| --- | --- | --- |
| 0:00 to 0:08 | Title card: "A collections engagement, start to finish. Simulated: the client is invented; the system and the numbers are real runs." | This is a simulated customer engagement. The company is invented. The ERP, the agent and every number are real. |
| 0:08 to 0:22 | `01-discovery.md`, the "How the work is done today" table | A distributor runs on Odoo. One person chases late invoices every Monday, oldest first. Nobody can trace a balance to its invoices. Week one is interviews, not code. |
| 0:22 to 0:35 | `clearledger trace revenue_by_customer northwind` output | First, a warehouse that reads Odoo through its API and traces every number. Why does Northwind owe twenty six thousand dollars? Here are the invoices and payments, and the note that Northwind exists twice. |
| 0:35 to 0:55 | `python -m engagement`, the seeded failures scrolling: PASS lines | Then Remit, a collections agent. It plans, drafts and waits for a person. Here we plant twelve failures. Invoice text telling it to write off a balance. A customer in dispute. A model talked into a discount. A double run. Each one is held or refused. Nothing reaches Odoo that a person did not approve. |
| 0:55 to 1:05 | `02-rollout.md`, the stages table | It went live in stages: shadow, then suggest, then act. Each one undone in minutes. |
| 1:05 to 1:20 | The demo's simulation rows | The result. Late customers hear from the company after one day instead of five. About eighteen fewer calls in ninety days. And the late balance did not move, because most of it was already very old. |
| 1:20 to 1:30 | `06-90-day-review.md`, "Recommendation" | So the next step is a pilot that measures what an early reminder is worth. Everything here runs with one command. |

## Recording notes

- Run `python -m engagement` once before recording so the ChangeLens step is warm; the run takes about
  10 seconds.
- Keep the "Simulated" label visible in the first frame and in the terminal banner.
- Use the numbers exactly as the demo prints them; do not round differently in the voiceover.
