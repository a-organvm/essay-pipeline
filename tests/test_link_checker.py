"""Tests for src.link_checker — URL extraction, checking, and reporting."""

import json
from pathlib import Path
from unittest.mock import patch

import httpx
import pytest

from src.link_checker import (
    UrlEntry,
    UrlResult,
    Report,
    check_url,
    extract_urls,
    generate_report,
    check_all,
    check_local_link,
    main,
)


class TestCheckLocalLink:
    def test_valid_link(self, tmp_path):
        posts_dir = tmp_path / "posts"
        posts_dir.mkdir()
        (posts_dir / "2026-03-01-test-slug.md").write_text("...")

        # Valid format
        assert check_local_link("/essays/cat/test-slug/", posts_dir).status == "ok"
        assert check_local_link("/essays/cat/test-slug.html", posts_dir).status == "ok"

    def test_invalid_slug(self, tmp_path):
        posts_dir = tmp_path / "posts"
        posts_dir.mkdir()
        assert check_local_link("/essays/cat/missing/", posts_dir).status == "broken"

    def test_invalid_format(self, tmp_path):
        posts_dir = tmp_path / "posts"
        posts_dir.mkdir()
        assert check_local_link("not-an-internal-link", posts_dir).status == "error"
        assert check_local_link("/too/short", posts_dir).status == "error"


# --- Fixtures --------------------------------------------------------------

FIXTURE_DIR = Path(__file__).parent / "fixtures"


@pytest.fixture
def md_with_links(tmp_path: Path) -> Path:
    """Create a Markdown file with various link types."""
    content = """\
---
layout: essay
title: "Test Essay"
references:
  - "[1] Author, [*Book Title*](https://example.com/book), Publisher, 2020."
  - "[2] Author, \"[Article](https://example.com/article),\" Site, 2021."
---

# Test Essay

See [Example](https://example.com) and [Other](https://other.com/page).

Also see [Wiki](<https://en.wikipedia.org/wiki/Test_(thing)>).

Internal ref [[1]](#ref-1) and [local]({{ site.baseurl }}{% post_url 2026-01-01-test %}).

Contact us at [email](mailto:test@example.com).
"""
    fp = tmp_path / "test-essay.md"
    fp.write_text(content, encoding="utf-8")
    return fp


@pytest.fixture
def md_no_links(tmp_path: Path) -> Path:
    """Create a Markdown file with no external links."""
    content = """\
---
layout: essay
title: "No Links"
---

# No Links

Just plain text with no links at all.
"""
    fp = tmp_path / "no-links.md"
    fp.write_text(content, encoding="utf-8")
    return fp


# --- extract_urls tests ----------------------------------------------------


def test_extract_urls_markdown_links(md_with_links: Path) -> None:
    """Finds [text](url) links in body and references."""
    entries = extract_urls(md_with_links)
    urls = {e.url for e in entries}
    assert "https://example.com/book" in urls
    assert "https://example.com/article" in urls
    assert "https://example.com" in urls
    assert "https://other.com/page" in urls


def test_extract_urls_angle_bracket(md_with_links: Path) -> None:
    """Finds [text](<url>) links for URLs with parentheses."""
    entries = extract_urls(md_with_links)
    urls = {e.url for e in entries}
    assert "https://en.wikipedia.org/wiki/Test_(thing)" in urls


def test_extract_urls_skips_anchors(md_with_links: Path) -> None:
    """Ignores #ref-N internal anchors."""
    entries = extract_urls(md_with_links)
    urls = {e.url for e in entries}
    assert not any(u.startswith("#") for u in urls)


def test_extract_urls_skips_mailto(md_with_links: Path) -> None:
    """Ignores mailto: links."""
    entries = extract_urls(md_with_links)
    urls = {e.url for e in entries}
    assert not any(u.startswith("mailto:") for u in urls)


def test_extract_urls_skips_post_url(md_with_links: Path) -> None:
    """Ignores Jekyll post_url liquid tags."""
    entries = extract_urls(md_with_links)
    urls = {e.url for e in entries}
    assert not any("post_url" in u for u in urls)
    assert not any(u.startswith("{{") for u in urls)


def test_extract_urls_no_links(md_no_links: Path) -> None:
    """Returns empty list for a file with no links."""
    entries = extract_urls(md_no_links)
    assert entries == []


def test_extract_urls_has_line_numbers(md_with_links: Path) -> None:
    """Each entry has a positive line number."""
    entries = extract_urls(md_with_links)
    assert all(e.line > 0 for e in entries)


