"""Tests for the essay indexer."""

import json
from pathlib import Path

from src.indexer import (
    build_cross_references,
    build_essays_index,
    build_logs_index,
    build_publication_calendar,
    extract_essay_data,
    index_all,
    compute_readability,
)


class TestComputeReadability:
    def test_basic_scoring(self):
        text = "This is a simple sentence. It has clear words."
        score = compute_readability(text)
        assert 0 <= score <= 100
        # High score means easy to read
        assert score > 60

    def test_empty_text(self):
        assert compute_readability("") == 0.0


FIXTURES = Path(__file__).parent / "fixtures"


class TestExtractEssayData:
    def test_extracts_valid_essay(self):
        data = extract_essay_data(FIXTURES / "valid-essay.md")
        assert data is not None
        assert data["filename"] == "valid-essay.md"
        assert (
            data["frontmatter"]["title"]
            == "A Perfectly Valid Test Essay for the Pipeline"
        )
        assert data["computed_word_count"] > 0
        assert "readability_score" in data

    def test_no_frontmatter_returns_none(self, tmp_path):
        p = tmp_path / "bare.md"
        p.write_text("Just plain markdown with no frontmatter.")
        assert extract_essay_data(p) is None

    def test_word_count_computation(self):
        data = extract_essay_data(FIXTURES / "valid-essay.md")
        assert data is not None
        # Body has real content — should be at least 40 words
        assert data["computed_word_count"] >= 40


class TestBuildEssaysIndex:
    def test_generates_correct_structure(self):
        essays = [extract_essay_data(FIXTURES / "valid-essay.md")]
        index = build_essays_index(essays)
        assert index["total_essays"] == 1
        assert index["total_words"] > 0
        assert "meta-system" in index["categories"]
        assert "governance" in index["tag_frequency"]
        assert len(index["essays"]) == 1

    def test_handles_zero_essays(self):
        index = build_essays_index([])
        assert index["total_essays"] == 0
        assert index["total_words"] == 0
        assert index["categories"] == {}

    def test_multiple_essays(self):
        essays = []
        for f in FIXTURES.glob("*.md"):
            data = extract_essay_data(f)
            if data:
                essays.append(data)
        index = build_essays_index(essays)
        assert index["total_essays"] == len(essays)
        assert index["total_essays"] >= 3  # We have at least 3 valid fixture files


class TestBuildCrossReferences:
    def test_generates_keyed_entries(self):
        essays = [extract_essay_data(FIXTURES / "valid-essay.md")]
        xrefs = build_cross_references(essays)
        assert xrefs["total"] == 1
        assert "valid-essay.md" in xrefs["entries"]
        entry = xrefs["entries"]["valid-essay.md"]
        assert "organvm-v-logos/essay-pipeline" in entry["related_repos"]


def _make_essay(
    filename: str,
    title: str,
    dt: str,
    category: str = "meta-system",
    tags: list | None = None,
    mood: str = "",
    organs: list | None = None,
) -> dict:
    """Helper to build a minimal essay/log data dict for testing."""
    return {
        "filename": filename,
        "frontmatter": {
            "title": title,
            "date": dt,
            "category": category,
            "tags": tags or ["governance"],
            "reading_time": "5 min",
            "portfolio_relevance": "HIGH",
            "related_repos": [],
            "mood": mood,
            "organs_touched": organs or [],
        },
        "computed_word_count": 500,
    }


class TestBuildPublicationCalendar:
    def test_groups_by_date(self):
        essays = [
            _make_essay("a.md", "A", "2026-02-10"),
            _make_essay("b.md", "B", "2026-02-10"),
            _make_essay("c.md", "C", "2026-02-11"),
        ]
        calendar = build_publication_calendar(essays)
        assert calendar["total_essays"] == 3
        assert calendar["essays"]["2026-02-10"] == 2
        assert calendar["essays"]["2026-02-11"] == 1

    def test_includes_logs_when_provided(self):
        essays = [_make_essay("a.md", "A", "2026-02-10")]
        logs = [_make_essay("log1.md", "Log 1", "2026-02-12")]
        calendar = build_publication_calendar(essays, logs=logs)
        assert "total_logs" in calendar
        assert calendar["total_logs"] == 1
        assert "logs" in calendar

    def test_empty_essays(self):
        calendar = build_publication_calendar([])
        assert calendar["total_essays"] == 0
        assert calendar["essays"] == {}


