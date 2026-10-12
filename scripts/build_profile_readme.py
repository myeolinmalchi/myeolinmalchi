#!/usr/bin/env python3
"""Refresh the dynamic sections of the GitHub profile README.

Discover public PRs and issues across GitHub, plus commit contributions credited
on the user's profile (including co-authored commits). No repository allowlist
is required. Projects are listed by role and latest activity date.
"""

from __future__ import annotations

import argparse
from datetime import datetime, timedelta, timezone
import html
import json
import os
from pathlib import Path
import sys
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen


GITHUB_API = "https://api.github.com"
GITHUB_USER = "myeolinmalchi"

CONTRIBUTION_START = "<!-- oss_contributions starts -->"
CONTRIBUTION_END = "<!-- oss_contributions ends -->"
PROJECT_START = "<!-- projects_and_collaborations starts -->"
PROJECT_END = "<!-- projects_and_collaborations ends -->"


def github_headers() -> dict[str, str]:
    headers = {
        "Accept": "application/vnd.github+json",
        "User-Agent": f"{GITHUB_USER}-profile-readme-builder",
        "X-GitHub-Api-Version": "2022-11-28",
    }
    token = os.environ.get("GITHUB_TOKEN") or os.environ.get("GH_TOKEN")
    if token:
        headers["Authorization"] = f"Bearer {token}"
    return headers


def request_json(url: str, *, payload: dict[str, Any] | None = None) -> Any:
    headers = github_headers()
    data = None
    if payload is not None:
        headers["Content-Type"] = "application/json"
        data = json.dumps(payload).encode("utf-8")
    request = Request(url, data=data, headers=headers)
    try:
        with urlopen(request, timeout=30) as response:
            return json.load(response)
    except HTTPError as error:
        detail = error.read().decode("utf-8", errors="replace")
        raise RuntimeError(
            f"GitHub API request failed ({error.code}) for {url}: {detail}"
        ) from error
    except URLError as error:
        raise RuntimeError(f"Could not reach GitHub API for {url}: {error}") from error


def date_part(timestamp: str | None) -> str:
    return timestamp[:10] if timestamp else "date unavailable"


def repository_activity() -> dict[str, list[dict[str, Any]]]:
    """Search all public repositories, including results beyond the first page."""
    activity: dict[str, list[dict[str, Any]]] = {}
    for item in search_activity(f"author:{GITHUB_USER} is:public"):
        repository = item["repository_url"].split("/repos/", 1)[-1]
        activity.setdefault(repository, []).append(item)
    return activity


def search_activity(
    query: str,
    *,
    start: datetime | None = None,
    finish: datetime | None = None,
) -> list[dict[str, Any]]:
    """Fetch search pages; fail instead of publishing incomplete search results."""
    items: list[dict[str, Any]] = []
    page = 1
    while True:
        params = urlencode(
            {
                "q": query,
                "sort": "updated",
                "order": "desc",
                "per_page": 100,
                "page": page,
            }
        )
        result = request_json(f"{GITHUB_API}/search/issues?{params}")
        if result.get("incomplete_results"):
            raise RuntimeError("GitHub returned incomplete activity search results")
        if result["total_count"] > 1000:
            # Search exposes only 1,000 results per query. Partition by update
            # time so busy accounts do not lose repositories beyond that cap.
            start = start or datetime(1970, 1, 1, tzinfo=timezone.utc)
            finish = finish or datetime.now(timezone.utc).replace(microsecond=0)
            if finish - start <= timedelta(seconds=1):
                raise RuntimeError("Over 1,000 activities share the same update time")
            middle = (start + (finish - start) / 2).replace(microsecond=0)
            found: dict[str, dict[str, Any]] = {}
            for lower, upper in ((start, middle), (middle, finish)):
                base_query = query.split(" updated:", 1)[0]
                interval = f"{lower.isoformat()}..{upper.isoformat()}"
                for item in search_activity(
                    f"{base_query} updated:{interval}", start=lower, finish=upper
                ):
                    found[item["html_url"]] = item
            return sorted(found.values(), key=lambda item: item["updated_at"], reverse=True)
        batch = result["items"]
        items.extend(batch)
        if len(items) >= result["total_count"]:
            return items
        if not batch:
            raise RuntimeError("GitHub activity search ended before all results arrived")
        page += 1


