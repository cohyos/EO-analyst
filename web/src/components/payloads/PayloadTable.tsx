import type { PayloadRecord } from "@/types/api";
import { CATEGORY_LABELS_HE } from "./PayloadFilters";

export function PayloadTable({
  payloads,
  selectedId,
  onSelect,
}: {
  payloads: PayloadRecord[];
  selectedId: number | null;
  onSelect: (id: number) => void;
}) {
  return (
    <div className="overflow-x-auto rounded-lg border border-border">
      <table className="w-full min-w-[800px] border-collapse text-sm">
        <thead>
          <tr className="border-b border-border bg-bg-raised text-fg-dim">
            <th className="p-2 text-start font-medium">{'שם המטע"ד'}</th>
            <th className="p-2 text-start font-medium">יצרן</th>
            <th className="p-2 text-start font-medium">קטגוריה</th>
            <th className="p-2 text-center font-medium">גרסאות מפרט</th>
            <th className="p-2 text-start font-medium">מפרט אחרון</th>
            <th className="p-2 text-center font-medium">מחירי ייחוס</th>
            <th className="p-2 text-start font-medium">מחיר אחרון</th>
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
              <td className="p-2 font-medium text-fg">{p.canonical_name}</td>
              <td className="p-2 text-fg-muted">{p.vendor_entity_name ?? "—"}</td>
              <td className="p-2 text-fg-muted">{CATEGORY_LABELS_HE[p.category]}</td>
              <td className="p-2 text-center text-fg-muted">{p.spec_version_count}</td>
              <td className="p-2 text-fg-muted">{p.latest_spec_date ?? "—"}</td>
              <td className="p-2 text-center text-fg-muted">{p.price_ref_count}</td>
              <td className="p-2 text-fg-muted">{p.latest_price_date ?? "—"}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
