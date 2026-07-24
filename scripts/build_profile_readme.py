#!/usr/bin/env python3
"""Refresh the dynamic sections of the GitHub profile README.

Only public GitHub API fields are rendered. Contribution descriptions are the
original PR/issue titles. Projects are listed by role and latest activity date.
"""

from __future__ import annotations

import argparse
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

# A small allowlist keeps the profile focused on public upstream/community work.
CONTRIBUTION_REPOSITORIES = (
    "docling-project/docling",
    "edwardkim/rhwp",
    "langchain-ai/deepagents",
)

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


def request_json(url: str, *, allow_not_found: bool = False) -> Any:
    request = Request(url, headers=github_headers())
    try:
        with urlopen(request, timeout=30) as response:
            return json.load(response)
    except HTTPError as error:
        if allow_not_found and error.code == 404:
            return None
        detail = error.read().decode("utf-8", errors="replace")
        raise RuntimeError(
            f"GitHub API request failed ({error.code}) for {url}: {detail}"
        ) from error
    except URLError as error:
        raise RuntimeError(f"Could not reach GitHub API for {url}: {error}") from error


def date_part(timestamp: str | None) -> str:
    return timestamp[:10] if timestamp else "date unavailable"


def repository_activity() -> dict[str, list[dict[str, Any]]]:
    activity: dict[str, list[dict[str, Any]]] = {}

    for repository in CONTRIBUTION_REPOSITORIES:
        params = urlencode(
            {
                "q": f"repo:{repository} author:{GITHUB_USER}",
                "sort": "updated",
                "order": "desc",
                "per_page": 100,
            }
        )
        result = request_json(f"{GITHUB_API}/search/issues?{params}")
        activity[repository] = result.get("items", [])

    return activity


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
    params = urlencode(
        {
            "type": "owner",
            "sort": "pushed",
            "direction": "desc",
            "per_page": 100,
        }
    )
    repositories = request_json(
        f"{GITHUB_API}/users/{GITHUB_USER}/repos?{params}"
    )
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
        and repository["name"] != GITHUB_USER
    ]


def collaboration_project_items() -> list[dict[str, str]]:
    params = urlencode(
        {
            "q": f"author:{GITHUB_USER} is:pr is:merged",
            "sort": "updated",
            "order": "desc",
            "per_page": 100,
        }
    )
    result = request_json(f"{GITHUB_API}/search/issues?{params}")
    latest_by_repository: dict[str, dict[str, str]] = {}

    for item in result.get("items", []):
        repository = item["repository_url"].split("/repos/", 1)[-1]
        if repository.startswith(f"{GITHUB_USER}/"):
            continue

        merged_at = item.get("pull_request", {}).get("merged_at")
        if not merged_at:
            continue

        existing = latest_by_repository.get(repository)
        if existing and existing["activity_at"] >= merged_at:
            continue

        latest_by_repository[repository] = {
            "activity_at": merged_at,
            "repository": repository,
            "role": "Contributor",
            "url": f"https://github.com/{repository}",
        }

    return list(latest_by_repository.values())


def project_items(limit: int) -> list[dict[str, str]]:
    projects = maintained_project_items() + collaboration_project_items()
    projects.sort(key=lambda item: item["activity_at"], reverse=True)
    return projects[:limit]


def render_contributions(items: list[dict[str, str]], limit: int) -> str:
    if not items:
        return "_No matching public contributions found._"

    blocks = []
    for item in items[:limit]:
        repository_name = html.escape(item["repository"].rsplit("/", 1)[-1])
        title = html.escape(item["title"])
        url = html.escape(item["url"], quote=True)
        blocks.append(
            f'{item["emoji"]} [{repository_name} #{item["number"]}]({url})  \n'
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
        emoji = "🤝" if item["role"] == "Contributor" else "🛠️"
        blocks.append(
            f'<div>{emoji} <a href="{url}" title="{repository_full_name}">'
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
    document = replace_section(
        document,
        CONTRIBUTION_START,
        CONTRIBUTION_END,
        render_contributions(contribution_items(activity), contribution_limit),
    )
    return replace_section(
        document,
        PROJECT_START,
        PROJECT_END,
        render_projects(project_items(project_limit)),
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
