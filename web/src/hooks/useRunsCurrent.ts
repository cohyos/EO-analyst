import { useQuery } from "@tanstack/react-query";
import { api } from "@/api";

/**
 * U4/F17: polls `GET /api/runs/current` so the "run now" button's popover and the status-strip's
 * background indicator share one source of truth for "what's running right now" (the primary
 * run's stage progress/ETA, plus any other job — e.g. a `deep_search` — a separate worker claimed
 * concurrently and that would otherwise never show up anywhere in the UI, per F17).
 *
 * Polls every 4s while something is active, backing off to 15s when idle — cheap either way (one
 * `jobs`/`run_log` read), but no reason to hammer the server once there's nothing to watch.
 */
export function useRunsCurrent() {
  return useQuery({
    queryKey: ["runs-current"],
    queryFn: () => api.getRunsCurrent(),
    refetchInterval: (query) => {
      const data = query.state.data;
      const active = !!data?.current || (data?.other_running.length ?? 0) > 0;
      return active ? 4000 : 15000;
    },
  });
}
