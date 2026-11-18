# job-feed

Public daily internship feed for KUNG's `job-hunt` Claude skill (Robotics / AV / Embedded, Summer 2027).

- GitHub Actions runs `job-feed/feed.py` twice a day and commits `data/job-feed/feed.json`.
- The skill reads it with no credentials:
  `https://raw.githubusercontent.com/RICHKUNG/job-feed/main/data/job-feed/feed.json`
- Details (sources, filter, how to add a company): [job-feed/README.md](job-feed/README.md).

This repo is public on purpose: scheduled Claude runs can't reach private repos. Keep secrets and personal
data out of it.
