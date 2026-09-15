import { entityKindLabel } from "@/components/entities/eventKindLabel";
import { KIND_COLOR } from "./graphColors";

export function GraphLegend({ kinds }: { kinds: string[] }) {
  if (kinds.length === 0) return null;
  return (
    // Round-4 mobile fix (fix #2): 10px was below the 12px informational-text floor -- text-xs
    // (12px) keeps the legend legible on a phone without the overlay outgrowing its corner.
    <div className="flex flex-wrap gap-2 rounded-md bg-bg-raised/90 p-1.5 text-xs text-fg-muted shadow-panel">
      {kinds.map((k) => (
        <span key={k} className="flex items-center gap-1">
          <span
            className="inline-block h-2 w-2 rounded-full"
            style={{ backgroundColor: KIND_COLOR[k] ?? KIND_COLOR.default }}
            aria-hidden="true"
          />
          {entityKindLabel(k)}
        </span>
      ))}
    </div>
  );
}
