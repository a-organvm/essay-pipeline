"""Autonomous link checker for ORGAN-V content.

Extracts URLs from Markdown body text and YAML reference strings,
checks each unique URL via HTTP, and produces a JSON report.

CLI: python -m src.link_checker --posts-dir _posts/ --output link-report.json
"""

import argparse
import json
import re
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

import httpx


# --- Data types -----------------------------------------------------------


@dataclass
class UrlEntry:
    """A URL found in a source file."""

    url: str
    file: str
    line: int
    context: str  # surrounding text snippet


@dataclass
class UrlResult:
    """Result of checking a single URL."""

    url: str
    status: str  # ok | redirect | broken | timeout | error
    status_code: int | None = None
    redirect_url: str | None = None
    error: str | None = None


@dataclass
class Report:
    """Full link-check report."""

    entries: list[UrlEntry] = field(default_factory=list)
    results: dict[str, UrlResult] = field(default_factory=dict)


# --- URL extraction -------------------------------------------------------

# Matches [text](url) but not ![image](url)
_MD_LINK_RE = re.compile(r"(?<!!)\[([^\]]+)\]\(([^)]+)\)")
# Matches [text](<url>) for URLs with parens
_MD_LINK_ANGLE_RE = re.compile(r"(?<!!)\[([^\]]+)\]\(<([^>]+)>\)")
# Jekyll post_url tag
_POST_URL_RE = re.compile(r"\{%\s*post_url\s+")

_SKIP_SCHEMES = {"mailto", "tel", "javascript", "data"}


def _should_skip(url: str) -> bool:
    """Return True if the URL should not be checked."""
    if url.startswith("#"):
        return True
    if _POST_URL_RE.search(url):
        return True
    if url.startswith("{{") or url.startswith("{%"):
        return True
    parsed = urlparse(url)
    if parsed.scheme in _SKIP_SCHEMES:
        return True
    if not parsed.scheme and not parsed.netloc:
        # Relative path or anchor — skip
        return True
    return False


def extract_urls(filepath: Path) -> list[UrlEntry]:
    """Extract all checkable URLs from a Markdown file.

    Parses both body text and YAML frontmatter references for
    markdown-style links. Skips internal anchors, post_url tags,
    mailto: links, and other non-HTTP URLs.
    """
    text = filepath.read_text(encoding="utf-8")
    filename = str(filepath)
    entries: list[UrlEntry] = []
    seen_urls: set[str] = set()

    lines = text.split("\n")
    for line_num, line in enumerate(lines, start=1):
        # Check angle-bracket links first (higher priority for URLs with parens)
        for match in _MD_LINK_ANGLE_RE.finditer(line):
            url = match.group(2).strip()
            if not _should_skip(url) and url not in seen_urls:
                seen_urls.add(url)
                entries.append(
                    UrlEntry(
                        url=url,
                        file=filename,
                        line=line_num,
                        context=line.strip()[:120],
                    )
                )

        # Then standard markdown links
        for match in _MD_LINK_RE.finditer(line):
            raw = match.group(2).strip()
            # Skip if this was already caught by angle-bracket pattern
            if raw.startswith("<"):
                continue
            url = raw
            if not _should_skip(url) and url not in seen_urls:
                seen_urls.add(url)
                entries.append(
                    UrlEntry(
                        url=url,
                        file=filename,
                        line=line_num,
                        context=line.strip()[:120],
                    )
                )

    return entries


# --- URL checking ----------------------------------------------------------

_RATE_LIMIT_DELAY = 0.5  # seconds between requests to the same domain
_last_request_time: dict[str, float] = {}


def _rate_limit(url: str) -> None:
    """Sleep if necessary to avoid hammering the same domain."""
    domain = urlparse(url).netloc
    now = time.monotonic()
    last = _last_request_time.get(domain, 0.0)
    wait = _RATE_LIMIT_DELAY - (now - last)
    if wait > 0:
        time.sleep(wait)
    _last_request_time[domain] = time.monotonic()


_DEFAULT_HEADERS = {
    "User-Agent": "essay-pipeline-link-checker/1.0 (+https://github.com/organvm-v-logos/essay-pipeline)",
    "Accept": "*/*",
}