def test_extract_urls_deduplicates(tmp_path: Path) -> None:
    """Same URL appearing twice in a file produces one entry."""
    content = """\
---
layout: essay
---

[A](https://example.com/dup) and [B](https://example.com/dup).
"""
    fp = tmp_path / "dup.md"
    fp.write_text(content, encoding="utf-8")
    entries = extract_urls(fp)
    urls = [e.url for e in entries]
    assert urls.count("https://example.com/dup") == 1


# --- check_url tests (mocked HTTP) ----------------------------------------


def _mock_transport(handler):
    """Build an httpx.Client with a mock transport."""
    return httpx.Client(
        transport=httpx.MockTransport(handler),
        follow_redirects=False,
    )


def test_check_url_ok() -> None:
    """200 response → status 'ok'."""

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200)

    client = _mock_transport(handler)
    result = check_url("https://example.com", client=client)
    assert result.status == "ok"
    assert result.status_code == 200


def test_check_url_redirect() -> None:
    """301 response → status 'redirect'."""

    def handler(request: httpx.Request) -> httpx.Response:
        if "old" in str(request.url):
            return httpx.Response(301, headers={"location": "https://example.com/new"})
        return httpx.Response(200)

    client = _mock_transport(handler)
    result = check_url("https://example.com/old", client=client)
    assert result.status == "redirect"
    assert result.status_code == 301
    assert result.redirect_url == "https://example.com/new"


def test_check_url_broken() -> None:
    """404 response → status 'broken'."""

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(404)

    client = _mock_transport(handler)
    result = check_url("https://example.com/gone", retries=0, client=client)
    assert result.status == "broken"
    assert result.status_code == 404


def test_check_url_timeout() -> None:
    """Timeout → status 'timeout'."""

    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ReadTimeout("timed out")

    client = _mock_transport(handler)
    result = check_url("https://example.com/slow", retries=0, client=client)
    assert result.status == "timeout"
    assert result.error == "timeout"


def test_check_url_head_fallback_to_get() -> None:
    """405 on HEAD → falls back to GET."""
    call_count = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        call_count["n"] += 1
        if request.method == "HEAD":
            return httpx.Response(405)
        return httpx.Response(200)

    client = _mock_transport(handler)
    result = check_url("https://example.com/head-reject", client=client)
    assert result.status == "ok"
    assert call_count["n"] == 2  # HEAD then GET


def test_check_url_retries_on_failure() -> None:
    """Retries on transient errors."""
    attempts = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        attempts["n"] += 1
        if attempts["n"] < 3:
            return httpx.Response(500)
        return httpx.Response(200)

    client = _mock_transport(handler)
    result = check_url("https://example.com/flaky", retries=2, client=client)
    assert result.status == "ok"
    assert attempts["n"] == 3


# --- generate_report tests -------------------------------------------------


def test_generate_report_json_structure() -> None:
    """Report has expected top-level keys and summary counts."""
    report = Report(
        entries=[
            UrlEntry(
                url="https://a.com", file="a.md", line=1, context="[A](https://a.com)"
            ),
            UrlEntry(
                url="https://b.com", file="b.md", line=5, context="[B](https://b.com)"
            ),
            UrlEntry(
                url="https://c.com", file="c.md", line=10, context="[C](https://c.com)"
            ),
        ],
        results={
            "https://a.com": UrlResult(
                url="https://a.com", status="ok", status_code=200
            ),
            "https://b.com": UrlResult(
                url="https://b.com", status="broken", status_code=404, error="HTTP 404"
            ),
            "https://c.com": UrlResult(
                url="https://c.com",
                status="redirect",
                status_code=301,
                redirect_url="https://c.org",
            ),
        },
    )

    output = generate_report(report)
    assert output["summary"]["total"] == 3
    assert output["summary"]["ok"] == 1
    assert output["summary"]["broken"] == 1
    assert output["summary"]["redirect"] == 1
    assert len(output["broken"]) == 1
    assert output["broken"][0]["url"] == "https://b.com"
    assert len(output["redirects"]) == 1

    # Verify it's JSON-serializable
    json.dumps(output)


def test_generate_report_empty() -> None:
    """Empty report produces valid structure."""
    report = Report()
    output = generate_report(report)
    assert output["summary"]["total"] == 0
    assert output["broken"] == []
    assert output["redirects"] == []


# --- check_all integration test --------------------------------------------


def test_full_scan_fixture(tmp_path: Path) -> None:
    """Integration test: scan a directory with mock HTTP."""
    posts = tmp_path / "posts"
    posts.mkdir()

    (posts / "2026-01-01-test.md").write_text(
        """\
---
layout: essay
title: "Test"
references:
  - "[1] [Book](https://example.com/book), 2020."
---

See [Site](https://example.com).
""",
        encoding="utf-8",
    )

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200)

    mock_client = _mock_transport(handler)

    with patch("src.link_checker.httpx.Client") as mock_cls:
        mock_cls.return_value.__enter__ = lambda s: mock_client
        mock_cls.return_value.__exit__ = lambda s, *a: mock_client.close()

        report = check_all(posts_dir=posts, timeout=5, retries=0)

    assert len(report.entries) == 2
    assert all(r.status == "ok" for r in report.results.values())


