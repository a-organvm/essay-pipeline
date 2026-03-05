"""Tests for the topic suggestion engine."""

import json
from collections import Counter
from pathlib import Path
from unittest.mock import patch

from src.topic_suggester import (
    build_existing_title_keys,
    build_tag_cooccurrence,
    deduplicate_suggestions,
    extract_surfaced_topics,
    find_category_seed_tags,
    find_cross_reference_gaps,
    find_underserved_categories,
    find_underused_tags,
    generate_suggestions,
    main,
    rank_and_limit_suggestions,
    select_companion_tags,
    suggest_all,
    summarize_suggestion_mix,
)

FIXTURES = Path(__file__).parent / "fixtures"


def _fixture_index() -> dict:
    return json.loads((FIXTURES / "mini-essays-index.json").read_text())


def _fixture_xrefs() -> dict:
    return json.loads((FIXTURES / "mini-cross-references.json").read_text())


def _fixture_surfaced() -> list[dict]:
    return json.loads((FIXTURES / "mini-surfaced.json").read_text())


class TestTopicSuggesterMain:
    @patch("src.topic_suggester.suggest_all")
    @patch("sys.exit")
    def test_main_runs(self, mock_exit, mock_suggest, tmp_path):
        mock_suggest.return_value = {"total_suggestions": 5}
        output = tmp_path / "suggestions.json"
        with patch(
            "sys.argv",
            [
                "prog",
                "--essays-index",
                "idx",
                "--xrefs",
                "xr",
                "--tag-governance",
                "tag",
                "--category-taxonomy",
                "cat",
                "--surfaced",
                "surf",
                "--output",
                str(output),
            ],
        ):
            main()

        assert output.exists()
        mock_exit.assert_called_with(0)


class TestFindUnderusedTags:
    def test_finds_tags_below_threshold_with_gap_details(self):
        tag_freq = {"governance": 10, "game-design": 1, "art": 0}
        preferred = ["governance", "game-design", "art", "missing-tag"]
        result = find_underused_tags(tag_freq, preferred, threshold=2)
        tags = [r["tag"] for r in result]

        assert "game-design" in tags
        assert "art" in tags
        assert "missing-tag" in tags
        assert "governance" not in tags

        missing = next(r for r in result if r["tag"] == "missing-tag")
        assert missing["current_count"] == 0
        assert missing["deficit"] == 2
        assert missing["target_count"] == 2

    def test_empty_preferred_returns_empty(self):
        assert find_underused_tags({"governance": 5}, [], threshold=2) == []

    def test_custom_threshold_applies(self):
        result = find_underused_tags({"governance": 3}, ["governance"], threshold=5)
        assert result[0]["deficit"] == 2


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
        assert result[0]["deficit"] >= 1

    def test_ignores_invalid_typical_counts(self):
        categories = {"meta-system": 2}
        taxonomy = {
            "categories": {
                "meta-system": {"typical_count": 0},
                "case-study": {"typical_count": "3"},
            }
        }
        result = find_underserved_categories(categories, taxonomy)
        assert [item["category"] for item in result] == ["case-study"]


class TestExtractSurfacedTopics:
    def test_filters_by_score_with_custom_threshold(self):
        surfaced = _fixture_surfaced()
        result = extract_surfaced_topics(surfaced, threshold=0.56)
        assert len(result) == 1
        assert result[0]["title"] == "Why Multi-Repo Architectures Fail"

    def test_deduplicates_by_url(self):
        surfaced = [
            {
                "title": "A",
                "url": "https://example.com/a",
                "matched_collections": ["governance"],
                "score": 0.9,
            },
            {
                "title": "A duplicate",
                "url": "https://example.com/a",
                "matched_collections": ["methodology"],
                "score": 0.8,
            },
        ]
        result = extract_surfaced_topics(surfaced, threshold=0.4)
        assert len(result) == 1
        assert result[0]["matched_collections"] == ["governance"]

    def test_preserves_optional_fields(self):
        surfaced = [
            {
                "title": "Test",
                "url": "https://x",
                "matched_collections": ["A B", "orchestration"],
                "score": 0.9,
                "source_feed": "digest",
                "surfaced_at": "2026-02-23T14:00:00+00:00",
            }
        ]
        result = extract_surfaced_topics(surfaced, threshold=0.4)
        assert result[0]["title"] == "Test"
        assert result[0]["url"] == "https://x"
        assert result[0]["matched_collections"] == ["a-b", "orchestration"]
        assert result[0]["source_feed"] == "digest"
        assert result[0]["surfaced_at"] == "2026-02-23T14:00:00+00:00"

    def test_supports_relevance_score_and_surfaced_date_aliases(self):
        surfaced = [
            {
                "title": "Alias Shape",
                "url": "https://alias.example/item",
                "matched_collections": ["systems-thinking"],
                "relevance_score": 0.91,
                "feed_title": "Alias Feed",
                "surfaced_date": "2026-03-04",
            }
        ]
        result = extract_surfaced_topics(surfaced, threshold=0.4)
        assert len(result) == 1
        assert result[0]["score"] == 0.91
        assert result[0]["source_feed"] == "Alias Feed"
        assert result[0]["surfaced_at"] == "2026-03-04T00:00:00+00:00"


