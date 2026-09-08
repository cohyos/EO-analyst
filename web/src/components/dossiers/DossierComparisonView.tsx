import type { CitationLike } from "@/components/CitationText";
import { DossierTable, type DossierTableColumn } from "@/components/dossiers/DossierTable";
import { DossierCiteChips, DossierPlainText } from "@/components/dossiers/DossierFact";
import { groupVocabulary, vocabularyForTable, type SpecTableKind } from "@/lib/specVocabulary";
import { useT } from "@/i18n";
import type { DossierPerformanceRow, DossierSource, DossierSpecRow } from "@/types/api";

type AnyVocabRow = DossierSpecRow | DossierPerformanceRow;

export interface CompareProduct {
  productKey: string;
  productName: string;
  vendor: string | null;
  specifications: DossierSpecRow[];
  performance: DossierPerformanceRow[];
  otherSpecifications: DossierSpecRow[];
  sources: DossierSource[];
}

function rowLabelOf(r: AnyVocabRow, table: SpecTableKind): string {
  return table === "performance" ? (r as DossierPerformanceRow).metric_he : (r as DossierSpecRow).parameter_he;
}

function citationsFor(product: CompareProduct): CitationLike[] {
  return product.sources.map((s) => ({ n: s.n, item_id: null, title: s.title, url: s.url }));
}

/** One rendered comparison row: a vocabulary parameter plus, per product key, whichever rows
 * (usually 0 or 1, occasionally >1 for a `variant`-split parameter) that product carries for it. */
interface CompareRow {
  label: string;
  key: string;
  byProduct: Map<string, AnyVocabRow[]>;
}

function rowsOfProduct(p: CompareProduct, table: SpecTableKind): AnyVocabRow[] {
  return table === "performance" ? p.performance : p.specifications;
}

/** Groups the given table's rows into `CompareRow`s in the shared vocabulary's fixed group/
 * declaration order (docs/PLAN_SPEC_VOCABULARY.md §5.2: "Renders one row per vocabulary key
 * (grouped by group_he, same order as §5.1)"). A vocab param with no row from ANY product is
 * simply omitted (unlike the single-dossier table, a comparison table only exists to show what
 * IS there across products -- an all-empty row would just be noise across every column). */
function buildGroupedRows(
  products: CompareProduct[],
  productLine: string,
  table: SpecTableKind,
): { groupHe: string; rows: CompareRow[] }[] {
  const byProductKey = new Map<string, Map<string, AnyVocabRow[]>>();
  for (const p of products) {
    const byKey = new Map<string, AnyVocabRow[]>();
    for (const r of rowsOfProduct(p, table)) {
      const k = r.key ?? "";
      if (!k) continue; // unkeyed rows aren't comparable across products by construction
      const list = byKey.get(k);
      if (list) list.push(r);
      else byKey.set(k, [r]);
    }
    byProductKey.set(p.productKey, byKey);
  }

  const groups = groupVocabulary(vocabularyForTable(productLine, table));
  const result: { groupHe: string; rows: CompareRow[] }[] = [];
  for (const g of groups) {
    const rows: CompareRow[] = [];
    for (const param of g.params) {
      const byProduct = new Map<string, AnyVocabRow[]>();
      let anyMatch = false;
      let label = param.labelHe;
      for (const p of products) {
        const matches = byProductKey.get(p.productKey)?.get(param.key) ?? [];
        if (matches.length > 0) {
          anyMatch = true;
          label = rowLabelOf(matches[0], table) || param.labelHe;
        }
        byProduct.set(p.productKey, matches);
      }
      if (anyMatch) rows.push({ label, key: param.key, byProduct });
    }
    if (rows.length > 0) result.push({ groupHe: g.groupHe, rows });
  }
  return result;
}

/** True when the products' own values for this row differ (including "one has it, another
 * doesn't") -- docs/PLAN_SPEC_VOCABULARY.md §5.2: "rows = vocabulary keys, columns = products,
 * differences highlighted." Compares the first row's own `value`/`claimed_value` per product
 * (a variant-split param's extra rows don't participate in this specific highlight -- the base
 * comparison is "does the headline value differ", not every variant permutation). */
function rowDiffers(row: CompareRow, table: SpecTableKind): boolean {
  const values = [...row.byProduct.values()].map((matches) => {
    const r = matches[0];
    if (!r) return "";
    return (table === "performance" ? (r as DossierPerformanceRow).claimed_value : (r as DossierSpecRow).value) ?? "";
  });
  return new Set(values.map((v) => v.trim())).size > 1;
}

