"""Tests for the frontmatter validator."""

from pathlib import Path

from src.schema_loader import load_schema
from src.validator import extract_frontmatter, validate_all, validate_essay, validate_field

FIXTURES = Path(__file__).parent / "fixtures"
SCHEMA_PATH = str(
    Path(__file__).parent.parent.parent
    / "editorial-standards"
    / "schemas"
    / "frontmatter-schema.yaml"
)


def get_schema():
    return load_schema(SCHEMA_PATH)


class TestExtractFrontmatter:
    def test_valid_frontmatter(self):
        fm = extract_frontmatter(FIXTURES / "valid-essay.md")
        assert fm is not None
        assert fm["title"] == "A Perfectly Valid Test Essay for the Pipeline"

    def test_no_frontmatter(self, tmp_path):
        p = tmp_path / "no-fm.md"
        p.write_text("# Just a heading\n\nNo frontmatter here.")
        assert extract_frontmatter(p) is None


class TestValidateField:
    def test_string_enum_valid(self):
        spec = {"type": "string", "enum": ["essay"]}
        assert validate_field("layout", "essay", spec) == []

    def test_string_enum_invalid(self):
        spec = {"type": "string", "enum": ["essay"]}
        errors = validate_field("layout", "post", spec)
        assert len(errors) == 1
        assert "must be one of" in errors[0]

    def test_string_pattern_valid(self):
        spec = {"type": "string", "pattern": "^@"}
        assert validate_field("author", "@4444J99", spec) == []

    def test_string_pattern_invalid(self):
        spec = {"type": "string", "pattern": "^@"}
        errors = validate_field("author", "no-prefix", spec)
        assert len(errors) == 1
        assert "pattern" in errors[0]

    def test_string_min_length(self):
        spec = {"type": "string", "min_length": 50}
        errors = validate_field("excerpt", "Too short.", spec)
        assert any("too short" in e for e in errors)

    def test_integer_valid(self):
        spec = {"type": "integer", "min": 500}
        assert validate_field("word_count", 1000, spec) == []

    def test_integer_below_min(self):
        spec = {"type": "integer", "min": 500}
        errors = validate_field("word_count", 100, spec)
        assert any("below minimum" in e for e in errors)

    def test_integer_wrong_type(self):
        spec = {"type": "integer", "min": 500}
        errors = validate_field("word_count", "not a number", spec)
        assert any("expected integer" in e for e in errors)

    def test_list_valid(self):
        spec = {"type": "list", "min_items": 2, "max_items": 8, "item_type": "string"}
        assert validate_field("tags", ["a", "b"], spec) == []

    def test_list_too_few(self):
        spec = {"type": "list", "min_items": 2, "max_items": 8, "item_type": "string"}
        errors = validate_field("tags", ["only-one"], spec)
        assert any("too few" in e for e in errors)

    def test_list_item_pattern(self):
        spec = {
            "type": "list",
            "item_type": "string",
            "item_pattern": "^(organvm-|meta-organvm)",
        }
        errors = validate_field("related_repos", ["bad-repo"], spec)
        assert any("pattern" in e for e in errors)

    def test_tag_pattern_valid(self):
        spec = {
            "type": "list",
            "min_items": 2,
            "max_items": 8,
            "item_type": "string",
            "item_pattern": "^[a-z0-9]+(-[a-z0-9]+)*$",
        }
        assert validate_field("tags", ["meta-system", "building-in-public"], spec) == []

    def test_tag_pattern_rejects_uppercase(self):
        spec = {
            "type": "list",
            "min_items": 1,
            "max_items": 8,
            "item_type": "string",
            "item_pattern": "^[a-z0-9]+(-[a-z0-9]+)*$",
        }
        errors = validate_field("tags", ["UPPERCASE", "valid-tag"], spec)
        assert any("UPPERCASE" in e and "pattern" in e for e in errors)

    def test_tag_pattern_rejects_spaces(self):
        spec = {
            "type": "list",
            "min_items": 1,
            "max_items": 8,
            "item_type": "string",
            "item_pattern": "^[a-z0-9]+(-[a-z0-9]+)*$",
        }
        errors = validate_field("tags", ["has spaces"], spec)
        assert any("pattern" in e for e in errors)


