"""Topic suggestion engine for ORGAN-V essay-pipeline.

Analyzes the essay corpus against editorial governance to identify under-covered
areas, then combines with reading-observatory surfaced articles to suggest new
essay topics.

CLI: python -m src.topic_suggester --essays-index PATH --xrefs PATH \
     --tag-governance PATH --category-taxonomy PATH --surfaced PATH --output PATH
"""

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

import yaml

PIPELINE_VERSION = "0.2.0"


def find_underused_tags(
    tag_frequency: dict[str, int],
    preferred_tags: list[str],
    threshold: int = 2,
) -> list[dict]:
    """Find preferred tags used fewer than `threshold` times.

    Returns list of dicts with tag name and current count.
    """
    underused = []
    for tag in preferred_tags:
        count = tag_frequency.get(tag, 0)
        if count < threshold:
            underused.append({"tag": tag, "current_count": count})
    return underused


def find_underserved_categories(
    categories: dict[str, int],
    taxonomy: dict,
) -> list[dict]:
    """Find categories where essay count is below typical_count.

    Returns list of dicts with category name, current count, and deficit.
    """
    underserved = []
    cat_defs = taxonomy.get("categories", {})
    for cat_name, cat_spec in cat_defs.items():
        typical = cat_spec.get("typical_count", 0)
        current = categories.get(cat_name, 0)
        if current < typical:
            underserved.append({
                "category": cat_name,
                "current_count": current,
                "typical_count": typical,
                "deficit": typical - current,
            })
    return underserved


def extract_surfaced_topics(surfaced: list[dict]) -> list[dict]:
    """Extract surfaced articles scoring above 0.4.

    Returns list of dicts with title, url, matched_collections, and score.
    """
    results = []
    for item in surfaced:
        score = item.get("score", 0)
        if score > 0.4:
            results.append({
                "title": item.get("title", ""),
                "url": item.get("url", ""),
                "matched_collections": item.get("matched_collections", []),
                "score": score,
            })
    return results


def find_cross_reference_gaps(xrefs: dict) -> list[dict]:
    """Find essays with empty related_repos (orphan essays).

    Returns list of dicts with filename and title.
    """
    entries = xrefs.get("entries", {})
    orphans = []
    for filename, entry in entries.items():
        if not entry.get("related_repos"):
            orphans.append({
                "filename": filename,
                "title": entry.get("title", ""),
            })
    return orphans


def generate_suggestions(
    underused_tags: list[dict],
    underserved_categories: list[dict],
    surfaced_topics: list[dict],
    orphan_essays: list[dict],
) -> list[dict]:
    """Combine all gap analyses into prioritized essay suggestions."""
    suggestions = []

    for gap in underused_tags:
        tag = gap["tag"]
        title_tag = tag.replace("-", " ").title()
        suggestions.append({
            "type": "tag-gap",
            "title": f"Exploring {title_tag}: Untapped Perspectives in the ORGANVM System",
            "rationale": (
                f"Tag '{tag}' appears in only {gap['current_count']} essay(s). "
                f"Preferred tags should have broader coverage."
            ),
            "suggested_tags": [tag],
            "suggested_category": "case-study",
            "priority": "medium",
            "source_data": {"tag": tag, "current_count": gap["current_count"]},
        })

    for gap in underserved_categories:
        cat = gap["category"]
        title_cat = cat.replace("-", " ").title()
        suggestions.append({
            "type": "category-gap",
            "title": f"New {title_cat}: Filling the {title_cat} Gap",
            "rationale": (
                f"Category '{cat}' has {gap['current_count']} essays but "
                f"typical count is {gap['typical_count']} (deficit: {gap['deficit']})."
            ),
            "suggested_tags": [cat],
            "suggested_category": cat,
            "priority": "high",
            "source_data": {
                "category": cat,
                "current_count": gap["current_count"],
                "typical_count": gap["typical_count"],
            },
        })

    for topic in surfaced_topics:
        tags = topic.get("matched_collections", [])
        suggestions.append({
            "type": "surfaced-article",
            "title": f"Response to: {topic['title']}",
            "rationale": (
                f"Surfaced article scored {topic['score']:.2f} against "
                f"collection tags: {', '.join(tags) if tags else 'none'}."
            ),
            "suggested_tags": tags,
            "suggested_category": "meta-system",
            "priority": "high",
            "source_data": {
                "article_title": topic["title"],
                "url": topic["url"],
                "score": topic["score"],
            },
        })

    for orphan in orphan_essays:
        suggestions.append({
            "type": "cross-ref-gap",
            "title": f"Follow-up: Connecting '{orphan['title']}' to the Wider System",
            "rationale": (
                f"Essay '{orphan['filename']}' has no cross-organ references. "
                f"A follow-up could connect it to other ORGANVM organs."
            ),
            "suggested_tags": ["cross-organ", "governance"],
            "suggested_category": "meta-system",
            "priority": "low",
            "source_data": {
                "filename": orphan["filename"],
                "title": orphan["title"],
            },
        })

    return suggestions


def suggest_all(
    essays_index_path: str,
    xrefs_path: str,
    tag_gov_path: str,
    cat_tax_path: str,
    surfaced_path: str,
) -> dict:
    """Run full suggestion pipeline and return result dict."""
    with open(essays_index_path) as f:
        index = json.load(f)

    with open(xrefs_path) as f:
        xrefs = json.load(f)

    with open(tag_gov_path) as f:
        tag_gov = yaml.safe_load(f)

    with open(cat_tax_path) as f:
        cat_tax = yaml.safe_load(f)

    with open(surfaced_path) as f:
        surfaced = json.load(f)

    tag_frequency = index.get("tag_frequency", {})
    preferred_tags = tag_gov.get("preferred_tags", [])
    categories = index.get("categories", {})

    underused = find_underused_tags(tag_frequency, preferred_tags)
    underserved = find_underserved_categories(categories, cat_tax)
    surfaced_topics = extract_surfaced_topics(surfaced)
    orphans = find_cross_reference_gaps(xrefs)

    suggestions = generate_suggestions(underused, underserved, surfaced_topics, orphans)

    return {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "pipeline_version": PIPELINE_VERSION,
        "total_suggestions": len(suggestions),
        "suggestions": suggestions,
    }


def main():
    parser = argparse.ArgumentParser(
        description="Generate essay topic suggestions from corpus analysis"
    )
    parser.add_argument("--essays-index", required=True, help="Path to essays-index.json")
    parser.add_argument("--xrefs", required=True, help="Path to cross-references.json")
    parser.add_argument("--tag-governance", required=True, help="Path to tag-governance.yaml")
    parser.add_argument("--category-taxonomy", required=True, help="Path to category-taxonomy.yaml")
    parser.add_argument("--surfaced", required=True, help="Path to surfaced.json")
    parser.add_argument("--output", required=True, help="Output path for topic-suggestions.json")
    args = parser.parse_args()

    result = suggest_all(
        args.essays_index,
        args.xrefs,
        args.tag_governance,
        args.category_taxonomy,
        args.surfaced,
    )

    out = Path(args.output)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(result, indent=2, ensure_ascii=False) + "\n")

    print(f"Generated {result['total_suggestions']} topic suggestions → {args.output}")
    sys.exit(0)


if __name__ == "__main__":
    main()