class TestCooccurrenceAndTagHelpers:
    def test_build_tag_cooccurrence(self):
        essays = _fixture_index()["essays"]
        co = build_tag_cooccurrence(essays)
        assert co["governance"]["methodology"] >= 1
        assert co["methodology"]["governance"] >= 1

    def test_select_companion_tags(self):
        co = {
            "governance": {"methodology": 4, "infrastructure": 2},
            "methodology": {"governance": 4},
        }
        companions = select_companion_tags(
            ["governance"],
            co,
            max_tags=2,
            blocked_tags={"infrastructure"},
        )
        assert companions == ["methodology"]

    def test_find_category_seed_tags(self):
        essays = _fixture_index()["essays"]
        tags = find_category_seed_tags(essays, "meta-system", max_tags=2)
        assert len(tags) == 2
        assert "governance" in tags


class TestFindCrossReferenceGaps:
    def test_finds_orphan_essays_and_enriches_fields(self):
        xrefs = _fixture_xrefs()
        index = _fixture_index()
        lookup = {essay["filename"]: essay for essay in index["essays"]}
        result = find_cross_reference_gaps(xrefs, essay_lookup=lookup)
        filenames = [r["filename"] for r in result]

        assert "2026-02-12-game-case-study.md" in filenames
        assert "2026-02-13-dependency-graph.md" in filenames
        assert "2026-02-10-how-we-orchestrate.md" not in filenames
        assert all("category" in item for item in result)
        assert all("tags" in item for item in result)

    def test_empty_entries_returns_empty(self):
        assert find_cross_reference_gaps({}) == []


class TestGenerateSuggestions:
    def test_combines_all_sources_with_enriched_fields(self):
        underused = [{"tag": "game-design", "current_count": 1, "target_count": 2}]
        underserved = [
            {
                "category": "retrospective",
                "current_count": 2,
                "typical_count": 4,
                "deficit": 2,
            }
        ]
        surfaced = [
            {
                "title": "Article",
                "url": "https://x",
                "matched_collections": ["governance"],
                "score": 0.8,
                "surfaced_at": "2026-02-23T10:00:00+00:00",
            }
        ]
        orphans = [
            {"filename": "orphan.md", "title": "Orphan Essay", "category": "meta-system"}
        ]
        result = generate_suggestions(
            underused,
            underserved,
            surfaced,
            orphans,
            essay_index=_fixture_index(),
            taxonomy={"categories": {"retrospective": {}, "meta-system": {}}},
        )
        types = {s["type"] for s in result}

        assert "tag-gap" in types
        assert "category-gap" in types
        assert "surfaced-article" in types
        assert "cross-ref-gap" in types
        for suggestion in result:
            assert suggestion["id"]
            assert suggestion["focus_area"]
            assert suggestion["estimated_effort"] in {"small", "medium", "large"}
            assert suggestion["priority"] in {"low", "medium", "high", "critical"}
            assert 0.0 <= suggestion["score"] <= 1.0

    def test_empty_inputs_returns_empty(self):
        assert generate_suggestions([], [], [], []) == []