def check_url(
    url: str,
    timeout: float = 10.0,
    retries: int = 2,
    client: httpx.Client | None = None,
) -> UrlResult:
    """Check a single URL via HTTP HEAD (with GET fallback).

    Returns a UrlResult with status: ok, redirect, broken, timeout, or error.
    """
    own_client = client is None
    if own_client:
        client = httpx.Client(
            headers=_DEFAULT_HEADERS,
            follow_redirects=False,
            timeout=timeout,
        )

    try:
        return _check_url_inner(url, timeout, retries, client)
    finally:
        if own_client:
            client.close()


def _check_url_inner(
    url: str,
    timeout: float,
    retries: int,
    client: httpx.Client,
) -> UrlResult:
    """Inner implementation with retry logic."""
    last_error: str | None = None

    for attempt in range(1 + retries):
        try:
            _rate_limit(url)

            # Try HEAD first
            resp = client.request("HEAD", url, timeout=timeout)

            # Some servers reject HEAD — fall back to GET
            if resp.status_code == 405 or resp.status_code == 403:
                _rate_limit(url)
                resp = client.request("GET", url, timeout=timeout)

            # Handle redirects
            if resp.status_code in (301, 302, 307, 308):
                redirect_url = resp.headers.get("location", "")
                # Follow one redirect to verify target
                if redirect_url:
                    try:
                        _rate_limit(redirect_url)
                        target_resp = client.request(
                            "HEAD", redirect_url, timeout=timeout
                        )
                        if target_resp.status_code < 400:
                            return UrlResult(
                                url=url,
                                status="redirect",
                                status_code=resp.status_code,
                                redirect_url=redirect_url,
                            )
                    except (httpx.HTTPError, httpx.TimeoutException):
                        pass
                return UrlResult(
                    url=url,
                    status="redirect",
                    status_code=resp.status_code,
                    redirect_url=redirect_url,
                )

            if resp.status_code < 400:
                return UrlResult(url=url, status="ok", status_code=resp.status_code)

            if resp.status_code >= 400:
                last_error = f"HTTP {resp.status_code}"
                if attempt < retries:
                    continue
                return UrlResult(
                    url=url,
                    status="broken",
                    status_code=resp.status_code,
                    error=last_error,
                )

        except httpx.TimeoutException:
            last_error = "timeout"
            if attempt < retries:
                continue
            return UrlResult(url=url, status="timeout", error="timeout")

        except httpx.HTTPError as exc:
            last_error = str(exc)
            if attempt < retries:
                continue
            return UrlResult(url=url, status="error", error=last_error)

    return UrlResult(url=url, status="error", error=last_error or "unknown")


# --- Full scan -------------------------------------------------------------


def check_local_link(url: str, posts_dir: Path) -> UrlResult:
    """Validate a local internal link against the posts directory."""
    if not url.startswith("/"):
        return UrlResult(url=url, status="error", error="invalid internal link format")

    # Normalize: strip .html, leading/trailing slashes
    path = url.strip("/").replace(".html", "")
    parts = path.split("/")

    # Expected: ["essays", category, slug]
    if len(parts) < 3 or parts[0] != "essays":
        return UrlResult(url=url, status="error", error="not a standard essay path")

    slug = parts[2]

    # Search posts_dir for *-slug.md
    matches = list(posts_dir.glob(f"*-{slug}.md"))
    if len(matches) > 0:
        return UrlResult(url=url, status="ok")
    else:
        return UrlResult(url=url, status="broken", error="File Not Found (Local)")


def check_all(
    posts_dir: Path,
    logs_dir: Path | None = None,
    timeout: float = 10.0,
    retries: int = 2,
    internal_only: bool = False,
) -> Report:
    """Scan all Markdown files, extract URLs, and check each unique one.

    If internal_only is True, only validates URL syntax without HTTP requests.
    """
    report = Report()

    # Collect all .md files
    md_files: list[Path] = sorted(posts_dir.glob("*.md"))
    if logs_dir and logs_dir.is_dir():
        md_files.extend(sorted(logs_dir.glob("*.md")))

    # Extract all URLs
    for filepath in md_files:
        entries = extract_urls(filepath)
        report.entries.extend(entries)

    # Deduplicate URLs for checking
    unique_urls: dict[str, UrlEntry] = {}
    for entry in report.entries:
        if entry.url not in unique_urls:
            unique_urls[entry.url] = entry

    # Create client for external checks
    with httpx.Client(
        headers=_DEFAULT_HEADERS,
        follow_redirects=False,
        timeout=timeout,
    ) as client:
        total = len(unique_urls)
        for i, url in enumerate(unique_urls, start=1):
            is_external = url.startswith("http")

            if is_external:
                if internal_only:
                    parsed = urlparse(url)
                    if parsed.scheme and parsed.netloc:
                        report.results[url] = UrlResult(url=url, status="ok")
                    else:
                        report.results[url] = UrlResult(
                            url=url, status="error", error="invalid URL"
                        )
                else:
                    print(f"  [{i}/{total}] {url[:80]}...", file=sys.stderr, flush=True)
                    result = check_url(
                        url, timeout=timeout, retries=retries, client=client
                    )
                    report.results[url] = result
            else:
                # Internal local link
                report.results[url] = check_local_link(url, posts_dir)

    return report


