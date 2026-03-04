"""Workspace activity scanner and captain's log scaffolder for ORGAN-V.

Scans git repos across ~/Workspace (local mode) or GitHub Events API
(github-api mode) and produces a JSON activity snapshot plus a captain's
log entry pre-filled with real commit data.

CLI (local):
    python -m src.log_generator --workspace ~/Workspace \
           --logs-dir ../public-process/_logs/ \
           --data-dir ../public-process/data/

CLI (github-api):
    python -m src.log_generator --mode github-api \
           --logs-dir ../public-process/_logs/ \
           --data-dir ../public-process/data/
"""

import argparse
import json
import os
import re
import subprocess
import sys
import urllib.error
import urllib.request
from datetime import date, datetime, timedelta, timezone
from pathlib import Path


# Organ directory name → (numeral, full name)
ORGAN_MAP: dict[str, tuple[str, str]] = {
    "organvm-i-theoria": ("I", "Theoria"),
    "organvm-ii-poiesis": ("II", "Poiesis"),
    "organvm-iii-ergon": ("III", "Ergon"),
    "organvm-iv-taxis": ("IV", "Taxis"),
    "organvm-v-logos": ("V", "Logos"),
    "organvm-vi-koinonia": ("VI", "Koinonia"),
    "organvm-vii-kerygma": ("VII", "Kerygma"),
    "meta-organvm": ("META", "Meta"),
    "4444J99": ("Personal", "Personal"),
    "4444j99": ("Personal", "Personal"),
}

# Conventional commit prefix → suggested tag
PREFIX_TAG_MAP: dict[str, str] = {
    "feat": "feature",
    "fix": "fix",
    "docs": "documentation",
    "chore": "infrastructure",
    "refactor": "refactoring",
    "test": "testing",
}


def detect_organ(dir_name: str) -> tuple[str, str] | None:
    """Map a workspace directory name to its organ numeral and name."""
    return ORGAN_MAP.get(dir_name)


def find_git_repos(workspace: Path, max_depth: int = 2) -> list[Path]:
    """Walk workspace up to max_depth for directories containing .git."""
    repos = []
    for depth1 in sorted(workspace.iterdir()):
        if not depth1.is_dir() or depth1.name.startswith("."):
            continue
        if (depth1 / ".git").is_dir():
            repos.append(depth1)
        if max_depth >= 2:
            for depth2 in sorted(depth1.iterdir()):
                if not depth2.is_dir() or depth2.name.startswith("."):
                    continue
                if (depth2 / ".git").is_dir():
                    repos.append(depth2)
    return repos


def _anchor_date(d: str) -> str:
    """Append T00:00:00 to bare YYYY-MM-DD dates for consistent git behavior.

    Git interprets bare dates relative to the current time of day, which
    can exclude commits made earlier today. Anchoring to midnight avoids this.
    """
    if re.match(r"^\d{4}-\d{2}-\d{2}$", d):
        return f"{d}T00:00:00"
    return d


def git_log(repo: Path, since: str, until: str) -> list[dict]:
    """Run git log and return parsed commits."""
    try:
        result = subprocess.run(
            [
                "git", "log",
                f"--since={_anchor_date(since)}",
                f"--until={_anchor_date(until)}",
                "--format=%H|%ai|%s",
                "--no-merges",
            ],
            cwd=repo,
            capture_output=True,
            text=True,
            timeout=10,
        )
    except (subprocess.TimeoutExpired, FileNotFoundError):
        return []

    if result.returncode != 0:
        return []

    commits = []
    for line in result.stdout.strip().splitlines():
        if not line:
            continue
        parts = line.split("|", 2)
        if len(parts) != 3:
            continue
        commits.append({
            "hash": parts[0][:7],
            "date": parts[1].split(" ")[0],
            "message": parts[2],
        })
    return commits


