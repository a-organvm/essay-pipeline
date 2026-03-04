"""Tests for the log generator."""

import json
import subprocess
import urllib.error
from pathlib import Path
from unittest.mock import patch

from src.log_generator import (
    ORG_TO_ORGAN,
    build_json_output,
    build_scaffold,
    detect_organ,
    detect_since_date,
    find_git_repos,
    git_log,
    git_files_changed,
    infer_tags,
    normalize_github_url,
    scan_github_orgs,
    scan_workspace,
)


class TestOrganDetection:
    def test_all_organs(self):
        cases = {
            "organvm-i-theoria": ("I", "Theoria"),
            "organvm-ii-poiesis": ("II", "Poiesis"),
            "organvm-iii-ergon": ("III", "Ergon"),
            "organvm-iv-taxis": ("IV", "Taxis"),
            "organvm-v-logos": ("V", "Logos"),
            "organvm-vi-koinonia": ("VI", "Koinonia"),
            "organvm-vii-kerygma": ("VII", "Kerygma"),
            "meta-organvm": ("META", "Meta"),
            "4444J99": ("Personal", "Personal"),
        }
        for dir_name, expected in cases.items():
            assert detect_organ(dir_name) == expected, f"Failed for {dir_name}"

    def test_unknown_directory(self):
        assert detect_organ("random-dir") is None
        assert detect_organ("") is None

    def test_case_sensitive(self):
        # Only exact matches
        assert detect_organ("ORGANVM-I-THEORIA") is None
        # But 4444j99 lowercase works
        assert detect_organ("4444j99") == ("Personal", "Personal")


class TestAutoSinceDetection:
    def test_from_log_filenames(self, tmp_path):
        (tmp_path / "2026-02-20-first.md").write_text("---\n---\n")
        (tmp_path / "2026-02-25-second.md").write_text("---\n---\n")
        (tmp_path / "2026-02-27-third.md").write_text("---\n---\n")
        assert detect_since_date(tmp_path) == "2026-02-27"

    def test_no_logs_dir(self, tmp_path):
        nonexistent = tmp_path / "nope"
        result = detect_since_date(nonexistent)
        # Should return yesterday's date
        assert len(result) == 10  # YYYY-MM-DD format

    def test_empty_logs_dir(self, tmp_path):
        result = detect_since_date(tmp_path)
        assert len(result) == 10

    def test_non_date_filenames_skipped(self, tmp_path):
        (tmp_path / "readme.md").write_text("hello")
        (tmp_path / "2026-02-15-real-log.md").write_text("---\n---\n")
        assert detect_since_date(tmp_path) == "2026-02-15"


def _init_test_repo(path: Path, commits: list[tuple[str, str]] | None = None):
    """Create a git repo with optional commits. Each commit is (filename, message)."""
    subprocess.run(["git", "init"], cwd=path, capture_output=True)
    subprocess.run(
        ["git", "config", "user.email", "test@test.com"],
        cwd=path, capture_output=True,
    )
    subprocess.run(
        ["git", "config", "user.name", "Test"],
        cwd=path, capture_output=True,
    )
    if commits:
        for filename, message in commits:
            (path / filename).write_text(f"content of {filename}\n")
            subprocess.run(["git", "add", filename], cwd=path, capture_output=True)
            subprocess.run(
                ["git", "commit", "-m", message],
                cwd=path, capture_output=True,
            )


class TestCommitParsing:
    def test_parses_commits(self, tmp_path):
        repo = tmp_path / "repo"
        repo.mkdir()
        _init_test_repo(repo, [
            ("a.txt", "feat: add feature A"),
            ("b.txt", "fix: resolve bug B"),
        ])
        commits = git_log(repo, "1970-01-01", "2099-12-31")
        assert len(commits) == 2
        # Most recent first
        assert commits[0]["message"] == "fix: resolve bug B"
        assert commits[1]["message"] == "feat: add feature A"
        assert len(commits[0]["hash"]) == 7

    def test_no_commits_in_range(self, tmp_path):
        repo = tmp_path / "repo"
        repo.mkdir()
        _init_test_repo(repo, [("a.txt", "feat: old commit")])
        # Use a far-future range that excludes today
        commits = git_log(repo, "2099-01-01", "2099-12-31")
        assert commits == []

    def test_nonexistent_repo(self, tmp_path):
        fake = tmp_path / "nonexistent"
        assert git_log(fake, "2026-01-01", "2026-12-31") == []

    def test_files_changed(self, tmp_path):
        repo = tmp_path / "repo"
        repo.mkdir()
        _init_test_repo(repo, [
            ("file1.txt", "feat: add file1"),
            ("file2.txt", "feat: add file2"),
        ])
        count = git_files_changed(repo, "1970-01-01", "2099-12-31")
        assert count == 2


