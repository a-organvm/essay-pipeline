"""Topic suggestion engine for ORGAN-V essay-pipeline.

Analyzes corpus coverage gaps, surfaced research, and cross-reference holes to
generate deeply structured topic suggestions for essay drafting.

CLI:
  python -m src.topic_suggester \
    --essays-index PATH \
    --xrefs PATH \
    --tag-governance PATH \
    --category-taxonomy PATH \
    --surfaced PATH \
    --output PATH \
    --tag-threshold 2 \
    --surfaced-threshold 0.4 \
    --max-suggestions 24 \
    --per-type-limit 8
"""

import argparse
import json
import re
import sys
from collections import Counter, defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path

import yaml

PIPELINE_VERSION = "0.4.0"
_DEFAULT_TAG_THRESHOLD = 2
_DEFAULT_SURFACED_THRESHOLD = 0.4
_DEFAULT_MAX_SUGGESTIONS = 24
_DEFAULT_PER_TYPE_LIMIT = 8

_PRIORITY_ORDER = {
    "critical": 0,
    "high": 1,
    "medium": 2,
    "low": 3,
}


def _normalize_tag(tag: str) -> str:
    """Normalize tag text into lowercase hyphenated form."""
    clean = re.sub(r"[\s_]+", "-", str(tag).strip().lower())
    clean = re.sub(r"[^a-z0-9-]", "", clean)
    clean = re.sub(r"-{2,}", "-", clean).strip("-")
    return clean


def _normalize_tag_list(tags: list[str] | None) -> list[str]:
    """Normalize, deduplicate, and preserve order for tag lists."""
    if not isinstance(tags, list):
        return []

    result = []
    seen: set[str] = set()
    for tag in tags:
        if not isinstance(tag, str):
            continue
        clean = _normalize_tag(tag)
        if clean and clean not in seen:
            seen.add(clean)
            result.append(clean)
    return result


def _normalize_title_key(title: str) -> str:
    """Build a canonical title key for deduplication."""
    clean = re.sub(r"[^a-z0-9]+", " ", str(title).strip().lower())
    return re.sub(r"\s+", " ", clean).strip()


def _slugify(value: str) -> str:
    """Create a compact slug for stable suggestion ids."""
    slug = re.sub(r"[^a-z0-9]+", "-", str(value).strip().lower()).strip("-")
    return slug[:80] or "untitled"


def _parse_score(value) -> float:
    """Parse and clamp a score to [0.0, 1.0]."""
    try:
        score = float(value)
    except (TypeError, ValueError):
        return 0.0
    return max(0.0, min(1.0, score))


def _parse_iso_datetime(value: str) -> datetime | None:
    """Parse ISO timestamp values, including Z suffix."""
    if not value:
        return None
    raw = value.strip()
    if not raw:
        return None
    if raw.endswith("Z"):
        raw = f"{raw[:-1]}+00:00"
    try:
        parsed = datetime.fromisoformat(raw)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _recency_bonus(surfaced_at: str) -> float:
    """Compute a small score bonus for recent surfaced topics."""
    parsed = _parse_iso_datetime(surfaced_at)
    if not parsed:
        return 0.0
    age_days = max(0, (datetime.now(timezone.utc) - parsed).days)
    if age_days <= 3:
        return 0.08
    if age_days <= 7:
        return 0.05
    if age_days <= 14:
        return 0.03
    return 0.0


def _clip_score(score: float) -> float:
    """Clamp and round scores to 3 decimal places."""
    return round(max(0.0, min(1.0, score)), 3)


def _priority_from_score(score: float) -> str:
    """Convert numeric score to a priority bucket."""
    if score >= 0.82:
        return "critical"
    if score >= 0.67:
        return "high"
    if score >= 0.5:
        return "medium"
    return "low"