# --- Report generation -----------------------------------------------------


def generate_report(report: Report) -> dict[str, Any]:
    """Convert a Report into a JSON-serializable dict."""
    ok_urls = [r for r in report.results.values() if r.status == "ok"]
    redirect_urls = [r for r in report.results.values() if r.status == "redirect"]
    broken_urls = [r for r in report.results.values() if r.status == "broken"]
    timeout_urls = [r for r in report.results.values() if r.status == "timeout"]
    error_urls = [r for r in report.results.values() if r.status == "error"]

    # Map URLs back to source files
    url_to_entries: dict[str, list[UrlEntry]] = {}
    for entry in report.entries:
        url_to_entries.setdefault(entry.url, []).append(entry)

    def _result_dict(result: UrlResult) -> list[dict]:
        items = []
        for entry in url_to_entries.get(result.url, []):
            item: dict[str, Any] = {
                "url": result.url,
                "file": entry.file,
                "line": entry.line,
                "status": result.status,
            }
            if result.status_code is not None:
                item["status_code"] = result.status_code
            if result.redirect_url:
                item["redirect_url"] = result.redirect_url
            if result.error:
                item["error"] = result.error
            items.append(item)
        return items

    broken_list = []
    for r in broken_urls + timeout_urls + error_urls:
        broken_list.extend(_result_dict(r))

    redirect_list = []
    for r in redirect_urls:
        redirect_list.extend(_result_dict(r))

    return {
        "summary": {
            "total": len(report.results),
            "ok": len(ok_urls),
            "redirect": len(redirect_urls),
            "broken": len(broken_urls),
            "timeout": len(timeout_urls),
            "error": len(error_urls),
        },
        "broken": broken_list,
        "redirects": redirect_list,
    }


# --- CLI -------------------------------------------------------------------


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Check links in ORGAN-V Markdown content",
    )
    parser.add_argument(
        "--posts-dir",
        type=Path,
        required=True,
        help="Directory containing essay Markdown files",
    )
    parser.add_argument(
        "--logs-dir",
        type=Path,
        default=None,
        help="Directory containing log Markdown files (optional)",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=None,
        help="Output JSON report path (default: stdout)",
    )
    parser.add_argument(
        "--timeout",
        type=float,
        default=10.0,
        help="HTTP request timeout in seconds (default: 10)",
    )
    parser.add_argument(
        "--retries",
        type=int,
        default=2,
        help="Number of retries per URL (default: 2)",
    )
    parser.add_argument(
        "--internal-only",
        action="store_true",
        help="Only validate URL syntax, no HTTP requests",
    )

    args = parser.parse_args()

    if not args.posts_dir.is_dir():
        print(f"Error: {args.posts_dir} is not a directory", file=sys.stderr)
        sys.exit(1)

    print(f"Scanning {args.posts_dir}...", file=sys.stderr)
    report = check_all(
        posts_dir=args.posts_dir,
        logs_dir=args.logs_dir,
        timeout=args.timeout,
        retries=args.retries,
        internal_only=args.internal_only,
    )

    output = generate_report(report)
    json_str = json.dumps(output, indent=2)

    if args.output and str(args.output) != "/dev/null":
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json_str, encoding="utf-8")
        print(f"Report written to {args.output}", file=sys.stderr)
    elif args.output and str(args.output) == "/dev/null":
        pass  # Discard output
    else:
        print(json_str)

    # Exit with error if broken links found
    summary = output["summary"]
    broken_count = summary["broken"] + summary["timeout"] + summary["error"]
    if broken_count > 0:
        print(
            f"\n{broken_count} broken link(s) found out of {summary['total']} total",
            file=sys.stderr,
        )
        sys.exit(1)
    else:
        print(
            f"\nAll {summary['total']} links OK ({summary['redirect']} redirects)",
            file=sys.stderr,
        )


if __name__ == "__main__":
    main()