class TestGitHubUrlInference:
    def test_ssh_url(self):
        url = normalize_github_url("git@github.com:organvm-v-logos/essay-pipeline.git")
        assert url == "https://github.com/organvm-v-logos/essay-pipeline"

    def test_ssh_url_no_git_suffix(self):
        url = normalize_github_url("git@github.com:organvm-v-logos/essay-pipeline")
        assert url == "https://github.com/organvm-v-logos/essay-pipeline"

    def test_https_url(self):
        url = normalize_github_url("https://github.com/organvm-v-logos/essay-pipeline.git")
        assert url == "https://github.com/organvm-v-logos/essay-pipeline"

    def test_https_url_no_git_suffix(self):
        url = normalize_github_url("https://github.com/organvm-v-logos/essay-pipeline")
        assert url == "https://github.com/organvm-v-logos/essay-pipeline"

    def test_non_github_url(self):
        assert normalize_github_url("https://gitlab.com/org/repo.git") is None

    def test_empty_string(self):
        assert normalize_github_url("") is None


class TestFindGitRepos:
    def test_finds_depth1_and_depth2(self, tmp_path):
        # Depth 1 repo
        top_repo = tmp_path / "standalone"
        top_repo.mkdir()
        (top_repo / ".git").mkdir()

        # Depth 2 repos (organ/repo pattern)
        organ = tmp_path / "organvm-v-logos"
        organ.mkdir()
        sub_repo = organ / "essay-pipeline"
        sub_repo.mkdir()
        (sub_repo / ".git").mkdir()

        repos = find_git_repos(tmp_path)
        assert len(repos) == 2

    def test_skips_hidden_dirs(self, tmp_path):
        hidden = tmp_path / ".hidden"
        hidden.mkdir()
        (hidden / ".git").mkdir()
        repos = find_git_repos(tmp_path)
        assert len(repos) == 0

    def test_empty_workspace(self, tmp_path):
        repos = find_git_repos(tmp_path)
        assert repos == []


class TestScaffoldFrontmatter:
    def _make_activity(self):
        return {
            "generated": "2026-02-28T20:00:00Z",
            "since": "2026-02-27",
            "until": "2026-02-28",
            "summary": {
                "total_commits": 5,
                "repos_active": 3,
                "files_changed": 10,
                "organs_touched": ["III", "V"],
            },
            "by_organ": {
                "V": {
                    "name": "Logos",
                    "repos": {
                        "public-process": {
                            "commits": [
                                {"hash": "abc1234", "date": "2026-02-27", "message": "fix: something"},
                            ],
                            "files_changed": 2,
                        },
                    },
                },
                "III": {
                    "name": "Ergon",
                    "repos": {
                        "some-tool": {
                            "commits": [
                                {"hash": "def5678", "date": "2026-02-27", "message": "feat: add thing"},
                                {"hash": "ghi9012", "date": "2026-02-27", "message": "chore: cleanup"},
                            ],
                            "files_changed": 8,
                        },
                    },
                },
            },
            "_links": [
                "https://github.com/organvm-v-logos/public-process",
                "https://github.com/organvm-iii-ergon/some-tool",
            ],
            "_all_commits": [
                {"hash": "abc1234", "date": "2026-02-27", "message": "fix: something"},
                {"hash": "def5678", "date": "2026-02-27", "message": "feat: add thing"},
                {"hash": "ghi9012", "date": "2026-02-27", "message": "chore: cleanup"},
            ],
        }

    def test_contains_frontmatter_delimiters(self):
        activity = self._make_activity()
        scaffold = build_scaffold(activity, "2026-02-28")
        assert scaffold.startswith("---\n")
        assert "\n---\n" in scaffold

    def test_contains_required_frontmatter_fields(self):
        activity = self._make_activity()
        scaffold = build_scaffold(activity, "2026-02-28")
        assert 'layout: log' in scaffold
        assert 'title: ""' in scaffold
        assert 'date: "2026-02-28"' in scaffold
        assert "mood:" in scaffold
        assert "organs_touched:" in scaffold
        assert "activity:" in scaffold
        assert "commits: 5" in scaffold
        assert "repos_active: 3" in scaffold
        assert "files_changed: 10" in scaffold

    def test_contains_organ_sections(self):
        activity = self._make_activity()
        scaffold = build_scaffold(activity, "2026-02-28")
        assert "Ergon" in scaffold
        assert "Logos" in scaffold
        assert "public-process" in scaffold
        assert "some-tool" in scaffold

    def test_contains_narrative_sections(self):
        activity = self._make_activity()
        scaffold = build_scaffold(activity, "2026-02-28")
        assert "## The Voices" in scaffold
        assert "— *Ego*" in scaffold
        assert "— *Id*" in scaffold
        assert "— *Superego*" in scaffold
        assert "— *Anima*" in scaffold
        assert "— *Animus*" in scaffold

    def test_contains_summary_placeholders(self):
        activity = self._make_activity()
        scaffold = build_scaffold(activity, "2026-02-28")
        assert "## Précis" in scaffold
        assert "## Descriptive Summary" in scaffold
        assert "## Analytical Summary" in scaffold

    def test_section_order(self):
        """Verify sections appear in correct order: Précis → Descriptive → Analytical → Voices → Workspace Activity."""
        activity = self._make_activity()
        scaffold = build_scaffold(activity, "2026-02-28")
        precis_pos = scaffold.index("## Précis")
        descriptive_pos = scaffold.index("## Descriptive Summary")
        analytical_pos = scaffold.index("## Analytical Summary")
        voices_pos = scaffold.index("## The Voices")
        activity_pos = scaffold.index("## Workspace Activity")
        assert precis_pos < descriptive_pos < analytical_pos < voices_pos < activity_pos

    def test_suggested_tags_in_comment(self):
        activity = self._make_activity()
        scaffold = build_scaffold(activity, "2026-02-28")
        assert "# suggested:" in scaffold
        assert "feature" in scaffold
        assert "fix" in scaffold