def _effort_from_type_and_score(suggestion_type: str, score: float) -> str:
    """Estimate authoring effort for the proposed essay."""
    if suggestion_type == "category-gap" and score >= 0.75:
        return "large"
    if suggestion_type in {"surfaced-article", "tag-gap"} and score >= 0.65:
        return "medium"
    if suggestion_type == "cross-ref-gap":
        return "small"
    if score >= 0.78:
        return "large"
    if score >= 0.55:
        return "medium"
    return "small"


def _priority_reason(suggestion_type: str, score: float, source_data: dict) -> str:
    """Create a short machine-generated reason for assigned priority."""
    if suggestion_type == "category-gap":
        deficit = source_data.get("deficit", 0)
        typical = source_data.get("typical_count", 0)
        return (
            f"Category coverage deficit {deficit}/{typical} and score {score:.3f} "
            f"indicate meaningful coverage risk."
        )
    if suggestion_type == "tag-gap":
        deficit = source_data.get("deficit", 0)
        target = source_data.get("target_count", 0)
        return (
            f"Preferred tag deficit {deficit}/{target} and score {score:.3f} "
            f"indicate topical underrepresentation."
        )
    if suggestion_type == "surfaced-article":
        surfaced_score = _parse_score(source_data.get("score", 0.0))
        return (
            f"External surfaced score {surfaced_score:.3f} with blended score {score:.3f} "
            f"indicates high discourse relevance."
        )
    filename = source_data.get("filename", "unknown")
    return (
        f"Orphan essay '{filename}' lacks cross-organ linkage; blended score {score:.3f} "
        f"indicates follow-up value."
    )


def build_essay_lookup(index: dict) -> dict[str, dict]:
    """Build quick lookup for essays keyed by filename."""
    lookup: dict[str, dict] = {}
    for essay in index.get("essays", []):
        if not isinstance(essay, dict):
            continue
        filename = str(essay.get("filename", "")).strip()
        if not filename:
            continue
        lookup[filename] = {
            "title": str(essay.get("title", "")).strip(),
            "category": str(essay.get("category", "")).strip(),
            "tags": _normalize_tag_list(essay.get("tags", [])),
        }
    return lookup


def build_existing_title_keys(index: dict, xrefs: dict) -> set[str]:
    """Build canonical title key set for duplicate prevention."""
    keys: set[str] = set()

    for essay in index.get("essays", []):
        if not isinstance(essay, dict):
            continue
        key = _normalize_title_key(str(essay.get("title", "")))
        if key:
            keys.add(key)

    entries = xrefs.get("entries", {})
    if isinstance(entries, dict):
        for entry in entries.values():
            if not isinstance(entry, dict):
                continue
            key = _normalize_title_key(str(entry.get("title", "")))
            if key:
                keys.add(key)

    return keys


def build_tag_cooccurrence(essays: list[dict]) -> dict[str, dict[str, int]]:
    """Build tag co-occurrence frequency map from existing essays."""
    cooccurrence: dict[str, Counter] = defaultdict(Counter)

    for essay in essays:
        if not isinstance(essay, dict):
            continue
        tags = _normalize_tag_list(essay.get("tags", []))
        unique_tags = list(dict.fromkeys(tags))
        for tag in unique_tags:
            for other in unique_tags:
                if other == tag:
                    continue
                cooccurrence[tag][other] += 1

    return {tag: dict(counter) for tag, counter in cooccurrence.items()}


def select_companion_tags(
    seed_tags: list[str],
    cooccurrence: dict[str, dict[str, int]],
    max_tags: int = 2,
    blocked_tags: set[str] | None = None,
) -> list[str]:
    """Choose frequently co-occurring companion tags."""
    if max_tags <= 0:
        return []

    blocked = {_normalize_tag(tag) for tag in (blocked_tags or set())}
    seeds = [_normalize_tag(tag) for tag in seed_tags]
    seeds = [tag for tag in seeds if tag]
    if not seeds:
        return []

    scores: Counter = Counter()
    for seed in seeds:
        related = cooccurrence.get(seed, {})
        if not isinstance(related, dict):
            continue
        for raw_tag, raw_count in related.items():
            tag = _normalize_tag(raw_tag)
            if not tag or tag in blocked or tag in seeds:
                continue
            try:
                count = int(raw_count)
            except (TypeError, ValueError):
                continue
            if count > 0:
                scores[tag] += count

    ranked = sorted(scores.items(), key=lambda item: (-item[1], item[0]))
    return [tag for tag, _ in ranked[:max_tags]]