def git_files_changed(repo: Path, since: str, until: str) -> int:
    """Count files changed in the date range using git log --stat."""
    try:
        result = subprocess.run(
            [
                "git", "log",
                f"--since={_anchor_date(since)}",
                f"--until={_anchor_date(until)}",
                "--no-merges",
                "--diff-filter=ACDMRT",
                "--name-only",
                "--format=",
            ],
            cwd=repo,
            capture_output=True,
            text=True,
            timeout=10,
        )
    except (subprocess.TimeoutExpired, FileNotFoundError):
        return 0

    if result.returncode != 0:
        return 0

    files = {line for line in result.stdout.strip().splitlines() if line}
    return len(files)


def git_remote_url(repo: Path) -> str | None:
    """Get the origin remote URL and convert to GitHub HTTPS URL."""
    try:
        result = subprocess.run(
            ["git", "remote", "get-url", "origin"],
            cwd=repo,
            capture_output=True,
            text=True,
            timeout=5,
        )
    except (subprocess.TimeoutExpired, FileNotFoundError):
        return None

    if result.returncode != 0:
        return None

    url = result.stdout.strip()
    return normalize_github_url(url)


def normalize_github_url(url: str) -> str | None:
    """Convert SSH or HTTPS git remote URL to a GitHub HTTPS URL."""
    # SSH: git@github.com:org/repo.git
    ssh_match = re.match(r"git@github\.com:(.+?)(?:\.git)?$", url)
    if ssh_match:
        return f"https://github.com/{ssh_match.group(1)}"

    # HTTPS: https://github.com/org/repo.git
    https_match = re.match(r"https://github\.com/(.+?)(?:\.git)?$", url)
    if https_match:
        return f"https://github.com/{https_match.group(1)}"

    return None


def detect_since_date(logs_dir: Path) -> str:
    """Determine --since date from most recent log entry filename.

    Falls back to "24 hours ago" if no logs exist.
    """
    if not logs_dir.exists():
        return (date.today() - timedelta(days=1)).isoformat()

    log_files = sorted(logs_dir.glob("*.md"))
    if not log_files:
        return (date.today() - timedelta(days=1)).isoformat()

    # Parse date from filename pattern: YYYY-MM-DD-*.md
    for f in reversed(log_files):
        match = re.match(r"(\d{4}-\d{2}-\d{2})", f.name)
        if match:
            return match.group(1)

    return (date.today() - timedelta(days=1)).isoformat()


def infer_tags(commits: list[dict]) -> list[str]:
    """Extract suggested tags from conventional commit prefixes."""
    tags = set()
    for commit in commits:
        msg = commit["message"]
        match = re.match(r"^(\w+)(?:\(.+?\))?:", msg)
        if match:
            prefix = match.group(1).lower()
            if prefix in PREFIX_TAG_MAP:
                tags.add(PREFIX_TAG_MAP[prefix])
    return sorted(tags)


def scan_workspace(
    workspace: Path, since: str, until: str
) -> dict:
    """Scan all git repos and build the activity data structure."""
    repos = find_git_repos(workspace)

    by_organ: dict[str, dict] = {}
    total_commits = 0
    total_files = 0
    repos_active = 0
    all_commits_flat: list[dict] = []
    github_links: list[str] = []

    for repo in repos:
        commits = git_log(repo, since, until)
        if not commits:
            continue

        files_changed = git_files_changed(repo, since, until)
        repos_active += 1
        total_commits += len(commits)
        total_files += files_changed
        all_commits_flat.extend(commits)

        # Determine organ
        parent_name = repo.parent.name
        organ_info = detect_organ(parent_name)
        if organ_info is None:
            # Top-level repo or unknown organ
            organ_info = detect_organ(repo.name)
        if organ_info is None:
            organ_key = "other"
            organ_name = "Other"
        else:
            organ_key = organ_info[0]
            organ_name = organ_info[1]

        if organ_key not in by_organ:
            by_organ[organ_key] = {"name": organ_name, "repos": {}}

        by_organ[organ_key]["repos"][repo.name] = {
            "commits": commits,
            "files_changed": files_changed,
        }

        # GitHub URL
        url = git_remote_url(repo)
        if url and url not in github_links:
            github_links.append(url)

    organs_touched = sorted(by_organ.keys())

    return {
        "generated": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "since": since,
        "until": until,
        "summary": {
            "total_commits": total_commits,
            "repos_active": repos_active,
            "files_changed": total_files,
            "organs_touched": organs_touched,
        },
        "by_organ": by_organ,
        "_links": github_links,
        "_all_commits": all_commits_flat,
    }


