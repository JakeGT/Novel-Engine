import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { BrowserRouter, Navigate, Route, Routes, useParams } from 'react-router-dom'
import { SignedOutOverlay } from './components/SignedOutOverlay'
import { KeepSignedIn } from './lib/keepalive'
import { AssistantPage } from './pages/AssistantPage'
import { BiblePage } from './pages/BiblePage'
import { CustomizePage } from './pages/CustomizePage'
import { EntryPage } from './pages/EntryPage'
import { HistoryPage } from './pages/HistoryPage'
import { InvitePage } from './pages/InvitePage'
import { Home } from './pages/Home'
import { MediaPage } from './pages/MediaPage'
import { Overview } from './pages/Overview'
import { PeoplePage } from './pages/PeoplePage'
import { PlanPage } from './pages/PlanPage'
import { RulesPage } from './pages/RulesPage'
import { StudioPage } from './pages/StudioPage'
import { TextPage } from './pages/TextPage'
import { TextsPage } from './pages/TextsPage'
import { TimelinePage } from './pages/TimelinePage'
import { WhatIfPage, WhatIfsPage } from './pages/WhatIfsPage'
import { ProjectLayout } from './pages/ProjectLayout'
import { ReviewDetail } from './pages/ReviewDetail'
import { ReviewQueue } from './pages/ReviewQueue'

const queryClient = new QueryClient({
  defaultOptions: {
    queries: { retry: false, refetchOnWindowFocus: false, staleTime: 5_000 },
  },
})

/** Addresses from before the redesign keep working. */
function Moved({ to }: { to: (params: Record<string, string | undefined>) => string }) {
  return <Navigate to={to(useParams())} replace />
}

export default function App() {
  return (
    <QueryClientProvider client={queryClient}>
      <KeepSignedIn />
      <SignedOutOverlay />
      <BrowserRouter>
        <Routes>
          <Route path="/" element={<Home />} />
          <Route path="/invite/:token" element={<InvitePage />} />
          <Route path="/projects/:projectId" element={<ProjectLayout />}>
            <Route index element={<Overview />} />
            <Route path="bible" element={<BiblePage />} />
            <Route path="entries/:entityId" element={<EntryPage />} />
            <Route path="review" element={<ReviewQueue />} />
            <Route path="review/:mutationId" element={<ReviewDetail />} />
            <Route path="plan" element={<PlanPage />} />
            <Route path="plan/:planId/write" element={<StudioPage />} />
            <Route path="rules" element={<RulesPage />} />
            <Route path="timeline" element={<TimelinePage />} />
            <Route path="what-ifs" element={<WhatIfsPage />} />
            <Route path="what-ifs/:branchId" element={<WhatIfPage />} />
            <Route path="texts" element={<TextsPage />} />
            <Route path="texts/:sourceId" element={<TextPage />} />
            <Route path="history" element={<HistoryPage />} />
            <Route path="customize" element={<CustomizePage />} />
            <Route path="media" element={<MediaPage />} />
            <Route path="assistant" element={<AssistantPage />} />
            <Route path="people" element={<PeoplePage />} />
            <Route path="entities/:entityId" element={<Moved to={(p) => `../entries/${p.entityId}`} />} />
            <Route path="characters/:entityId" element={<Moved to={(p) => `../entries/${p.entityId}`} />} />
            <Route path="mutations" element={<Navigate to="../review" replace />} />
            <Route path="mutations/:mutationId" element={<Moved to={(p) => `../review/${p.mutationId}`} />} />
            <Route path="revisions" element={<Navigate to="../history" replace />} />
            <Route path="schema" element={<Navigate to="../customize" replace />} />
            <Route path="assets" element={<Navigate to="../media" replace />} />
          </Route>
          <Route path="*" element={<Navigate to="/" replace />} />
        </Routes>
      </BrowserRouter>
    </QueryClientProvider>
  )
}