def find_category_seed_tags(
    essays: list[dict],
    category: str,
    max_tags: int = 2,
) -> list[str]:
    """Find the most common tags already used in a category."""
    if max_tags <= 0:
        return []

    target = str(category).strip()
    if not target:
        return []

    counter: Counter = Counter()
    for essay in essays:
        if not isinstance(essay, dict):
            continue
        if str(essay.get("category", "")).strip() != target:
            continue
        for tag in _normalize_tag_list(essay.get("tags", [])):
            counter[tag] += 1

    ranked = sorted(counter.items(), key=lambda item: (-item[1], item[0]))
    return [tag for tag, _ in ranked[:max_tags]]


def find_underused_tags(
    tag_frequency: dict[str, int],
    preferred_tags: list[str],
    threshold: int = _DEFAULT_TAG_THRESHOLD,
) -> list[dict]:
    """Find preferred tags used fewer than threshold times."""
    threshold = max(1, int(threshold))
    underused = []

    for raw_tag in preferred_tags:
        if not isinstance(raw_tag, str):
            continue
        tag = _normalize_tag(raw_tag)
        if not tag:
            continue
        raw_count = tag_frequency.get(tag, tag_frequency.get(raw_tag, 0))
        try:
            current = int(raw_count)
        except (TypeError, ValueError):
            current = 0

        deficit = max(0, threshold - current)
        if deficit > 0:
            underused.append(
                {
                    "tag": tag,
                    "current_count": current,
                    "target_count": threshold,
                    "deficit": deficit,
                    "coverage_ratio": round(current / threshold, 3),
                }
            )

    underused.sort(key=lambda item: (-item["deficit"], item["current_count"], item["tag"]))
    return underused


def find_underserved_categories(
    categories: dict[str, int],
    taxonomy: dict,
) -> list[dict]:
    """Find categories where essay count is below taxonomy typical_count."""
    underserved = []
    cat_defs = taxonomy.get("categories", {}) if isinstance(taxonomy, dict) else {}

    for cat_name, cat_spec in cat_defs.items():
        if not isinstance(cat_name, str) or not isinstance(cat_spec, dict):
            continue

        try:
            typical = int(cat_spec.get("typical_count", 0))
        except (TypeError, ValueError):
            typical = 0
        if typical <= 0:
            continue

        try:
            current = int(categories.get(cat_name, 0))
        except (TypeError, ValueError):
            current = 0

        if current < typical:
            deficit = typical - current
            underserved.append(
                {
                    "category": cat_name,
                    "current_count": current,
                    "typical_count": typical,
                    "deficit": deficit,
                    "coverage_ratio": round(current / typical, 3),
                }
            )

    underserved.sort(
        key=lambda item: (-item["deficit"], item["coverage_ratio"], item["category"])
    )
    return underserved


