"""Tests for the essay drafter module."""

import json
import os
from pathlib import Path
from unittest.mock import MagicMock, patch

import yaml

from src.essay_drafter import (
    _count_body_words,
    _derive_slug,
    _extract_markdown,
    build_system_prompt,
    build_user_prompt,
    draft_essay,
    repair_frontmatter,
    validate_draft,
)

FIXTURES = Path(__file__).parent / "fixtures"
SCHEMA_PATH = str(
    Path(__file__).parent.parent.parent
    / "editorial-standards"
    / "schemas"
    / "frontmatter-schema.yaml"
)
RUBRIC_PATH = str(
    Path(__file__).parent.parent.parent
    / "editorial-standards"
    / "schemas"
    / "quality-rubric.yaml"
)
TAG_GOV_PATH = str(
    Path(__file__).parent.parent.parent
    / "editorial-standards"
    / "schemas"
    / "tag-governance.yaml"
)
CAT_TAX_PATH = str(
    Path(__file__).parent.parent.parent
    / "editorial-standards"
    / "schemas"
    / "category-taxonomy.yaml"
)
TEMPLATE_DIR = str(
    Path(__file__).parent.parent.parent
    / "editorial-standards"
    / "templates"
)


def _make_suggestion(
    category: str = "meta-system",
    title: str = "Test Topic",
) -> dict:
    return {
        "type": "tag-gap",
        "title": title,
        "rationale": "Testing the essay drafter",
        "suggested_tags": ["governance", "meta-system"],
        "suggested_category": category,
        "priority": "medium",
        "source_data": {"tag": "governance", "current_count": 1},
    }


VALID_DRAFT = """---
layout: essay
title: "Understanding Recursive Governance in Multi-Organ Systems"
author: "@4444J99"
date: "2026-02-27"
tags:
  - governance
  - meta-system
  - architecture
category: "meta-system"
excerpt: "This essay explores how recursive governance patterns emerge in complex multi-organ creative-institutional systems, examining the ORGANVM model as a case study."
portfolio_relevance: "HIGH"
related_repos:
  - organvm-v-logos/essay-pipeline
  - organvm-v-logos/editorial-standards
reading_time: "2 min"
word_count: 576
references: []
---

# Understanding Recursive Governance in Multi-Organ Systems

## The Problem / Context

When building systems that span multiple organs — each with independent repositories, CI pipelines, and deployment workflows — governance becomes a recursive challenge. The ORGANVM system, with its eight organs and over 100 repositories, exemplifies this tension between autonomy and coherence.

## How the System Handles It

The ORGANVM system uses seed contracts and a registry as its primary governance mechanism. Each repository declares its organ membership, tier, and inter-repo dependencies through a seed.yaml file. The central registry (registry-v2.json) aggregates these declarations into a system-wide dependency graph.

This approach inverts the traditional top-down governance model. Instead of a central authority dictating rules, each repository self-declares its position in the system. The registry validates these declarations for consistency.

## What This Reveals About the Meta-System

The recursive nature of this governance model reveals several insights about building systems of systems. First, governance must be encoded in the same artifacts it governs — YAML files validated by the same CI pipelines that validate code.

Second, the promotion state machine (LOCAL through GRADUATED to ARCHIVED) creates natural checkpoints where human review intersects with automated validation. This prevents premature promotion while enabling autonomous operation within tiers.

## What Doesn't Work Yet

The current system has known limitations. Cross-organ dependency validation relies on CI being configured in every repository, which is not yet the case. Some repositories have stale seed.yaml files that don't reflect current usage.

The human review bottleneck at promotion gates can cause backlogs when multiple repositories reach CANDIDATE status simultaneously.

## Implications

As the system grows beyond 100 repositories, the governance model will need to evolve. Automated dependency analysis could supplement manual seed.yaml declarations. The promotion pipeline could incorporate automated quality metrics from the analytics engine.

The recursive governance pattern demonstrated here has broader applications beyond creative-institutional systems. Any multi-team organization managing interdependent repositories faces similar challenges of balancing autonomy with coherence. The key insight is that governance metadata should live alongside the code it governs, not in a separate management layer.

## Practical Implementation Details

The implementation of recursive governance requires careful attention to tooling. The essay-pipeline repository contains the validator that enforces frontmatter schema compliance. Every pull request to the public-process site triggers this validator through GitHub Actions. The validator reads the schema from editorial-standards, which itself is version-controlled and requires an ADR for any changes.

This creates a governance chain where the rules governing content are themselves governed by the same processes they enforce. The tag-governance.yaml file defines acceptable tags. The category-taxonomy.yaml file defines the five canonical categories. Both files are consumed by the validator and by the topic-suggester, which analyzes coverage gaps across the corpus.

The analytics-engine collects engagement metrics from GoatCounter and GitHub activity data, feeding into the sprint-narrator which produces weekly summaries. These summaries inform the topic-suggester about which areas of the system are receiving attention and which are neglected. The reading-observatory aggregates external articles and scores their relevance against the existing essay corpus, surfacing potential topics for new essays.

This feedback loop means the system becomes self-aware of its own coverage patterns. When a particular organ receives heavy development activity but has few corresponding essays, the topic-suggester flags this gap. When external articles align closely with ongoing work, the system surfaces them as potential response pieces.

The autonomous living system connects all these components into a continuous pipeline that operates without human intervention while maintaining human oversight through the 48-hour review window on auto-generated pull requests.
"""


