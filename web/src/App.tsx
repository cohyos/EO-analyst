import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { BrowserRouter, Route, Routes } from "react-router-dom";
import { AppShell } from "@/components/shell/AppShell";
import { MorningPage } from "@/pages/MorningPage";
import { FeedPage } from "@/pages/FeedPage";
import { ItemDetailPage } from "@/pages/ItemDetailPage";
import { EntitiesListPage } from "@/pages/EntitiesListPage";
import { EntityDetailPage } from "@/pages/EntityDetailPage";
import { InvestigationsListPage } from "@/pages/InvestigationsListPage";
import { InvestigationDetailPage } from "@/pages/InvestigationDetailPage";
import { AskPage } from "@/pages/AskPage";
import { ConferencesPage } from "@/pages/ConferencesPage";
import { InboxPage } from "@/pages/InboxPage";
import { ReportsPage } from "@/pages/ReportsPage";
import { SettingsPage } from "@/pages/SettingsPage";
import { NotFoundPage } from "@/pages/NotFoundPage";

const queryClient = new QueryClient({
  defaultOptions: {
    queries: {
      staleTime: 15_000,
      retry: 1,
      refetchOnWindowFocus: false,
    },
  },
});

export default function App() {
  return (
    <QueryClientProvider client={queryClient}>
      <BrowserRouter>
        <Routes>
          <Route element={<AppShell />}>
            <Route index element={<MorningPage />} />
            <Route path="feed" element={<FeedPage />} />
            <Route path="items/:id" element={<ItemDetailPage />} />
            <Route path="entities" element={<EntitiesListPage />} />
            <Route path="entities/:id" element={<EntityDetailPage />} />
            <Route path="investigations" element={<InvestigationsListPage />} />
            <Route path="investigations/:jobId" element={<InvestigationDetailPage />} />
            <Route path="ask" element={<AskPage />} />
            <Route path="conferences" element={<ConferencesPage />} />
            <Route path="inbox" element={<InboxPage />} />
            <Route path="reports" element={<ReportsPage />} />
            <Route path="settings" element={<SettingsPage />} />
            <Route path="*" element={<NotFoundPage />} />
          </Route>
        </Routes>
      </BrowserRouter>
    </QueryClientProvider>
  );
}