def extract_surfaced_topics(
    surfaced: list[dict],
    threshold: float = _DEFAULT_SURFACED_THRESHOLD,
) -> list[dict]:
    """Extract surfaced topics above threshold, with normalization + dedupe."""
    results = []
    seen: set[str] = set()
    threshold = _parse_score(threshold)

    for item in surfaced:
        if not isinstance(item, dict):
            continue

        score_input = item.get("score")
        if score_input is None:
            # Backward compatibility with reading-observatory contract.
            score_input = item.get("relevance_score", 0.0)
        score = _parse_score(score_input)
        if score < threshold:
            continue

        title = str(item.get("title", "")).strip()
        url = str(item.get("url", "")).strip()
        if not title and not url:
            continue

        dedupe_key = url.lower() if url else _normalize_title_key(title)
        if dedupe_key and dedupe_key in seen:
            continue
        if dedupe_key:
            seen.add(dedupe_key)

        surfaced_at = str(
            item.get("surfaced_at") or item.get("surfaced_date") or ""
        ).strip()
        if surfaced_at and re.match(r"^\d{4}-\d{2}-\d{2}$", surfaced_at):
            surfaced_at = f"{surfaced_at}T00:00:00+00:00"

        results.append(
            {
                "title": title,
                "url": url,
                "matched_collections": _normalize_tag_list(
                    item.get("matched_collections", [])
                ),
                "score": score,
                "source_feed": str(
                    item.get("source_feed") or item.get("feed_title") or ""
                ).strip(),
                "surfaced_at": surfaced_at,
            }
        )

    results.sort(key=lambda item: (-item["score"], item["title"]))
    return results


def find_cross_reference_gaps(
    xrefs: dict,
    essay_lookup: dict[str, dict] | None = None,
) -> list[dict]:
    """Find essays with no related_repos and enrich with known metadata."""
    entries = xrefs.get("entries", {}) if isinstance(xrefs, dict) else {}
    orphans = []

    for filename, entry in entries.items():
        if not isinstance(entry, dict):
            continue
        related = entry.get("related_repos") or []
        if related:
            continue

        lookup_entry = (essay_lookup or {}).get(filename, {})
        tags = _normalize_tag_list(
            entry.get("tags") or lookup_entry.get("tags") or []
        )
        category = str(
            entry.get("category") or lookup_entry.get("category") or "meta-system"
        ).strip()
        title = str(entry.get("title") or lookup_entry.get("title") or "").strip()

        orphans.append(
            {
                "filename": filename,
                "title": title,
                "category": category or "meta-system",
                "tags": tags,
            }
        )

    orphans.sort(key=lambda item: (item["category"], item["filename"]))
    return orphans


def _score_tag_gap(gap: dict, tag_threshold: int) -> float:
    """Score tag-gap suggestions."""
    current = int(gap.get("current_count", 0))
    deficit = max(0, int(gap.get("deficit", max(0, tag_threshold - current))))
    threshold = max(1, int(tag_threshold))
    severity = deficit / threshold
    zero_bonus = 0.08 if current == 0 else 0.0
    return _clip_score(0.42 + (0.42 * severity) + zero_bonus)


def _score_category_gap(gap: dict) -> float:
    """Score category-gap suggestions."""
    current = max(0, int(gap.get("current_count", 0)))
    typical = max(1, int(gap.get("typical_count", 1)))
    deficit = max(0, int(gap.get("deficit", typical - current)))
    severity = deficit / typical
    empty_bonus = 0.12 if current == 0 else 0.0
    return _clip_score(0.52 + (0.4 * severity) + empty_bonus)


def _score_surfaced_topic(topic: dict) -> float:
    """Score surfaced-article suggestions."""
    surfaced_score = _parse_score(topic.get("score", 0.0))
    tags = _normalize_tag_list(topic.get("matched_collections", []))
    tag_bonus = min(0.08, len(tags) * 0.02)
    recency = _recency_bonus(str(topic.get("surfaced_at", "")))
    return _clip_score(0.30 + (0.55 * surfaced_score) + tag_bonus + recency)


def _score_cross_ref_gap(orphan: dict) -> float:
    """Score cross-reference-gap suggestions."""
    tags = _normalize_tag_list(orphan.get("tags", []))
    category = str(orphan.get("category", "")).strip()
    tag_bonus = min(0.10, len(tags) * 0.03)
    category_bonus = 0.05 if category and category != "meta-system" else 0.0
    return _clip_score(0.28 + tag_bonus + category_bonus)


