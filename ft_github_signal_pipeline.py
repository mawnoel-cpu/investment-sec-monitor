"""Read-only GitHub activity collector for FT Game Intelligence.

Stage 1 is deliberately isolated: it reads curated public company-controlled
repositories and emits compact activity snapshots. It does not write Google
Sheets, create Evidence, change allocations, or make trading decisions.

The later workbook writer will consume the same snapshots only after this
read-only collector has passed live validation on GitHub Actions.
"""

from __future__ import annotations

import argparse
import json
import os
from datetime import datetime, timedelta, timezone
from typing import Any
from zoneinfo import ZoneInfo

API_ROOT = "https://api.github.com"
TORONTO = ZoneInfo("America/Toronto")
GAME_END = datetime(2026, 11, 27, 23, 59, 59, tzinfo=TORONTO)
WINDOW_DAYS = 28
MAX_PAGE = 100
REQUESTS_PER_REPO = 4

# Curated rather than inferred. These are official/company-controlled repositories
# with a plausible connection to the FT-game holding or qualified rotation role.
# GitHub activity is useful only where software/developer adoption can illuminate
# product/ecosystem momentum; low-signal issuers are intentionally omitted.
WATCHLIST = [
    {
        "ticker": "NVDA",
        "company": "NVIDIA",
        "repository": "NVIDIA/TensorRT-LLM",
        "role": "AI inference / GPU software ecosystem",
    },
    {
        "ticker": "META",
        "company": "Meta Platforms",
        "repository": "meta-llama/PurpleLlama",
        "role": "Llama security / open-source AI ecosystem",
    },
    {
        "ticker": "PANW",
        "company": "Palo Alto Networks",
        "repository": "PaloAltoNetworks/Unit42-timely-threat-intel",
        "role": "Threat-intelligence publication activity",
    },
    {
        "ticker": "CRWD",
        "company": "CrowdStrike",
        "repository": "CrowdStrike/falconpy",
        "role": "Falcon developer SDK activity",
    },
    {
        "ticker": "GOOGL",
        "company": "Alphabet / Google",
        "repository": "GoogleCloudPlatform/generative-ai",
        "role": "Google Cloud generative-AI developer ecosystem",
    },
    {
        "ticker": "CRWV",
        "company": "CoreWeave",
        "repository": "coreweave/terraform-provider-coreweave",
        "role": "Cloud provisioning / customer integration",
    },
    {
        "ticker": "CIEN",
        "company": "Ciena",
        "repository": "ciena/ciena.saos10",
        "role": "Network automation / product integration",
    },
    {
        "ticker": "ANET",
        "company": "Arista Networks",
        "repository": "aristanetworks/avd",
        "role": "Network automation / data-centre deployment",
    },
]

CONTEXT_NOTE = (
    "Discovery/context only; GitHub activity is not financial evidence or a trade signal. "
    "Material anomalies require verification against company disclosures, research, catalysts "
    "and price behaviour. Counts may include documentation, maintenance and security-response work. "
    "Capped activity means at least one 100-item API sample is incomplete."
)


def parse_time(value: str | None) -> datetime | None:
    if not value:
        return None
    parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def in_window(value: str | None, now: datetime, days: int) -> bool:
    parsed = parse_time(value)
    if parsed is None:
        return False
    return parsed >= now.astimezone(timezone.utc) - timedelta(days=days)


def is_bot(item: dict[str, Any]) -> bool:
    actor = item.get("author") or item.get("committer") or {}
    login = str(actor.get("login", "")).lower()
    actor_type = str(actor.get("type", "")).lower()
    return actor_type == "bot" or login.endswith("[bot]") or login.endswith("-bot")


def commit_time(item: dict[str, Any]) -> str | None:
    commit = item.get("commit") or {}
    for key in ("committer", "author"):
        value = (commit.get(key) or {}).get("date")
        if value:
            return str(value)
    return None