def commit_contribution_items(
    *, now: datetime | None = None
) -> list[dict[str, str]]:
    """Read the latest credited commit day per public repository in the past year.

    Profile contribution records include co-authors, unlike commit-author and
    PR-author searches. They establish commit credit, not ownership of a PR.
    Split crowded time windows so GitHub's repository cap cannot silently omit
    a repository. Only public records may reach the README.
    """
    end = now or datetime.now(timezone.utc)
    windows = [(end - timedelta(days=365), end)]
    latest: dict[str, dict[str, str]] = {}
    query = """
        query($login: String!, $from: DateTime!, $to: DateTime!) {
          user(login: $login) {
            contributionsCollection(from: $from, to: $to) {
              commitContributionsByRepository(maxRepositories: 100) {
                repository { nameWithOwner isPrivate url }
                contributions(first: 1, orderBy: {field: OCCURRED_AT, direction: DESC}) {
                  nodes { occurredAt commitCount url }
                }
              }
            }
          }
        }
    """
    while windows:
        start, finish = windows.pop()
        result = request_json(
            f"{GITHUB_API}/graphql",
            payload={
                "query": query,
                "variables": {
                    "login": GITHUB_USER,
                    "from": start.isoformat(),
                    "to": finish.isoformat(),
                },
            },
        )
        if result.get("errors"):
            raise RuntimeError("GitHub could not read profile commit contributions")
        user = result.get("data", {}).get("user")
        if not user:
            raise RuntimeError("GitHub profile contribution data is unavailable")
        groups = user["contributionsCollection"]["commitContributionsByRepository"]
        if len(groups) >= 100:
            if finish - start <= timedelta(days=1):
                raise RuntimeError("Too many contribution repositories in a single day")
            middle = start + (finish - start) / 2
            windows.extend([(start, middle), (middle, finish)])
            continue
        for group in groups:
            metadata = group["repository"]
            # Fail closed if visibility is missing, even with a privileged token.
            if metadata.get("isPrivate") is not False:
                continue
            repository = metadata["nameWithOwner"]
            if repository == f"{GITHUB_USER}/{GITHUB_USER}":
                continue  # Do not make README maintenance its own featured activity.
            for contribution in group["contributions"]["nodes"]:
                event_at = contribution["occurredAt"]
                if repository in latest and latest[repository]["event_at"] >= event_at:
                    continue
                count = contribution["commitCount"]
                if count < 1:
                    continue
                latest[repository] = {
                    "emoji": "🔨",
                    "event_at": event_at,
                    "number": "",
                    "repository": repository,
                    "status": "Commit contribution",
                    "title": f"{count} commit{'s' if count != 1 else ''} contributed",
                    "updated_at": event_at,
                    "url": contribution["url"],
                }
    return list(latest.values())


def contribution_items(
    activity: dict[str, list[dict[str, Any]]],
) -> list[dict[str, str]]:
    candidates: list[dict[str, str]] = []

    for repository, items in activity.items():
        for item in items:
            pull_request = item.get("pull_request")
            if pull_request:
                if pull_request.get("merged_at"):
                    status = "Merged"
                    emoji = "🟣"
                    event_at = pull_request["merged_at"]
                elif item.get("state") == "open":
                    status = "Open PR"
                    emoji = "🟢"
                    event_at = item.get("created_at")
                else:
                    # Closed, unmerged PRs are not highlighted.
                    continue
            else:
                if item.get("state") != "open":
                    # Keep the profile current and avoid duplicating resolved issues.
                    continue
                status = "Open issue"
                emoji = "💬"
                event_at = item.get("created_at")

            candidates.append(
                {
                    "emoji": emoji,
                    "event_at": event_at or "",
                    "number": str(item["number"]),
                    "repository": repository,
                    "status": status,
                    "title": item["title"],
                    "updated_at": item.get("updated_at") or "",
                    "url": item["html_url"],
                }
            )

    unique = {item["url"]: item for item in candidates}
    return sorted(
        unique.values(),
        key=lambda item: item["updated_at"],
        reverse=True,
    )


def maintained_project_items() -> list[dict[str, str]]:
    repositories: list[dict[str, Any]] = []
    page = 1
    while True:
        params = urlencode(
            {
                "type": "owner",
                "sort": "pushed",
                "direction": "desc",
                "per_page": 100,
                "page": page,
            }
        )
        batch = request_json(f"{GITHUB_API}/users/{GITHUB_USER}/repos?{params}")
        repositories.extend(batch)
        if len(batch) < 100:
            break
        page += 1
    return [
        {
            "activity_at": repository.get("pushed_at") or "",
            "repository": repository["full_name"],
            "role": "Maintainer",
            "url": repository["html_url"],
        }
        for repository in repositories
        if not repository.get("archived")
        and not repository.get("fork")
        and not repository.get("private")
        and repository["name"] != GITHUB_USER
    ]