class TestBuildSystemPrompt:
    def test_contains_author(self):
        schema = yaml.safe_load(open(SCHEMA_PATH))
        rubric = yaml.safe_load(open(RUBRIC_PATH))
        tag_gov = yaml.safe_load(open(TAG_GOV_PATH))
        cat_tax = yaml.safe_load(open(CAT_TAX_PATH))
        template = "## Template Section\n\nSome guidance."

        prompt = build_system_prompt(
            template, schema, rubric, tag_gov, cat_tax, []
        )
        assert "@4444J99" in prompt
        assert "meta-system" in prompt
        assert "case-study" in prompt
        assert "governance" in prompt

    def test_includes_existing_titles(self):
        schema = yaml.safe_load(open(SCHEMA_PATH))
        rubric = yaml.safe_load(open(RUBRIC_PATH))
        tag_gov = yaml.safe_load(open(TAG_GOV_PATH))
        cat_tax = yaml.safe_load(open(CAT_TAX_PATH))

        titles = ["Existing Essay One", "Existing Essay Two"]
        prompt = build_system_prompt(
            "template", schema, rubric, tag_gov, cat_tax, titles
        )
        assert "Existing Essay One" in prompt
        assert "Existing Essay Two" in prompt

    def test_includes_quality_criteria(self):
        schema = yaml.safe_load(open(SCHEMA_PATH))
        rubric = yaml.safe_load(open(RUBRIC_PATH))
        tag_gov = yaml.safe_load(open(TAG_GOV_PATH))
        cat_tax = yaml.safe_load(open(CAT_TAX_PATH))

        prompt = build_system_prompt(
            "template", schema, rubric, tag_gov, cat_tax, []
        )
        assert "substance" in prompt
        assert "honesty" in prompt


class TestBuildUserPrompt:
    def test_basic_prompt(self):
        suggestion = _make_suggestion()
        prompt = build_user_prompt(suggestion)
        assert "tag-gap" in prompt
        assert "Test Topic" in prompt
        assert "governance" in prompt

    def test_with_context(self):
        suggestion = _make_suggestion()
        context = {
            "sprint_narrative": "This sprint saw 50 commits across 3 organs.",
            "metrics_summary": "Page views up 20%.",
        }
        prompt = build_user_prompt(suggestion, context)
        assert "50 commits" in prompt
        assert "Page views" in prompt

    def test_includes_todays_date(self):
        from datetime import date
        suggestion = _make_suggestion()
        prompt = build_user_prompt(suggestion)
        assert date.today().isoformat() in prompt


class TestValidateDraft:
    def test_valid_draft_passes(self):
        valid, errors = validate_draft(VALID_DRAFT, SCHEMA_PATH)
        assert valid is True
        assert errors == []

    def test_missing_field_fails(self):
        bad_draft = """---
layout: essay
title: "A test"
---

# Content
"""
        valid, errors = validate_draft(bad_draft, SCHEMA_PATH)
        assert valid is False
        assert len(errors) > 0

    def test_no_frontmatter_fails(self):
        valid, errors = validate_draft("Just plain text", SCHEMA_PATH)
        assert valid is False