def _build_suggestion(
    suggestion_type: str,
    title: str,
    rationale: str,
    suggested_tags: list[str],
    suggested_category: str,
    score: float,
    focus_area: str,
    source_data: dict,
) -> dict:
    """Build a consistently-shaped suggestion payload."""
    clean_score = _clip_score(score)
    priority = _priority_from_score(clean_score)
    clean_tags = _normalize_tag_list(suggested_tags)[:5]

    return {
        "id": f"{suggestion_type}:{_slugify(title)}",
        "type": suggestion_type,
        "title": str(title).strip(),
        "rationale": str(rationale).strip(),
        "suggested_tags": clean_tags,
        "suggested_category": str(suggested_category).strip() or "meta-system",
        "priority": priority,
        "score": clean_score,
        "focus_area": focus_area,
        "estimated_effort": _effort_from_type_and_score(suggestion_type, clean_score),
        "priority_reason": _priority_reason(suggestion_type, clean_score, source_data),
        "source_data": source_data,
    }


def generate_suggestions(
    underused_tags: list[dict],
    underserved_categories: list[dict],
    surfaced_topics: list[dict],
    orphan_essays: list[dict],
    essay_index: dict | None = None,
    taxonomy: dict | None = None,
) -> list[dict]:
    """Combine all gap analyses into rich, scored topic suggestions."""
    suggestions = []
    essay_index = essay_index or {}
    taxonomy = taxonomy or {}
    essays = essay_index.get("essays", [])
    cooccurrence = build_tag_cooccurrence(essays)

    available_categories = set((taxonomy.get("categories") or {}).keys())
    if not available_categories:
        available_categories = {"meta-system", "case-study", "guide"}

    default_category = (
        underserved_categories[0]["category"] if underserved_categories else "meta-system"
    )
    if default_category not in available_categories:
        default_category = "meta-system"

    for gap in underused_tags:
        tag = _normalize_tag(gap.get("tag", ""))
        if not tag:
            continue
        title_tag = tag.replace("-", " ").title()

        companion_tags = select_companion_tags(
            [tag],
            cooccurrence,
            max_tags=2,
            blocked_tags={tag},
        )
        target_count = max(1, int(gap.get("target_count", _DEFAULT_TAG_THRESHOLD)))
        score = _score_tag_gap(gap, target_count)
        source_data = {
            "tag": tag,
            "current_count": int(gap.get("current_count", 0)),
            "target_count": target_count,
            "deficit": int(gap.get("deficit", max(0, target_count - int(gap.get("current_count", 0))))),
            "companion_tags": companion_tags,
        }
        suggestions.append(
            _build_suggestion(
                suggestion_type="tag-gap",
                title=f"Exploring {title_tag}: Untapped Perspectives in the ORGANVM System",
                rationale=(
                    f"Tag '{tag}' appears in only {source_data['current_count']} essay(s) "
                    f"against a target of {target_count}. Broader coverage would improve "
                    "discoverability and corpus balance."
                ),
                suggested_tags=[tag] + companion_tags,
                suggested_category=default_category,
                score=score,
                focus_area="coverage-gap",
                source_data=source_data,
            )
        )

    for gap in underserved_categories:
        category = str(gap.get("category", "")).strip()
        if not category:
            continue
        if category not in available_categories:
            continue

        title_category = category.replace("-", " ").title()
        category_seed_tags = find_category_seed_tags(essays, category, max_tags=2)
        score = _score_category_gap(gap)
        source_data = {
            "category": category,
            "current_count": int(gap.get("current_count", 0)),
            "typical_count": int(gap.get("typical_count", 0)),
            "deficit": int(gap.get("deficit", 0)),
            "category_seed_tags": category_seed_tags,
        }
        suggestions.append(
            _build_suggestion(
                suggestion_type="category-gap",
                title=f"New {title_category}: Filling the {title_category} Gap",
                rationale=(
                    f"Category '{category}' has {source_data['current_count']} essays while "
                    f"typical volume is {source_data['typical_count']} "
                    f"(deficit: {source_data['deficit']})."
                ),
                suggested_tags=[category] + category_seed_tags,
                suggested_category=category,
                score=score,
                focus_area="coverage-gap",
                source_data=source_data,
            )
        )

    for topic in surfaced_topics:
        article_title = str(topic.get("title", "")).strip()
        article_tags = _normalize_tag_list(topic.get("matched_collections", []))
        companions = select_companion_tags(
            article_tags,
            cooccurrence,
            max_tags=2,
            blocked_tags=set(article_tags),
        )
        score = _score_surfaced_topic(topic)
        source_data = {
            "article_title": article_title,
            "url": str(topic.get("url", "")).strip(),
            "score": _parse_score(topic.get("score", 0.0)),
            "source_feed": str(topic.get("source_feed", "")).strip(),
            "surfaced_at": str(topic.get("surfaced_at", "")).strip(),
            "matched_collections": article_tags,
            "companion_tags": companions,
        }
        suggestions.append(
            _build_suggestion(
                suggestion_type="surfaced-article",
                title=f"Response to: {article_title}",
                rationale=(
                    f"Surfaced article scored {source_data['score']:.2f} against "
                    f"collection tags: {', '.join(article_tags) if article_tags else 'none'}."
                ),
                suggested_tags=article_tags + companions,
                suggested_category=default_category,
                score=score,
                focus_area="external-signal",
                source_data=source_data,
            )
        )

    for orphan in orphan_essays:
        orphan_title = str(orphan.get("title", "")).strip() or str(
            orphan.get("filename", "Untitled orphan")
        )
        orphan_tags = _normalize_tag_list(orphan.get("tags", []))
        if not orphan_tags:
            orphan_tags = ["cross-organ", "governance"]
        companions = select_companion_tags(
            orphan_tags,
            cooccurrence,
            max_tags=1,
            blocked_tags=set(orphan_tags),
        )
        category = str(orphan.get("category", "meta-system")).strip() or "meta-system"
        if category not in available_categories:
            category = "meta-system"
        score = _score_cross_ref_gap(orphan)
        source_data = {
            "filename": str(orphan.get("filename", "")).strip(),
            "title": str(orphan.get("title", "")).strip(),
            "category": category,
            "tags": orphan_tags,
            "companion_tags": companions,
        }
        suggestions.append(
            _build_suggestion(
                suggestion_type="cross-ref-gap",
                title=f"Follow-up: Connecting '{orphan_title}' to the Wider System",
                rationale=(
                    f"Essay '{source_data['filename']}' has no cross-organ references. "
                    "A follow-up can link it to repos and essays in adjacent organs."
                ),
                suggested_tags=orphan_tags + companions,
                suggested_category=category,
                score=score,
                focus_area="cross-organ-linking",
                source_data=source_data,
            )
        )

    return suggestions