function ComparisonCell({
  matches,
  table,
  citations,
}: {
  matches: AnyVocabRow[];
  table: SpecTableKind;
  citations: CitationLike[];
}) {
  if (matches.length === 0) return <DossierPlainText text={null} />;
  return (
    <div className="space-y-1">
      {matches.map((r, i) => {
        const value = table === "performance" ? (r as DossierPerformanceRow).claimed_value : (r as DossierSpecRow).value;
        const variant = table === "specifications" ? (r as DossierSpecRow).variant : null;
        return (
          <div key={i}>
            <DossierPlainText text={variant ? `${value} (${variant})` : value} />
            {r.cites.length > 0 && (
              <div className="mt-0.5">
                <DossierCiteChips cites={r.cites} citations={citations} />
              </div>
            )}
          </div>
        );
      })}
    </div>
  );
}

function ComparisonGroupTable({
  groupHe,
  rows,
  table,
  products,
  caption,
}: {
  groupHe: string;
  rows: CompareRow[];
  table: SpecTableKind;
  products: CompareProduct[];
  caption: string;
}) {
  const t = useT();
  const columns: DossierTableColumn<CompareRow>[] = [
    {
      key: "parameter",
      label: table === "performance" ? t("dossiers.table.colMetric") : t("dossiers.table.colParameter"),
      render: (row) => (
        <span className="inline-flex items-center gap-1.5">
          {rowDiffers(row, table) && (
            <span
              aria-label={t("dossiers.compare.differsIndicator")}
              title={t("dossiers.compare.differsIndicator")}
              className="inline-block h-1.5 w-1.5 shrink-0 rounded-full bg-level-orange"
            />
          )}
          <DossierPlainText text={row.label} />
        </span>
      ),
    },
    ...products.map(
      (p): DossierTableColumn<CompareRow> => ({
        key: p.productKey,
        label: p.productName,
        render: (row) => (
          <ComparisonCell matches={row.byProduct.get(p.productKey) ?? []} table={table} citations={citationsFor(p)} />
        ),
      }),
    ),
  ];
  return (
    <div>
      <h4 className="mb-1.5 text-xs font-semibold text-fg-dim">{groupHe}</h4>
      <DossierTable
        columns={columns}
        rows={rows}
        rowKey={(row) => row.key}
        emptyLabel={t("dossiers.emptySections.specifications")}
        caption={`${caption} — ${groupHe}`}
      />
    </div>
  );
}

/**
 * PD-vocab-ui (2026-09-09, docs/PLAN_SPEC_VOCABULARY.md §5.2): up to 3 dossiers of the same
 * `product_line`, rows = vocabulary keys (grouped by `group_he`, same fixed order as
 * `DossierSpecTable`), one column per product, a cell a product's own dossier doesn't have
 * renders "לא נמצא במקורות" same as the single-dossier view, differing values flagged with a
 * small dot next to the parameter label. Renders BOTH the specifications and performance tables
 * (the two sections `DossierDetailPage` itself keeps separate) -- `other_specifications`/unkeyed
 * rows are deliberately excluded (never comparable across products by construction, per this
 * component's own `buildGroupedRows` doc comment).
 */
export function DossierComparisonView({ products, productLine }: { products: CompareProduct[]; productLine: string }) {
  const t = useT();
  const specGroups = buildGroupedRows(products, productLine, "specifications");
  const perfGroups = buildGroupedRows(products, productLine, "performance");

  return (
    <div className="space-y-6">
      <section>
        <h3 className="mb-2 text-sm font-semibold text-fg">{t("dossiers.sections.specifications")}</h3>
        {specGroups.length === 0 ? (
          <p className="text-sm text-fg-dim">{t("dossiers.emptySections.specifications")}</p>
        ) : (
          <div className="space-y-4">
            {specGroups.map((g) => (
              <ComparisonGroupTable
                key={g.groupHe}
                groupHe={g.groupHe}
                rows={g.rows}
                table="specifications"
                products={products}
                caption={t("dossiers.sections.specifications")}
              />
            ))}
          </div>
        )}
      </section>
      <section>
        <h3 className="mb-2 text-sm font-semibold text-fg">{t("dossiers.sections.performance")}</h3>
        {perfGroups.length === 0 ? (
          <p className="text-sm text-fg-dim">{t("dossiers.emptySections.performance")}</p>
        ) : (
          <div className="space-y-4">
            {perfGroups.map((g) => (
              <ComparisonGroupTable
                key={g.groupHe}
                groupHe={g.groupHe}
                rows={g.rows}
                table="performance"
                products={products}
                caption={t("dossiers.sections.performance")}
              />
            ))}
          </div>
        )}
      </section>
    </div>
  );
}
