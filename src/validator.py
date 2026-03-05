"""Frontmatter schema validator for ORGAN-V content.

Reads YAML frontmatter from Markdown files, validates each field against
a schema (essay or log) defined in editorial-standards/schemas/.

Supports dual content types:
  - essay (default): validates against frontmatter-schema.yaml
  - log: validates against log-schema.yaml (lighter requirements)

CLI: python -m src.validator --posts-dir _posts/ --schema path/to/frontmatter-schema.yaml
     python -m src.validator --posts-dir _logs/ --schema path/to/log-schema.yaml --content-type log
"""

import argparse
import re
import sys
from pathlib import Path

import yaml

from .schema_loader import load_schema


def _compute_body_word_count(text: str) -> int:
    """Compute the essay body word count using the indexer's normalization rules."""
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


def _expected_reading_time(word_count: int) -> str:
    """Derive canonical reading_time from word_count."""
    minutes = max(1, round(word_count / 250))
    return f"{minutes} min"


def extract_frontmatter(filepath: Path) -> dict | None:
    """Extract YAML frontmatter from a Markdown file.

    Returns None if no frontmatter delimiters found.
    """
    text = filepath.read_text(encoding="utf-8")
    if not text.startswith("---"):
        return None
    parts = text.split("---", 2)
    if len(parts) < 3:
        return None
    try:
        return yaml.safe_load(parts[1])
    except yaml.YAMLError:
        return None


def validate_field(field_name: str, value, spec: dict) -> list[str]:
    """Validate a single field value against its schema spec.

    Returns a list of error strings (empty if valid).
    """
    errors = []
    field_type = spec.get("type")

    if field_type == "string":
        if not isinstance(value, str):
            return [f"expected string, got {type(value).__name__}"]
        if "enum" in spec and value not in spec["enum"]:
            errors.append(f"must be one of {spec['enum']}, got '{value}'")
        if "min_length" in spec and len(value) < spec["min_length"]:
            errors.append(f"too short ({len(value)} chars, min {spec['min_length']})")
        if "max_length" in spec and len(value) > spec["max_length"]:
            errors.append(f"too long ({len(value)} chars, max {spec['max_length']})")
        if "pattern" in spec and not re.match(spec["pattern"], value):
            errors.append(f"does not match pattern {spec['pattern']}")

    elif field_type == "integer":
        if not isinstance(value, int) or isinstance(value, bool):
            return [f"expected integer, got {type(value).__name__}"]
        if "min" in spec and value < spec["min"]:
            errors.append(f"value {value} below minimum {spec['min']}")

    elif field_type == "list":
        if not isinstance(value, list):
            return [f"expected list, got {type(value).__name__}"]
        if "min_items" in spec and len(value) < spec["min_items"]:
            errors.append(f"too few items ({len(value)}, min {spec['min_items']})")
        if "max_items" in spec and len(value) > spec["max_items"]:
            errors.append(f"too many items ({len(value)}, max {spec['max_items']})")
        item_type = spec.get("item_type")
        item_pattern = spec.get("item_pattern")
        for i, item in enumerate(value):
            if item_type == "string" and not isinstance(item, str):
                errors.append(f"item [{i}] expected string, got {type(item).__name__}")
            elif item_pattern and isinstance(item, str) and not re.match(item_pattern, item):
                errors.append(f"item [{i}] '{item}' does not match pattern {item_pattern}")

    return errors


def validate_entry(filepath: Path, schema: dict) -> list[str]:
    """Validate a single content entry's frontmatter against the schema.

    Works for both essays and logs — the schema determines which fields
    are required vs optional.

    Returns a list of error strings (empty if valid).
    """
    fm = extract_frontmatter(filepath)
    if fm is None:
        return [f"{filepath.name}: no valid frontmatter found"]

    errors = []
    required = schema["required_fields"]

    for field_name, spec in required.items():
        if field_name not in fm:
            errors.append(f"{filepath.name}: missing required field '{field_name}'")
            continue
        field_errors = validate_field(field_name, fm[field_name], spec)
        for err in field_errors:
            errors.append(f"{filepath.name}: field '{field_name}' — {err}")

    # Validate optional fields if present
    optional = schema.get("optional_fields", {})
    for field_name, spec in optional.items():
        if field_name in fm:
            field_errors = validate_field(field_name, fm[field_name], spec)
            for err in field_errors:
                errors.append(f"{filepath.name}: field '{field_name}' — {err}")

    # Cross-field integrity checks for word_count/reading_time coherence
    if isinstance(fm, dict) and isinstance(fm.get("word_count"), int) and not isinstance(fm.get("word_count"), bool):
        declared_word_count = fm["word_count"]
        word_count_policy = fm.get("word_count_policy", "computed")
        if word_count_policy not in {"computed", "external"}:
            errors.append(
                f"{filepath.name}: field 'word_count_policy' — must be one of ['computed', 'external'], "
                f"got '{word_count_policy}'"
            )
        else:
            if word_count_policy == "external":
                reason = fm.get("word_count_override_reason")
                if not isinstance(reason, str) or len(reason.strip()) < 20:
                    errors.append(
                        f"{filepath.name}: field 'word_count_override_reason' — required (min 20 chars) "
                        "when word_count_policy is 'external'"
                    )
            else:
                computed_word_count = _compute_body_word_count(filepath.read_text(encoding="utf-8"))
                if declared_word_count != computed_word_count:
                    errors.append(
                        f"{filepath.name}: field 'word_count' — declared {declared_word_count} does not "
                        f"match computed body word count {computed_word_count}"
                    )
                reading_time = fm.get("reading_time")
                expected = _expected_reading_time(computed_word_count)
                if isinstance(reading_time, str) and reading_time != expected:
                    errors.append(
                        f"{filepath.name}: field 'reading_time' — declared '{reading_time}' does not "
                        f"match expected '{expected}' for computed word_count {computed_word_count}"
                    )

    return errors


# Backward-compatible alias
validate_essay = validate_entry


def validate_all(posts_dir: str, schema_path: str) -> list[str]:
    """Validate all .md files in posts_dir against the schema.

    Returns a list of all error strings across all files.
    """
    schema = load_schema(schema_path)
    posts = sorted(Path(posts_dir).glob("*.md"))

    if not posts:
        return [f"No .md files found in {posts_dir}"]

    all_errors = []
    for post in posts:
        all_errors.extend(validate_entry(post, schema))

    return all_errors


def main():
    parser = argparse.ArgumentParser(description="Validate content frontmatter")
    parser.add_argument("--posts-dir", required=True, help="Path to content directory (_posts/ or _logs/)")
    parser.add_argument("--schema", required=True, help="Path to schema YAML file")
    parser.add_argument(
        "--content-type", choices=["essay", "log"], default="essay",
        help="Content type being validated (default: essay)"
    )
    args = parser.parse_args()

    errors = validate_all(args.posts_dir, args.schema)

    label = "essays" if args.content_type == "essay" else "logs"
    if errors:
        print(f"FAILED — {len(errors)} error(s):\n")
        for err in errors:
            print(f"  {err}")
        sys.exit(1)
    else:
        count = len(list(Path(args.posts_dir).glob("*.md")))
        print(f"PASSED — {count} {label} validated, 0 errors")
        sys.exit(0)


if __name__ == "__main__":
    main()