def deduplicate_suggestions(
    suggestions: list[dict],
    existing_titles: set[str] | None = None,
) -> tuple[list[dict], int]:
    """Drop duplicates by title key and surfaced URL."""
    seen_titles = set(existing_titles or set())
    seen_urls: set[str] = set()
    deduped = []
    removed = 0

    for suggestion in suggestions:
        title_key = _normalize_title_key(str(suggestion.get("title", "")))
        source_data = suggestion.get("source_data", {})
        url = ""
        if isinstance(source_data, dict):
            url = str(source_data.get("url", "")).strip().lower()

        if title_key and title_key in seen_titles:
            removed += 1
            continue
        if url and url in seen_urls:
            removed += 1
            continue

        if title_key:
            seen_titles.add(title_key)
        if url:
            seen_urls.add(url)
        deduped.append(suggestion)

    return deduped, removed


def rank_and_limit_suggestions(
    suggestions: list[dict],
    max_suggestions: int = _DEFAULT_MAX_SUGGESTIONS,
    per_type_limit: int = _DEFAULT_PER_TYPE_LIMIT,
) -> list[dict]:
    """Sort by score, apply per-type balancing, and assign ranks."""
    if max_suggestions <= 0:
        max_suggestions = len(suggestions)
    if per_type_limit <= 0:
        per_type_limit = len(suggestions)

    sorted_suggestions = sorted(
        suggestions,
        key=lambda item: (
            -_parse_score(item.get("score", 0.0)),
            _PRIORITY_ORDER.get(str(item.get("priority", "low")), 99),
            str(item.get("title", "")),
        ),
    )

    selected = []
    type_counts: Counter = Counter()
    for suggestion in sorted_suggestions:
        suggestion_type = str(suggestion.get("type", "unknown"))
        if type_counts[suggestion_type] >= per_type_limit:
            continue
        type_counts[suggestion_type] += 1

        selected.append(dict(suggestion))
        if len(selected) >= max_suggestions:
            break

    for rank, suggestion in enumerate(selected, start=1):
        suggestion["rank"] = rank

    return selected


