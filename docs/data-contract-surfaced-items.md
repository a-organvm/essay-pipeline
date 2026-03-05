# Surfaced Items Data Contract

Version: `1.0`  
Last updated: `2026-03-05`

This consumer contract mirrors `reading-observatory/docs/data-contract-surfaced-items.md`.

## Expected Input (`--surfaced`)
`src.topic_suggester` expects a JSON array of surfaced-item objects.

### Canonical producer fields
- `title` (string)
- `url` (string)
- `relevance_score` (float, 0..1)
- `matched_collections` (string[])
- `surfaced_date` (`YYYY-MM-DD`)

### Accepted compatibility aliases
- `score` (legacy/alternate score key; preferred by consumer when present)
- `source_feed` or `feed_title` (source label)
- `surfaced_at` (ISO datetime)

## Normalization Behavior
- Score is parsed from `score` first, else `relevance_score`.
- Date is parsed from `surfaced_at` first, else `surfaced_date`.
- `surfaced_date` values are normalized to midnight UTC for recency scoring.

## Contract Change Policy
- Any change to surfaced-item shape must be accompanied by:
  - producer tests in `reading-observatory`
  - consumer tests in `essay-pipeline`
  - contract doc updates in both repos
