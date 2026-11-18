# job-feed

Daily candidate feed for the `job-hunt` Claude skill. GitHub Actions runs `feed.py` every morning
and commits `data/job-feed/feed.json`; the skill only **reads** that file (scheduled Claude runs
may not execute code pulled from a repo, but reading data is fine).

- Sources: SimplifyJobs + vanshb03 Summer 2027 lists, plus Greenhouse / Lever / Ashby / Workday public
  job-board APIs of AV and robotics companies (`boards.json` + boards discovered from Simplify URLs).
- Filter: the loose two-tier filter that used to live in the skill appendix (`hunt_v3.py`), plus a fix
  for postings Simplify mislabels as "Summer 2026".
- To change what counts as core / triage, or add a company to `ROBOT_CO` / `AV_CO`, edit `feed.py`.
- To poll another company's board, add a line to `boards.json`.
- Run by hand: Actions tab -> job-feed -> Run workflow. Local test:
  `python3 -I job-feed/feed.py --simplify listings.json --vansh vansh.json --no-ats --out /tmp/feed.json`