def activity_state(commits_7d: int, commits_28d: int, releases_7d: int) -> str:
    if commits_28d < 4 and releases_7d == 0:
        return "Sparse"
    prior_weekly = max(commits_28d - commits_7d, 0) / 3
    if (
        commits_7d >= 5
        and commits_7d >= max(prior_weekly * 1.5, prior_weekly + 2)
    ) or (releases_7d > 0 and commits_7d >= max(2, prior_weekly)):
        return "Accelerating"
    if prior_weekly >= 5 and commits_7d <= prior_weekly * 0.5 and releases_7d == 0:
        return "Slowing"
    return "Normal"


class GitHubClient:
    def __init__(self, token: str = "") -> None:
        import requests
        from requests.adapters import HTTPAdapter
        from urllib3.util.retry import Retry

        self.session = requests.Session()
        retry = Retry(
            total=3,
            connect=3,
            read=3,
            status=3,
            backoff_factor=1,
            status_forcelist=(408, 425, 429, 500, 502, 503, 504),
            allowed_methods=frozenset({"GET"}),
            respect_retry_after_header=True,
        )
        self.session.mount("https://", HTTPAdapter(max_retries=retry))
        headers = {
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
            "User-Agent": "FT-Game-GitHub-Signal-Monitor/1.0",
        }
        if token:
            headers["Authorization"] = f"Bearer {token}"
        self.session.headers.update(headers)
        self.min_remaining: int | None = None

    def get_json(
        self,
        path: str,
        *,
        params: dict[str, Any] | None = None,
        allow_empty: bool = False,
    ) -> Any:
        url = path if path.startswith("https://") else API_ROOT + path
        response = self.session.get(url, params=params, timeout=(10, 30))
        if allow_empty and response.status_code in (404, 409):
            return []
        response.raise_for_status()
        remaining = response.headers.get("X-RateLimit-Remaining")
        if remaining and remaining.isdigit():
            value = int(remaining)
            self.min_remaining = value if self.min_remaining is None else min(self.min_remaining, value)
        return response.json()


