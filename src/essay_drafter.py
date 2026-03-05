"""Full essay generation pipeline for ORGAN-V.

Generates complete essays using an LLM provider, validates against the
frontmatter schema, and applies automatic repairs for common issues.

Reuses existing validator.validate_entry() for schema compliance checking.

CLI: python -m src.essay_drafter \
       --suggestions data/topic-suggestions.json \
       --suggestion-index 0 \
       --template-dir ../../editorial-standards/templates/ \
       --schema ../../editorial-standards/schemas/frontmatter-schema.yaml \
       --rubric ../../editorial-standards/schemas/quality-rubric.yaml \
       --tag-governance ../../editorial-standards/schemas/tag-governance.yaml \
       --category-taxonomy ../../editorial-standards/schemas/category-taxonomy.yaml \
       --posts-dir ../../public-process/_posts/ \
       --output-dir _public/_posts/
"""

import argparse
import json
import re
import sys
import tempfile
from datetime import date
from pathlib import Path

import yaml

from .llm_client import LLMResponse, create_client
from .schema_loader import load_schema
from .validator import validate_entry

PIPELINE_VERSION = "0.3.0"
MAX_RETRIES = 2


def build_system_prompt(
    template: str,
    schema: dict,
    rubric: dict,
    tag_governance: dict,
    category_taxonomy: dict,
    existing_titles: list[str],
) -> str:
    """Encode the structural contract into a system prompt.

    Provides the LLM with all constraints it must satisfy to produce
    a valid essay that passes schema validation.
    """
    # Extract schema constraints
    categories = list(
        category_taxonomy.get("categories", {}).keys()
    )
    preferred_tags = tag_governance.get("preferred_tags", [])
    tag_rules = tag_governance.get("rules", {})
    rubric_dims = rubric.get("dimensions", {})
    publish_threshold = rubric.get("thresholds", {}).get("publish", 60)

    # Build quality criteria summary
    quality_criteria = []
    for dim_name, dim_spec in rubric_dims.items():
        desc = dim_spec.get("description", "")
        quality_criteria.append(f"- {dim_name}: {desc}")

    return f"""You are an essay writer for the ORGANVM eight-organ creative-institutional system.
You write substantive, honest, technically grounded essays for the public-process site.

## Author
Always use author: "@4444J99"

## Structural Requirements (MUST pass schema validation)
- layout: must be "essay"
- title: 10-200 characters, descriptive not clickbait
- date: YYYY-MM-DD format, use today's date
- tags: {tag_rules.get('min_per_essay', 2)}-{tag_rules.get('max_per_essay', 8)} items, lowercase hyphenated, pattern: ^[a-z0-9]+(-[a-z0-9]+)*$
- category: one of {categories}
- excerpt: 50-400 characters, one-paragraph summary
- portfolio_relevance: one of CRITICAL, HIGH, MEDIUM
- related_repos: list of repo paths matching ^(organvm-|meta-organvm)
- reading_time: format "N min" (e.g. "12 min")
- word_count: integer >= 500, must match actual body word count

## Tag Guidance
Prefer these tags: {', '.join(preferred_tags[:20])}
Tags must match pattern: {tag_rules.get('pattern', '^[a-z0-9]+(-[a-z0-9]+)*$')}

## Quality Criteria (aim for {publish_threshold}+ points)
{chr(10).join(quality_criteria)}

## Template Structure
Follow this template structure for the essay body:

{template}

## Constraints
- Do NOT fabricate repository names or metrics. Only reference repos that exist in the ORGANVM system.
- Do NOT repeat titles that already exist. Existing titles: {json.dumps(existing_titles[-10:]) if existing_titles else '[]'}
- Be honest about limitations and what doesn't work yet.
- Every paragraph should advance the argument. No filler.
- The output must be a complete Markdown file with YAML frontmatter delimited by --- on its own line.
- The word_count field must be an integer approximating the actual body word count.
- The reading_time should be approximately word_count / 250, rounded, formatted as "N min"."""