def summarize_suggestion_mix(suggestions: list[dict]) -> dict:
    """Summarize resulting suggestion mix by type/priority/tags."""
    if not suggestions:
        return {
            "by_type": {},
            "by_priority": {},
            "average_score": 0.0,
            "top_tags": [],
        }

    by_type = Counter(str(s.get("type", "unknown")) for s in suggestions)
    by_priority = Counter(str(s.get("priority", "low")) for s in suggestions)
    tag_counter = Counter()
    scores = []
    for suggestion in suggestions:
        scores.append(_parse_score(suggestion.get("score", 0.0)))
        for tag in _normalize_tag_list(suggestion.get("suggested_tags", [])):
            tag_counter[tag] += 1

    top_tags = [tag for tag, _ in tag_counter.most_common(10)]

    return {
        "by_type": dict(sorted(by_type.items(), key=lambda item: (-item[1], item[0]))),
        "by_priority": dict(
            sorted(by_priority.items(), key=lambda item: (-item[1], item[0]))
        ),
        "average_score": round(sum(scores) / len(scores), 3),
        "top_tags": top_tags,
    }


def suggest_all(
    essays_index_path: str,
    xrefs_path: str,
    tag_gov_path: str,
    cat_tax_path: str,
    surfaced_path: str,
    tag_threshold: int = _DEFAULT_TAG_THRESHOLD,
    surfaced_threshold: float = _DEFAULT_SURFACED_THRESHOLD,
    max_suggestions: int = _DEFAULT_MAX_SUGGESTIONS,
    per_type_limit: int = _DEFAULT_PER_TYPE_LIMIT,
) -> dict:
    """Run the full suggestion pipeline and return rich result data."""
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

    tag_frequency = index.get("tag_frequency", {}) if isinstance(index, dict) else {}
    preferred_tags = (
        tag_gov.get("preferred_tags", []) if isinstance(tag_gov, dict) else []
    )
    categories = index.get("categories", {}) if isinstance(index, dict) else {}

    underused = find_underused_tags(
        tag_frequency,
        preferred_tags,
        threshold=tag_threshold,
    )
    underserved = find_underserved_categories(categories, cat_tax or {})
    surfaced_topics = extract_surfaced_topics(
        surfaced if isinstance(surfaced, list) else [],
        threshold=surfaced_threshold,
    )
    essay_lookup = build_essay_lookup(index if isinstance(index, dict) else {})
    orphans = find_cross_reference_gaps(xrefs, essay_lookup=essay_lookup)

    raw_suggestions = generate_suggestions(
        underused,
        underserved,
        surfaced_topics,
        orphans,
        essay_index=index if isinstance(index, dict) else {},
        taxonomy=cat_tax if isinstance(cat_tax, dict) else {},
    )
    existing_title_keys = build_existing_title_keys(
        index if isinstance(index, dict) else {},
        xrefs if isinstance(xrefs, dict) else {},
    )
    deduped, duplicates_removed = deduplicate_suggestions(
        raw_suggestions,
        existing_titles=existing_title_keys,
    )
    suggestions = rank_and_limit_suggestions(
        deduped,
        max_suggestions=max_suggestions,
        per_type_limit=per_type_limit,
    )
    mix = summarize_suggestion_mix(suggestions)

    return {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "pipeline_version": PIPELINE_VERSION,
        "configuration": {
            "tag_threshold": max(1, int(tag_threshold)),
            "surfaced_threshold": _parse_score(surfaced_threshold),
            "max_suggestions": max(1, int(max_suggestions)),
            "per_type_limit": max(1, int(per_type_limit)),
            "freshness_window_days": int(timedelta(days=14).days),
        },
        "diagnostics": {
            "underused_tag_count": len(underused),
            "underserved_category_count": len(underserved),
            "surfaced_topic_count": len(surfaced_topics),
            "orphan_essay_count": len(orphans),
            "raw_suggestion_count": len(raw_suggestions),
            "duplicates_removed": duplicates_removed,
            "final_suggestion_count": len(suggestions),
            "mix": mix,
        },
        "total_suggestions": len(suggestions),
        "suggestions": suggestions,
    }


