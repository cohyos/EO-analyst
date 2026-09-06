import { useEffect, useMemo, useRef, useState } from "react";
import { ChevronRight } from "lucide-react";
import type { PayloadTreeFamily, PayloadTreeResponse, PayloadTreeVariant, PayloadTreeVendor } from "@/types/api";
import { familyNodeKey, variantNodeKey, vendorNodeKey } from "@/lib/payloadFamilies";
import { PayloadThumbnail } from "./PayloadTable";
import { useCategoryLabels } from "./PayloadFilters";
import { useT } from "@/i18n";

/** One visible row of the flattened tree -- built fresh every render from `tree` + the current
 * expand state so keyboard navigation (Arrow keys/Home/End) always walks exactly what is on
 * screen, in document order. A family with exactly one variant whose label is identical to the
 * family's own label (the common case for a single, ungenerationed product -- "Toplite",
 * "DCoMPASS": `eoa.payloads.models.parse_family_variant` gives it `family == variant`) is folded
 * -- its family row is skipped and the lone variant renders directly under the vendor -- since a
 * "Toplite > Toplite" row would say nothing a screen reader or a sighted operator doesn't already
 * see once, and (just as importantly for the W25 English-mode/PayloadsPage test suite) would
 * otherwise put the exact same text on screen twice. */
interface TreeRow {
  key: string;
  level: 1 | 2 | 3;
  kind: "vendor" | "family" | "variant";
  expandable: boolean;
  expanded: boolean;
  vendor: PayloadTreeVendor;
  family?: PayloadTreeFamily;
  variant?: PayloadTreeVariant;
}

function buildRows(tree: PayloadTreeResponse, isExpanded: (key: string) => boolean): TreeRow[] {
  const rows: TreeRow[] = [];
  for (const vendorNode of tree.vendors) {
    const vKey = vendorNodeKey(vendorNode.vendor);
    const vExpanded = isExpanded(vKey);
    rows.push({ key: vKey, level: 1, kind: "vendor", expandable: true, expanded: vExpanded, vendor: vendorNode });
    if (!vExpanded) continue;

    for (const familyNode of vendorNode.families) {
      const sole = familyNode.variant_count === 1 ? familyNode.variants[0] : undefined;
      const foldFamily = sole != null && sole.variant === familyNode.family;
      if (foldFamily && sole) {
        rows.push({
          key: variantNodeKey(sole.id),
          level: 2,
          kind: "variant",
          expandable: false,
          expanded: false,
          vendor: vendorNode,
          family: familyNode,
          variant: sole,
        });
        continue;
      }
      const fKey = familyNodeKey(vendorNode.vendor, familyNode.family);
      const fExpanded = isExpanded(fKey);
      rows.push({
        key: fKey,
        level: 2,
        kind: "family",
        expandable: true,
        expanded: fExpanded,
        vendor: vendorNode,
        family: familyNode,
      });
      if (!fExpanded) continue;
      for (const variant of familyNode.variants) {
        rows.push({
          key: variantNodeKey(variant.id),
          level: 3,
          kind: "variant",
          expandable: false,
          expanded: false,
          vendor: vendorNode,
          family: familyNode,
          variant,
        });
      }
    }
  }
  return rows;
}

const LEVEL_PADDING_REM = [0, 0.5, 1.75, 3];

