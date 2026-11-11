#!/usr/bin/env python3
"""
Snapshot the public ALPS-Bench Codabench leaderboard for the project page.

Run by .github/workflows/update-leaderboard.yml. Reads phase_id from
docs/assets/data/leaderboard_config.json and writes the fields the page
shows to docs/assets/data/leaderboard_snapshot.json, only when they
changed. Moderation happens in the page (docs/assets/js/leaderboard.js).

Usage: python3 .github/scripts/snapshot_leaderboard.py
"""

import datetime as dt
import json
import os
import sys
import urllib.request

REPO = os.path.dirname(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
)
CONFIG = os.path.join(
    REPO, "docs", "assets", "data", "leaderboard_config.json"
)
OUT = os.path.join(
    REPO, "docs", "assets", "data", "leaderboard_snapshot.json"
)
API = "https://www.codabench.org/api/phases/{}/get_leaderboard/"


def main():
    with open(CONFIG, encoding="utf-8") as f:
        phase_id = json.load(f).get("phase_id")
    if not phase_id:
        sys.exit("phase_id is not set in leaderboard_config.json")

    req = urllib.request.Request(
        API.format(int(phase_id)) + "?page_size=all",
        headers={"User-Agent": "alps-bench-leaderboard-snapshot"},
    )
    with urllib.request.urlopen(req, timeout=60) as r:
        body = r.read().decode("utf-8", "replace")
    try:
        data = json.loads(body)
    except json.JSONDecodeError:
        sys.exit(
            f"Codabench did not return JSON (bot check?): {body[:200]!r}"
        )

    submissions = [
        {
            "id": s.get("id"),
            "owner": s.get("owner"),
            "created_when": s.get("created_when"),
            "fact_sheet_answers": s.get("fact_sheet_answers"),
            "scores": [
                {"column_key": x.get("column_key"), "score": x.get("score")}
                for x in s.get("scores", [])
            ],
        }
        for s in data.get("submissions", [])
    ]

    try:
        with open(OUT, encoding="utf-8") as f:
            previous = json.load(f).get("submissions")
    except (FileNotFoundError, json.JSONDecodeError):
        previous = None
    if submissions == previous:
        print(f"Unchanged ({len(submissions)} submissions).")
        return

    # timezone.utc rather than datetime.UTC so system Pythons < 3.11 work
    now = dt.datetime.now(dt.timezone.utc)  # noqa: UP017
    snapshot = {
        "fetched_at": now.replace(microsecond=0).isoformat(),
        "submissions": submissions,
    }
    with open(OUT, "w", encoding="utf-8") as f:
        json.dump(snapshot, f, indent=2, ensure_ascii=False)
        f.write("\n")
    print(f"Updated: {len(submissions)} submissions.")


if __name__ == "__main__":
    main()