# GitHub org name → (organ numeral, organ name)
ORG_TO_ORGAN: dict[str, tuple[str, str]] = {
    "ivviiviivvi": ("I", "Theoria"),
    "omni-dromenon-machina": ("II", "Poiesis"),
    "labores-profani-crux": ("III", "Ergon"),
    "organvm-iv-taxis": ("IV", "Taxis"),
    "organvm-v-logos": ("V", "Logos"),
    "organvm-vi-koinonia": ("VI", "Koinonia"),
    "organvm-vii-kerygma": ("VII", "Kerygma"),
    "meta-organvm": ("META", "Meta"),
}

DEFAULT_GITHUB_ORGS: list[str] = list(ORG_TO_ORGAN.keys())


def _github_api_get(url: str, token: str) -> list | dict:  # allow-secret
    """Make an authenticated GET request to the GitHub API."""
    req = urllib.request.Request(url)
    req.add_header("Authorization", f"Bearer {token}")
    req.add_header("Accept", "application/vnd.github+json")
    req.add_header("X-GitHub-Api-Version", "2022-11-28")

    with urllib.request.urlopen(req, timeout=30) as resp:
        return json.loads(resp.read().decode("utf-8"))


def scan_github_orgs(
    token: str,  # allow-secret
    orgs: list[str],
    since: str,
    until: str,
) -> dict:
    """Scan GitHub Events API across orgs and build the activity data structure.

    Same output shape as scan_workspace() so downstream functions
    (build_scaffold, build_json_output, write_outputs) work unchanged.
    """
    since_date = since[:10]  # Normalize to YYYY-MM-DD

    by_organ: dict[str, dict] = {}
    total_commits = 0
    total_files = 0
    repos_active = 0
    all_commits_flat: list[dict] = []
    github_links: list[str] = []
    seen_repos: set[str] = set()

    for org in orgs:
        organ_info = ORG_TO_ORGAN.get(org)
        if organ_info is None:
            continue
        organ_key, organ_name = organ_info

        try:
            events = _github_api_get(
                f"https://api.github.com/orgs/{org}/events?per_page=100",
                token,
            )
            if not isinstance(events, list):
                events = []
        except (urllib.error.URLError, urllib.error.HTTPError):
            continue

        # Group push events by repo
        repo_commits: dict[str, list[dict]] = {}
        for event in events:
            created = event.get("created_at", "")[:10]
            if created < since_date:
                continue

            if event.get("type") != "PushEvent":
                continue

            repo_name = event.get("repo", {}).get("name", "")
            if "/" in repo_name:
                repo_name = repo_name.split("/", 1)[1]

            payload = event.get("payload", {})
            for c in payload.get("commits", []):
                commit = {
                    "hash": c.get("sha", "")[:7],
                    "date": created,
                    "message": c.get("message", "").split("\n")[0],
                }
                repo_commits.setdefault(repo_name, []).append(commit)
                all_commits_flat.append(commit)

        # Build organ entry
        for repo_name, commits in repo_commits.items():
            if repo_name not in seen_repos:
                repos_active += 1
                seen_repos.add(repo_name)

            total_commits += len(commits)

            if organ_key not in by_organ:
                by_organ[organ_key] = {"name": organ_name, "repos": {}}

            by_organ[organ_key]["repos"][repo_name] = {
                "commits": commits,
                "files_changed": 0,  # Not available from Events API
            }

            url = f"https://github.com/{org}/{repo_name}"
            if url not in github_links:
                github_links.append(url)

    organs_touched = sorted(by_organ.keys())

    return {
        "generated": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "since": since,
        "until": until,
        "summary": {
            "total_commits": total_commits,
            "repos_active": repos_active,
            "files_changed": total_files,
            "organs_touched": organs_touched,
        },
        "by_organ": by_organ,
        "_links": github_links,
        "_all_commits": all_commits_flat,
    }