class TestJsonOutputStructure:
    def test_has_required_keys(self):
        activity = {
            "generated": "2026-02-28T20:00:00Z",
            "since": "2026-02-27",
            "until": "2026-02-28",
            "summary": {
                "total_commits": 0,
                "repos_active": 0,
                "files_changed": 0,
                "organs_touched": [],
            },
            "by_organ": {},
            "_links": [],
            "_all_commits": [],
        }
        output = build_json_output(activity)
        assert "generated" in output
        assert "since" in output
        assert "until" in output
        assert "summary" in output
        assert "by_organ" in output
        # Internal fields excluded
        assert "_links" not in output
        assert "_all_commits" not in output

    def test_summary_fields(self):
        activity = {
            "generated": "2026-02-28T20:00:00Z",
            "since": "2026-02-27",
            "until": "2026-02-28",
            "summary": {
                "total_commits": 15,
                "repos_active": 7,
                "files_changed": 23,
                "organs_touched": ["III", "V", "VII"],
            },
            "by_organ": {},
            "_links": [],
            "_all_commits": [],
        }
        output = build_json_output(activity)
        s = output["summary"]
        assert s["total_commits"] == 15
        assert s["repos_active"] == 7
        assert s["files_changed"] == 23
        assert s["organs_touched"] == ["III", "V", "VII"]


class TestEmptyWorkspace:
    def test_produces_valid_output(self, tmp_path):
        activity = scan_workspace(tmp_path, "2026-01-01", "2026-12-31")
        assert activity["summary"]["total_commits"] == 0
        assert activity["summary"]["repos_active"] == 0
        assert activity["by_organ"] == {}

    def test_empty_scaffold_is_valid(self, tmp_path):
        activity = scan_workspace(tmp_path, "2026-01-01", "2026-12-31")
        scaffold = build_scaffold(activity, "2026-02-28")
        assert "---" in scaffold
        assert "## Précis" in scaffold
        assert "## The Voices" in scaffold
        assert "## Workspace Activity" in scaffold

    def test_empty_json_is_valid(self, tmp_path):
        activity = scan_workspace(tmp_path, "2026-01-01", "2026-12-31")
        output = build_json_output(activity)
        # Should be valid JSON
        serialized = json.dumps(output)
        parsed = json.loads(serialized)
        assert parsed["summary"]["total_commits"] == 0


class TestTagInference:
    def test_extracts_conventional_prefixes(self):
        commits = [
            {"hash": "a", "date": "2026-01-01", "message": "feat: add thing"},
            {"hash": "b", "date": "2026-01-01", "message": "fix: resolve bug"},
            {"hash": "c", "date": "2026-01-01", "message": "docs: update readme"},
            {"hash": "d", "date": "2026-01-01", "message": "chore: cleanup"},
        ]
        tags = infer_tags(commits)
        assert "feature" in tags
        assert "fix" in tags
        assert "documentation" in tags
        assert "infrastructure" in tags

    def test_scoped_prefixes(self):
        commits = [
            {"hash": "a", "date": "2026-01-01", "message": "feat(auth): add login"},
        ]
        tags = infer_tags(commits)
        assert "feature" in tags

    def test_no_conventional_commits(self):
        commits = [
            {"hash": "a", "date": "2026-01-01", "message": "updated stuff"},
        ]
        tags = infer_tags(commits)
        assert tags == []


