#!/usr/bin/env python3
"""
Snapshot the public ALPS-Bench Codabench leaderboard for the project page.

Run by .github/workflows/update-leaderboard.yml. Reads phase_id from
docs/assets/data/leaderboard_config.json and writes the fields the page
shows to docs/assets/data/leaderboard_snapshot.json, only when they
changed. Moderation happens in the page (docs/assets/js/leaderboard.js).

If CODABENCH_API_TOKEN is set, the request is sent as
"Authorization: Token <token>" (Django REST Framework token auth).

Transient Codabench failures (network, 5xx, bot check, unexpected payload)
keep the previous snapshot and exit 0 with a warning; a rejected token
or a missing phase_id exits 1.

Usage: python3 .github/scripts/snapshot_leaderboard.py
"""

import datetime as dt
import http.client
import json
import os
import sys
import urllib.error
import urllib.request
from typing import NoReturn

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


class NoRedirect(urllib.request.HTTPRedirectHandler):
    # urllib would forward the Authorization header to the redirect target
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def skip(reason) -> NoReturn:
    print(f"::warning title=Leaderboard snapshot skipped::{reason}")
    sys.exit(0)


def main():
    with open(CONFIG, encoding="utf-8") as f:
        phase_id = json.load(f).get("phase_id")
    if not phase_id:
        sys.exit("phase_id is not set in leaderboard_config.json")

    headers = {}
    token = os.environ.get("CODABENCH_API_TOKEN", "").strip()
    if token:
        headers["Authorization"] = f"Token {token}"
    req = urllib.request.Request(
        API.format(int(phase_id)) + "?page_size=all", headers=headers
    )
    print(f"Authenticated request: {bool(token)}")
    try:
        with urllib.request.build_opener(NoRedirect).open(
            req, timeout=60
        ) as r:
            body = r.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as e:
        detail = (
            f"Codabench returned HTTP {e.code}"
            f" (Location: {e.headers.get('Location')}):"
            f" {e.read(300).decode('utf-8', 'replace')!r}"
        )
        if e.code in (401, 403):
            sys.exit(f"::error::{detail} - check CODABENCH_API_TOKEN")
        skip(detail)
    except (OSError, http.client.HTTPException) as e:
        skip(f"Request to Codabench failed: {e!r}")
    try:
        data = json.loads(body)
    except json.JSONDecodeError:
        skip(f"Codabench did not return JSON (bot check?): {body[:200]!r}")
    if not isinstance(data, dict) or not isinstance(
        data.get("submissions"), list
    ):
        skip(f"Unexpected response from Codabench: {body[:200]!r}")

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
        for s in data["submissions"]
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