def build_json_output(activity: dict) -> dict:
    """Build the JSON output structure (without internal fields)."""
    output = {
        "generated": activity["generated"],
        "since": activity["since"],
        "until": activity["until"],
        "summary": activity["summary"],
        "by_organ": activity["by_organ"],
    }
    return output


def build_scaffold(activity: dict, until_date: str) -> str:
    """Build the markdown scaffold for a captain's log entry."""
    summary = activity["summary"]
    by_organ = activity["by_organ"]
    links = activity.get("_links", [])
    all_commits = activity.get("_all_commits", [])

    # Infer tags from commit messages
    suggested_tags = infer_tags(all_commits)
    tag_comment = f"  # suggested: {suggested_tags}" if suggested_tags else ""

    # Frontmatter
    organs_yaml = ""
    for organ in summary["organs_touched"]:
        organs_yaml += f"\n  - {organ}"

    links_yaml = ""
    for link in links:
        links_yaml += f"\n  - {link}"

    frontmatter = f"""---
layout: log
title: ""  # fill in
date: "{until_date}"
tags: []{tag_comment}
mood:     # choose: breakthrough, focused, grinding, frustrated, reflective
organs_touched:{organs_yaml}
activity:
  since: "{activity['since']}"
  commits: {summary['total_commits']}
  repos_active: {summary['repos_active']}
  files_changed: {summary['files_changed']}
links:{links_yaml}
---"""

    # Précis placeholder
    body = """

## Précis

<!-- 1-2 sentences: the headline of this day. What was the single most important thing? -->

## Descriptive Summary

<!-- Factual narrative of what happened. What was built, fixed, moved, deployed? -->

## Analytical Summary

<!-- What patterns emerged? What does this day reveal about the system's trajectory? -->
"""

    # Polyvocal narrative sections
    body += """
---

## The Voices

> <!-- The mediator. What actually happened — the decisions, the tradeoffs, the practical shape of the day. Reference the commits above. -->
> — *Ego*

> <!-- The raw nerve. What you wanted, what felt good, what frustrated you. The visceral truth under the commit messages. -->
> — *Id*

> <!-- The critic and the conscience. What should have been done differently. The standard you're holding yourself to. -->
> — *Superego*

> <!-- Intuition, the felt sense. What's emerging that you can't yet name. The creative undercurrent. -->
> — *Anima*

> <!-- Drive and structure. The analytical thread — where this trajectory leads, what the pattern means. -->
> — *Animus*
"""

    # Workspace Activity section (now at bottom)
    since_display = _format_date_display(activity["since"])
    organ_count = len(summary["organs_touched"])
    body += f"""
---

## Workspace Activity

**{summary['total_commits']} commits** across **{summary['repos_active']} repos** in **{organ_count} organs** since {since_display}.
"""

    # Per-organ breakdown
    for organ_key in sorted(by_organ.keys()):
        organ_data = by_organ[organ_key]
        organ_name = organ_data["name"]
        if organ_key == organ_name:
            body += f"\n### {organ_key}\n"
        else:
            body += f"\n### ORGAN {organ_key} — {organ_name}\n"

        for repo_name, repo_data in sorted(organ_data["repos"].items()):
            commits = repo_data["commits"]
            count = len(commits)
            messages = ", ".join(c["message"] for c in commits[:3])
            if count > 3:
                messages += f", ... (+{count - 3} more)"
            body += f"- **{repo_name}** ({count} commits): {messages}\n"

    return frontmatter + body


def _format_date_display(iso_date: str) -> str:
    """Format an ISO date string for human display."""
    try:
        d = date.fromisoformat(iso_date)
        return d.strftime("%b %d, %Y")
    except ValueError:
        return iso_date


