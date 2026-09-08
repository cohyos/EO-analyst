import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { BrowserRouter, Route, Routes } from "react-router-dom";
import { I18nProvider } from "@/i18n";
import { AccessGate } from "@/components/auth/AccessGate";
import { AppShell } from "@/components/shell/AppShell";
import { MorningPage } from "@/pages/MorningPage";
import { FeedPage } from "@/pages/FeedPage";
import { ItemDetailPage } from "@/pages/ItemDetailPage";
import { EntitiesPage } from "@/pages/EntitiesPage";
import { InvestigationsListPage } from "@/pages/InvestigationsListPage";
import { InvestigationDetailPage } from "@/pages/InvestigationDetailPage";
import { AskPage } from "@/pages/AskPage";
import { ConferencesPage } from "@/pages/ConferencesPage";
import { TendersPage } from "@/pages/TendersPage";
import { PatentsPage } from "@/pages/PatentsPage";
import { PayloadsPage } from "@/pages/PayloadsPage";
import { InboxPage } from "@/pages/InboxPage";
import { ReportsPage } from "@/pages/ReportsPage";
import { BdPage } from "@/pages/BdPage";
import { ProductLinesPage } from "@/pages/ProductLinesPage";
import { ProductLineDetailPage } from "@/pages/ProductLineDetailPage";
import { DossiersPage } from "@/pages/DossiersPage";
import { DossierDetailPage } from "@/pages/DossierDetailPage";
import { DossierComparePage } from "@/pages/DossierComparePage";
import { TechRadarPage } from "@/pages/TechRadarPage";
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
      <I18nProvider>
        {/* ADR-008 (docs/adr/008-remote-access.md): renders the passcode form instead of the app
            whenever the API has told us (via a 401 auth_required) that this client -- reaching the
            API from a non-loopback host -- needs a session first. A no-op for the primary local
            usage this app was built around. */}
        <AccessGate>
          <BrowserRouter>
            <Routes>
              <Route element={<AppShell />}>
                <Route index element={<MorningPage />} />
                <Route path="feed" element={<FeedPage />} />
                <Route path="items/:id" element={<ItemDetailPage />} />
                <Route path="entities" element={<EntitiesPage />} />
                <Route path="entities/:id" element={<EntitiesPage />} />
                <Route path="investigations" element={<InvestigationsListPage />} />
                <Route path="investigations/:jobId" element={<InvestigationDetailPage />} />
                <Route path="ask" element={<AskPage />} />
                <Route path="conferences" element={<ConferencesPage />} />
                <Route path="tenders" element={<TendersPage />} />
                <Route path="patents" element={<PatentsPage />} />
                <Route path="payloads" element={<PayloadsPage />} />
                <Route path="inbox" element={<InboxPage />} />
                <Route path="reports" element={<ReportsPage />} />
                <Route path="bd" element={<BdPage />} />
                <Route path="product-lines" element={<ProductLinesPage />} />
                <Route path="product-lines/:id" element={<ProductLineDetailPage />} />
                <Route path="dossiers" element={<DossiersPage />} />
                <Route path="dossiers/compare" element={<DossierComparePage />} />
                <Route path="dossiers/:key" element={<DossierDetailPage />} />
                <Route path="tech-radar" element={<TechRadarPage />} />
                <Route path="settings" element={<SettingsPage />} />
                <Route path="*" element={<NotFoundPage />} />
              </Route>
            </Routes>
          </BrowserRouter>
        </AccessGate>
      </I18nProvider>
    </QueryClientProvider>
  );
}
