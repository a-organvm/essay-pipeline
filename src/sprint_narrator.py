"""Sprint narrative generator for ORGAN-V essay-pipeline.

Combines analytics metrics, essay stats, and publication cadence into a
readable markdown sprint narrative — a human-reviewable draft for the next
retrospective essay or sprint report.

CLI: python -m src.sprint_narrator --metrics PATH --report PATH \
     --index PATH --calendar PATH --output PATH
"""

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

PIPELINE_VERSION = "0.2.0"


def load_json_safe(path: str) -> dict | list:
    """Load JSON from path, returning {} on missing or malformed file."""
    try:
        with open(path) as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError, TypeError):
        return {}


def summarize_web_engagement(metrics: dict) -> str:
    """Produce a paragraph summarizing web engagement metrics."""
    if not metrics:
        return "No web engagement data available for this period."

    totals = metrics.get("site_totals", {})
    views = totals.get("page_views", 0)
    visitors = totals.get("unique_visitors", 0)
    pages = metrics.get("pages", [])
    trends = metrics.get("trends", {})

    parts = [f"The public-process site received **{views:,} page views** from **{visitors:,} unique visitors**."]

    if pages:
        top = pages[0]
        parts.append(f"The most-visited page was `{top.get('path', '?')}` with {top.get('views', 0):,} views.")

    views_delta = trends.get("views_delta_pct")
    visitors_delta = trends.get("visitors_delta_pct")
    if views_delta is not None:
        direction = "up" if views_delta >= 0 else "down"
        parts.append(f"Page views are {direction} {abs(views_delta):.1f}% from the previous period.")
    if visitors_delta is not None:
        direction = "up" if visitors_delta >= 0 else "down"
        parts.append(f"Unique visitors are {direction} {abs(visitors_delta):.1f}%.")

    return " ".join(parts)


def summarize_github_activity(report: dict) -> str:
    """Produce a paragraph summarizing GitHub activity across organs."""
    if not report:
        return "No GitHub activity data available for this period."

    gh = report.get("github_activity", {})
    commits = gh.get("total_commits", 0)
    prs = gh.get("total_prs", 0)
    releases = gh.get("total_releases", 0)
    breakdown = gh.get("organ_breakdown", {})

    parts = [
        f"Across all organs, there were **{commits} commits**, "
        f"**{prs} PRs**, and **{releases} releases**."
    ]

    active = []
    for organ, stats in breakdown.items():
        organ_commits = stats.get("commits", 0)
        if organ_commits > 0:
            active.append(f"ORGAN-{organ} ({organ_commits})")
    if active:
        parts.append(f"Most active by commits: {', '.join(active)}.")
    elif commits == 0:
        parts.append("No commit activity was recorded in any organ.")

    return " ".join(parts)


def summarize_essay_corpus(index: dict) -> str:
    """Produce a paragraph summarizing the essay corpus."""
    if not index:
        return "No essay index data available."

    total = index.get("total_essays", 0)
    words = index.get("total_words", 0)
    categories = index.get("categories", {})
    tag_freq = index.get("tag_frequency", {})

    parts = [f"The corpus now contains **{total} essays** totaling **{words:,} words**."]

    if categories:
        cat_parts = [f"{cat} ({count})" for cat, count in categories.items()]
        parts.append(f"Category distribution: {', '.join(cat_parts)}.")

    if tag_freq:
        top_tags = list(tag_freq.keys())[:5]
        parts.append(f"Most-used tags: {', '.join(top_tags)}.")

    return " ".join(parts)


def summarize_publication_cadence(calendar: dict) -> str:
    """Produce a paragraph summarizing publication cadence."""
    if not calendar:
        return "No publication calendar data available."

    dates = calendar.get("dates", {})
    total = calendar.get("total_essays", 0)

    if not dates:
        return f"The corpus contains {total} essays but no dated publication data."

    num_days = len(dates)
    sorted_dates = sorted(dates.keys())
    first = sorted_dates[0]
    last = sorted_dates[-1]

    parts = [f"Essays were published across **{num_days} distinct days**, from {first} to {last}."]

    max_day = max(dates.items(), key=lambda x: x[1])
    parts.append(f"The busiest day was {max_day[0]} with {max_day[1]} essay(s).")

    return " ".join(parts)


