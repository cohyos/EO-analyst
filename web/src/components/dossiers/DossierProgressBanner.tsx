import { Circle, CircleCheck, CircleX, Loader2 } from "lucide-react";
import { useT } from "@/i18n";
import type { TranslationKey } from "@/i18n";
import type { DossierProgressTopic } from "@/types/api";

/**
 * PD-fix (2026-09-08, item 5): the pending-run banner's per-topic list -- a check/spinner/x per
 * research topic (``eoa.dossier.plan.TOPICS``, surfaced through ``pending_job.progress`` --
 * ``eoa.dossier.plan.run_plan``'s ``on_progress`` writes it into the running job's own row, polled
 * every 10s by ``DossierDetailPage`` same as the rest of ``pending_job``) with elapsed time once a
 * topic finishes, instead of one opaque "running" line for a run that can take ~2h.
 */

//: Only the topics that already have their own top-level nav section (``DossierDetailPage``'s
//: ``NAV_ITEMS``) get a localized label here -- "regulatory" (a plan.py topic) has no equivalent
//: top-level section of its own (it renders inline under "deals"), so it -- and any future/unknown
//: topic key -- falls back to the backend's own Hebrew ``title_he`` instead of a raw dotted key.
const TOPIC_LABEL_KEYS: Partial<Record<string, TranslationKey>> = {
  specifications: "dossiers.sections.specifications",
  versions: "dossiers.sections.versions",
  performance: "dossiers.sections.performance",
  maturity: "dossiers.sections.maturity",
  deals: "dossiers.sections.deals",
  pricing: "dossiers.sections.pricing",
  partnerships: "dossiers.sections.partnerships",
  competitors: "dossiers.sections.competitors",
};

const STATUS_LABEL_KEYS: Record<DossierProgressTopic["status"], TranslationKey> = {
  pending: "dossiers.progress.status.pending",
  running: "dossiers.progress.status.running",
  done: "dossiers.progress.status.done",
  failed: "dossiers.progress.status.failed",
};

function StatusIcon({ status }: { status: DossierProgressTopic["status"] }) {
  switch (status) {
    case "done":
      return <CircleCheck size={14} className="shrink-0 text-ok" aria-hidden="true" />;
    case "running":
      return <Loader2 size={14} className="shrink-0 animate-spin text-accent" aria-hidden="true" />;
    case "failed":
      return <CircleX size={14} className="shrink-0 text-danger" aria-hidden="true" />;
    default:
      return <Circle size={14} className="shrink-0 text-fg-dim" aria-hidden="true" />;
  }
}

export function DossierProgressList({ progress }: { progress: DossierProgressTopic[] }) {
  const t = useT();
  if (progress.length === 0) return null;
  return (
    <ul className="mt-2 grid grid-cols-1 gap-x-4 gap-y-1 text-xs sm:grid-cols-2" data-testid="dossier-progress-list">
      {progress.map((topic) => {
        const labelKey = TOPIC_LABEL_KEYS[topic.topic];
        const label = labelKey ? t(labelKey) : null;
        return (
          <li key={topic.topic} className="flex items-center gap-1.5" data-testid={`dossier-progress-topic-${topic.topic}`}>
            <StatusIcon status={topic.status} />
            <span className="min-w-0 truncate">{label ? label : <bdi>{topic.title_he}</bdi>}</span>
            <span className="text-fg-dim">{t(STATUS_LABEL_KEYS[topic.status])}</span>
            {topic.seconds != null && (
              <span className="text-fg-dim">· {t("dossiers.progress.seconds", { s: Math.round(topic.seconds) })}</span>
            )}
            {topic.sources_found != null && topic.sources_found > 0 && (
              <span className="text-fg-dim">· {t("dossiers.progress.sourcesFound", { n: topic.sources_found })}</span>
            )}
          </li>
        );
      })}
    </ul>
  );
}
