"""W23 (docs/REVIEW_2026-09-06_evening.md): a link audit across the whole app -- every href the
UI/reports actually surface, checked for reachability.

Two link populations are collected, read-only:

1. **The live app's API** (``--api-base``, default ``http://127.0.0.1:8765``): a fixed list of
   endpoints covering items, investigations, reports (+ each report's own ``/citations``
   appendix), tenders (+ ``/tenders/forecasts``), conferences, and payloads. Every JSON response
   is walked recursively and any string value that looks like a URL (``http://``/``https://``) is
   collected, tagged with the endpoint + JSON path it came from.
2. **The built report HTML files** in ``output/reports/*.html``: every ``<a href="...">`` is
   collected via ``lxml.html``, tagged with the file it came from. A same-document ``#anchor``
   href is checked against that file's own ``id="..."`` attributes (catches the W4/W17-style
   "the [n] marker never links to its source" class of bug) instead of being treated as external.

**External** links (``http``/``https``, not the app's own host) are checked with HEAD, falling
back to GET on a 4xx/405/timeout, 8-second timeout, reusing the same SSRF guard as ingestion
(``eoa.fetch.remote.assert_public_http_url``) before ever opening a socket -- a link this audit
would refuse to fetch for ingestion is reported as ``blocked (ssrf-guard)``, not silently skipped.

**Internal** links (a bare ``#id`` anchor, or a path under the app's own API/frontend origin) are
resolved against what the live API/HTML file itself already told us exists, per docs/CONVENTIONS.md
rule 4 ("provenance everywhere") -- never assumed valid just because the string looks well-formed.

Writes ``docs/qa/link_audit_<date>.md`` (per-source counts + the full broken list) and prints a
one-line-per-source summary to stdout. Never mutates anything -- GET/HEAD only, no job enqueued,
no DB write.

Usage::

    python scripts/link_audit.py [--api-base http://127.0.0.1:8765] [--timeout 8]
                                  [--reports-dir output/reports] [--out docs/qa]
"""

from __future__ import annotations

import argparse
import datetime as dt
import sys
from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import urljoin, urlsplit

import httpx
import structlog

_REPO_ROOT = Path(__file__).resolve().parents[1]
_AGENT_DIR = _REPO_ROOT / "agent"
if str(_AGENT_DIR) not in sys.path:
    sys.path.insert(0, str(_AGENT_DIR))

from eoa.errors import FetchError  # noqa: E402
from eoa.fetch.remote import assert_public_http_url  # noqa: E402

try:
    from lxml import html as lxml_html
except ImportError:  # pragma: no cover -- lxml is a project dependency already (docs/CONVENTIONS.md)
    lxml_html = None

log = structlog.get_logger(__name__)

_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0 Safari/537.36 EO-Analyst-link-audit/1.0"
)

# API endpoints scanned for embedded URLs (items, reports + their citation appendices, tenders +
# forecasts, conferences, payloads -- exactly the surfaces named in W23). Report ids/tender-count
# are read live rather than hardcoded so the audit always reflects the current DB, per
# docs/CONVENTIONS.md rule 5 (never invent/assume a fixed id set).
_FIXED_ENDPOINTS = [
    "/api/items?limit=100",
    "/api/investigations?limit=50",
    "/api/reports?limit=50",
    "/api/tenders?limit=200",
    "/api/tenders/forecasts",
    "/api/conferences",
    "/api/payloads?limit=500",
]


@dataclass
class LinkRef:
    url: str
    source: str  # e.g. "/api/items?limit=100" or "output/reports/daily_2026-09-06.html"
    path: str  # JSON path (e.g. "items[3].source_url") or HTML context (e.g. "a[12]")
    kind: str = ""  # "external" | "internal-anchor" | "internal-path"


@dataclass
class CheckResult:
    ref: LinkRef
    ok: bool
    status: str  # human status: "200", "404", "timeout", "blocked (ssrf-guard)", "dns-error", ...


@dataclass
class AuditReport:
    per_source: dict[str, list[CheckResult]] = field(default_factory=dict)

    def add(self, result: CheckResult) -> None:
        self.per_source.setdefault(result.ref.source, []).append(result)


# --------------------------------------------------------------------------
# collection: API
# --------------------------------------------------------------------------


def _walk_json_for_urls(node: object, path: str, out: list[tuple[str, str]]) -> None:
    """Recursively collect every string value under ``node`` that looks like an absolute
    http(s) URL, tagged with its JSON path (dot/bracket notation) for the report's "path" column."""
    if isinstance(node, dict):
        for key, value in node.items():
            _walk_json_for_urls(value, f"{path}.{key}" if path else key, out)
    elif isinstance(node, list):
        for i, value in enumerate(node):
            _walk_json_for_urls(value, f"{path}[{i}]", out)
    elif isinstance(node, str) and node.startswith(("http://", "https://")):
        out.append((node, path))