class TestCountBodyWords:
    def test_counts_words(self):
        text = """---
title: test
---

This is a test body with exactly nine words here.
"""
        count = _count_body_words(text)
        assert count == 10  # "This is a test body with exactly nine words here"

    def test_no_frontmatter(self):
        assert _count_body_words("no frontmatter") == 0

    def test_strips_markdown(self):
        text = """---
title: test
---

## Heading

**bold** and *italic* and `code` and [link](url).
"""
        count = _count_body_words(text)
        assert count > 0
        # Should not count markdown symbols as words


class TestRepairFrontmatter:
    def test_fixes_date_format(self):
        draft = """---
layout: essay
title: "Test Essay Title for Repair Testing"
author: "@4444J99"
date: "2026-02-27T10:30:00"
tags: [governance, meta-system]
category: "meta-system"
excerpt: "This is a test excerpt that is long enough to pass the minimum length requirement for validation."
portfolio_relevance: "HIGH"
related_repos: [organvm-v-logos/essay-pipeline]
reading_time: "5 min"
word_count: 100
---

""" + "word " * 100
        schema = yaml.safe_load(open(SCHEMA_PATH))
        repaired = repair_frontmatter(draft, ["date"], schema)
        assert "2026-02-27" in repaired
        # Should not have the time component
        assert "T10:30:00" not in repaired.split("---")[1]

    def test_fixes_author_prefix(self):
        draft = """---
layout: essay
title: "Test Essay Title for Author Prefix"
author: "4444J99"
date: "2026-02-27"
tags: [governance, meta-system]
category: "meta-system"
excerpt: "This is a test excerpt that is long enough to pass the minimum length requirement for validation."
portfolio_relevance: "HIGH"
related_repos: [organvm-v-logos/essay-pipeline]
reading_time: "5 min"
word_count: 500
---

Body text here.
"""
        schema = yaml.safe_load(open(SCHEMA_PATH))
        repaired = repair_frontmatter(draft, ["author"], schema)
        # Parse the repaired frontmatter
        parts = repaired.split("---", 2)
        fm = yaml.safe_load(parts[1])
        assert fm["author"] == "@4444J99"

    def test_fixes_tag_case(self):
        draft = """---
layout: essay
title: "Test Essay Title for Tag Case Fix"
author: "@4444J99"
date: "2026-02-27"
tags: [Governance, META_SYSTEM]
category: "meta-system"
excerpt: "This is a test excerpt that is long enough to pass the minimum length requirement for validation."
portfolio_relevance: "HIGH"
related_repos: [organvm-v-logos/essay-pipeline]
reading_time: "5 min"
word_count: 500
---

Body text here.
"""
        schema = yaml.safe_load(open(SCHEMA_PATH))
        repaired = repair_frontmatter(draft, ["tags"], schema)
        parts = repaired.split("---", 2)
        fm = yaml.safe_load(parts[1])
        assert fm["tags"] == ["governance", "meta-system"]

    def test_fixes_word_count(self):
        body_words = "word " * 200
        draft = f"""---
layout: essay
title: "Test Essay for Word Count"
author: "@4444J99"
date: "2026-02-27"
tags: [governance, meta-system]
category: "meta-system"
excerpt: "This is a test excerpt that is long enough to pass the minimum length requirement for validation."
portfolio_relevance: "HIGH"
related_repos: [organvm-v-logos/essay-pipeline]
reading_time: "5 min"
word_count: 999
---

{body_words}
"""
        schema = yaml.safe_load(open(SCHEMA_PATH))
        repaired = repair_frontmatter(draft, ["word_count"], schema)
        parts = repaired.split("---", 2)
        fm = yaml.safe_load(parts[1])
        assert fm["word_count"] == 200

    def test_no_frontmatter_returns_unchanged(self):
        text = "No frontmatter here"
        schema = yaml.safe_load(open(SCHEMA_PATH))
        assert repair_frontmatter(text, [], schema) == text


