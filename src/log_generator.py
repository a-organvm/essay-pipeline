"""Workspace activity scanner and captain's log scaffolder for ORGAN-V.

Scans git repos across ~/Workspace, produces a JSON activity snapshot
and scaffolds a captain's log entry pre-filled with real commit data.

CLI: python -m src.log_generator --workspace ~/Workspace \
       --logs-dir ../public-process/_logs/ \
       --data-dir ../public-process/data/
"""

import argparse
import json
import re
import subprocess
import sys
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

    # Activity summary line
    since_display = _format_date_display(activity["since"])
    organ_count = len(summary["organs_touched"])
    body = f"""

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

    # Narrative sections
    body += """
---

## What I Did

<!-- Write your narrative here -->

## What I Learned

<!-- Write your reflection here -->

## What's Next

<!-- Write your forward-looking items here -->
"""

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

    # Scaffold output
    logs_dir.mkdir(parents=True, exist_ok=True)
    scaffold_path = logs_dir / f"{until_date}-captains-log.md"
    scaffold = build_scaffold(activity, until_date)
    scaffold_path.write_text(scaffold)

    return json_path, scaffold_path


def main():
    parser = argparse.ArgumentParser(
        description="Scan workspace git activity and scaffold a captain's log entry"
    )
    parser.add_argument(
        "--workspace",
        default=str(Path.home() / "Workspace"),
        help="Path to workspace root (default: ~/Workspace)",
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

    workspace = Path(args.workspace).expanduser()
    logs_dir = Path(args.logs_dir).expanduser()
    data_dir = Path(args.data_dir).expanduser()

    if not workspace.is_dir():
        print(f"Error: workspace not found: {workspace}", file=sys.stderr)
        sys.exit(1)

    # Resolve --since
    if args.since == "auto":
        since = detect_since_date(logs_dir)
    else:
        since = args.since

    until = args.until
    # The log entry date is today (not the exclusive git upper bound)
    log_date = date.today().isoformat()

    # Scan
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
