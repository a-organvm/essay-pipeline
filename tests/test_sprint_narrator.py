"""Tests for the sprint narrative generator."""

import json
from pathlib import Path

from src.sprint_narrator import (
    format_alerts,
    generate_narrative,
    load_json_safe,
    narrate_all,
    summarize_essay_corpus,
    summarize_github_activity,
    summarize_publication_cadence,
    summarize_web_engagement,
)

FIXTURES = Path(__file__).parent / "fixtures"


class TestLoadJsonSafe:
    def test_loads_valid_json(self):
        result = load_json_safe(str(FIXTURES / "mini-essays-index.json"))
        assert isinstance(result, dict)
        assert result["total_essays"] == 5

    def test_missing_file_returns_empty(self):
        result = load_json_safe("/nonexistent/path.json")
        assert result == {}

    def test_malformed_json_returns_empty(self, tmp_path):
        bad = tmp_path / "bad.json"
        bad.write_text("{invalid json")
        assert load_json_safe(str(bad)) == {}


class TestSummarizeWebEngagement:
    def test_with_real_metrics(self):
        metrics = json.loads((FIXTURES / "mini-engagement-metrics.json").read_text())
        result = summarize_web_engagement(metrics)
        assert "1,077 page views" in result
        assert "782 unique visitors" in result
        assert "/essays/how-we-orchestrate/" in result
        assert "15.3%" in result

    def test_empty_metrics(self):
        result = summarize_web_engagement({})
        assert "No web engagement data" in result

    def test_no_pages(self):
        metrics = {
            "site_totals": {"page_views": 100, "unique_visitors": 50},
            "pages": [],
            "trends": {},
        }
        result = summarize_web_engagement(metrics)
        assert "100 page views" in result
        assert "most-visited" not in result.lower()

    def test_negative_trend(self):
        metrics = {
            "site_totals": {"page_views": 100, "unique_visitors": 50},
            "pages": [],
            "trends": {"views_delta_pct": -12.5, "visitors_delta_pct": -5.0},
        }
        result = summarize_web_engagement(metrics)
        assert "down 12.5%" in result


class TestSummarizeGithubActivity:
    def test_with_real_report(self):
        report = json.loads((FIXTURES / "mini-system-report.json").read_text())
        result = summarize_github_activity(report)
        assert "45 commits" in result
        assert "7 PRs" in result
        assert "2 releases" in result
        assert "ORGAN-III" in result

    def test_empty_report(self):
        result = summarize_github_activity({})
        assert "No GitHub activity data" in result

    def test_zero_commits(self):
        report = {
            "github_activity": {
                "total_commits": 0,
                "total_prs": 0,
                "total_releases": 0,
                "organ_breakdown": {"I": {"commits": 0}},
            }
        }
        result = summarize_github_activity(report)
        assert "0 commits" in result
        assert "No commit activity" in result


class TestSummarizeEssayCorpus:
    def test_with_fixture(self):
        index = json.loads((FIXTURES / "mini-essays-index.json").read_text())
        result = summarize_essay_corpus(index)
        assert "5 essays" in result
        assert "15,000 words" in result
        assert "meta-system" in result

    def test_empty_index(self):
        result = summarize_essay_corpus({})
        assert "No essay index data" in result


class TestSummarizePublicationCadence:
    def test_with_fixture(self):
        calendar = json.loads((FIXTURES / "mini-pub-calendar.json").read_text())
        result = summarize_publication_cadence(calendar)
        assert "5 distinct days" in result
        assert "2026-02-10" in result
        assert "2026-02-14" in result

    def test_empty_calendar(self):
        result = summarize_publication_cadence({})
        assert "No publication calendar data" in result

    def test_no_dates(self):
        result = summarize_publication_cadence({"total_essays": 5, "dates": {}})
        assert "no dated publication data" in result


class TestFormatAlerts:
    def test_with_alerts(self):
        report = json.loads((FIXTURES / "mini-system-report.json").read_text())
        result = format_alerts(report)
        assert "github_stall" in result
        assert "\u26a0\ufe0f" in result

    def test_no_alerts(self):
        result = format_alerts({"alerts": []})
        assert "No alerts triggered" in result

    def test_empty_report(self):
        result = format_alerts({})
        assert "No alert data" in result


class TestGenerateNarrative:
    def test_full_narrative_structure(self):
        metrics = json.loads((FIXTURES / "mini-engagement-metrics.json").read_text())
        report = json.loads((FIXTURES / "mini-system-report.json").read_text())
        index = json.loads((FIXTURES / "mini-essays-index.json").read_text())
        calendar = json.loads((FIXTURES / "mini-pub-calendar.json").read_text())

        result = generate_narrative(metrics, report, index, calendar)

        assert "# Sprint Narrative" in result
        assert "## Sprint Summary" in result
        assert "## Web Engagement" in result
        assert "## GitHub Activity" in result
        assert "## Essay Corpus" in result
        assert "## Publication Cadence" in result
        assert "## Alerts" in result
        assert "## Suggested Focus Areas" in result
        assert "Human review required" in result

    def test_handles_all_empty_data(self):
        result = generate_narrative({}, {}, {}, {})
        assert "# Sprint Narrative" in result
        assert "No web engagement data" in result
        assert "No GitHub activity data" in result


class TestNarrateAll:
    def test_end_to_end(self, tmp_path):
        output = tmp_path / "narrative.md"
        summary = narrate_all(
            str(FIXTURES / "mini-engagement-metrics.json"),
            str(FIXTURES / "mini-system-report.json"),
            str(FIXTURES / "mini-essays-index.json"),
            str(FIXTURES / "mini-pub-calendar.json"),
            str(output),
        )

        assert output.exists()
        content = output.read_text()
        assert "# Sprint Narrative" in content
        assert summary["pipeline_version"] == "0.2.0"
        assert len(summary["sections"]) == 7

    def test_handles_missing_files(self, tmp_path):
        output = tmp_path / "narrative.md"
        summary = narrate_all(
            "/nonexistent/metrics.json",
            "/nonexistent/report.json",
            "/nonexistent/index.json",
            "/nonexistent/calendar.json",
            str(output),
        )

        assert output.exists()
        content = output.read_text()
        assert "No web engagement data" in content
        assert "generated_at" in summary
