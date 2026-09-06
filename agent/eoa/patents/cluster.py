"""Deterministic patent clustering, timeline/expiry math, business-relationship extraction, and
the "no-invented-negation" consistency check (A14b, docs/PLAN_WINDOWS_NATIVE.md row A14b, user
requirement 2026-09-06 points 2/3/6). Pure, DB-free, LLM-free logic over the same registry-row
shape ``eoa.patents.survey`` already builds (a dict per patent: ``n``/``id``/``title``/
``abstract``/``assignees``/``cpc``/``publication_date``/``filing_date``/``priority_date``/
``grant_date``/``family_id``) -- kept in its own module (rather than growing ``survey.py`` further)
so every piece here is unit-testable without a database, Ollama, or ``docx_builder``.

**Clustering method** (point 2 of the 2026-09-06 request): primarily the patent's first/primary
CPC code (already a coarse subclass-level code in this project's own data, e.g. ``"G01J5"`` --
see ``config/patents.yaml``), labelled in Hebrew via whichever configured watch topic already
names that CPC code (``config/patents.yaml``'s ``watch_topics``). A patent with no CPC code at all
(the common case for the keyless Google-Patents-search fallback -- see ``eoa.patents.scan``'s own
docstring) falls back to a keyword-overlap match against each watch topic's own query text; a
patent matching neither lands in an explicit "לא מסווג" bucket rather than a fabricated cluster.
This is a landscape-analysis heuristic, not a legal CPC classification.

**Cross-citation note**: this project's ``patents`` table only stores citation *counts*
(``forward_citations``/``backward_citations``), never the actual citing/cited publication numbers
(neither EPO OPS/PatentsView-when-configured nor the keyless search fallback surface that graph) --
so a genuine cross-citation edge between two specific patents cannot be constructed from this data.
:func:`same_family_groups` (shared ``family_id``) is the one real structural link available instead,
documented here as exactly that (a patent-family relationship, not a citation).
"""

from __future__ import annotations

import datetime as dt
import re
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from typing import Any, Protocol

PATENT_TERM_YEARS = 20
_DAYS_PER_YEAR = 365.25
EXPIRY_SOON_YEARS = 3

FLAG_EXPIRED_HE = "פג"
FLAG_EXPIRING_SOON_HE = "עומד לפוג ב-3 השנים הקרובות"
FLAG_PENDING_HE = "בבחינה"
UNCLASSIFIED_KEY = "_UNCLASSIFIED_"
UNCLASSIFIED_LABEL_HE = "לא מסווג"


class _TopicLike(Protocol):
    name_he: str
    query: str
    cpc: list[str]


# --------------------------------------------------------------------------
# expiry / timeline (point 6)
# --------------------------------------------------------------------------


def expiry_estimate(filing_date: dt.date | None, priority_date: dt.date | None) -> dt.date | None:
    """20 years from ``filing_date`` (falling back to ``priority_date`` when filing is unknown) --
    the standard utility-patent term proxy, "subject to maintenance fees" (בכפוף לתחזוקה) per the
    user's own phrasing; this is never a legal-certainty expiry date, only an estimate."""
    base = filing_date or priority_date
    if base is None:
        return None
    return base + dt.timedelta(days=round(PATENT_TERM_YEARS * _DAYS_PER_YEAR))


def expiry_flag(
    *, expiry_date: dt.date | None, grant_date: dt.date | None, today: dt.date | None = None
) -> str:
    """One of :data:`FLAG_PENDING_HE` / :data:`FLAG_EXPIRED_HE` / :data:`FLAG_EXPIRING_SOON_HE`, or
    ``""`` for a normal in-force patent. A patent with no recorded ``grant_date`` is always flagged
    "בבחינה" (still under examination / not confirmed granted in this project's own data) regardless
    of its estimated expiry -- an un-granted application has no term running yet."""
    today = today or dt.date.today()
    if grant_date is None:
        return FLAG_PENDING_HE
    if expiry_date is None:
        return ""
    if expiry_date <= today:
        return FLAG_EXPIRED_HE
    if expiry_date <= today + dt.timedelta(days=round(EXPIRY_SOON_YEARS * _DAYS_PER_YEAR)):
        return FLAG_EXPIRING_SOON_HE
    return ""


