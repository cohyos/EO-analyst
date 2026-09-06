import type { PayloadRecord } from "@/types/api";
import { useCategoryLabels } from "./PayloadFilters";
import { useT } from "@/i18n";

/** W19 (docs/REVIEW_2026-09-06_evening.md): small lazy-loaded thumbnail -- `image_url` is
 * identity-level and nullable (never invented, see migration 0022's docstring); a payload with no
 * known image renders a neutral placeholder instead of an empty cell or a broken `<img>`.
 * Exported (W19b) so `PayloadTree`'s variant rows can reuse the exact same thumbnail/placeholder
 * without duplicating the markup -- takes just `imageUrl` rather than a full `PayloadRecord` so
 * it also works for a `PayloadTreeVariant`, which doesn't carry every `PayloadRecord` field. */
export function PayloadThumbnail({ imageUrl, alt }: { imageUrl: string | null; alt: string }) {
  if (!imageUrl) {
    return (
      <div
        aria-hidden="true"
        className="flex h-10 w-14 items-center justify-center rounded border border-dashed border-border text-[10px] text-fg-dim"
      >
        —
      </div>
    );
  }
  return (
    <img
      src={imageUrl}
      alt={alt}
      loading="lazy"
      className="h-10 w-14 rounded border border-border object-cover"
    />
  );
}

export function PayloadTable({
  payloads,
  selectedId,
  onSelect,
}: {
  payloads: PayloadRecord[];
  selectedId: number | null;
  onSelect: (id: number) => void;
}) {
  const t = useT();
  const categoryLabels = useCategoryLabels();
  return (
    <div className="overflow-x-auto rounded-lg border border-border">
      <table className="w-full min-w-[900px] border-collapse text-sm">
        <thead>
          <tr className="border-b border-border bg-bg-raised text-fg-dim">
            <th className="p-2 text-start font-medium">{t("payloads.colImage")}</th>
            <th className="p-2 text-start font-medium">{t("payloads.colName")}</th>
            <th className="p-2 text-start font-medium">{t("payloads.colVendor")}</th>
            <th className="p-2 text-start font-medium">{t("payloads.colCategory")}</th>
            <th className="p-2 text-center font-medium">{t("payloads.colSpecVersions")}</th>
            <th className="p-2 text-start font-medium">{t("payloads.colLatestSpec")}</th>
            <th className="p-2 text-center font-medium">{t("payloads.colPriceRefs")}</th>
            <th className="p-2 text-start font-medium">{t("payloads.colLatestPrice")}</th>
            <th className="p-2 text-start font-medium">{t("payloads.specLink")}</th>
          </tr>
        </thead>
        <tbody>
          {payloads.map((p) => (
            <tr
              key={p.id}
              onClick={() => onSelect(p.id)}
              aria-selected={selectedId === p.id}
              className={
                "cursor-pointer border-b border-border last:border-0 hover:bg-bg-sunken " +
                (selectedId === p.id ? "bg-bg-sunken" : "")
              }
            >
              <td className="p-2">
                <PayloadThumbnail imageUrl={p.image_url} alt={p.canonical_name} />
              </td>
              <td className="p-2 font-medium text-fg">{p.canonical_name}</td>
              <td className="p-2 text-fg-muted">{p.vendor_entity_name ?? "—"}</td>
              <td className="p-2 text-fg-muted">{categoryLabels[p.category]}</td>
              <td className="p-2 text-center text-fg-muted">{p.spec_version_count}</td>
              <td className="p-2 text-fg-muted">{p.latest_spec_date ?? "—"}</td>
              <td className="p-2 text-center text-fg-muted">{p.price_ref_count}</td>
              <td className="p-2 text-fg-muted">{p.latest_price_date ?? "—"}</td>
              <td className="p-2">
                {p.spec_url ? (
                  <a
                    href={p.spec_url}
                    target="_blank"
                    rel="noreferrer"
                    onClick={(e) => e.stopPropagation()}
                    className="text-accent hover:underline"
                    title={p.spec_source ?? undefined}
                  >
                    {t("payloads.specLink")}
                  </a>
                ) : (
                  <span className="text-xs text-fg-dim">{t("payloads.specMissing")}</span>
                )}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
