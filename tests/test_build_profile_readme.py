"""Behavioral coverage for public contribution discovery and README rendering."""

from datetime import datetime, timezone
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from urllib.parse import parse_qs, urlparse

from scripts import build_profile_readme as profile


NOW = datetime(2026, 10, 12, 12, tzinfo=timezone.utc)


def issue(repository="new-community/new-project", number=1, **fields):
    return {
        "number": number,
        "repository_url": f"https://api.github.com/repos/{repository}",
        "html_url": f"https://github.com/{repository}/issues/{number}",
        "title": "A public contribution",
        "state": "open",
        "created_at": "2026-10-10T00:00:00Z",
        "updated_at": "2026-10-10T00:00:00Z",
        **fields,
    }


def commit_group(repository="new-community/new-project", **metadata):
    return {
        "repository": {
            "nameWithOwner": repository,
            "url": f"https://github.com/{repository}",
            "isPrivate": False,
            **metadata,
        },
        "contributions": {"nodes": [{
            "occurredAt": "2026-10-11T07:00:00Z",
            "commitCount": 1,
            "url": "https://github.com/myeolinmalchi?tab=overview&from=2026-10-01",
        }]},
    }


def commit_response(*groups):
    return {"data": {"user": {"contributionsCollection": {
        "commitContributionsByRepository": list(groups),
    }}}}


class PublicContributionTests(unittest.TestCase):
    def test_discovers_unlisted_repositories_across_search_pages(self):
        first = [issue("community/first", number=index) for index in range(100)]
        with patch.object(profile, "request_json", side_effect=[
            {"total_count": 101, "items": first},
            {"total_count": 101, "items": [issue("new-community/new-project")]},
        ]) as request:
            activity = profile.repository_activity()
        self.assertIn("new-community/new-project", activity)
        queries = [parse_qs(urlparse(call.args[0]).query) for call in request.call_args_list]
        self.assertEqual(queries[0]["q"], ["author:myeolinmalchi is:public"])
        self.assertEqual(queries[1]["page"], ["2"])

    def test_search_splits_over_1000_results_and_deduplicates_boundary(self):
        item = issue()
        with patch.object(profile, "request_json", side_effect=[
            {"total_count": 1001, "items": [item]},
            {"total_count": 1, "items": [item]},
            {"total_count": 1, "items": [item]},
        ]) as request:
            items = profile.search_activity("author:myeolinmalchi is:public")
        self.assertEqual(items, [item])
        self.assertIn("updated:", parse_qs(urlparse(request.call_args_list[1].args[0]).query)["q"][0])

    def test_incomplete_search_does_not_publish_partial_activity(self):
        with patch.object(profile, "request_json", return_value={"incomplete_results": True}):
            with self.assertRaisesRegex(RuntimeError, "incomplete"):
                profile.repository_activity()

    def test_credited_commit_counts_without_authored_pr_and_excludes_private(self):
        with patch.object(profile, "request_json", return_value=commit_response(
            commit_group(),
            commit_group("private-org/secret", isPrivate=True),
            commit_group("unknown/visibility", isPrivate=None),
            commit_group("myeolinmalchi/myeolinmalchi"),
        )):
            commits = profile.commit_contribution_items(now=NOW)
        self.assertEqual(len(commits), 1)
        self.assertEqual(commits[0]["status"], "Commit contribution")
        self.assertEqual(profile.collaboration_project_items({}, commits), [{
            "repository": "new-community/new-project",
            "url": "https://github.com/new-community/new-project",
            "role": "Contributor",
            "activity_at": "2026-10-11T07:00:00Z",
        }])
        rendered = profile.render_contributions(commits, 4)
        self.assertIn("1 commit contributed", rendered)
        self.assertNotIn("Merged", rendered)
        self.assertNotIn("secret", rendered)

    def test_commit_discovery_splits_crowded_windows(self):
        with patch.object(profile, "request_json", side_effect=[
            commit_response(*(commit_group(f"org/repo-{index}") for index in range(100))),
            commit_response(commit_group("later/new-project")),
            commit_response(commit_group("earlier/another-project")),
        ]):
            commits = profile.commit_contribution_items(now=NOW)
        self.assertEqual({item["repository"] for item in commits}, {
            "later/new-project", "earlier/another-project",
        })

    def test_graphql_errors_are_not_treated_as_empty_contributions(self):
        with patch.object(profile, "request_json", return_value={"errors": [{"message": "Denied"}]}):
            with self.assertRaisesRegex(RuntimeError, "could not read"):
                profile.commit_contribution_items(now=NOW)

    def test_open_pr_is_not_labeled_merged_and_closed_unmerged_pr_is_omitted(self):
        items = profile.contribution_items({"org/project": [
            issue(number=1, pull_request={"merged_at": None}),
            issue(number=2, state="closed", pull_request={"merged_at": None}),
            issue(number=3, state="closed", pull_request={"merged_at": "2026-10-11T00:00:00Z"}),
        ]})
        self.assertEqual({item["number"]: item["status"] for item in items}, {
            "1": "Open PR", "3": "Merged",
        })

    def test_projects_combine_merged_pr_and_commit_credit_without_duplicates(self):
        commits = [{"repository": "org/project", "event_at": "2026-10-11T00:00:00Z"}]
        projects = profile.collaboration_project_items({"org/project": [
            issue(pull_request={"merged_at": "2026-10-01T00:00:00Z"}),
        ]}, commits)
        self.assertEqual(len(projects), 1)
        self.assertEqual(projects[0]["activity_at"], "2026-10-11T00:00:00Z")

    def test_readme_renders_new_sources_and_preserves_surrounding_content(self):
        document = "\n".join([
            "Personal introduction",
            profile.CONTRIBUTION_START, "old activity", profile.CONTRIBUTION_END,
            "Existing table layout",
            profile.PROJECT_START, "old projects", profile.PROJECT_END,
            "Awards and community work",
        ])
        with tempfile.TemporaryDirectory() as temporary:
            readme = Path(temporary) / "README.md"
            readme.write_text(document)
            with patch.object(profile, "repository_activity", return_value={
                "new-community/new-project": [issue()],
            }), patch.object(profile, "maintained_project_items", return_value=[]), patch.object(
                profile, "request_json", side_effect=[
                    commit_response(commit_group("another-community/coauthored-project")),
                    {"private": False, "visibility": "public"},
                ]
            ):
                generated = profile.build_readme(readme, 4, 10)
            self.assertEqual(readme.read_text(), document)
        for unchanged in ("Personal introduction", "Existing table layout", "Awards and community work"):
            self.assertIn(unchanged, generated)
        self.assertIn("new-project #1", generated)
        self.assertIn("coauthored-project", generated)
        self.assertNotIn("old activity", generated)


if __name__ == "__main__":
    unittest.main()