def format_alerts(report: dict) -> str:
    """Format any triggered alerts from the system engagement report."""
    if not report:
        return "No alert data available."

    alerts = report.get("alerts", [])
    if not alerts:
        return "No alerts triggered during this period."

    lines = []
    for alert in alerts:
        severity = alert.get("severity", "info")
        icon = {"warning": "\u26a0\ufe0f", "critical": "\u274c", "info": "\u2139\ufe0f"}.get(severity, "\u2139\ufe0f")
        rule = alert.get("rule", "unknown")
        desc = alert.get("description", "")
        current = alert.get("current_value", "?")
        threshold = alert.get("threshold", "?")
        lines.append(f"- {icon} **{rule}**: {desc} ({current} < {threshold})")

    return "\n".join(lines)


def generate_narrative(
    metrics: dict,
    report: dict,
    index: dict,
    calendar: dict,
) -> str:
    """Generate the full sprint narrative markdown."""
    period = (metrics or report or {}).get("period", {})
    start = period.get("start", "unknown")
    end = period.get("end", "unknown")

    sections = [
        f"# Sprint Narrative \u2014 {start} to {end}",
        "",
        f"> Auto-generated by essay-pipeline sprint_narrator v{PIPELINE_VERSION}. Human review required.",
        "",
        "## Sprint Summary",
        "",
        f"This sprint covered the period from {start} to {end} across the ORGANVM system. "
        f"Below is an automated summary of web engagement, GitHub activity, essay corpus "
        f"status, and publication cadence.",
        "",
        "## Web Engagement",
        "",
        summarize_web_engagement(metrics),
        "",
        "## GitHub Activity",
        "",
        summarize_github_activity(report),
        "",
        "## Essay Corpus",
        "",
        summarize_essay_corpus(index),
        "",
        "## Publication Cadence",
        "",
        summarize_publication_cadence(calendar),
        "",
        "## Alerts",
        "",
        format_alerts(report),
        "",
        "## Suggested Focus Areas",
        "",
        "Based on the data above, consider:",
        "",
    ]

    focus_items = []
    # Suggest based on alerts
    alerts = (report or {}).get("alerts", [])
    for alert in alerts:
        rule = alert.get("rule", "")
        if "stall" in rule:
            focus_items.append("- Address development stall: increase commit cadence across organs.")

    # Suggest based on engagement
    totals = (metrics or {}).get("site_totals", {})
    if totals.get("page_views", 0) == 0:
        focus_items.append("- No web traffic recorded — verify GoatCounter integration or site deployment.")

    # Suggest based on corpus
    total_essays = (index or {}).get("total_essays", 0)
    if total_essays > 0:
        focus_items.append(f"- Continue expanding the essay corpus (currently {total_essays} essays).")

    if not focus_items:
        focus_items.append("- No specific focus areas identified from the current data.")

    sections.extend(focus_items)
    sections.append("")

    return "\n".join(sections)


def narrate_all(
    metrics_path: str,
    report_path: str,
    index_path: str,
    calendar_path: str,
    output_path: str,
) -> dict:
    """Run the full narrative pipeline, write output, return summary."""
    metrics = load_json_safe(metrics_path)
    report = load_json_safe(report_path)
    index = load_json_safe(index_path)
    calendar = load_json_safe(calendar_path)

    narrative = generate_narrative(metrics, report, index, calendar)

    out = Path(output_path)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(narrative)

    return {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "pipeline_version": PIPELINE_VERSION,
        "output_path": str(out),
        "sections": [
            "Sprint Summary",
            "Web Engagement",
            "GitHub Activity",
            "Essay Corpus",
            "Publication Cadence",
            "Alerts",
            "Suggested Focus Areas",
        ],
    }


def main():
    parser = argparse.ArgumentParser(
        description="Generate sprint narrative from analytics and essay data"
    )
    parser.add_argument("--metrics", required=True, help="Path to engagement-metrics.json")
    parser.add_argument("--report", required=True, help="Path to system-engagement-report.json")
    parser.add_argument("--index", required=True, help="Path to essays-index.json")
    parser.add_argument("--calendar", required=True, help="Path to publication-calendar.json")
    parser.add_argument("--output", required=True, help="Output path for sprint-narrative-draft.md")
    args = parser.parse_args()

    summary = narrate_all(args.metrics, args.report, args.index, args.calendar, args.output)

    print(
        f"Generated sprint narrative with {len(summary['sections'])} sections "
        f"→ {args.output}"
    )
    sys.exit(0)


if __name__ == "__main__":
    main()
