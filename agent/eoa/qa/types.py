"""Shared result types for every ``eoa.qa.d*`` domain scorer."""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class Check:
    """One deterministic pass/fail criterion within a domain.

    ``passed`` is a boolean gate (did this criterion clear its threshold), but ``evidence`` should
    always carry the underlying numbers (pass rate, offending ids, counts) so a human reviewing
    ``round_N_auto.json`` can see *why* without re-running the query.
    """

    name: str
    passed: bool
    weight: float = 1.0
    evidence: str = ""

    def to_dict(self) -> dict:
        return {"name": self.name, "passed": self.passed, "weight": self.weight, "evidence": self.evidence}


@dataclass
class DomainScore:
    """Result of one ``score_Dn`` call.

    ``score_0_100`` is ``None`` for a domain that has no deterministic signal at all on this round
    (e.g. D5 with no chat-log persistence table, or D10 when ``--e2e`` was not passed) -- callers
    must render that as "manual only" / "skipped", never as a numeric 0.
    """

    domain: str
    score_0_100: float | None
    checks: list[Check] = field(default_factory=list)
    n: int = 0
    note: str = ""

    def to_dict(self) -> dict:
        return {
            "domain": self.domain,
            "score_0_100": self.score_0_100,
            "n": self.n,
            "note": self.note,
            "checks": [c.to_dict() for c in self.checks],
        }


def weighted_score(checks: list[Check]) -> float | None:
    """Sum(weight for passed checks) / sum(weight) * 100, or ``None`` for an empty check list."""
    total = sum(c.weight for c in checks)
    if total <= 0:
        return None
    return round(100.0 * sum(c.weight for c in checks if c.passed) / total, 1)