def main():
    parser = argparse.ArgumentParser(
        description="Generate essay topic suggestions from corpus analysis"
    )
    parser.add_argument(
        "--essays-index", required=True, help="Path to essays-index.json"
    )
    parser.add_argument("--xrefs", required=True, help="Path to cross-references.json")
    parser.add_argument(
        "--tag-governance", required=True, help="Path to tag-governance.yaml"
    )
    parser.add_argument(
        "--category-taxonomy", required=True, help="Path to category-taxonomy.yaml"
    )
    parser.add_argument("--surfaced", required=True, help="Path to surfaced.json")
    parser.add_argument(
        "--output", required=True, help="Output path for topic-suggestions.json"
    )
    parser.add_argument(
        "--tag-threshold",
        type=int,
        default=_DEFAULT_TAG_THRESHOLD,
        help=f"Preferred-tag minimum count target (default: {_DEFAULT_TAG_THRESHOLD})",
    )
    parser.add_argument(
        "--surfaced-threshold",
        type=float,
        default=_DEFAULT_SURFACED_THRESHOLD,
        help=(
            "Minimum surfaced-topic score to consider "
            f"(default: {_DEFAULT_SURFACED_THRESHOLD})"
        ),
    )
    parser.add_argument(
        "--max-suggestions",
        type=int,
        default=_DEFAULT_MAX_SUGGESTIONS,
        help=f"Maximum suggestions to emit (default: {_DEFAULT_MAX_SUGGESTIONS})",
    )
    parser.add_argument(
        "--per-type-limit",
        type=int,
        default=_DEFAULT_PER_TYPE_LIMIT,
        help=f"Maximum suggestions per type (default: {_DEFAULT_PER_TYPE_LIMIT})",
    )
    args = parser.parse_args()

    result = suggest_all(
        args.essays_index,
        args.xrefs,
        args.tag_governance,
        args.category_taxonomy,
        args.surfaced,
        tag_threshold=args.tag_threshold,
        surfaced_threshold=args.surfaced_threshold,
        max_suggestions=args.max_suggestions,
        per_type_limit=args.per_type_limit,
    )

    out = Path(args.output)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(result, indent=2, ensure_ascii=False) + "\n")

    print(f"Generated {result['total_suggestions']} topic suggestions -> {args.output}")
    sys.exit(0)


if __name__ == "__main__":
    main()