def write_outputs(
    activity: dict,
    logs_dir: Path,
    data_dir: Path,
    until_date: str,
) -> tuple[Path, Path]:
    """Write JSON and scaffold files. Returns (json_path, scaffold_path)."""
    # JSON output
    activity_dir = data_dir / "activity"
    activity_dir.mkdir(parents=True, exist_ok=True)
    json_path = activity_dir / f"{until_date}.json"
    json_output = build_json_output(activity)
    json_path.write_text(
        json.dumps(json_output, indent=2, ensure_ascii=False) + "\n"
    )

    # Scaffold output — never overwrite an existing log entry
    logs_dir.mkdir(parents=True, exist_ok=True)
    scaffold_path = logs_dir / f"{until_date}-captains-log.md"
    if scaffold_path.exists():
        print(
            f"Warning: {scaffold_path} already exists, skipping scaffold "
            f"(use --dry-run to preview)",
            file=sys.stderr,
        )
    else:
        scaffold = build_scaffold(activity, until_date)
        scaffold_path.write_text(scaffold)

    return json_path, scaffold_path


def main():
    parser = argparse.ArgumentParser(
        description="Scan workspace git activity and scaffold a captain's log entry"
    )
    parser.add_argument(
        "--mode", choices=["local", "github-api"], default="local",
        help="Data source: local git repos or GitHub Events API (default: local)",
    )
    parser.add_argument(
        "--workspace",
        default=str(Path.home() / "Workspace"),
        help="Path to workspace root — used in local mode (default: ~/Workspace)",
    )
    parser.add_argument(
        "--logs-dir", required=True,
        help="Path to _logs/ directory",
    )
    parser.add_argument(
        "--data-dir", required=True,
        help="Path to data/ directory",
    )
    parser.add_argument(
        "--since", default="auto",
        help="ISO date or 'auto' (default: auto = most recent log date)",
    )
    parser.add_argument(
        "--until", default=(date.today() + timedelta(days=1)).isoformat(),
        help="ISO date, exclusive upper bound (default: tomorrow, i.e. includes today)",
    )
    parser.add_argument(
        "--dry-run", action="store_true",
        help="Print scaffold to stdout instead of writing files",
    )
    args = parser.parse_args()

    logs_dir = Path(args.logs_dir).expanduser()
    data_dir = Path(args.data_dir).expanduser()

    # Resolve --since
    if args.since == "auto":
        since = detect_since_date(logs_dir)
    else:
        since = args.since

    until = args.until
    log_date = date.today().isoformat()

    # Scan via selected mode
    if args.mode == "github-api":
        token = os.environ.get("GITHUB_TOKEN", "")  # allow-secret
        if not token:
            print("Error: GITHUB_TOKEN not set (required for github-api mode)", file=sys.stderr)
            sys.exit(1)
        activity = scan_github_orgs(token, DEFAULT_GITHUB_ORGS, since, until)
    else:
        workspace = Path(args.workspace).expanduser()
        if not workspace.is_dir():
            print(f"Error: workspace not found: {workspace}", file=sys.stderr)
            sys.exit(1)
        activity = scan_workspace(workspace, since, until)

    summary = activity["summary"]

    if args.dry_run:
        print("=== JSON ===")
        print(json.dumps(build_json_output(activity), indent=2, ensure_ascii=False))
        print()
        print("=== SCAFFOLD ===")
        print(build_scaffold(activity, log_date))
        print()
        print(
            f"Summary: {summary['total_commits']} commits across "
            f"{summary['repos_active']} repos in "
            f"{len(summary['organs_touched'])} organs"
        )
    else:
        json_path, scaffold_path = write_outputs(
            activity, logs_dir, data_dir, log_date
        )
        print(f"Activity JSON: {json_path}")
        print(f"Log scaffold:  {scaffold_path}")
        print(
            f"Summary: {summary['total_commits']} commits across "
            f"{summary['repos_active']} repos in "
            f"{len(summary['organs_touched'])} organs"
        )

    sys.exit(0)


if __name__ == "__main__":
    main()