def build_user_prompt(
    suggestion: dict,
    context: dict | None = None,
) -> str:
    """Build the user prompt from a topic suggestion and optional context."""
    suggestion_type = suggestion.get("type", "unknown")
    title_hint = suggestion.get("title", "")
    rationale = suggestion.get("rationale", "")
    suggested_tags = suggestion.get("suggested_tags", [])
    suggested_category = suggestion.get("suggested_category", "meta-system")
    source_data = suggestion.get("source_data", {})

    prompt_parts = [
        "Write a complete essay based on the following topic suggestion.",
        "",
        "## Topic",
        f"Type: {suggestion_type}",
        f"Suggested title direction: {title_hint}",
        f"Rationale: {rationale}",
        f"Suggested category: {suggested_category}",
        f"Suggested tags: {', '.join(suggested_tags)}",
    ]

    if source_data:
        prompt_parts.append(f"Source data: {json.dumps(source_data)}")

    if context:
        sprint_narrative = context.get("sprint_narrative", "")
        if sprint_narrative:
            prompt_parts.extend([
                "",
                "## Current Sprint Context",
                sprint_narrative[:2000],
            ])

        metrics_summary = context.get("metrics_summary", "")
        if metrics_summary:
            prompt_parts.extend([
                "",
                "## Recent Metrics",
                metrics_summary[:1000],
            ])

    prompt_parts.extend([
        "",
        "## Instructions",
        "Write the complete essay as a single Markdown file.",
        "Start with YAML frontmatter between --- delimiters.",
        f"Use today's date: {date.today().isoformat()}",
        "The essay body should be at least 500 words.",
        "Calculate word_count and reading_time from the actual body length.",
    ])

    return "\n".join(prompt_parts)


def validate_draft(draft_text: str, schema_path: str) -> tuple[bool, list[str]]:
    """Validate a draft essay against the frontmatter schema.

    Writes to a temp file, calls validate_entry(), returns (valid, errors).
    """
    schema = load_schema(schema_path)
    with tempfile.NamedTemporaryFile(
        mode="w", suffix=".md", delete=False, encoding="utf-8"
    ) as f:
        f.write(draft_text)
        tmp_path = Path(f.name)

    try:
        errors = validate_entry(tmp_path, schema)
        return (len(errors) == 0, errors)
    finally:
        tmp_path.unlink(missing_ok=True)


def _count_body_words(text: str) -> int:
    """Count words in the essay body (after frontmatter)."""
    if not text.startswith("---"):
        return 0
    parts = text.split("---", 2)
    if len(parts) < 3:
        return 0
    body = parts[2].strip()
    clean = re.sub(r"[#*_`\[\]()>|]", " ", body)
    clean = re.sub(r"https?://\S+", "", clean)
    clean = re.sub(r"\s+", " ", clean).strip()
    return len(clean.split()) if clean else 0


def repair_frontmatter(
    draft_text: str, errors: list[str], schema: dict
) -> str:
    """Fix common frontmatter errors without re-calling the LLM.

    Handles: date format, tag patterns, word_count accuracy,
    reading_time format, missing author prefix.
    """
    if not draft_text.startswith("---"):
        return draft_text

    parts = draft_text.split("---", 2)
    if len(parts) < 3:
        return draft_text

    try:
        fm = yaml.safe_load(parts[1])
    except yaml.YAMLError:
        return draft_text

    if fm is None:
        return draft_text

    repaired = False

    # Fix date format — strip time components or reformat
    if "date" in fm:
        raw_date = str(fm["date"])
        # Handle datetime objects from YAML
        if hasattr(fm["date"], "strftime"):
            fm["date"] = fm["date"].strftime("%Y-%m-%d")
            repaired = True
        elif not re.match(r"^\d{4}-\d{2}-\d{2}$", raw_date):
            # Try to extract YYYY-MM-DD from longer string
            match = re.search(r"(\d{4}-\d{2}-\d{2})", raw_date)
            if match:
                fm["date"] = match.group(1)
                repaired = True

    # Fix author — ensure @ prefix
    if "author" in fm and isinstance(fm["author"], str):
        if not fm["author"].startswith("@"):
            fm["author"] = f"@{fm['author']}"
            repaired = True

    # Fix tags — lowercase and hyphenate
    if "tags" in fm and isinstance(fm["tags"], list):
        fixed_tags = []
        for tag in fm["tags"]:
            if isinstance(tag, str):
                fixed = tag.lower().strip().replace(" ", "-").replace("_", "-")
                # Remove any characters that don't match the pattern
                fixed = re.sub(r"[^a-z0-9-]", "", fixed)
                fixed = re.sub(r"-+", "-", fixed).strip("-")
                if fixed:
                    fixed_tags.append(fixed)
        if fixed_tags != fm["tags"]:
            fm["tags"] = fixed_tags
            repaired = True

    # Fix word_count — compute from actual body
    actual_wc = _count_body_words(draft_text)
    if actual_wc > 0 and fm.get("word_count") != actual_wc:
        fm["word_count"] = actual_wc
        repaired = True

    # Fix reading_time — derive from word count
    wc = fm.get("word_count", actual_wc)
    if isinstance(wc, int) and wc > 0:
        expected_rt = f"{max(1, round(wc / 250))} min"
        if fm.get("reading_time") != expected_rt:
            fm["reading_time"] = expected_rt
            repaired = True

    # Fix layout
    if fm.get("layout") != "essay":
        fm["layout"] = "essay"
        repaired = True

    if not repaired:
        return draft_text

    # Reconstruct the document
    fm_text = yaml.dump(fm, default_flow_style=False, allow_unicode=True, sort_keys=False)
    return f"---\n{fm_text}---{parts[2]}"


