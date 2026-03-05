"""Tests for the schema loader."""

from pathlib import Path

import pytest

from src.schema_loader import load_schema

# Real schema path (relative to essay-pipeline root, resolved via test location)
SCHEMA_PATH = Path(__file__).parent.parent.parent / "editorial-standards" / "schemas" / "frontmatter-schema.yaml"


class TestLoadSchema:
    def test_loads_valid_schema(self):
        schema = load_schema(str(SCHEMA_PATH))
        assert "required_fields" in schema
        assert isinstance(schema["required_fields"], dict)

    def test_file_not_found(self, tmp_path):
        with pytest.raises(FileNotFoundError):
            load_schema(str(tmp_path / "nonexistent.yaml"))

    def test_missing_required_fields_key(self, tmp_path):
        bad_schema = tmp_path / "bad-schema.yaml"
        bad_schema.write_text("some_key: some_value\n")
        with pytest.raises(ValueError, match="required_fields"):
            load_schema(str(bad_schema))

    def test_empty_file_raises(self, tmp_path):
        empty = tmp_path / "empty.yaml"
        empty.write_text("")
        with pytest.raises(ValueError, match="required_fields"):
            load_schema(str(empty))

    def test_returns_dict_with_fields(self):
        schema = load_schema(str(SCHEMA_PATH))
        assert isinstance(schema, dict)
        fields = schema["required_fields"]
        # The schema should define at least the core required fields
        assert "title" in fields
        assert "layout" in fields
        assert "date" in fields
