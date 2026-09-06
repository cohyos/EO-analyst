"""payloads: image_url, spec_url, spec_source (W19: EO payload thumbnail + manufacturer spec link)

Revision ID: 0022
Revises: 0021
Create Date: 2026-09-06

User requirement (docs/REVIEW_2026-09-06_evening.md W19, verbatim intent): the payloads screen
should show a product image where one exists and link to the manufacturer's own spec page,
instead of just a bare identity row -- and be honest ("spec/price not yet documented") when
neither is known.

These three columns are identity-level (same mutability tier as `vendor_entity_name`/`family` on
`payloads` -- slow-changing, never versioned) rather than part of `payload_spec_versions`: an
image/spec-sheet URL is a property of the *product page*, not a dated technical measurement, so
it does not belong in the append-only spec-version history and is not subject to
`field_diff`/`verify_numbers_verbatim`. All three are nullable and additive -- no backfill, no
existing column touched, per docs/CONVENTIONS.md rule 6 ("never invent": a NULL here means
"not yet known", not "empty string").

``image_url``/``spec_url``: the vendor's own product/spec page (never a third-party mirror),
populated by `db/seed/seed_payloads.py` from `config/payloads_seed.yaml` (identity-only seed,
each URL verified reachable at seed-authoring time) and/or by a future extraction pass.
``spec_source``: a short human-readable label for the citation (e.g. "l3harris.com") shown next
to the "מפרט יצרן" link so an analyst always sees whose page they're about to open.
"""

from __future__ import annotations

from alembic import op

revision = "0022"
down_revision = "0021"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("ALTER TABLE payloads ADD COLUMN IF NOT EXISTS image_url TEXT")
    op.execute("ALTER TABLE payloads ADD COLUMN IF NOT EXISTS spec_url TEXT")
    op.execute("ALTER TABLE payloads ADD COLUMN IF NOT EXISTS spec_source TEXT")


def downgrade() -> None:
    op.execute("ALTER TABLE payloads DROP COLUMN IF EXISTS spec_source")
    op.execute("ALTER TABLE payloads DROP COLUMN IF EXISTS spec_url")
    op.execute("ALTER TABLE payloads DROP COLUMN IF EXISTS image_url")