@dataclass
class TimelineRow:
    n: int
    pub_number: str
    title: str | None
    priority_date: dt.date | None
    filing_date: dt.date | None
    publication_date: dt.date | None
    grant_date: dt.date | None
    expiry_date: dt.date | None
    flag_he: str


def build_timeline_rows(
    registry_rows: list[dict[str, Any]], *, today: dt.date | None = None
) -> list[TimelineRow]:
    """One :class:`TimelineRow` per patent in ``registry_rows`` (each a dict carrying at least
    ``n``/``pub_number``; missing date fields are simply ``None`` -- never invented)."""
    today = today or dt.date.today()
    out: list[TimelineRow] = []
    for row in registry_rows:
        filing_date = row.get("filing_date")
        priority_date = row.get("priority_date")
        grant_date = row.get("grant_date")
        expiry = expiry_estimate(filing_date, priority_date)
        out.append(
            TimelineRow(
                n=row["n"],
                pub_number=row.get("pub_number") or "",
                title=row.get("title"),
                priority_date=priority_date,
                filing_date=filing_date,
                publication_date=row.get("publication_date"),
                grant_date=grant_date,
                expiry_date=expiry,
                flag_he=expiry_flag(expiry_date=expiry, grant_date=grant_date, today=today),
            )
        )
    return out


def filing_waves(rows: list[dict[str, Any]], key_fn: Any) -> dict[str, Counter[int]]:
    """``{key: {year: count}}`` filing/publication waves grouped by whatever ``key_fn(row)``
    returns (a cluster label, an assignee name, ...) -- ``None``/falsy keys and rows with no
    ``publication_date`` are skipped (never fabricate a year)."""
    out: dict[str, Counter[int]] = defaultdict(Counter)
    for row in rows:
        d = row.get("publication_date")
        if not d:
            continue
        key = key_fn(row)
        if not key:
            continue
        out[key][d.year] += 1
    return dict(out)


# --------------------------------------------------------------------------
# clustering (point 1 + 2)
# --------------------------------------------------------------------------


_WORD_RE = re.compile(r"[A-Za-z]{3,}")
_STOPWORDS = {
    "and",
    "the",
    "for",
    "with",
    "using",
    "based",
    "system",
    "systems",
    "method",
    "methods",
    "device",
    "devices",
    "apparatus",
    "of",
    "in",
    "on",
    "to",
    "an",
    "or",
}


def _keyword_tokens(text: str) -> set[str]:
    return {t.lower() for t in _WORD_RE.findall(text or "") if t.lower() not in _STOPWORDS}


def _topic_keyword_index(topics: list[_TopicLike]) -> list[tuple[_TopicLike, set[str]]]:
    return [(t, _keyword_tokens(t.query)) for t in topics]


def _cpc_label(code: str, topics: list[_TopicLike]) -> str:
    for topic in topics:
        if code in (topic.cpc or []):
            return topic.name_he
    return f"אשכול טכנולוגי {code}"


def _keyword_cluster(
    title: str, abstract: str, topic_index: list[tuple[_TopicLike, set[str]]], *, min_overlap: int = 2
) -> tuple[str, str] | None:
    tokens = _keyword_tokens(f"{title or ''} {abstract or ''}")
    if not tokens:
        return None
    best_topic: _TopicLike | None = None
    best_score = 0
    for topic, kw in topic_index:
        score = len(tokens & kw)
        if score > best_score:
            best_score = score
            best_topic = topic
    if best_topic is not None and best_score >= min_overlap:
        topic_cpc = best_topic.cpc or []
        key = topic_cpc[0] if topic_cpc else f"KW:{best_topic.name_he}"
        return key, best_topic.name_he
    return None


