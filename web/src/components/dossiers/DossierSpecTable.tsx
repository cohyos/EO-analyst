import type { CitationLike } from "@/components/CitationText";
import { DossierTable, type DossierTableColumn } from "@/components/dossiers/DossierTable";
import { DossierCiteChips, DossierPlainText } from "@/components/dossiers/DossierFact";
import { groupVocabulary, vocabularyForTable, type SpecTableKind, type SpecVocabParam } from "@/lib/specVocabulary";
import { useT } from "@/i18n";
import type { DossierPerformanceRow, DossierSpecRow } from "@/types/api";

type AnyVocabRow = DossierSpecRow | DossierPerformanceRow;

function rowKeyOf(r: AnyVocabRow): string {
  return r.key ?? "";
}

function rowLabelOf(r: AnyVocabRow, table: SpecTableKind): string {
  return table === "performance" ? (r as DossierPerformanceRow).metric_he : (r as DossierSpecRow).parameter_he;
}

/** One rendered row: either a real grounded row from the dossier, or a synthesized placeholder
 * for a `required: true` vocabulary key the sources said nothing about (docs/
 * PLAN_SPEC_VOCABULARY.md §5.1: "A required: true row always renders ... even if the extraction
 * found nothing for it"). `param` is null for a legacy/overflow row that carries no vocabulary
 * key at all (rendered in the trailing "אחר" table instead of a group). */
interface DisplayRow {
  label: string;
  unit: string | null;
  row: AnyVocabRow | null;
  requiredMissing: boolean;
}

/** Distinct from `NotFoundInSources` (used for an ordinary empty cell): this is specifically the
 * "a required parameter was looked for and not established" case (docs/PLAN_SPEC_VOCABULARY.md
 * §5.1: "styled distinct from a genuinely-empty-but-not-required row so a BD reader can tell
 * 'nobody looked' apart from 'not applicable to this line'"). */
function RequiredMissingCell() {
  const t = useT();
  return (
    <span className="inline-flex items-center gap-1 rounded bg-level-orange/10 px-1.5 py-0.5 text-xs italic text-level-orange">
      {t("dossiers.notFoundInSources")}
      <span aria-hidden="true">*</span>
    </span>
  );
}

function buildColumns(table: SpecTableKind, t: ReturnType<typeof useT>, citations: CitationLike[]): DossierTableColumn<DisplayRow>[] {
  const parameterCol: DossierTableColumn<DisplayRow> = {
    key: "parameter",
    label: table === "performance" ? t("dossiers.table.colMetric") : t("dossiers.table.colParameter"),
    render: (d) => (
      <span className={d.requiredMissing ? "font-medium" : undefined}>
        <DossierPlainText text={d.label} />
      </span>
    ),
  };

  if (table === "performance") {
    return [
      parameterCol,
      {
        key: "claimed",
        label: t("dossiers.table.colClaimed"),
        render: (d) =>
          d.requiredMissing ? (
            <RequiredMissingCell />
          ) : (
            <DossierPlainText text={(d.row as DossierPerformanceRow | null)?.claimed_value ?? null} />
          ),
      },
      {
        key: "demonstrated",
        label: t("dossiers.table.colDemonstrated"),
        render: (d) => <DossierPlainText text={(d.row as DossierPerformanceRow | null)?.tested_value ?? null} />,
      },
      {
        key: "conditions",
        label: t("dossiers.table.colConditions"),
        render: (d) => <DossierPlainText text={(d.row as DossierPerformanceRow | null)?.conditions_he ?? null} />,
      },
      {
        key: "cites",
        label: t("dossiers.table.colSources"),
        render: (d) => <DossierCiteChips cites={d.row?.cites ?? []} citations={citations} />,
      },
    ];
  }

  return [
    parameterCol,
    {
      key: "value",
      label: t("dossiers.table.colValue"),
      render: (d) =>
        d.requiredMissing ? (
          <RequiredMissingCell />
        ) : (
          <DossierPlainText text={(d.row as DossierSpecRow | null)?.value ?? null} />
        ),
    },
    {
      key: "unit",
      label: t("dossiers.table.colUnit"),
      render: (d) => <DossierPlainText text={(d.row as DossierSpecRow | null)?.unit ?? d.unit} />,
    },
    {
      key: "variant",
      label: t("dossiers.table.colVariant"),
      render: (d) => <DossierPlainText text={(d.row as DossierSpecRow | null)?.variant ?? null} />,
    },
    {
      key: "source_kind",
      label: t("dossiers.table.colSourceKind"),
      render: (d) => <DossierPlainText text={(d.row as DossierSpecRow | null)?.source_kind ?? null} />,
    },
    {
      key: "cites",
      label: t("dossiers.table.colSources"),
      render: (d) => <DossierCiteChips cites={d.row?.cites ?? []} citations={citations} />,
    },
  ];
}

