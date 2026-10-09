import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { BrowserRouter, Navigate, Route, Routes } from 'react-router-dom'
import './App.css'
import { ProjectLayout } from './pages/ProjectLayout'
import { WorkspaceHome } from './pages/WorkspaceHome'
import { EntityList } from './pages/EntityList'
import { EntityDetail } from './pages/EntityDetail'
import { CharacterView } from './pages/CharacterView'
import { SchemaPage } from './pages/SchemaPage'
import { MutationQueue } from './pages/MutationQueue'
import { MutationDetail } from './pages/MutationDetail'
import { RevisionsPage } from './pages/RevisionsPage'
import { AssetsPage } from './pages/AssetsPage'

const queryClient = new QueryClient({
  defaultOptions: {
    queries: { retry: false, refetchOnWindowFocus: false, staleTime: 5_000 },
  },
})

export default function App() {
  return (
    <QueryClientProvider client={queryClient}>
      <BrowserRouter>
        <Routes>
          <Route path="/" element={<WorkspaceHome />} />
          <Route path="/projects/:projectId" element={<ProjectLayout />}>
            <Route index element={<EntityList />} />
            <Route path="entities/:entityId" element={<EntityDetail />} />
            <Route path="characters/:entityId" element={<CharacterView />} />
            <Route path="mutations" element={<MutationQueue />} />
            <Route path="mutations/:mutationId" element={<MutationDetail />} />
            <Route path="revisions" element={<RevisionsPage />} />
            <Route path="schema" element={<SchemaPage />} />
            <Route path="assets" element={<AssetsPage />} />
          </Route>
          <Route path="*" element={<Navigate to="/" replace />} />
        </Routes>
      </BrowserRouter>
    </QueryClientProvider>
  )
}