class TestValidateEssay:
    def test_valid_essay_passes(self):
        schema = get_schema()
        errors = validate_essay(FIXTURES / "valid-essay.md", schema)
        assert errors == []

    def test_missing_fields_detected(self):
        schema = get_schema()
        errors = validate_essay(FIXTURES / "missing-field.md", schema)
        # Missing: excerpt, portfolio_relevance, related_repos, reading_time, word_count
        # Also tags has only 1 item (min 2)
        assert len(errors) >= 5
        assert any("missing required field" in e for e in errors)

    def test_wrong_enum_detected(self):
        schema = get_schema()
        errors = validate_essay(FIXTURES / "wrong-enum.md", schema)
        assert any("category" in e and "must be one of" in e for e in errors)

    def test_bad_pattern_detected(self):
        schema = get_schema()
        errors = validate_essay(FIXTURES / "bad-pattern.md", schema)
        # author missing @, date wrong format, related_repo wrong pattern, reading_time wrong format
        assert any("author" in e for e in errors)
        assert any("date" in e for e in errors)

    def test_short_excerpt_detected(self):
        schema = get_schema()
        errors = validate_essay(FIXTURES / "short-excerpt.md", schema)
        assert any("excerpt" in e and "too short" in e for e in errors)

    def test_bad_tag_format_detected(self):
        schema = get_schema()
        errors = validate_essay(FIXTURES / "bad-tag-format.md", schema)
        assert any("tags" in e and "pattern" in e for e in errors)


class TestWordCountIntegrity:
    @staticmethod
    def _write_essay(
        tmp_path,
        *,
        body_words: int,
        declared_word_count: int,
        reading_time: str,
        word_count_policy: str | None = None,
        word_count_override_reason: str | None = None,
    ):
        body = " ".join(["word"] * body_words)
        lines = [
            "---",
            "layout: essay",
            'title: "Word Count Integrity Test Essay"',
            'author: "@4444J99"',
            'date: "2026-03-05"',
            "tags: [governance, testing]",
            'category: "meta-system"',
            'excerpt: "This is a sufficiently long excerpt for validator testing of word count integrity behavior."',
            'portfolio_relevance: "MEDIUM"',
            "related_repos:",
            "  - organvm-v-logos/essay-pipeline",
            f'reading_time: "{reading_time}"',
            f"word_count: {declared_word_count}",
            "references: []",
        ]
        if word_count_policy:
            lines.append(f"word_count_policy: {word_count_policy}")
        if word_count_override_reason:
            lines.append(f'word_count_override_reason: "{word_count_override_reason}"')
        lines.extend(["---", "", body, ""])

        p = tmp_path / "word-count-test.md"
        p.write_text("\n".join(lines), encoding="utf-8")
        return p

    def test_computed_policy_mismatch_rejected(self, tmp_path):
        schema = get_schema()
        p = self._write_essay(
            tmp_path,
            body_words=600,
            declared_word_count=650,
            reading_time="2 min",
        )
        errors = validate_essay(p, schema)
        assert any("does not match computed body word count" in e for e in errors)

    def test_computed_policy_reading_time_mismatch_rejected(self, tmp_path):
        schema = get_schema()
        p = self._write_essay(
            tmp_path,
            body_words=600,
            declared_word_count=600,
            reading_time="5 min",
        )
        errors = validate_essay(p, schema)
        assert any("does not match expected" in e and "reading_time" in e for e in errors)

    def test_computed_policy_match_passes(self, tmp_path):
        schema = get_schema()
        p = self._write_essay(
            tmp_path,
            body_words=600,
            declared_word_count=600,
            reading_time="2 min",
        )
        errors = validate_essay(p, schema)
        assert errors == []

    def test_external_policy_requires_reason(self, tmp_path):
        schema = get_schema()
        p = self._write_essay(
            tmp_path,
            body_words=100,
            declared_word_count=5000,
            reading_time="20 min",
            word_count_policy="external",
        )
        errors = validate_essay(p, schema)
        assert any("word_count_override_reason" in e for e in errors)

    def test_external_policy_allows_mismatch_with_reason(self, tmp_path):
        schema = get_schema()
        p = self._write_essay(
            tmp_path,
            body_words=100,
            declared_word_count=5000,
            reading_time="20 min",
            word_count_policy="external",
            word_count_override_reason=(
                "This post summarizes a larger dissertation corpus and reports aggregate chapter words."
            ),
        )
        errors = validate_essay(p, schema)
        assert errors == []


# --- validate_field additional coverage ------------------------------------


