"""Round-4: over-long entity/term lists from the model are truncated, not rejected."""

from eoa.llm.schemas.tenders import TenderExtract


def test_lists_are_capped_to_ten() -> None:
    out = TenderExtract(
        relevant=True,
        relevance=7,
        matched_terms=[f"t{i}" for i in range(16)],
        entities=[f"e{i}" for i in range(16)],
        confidence=0.5,
    )
    assert len(out.entities) == 10 and out.entities[0] == "e0"
    assert len(out.matched_terms) == 10