def collect_api_links(api_base: str, timeout: float, extra_endpoints: list[str]) -> list[LinkRef]:
    refs: list[LinkRef] = []
    endpoints = list(_FIXED_ENDPOINTS) + extra_endpoints
    with httpx.Client(timeout=timeout, headers={"User-Agent": _UA}) as client:
        for ep in endpoints:
            url = urljoin(api_base, ep)
            try:
                r = client.get(url)
            except httpx.RequestError as exc:
                log.warning("link_audit_api_endpoint_unreachable", endpoint=ep, error=str(exc))
                continue
            if r.status_code >= 400:
                log.warning("link_audit_api_endpoint_error", endpoint=ep, status=r.status_code)
                continue
            try:
                data = r.json()
            except ValueError:
                continue
            found: list[tuple[str, str]] = []
            _walk_json_for_urls(data, "", found)
            for link_url, json_path in found:
                refs.append(LinkRef(url=link_url, source=ep, path=json_path))

            # Reports: also crawl each report's own citation appendix (W23 explicitly names
            # "reports appendices") -- report ids come from this very response, never guessed.
            if ep.startswith("/api/reports") and isinstance(data, list):
                for row in data:
                    rid = row.get("id") if isinstance(row, dict) else None
                    if rid is None:
                        continue
                    cite_ep = f"/api/reports/{rid}/citations"
                    try:
                        cr = client.get(urljoin(api_base, cite_ep))
                    except httpx.RequestError:
                        continue
                    if cr.status_code >= 400:
                        continue
                    try:
                        cdata = cr.json()
                    except ValueError:
                        continue
                    cfound: list[tuple[str, str]] = []
                    _walk_json_for_urls(cdata, "", cfound)
                    for link_url, json_path in cfound:
                        refs.append(LinkRef(url=link_url, source=cite_ep, path=json_path))
    return refs


# --------------------------------------------------------------------------
# collection: built report HTML
# --------------------------------------------------------------------------


def collect_report_html_links(reports_dir: Path) -> tuple[list[LinkRef], dict[str, set[str]]]:
    """Returns (refs, anchor_ids_per_file) -- ``anchor_ids_per_file[file]`` is every ``id="..."``
    attribute value in that file, used to resolve same-document ``#anchor`` hrefs."""
    refs: list[LinkRef] = []
    anchor_ids: dict[str, set[str]] = {}
    if lxml_html is None:
        log.warning("link_audit_lxml_missing", detail="skipping report HTML scan")
        return refs, anchor_ids
    for html_path in sorted(reports_dir.glob("*.html")):
        source = str(html_path.relative_to(_REPO_ROOT)).replace("\\", "/")
        try:
            tree = lxml_html.parse(str(html_path))
        except Exception as exc:  # a malformed report file is a finding, not a crash worth stopping the audit
            log.warning("link_audit_html_parse_failed", file=source, error=str(exc))
            continue
        root = tree.getroot()
        anchor_ids[source] = {el.get("id") for el in root.iter() if el.get("id")}
        for i, a in enumerate(root.iter("a")):
            href = a.get("href")
            if not href:
                continue
            refs.append(LinkRef(url=href, source=source, path=f"a[{i}]"))
    return refs, anchor_ids


# --------------------------------------------------------------------------
# checking
# --------------------------------------------------------------------------


def _is_internal_host(hostname: str | None, api_base: str) -> bool:
    if not hostname:
        return False
    api_host = urlsplit(api_base).hostname
    return hostname in {api_host, "127.0.0.1", "localhost"}


