"""Tests for the topic suggestion engine."""

import json
from pathlib import Path

from src.topic_suggester import (
    extract_surfaced_topics,
    find_cross_reference_gaps,
    find_underserved_categories,
    find_underused_tags,
    generate_suggestions,
    suggest_all,
)

FIXTURES = Path(__file__).parent / "fixtures"


class TestFindUnderusedTags:
    def test_finds_tags_below_threshold(self):
        tag_freq = {"governance": 10, "game-design": 1, "art": 0}
        preferred = ["governance", "game-design", "art", "missing-tag"]
        result = find_underused_tags(tag_freq, preferred, threshold=2)
        tags = [r["tag"] for r in result]
        assert "game-design" in tags
        assert "art" in tags
        assert "missing-tag" in tags
        assert "governance" not in tags

    def test_empty_preferred_returns_empty(self):
        assert find_underused_tags({"governance": 5}, [], threshold=2) == []

    def test_custom_threshold(self):
        tag_freq = {"governance": 3, "art": 2}
        preferred = ["governance", "art"]
        result = find_underused_tags(tag_freq, preferred, threshold=5)
        assert len(result) == 2

    def test_zero_count_included(self):
        result = find_underused_tags({}, ["never-used"], threshold=1)
        assert len(result) == 1
        assert result[0]["current_count"] == 0


class TestFindUnderservedCategories:
    def test_finds_categories_below_typical(self):
        categories = {"meta-system": 21, "case-study": 3, "guide": 6}
        taxonomy = {
            "categories": {
                "meta-system": {"typical_count": 19},
                "case-study": {"typical_count": 7},
                "guide": {"typical_count": 6},
            }
        }
        result = find_underserved_categories(categories, taxonomy)
        cats = [r["category"] for r in result]
        assert "case-study" in cats
        assert "meta-system" not in cats
        assert "guide" not in cats

    def test_empty_taxonomy_returns_empty(self):
        result = find_underserved_categories({"meta-system": 5}, {})
        assert result == []

    def test_deficit_computed_correctly(self):
        categories = {"retrospective": 2}
        taxonomy = {"categories": {"retrospective": {"typical_count": 4}}}
        result = find_underserved_categories(categories, taxonomy)
        assert result[0]["deficit"] == 2


class TestExtractSurfacedTopics:
    def test_filters_by_score(self):
        surfaced = json.loads((FIXTURES / "mini-surfaced.json").read_text())
        result = extract_surfaced_topics(surfaced)
        assert len(result) == 2
        scores = [r["score"] for r in result]
        assert all(s > 0.4 for s in scores)

    def test_empty_surfaced_returns_empty(self):
        assert extract_surfaced_topics([]) == []

    def test_preserves_fields(self):
        surfaced = [{"title": "Test", "url": "http://x", "matched_collections": ["a"], "score": 0.9}]
        result = extract_surfaced_topics(surfaced)
        assert result[0]["title"] == "Test"
        assert result[0]["url"] == "http://x"
        assert result[0]["matched_collections"] == ["a"]


class TestFindCrossReferenceGaps:
    def test_finds_orphan_essays(self):
        xrefs = json.loads((FIXTURES / "mini-cross-references.json").read_text())
        result = find_cross_reference_gaps(xrefs)
        filenames = [r["filename"] for r in result]
        assert "2026-02-12-game-case-study.md" in filenames
        assert "2026-02-13-dependency-graph.md" in filenames
        assert "2026-02-10-how-we-orchestrate.md" not in filenames

    def test_no_orphans_returns_empty(self):
        xrefs = {
            "entries": {
                "a.md": {"title": "A", "related_repos": ["org/repo"]},
            }
        }
        assert find_cross_reference_gaps(xrefs) == []

    def test_empty_entries_returns_empty(self):
        assert find_cross_reference_gaps({}) == []


class TestGenerateSuggestions:
    def test_combines_all_sources(self):
        underused = [{"tag": "game-design", "current_count": 1}]
        underserved = [{"category": "retrospective", "current_count": 2, "typical_count": 4, "deficit": 2}]
        surfaced = [{"title": "Article", "url": "http://x", "matched_collections": ["a"], "score": 0.8}]
        orphans = [{"filename": "orphan.md", "title": "Orphan Essay"}]

        result = generate_suggestions(underused, underserved, surfaced, orphans)
        types = [s["type"] for s in result]
        assert "tag-gap" in types
        assert "category-gap" in types
        assert "surfaced-article" in types
        assert "cross-ref-gap" in types

    def test_all_suggestions_have_required_fields(self):
        underused = [{"tag": "test-tag", "current_count": 0}]
        result = generate_suggestions(underused, [], [], [])
        for s in result:
            assert "type" in s
            assert "title" in s
            assert "rationale" in s
            assert "suggested_tags" in s
            assert "suggested_category" in s
            assert "priority" in s
            assert "source_data" in s

    def test_empty_inputs_returns_empty(self):
        assert generate_suggestions([], [], [], []) == []

    def test_priority_assignments(self):
        underused = [{"tag": "t", "current_count": 0}]
        underserved = [{"category": "c", "current_count": 0, "typical_count": 5, "deficit": 5}]
        surfaced = [{"title": "A", "url": "u", "matched_collections": [], "score": 0.5}]
        orphans = [{"filename": "f.md", "title": "T"}]
        result = generate_suggestions(underused, underserved, surfaced, orphans)
        priorities = {s["type"]: s["priority"] for s in result}
        assert priorities["tag-gap"] == "medium"
        assert priorities["category-gap"] == "high"
        assert priorities["surfaced-article"] == "high"
        assert priorities["cross-ref-gap"] == "low"


class TestSuggestAll:
    def test_end_to_end_with_fixtures(self, tmp_path):
        # Create minimal tag-governance and category-taxonomy
        tag_gov = tmp_path / "tag-governance.yaml"
        tag_gov.write_text(
            "preferred_tags:\n  - governance\n  - game-design\n  - generative-art\n  - organ-vi\n"
        )
        cat_tax = tmp_path / "category-taxonomy.yaml"
        cat_tax.write_text(
            "categories:\n"
            "  meta-system:\n    typical_count: 3\n"
            "  case-study:\n    typical_count: 5\n"
            "  guide:\n    typical_count: 1\n"
        )

        result = suggest_all(
            str(FIXTURES / "mini-essays-index.json"),
            str(FIXTURES / "mini-cross-references.json"),
            str(tag_gov),
            str(cat_tax),
            str(FIXTURES / "mini-surfaced.json"),
        )

        assert "generated_at" in result
        assert result["pipeline_version"] == "0.2.0"
        assert result["total_suggestions"] == len(result["suggestions"])
        assert result["total_suggestions"] > 0
