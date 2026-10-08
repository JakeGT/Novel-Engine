import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { BrowserRouter, Navigate, Route, Routes } from 'react-router-dom'
import './App.css'
import { ProjectLayout } from './pages/ProjectLayout'
import { WorkspaceHome } from './pages/WorkspaceHome'
import { EntityList } from './pages/EntityList'
import { EntityDetail } from './pages/EntityDetail'

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
          </Route>
          <Route path="*" element={<Navigate to="/" replace />} />
        </Routes>
      </BrowserRouter>
    </QueryClientProvider>
  )
}