def run_audit(api_base: str, timeout: float, reports_dir: Path, extra_endpoints: list[str]) -> AuditReport:
    report = AuditReport()

    api_refs = collect_api_links(api_base, timeout, extra_endpoints)
    html_refs, anchor_ids = collect_report_html_links(reports_dir)
    all_refs = api_refs + html_refs

    # Dedup external checks by URL (the same source_url legitimately appears on many items/
    # citations) -- check each unique external URL once, then fan the result back out to every
    # LinkRef that pointed at it, so the per-source counts still reflect every occurrence.
    external_cache: dict[str, tuple[bool, str]] = {}

    with httpx.Client(headers={"User-Agent": _UA}) as client:
        for ref in all_refs:
            parts = urlsplit(ref.url)

            if ref.url.startswith("#"):
                ref.kind = "internal-anchor"
                ids = anchor_ids.get(ref.source, set())
                target = ref.url[1:]
                ok = target in ids
                report.add(CheckResult(ref=ref, ok=ok, status="anchor-found" if ok else "anchor-missing"))
                continue

            if parts.scheme in {"", "http", "https"} and _is_internal_host(parts.hostname, api_base):
                ref.kind = "internal-path"
                # A same-app path -- re-request it against the live API/base to confirm the
                # referenced route/id actually resolves (never assumed from the string alone).
                try:
                    target_url = ref.url if parts.scheme else urljoin(api_base, ref.url)
                    r = client.get(target_url, timeout=timeout, follow_redirects=True)
                    ok = r.status_code < 400
                    report.add(CheckResult(ref=ref, ok=ok, status=str(r.status_code)))
                except httpx.RequestError as exc:
                    report.add(CheckResult(ref=ref, ok=False, status=f"error: {exc}"))
                continue

            if parts.scheme not in {"http", "https"}:
                # mailto:, javascript:void(0), tel: etc. -- not a fetchable link at all; note and skip.
                report.add(CheckResult(ref=ref, ok=True, status=f"skipped ({parts.scheme or 'relative'})"))
                continue

            ref.kind = "external"
            if ref.url not in external_cache:
                try:
                    assert_public_http_url(ref.url)
                except FetchError:
                    external_cache[ref.url] = (False, "blocked (ssrf-guard)")
                else:
                    try:
                        r = client.head(ref.url, timeout=timeout, follow_redirects=True)
                        if r.status_code >= 400 or r.status_code == 405:
                            r = client.get(ref.url, timeout=timeout, follow_redirects=True)
                        external_cache[ref.url] = (r.status_code < 400, str(r.status_code))
                    except httpx.TimeoutException:
                        external_cache[ref.url] = (False, "timeout")
                    except httpx.RequestError as exc:
                        external_cache[ref.url] = (False, f"error: {exc}"[:120])
            ok, status = external_cache[ref.url]
            report.add(CheckResult(ref=ref, ok=ok, status=status))

    return report


# --------------------------------------------------------------------------
# reporting
# --------------------------------------------------------------------------


def render_markdown(report: AuditReport, api_base: str, generated_at: dt.datetime) -> str:
    lines = [
        f"# Link audit -- {generated_at.date().isoformat()}",
        "",
        f"Generated {generated_at.isoformat()} against API base `{api_base}` "
        "and `output/reports/*.html` (W23, docs/REVIEW_2026-09-06_evening.md). Read-only: "
        "GET/HEAD checks only, no job enqueued, no DB write.",
        "",
        "## Per-source summary",
        "",
        "| Source | Total links | OK | Broken |",
        "|---|---:|---:|---:|",
    ]
    total_all = ok_all = broken_all = 0
    broken_rows: list[tuple[str, str, str, str]] = []  # source, path, url, status
    for source in sorted(report.per_source):
        results = report.per_source[source]
        ok = sum(1 for r in results if r.ok)
        broken = len(results) - ok
        total_all += len(results)
        ok_all += ok
        broken_all += broken
        lines.append(f"| `{source}` | {len(results)} | {ok} | {broken} |")
        for r in results:
            if not r.ok:
                broken_rows.append((source, r.ref.path, r.ref.url, r.status))
    lines.append(f"| **Total** | **{total_all}** | **{ok_all}** | **{broken_all}** |")
    lines.append("")
    lines.append("## Broken links")
    lines.append("")
    if not broken_rows:
        lines.append("None found.")
    else:
        lines.append("| Source | Path | URL | Status |")
        lines.append("|---|---|---|---|")
        for source, path, url, status in broken_rows:
            url_display = url if len(url) <= 100 else url[:97] + "..."
            lines.append(f"| `{source}` | `{path}` | {url_display} | {status} |")
    lines.append("")
    return "\n".join(lines)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--api-base", default="http://127.0.0.1:8765")
    parser.add_argument("--timeout", type=float, default=8.0)
    parser.add_argument("--reports-dir", default=str(_REPO_ROOT / "output" / "reports"))
    parser.add_argument("--out", default=str(_REPO_ROOT / "docs" / "qa"))
    parser.add_argument(
        "--extra-endpoint",
        action="append",
        default=[],
        help="additional /api/... path to scan for embedded URLs (repeatable)",
    )
    args = parser.parse_args()

    reports_dir = Path(args.reports_dir)
    out_dir = Path(args.out)
    out_dir.mkdir(parents=True, exist_ok=True)

    report = run_audit(args.api_base, args.timeout, reports_dir, args.extra_endpoint)

    generated_at = dt.datetime.now(dt.UTC)
    md = render_markdown(report, args.api_base, generated_at)
    out_path = out_dir / f"link_audit_{generated_at.date().isoformat()}.md"
    out_path.write_text(md, encoding="utf-8")

    total = sum(len(v) for v in report.per_source.values())
    ok = sum(sum(1 for r in v if r.ok) for v in report.per_source.values())
    print(f"link audit: {ok}/{total} links OK across {len(report.per_source)} sources")
    for source in sorted(report.per_source):
        results = report.per_source[source]
        broken = [r for r in results if not r.ok]
        print(f"  {source}: {len(results)} links, {len(broken)} broken")
    print(f"wrote {out_path}")


if __name__ == "__main__":
    main()
