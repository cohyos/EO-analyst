import { useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { X } from "lucide-react";
import { api } from "@/api";
import { LoadingState, ErrorState, EmptyState } from "@/components/states";
import type { PayloadPriceRef, PayloadSpec, PayloadSpecVersion } from "@/types/api";
import { CATEGORY_LABELS_HE } from "./PayloadFilters";

const PRICE_KIND_LABELS_HE: Record<string, string> = {
  unit: "יחידה",
  contract: "חוזה",
  estimate: "הערכה",
};

function fmtPrice(p: PayloadPriceRef): string {
  if (p.unit_price_usd != null) return `$${p.unit_price_usd.toLocaleString("en-US")}`;
  if (p.price_usd != null) return `$${p.price_usd.toLocaleString("en-US")}`;
  if (p.original_amount != null) return `${p.original_amount.toLocaleString("en-US")} ${p.currency ?? ""}`;
  return "—";
}

const SPEC_FIELD_LABELS_HE: Record<string, string> = {
  mass_kg: 'משקל (ק"ג)',
  channels: "ערוצים",
  detector: "גלאי",
  fov: "שדה ראייה",
  ranges_km: 'טווחים (ק"מ)',
  stabilisation_urad: "ייצוב (מיקרורדיאן)",
  interfaces: "ממשקים",
  trl: "TRL",
  other: "נוסף",
};

function specFieldValue(spec: PayloadSpec, key: string): string {
  const v = (spec as Record<string, unknown>)[key];
  if (v == null) return "—";
  if (key === "channels" || key === "interfaces") return (v as string[]).join(", ") || "—";
  if (key === "detector") {
    const d = v as { type?: string | null; resolution?: string | null; pitch_um?: number | null };
    return [d.type, d.resolution, d.pitch_um != null ? `${d.pitch_um}µm` : null].filter(Boolean).join(" / ") || "—";
  }
  if (key === "fov") {
    const f = v as { wide_deg?: number | null; narrow_deg?: number | null };
    return [f.wide_deg != null ? `רחב ${f.wide_deg}°` : null, f.narrow_deg != null ? `צר ${f.narrow_deg}°` : null]
      .filter(Boolean)
      .join(" / ") || "—";
  }
  if (key === "ranges_km") {
    const r = v as { detect?: number | null; recognize?: number | null; identify?: number | null; target_class?: string | null };
    const parts = [
      r.detect != null ? `גילוי ${r.detect}` : null,
      r.recognize != null ? `הכרה ${r.recognize}` : null,
      r.identify != null ? `זיהוי ${r.identify}` : null,
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
  const changed = new Set(changedFields ?? []);
  return (
    <table className="w-full border-collapse text-sm">
      <tbody>
        {Object.keys(SPEC_FIELD_LABELS_HE).map((key) => (
          <tr key={key} className={changed.has(key) ? "bg-accent-muted" : ""}>
            <td className="p-1.5 pe-3 text-fg-dim">{SPEC_FIELD_LABELS_HE[key]}</td>
            <td className="p-1.5 text-fg">{specFieldValue(spec, key)}</td>
          </tr>
        ))}
      </tbody>
    </table>
  );
}

export function PayloadDetailDrawer({ payloadId, onClose }: { payloadId: number; onClose: () => void }) {
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
          <h2 className="text-lg font-semibold text-fg">{'פרטי מטע"ד'}</h2>
          <button type="button" onClick={onClose} aria-label="סגור" className="rounded-md p-1 hover:bg-bg-sunken">
            <X size={18} />
          </button>
        </div>

        {detailQuery.isLoading && <LoadingState label={'טוען פרטי מטע"ד…'} />}
        {detailQuery.isError && <ErrorState onRetry={() => detailQuery.refetch()} />}

        {detailQuery.data && (
          <div className="space-y-6">
            <section>
              <h3 className="text-base font-semibold text-fg">{detailQuery.data.payload.canonical_name}</h3>
              <p className="text-sm text-fg-muted">
                {detailQuery.data.payload.vendor_entity_name ?? "יצרן לא ידוע"} ·{" "}
                {CATEGORY_LABELS_HE[detailQuery.data.payload.category]}
                {detailQuery.data.payload.family ? ` · משפחה: ${detailQuery.data.payload.family}` : ""}
              </p>
            </section>

            <section>
              <h4 className="mb-2 text-sm font-semibold text-fg">היסטוריית גרסאות מפרט</h4>
              {detailQuery.data.spec_versions.length === 0 ? (
                <EmptyState title={'אין עדיין מפרט מתועד למטע"ד זה'} />
              ) : (
                <>
                  <p className="mb-2 text-xs text-fg-dim">סמן שתי גרסאות להשוואה (diff).</p>
                  <ul className="space-y-2">
                    {detailQuery.data.spec_versions.map((v: PayloadSpecVersion) => (
                      <li key={v.id} className="rounded-md border border-border p-2">
                        <label className="mb-1 flex items-center gap-2 text-sm font-medium text-fg">
                          <input
                            type="checkbox"
                            checked={selectedVersions.includes(v.version_no)}
                            onChange={() => toggleVersion(v.version_no)}
                            aria-label={`בחר גרסה ${v.version_no} להשוואה`}
                          />
                          גרסה {v.version_no} — {v.effective_date}
                        </label>
                        <SpecTable spec={v.spec} />
                        {v.source_url && (
                          <a
                            href={v.source_url}
                            target="_blank"
                            rel="noreferrer"
                            className="mt-1 inline-block text-xs text-accent hover:underline"
                          >
                            מקור
                          </a>
                        )}
                      </li>
                    ))}
                  </ul>
                </>
              )}

              {a != null && b != null && (
                <div className="mt-4 rounded-md border border-border-strong p-3">
                  <h4 className="mb-2 text-sm font-semibold text-fg">
                    השוואת גרסה {a} מול גרסה {b}
                  </h4>
                  {diffQuery.isLoading && <LoadingState label="מחשב הבדלים…" />}
                  {diffQuery.data && (
                    <>
                      {diffQuery.data.changed_fields.length === 0 ? (
                        <p className="text-sm text-fg-muted">אין הבדלים בין הגרסאות שנבחרו.</p>
                      ) : (
                        <p className="mb-2 text-sm text-fg-muted">
                          שדות שהשתנו: {diffQuery.data.changed_fields.map((f) => SPEC_FIELD_LABELS_HE[f] ?? f).join(", ")}
                        </p>
                      )}
                      <div className="grid grid-cols-2 gap-3">
                        <div>
                          <p className="mb-1 text-xs font-medium text-fg-dim">גרסה {a}</p>
                          <SpecTable spec={diffQuery.data.a.spec} changedFields={diffQuery.data.changed_fields} />
                        </div>
                        <div>
                          <p className="mb-1 text-xs font-medium text-fg-dim">גרסה {b}</p>
                          <SpecTable spec={diffQuery.data.b.spec} changedFields={diffQuery.data.changed_fields} />
                        </div>
                      </div>
                    </>
                  )}
                </div>
              )}
            </section>

            <section>
              <h4 className="mb-2 text-sm font-semibold text-fg">מחירי ייחוס</h4>
              {detailQuery.data.price_refs.length === 0 ? (
                <EmptyState title={'לא נאספו מחירי ייחוס למטע"ד זה'} />
              ) : (
                <div className="overflow-x-auto rounded-lg border border-border">
                  <table className="w-full min-w-[420px] border-collapse text-sm">
                    <thead>
                      <tr className="border-b border-border bg-bg-raised text-fg-dim">
                        <th className="p-2 text-start font-medium">תאריך</th>
                        <th className="p-2 text-start font-medium">מחיר</th>
                        <th className="p-2 text-start font-medium">סוג</th>
                        <th className="p-2 text-start font-medium">רוכש/תוכנית</th>
                        <th className="p-2 text-start font-medium">מקור</th>
                      </tr>
                    </thead>
                    <tbody>
                      {detailQuery.data.price_refs.map((p: PayloadPriceRef) => (
                        <tr key={p.id} className="border-b border-border last:border-0">
                          <td className="p-2 text-fg-muted">{p.date}</td>
                          <td className="p-2 font-medium text-fg">{fmtPrice(p)}</td>
                          <td className="p-2 text-fg-muted">{PRICE_KIND_LABELS_HE[p.price_kind] ?? p.price_kind}</td>
                          <td className="p-2 text-fg-muted">{[p.buyer, p.programme].filter(Boolean).join(" / ") || "—"}</td>
                          <td className="p-2">
                            {p.source_url ? (
                              <a href={p.source_url} target="_blank" rel="noreferrer" className="text-accent hover:underline">
                                מקור
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
