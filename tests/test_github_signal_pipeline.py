import unittest
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from ft_github_signal_pipeline import (
    REQUESTS_PER_REPO,
    WATCHLIST,
    activity_state,
    collect_all,
    collect_repository,
)


NOW = datetime(2026, 10, 6, 12, 0, tzinfo=ZoneInfo("America/Toronto"))


def iso(days_ago=0):
    return (NOW.astimezone(timezone.utc) - timedelta(days=days_ago)).isoformat().replace("+00:00", "Z")


class FakeClient:
    min_remaining = 50

    def __init__(self, repo="Example/repo", fail=False):
        self.repo = repo
        self.fail = fail

    def get_json(self, path, **kwargs):
        if self.fail:
            raise RuntimeError("blocked")
        if path == f"/repos/{self.repo}":
            return {
                "full_name": self.repo,
                "fork": False,
                "archived": False,
                "stargazers_count": 100,
                "forks_count": 20,
                "open_issues_count": 12,
                "pushed_at": iso(0),
                "has_discussions": True,
            }
        if path.endswith("/commits"):
            rows = []
            for n in range(7):
                rows.append({
                    "commit": {"committer": {"date": iso(1)}, "author": {"date": iso(1)}},
                    "author": {"login": f"dev{n}", "type": "User"},
                })
            for n in range(6):
                rows.append({
                    "commit": {"committer": {"date": iso(14)}, "author": {"date": iso(14)}},
                    "author": {"login": f"old{n}", "type": "User"},
                })
            rows.append({
                "commit": {"committer": {"date": iso(1)}},
                "author": {"login": "dependabot[bot]", "type": "Bot"},
            })
            return rows
        if path.endswith("/issues"):
            return [
                {"created_at": iso(2), "closed_at": iso(1)},
                {"created_at": iso(3), "closed_at": None},
                {"created_at": iso(2), "closed_at": iso(1), "pull_request": {"url": "x"}},
            ]
        if path.endswith("/releases"):
            return [
                {"name": "v2", "tag_name": "v2", "published_at": iso(2)},
                {"name": "v1", "tag_name": "v1", "published_at": iso(20)},
            ]
        raise AssertionError(path)


class Tests(unittest.TestCase):
    def test_watchlist_is_small_unique_and_within_public_request_budget(self):
        repos = [item["repository"] for item in WATCHLIST]
        self.assertEqual(len(repos), len(set(repos)))
        self.assertLessEqual(len(repos) * REQUESTS_PER_REPO, 50)
        self.assertEqual(len(WATCHLIST), 8)

    def test_snapshot_counts_human_activity_and_labels_context(self):
        item = {
            "ticker": "TEST",
            "company": "Example",
            "repository": "Example/repo",
            "role": "Test role",
        }
        row = collect_repository(FakeClient(), item, NOW)
        self.assertEqual(row["commits_7d"], 7)
        self.assertEqual(row["commits_28d"], 13)
        self.assertEqual(row["contributors_28d"], 13)
        self.assertEqual(row["issues_opened_7d"], 2)
        self.assertEqual(row["issues_closed_7d"], 1)
        self.assertEqual(row["prs_opened_7d"], 1)
        self.assertEqual(row["prs_closed_7d"], 1)
        self.assertEqual(row["releases_7d"], 1)
        self.assertEqual(row["releases_28d"], 2)
        self.assertEqual(row["activity_state"], "Accelerating")
        self.assertTrue(row["discussions_available"])
        self.assertIn("not financial evidence", row["context"])

    def test_activity_state_does_not_overcall_sparse_or_flat_activity(self):
        self.assertEqual(activity_state(0, 0, 0), "Sparse")
        self.assertEqual(activity_state(3, 12, 0), "Normal")
        self.assertEqual(activity_state(2, 23, 0), "Slowing")

    def test_commit_cap_prevents_false_acceleration_label(self):
        item = {
            "ticker": "TEST",
            "company": "Example",
            "repository": "Example/repo",
            "role": "Test role",
        }

        class CappedClient(FakeClient):
            def get_json(self, path, **kwargs):
                if path.endswith("/commits"):
                    return [
                        {
                            "commit": {"committer": {"date": iso(1)}},
                            "author": {"login": f"dev{n}", "type": "User"},
                        }
                        for n in range(100)
                    ]
                return super().get_json(path, **kwargs)

        row = collect_repository(CappedClient(), item, NOW)
        self.assertTrue(row["activity_capped"])
        self.assertEqual(row["activity_state"], "Capped")

    def test_partial_run_preserves_successful_repositories(self):
        import ft_github_signal_pipeline as pipeline

        original = pipeline.WATCHLIST
        try:
            pipeline.WATCHLIST = [
                {"ticker": "A", "company": "A", "repository": "Example/repo", "role": "x"},
                {"ticker": "B", "company": "B", "repository": "Broken/repo", "role": "y"},
            ]

            class MixedClient(FakeClient):
                def get_json(self, path, **kwargs):
                    if path.startswith("/repos/Broken/"):
                        raise RuntimeError("blocked")
                    return super().get_json(path, **kwargs)

            result = collect_all(MixedClient(), NOW)
            self.assertEqual(result["status"], "Partial")
            self.assertEqual(len(result["snapshots"]), 1)
            self.assertEqual(len(result["failures"]), 1)
        finally:
            pipeline.WATCHLIST = original


if __name__ == "__main__":
    unittest.main()
