import { useState } from "react";
import { Link } from "react-router-dom";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Trash2 } from "lucide-react";
import { api } from "@/api";
import { EmptyState, ErrorState, LoadingState } from "@/components/states";
import { SecurityReviewBanner } from "@/components/investigations/SecurityReviewBanner";
import { formatDateTime } from "@/lib/time";

export function InboxPage() {
  const queryClient = useQueryClient();

  const clarificationsQuery = useQuery({
    queryKey: ["clarifications"],
    queryFn: () => api.getClarifications(true),
  });
  const surveyQuery = useQuery({
    queryKey: ["survey"],
    queryFn: () => api.getLatestSurvey(),
  });
  const lessonsQuery = useQuery({
    queryKey: ["lessons"],
    queryFn: () => api.getLessons(),
  });
  // W10 (docs/REVIEW_2026-09-06_evening.md round 4): every deep-search investigation the L2
  // security guard partially blocked and nobody has approved/dismissed yet.
  const securityReviewsQuery = useQuery({
    queryKey: ["security-reviews"],
    queryFn: () => api.getSecurityReviews(),
  });
  // `pending` tracks which single row is mid-action (and which action) so only that row's buttons
  // disable/relabel -- both mutations are shared across every row in the list.
  const [pending, setPending] = useState<{
    jobId: string;
    action: "approve" | "dismiss";
  } | null>(null);
  const approveSecurityReview = useMutation({
    mutationFn: (jobId: string) => api.postSecurityReviewApprove(jobId),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ["security-reviews"] }),
    onSettled: () => setPending(null),
  });
  const dismissSecurityReview = useMutation({
    mutationFn: (jobId: string) => api.postSecurityReviewDismiss(jobId),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ["security-reviews"] }),
    onSettled: () => setPending(null),
  });

  const answerClarification = useMutation({
    mutationFn: ({ id, answer }: { id: number; answer: string }) =>
      api.postClarificationAnswer(id, answer),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ["clarifications"] }),
  });

  const deleteLesson = useMutation({
    mutationFn: (id: number) => api.deleteLesson(id),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ["lessons"] }),
  });

  const [surveyAnswers, setSurveyAnswers] = useState<Record<string, unknown>>({});
  const [surveySubmitted, setSurveySubmitted] = useState(false);
  const submitSurvey = useMutation({
    mutationFn: () => api.postSurveyAnswers(surveyQuery.data!.id, surveyAnswers),
    onSuccess: () => setSurveySubmitted(true),
  });

  return (
    <div className="mx-auto max-w-3xl space-y-8 p-4 md:p-6">
      <section aria-label="בדיקות אבטחה ממתינות">
        <h2 className="mb-2 text-sm font-semibold text-fg-dim">בדיקות אבטחה ממתינות</h2>
        {securityReviewsQuery.isLoading && <LoadingState label="טוען בדיקות אבטחה…" />}
        {securityReviewsQuery.isError && (
          <ErrorState
            error={securityReviewsQuery.error}
            onRetry={() => securityReviewsQuery.refetch()}
          />
        )}
        {securityReviewsQuery.data && securityReviewsQuery.data.length === 0 && (
          <EmptyState title="אין בדיקות אבטחה ממתינות" />
        )}
        {securityReviewsQuery.data && securityReviewsQuery.data.length > 0 && (
          <ul className="space-y-3">
            {securityReviewsQuery.data.map((review) => {
              const jobId = String(review.job_id);
              return (
                <li key={review.job_id}>
                  <SecurityReviewBanner
                    reasonHe={review.reason_he}
                    snippet={review.snippet}
                    onApprove={() => {
                      setPending({ jobId, action: "approve" });
                      approveSecurityReview.mutate(jobId);
                    }}
                    onDismiss={() => {
                      setPending({ jobId, action: "dismiss" });
                      dismissSecurityReview.mutate(jobId);
                    }}
                    approving={pending?.jobId === jobId && pending.action === "approve"}
                    dismissing={pending?.jobId === jobId && pending.action === "dismiss"}
                  />
                  <p className="mt-1 text-xs text-fg-dim">
                    <bdi>{review.item_title ?? review.question ?? `חקירה #${jobId}`}</bdi>{" "}
                    ·{" "}
                    <Link
                      to={`/investigations/${jobId}`}
                      className="text-accent hover:underline"
                    >
                      פתח חקירה
                    </Link>
                  </p>
                </li>
              );
            })}
          </ul>
        )}
      </section>

      <section aria-label="הבהרות פתוחות">
        <h2 className="mb-2 text-sm font-semibold text-fg-dim">הבהרות פתוחות</h2>
        {clarificationsQuery.isLoading && <LoadingState label="טוען הבהרות…" />}
        {clarificationsQuery.isError && (
          <ErrorState
            error={clarificationsQuery.error}
            onRetry={() => clarificationsQuery.refetch()}
          />
        )}
        {clarificationsQuery.data && clarificationsQuery.data.length === 0 && (
          <EmptyState title="אין הבהרות פתוחות" description="כל השאלות טופלו." />
        )}
        {clarificationsQuery.data && clarificationsQuery.data.length > 0 && (
          <ul className="space-y-2">
            {clarificationsQuery.data.map((c) => (
              <li key={c.id} className="rounded-lg border border-border bg-bg-raised p-3">
                <p className="mb-1 text-sm">{c.question}</p>
                <p className="mb-2 text-xs text-fg-dim">
                  נשאל {formatDateTime(c.asked_at)}
                  {c.timeout_at && ` · תפוגה ${formatDateTime(c.timeout_at)}`}
                </p>
                <div className="flex flex-wrap gap-2">
                  {(c.options ?? ["כן", "לא"]).map((opt) => (
                    <button
                      key={opt}
                      type="button"
                      onClick={() =>
                        answerClarification.mutate({ id: c.id, answer: opt })
                      }
                      disabled={answerClarification.isPending}
                      className="rounded-md border border-border-strong px-2.5 py-1 text-xs hover:bg-bg-sunken disabled:opacity-50"
                    >
                      {opt}
                    </button>
                  ))}
                </div>
              </li>
            ))}
          </ul>
        )}
      </section>

      <section aria-label="שאלון יומי">
        <h2 className="mb-2 text-sm font-semibold text-fg-dim">שאלון יומי</h2>
        {surveyQuery.isLoading && <LoadingState label="טוען שאלון…" />}
        {!surveyQuery.isLoading && !surveyQuery.data && (
          <EmptyState title="אין שאלון זמין" description="השאלון היומי טרם נוצר." />
        )}
        {surveyQuery.data && !surveySubmitted && (
          <form
            onSubmit={(e) => {
              e.preventDefault();
              submitSurvey.mutate();
            }}
            className="space-y-4 rounded-lg border border-border bg-bg-raised p-3"
          >
            {(surveyQuery.data.questions ?? []).map((q) => (
              <div key={q.id}>
                <label className="mb-1 block text-sm" htmlFor={`q-${q.id}`}>
                  {q.text_he}
                </label>
                {q.type === "choice" && (
                  <select
                    id={`q-${q.id}`}
                    className="w-full rounded-md border border-border-strong bg-bg px-2 py-1.5 text-sm"
                    onChange={(e) =>
                      setSurveyAnswers((prev) => ({ ...prev, [q.id]: e.target.value }))
                    }
                  >
                    <option value="">בחר…</option>
                    {(q.options ?? []).map((opt) => (
                      <option key={opt} value={opt}>
                        {opt}
                      </option>
                    ))}
                  </select>
                )}
                {q.type === "scale" && (
                  <div className="flex items-center gap-2">
                    {[1, 2, 3, 4, 5].map((n) => (
                      <button
                        type="button"
                        key={n}
                        onClick={() =>
                          setSurveyAnswers((prev) => ({ ...prev, [q.id]: n }))
                        }
                        className={`h-8 w-8 rounded-full border text-sm ${
                          surveyAnswers[q.id] === n
                            ? "border-accent bg-accent-muted text-accent-fg"
                            : "border-border-strong text-fg-muted hover:bg-bg-sunken"
                        }`}
                      >
                        {n}
                      </button>
                    ))}
                  </div>
                )}
                {q.type === "text" && (
                  <textarea
                    id={`q-${q.id}`}
                    rows={2}
                    className="w-full rounded-md border border-border-strong bg-bg px-2 py-1.5 text-sm"
                    onChange={(e) =>
                      setSurveyAnswers((prev) => ({ ...prev, [q.id]: e.target.value }))
                    }
                  />
                )}
              </div>
            ))}
            <button
              type="submit"
              disabled={submitSurvey.isPending}
              className="rounded-md bg-accent px-3 py-1.5 text-sm font-medium text-accent-fg hover:opacity-90 disabled:opacity-50"
            >
              שלח משוב
            </button>
          </form>
        )}
        {surveySubmitted && (
          <p className="rounded-lg border border-border bg-bg-raised p-3 text-sm text-ok">
            תודה — המשוב נשלח.
          </p>
        )}
      </section>

      <section aria-label="מה למדתי ממך">
        <h2 className="mb-2 text-sm font-semibold text-fg-dim">מה למדתי ממך</h2>
        {lessonsQuery.isLoading && <LoadingState label="טוען לקחים…" />}
        {lessonsQuery.data && lessonsQuery.data.length === 0 && (
          <EmptyState title="אין עדיין לקחים שנרשמו" />
        )}
        {lessonsQuery.data && lessonsQuery.data.length > 0 && (
          <ul className="space-y-2">
            {lessonsQuery.data.map((l) => (
              <li
                key={l.id}
                className="flex items-start gap-2 rounded-lg border border-border bg-bg-raised p-3"
              >
                <span className="rounded bg-bg-sunken px-1.5 py-0.5 text-xs text-fg-dim">
                  {l.kind}
                </span>
                <bdi className="flex-1 text-sm">{l.text}</bdi>
                <button
                  type="button"
                  onClick={() => deleteLesson.mutate(l.id)}
                  className="rounded p-1 text-fg-dim hover:bg-bg-sunken hover:text-danger"
                  aria-label="מחק לקח"
                >
                  <Trash2 size={14} aria-hidden="true" />
                </button>
              </li>
            ))}
          </ul>
        )}
      </section>
    </div>
  );
}