def test_internal_only_mode(tmp_path: Path) -> None:
    """internal_only mode validates syntax without HTTP."""
    posts = tmp_path / "posts"
    posts.mkdir()

    (posts / "2026-01-01-test.md").write_text(
        """\
---
layout: essay
---

See [Site](https://example.com) and [Other](https://other.com).
""",
        encoding="utf-8",
    )

    report = check_all(posts_dir=posts, internal_only=True)
    assert len(report.results) == 2
    assert all(r.status == "ok" for r in report.results.values())


# --- Additional coverage tests ---------------------------------------------


def test_extract_urls_excludes_images(tmp_path: Path) -> None:
    """![alt](url) image links are NOT extracted."""
    content = """\
---
layout: essay
---

Here is an image: ![logo](https://example.com/logo.png).

And a real link: [Site](https://example.com).
"""
    fp = tmp_path / "images.md"
    fp.write_text(content, encoding="utf-8")
    entries = extract_urls(fp)
    urls = {e.url for e in entries}
    assert "https://example.com" in urls
    assert "https://example.com/logo.png" not in urls


def test_check_url_connection_error() -> None:
    """HTTP connection error → status 'error'."""

    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("Connection refused")

    client = _mock_transport(handler)
    result = check_url("https://example.com/down", retries=0, client=client)
    assert result.status == "error"
    assert result.error is not None


def test_check_all_with_logs_dir(tmp_path: Path) -> None:
    """check_all scans both posts_dir and logs_dir."""
    posts = tmp_path / "posts"
    posts.mkdir()
    logs = tmp_path / "logs"
    logs.mkdir()

    (posts / "2026-01-01-essay.md").write_text(
        """\
---
layout: essay
---

See [A](https://example.com/a).
""",
        encoding="utf-8",
    )

    (logs / "2026-01-02-log.md").write_text(
        """\
---
layout: log
---

See [B](https://example.com/b).
""",
        encoding="utf-8",
    )

    report = check_all(posts_dir=posts, logs_dir=logs, internal_only=True)
    urls = set(report.results.keys())
    assert "https://example.com/a" in urls
    assert "https://example.com/b" in urls
    assert len(report.results) == 2


def test_generate_report_all_status_types() -> None:
    """Report correctly categorizes timeout and error statuses in broken list."""
    report = Report(
        entries=[
            UrlEntry(
                url="https://a.com", file="a.md", line=1, context="[A](https://a.com)"
            ),
            UrlEntry(
                url="https://b.com", file="b.md", line=2, context="[B](https://b.com)"
            ),
            UrlEntry(
                url="https://c.com", file="c.md", line=3, context="[C](https://c.com)"
            ),
        ],
        results={
            "https://a.com": UrlResult(
                url="https://a.com", status="broken", status_code=404, error="HTTP 404"
            ),
            "https://b.com": UrlResult(
                url="https://b.com", status="timeout", error="timeout"
            ),
            "https://c.com": UrlResult(
                url="https://c.com", status="error", error="Connection refused"
            ),
        },
    )

    output = generate_report(report)
    assert output["summary"]["broken"] == 1
    assert output["summary"]["timeout"] == 1
    assert output["summary"]["error"] == 1
    # All three should appear in the broken list
    assert len(output["broken"]) == 3
    broken_urls = {item["url"] for item in output["broken"]}
    assert broken_urls == {"https://a.com", "https://b.com", "https://c.com"}


def test_cli_main_internal_only(tmp_path: Path) -> None:
    """CLI main() runs successfully with --internal-only."""
    posts = tmp_path / "posts"
    posts.mkdir()
    output_file = tmp_path / "report.json"

    (posts / "2026-01-01-test.md").write_text(
        """\
---
layout: essay
---

See [Site](https://example.com).
""",
        encoding="utf-8",
    )

    import sys

    original_argv = sys.argv
    try:
        sys.argv = [
            "link_checker",
            "--posts-dir",
            str(posts),
            "--output",
            str(output_file),
            "--internal-only",
        ]
        main()
    except SystemExit as e:
        assert e.code == 0 or e.code is None
    finally:
        sys.argv = original_argv

    assert output_file.exists()
    data = json.loads(output_file.read_text())
    assert data["summary"]["total"] == 1
    assert data["summary"]["ok"] == 1