@dataclass
class PatentCluster:
    key: str
    label_he: str
    patent_ns: list[int] = field(default_factory=list)
    patent_ids: list[Any] = field(default_factory=list)
    cpc_codes: list[str] = field(default_factory=list)
    assignees: Counter[str] = field(default_factory=Counter)
    years: Counter[int] = field(default_factory=Counter)
    granted: int = 0
    total: int = 0

    @property
    def size(self) -> int:
        return len(self.patent_ns)

    @property
    def dominant_assignees(self) -> list[str]:
        return [a for a, _n in self.assignees.most_common(3)]

    @property
    def grant_ratio(self) -> float:
        return (self.granted / self.total) if self.total else 0.0

    @property
    def filing_velocity_per_year(self) -> float:
        """Mean filings/publications per year across the cluster's own observed year span
        (inclusive) -- 0.0 when fewer than one distinct year is known."""
        if not self.years:
            return 0.0
        span = max(self.years) - min(self.years) + 1
        return sum(self.years.values()) / span

    @property
    def maturity_label_he(self) -> str:
        """A coarse maturity proxy from the grant ratio -- a real legal-status determination needs
        a patent attorney; this is a landscape hint only."""
        ratio = self.grant_ratio
        if self.total == 0:
            return "—"
        if ratio >= 0.6:
            return "בשל (רוב הענקות)"
        if ratio >= 0.2:
            return "מתבגר (הענקות חלקיות)"
        return "מוקדם (רוב בבחינה)"


def cluster_patents(
    registry_rows: list[dict[str, Any]], *, topics: list[_TopicLike] | None = None
) -> list[PatentCluster]:
    """Group every patent registry row (each carrying at least ``n``/``id``/``cpc``/``assignees``/
    ``title``/``abstract``/``publication_date``/``grant_date``) into :class:`PatentCluster`\\ s,
    sorted largest-first. See the module docstring for the clustering method."""
    topic_index = _topic_keyword_index(topics or [])
    clusters: dict[str, PatentCluster] = {}
    for row in registry_rows:
        cpc_list = row.get("cpc") or []
        if cpc_list:
            key = cpc_list[0]
            label = _cpc_label(key, topics or [])
        else:
            kw = _keyword_cluster(row.get("title") or "", row.get("abstract") or "", topic_index)
            key, label = kw if kw else (UNCLASSIFIED_KEY, UNCLASSIFIED_LABEL_HE)
        cluster = clusters.setdefault(key, PatentCluster(key=key, label_he=label))
        cluster.patent_ns.append(row["n"])
        cluster.patent_ids.append(row.get("id"))
        for c in cpc_list:
            if c not in cluster.cpc_codes:
                cluster.cpc_codes.append(c)
        for a in row.get("assignees") or []:
            if a:
                cluster.assignees[a] += 1
        pub = row.get("publication_date")
        if pub:
            cluster.years[pub.year] += 1
        cluster.total += 1
        if row.get("grant_date"):
            cluster.granted += 1
    return sorted(clusters.values(), key=lambda c: -c.size)


def cross_cluster_links(clusters: list[PatentCluster]) -> list[tuple[str, str, list[str]]]:
    """``(cluster_a_label, cluster_b_label, shared_assignee_names)`` for every pair of clusters
    that share at least one real assignee -- the cross-link signal available without a real
    citation graph (see module docstring)."""
    links: list[tuple[str, str, list[str]]] = []
    for i, a in enumerate(clusters):
        for b in clusters[i + 1 :]:
            shared = sorted(set(a.assignees) & set(b.assignees))
            if shared:
                links.append((a.label_he, b.label_he, shared))
    return links