def _extract_markdown(llm_text: str) -> str:
    """Extract the markdown document from LLM output.

    Handles cases where the LLM wraps output in ```markdown fences.
    """
    # Check for markdown code fence
    fence_match = re.search(
        r"```(?:markdown|md)?\s*\n(---\n.+?\n---\n.+?)```",
        llm_text,
        re.DOTALL,
    )
    if fence_match:
        return fence_match.group(1).strip()

    # Check for raw frontmatter start
    fm_match = re.search(r"(---\n.+?\n---\n.+)", llm_text, re.DOTALL)
    if fm_match:
        return fm_match.group(1).strip()

    return llm_text.strip()


def draft_essay(
    suggestion: dict,
    template_dir: str,
    schema_path: str,
    rubric_path: str,
    tag_governance_path: str,
    category_taxonomy_path: str,
    posts_dir: str,
    output_dir: str,
    context: dict | None = None,
    provider: str | None = None,
) -> dict:
    """Orchestrate the full essay drafting pipeline.

    Returns a summary dict with draft path, validation status, and LLM metadata.
    """
    # Load editorial assets
    schema = load_schema(schema_path)
    with open(rubric_path) as f:
        rubric = yaml.safe_load(f)
    with open(tag_governance_path) as f:
        tag_governance = yaml.safe_load(f)
    with open(category_taxonomy_path) as f:
        category_taxonomy = yaml.safe_load(f)

    # Load the appropriate template
    suggested_category = suggestion.get("suggested_category", "meta-system")
    template_file = Path(template_dir) / f"{suggested_category}.md"
    if not template_file.exists():
        template_file = Path(template_dir) / "meta-system.md"
    template = template_file.read_text(encoding="utf-8")

    # Collect existing titles to avoid duplication
    existing_titles = []
    posts_path = Path(posts_dir)
    if posts_path.exists():
        for post in posts_path.glob("*.md"):
            text = post.read_text(encoding="utf-8")
            if text.startswith("---"):
                parts = text.split("---", 2)
                if len(parts) >= 3:
                    try:
                        fm = yaml.safe_load(parts[1])
                        if fm and "title" in fm:
                            existing_titles.append(fm["title"])
                    except yaml.YAMLError:
                        pass

    # Build prompts
    system_prompt = build_system_prompt(
        template, schema, rubric, tag_governance, category_taxonomy, existing_titles
    )
    user_prompt = build_user_prompt(suggestion, context)

    # Create LLM client
    client = create_client(provider)

    # Generate with retry loop
    last_errors: list[str] = []
    last_response: LLMResponse | None = None

    for attempt in range(1 + MAX_RETRIES):
        if attempt > 0:
            # Re-prompt with error feedback
            error_feedback = "\n".join(f"- {e}" for e in last_errors)
            user_prompt_retry = (
                f"{user_prompt}\n\n"
                f"## Previous Attempt Failed Validation\n"
                f"The previous draft had these errors:\n{error_feedback}\n\n"
                f"Fix these issues in the new draft."
            )
            response = client.generate(system_prompt, user_prompt_retry)
        else:
            response = client.generate(system_prompt, user_prompt)

        last_response = response
        draft_text = _extract_markdown(response.text)

        # Validate
        valid, errors = validate_draft(draft_text, schema_path)
        if valid:
            return _write_draft(draft_text, output_dir, response, attempt)

        # Try automatic repair
        repaired = repair_frontmatter(draft_text, errors, schema)
        if repaired != draft_text:
            valid, errors = validate_draft(repaired, schema_path)
            if valid:
                return _write_draft(repaired, output_dir, response, attempt, repaired=True)

        last_errors = errors

    # All retries exhausted — write the best attempt with a warning
    return _write_draft(
        draft_text, output_dir, last_response, MAX_RETRIES,
        validation_errors=last_errors,
    )


