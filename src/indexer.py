"""Content indexer for ORGAN-V public-process.

Reads all Markdown content (essays and logs), extracts frontmatter,
computes word counts, and generates structured JSON data files.

CLI: python -m src.indexer --posts-dir _posts/ --output-dir data/
     python -m src.indexer --posts-dir _posts/ --logs-dir _logs/ --output-dir data/
"""

import argparse
import json
import re
import sys
from collections import Counter
from datetime import date
from pathlib import Path

import yaml


def compute_readability(text: str) -> float:
    """Compute a simple Flesch Reading Ease score.

    Formula: 206.835 - 1.015 * (total_words / total_sentences) - 84.6 * (total_syllables / total_words)
    Approximation for syllables: average 1.5 syllables per word for simplicity.
    """
    words = [w for w in text.split() if w.strip()]
    if not words:
        return 0.0

    # Simple sentence detection
    sentences = re.split(r"[.!?]+", text)
    sentences = [s for s in sentences if s.strip()]
    if not sentences:
        sentences = [text]

    avg_sentence_length = len(words) / len(sentences)
    avg_syllables_per_word = 1.5

    score = 206.835 - (1.015 * avg_sentence_length) - (84.6 * avg_syllables_per_word)
    return round(max(0.0, min(100.0, score)), 1)


def extract_essay_data(filepath: Path) -> dict | None:
    """Extract frontmatter and compute body word count from a Markdown file."""
    text = filepath.read_text(encoding="utf-8")
    if not text.startswith("---"):
        return None
    parts = text.split("---", 2)
    if len(parts) < 3:
        return None
    try:
        fm = yaml.safe_load(parts[1])
    except yaml.YAMLError:
        return None

    body = parts[2].strip()
    # Strip markdown formatting for word count
    clean = re.sub(r"[#*_`\[\]()>|]", " ", body)
    clean = re.sub(r"https?://\S+", "", clean)
    clean = re.sub(r"\s+", " ", clean).strip()
    word_count = len(clean.split()) if clean else 0

    readability = compute_readability(clean)

    return {
        "filename": filepath.name,
        "frontmatter": fm,
        "computed_word_count": word_count,
        "readability_score": readability,
    }


def build_essays_index(essays: list[dict]) -> dict:
    """Build the essays-index.json structure."""
    categories = Counter()
    tags = Counter()
    total_words = 0

    entries = []
    for e in essays:
        fm = e["frontmatter"]
        cat = fm.get("category", "uncategorized")
        categories[cat] += 1
        for tag in fm.get("tags", []):
            tags[tag] += 1
        wc = e["computed_word_count"]
        total_words += wc
        rs = e["readability_score"]
        entries.append(
            {
                "filename": e["filename"],
                "title": fm.get("title", ""),
                "date": fm.get("date", ""),
                "category": cat,
                "tags": fm.get("tags", []),
                "word_count": wc,
                "readability_score": rs,
                "reading_time": fm.get("reading_time", ""),
                "portfolio_relevance": fm.get("portfolio_relevance", ""),
            }
        )

    return {
        "version": "1.1",
        "updated": date.today().isoformat(),
        "generated_by": "essay-pipeline indexer v0.3.0",
        "total_essays": len(essays),
        "total_words": total_words,
        "categories": dict(sorted(categories.items(), key=lambda x: -x[1])),
        "tag_frequency": dict(sorted(tags.items(), key=lambda x: -x[1])),
        "essays": entries,
    }


def build_cross_references(essays: list[dict]) -> dict:
    """Build cross-references.json keyed by filename."""
    refs = {}
    for e in essays:
        fm = e["frontmatter"]
        refs[e["filename"]] = {
            "title": fm.get("title", ""),
            "related_repos": fm.get("related_repos", []),
            "tags": fm.get("tags", []),
            "category": fm.get("category", ""),
        }
    return {
        "version": "1.1",
        "updated": date.today().isoformat(),
        "total": len(refs),
        "entries": refs,
    }