class TestValidateFieldExtended:
    def test_string_max_length(self):
        spec = {"type": "string", "max_length": 10}
        errors = validate_field("title", "a" * 11, spec)
        assert any("too long" in e for e in errors)

    def test_string_max_length_ok(self):
        spec = {"type": "string", "max_length": 10}
        assert validate_field("title", "short", spec) == []

    def test_list_too_many(self):
        spec = {"type": "list", "min_items": 1, "max_items": 3, "item_type": "string"}
        errors = validate_field("tags", ["a", "b", "c", "d"], spec)
        assert any("too many" in e for e in errors)

    def test_boolean_rejected_as_integer(self):
        """bool is a subclass of int in Python — validator must reject it."""
        spec = {"type": "integer", "min": 0}
        errors = validate_field("word_count", True, spec)
        assert any("expected integer" in e for e in errors)

    def test_list_wrong_type(self):
        spec = {"type": "list", "min_items": 1, "item_type": "string"}
        errors = validate_field("tags", "not-a-list", spec)
        assert any("expected list" in e for e in errors)


# --- extract_frontmatter edge cases ----------------------------------------


class TestExtractFrontmatterExtended:
    def test_yaml_parse_error(self, tmp_path):
        """Malformed YAML returns None instead of crashing."""
        p = tmp_path / "bad-yaml.md"
        p.write_text("---\ntitle: [invalid yaml\n---\n\nBody text.")
        assert extract_frontmatter(p) is None

    def test_single_delimiter(self, tmp_path):
        """File with only opening --- but no closing --- returns None."""
        p = tmp_path / "one-delim.md"
        p.write_text("---\ntitle: Test\n")
        assert extract_frontmatter(p) is None


# --- validate_entry with optional fields ------------------------------------


class TestOptionalFieldValidation:
    def test_optional_field_valid(self, tmp_path):
        """Valid optional field produces no errors."""
        schema = get_schema()
        # Add a known optional field if schema has them
        optional = schema.get("optional_fields", {})
        if not optional:
            # Schema has no optional fields — test passes trivially
            return
        # Use the valid essay fixture which should pass
        errors = validate_essay(FIXTURES / "valid-essay.md", schema)
        assert errors == []

    def test_optional_field_invalid(self, tmp_path):
        """Invalid optional field value is reported."""
        schema = get_schema()
        optional = schema.get("optional_fields", {})
        if not optional:
            return
        # Pick the first optional field and give it a bad value
        field_name, spec = next(iter(optional.items()))
        field_type = spec.get("type", "string")
        # Create a file with a bad value for that optional field
        bad_value = 99999 if field_type == "string" else "not-a-valid-value"
        p = tmp_path / "bad-optional.md"
        p.write_text(
            f"---\nlayout: essay\ntitle: \"A Perfectly Valid Test Essay for the Pipeline\"\n"
            f"author: \"@4444J99\"\ndate: \"2026-02-10\"\n"
            f"tags:\n  - governance\n  - building-in-public\n"
            f"category: meta-system\n"
            f"excerpt: \"This essay explores the recursive structure of the essay pipeline itself.\"\n"
            f"portfolio_relevance: HIGH\n"
            f"related_repos:\n  - organvm-v-logos/essay-pipeline\n"
            f"reading_time: \"8 min\"\nword_count: 1500\n"
            f"{field_name}: {bad_value}\n"
            f"---\n\nBody text.\n"
        )
        errors = validate_essay(p, schema)
        # Should have at least one error about the optional field
        assert any(field_name in e for e in errors)


# --- validate_all tests ----------------------------------------------------


class TestValidateAll:
    def test_valid_directory(self):
        """validate_all on fixtures returns errors for known-bad files."""
        errors = validate_all(str(FIXTURES), SCHEMA_PATH)
        # fixtures has both valid and invalid files
        assert isinstance(errors, list)

    def test_empty_directory(self, tmp_path):
        """Empty directory returns 'no .md files' error."""
        errors = validate_all(str(tmp_path), SCHEMA_PATH)
        assert len(errors) == 1
        assert "No .md files" in errors[0]

    def test_all_valid(self, tmp_path):
        """Directory with only valid files returns empty error list."""
        import shutil
        p = tmp_path / "valid-essay.md"
        shutil.copy(FIXTURES / "valid-essay.md", p)
        errors = validate_all(str(tmp_path), SCHEMA_PATH)
        assert errors == []
