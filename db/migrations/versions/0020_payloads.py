"""payloads + payload_spec_versions + payload_price_refs (A17: EO payload spec/price tracking)

Revision ID: 0020
Revises: 0019
Create Date: 2026-09-06

User requirement (2026-09-06, verbatim intent): keep up-to-date documentation of EO payload
(מטע"ד) specifications and reference prices, INCLUDING PAST VERSIONS -- a new spec/price is a
new append-only row, never an overwrite of an earlier one.

``payloads``: one row per tracked payload family (canonical name unique, e.g. "WESCAM MX-15"),
carrying only slow-changing identity fields (vendor, family/series, category, first/last seen,
free-text notes). No spec/price data lives directly on this row -- that is entirely the job of
the two child tables below, so this row itself never needs to be "overwritten" as specs evolve.

``payload_spec_versions``: append-only. Each row is one dated snapshot of a payload's technical
specification (fixed-vocabulary ``spec`` JSONB -- see ``agent/eoa/payloads/models.py`` for the
schema this project's own code writes/reads: mass_kg, channels[], detector{}, fov{}, ranges_km{},
stabilisation_urad, interfaces[], trl, other{}), citing the exact source item/URL/quote it came
from. ``version_no`` is a per-payload monotonic counter assigned by application code
(``eoa.payloads.extract``) after comparing the incoming spec to the latest existing version and
finding at least one differing field -- this migration only enforces uniqueness
(``payload_id, version_no``), not the diff logic itself.

``payload_price_refs``: append-only. Each row is one dated, cited reference-price observation
(unit or contract price, quantity, buyer/programme where known). Never updated or deleted by
application code -- a corrected/superseding price is simply a new row with a later ``date``.

Both child tables carry ``source_item_id`` (nullable FK to ``items``, ``ON DELETE SET NULL`` so a
retention sweep of old raw items never cascades into deleting the citation row itself -- the
``source_url``/``source_quote`` columns keep the citation meaningful even if the item row is
later purged), ``source_url``, ``source_quote`` (the verbatim sentence the numbers were read
from) and ``confidence``/``created_at`` per docs/CONVENTIONS.md rule 4 (provenance everywhere).
"""

from __future__ import annotations

from alembic import op

revision = "0020"
down_revision = "0019"
branch_labels = None
depends_on = None

_CATEGORY_CHECK = "category IN ('gimbal', 'pod', 'thermal_camera', 'detector_core', 'lrf', 'seeker', 'other')"
_PRICE_KIND_CHECK = "price_kind IN ('unit', 'contract', 'estimate')"


def upgrade() -> None:
    op.execute(
        f"""
        CREATE TABLE payloads (
            id                  BIGSERIAL PRIMARY KEY,
            canonical_name      TEXT NOT NULL,
            vendor_entity_name  TEXT,
            family              TEXT,
            category            TEXT NOT NULL DEFAULT 'other' CHECK ({_CATEGORY_CHECK}),
            first_seen          DATE,
            last_seen           DATE,
            notes               TEXT,
            created_at          TIMESTAMPTZ NOT NULL DEFAULT now(),
            updated_at          TIMESTAMPTZ NOT NULL DEFAULT now(),
            UNIQUE (canonical_name)
        )
        """
    )
    op.execute("CREATE INDEX ix_payloads_category ON payloads (category)")
    op.execute("CREATE INDEX ix_payloads_vendor_entity_name ON payloads (vendor_entity_name)")
    op.execute(
        "CREATE TRIGGER trg_payloads_updated_at BEFORE UPDATE ON payloads "
        "FOR EACH ROW EXECUTE FUNCTION set_updated_at()"
    )

    op.execute(
        """
        CREATE TABLE payload_spec_versions (
            id              BIGSERIAL PRIMARY KEY,
            payload_id      BIGINT NOT NULL REFERENCES payloads(id) ON DELETE CASCADE,
            version_no      INTEGER NOT NULL,
            effective_date  DATE NOT NULL,
            spec            JSONB NOT NULL,
            source_item_id  BIGINT REFERENCES items(id) ON DELETE SET NULL,
            source_url      TEXT,
            source_quote    TEXT,
            confidence      REAL,
            created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
            UNIQUE (payload_id, version_no)
        )
        """
    )
    op.execute("CREATE INDEX ix_payload_spec_versions_payload_id ON payload_spec_versions (payload_id)")
    op.execute(
        "CREATE INDEX ix_payload_spec_versions_effective_date ON payload_spec_versions (effective_date)"
    )
    op.execute("CREATE INDEX ix_payload_spec_versions_spec ON payload_spec_versions USING GIN (spec)")

    op.execute(
        f"""
        CREATE TABLE payload_price_refs (
            id              BIGSERIAL PRIMARY KEY,
            payload_id      BIGINT NOT NULL REFERENCES payloads(id) ON DELETE CASCADE,
            price_usd       DOUBLE PRECISION,
            currency        TEXT,
            original_amount DOUBLE PRECISION,
            quantity        INTEGER,
            unit_price_usd  DOUBLE PRECISION,
            price_kind      TEXT NOT NULL DEFAULT 'estimate' CHECK ({_PRICE_KIND_CHECK}),
            date            DATE NOT NULL,
            buyer           TEXT,
            programme       TEXT,
            source_item_id  BIGINT REFERENCES items(id) ON DELETE SET NULL,
            source_url      TEXT,
            source_quote    TEXT,
            created_at      TIMESTAMPTZ NOT NULL DEFAULT now()
        )
        """
    )
    op.execute("CREATE INDEX ix_payload_price_refs_payload_id ON payload_price_refs (payload_id)")
    op.execute("CREATE INDEX ix_payload_price_refs_date ON payload_price_refs (date)")


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS payload_price_refs")
    op.execute("DROP TABLE IF EXISTS payload_spec_versions")
    op.execute("DROP TABLE IF EXISTS payloads")