# --------------------------------------------------------------------------
# business relationships (point 3)
# --------------------------------------------------------------------------


def co_assignment_pairs(rows: list[dict[str, Any]]) -> Counter[tuple[str, str]]:
    """``{(assignee_a, assignee_b): shared_patent_count}`` for every pair of distinct real
    assignees that co-appear on at least one patent (alphabetically ordered pair, so ``(A, B)`` and
    ``(B, A)`` never both appear)."""
    counter: Counter[tuple[str, str]] = Counter()
    for row in rows:
        names = sorted({a for a in (row.get("assignees") or []) if a})
        for i in range(len(names)):
            for j in range(i + 1, len(names)):
                counter[(names[i], names[j])] += 1
    return counter


def same_family_groups(registry_rows: list[dict[str, Any]]) -> dict[str, list[int]]:
    """``{family_id: [n, ...]}`` for every ``family_id`` shared by 2+ registry rows -- a real
    patent-family relationship (not a citation, see module docstring)."""
    groups: dict[str, list[int]] = defaultdict(list)
    for row in registry_rows:
        fam = row.get("family_id")
        if fam:
            groups[fam].append(row["n"])
    return {k: v for k, v in groups.items() if len(v) > 1}


def relationship_edges_from_events(assignee_name: str, events: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Supplier/integrator/customer-chain edges out of ``assignee_name`` inferred from its own
    (already-fetched) ``events`` rows: an event's ``customer`` field, and any other named
    ``parties``, become a directed edge ``assignee_name -> other_party`` labelled by the event kind
    and citing the event's own registry number (``n``, set by
    ``eoa.patents.survey._extend_registry_with_db_records`` before this is called)."""
    edges: list[dict[str, Any]] = []
    for ev in events:
        others = set()
        customer = ev.get("customer")
        if customer:
            others.add(customer)
        others.update(p for p in (ev.get("parties") or []) if p)
        others.discard(assignee_name)
        for other in sorted(others):
            edges.append(
                {
                    "from": assignee_name,
                    "to": other,
                    "kind": ev.get("kind"),
                    "n": ev.get("n"),
                    "program": ev.get("program"),
                    "title": ev.get("title"),
                }
            )
    return edges


# --------------------------------------------------------------------------
# consistency check (point 6: reject "אין פטנטים של X" when X actually has patents)
# --------------------------------------------------------------------------

_NEGATION_RE = re.compile(
    r"(?:אין|לא\s+נרשמו|לא\s+נמצאו|לא\s+קיימים|לא\s+קיים)\s+(?:עוד\s+)?פטנטים?\b"
    r"[^.!?]{0,20}?(?:של|מ-|עבור|הרשומים\s+על\s+שם)\s+"
    r"([A-Za-z][\w&.\-]*(?:\s+[A-Z][\w&.\-]*){0,3})"
)


def _name_matches(known_name: str, candidate: str) -> bool:
    known = known_name.lower().strip()
    cand = candidate.lower().strip()
    return bool(known) and bool(cand) and (known in cand or cand in known)


def consistency_violations(text: str, assignee_counts: dict[str, int]) -> list[str]:
    """Scan ``text`` for a Hebrew "no patents of <company>" negation naming a company that
    ``assignee_counts`` (the survey's own deterministic per-assignee counts, computed straight off
    the appendix rows -- never the LLM's own claim) shows actually has 1+ patents on record.
    Returns one human-readable violation message per match found; an empty list means the text is
    consistent with the deterministic counts (or contains no such negation at all)."""
    violations: list[str] = []
    for m in _NEGATION_RE.finditer(text or ""):
        candidate = m.group(1).strip().rstrip(".,;:")
        for name, count in assignee_counts.items():
            if count > 0 and name and _name_matches(name, candidate):
                violations.append(
                    f'הטענה "{m.group(0)}" סותרת את הנספח: יש {count} פטנט(ים) רשומים של {name}.'
                )
    return violations
