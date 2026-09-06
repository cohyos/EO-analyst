"""Q3-6 (docs/qa/findings_Q3_r1.md): dedup existing events, then enforce logical uniqueness.

Revision ID: 0016
Revises: 0015
Create Date: 2026-09-06

`events` had no uniqueness constraint at all, so re-processing an item (or the model
re-extracting a slightly different reading of the same underlying fact) could insert the same
(item_id, kind, title) combination more than once -- item 70's "investment" event was inserted
twice, once without an amount_usd and once with. This migration:

1. Deduplicates existing rows sharing (item_id, kind, lower(title)): for each such group, the
   row with the lowest id is kept and updated with the first non-null value across the group for
   date/amount_usd/currency/customer/program/summary_he, the first non-empty `parties` array, and
   the max confidence; every other row in the group is deleted.
2. Adds the unique index `ux_events_item_kind_title` on (item_id, kind, (lower(title))) that
   `eoa.memory.relational.insert_event`'s `ON CONFLICT` upsert relies on going forward.

`title IS NULL` rows are excluded from both steps -- there's no title to key identity on, and
Postgres unique indexes already treat NULLs as mutually distinct, so they need no special
handling here.

Historical duplicates from before this migration that are *not* exact (item_id, kind, lower(title))
matches (e.g. a near-duplicate title with different wording) are out of scope for this
migration -- see `scripts/repair_events_dedup.py` for a report of anything left over.
"""

from __future__ import annotations

from alembic import op

revision = "0016"
down_revision = "0015"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Q3-6: merge each duplicate group's fields into the lowest-id row, via `first_value()`
    # window functions rather than `array_agg()` -- `parties` is `text[]`, and `array_agg()` over
    # an already-array column plus `[1]` subscripting does not reliably yield back a plain
    # `text[]` value (this migration's first draft hit "column parties is of type text[] but
    # expression is of type text" for exactly this reason). `first_value()` just returns one row's
    # value per window, so it works identically for a scalar or array column.
    op.execute(
        """
        WITH ranked AS (
            SELECT
                id,
                min(id) OVER (PARTITION BY item_id, kind, lower(title)) AS keep_id,
                count(*) OVER (PARTITION BY item_id, kind, lower(title)) AS grp_count,
                first_value(date) OVER (
                    PARTITION BY item_id, kind, lower(title) ORDER BY (date IS NULL), id
                ) AS m_date,
                first_value(amount_usd) OVER (
                    PARTITION BY item_id, kind, lower(title) ORDER BY (amount_usd IS NULL), id
                ) AS m_amount_usd,
                first_value(currency) OVER (
                    PARTITION BY item_id, kind, lower(title) ORDER BY (currency IS NULL), id
                ) AS m_currency,
                first_value(customer) OVER (
                    PARTITION BY item_id, kind, lower(title) ORDER BY (customer IS NULL), id
                ) AS m_customer,
                first_value(program) OVER (
                    PARTITION BY item_id, kind, lower(title) ORDER BY (program IS NULL), id
                ) AS m_program,
                first_value(summary_he) OVER (
                    PARTITION BY item_id, kind, lower(title) ORDER BY (summary_he IS NULL), id
                ) AS m_summary_he,
                first_value(parties) OVER (
                    PARTITION BY item_id, kind, lower(title)
                    ORDER BY (parties IS NULL OR array_length(parties, 1) IS NULL), id
                ) AS m_parties,
                max(confidence) OVER (PARTITION BY item_id, kind, lower(title)) AS m_confidence
            FROM events
            WHERE title IS NOT NULL
        )
        UPDATE events e SET
            date = r.m_date,
            amount_usd = r.m_amount_usd,
            currency = r.m_currency,
            customer = r.m_customer,
            program = r.m_program,
            summary_he = r.m_summary_he,
            parties = r.m_parties,
            confidence = r.m_confidence,
            updated_at = now()
        FROM ranked r
        WHERE e.id = r.id AND r.grp_count > 1 AND r.id = r.keep_id
        """
    )
    op.execute(
        """
        DELETE FROM events e
        USING (
            SELECT id, min(id) OVER (PARTITION BY item_id, kind, lower(title)) AS keep_id
            FROM events
            WHERE title IS NOT NULL
        ) r
        WHERE e.id = r.id AND r.id <> r.keep_id
        """
    )
    op.execute(
        "CREATE UNIQUE INDEX ux_events_item_kind_title ON events (item_id, kind, (lower(title)))"
    )


def downgrade() -> None:
    op.execute("DROP INDEX ux_events_item_kind_title")