class TestBuildLogsIndex:
    def test_basic_structure(self):
        logs = [
            _make_essay(
                "log1.md",
                "Log 1",
                "2026-02-10",
                mood="focused",
                tags=["governance", "feature"],
            )
        ]
        index = build_logs_index(logs)
        assert index["total_logs"] == 1
        assert index["total_words"] == 500
        assert "focused" in index["mood_frequency"]
        assert "governance" in index["tag_frequency"]

    def test_mood_frequency(self):
        logs = [
            _make_essay("log1.md", "Log 1", "2026-02-10", mood="focused"),
            _make_essay("log2.md", "Log 2", "2026-02-11", mood="focused"),
            _make_essay("log3.md", "Log 3", "2026-02-12", mood="grinding"),
        ]
        index = build_logs_index(logs)
        assert index["mood_frequency"]["focused"] == 2
        assert index["mood_frequency"]["grinding"] == 1

    def test_empty_logs(self):
        index = build_logs_index([])
        assert index["total_logs"] == 0
        assert index["total_words"] == 0
        assert index["mood_frequency"] == {}


class TestIndexAll:
    def _write_fixture(self, path: Path, title: str, dt: str) -> None:
        """Write a minimal valid essay file."""
        path.write_text(
            f'---\nlayout: essay\ntitle: "{title}"\nauthor: "@test"\n'
            f'date: "{dt}"\ntags:\n  - governance\n  - testing\n'
            f'category: meta-system\nexcerpt: "A test essay for pipeline validation."\n'
            f"portfolio_relevance: HIGH\nrelated_repos:\n  - organvm-v-logos/test\n"
            f'reading_time: "5 min"\nword_count: 500\n---\n\n' + "Word " * 100 + "\n"
        )

    def test_generates_all_json_files(self, tmp_path):
        posts_dir = tmp_path / "_posts"
        posts_dir.mkdir()
        output_dir = tmp_path / "data"
        self._write_fixture(
            posts_dir / "2026-02-10-test.md", "Test Essay", "2026-02-10"
        )

        summary = index_all(str(posts_dir), str(output_dir))
        assert summary["essays"] == 1
        assert (output_dir / "essays-index.json").exists()
        assert (output_dir / "cross-references.json").exists()
        assert (output_dir / "publication-calendar.json").exists()
        assert not (output_dir / "logs-index.json").exists()

    def test_with_logs_generates_four_files(self, tmp_path):
        posts_dir = tmp_path / "_posts"
        posts_dir.mkdir()
        logs_dir = tmp_path / "_logs"
        logs_dir.mkdir()
        output_dir = tmp_path / "data"
        self._write_fixture(
            posts_dir / "2026-02-10-test.md", "Test Essay", "2026-02-10"
        )
        self._write_fixture(logs_dir / "2026-02-11-log.md", "Log Entry", "2026-02-11")

        summary = index_all(str(posts_dir), str(output_dir), logs_dir=str(logs_dir))
        assert summary["essays"] == 1
        assert summary["logs"] == 1
        assert (output_dir / "logs-index.json").exists()
        logs_index = json.loads((output_dir / "logs-index.json").read_text())
        assert logs_index["total_logs"] == 1


# --- Additional extract_essay_data coverage --------------------------------


class TestExtractEssayDataExtended:
    def test_yaml_parse_error(self, tmp_path):
        """Malformed YAML in frontmatter returns None."""
        p = tmp_path / "bad-yaml.md"
        p.write_text("---\ntitle: [invalid yaml\n---\n\nBody text here.")
        assert extract_essay_data(p) is None

    def test_empty_body_word_count(self, tmp_path):
        """File with frontmatter but empty body has word_count 0."""
        p = tmp_path / "empty-body.md"
        p.write_text("---\ntitle: Empty\n---\n\n")
        data = extract_essay_data(p)
        assert data is not None
        assert data["computed_word_count"] == 0

    def test_url_stripping_in_word_count(self, tmp_path):
        """URLs are stripped from body before word count."""
        p = tmp_path / "urls.md"
        p.write_text(
            "---\ntitle: URLs\n---\n\n"
            "Visit https://example.com/very/long/path and "
            "also http://other.com/page for details.\n"
        )
        data = extract_essay_data(p)
        assert data is not None
        # "Visit and also for details." = 5 words (URLs stripped)
        assert data["computed_word_count"] == 5

    def test_markdown_formatting_stripped(self, tmp_path):
        """Markdown symbols (#, *, etc.) don't inflate word count."""
        p = tmp_path / "formatted.md"
        p.write_text(
            "---\ntitle: Formatted\n---\n\n"
            "## Heading\n\n"
            "**Bold** and *italic* and `code`.\n"
        )
        data = extract_essay_data(p)
        assert data is not None
        # "Heading Bold and italic and code" — symbols stripped
        assert data["computed_word_count"] > 0
