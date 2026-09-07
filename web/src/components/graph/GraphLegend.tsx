import { entityKindLabel } from "@/components/entities/eventKindLabel";
import { KIND_COLOR } from "./graphColors";

export function GraphLegend({ kinds }: { kinds: string[] }) {
  if (kinds.length === 0) return null;
  return (
    <div className="flex flex-wrap gap-2 rounded-md bg-bg-raised/90 p-1.5 text-[10px] text-fg-muted shadow-panel">
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