class TestSuggestionPostProcessing:
    def test_build_existing_title_keys(self):
        keys = build_existing_title_keys(_fixture_index(), _fixture_xrefs())
        assert "how we orchestrate eight organs" in keys
        assert "bootstrap to scale" in keys

    def test_deduplicate_suggestions_uses_titles_and_urls(self):
        suggestions = [
            {
                "title": "Response to: A",
                "source_data": {"url": "https://example.com/a"},
                "type": "surfaced-article",
            },
            {
                "title": "Response to: A",
                "source_data": {"url": "https://example.com/a"},
                "type": "surfaced-article",
            },
            {
                "title": "Unique",
                "source_data": {"url": "https://example.com/u"},
                "type": "tag-gap",
            },
        ]

        deduped, removed = deduplicate_suggestions(
            suggestions,
            existing_titles={"existing title"},
        )
        assert len(deduped) == 2
        assert removed == 1

    def test_rank_and_limit_suggestions_applies_caps_and_ranks(self):
        suggestions = [
            {"title": "A", "type": "tag-gap", "priority": "high", "score": 0.7},
            {"title": "B", "type": "tag-gap", "priority": "high", "score": 0.8},
            {"title": "C", "type": "category-gap", "priority": "critical", "score": 0.9},
            {"title": "D", "type": "cross-ref-gap", "priority": "low", "score": 0.2},
        ]
        ranked = rank_and_limit_suggestions(
            suggestions,
            max_suggestions=3,
            per_type_limit=1,
        )
        assert [item["rank"] for item in ranked] == [1, 2, 3]
        assert len(ranked) == 3
        counts = Counter(item["type"] for item in ranked)
        assert counts["tag-gap"] == 1

    def test_summarize_suggestion_mix(self):
        suggestions = [
            {"type": "tag-gap", "priority": "high", "score": 0.8, "suggested_tags": ["a"]},
            {
                "type": "category-gap",
                "priority": "medium",
                "score": 0.6,
                "suggested_tags": ["b", "a"],
            },
        ]
        summary = summarize_suggestion_mix(suggestions)
        assert summary["by_type"]["tag-gap"] == 1
        assert summary["by_priority"]["high"] == 1
        assert summary["average_score"] == 0.7
        assert summary["top_tags"][0] == "a"


class TestSuggestAll:
    def test_end_to_end_with_fixtures_and_limits(self, tmp_path):
        tag_gov = tmp_path / "tag-governance.yaml"
        tag_gov.write_text(
            "preferred_tags:\n"
            "  - governance\n"
            "  - game-design\n"
            "  - generative-art\n"
            "  - organ-vi\n"
        )
        cat_tax = tmp_path / "category-taxonomy.yaml"
        cat_tax.write_text(
            "categories:\n"
            "  meta-system:\n    typical_count: 3\n"
            "  case-study:\n    typical_count: 5\n"
            "  guide:\n    typical_count: 1\n"
            "  retrospective:\n    typical_count: 2\n"
        )

        result = suggest_all(
            str(FIXTURES / "mini-essays-index.json"),
            str(FIXTURES / "mini-cross-references.json"),
            str(tag_gov),
            str(cat_tax),
            str(FIXTURES / "mini-surfaced.json"),
            tag_threshold=2,
            surfaced_threshold=0.4,
            max_suggestions=5,
            per_type_limit=2,
        )

        assert "generated_at" in result
        assert result["pipeline_version"] == "0.4.0"
        assert result["total_suggestions"] == len(result["suggestions"])
        assert result["total_suggestions"] <= 5
        assert result["configuration"]["tag_threshold"] == 2
        assert result["configuration"]["per_type_limit"] == 2
        assert result["diagnostics"]["raw_suggestion_count"] >= result["total_suggestions"]
        assert "mix" in result["diagnostics"]

        per_type = Counter(s["type"] for s in result["suggestions"])
        assert all(count <= 2 for count in per_type.values())

        ranks = [s["rank"] for s in result["suggestions"]]
        assert ranks == list(range(1, len(ranks) + 1))

    def test_end_to_end_accepts_reading_observatory_shape(self, tmp_path):
        tag_gov = tmp_path / "tag-governance.yaml"
        tag_gov.write_text("preferred_tags:\n  - governance\n  - methodology\n")
        cat_tax = tmp_path / "category-taxonomy.yaml"
        cat_tax.write_text("categories:\n  meta-system:\n    typical_count: 2\n")

        result = suggest_all(
            str(FIXTURES / "mini-essays-index.json"),
            str(FIXTURES / "mini-cross-references.json"),
            str(tag_gov),
            str(cat_tax),
            str(FIXTURES / "mini-surfaced-reading-observatory.json"),
            surfaced_threshold=0.4,
        )

        assert result["diagnostics"]["surfaced_topic_count"] == 1
        assert "surfaced-article" in result["diagnostics"]["mix"]["by_type"]