export function PayloadTree({
  tree,
  selectedId,
  onSelect,
  isExpanded,
  onToggleExpand,
}: {
  tree: PayloadTreeResponse;
  selectedId: number | null;
  onSelect: (id: number) => void;
  isExpanded: (key: string) => boolean;
  onToggleExpand: (key: string) => void;
}) {
  const t = useT();
  const categoryLabels = useCategoryLabels();
  const rows = useMemo(() => buildRows(tree, isExpanded), [tree, isExpanded]);

  const rowRefs = useRef(new Map<string, HTMLTableRowElement>());
  const [focusedKey, setFocusedKey] = useState<string | null>(rows[0]?.key ?? null);

  useEffect(() => {
    if (rows.length === 0) {
      setFocusedKey(null);
      return;
    }
    if (!rows.some((r) => r.key === focusedKey)) {
      setFocusedKey(rows[0].key);
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [rows]);

  function focusRow(key: string) {
    setFocusedKey(key);
    rowRefs.current.get(key)?.focus();
  }

  function handleKeyDown(event: React.KeyboardEvent<HTMLTableRowElement>, index: number) {
    const row = rows[index];
    switch (event.key) {
      case "ArrowDown": {
        event.preventDefault();
        const next = rows[Math.min(index + 1, rows.length - 1)];
        if (next) focusRow(next.key);
        break;
      }
      case "ArrowUp": {
        event.preventDefault();
        const prev = rows[Math.max(index - 1, 0)];
        if (prev) focusRow(prev.key);
        break;
      }
      case "ArrowRight": {
        event.preventDefault();
        if (row.expandable && !row.expanded) {
          onToggleExpand(row.key);
        } else if (row.expandable && row.expanded) {
          const next = rows[index + 1];
          if (next && next.level > row.level) focusRow(next.key);
        }
        break;
      }
      case "ArrowLeft": {
        event.preventDefault();
        if (row.expandable && row.expanded) {
          onToggleExpand(row.key);
        } else {
          for (let i = index - 1; i >= 0; i -= 1) {
            if (rows[i].level < row.level) {
              focusRow(rows[i].key);
              break;
            }
          }
        }
        break;
      }
      case "Home": {
        event.preventDefault();
        if (rows[0]) focusRow(rows[0].key);
        break;
      }
      case "End": {
        event.preventDefault();
        if (rows.length > 0) focusRow(rows[rows.length - 1].key);
        break;
      }
      case "Enter":
      case " ": {
        event.preventDefault();
        if (row.kind === "variant" && row.variant) {
          onSelect(row.variant.id);
        } else if (row.expandable) {
          onToggleExpand(row.key);
        }
        break;
      }
      default:
        break;
    }
  }

  return (
    <div className="overflow-x-auto rounded-lg border border-border">
      <table
        role="treegrid"
        aria-label={t("payloads.treeAriaLabel")}
        className="w-full min-w-[900px] border-collapse text-sm"
      >
        <thead>
          <tr role="row" className="border-b border-border bg-bg-raised text-fg-dim">
            <th role="columnheader" className="p-2 text-start font-medium">
              {t("payloads.colImage")}
            </th>
            <th role="columnheader" className="p-2 text-start font-medium">
              {t("payloads.colName")}
            </th>
            <th role="columnheader" className="p-2 text-start font-medium">
              {t("payloads.colCategory")}
            </th>
            <th role="columnheader" className="p-2 text-center font-medium">
              {t("payloads.colSpecVersions")}
            </th>
            <th role="columnheader" className="p-2 text-start font-medium">
              {t("payloads.colLatestSpec")}
            </th>
            <th role="columnheader" className="p-2 text-center font-medium">
              {t("payloads.colPriceRefs")}
            </th>
            <th role="columnheader" className="p-2 text-start font-medium">
              {t("payloads.colLatestPrice")}
            </th>
            <th role="columnheader" className="p-2 text-start font-medium">
              {t("payloads.specLink")}
            </th>
          </tr>
        </thead>
        <tbody>
          {rows.map((row, index) => {
            const isFocused = row.key === focusedKey;
            const indentRem = LEVEL_PADDING_REM[row.level] ?? 0;

            if (row.kind === "variant" && row.variant) {
              const v = row.variant;
              return (
                <tr
                  key={row.key}
                  ref={(el) => {
                    if (el) rowRefs.current.set(row.key, el);
                    else rowRefs.current.delete(row.key);
                  }}
                  role="row"
                  aria-level={row.level}
                  aria-selected={selectedId === v.id}
                  tabIndex={isFocused ? 0 : -1}
                  onFocus={() => setFocusedKey(row.key)}
                  onKeyDown={(e) => handleKeyDown(e, index)}
                  onClick={() => onSelect(v.id)}
                  className={
                    "cursor-pointer border-b border-border last:border-0 hover:bg-bg-sunken outline-none focus-visible:ring-2 focus-visible:ring-accent " +
                    (selectedId === v.id ? "bg-bg-sunken" : "")
                  }
                >
                  <td role="gridcell" className="p-2" style={{ paddingInlineStart: `${indentRem}rem` }}>
                    <PayloadThumbnail imageUrl={v.image_url} alt={v.canonical_name} />
                  </td>
                  <td role="gridcell" className="p-2 font-medium text-fg">
                    {v.canonical_name}
                  </td>
                  <td role="gridcell" className="p-2 text-fg-muted">
                    {categoryLabels[v.category]}
                  </td>
                  <td role="gridcell" className="p-2 text-center text-fg-muted">
                    {v.spec_version_count}
                  </td>
                  <td role="gridcell" className="p-2 text-fg-muted">
                    {v.latest_spec_date ?? "—"}
                  </td>
                  <td role="gridcell" className="p-2 text-center text-fg-muted">
                    {v.price_ref_count}
                  </td>
                  <td role="gridcell" className="p-2 text-fg-muted">
                    {v.latest_price_date ?? "—"}
                  </td>
                  <td role="gridcell" className="p-2">
                    {v.spec_url ? (
                      <a
                        href={v.spec_url}
                        target="_blank"
                        rel="noreferrer"
                        onClick={(e) => e.stopPropagation()}
                        className="text-accent hover:underline"
                        title={v.spec_source ?? undefined}
                      >
                        {t("payloads.specLink")}
                      </a>
                    ) : (
                      <span className="text-xs text-fg-dim">{t("payloads.specMissing")}</span>
                    )}
                  </td>
                </tr>
              );
            }

            // Group row -- vendor (level 1) or family (level 2).
            const label = row.kind === "vendor" ? row.vendor.vendor : (row.family?.family ?? "");
            const summary =
              row.kind === "vendor"
                ? t("payloads.vendorSummary", {
                    families: row.vendor.family_count,
                    payloads: row.vendor.payload_count,
                  })
                : t("payloads.familySummary", { count: row.family?.variant_count ?? 0 });
            const specVersionCount = row.kind === "vendor" ? row.vendor.spec_version_count : row.family?.spec_version_count ?? 0;
            const priceRefCount = row.kind === "vendor" ? row.vendor.price_ref_count : row.family?.price_ref_count ?? 0;
            const latestSpecDate = row.kind === "family" ? row.family?.latest_spec_date ?? null : null;
            const latestPriceDate = row.kind === "family" ? row.family?.latest_price_date ?? null : null;
            const ariaLabel = row.expanded
              ? t("payloads.collapseAria", { name: label })
              : t("payloads.expandAria", { name: label });

            return (
              <tr
                key={row.key}
                ref={(el) => {
                  if (el) rowRefs.current.set(row.key, el);
                  else rowRefs.current.delete(row.key);
                }}
                role="row"
                aria-level={row.level}
                aria-expanded={row.expanded}
                aria-label={ariaLabel}
                tabIndex={isFocused ? 0 : -1}
                onFocus={() => setFocusedKey(row.key)}
                onKeyDown={(e) => handleKeyDown(e, index)}
                onClick={() => onToggleExpand(row.key)}
                className="cursor-pointer border-b border-border bg-bg-raised/60 last:border-0 hover:bg-bg-sunken outline-none focus-visible:ring-2 focus-visible:ring-accent"
              >
                <td role="gridcell" className="p-2" />
                <td role="gridcell" className="p-2" style={{ paddingInlineStart: `${indentRem}rem` }}>
                  <span className="inline-flex items-center gap-1.5">
                    <ChevronRight
                      size={14}
                      aria-hidden="true"
                      style={{ transform: row.expanded ? "rotate(90deg)" : "rotate(0deg)", transition: "transform 120ms" }}
                    />
                    <span className="font-medium text-fg">{label}</span>
                    <span className="text-xs text-fg-dim">({summary})</span>
                  </span>
                </td>
                <td role="gridcell" className="p-2" />
                <td role="gridcell" className="p-2 text-center text-fg-muted">
                  {specVersionCount || ""}
                </td>
                <td role="gridcell" className="p-2 text-fg-muted">
                  {latestSpecDate ?? ""}
                </td>
                <td role="gridcell" className="p-2 text-center text-fg-muted">
                  {priceRefCount || ""}
                </td>
                <td role="gridcell" className="p-2 text-fg-muted">
                  {latestPriceDate ?? ""}
                </td>
                <td role="gridcell" className="p-2" />
              </tr>
            );
          })}
        </tbody>
      </table>
    </div>
  );
}
