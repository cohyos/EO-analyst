import { ContentShareActions } from "@/components/ContentShareActions";
import { useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { X } from "lucide-react";
import { api } from "@/api";
import { LoadingState, ErrorState, EmptyState } from "@/components/states";
import type { PayloadPriceRef, PayloadSpec, PayloadSpecVersion } from "@/types/api";
import { useCategoryLabels } from "./PayloadFilters";
import { useT } from "@/i18n";
import type { TranslationKey } from "@/i18n/types";

const PRICE_KIND_KEYS: Record<string, TranslationKey> = {
  unit: "payloads.priceKinds.unit",
  contract: "payloads.priceKinds.contract",
  estimate: "payloads.priceKinds.estimate",
};

const SPEC_FIELD_KEYS: Record<string, TranslationKey> = {
  mass_kg: "payloads.specFields.mass_kg",
  channels: "payloads.specFields.channels",
  detector: "payloads.specFields.detector",
  fov: "payloads.specFields.fov",
  ranges_km: "payloads.specFields.ranges_km",
  stabilisation_urad: "payloads.specFields.stabilisation_urad",
  interfaces: "payloads.specFields.interfaces",
  trl: "payloads.specFields.trl",
  other: "payloads.specFields.other",
};

const SPEC_FIELD_ORDER = Object.keys(SPEC_FIELD_KEYS);

function fmtPrice(p: PayloadPriceRef): string {
  if (p.unit_price_usd != null) return `$${p.unit_price_usd.toLocaleString("en-US")}`;
  if (p.price_usd != null) return `$${p.price_usd.toLocaleString("en-US")}`;
  if (p.original_amount != null) return `${p.original_amount.toLocaleString("en-US")} ${p.currency ?? ""}`;
  return "—";
}

function specFieldValue(spec: PayloadSpec, key: string, t: (k: TranslationKey, p?: Record<string, string | number>) => string): string {
  const v = (spec as Record<string, unknown>)[key];
  if (v == null) return "—";
  if (key === "channels" || key === "interfaces") return (v as string[]).join(", ") || "—";
  if (key === "detector") {
    const d = v as { type?: string | null; resolution?: string | null; pitch_um?: number | null };
    return [d.type, d.resolution, d.pitch_um != null ? `${d.pitch_um}µm` : null].filter(Boolean).join(" / ") || "—";
  }
  if (key === "fov") {
    const f = v as { wide_deg?: number | null; narrow_deg?: number | null };
    return (
      [
        f.wide_deg != null ? t("payloads.fovWide", { deg: f.wide_deg }) : null,
        f.narrow_deg != null ? t("payloads.fovNarrow", { deg: f.narrow_deg }) : null,
      ]
        .filter(Boolean)
        .join(" / ") || "—"
    );
  }
  if (key === "ranges_km") {
    const r = v as { detect?: number | null; recognize?: number | null; identify?: number | null; target_class?: string | null };
    const parts = [
      r.detect != null ? t("payloads.rangeDetect", { km: r.detect }) : null,
      r.recognize != null ? t("payloads.rangeRecognize", { km: r.recognize }) : null,
      r.identify != null ? t("payloads.rangeIdentify", { km: r.identify }) : null,
    ].filter(Boolean);
    return parts.length ? parts.join(" / ") + (r.target_class ? ` (${r.target_class})` : "") : "—";
  }
  if (key === "other") {
    const o = v as Record<string, string>;
    return Object.keys(o).length ? Object.entries(o).map(([k, val]) => `${k}: ${val}`).join("; ") : "—";
  }
  return String(v);
}

function SpecTable({ spec, changedFields }: { spec: PayloadSpec; changedFields?: string[] }) {
  const t = useT();
  const changed = new Set(changedFields ?? []);
  return (
    <table className="w-full border-collapse text-sm">
      <tbody>
        {SPEC_FIELD_ORDER.map((key) => (
          <tr key={key} className={changed.has(key) ? "bg-accent-muted" : ""}>
            <td className="p-1.5 pe-3 text-fg-dim">{t(SPEC_FIELD_KEYS[key])}</td>
            <td className="p-1.5 text-fg">{specFieldValue(spec, key, t)}</td>
          </tr>
        ))}
      </tbody>
    </table>
  );
}

export function PayloadDetailDrawer({ payloadId, onClose }: { payloadId: number; onClose: () => void }) {
  const t = useT();
  const categoryLabels = useCategoryLabels();
  const [selectedVersions, setSelectedVersions] = useState<number[]>([]);

  const detailQuery = useQuery({
    queryKey: ["payload-detail", payloadId],
    queryFn: () => api.getPayload(payloadId),
  });

  const [a, b] = selectedVersions.length === 2 ? [Math.min(...selectedVersions), Math.max(...selectedVersions)] : [null, null];
  const diffQuery = useQuery({
    queryKey: ["payload-diff", payloadId, a, b],
    queryFn: () => api.getPayloadDiff(payloadId, a as number, b as number),
    enabled: a != null && b != null,
  });

  function toggleVersion(versionNo: number) {
    setSelectedVersions((cur) => {
      if (cur.includes(versionNo)) return cur.filter((v) => v !== versionNo);
      if (cur.length >= 2) return [cur[1], versionNo];
      return [...cur, versionNo];
    });
  }

  return (
    <div className="fixed inset-0 z-40 flex justify-end bg-black/30" onClick={onClose}>
      <div
        className="h-full w-full max-w-xl overflow-y-auto bg-bg p-4 shadow-xl md:p-6"
        onClick={(e) => e.stopPropagation()}
        role="dialog"
        aria-modal="true"
      >
        <div className="mb-4 flex items-center justify-between">
          <h2 className="text-lg font-semibold text-fg">{t("payloads.detailTitle")}</h2>
          <button type="button" onClick={onClose} aria-label={t("payloads.closeAria")} className="rounded-md p-1 hover:bg-bg-sunken">
            <X size={18} />
          </button>
        </div>

        {detailQuery.isLoading && <LoadingState label={t("payloads.loading")} />}
        {detailQuery.isError && <ErrorState onRetry={() => detailQuery.refetch()} />}

        {detailQuery.data && (
          <div data-share-content className="space-y-6">
            <ContentShareActions title={detailQuery.data.payload.canonical_name} />
            <section>
              {detailQuery.data.payload.image_url && (
                <img
                  src={detailQuery.data.payload.image_url}
                  alt={detailQuery.data.payload.canonical_name}
                  loading="lazy"
                  className="mb-2 h-32 w-full rounded border border-border object-cover"
                />
              )}
              <h3 className="text-base font-semibold text-fg">{detailQuery.data.payload.canonical_name}</h3>
              <p className="text-sm text-fg-muted">
                {detailQuery.data.payload.vendor_entity_name ?? t("payloads.vendorUnknown")} ·{" "}
                {categoryLabels[detailQuery.data.payload.category]}
                {detailQuery.data.payload.family
                  ? ` · ${t("payloads.familySuffix", { family: detailQuery.data.payload.family })}`
                  : ""}
              </p>
              {detailQuery.data.payload.spec_url ? (
                <a
                  href={detailQuery.data.payload.spec_url}
                  target="_blank"
                  rel="noreferrer"
                  className="mt-1 inline-block text-sm text-accent hover:underline"
                >
                  {t("payloads.specLink")}
                  {detailQuery.data.payload.spec_source ? ` (${detailQuery.data.payload.spec_source})` : ""}
                </a>
              ) : (
                <p className="mt-1 text-sm text-fg-dim">{t("payloads.specMissing")}</p>
              )}
            </section>

            <section>
              <h4 className="mb-2 text-sm font-semibold text-fg">{t("payloads.specVersionsHistory")}</h4>
              {detailQuery.data.spec_versions.length === 0 ? (
                <EmptyState title={t("payloads.noSpecVersions")} />
              ) : (
                <>
                  <p className="mb-2 text-xs text-fg-dim">{t("payloads.compareHint")}</p>
                  <ul className="space-y-2">
                    {detailQuery.data.spec_versions.map((v: PayloadSpecVersion) => (
                      <li key={v.id} className="rounded-md border border-border p-2">
                        <label className="mb-1 flex items-center gap-2 text-sm font-medium text-fg">
                          <input
                            type="checkbox"
                            checked={selectedVersions.includes(v.version_no)}
                            onChange={() => toggleVersion(v.version_no)}
                            aria-label={t("payloads.selectVersionAria", { n: v.version_no })}
                          />
                          {t("payloads.versionLabel", { n: v.version_no, date: v.effective_date })}
                        </label>
                        <SpecTable spec={v.spec} />
                        {v.source_url && (
                          <a
                            href={v.source_url}
                            target="_blank"
                            rel="noreferrer"
                            className="mt-1 inline-block text-xs text-accent hover:underline"
                          >
                            {t("payloads.sourceLink")}
                          </a>
                        )}
                      </li>
                    ))}
                  </ul>
                </>
              )}

              {a != null && b != null && (
                <div className="mt-4 rounded-md border border-border-strong p-3">
                  <h4 className="mb-2 text-sm font-semibold text-fg">{t("payloads.compareTitle", { a, b })}</h4>
                  {diffQuery.isLoading && <LoadingState label={t("payloads.computingDiff")} />}
                  {diffQuery.data && (
                    <>
                      {diffQuery.data.changed_fields.length === 0 ? (
                        <p className="text-sm text-fg-muted">{t("payloads.noDiff")}</p>
                      ) : (
                        <p className="mb-2 text-sm text-fg-muted">
                          {t("payloads.changedFieldsLabel", {
                            fields: diffQuery.data.changed_fields.map((f) => t(SPEC_FIELD_KEYS[f] ?? "payloads.specFields.other")).join(", "),
                          })}
                        </p>
                      )}
                      <div className="grid grid-cols-2 gap-3">
                        <div>
                          <p className="mb-1 text-xs font-medium text-fg-dim">{t("payloads.versionCol", { n: a })}</p>
                          <SpecTable spec={diffQuery.data.a.spec} changedFields={diffQuery.data.changed_fields} />
                        </div>
                        <div>
                          <p className="mb-1 text-xs font-medium text-fg-dim">{t("payloads.versionCol", { n: b })}</p>
                          <SpecTable spec={diffQuery.data.b.spec} changedFields={diffQuery.data.changed_fields} />
                        </div>
                      </div>
                    </>
                  )}
                </div>
              )}
            </section>

            <section>
              <h4 className="mb-2 text-sm font-semibold text-fg">{t("payloads.priceRefsTitle")}</h4>
              {detailQuery.data.price_refs.length === 0 ? (
                <EmptyState title={t("payloads.noPriceRefs")} />
              ) : (
                <div className="overflow-x-auto rounded-lg border border-border">
                  <table className="w-full min-w-[420px] border-collapse text-sm">
                    <thead>
                      <tr className="border-b border-border bg-bg-raised text-fg-dim">
                        <th className="p-2 text-start font-medium">{t("payloads.priceColDate")}</th>
                        <th className="p-2 text-start font-medium">{t("payloads.priceColPrice")}</th>
                        <th className="p-2 text-start font-medium">{t("payloads.priceColKind")}</th>
                        <th className="p-2 text-start font-medium">{t("payloads.priceColBuyer")}</th>
                        <th className="p-2 text-start font-medium">{t("payloads.priceColSource")}</th>
                      </tr>
                    </thead>
                    <tbody>
                      {detailQuery.data.price_refs.map((p: PayloadPriceRef) => (
                        <tr key={p.id} className="border-b border-border last:border-0">
                          <td className="p-2 text-fg-muted">{p.date}</td>
                          <td className="p-2 font-medium text-fg">{fmtPrice(p)}</td>
                          <td className="p-2 text-fg-muted">{t(PRICE_KIND_KEYS[p.price_kind] ?? "payloads.priceKinds.estimate")}</td>
                          <td className="p-2 text-fg-muted">{[p.buyer, p.programme].filter(Boolean).join(" / ") || "—"}</td>
                          <td className="p-2">
                            {p.source_url ? (
                              <a href={p.source_url} target="_blank" rel="noreferrer" className="text-accent hover:underline">
                                {t("payloads.sourceLink")}
                              </a>
                            ) : (
                              "—"
                            )}
                          </td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              )}
            </section>
          </div>
        )}
      </div>
    </div>
  );
}