def _write_draft(
    text: str,
    output_dir: str,
    response: LLMResponse | None,
    attempt: int,
    repaired: bool = False,
    validation_errors: list[str] | None = None,
) -> dict:
    """Write draft to output directory and return summary."""
    out_path = Path(output_dir)
    out_path.mkdir(parents=True, exist_ok=True)

    # Derive filename from frontmatter date and title
    slug = _derive_slug(text)
    today = date.today().isoformat()
    filename = f"{today}-{slug}.md"
    filepath = out_path / filename
    filepath.write_text(text, encoding="utf-8")

    summary: dict = {
        "pipeline_version": PIPELINE_VERSION,
        "output_path": str(filepath),
        "filename": filename,
        "attempt": attempt + 1,
        "repaired": repaired,
        "valid": validation_errors is None,
    }
    if validation_errors:
        summary["validation_errors"] = validation_errors
    if response:
        summary["llm"] = {
            "provider": response.provider,
            "model": response.model,
            "input_tokens": response.input_tokens,
            "output_tokens": response.output_tokens,
        }
    return summary


def _derive_slug(text: str) -> str:
    """Derive a URL slug from the essay title in frontmatter."""
    if not text.startswith("---"):
        return "untitled"
    parts = text.split("---", 2)
    if len(parts) < 3:
        return "untitled"
    try:
        fm = yaml.safe_load(parts[1])
    except yaml.YAMLError:
        return "untitled"

    title = fm.get("title", "untitled") if fm else "untitled"
    # Slugify
    slug = title.lower().strip()
    slug = re.sub(r"[^a-z0-9\s-]", "", slug)
    slug = re.sub(r"[\s_]+", "-", slug)
    slug = re.sub(r"-+", "-", slug).strip("-")
    return slug[:60] if slug else "untitled"


def main():
    parser = argparse.ArgumentParser(
        description="Generate an essay draft using LLM"
    )
    parser.add_argument(
        "--suggestions", required=True,
        help="Path to topic-suggestions.json",
    )
    parser.add_argument(
        "--suggestion-index", type=int, default=0,
        help="Index into the suggestions list (default: 0)",
    )
    parser.add_argument(
        "--template-dir", required=True,
        help="Path to editorial-standards/templates/",
    )
    parser.add_argument(
        "--schema", required=True,
        help="Path to frontmatter-schema.yaml",
    )
    parser.add_argument(
        "--rubric", required=True,
        help="Path to quality-rubric.yaml",
    )
    parser.add_argument(
        "--tag-governance", required=True,
        help="Path to tag-governance.yaml",
    )
    parser.add_argument(
        "--category-taxonomy", required=True,
        help="Path to category-taxonomy.yaml",
    )
    parser.add_argument(
        "--posts-dir", required=True,
        help="Path to existing _posts/ directory",
    )
    parser.add_argument(
        "--output-dir", required=True,
        help="Output directory for draft essay",
    )
    parser.add_argument(
        "--sprint-narrative", default=None,
        help="Path to sprint-narrative-draft.md (optional context)",
    )
    parser.add_argument(
        "--provider", default=None,
        help="LLM provider override (default: auto-detect from env)",
    )
    args = parser.parse_args()

    # Load suggestions
    with open(args.suggestions) as f:
        suggestions_data = json.load(f)

    suggestions = suggestions_data.get("suggestions", [])
    if not suggestions:
        print("No suggestions available", file=sys.stderr)
        sys.exit(1)

    idx = args.suggestion_index
    if idx >= len(suggestions):
        print(
            f"Suggestion index {idx} out of range (max {len(suggestions) - 1})",
            file=sys.stderr,
        )
        sys.exit(1)

    suggestion = suggestions[idx]

    # Build optional context
    context = {}
    if args.sprint_narrative and Path(args.sprint_narrative).exists():
        context["sprint_narrative"] = Path(args.sprint_narrative).read_text(
            encoding="utf-8"
        )

    # Run pipeline
    try:
        result = draft_essay(
            suggestion=suggestion,
            template_dir=args.template_dir,
            schema_path=args.schema,
            rubric_path=args.rubric,
            tag_governance_path=args.tag_governance,
            category_taxonomy_path=args.category_taxonomy,
            posts_dir=args.posts_dir,
            output_dir=args.output_dir,
            context=context if context else None,
            provider=args.provider,
        )
    except ValueError as e:
        print(f"Error: {e}", file=sys.stderr)
        sys.exit(1)

    # Report
    if result.get("valid"):
        status = "VALID"
    else:
        status = f"INVALID ({len(result.get('validation_errors', []))} errors)"

    print(f"Draft: {result['output_path']}")
    print(f"Status: {status}")
    print(f"Attempt: {result['attempt']}")
    if result.get("repaired"):
        print("Frontmatter was auto-repaired")
    if result.get("llm"):
        llm = result["llm"]
        print(f"LLM: {llm['provider']}/{llm['model']} "
              f"({llm['input_tokens']}+{llm['output_tokens']} tokens)")

    sys.exit(0 if result.get("valid") else 1)


if __name__ == "__main__":
    main()