def build_publication_calendar(
    essays: list[dict], logs: list[dict] | None = None
) -> dict:
    """Build publication-calendar.json with content count by date."""
    essay_dates = Counter()
    for e in essays:
        d = e["frontmatter"].get("date", "unknown")
        essay_dates[d] += 1

    result: dict = {
        "version": "1.2",
        "updated": date.today().isoformat(),
        "total_essays": len(essays),
        "essays": dict(sorted(essay_dates.items())),
    }

    if logs is not None:
        log_dates = Counter()
        for entry in logs:
            d = entry["frontmatter"].get("date", "unknown")
            log_dates[d] += 1
        result["total_logs"] = len(logs)
        result["logs"] = dict(sorted(log_dates.items()))

    return result


def build_logs_index(logs: list[dict]) -> dict:
    """Build the logs-index.json structure."""
    tags = Counter()
    moods = Counter()
    total_words = 0

    entries = []
    for entry in logs:
        fm = entry["frontmatter"]
        mood = fm.get("mood", "")
        if mood:
            moods[mood] += 1
        for tag in fm.get("tags", []):
            tags[tag] += 1
        wc = entry["computed_word_count"]
        total_words += wc
        entries.append(
            {
                "filename": entry["filename"],
                "title": fm.get("title", ""),
                "date": fm.get("date", ""),
                "mood": mood,
                "tags": fm.get("tags", []),
                "word_count": wc,
                "organs_touched": fm.get("organs_touched", []),
            }
        )

    return {
        "version": "1.0",
        "updated": date.today().isoformat(),
        "generated_by": "essay-pipeline indexer v0.3.0",
        "total_logs": len(logs),
        "total_words": total_words,
        "mood_frequency": dict(sorted(moods.items(), key=lambda x: -x[1])),
        "tag_frequency": dict(sorted(tags.items(), key=lambda x: -x[1])),
        "logs": entries,
    }


def index_all(posts_dir: str, output_dir: str, logs_dir: str | None = None) -> dict:
    """Index all content and write JSON data files.

    Returns a summary dict with counts.
    """
    posts = sorted(Path(posts_dir).glob("*.md"))
    essays = []
    for p in posts:
        data = extract_essay_data(p)
        if data:
            essays.append(data)

    logs = []
    if logs_dir:
        logs_path = Path(logs_dir)
        if logs_path.exists():
            for p in sorted(logs_path.glob("*.md")):
                data = extract_essay_data(p)
                if data:
                    logs.append(data)

    out = Path(output_dir)
    out.mkdir(parents=True, exist_ok=True)

    index = build_essays_index(essays)
    xrefs = build_cross_references(essays)
    calendar = build_publication_calendar(essays, logs if logs else None)

    (out / "essays-index.json").write_text(
        json.dumps(index, indent=2, ensure_ascii=False) + "\n"
    )
    (out / "cross-references.json").write_text(
        json.dumps(xrefs, indent=2, ensure_ascii=False) + "\n"
    )
    (out / "publication-calendar.json").write_text(
        json.dumps(calendar, indent=2, ensure_ascii=False) + "\n"
    )

    if logs:
        logs_index = build_logs_index(logs)
        (out / "logs-index.json").write_text(
            json.dumps(logs_index, indent=2, ensure_ascii=False) + "\n"
        )

    summary = {
        "essays": len(essays),
        "categories": len(index["categories"]),
        "total_words": index["total_words"],
    }
    if logs:
        summary["logs"] = len(logs)
    return summary


def main():
    parser = argparse.ArgumentParser(
        description="Index content and generate data files"
    )
    parser.add_argument("--posts-dir", required=True, help="Path to _posts/ directory")
    parser.add_argument(
        "--logs-dir", default=None, help="Path to _logs/ directory (optional)"
    )
    parser.add_argument(
        "--output-dir", required=True, help="Path to output data/ directory"
    )
    args = parser.parse_args()

    summary = index_all(args.posts_dir, args.output_dir, args.logs_dir)
    parts = [
        f"{summary['essays']} essays across "
        f"{summary['categories']} categories ({summary['total_words']} words)"
    ]
    if "logs" in summary:
        parts.append(f"{summary['logs']} logs")
    print(f"Indexed {', '.join(parts)}")
    sys.exit(0)


if __name__ == "__main__":
    main()