class TestIntegration:
    def test_full_scan_with_test_repo(self, tmp_path):
        """End-to-end test with a controlled workspace."""
        # Create workspace with organ/repo structure
        organ_dir = tmp_path / "organvm-v-logos"
        organ_dir.mkdir()
        repo_dir = organ_dir / "test-repo"
        repo_dir.mkdir()
        _init_test_repo(repo_dir, [
            ("file1.py", "feat: add feature"),
            ("file2.py", "fix: resolve issue"),
        ])

        activity = scan_workspace(tmp_path, "1970-01-01", "2099-12-31")
        assert activity["summary"]["total_commits"] == 2
        assert activity["summary"]["repos_active"] == 1
        assert "V" in activity["summary"]["organs_touched"]
        assert "V" in activity["by_organ"]
        assert "test-repo" in activity["by_organ"]["V"]["repos"]

    def test_write_outputs(self, tmp_path):
        """Test that write_outputs creates both files."""
        organ_dir = tmp_path / "organvm-iii-ergon"
        organ_dir.mkdir()
        repo_dir = organ_dir / "my-tool"
        repo_dir.mkdir()
        _init_test_repo(repo_dir, [("x.py", "feat: init")])

        logs_dir = tmp_path / "logs"
        data_dir = tmp_path / "data"

        activity = scan_workspace(tmp_path, "1970-01-01", "2099-12-31")

        from src.log_generator import write_outputs
        json_path, scaffold_path = write_outputs(
            activity, logs_dir, data_dir, "2026-02-28"
        )

        assert json_path.exists()
        assert scaffold_path.exists()

        # Validate JSON
        data = json.loads(json_path.read_text())
        assert data["summary"]["total_commits"] == 1

        # Validate scaffold
        content = scaffold_path.read_text()
        assert content.startswith("---\n")
        assert "my-tool" in content


class TestOrgToOrganMapping:
    def test_all_orgs_present(self):
        expected_orgs = {
            "ivviiviivvi", "omni-dromenon-machina", "labores-profani-crux",
            "organvm-iv-taxis", "organvm-v-logos", "organvm-vi-koinonia",
            "organvm-vii-kerygma", "meta-organvm",
        }
        assert set(ORG_TO_ORGAN.keys()) == expected_orgs

    def test_organ_values_correct(self):
        assert ORG_TO_ORGAN["ivviiviivvi"] == ("I", "Theoria")
        assert ORG_TO_ORGAN["organvm-v-logos"] == ("V", "Logos")
        assert ORG_TO_ORGAN["meta-organvm"] == ("META", "Meta")


class TestScanGithubOrgs:
    @patch("src.log_generator._github_api_get")
    def test_processes_push_events(self, mock_api):
        mock_api.return_value = [
            {
                "type": "PushEvent",
                "created_at": "2026-02-20T10:00:00Z",
                "repo": {"name": "organvm-v-logos/essay-pipeline"},
                "payload": {
                    "commits": [
                        {"sha": "abc1234567890", "message": "feat: add feature"},
                    ]
                },
            },
        ]
        activity = scan_github_orgs(
            "ghp_test", ["organvm-v-logos"], "2026-02-19", "2026-02-21"  # allow-secret
        )
        assert activity["summary"]["total_commits"] == 1
        assert activity["summary"]["repos_active"] == 1
        assert "V" in activity["by_organ"]
        assert "essay-pipeline" in activity["by_organ"]["V"]["repos"]

    @patch("src.log_generator._github_api_get")
    def test_handles_api_error(self, mock_api):
        mock_api.side_effect = urllib.error.URLError("Connection refused")
        activity = scan_github_orgs(
            "ghp_test", ["organvm-v-logos"], "2026-02-19", "2026-02-21"  # allow-secret
        )
        assert activity["summary"]["total_commits"] == 0
        assert activity["by_organ"] == {}

    @patch("src.log_generator._github_api_get")
    def test_filters_by_date(self, mock_api):
        mock_api.return_value = [
            {
                "type": "PushEvent",
                "created_at": "2026-02-20T10:00:00Z",
                "repo": {"name": "organvm-v-logos/repo-a"},
                "payload": {"commits": [{"sha": "aaa", "message": "feat: new"}]},
            },
            {
                "type": "PushEvent",
                "created_at": "2026-02-15T10:00:00Z",  # before since
                "repo": {"name": "organvm-v-logos/repo-b"},
                "payload": {"commits": [{"sha": "bbb", "message": "fix: old"}]},
            },
        ]
        activity = scan_github_orgs(
            "ghp_test", ["organvm-v-logos"], "2026-02-19", "2026-02-21"  # allow-secret
        )
        assert activity["summary"]["total_commits"] == 1