def collaboration_project_items(
    activity: dict[str, list[dict[str, Any]]],
    commits: list[dict[str, str]],
) -> list[dict[str, str]]:
    latest_by_repository: dict[str, dict[str, str]] = {}
    contributions = [
        (repository, item.get("pull_request", {}).get("merged_at"))
        for repository, items in activity.items()
        for item in items
    ]
    contributions.extend((item["repository"], item["event_at"]) for item in commits)
    for repository, activity_at in contributions:
        if repository.startswith(f"{GITHUB_USER}/"):
            continue
        if not activity_at:
            continue

        existing = latest_by_repository.get(repository)
        if existing and existing["activity_at"] >= activity_at:
            continue

        latest_by_repository[repository] = {
            "activity_at": activity_at,
            "repository": repository,
            "role": "Contributor",
            "url": f"https://github.com/{repository}",
        }

    return list(latest_by_repository.values())


def project_items(
    limit: int,
    activity: dict[str, list[dict[str, Any]]],
    commits: list[dict[str, str]],
) -> list[dict[str, str]]:
    projects = maintained_project_items() + collaboration_project_items(activity, commits)
    projects.sort(key=lambda item: item["activity_at"], reverse=True)
    selected: list[dict[str, str]] = []

    for project in projects:
        if project["role"] == "Contributor":
            metadata = request_json(
                f"{GITHUB_API}/repos/{project['repository']}"
            )
            if metadata.get("private") or metadata.get("visibility") != "public":
                continue

        selected.append(project)
        if len(selected) == limit:
            break

    return selected


def render_contributions(items: list[dict[str, str]], limit: int) -> str:
    if not items:
        return "_No matching public contributions found._"

    blocks = []
    for item in items[:limit]:
        repository_name = html.escape(item["repository"].rsplit("/", 1)[-1])
        label = repository_name
        if item["number"]:
            label += f' #{item["number"]}'
        title = html.escape(item["title"])
        url = html.escape(item["url"], quote=True)
        blocks.append(
            f'{item["emoji"]} [{label}]({url})  \n'
            f"{title}  \n"
            f'<sub>{item["status"]} · {date_part(item["event_at"])}</sub>'
        )
    return "\n\n".join(blocks)


def render_projects(items: list[dict[str, str]]) -> str:
    if not items:
        return "_No matching public projects found._"

    blocks = []
    for item in items:
        repository_name = html.escape(item["repository"].rsplit("/", 1)[-1])
        repository_full_name = html.escape(item["repository"], quote=True)
        url = html.escape(item["url"], quote=True)
        blocks.append(
            f'<div><a href="{url}" title="{repository_full_name}">'
            f"{repository_name}</a> "
            f'<sub>{item["role"]} · {date_part(item["activity_at"])}</sub></div>'
        )
    return "\n".join(blocks)


def replace_section(document: str, start: str, end: str, body: str) -> str:
    start_index = document.find(start)
    end_index = document.find(end, start_index + len(start))
    if start_index == -1 or end_index == -1:
        raise ValueError(f"README markers are missing or malformed: {start} / {end}")
    if document.find(start, start_index + len(start)) != -1:
        raise ValueError(f"README contains more than one start marker: {start}")
    if document.find(end, end_index + len(end)) != -1:
        raise ValueError(f"README contains more than one end marker: {end}")

    body_start = start_index + len(start)
    return document[:body_start] + "\n" + body.strip() + "\n" + document[end_index:]


def build_readme(readme_path: Path, contribution_limit: int, project_limit: int) -> str:
    document = readme_path.read_text(encoding="utf-8")
    activity = repository_activity()
    commits = commit_contribution_items()
    contributions = contribution_items(activity) + commits
    contributions.sort(key=lambda item: item["updated_at"], reverse=True)
    document = replace_section(
        document,
        CONTRIBUTION_START,
        CONTRIBUTION_END,
        render_contributions(contributions, contribution_limit),
    )
    return replace_section(
        document,
        PROJECT_START,
        PROJECT_END,
        render_projects(project_items(project_limit, activity, commits)),
    )


def parse_args() -> argparse.Namespace:
    project_root = Path(__file__).resolve().parents[1]
    parser = argparse.ArgumentParser(
        description="Update the public GitHub activity sections in a profile README."
    )
    parser.add_argument(
        "--readme",
        type=Path,
        default=project_root / "README.md",
        help="Markdown file containing the dynamic-section markers.",
    )
    parser.add_argument("--contribution-limit", type=int, default=4)
    parser.add_argument(
        "--project-limit",
        type=int,
        default=10,
        help="Maximum recent repositories to show (default: 10).",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Print the generated Markdown without writing the file.",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    try:
        generated = build_readme(
            args.readme,
            contribution_limit=max(1, args.contribution_limit),
            project_limit=max(1, args.project_limit),
        )
    except (OSError, RuntimeError, ValueError) as error:
        print(f"error: {error}", file=sys.stderr)
        return 1

    if args.dry_run:
        print(generated, end="")
    else:
        args.readme.write_text(generated, encoding="utf-8")
        print(f"Updated {args.readme}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