/**
 * PD-vocab-ui (2026-09-09, docs/PLAN_SPEC_VOCABULARY.md §5.1): grouping WRAPPER around
 * `DossierTable` (not a replacement -- every group still renders through that shared <= 6-column,
 * sticky-header primitive) for the specifications/performance sections. Renders one sub-table per
 * `group_he`, in the fixed 8-group order (`specVocabulary.ts`'s `SPEC_GROUP_ORDER`), each group's
 * rows in vocabulary declaration order; a `required: true` key with no matching row still renders
 * (placeholder cell, styled distinct from an ordinary empty cell). Rows carrying no vocabulary
 * `key` at all -- a legacy pre-vocabulary row, or (for the specifications table) the dedicated
 * `other_specifications` overflow bucket -- render in one trailing, un-grouped "אחר" table instead
 * of being dropped, per this lane's "tolerant of both the new keyed rows and the legacy
 * free-named rows" brief.
 */
export function DossierSpecTable({
  table,
  rows,
  otherRows = [],
  productLine,
  citations,
  emptyLabel,
  caption,
}: {
  table: SpecTableKind;
  rows: AnyVocabRow[];
  /** Only meaningful for `table: "specifications"` -- `ProductDossierOut.other_specifications`. */
  otherRows?: DossierSpecRow[];
  productLine: string | null | undefined;
  citations: CitationLike[];
  emptyLabel: string;
  caption: string;
}) {
  const t = useT();
  const columns = buildColumns(table, t, citations);

  const byKey = new Map<string, AnyVocabRow[]>();
  const unkeyed: AnyVocabRow[] = [];
  for (const r of rows) {
    const k = rowKeyOf(r);
    if (!k) {
      unkeyed.push(r);
      continue;
    }
    const list = byKey.get(k);
    if (list) list.push(r);
    else byKey.set(k, [r]);
  }

  const groups = groupVocabulary(vocabularyForTable(productLine, table));
  const renderedGroups: { groupHe: string; displayRows: DisplayRow[] }[] = [];
  const matchedKeys = new Set<string>();

  for (const g of groups) {
    const displayRows: DisplayRow[] = [];
    for (const param of g.params) {
      const matches = byKey.get(param.key);
      if (matches && matches.length > 0) {
        matchedKeys.add(param.key);
        for (const m of matches) {
          displayRows.push({ label: rowLabelOf(m, table) || param.labelHe, unit: param.unit, row: m, requiredMissing: false });
        }
      } else if (param.required) {
        displayRows.push({ label: param.labelHe, unit: param.unit, row: null, requiredMissing: true });
      }
    }
    if (displayRows.length > 0) renderedGroups.push({ groupHe: g.groupHe, displayRows });
  }

  // A keyed row whose key isn't in this table's own vocabulary slice at all (e.g. a hallucinated
  // key the backend's own post-check would normally demote -- see docs/PLAN_SPEC_VOCABULARY.md
  // §3.5 item 1 -- or simply a key that belongs to the OTHER table) still renders, in the "אחר"
  // appendix, rather than silently vanishing.
  const strayKeyed = [...byKey.entries()]
    .filter(([k]) => !matchedKeys.has(k))
    .flatMap(([, list]) => list);

  const appendixRows: DisplayRow[] = [
    ...unkeyed.map((r) => ({ label: rowLabelOf(r, table), unit: null, row: r, requiredMissing: false })),
    ...strayKeyed.map((r) => ({ label: rowLabelOf(r, table), unit: null, row: r, requiredMissing: false })),
    ...otherRows.map((r) => ({ label: r.parameter_he, unit: r.unit, row: r, requiredMissing: false })),
  ];

  if (renderedGroups.length === 0 && appendixRows.length === 0) {
    return <DossierTable columns={columns} rows={[]} rowKey={() => ""} emptyLabel={emptyLabel} caption={caption} />;
  }

  return (
    <div className="space-y-4">
      {renderedGroups.map((g) => (
        <div key={g.groupHe}>
          <h4 className="mb-1.5 text-xs font-semibold text-fg-dim">{g.groupHe}</h4>
          <DossierTable
            columns={columns}
            rows={g.displayRows}
            rowKey={(d, i) => `${d.row ? rowKeyOf(d.row) || d.label : d.label}-${i}`}
            emptyLabel={emptyLabel}
            caption={`${caption} — ${g.groupHe}`}
          />
        </div>
      ))}
      {appendixRows.length > 0 && (
        <div>
          <h4 className="mb-1.5 text-xs font-semibold text-fg-dim">{t("dossiers.table.otherSpecifications")}</h4>
          <DossierTable
            columns={columns}
            rows={appendixRows}
            rowKey={(d, i) => `other-${d.label}-${i}`}
            emptyLabel={emptyLabel}
            caption={`${caption} — ${t("dossiers.table.otherSpecifications")}`}
          />
        </div>
      )}
    </div>
  );
}

export type { AnyVocabRow, SpecVocabParam };