def collect_repository(
    client: Any,
    item: dict[str, str],
    now: datetime,
) -> dict[str, Any]:
    repo = item["repository"]
    meta = client.get_json(f"/repos/{repo}")
    if str(meta.get("full_name", "")).lower() != repo.lower():
        raise ValueError("repository identity mismatch")
    if meta.get("fork"):
        raise ValueError("watchlist repository unexpectedly became a fork")

    since = (now.astimezone(timezone.utc) - timedelta(days=WINDOW_DAYS)).isoformat()
    commits = client.get_json(
        f"/repos/{repo}/commits",
        params={"since": since, "per_page": MAX_PAGE},
        allow_empty=True,
    )
    issues = client.get_json(
        f"/repos/{repo}/issues",
        params={"state": "all", "since": since, "per_page": MAX_PAGE},
        allow_empty=True,
    )
    releases = client.get_json(
        f"/repos/{repo}/releases",
        params={"per_page": MAX_PAGE},
        allow_empty=True,
    )
    if not isinstance(commits, list) or not isinstance(issues, list) or not isinstance(releases, list):
        raise ValueError("unexpected GitHub list response")

    human_commits = [row for row in commits if not is_bot(row)]
    commits_7d = sum(in_window(commit_time(row), now, 7) for row in human_commits)
    commits_28d = sum(in_window(commit_time(row), now, 28) for row in human_commits)
    contributors_28d = {
        str((row.get("author") or {}).get("login", "")).strip()
        for row in human_commits
        if str((row.get("author") or {}).get("login", "")).strip()
        and in_window(commit_time(row), now, 28)
    }

    pure_issues = [row for row in issues if "pull_request" not in row]
    pull_requests = [row for row in issues if "pull_request" in row]

    def opened(rows: list[dict[str, Any]], days: int) -> int:
        return sum(in_window(str(row.get("created_at") or ""), now, days) for row in rows)

    def closed(rows: list[dict[str, Any]], days: int) -> int:
        return sum(in_window(str(row.get("closed_at") or ""), now, days) for row in rows)

    published = [row for row in releases if row.get("published_at")]
    releases_7d = sum(in_window(str(row.get("published_at")), now, 7) for row in published)
    releases_28d = sum(in_window(str(row.get("published_at")), now, 28) for row in published)
    latest_release = max(
        published,
        key=lambda row: parse_time(str(row.get("published_at"))) or datetime.min.replace(tzinfo=timezone.utc),
        default={},
    )

    c7 = commits_7d
    c28 = commits_28d
    previous_weekly = max(c28 - c7, 0) / 3
    ratio = round(c7 / previous_weekly, 2) if previous_weekly >= 1 else None

    commit_capped = len(commits) >= MAX_PAGE
    any_capped = commit_capped or len(issues) >= MAX_PAGE or len(releases) >= MAX_PAGE

    return {
        "captured_at": now.astimezone(TORONTO).isoformat(),
        "ticker": item["ticker"],
        "company": item["company"],
        "repository": repo,
        "role": item["role"],
        "stars": int(meta.get("stargazers_count") or 0),
        "forks": int(meta.get("forks_count") or 0),
        "open_issues": int(meta.get("open_issues_count") or 0),
        "commits_7d": c7,
        "commits_28d": c28,
        "commit_pace_ratio": ratio,
        "contributors_28d": len(contributors_28d),
        "issues_opened_7d": opened(pure_issues, 7),
        "issues_closed_7d": closed(pure_issues, 7),
        "prs_opened_7d": opened(pull_requests, 7),
        "prs_closed_7d": closed(pull_requests, 7),
        "releases_7d": releases_7d,
        "releases_28d": releases_28d,
        "latest_release": str(latest_release.get("name") or latest_release.get("tag_name") or ""),
        "latest_release_at": str(latest_release.get("published_at") or ""),
        "pushed_at": str(meta.get("pushed_at") or ""),
        "discussions_available": bool(meta.get("has_discussions")),
        "archived": bool(meta.get("archived")),
        "activity_capped": any_capped,
        "activity_state": "Capped" if commit_capped else activity_state(c7, c28, releases_7d),
        "verification": "Curated official/company-controlled public repository",
        "context": CONTEXT_NOTE,
    }


def collect_all(client: Any, now: datetime) -> dict[str, Any]:
    snapshots: list[dict[str, Any]] = []
    failures: list[str] = []
    for item in WATCHLIST:
        try:
            snapshots.append(collect_repository(client, item, now))
        except Exception as exc:  # noqa: BLE001
            failures.append(f"{item['repository']}: {type(exc).__name__}")
    status = "Complete" if not failures else "Partial" if snapshots else "Failed"
    return {
        "status": status,
        "captured_at": now.astimezone(TORONTO).isoformat(),
        "repositories_checked": len(WATCHLIST),
        "snapshots": snapshots,
        "failures": failures,
        "api_remaining_min": getattr(client, "min_remaining", None),
        "discussions": (
            "Availability flag only in Stage 1. Discussion counts require optional authenticated GraphQL access."
        ),
        "semantics": "Developer/product activity context only; never a standalone stock direction or trade signal.",
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Collect and print public GitHub snapshots without changing any workbook.",
    )
    args = parser.parse_args()
    if not args.dry_run:
        raise RuntimeError("Stage 1 is read-only. Use --dry-run; workbook writing is not enabled yet.")

    now = datetime.now(TORONTO)
    if now > GAME_END:
        print(json.dumps({"status": "Stopped", "reason": "FT game ended"}))
        return 0

    # Optional token only. Without it, the eight-repository watchlist uses at most
    # 32 requests per run, below GitHub's public unauthenticated primary limit.
    client = GitHubClient(os.environ.get("GH_SIGNAL_TOKEN", ""))
    result = collect_all(client, now)
    print(json.dumps(result, ensure_ascii=False))
    return 1 if result["status"] == "Failed" else 0


if __name__ == "__main__":
    raise SystemExit(main())