class TestExtractMarkdown:
    def test_raw_markdown(self):
        text = """---
title: test
---

Body content
"""
        assert _extract_markdown(text).startswith("---")

    def test_fenced_markdown(self):
        text = """Here is the essay:

```markdown
---
title: test
---

Body content
```

That's it.
"""
        result = _extract_markdown(text)
        assert result.startswith("---")
        assert "```" not in result

    def test_fenced_md(self):
        text = """```md
---
title: test
---

Body
```"""
        result = _extract_markdown(text)
        assert result.startswith("---")

    def test_prefixed_text(self):
        text = """Sure, here's your essay:

---
title: test
---

Body content here.
"""
        result = _extract_markdown(text)
        assert result.startswith("---")


class TestDeriveSlug:
    def test_basic_slug(self):
        text = """---
title: "My Great Essay Title"
---

Body
"""
        assert _derive_slug(text) == "my-great-essay-title"

    def test_special_chars_removed(self):
        text = """---
title: "What's Next? A Look at the Future!"
---

Body
"""
        slug = _derive_slug(text)
        assert "?" not in slug
        assert "!" not in slug
        assert "'" not in slug

    def test_long_title_truncated(self):
        text = """---
title: "This Is an Extremely Long Title That Goes On and On and On and Really Should Be Truncated to Something Reasonable"
---

Body
"""
        slug = _derive_slug(text)
        assert len(slug) <= 60

    def test_no_frontmatter(self):
        assert _derive_slug("no frontmatter") == "untitled"


class TestDraftEssayIntegration:
    """Integration tests using a mocked LLM client."""

    @patch("src.essay_drafter.create_client")
    def test_successful_draft(self, mock_create):
        from src.llm_client import LLMResponse

        mock_client = MagicMock()
        mock_client.generate.return_value = LLMResponse(
            text=VALID_DRAFT,
            model="test-model",
            provider="test",
            input_tokens=100,
            output_tokens=500,
        )
        mock_create.return_value = mock_client

        # Skip if editorial-standards not available
        if not Path(SCHEMA_PATH).exists():
            return

        suggestion = _make_suggestion()
        import tempfile
        with tempfile.TemporaryDirectory() as tmpdir:
            posts_dir = Path(tmpdir) / "posts"
            posts_dir.mkdir()
            output_dir = Path(tmpdir) / "output"

            result = draft_essay(
                suggestion=suggestion,
                template_dir=TEMPLATE_DIR,
                schema_path=SCHEMA_PATH,
                rubric_path=RUBRIC_PATH,
                tag_governance_path=TAG_GOV_PATH,
                category_taxonomy_path=CAT_TAX_PATH,
                posts_dir=str(posts_dir),
                output_dir=str(output_dir),
            )

            assert result["valid"] is True
            assert Path(result["output_path"]).exists()
            assert result["llm"]["provider"] == "test"

    @patch("src.essay_drafter.create_client")
    def test_repair_fixes_draft(self, mock_create):
        from src.llm_client import LLMResponse

        # Draft with fixable issues: wrong date format, bad tag case
        fixable_draft = VALID_DRAFT.replace(
            'date: "2026-02-27"',
            'date: "2026-02-27T10:00:00"',
        )

        mock_client = MagicMock()
        mock_client.generate.return_value = LLMResponse(
            text=fixable_draft,
            model="test-model",
            provider="test",
            input_tokens=100,
            output_tokens=500,
        )
        mock_create.return_value = mock_client

        if not Path(SCHEMA_PATH).exists():
            return

        suggestion = _make_suggestion()
        import tempfile
        with tempfile.TemporaryDirectory() as tmpdir:
            result = draft_essay(
                suggestion=suggestion,
                template_dir=TEMPLATE_DIR,
                schema_path=SCHEMA_PATH,
                rubric_path=RUBRIC_PATH,
                tag_governance_path=TAG_GOV_PATH,
                category_taxonomy_path=CAT_TAX_PATH,
                posts_dir=str(Path(tmpdir) / "posts"),
                output_dir=str(Path(tmpdir) / "output"),
            )

            assert result["valid"] is True
            assert result["repaired"] is True
