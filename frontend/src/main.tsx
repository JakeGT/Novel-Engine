import { StrictMode } from 'react'
import { createRoot } from 'react-dom/client'
import '@fontsource-variable/instrument-sans/index.css'
import '@fontsource-variable/newsreader/opsz.css'
import '@fontsource-variable/newsreader/opsz-italic.css'
import './styles/tokens.css'
import './styles/app.css'
import './styles/landing.css'
import App from './App'
import { CrashScreen } from './components/CrashScreen'
import { reportReactError, startMonitoring } from './lib/monitoring'
import { applyInitialTheme } from './lib/theme'

// Off unless the server handed the page a DSN (see lib/monitoring.ts).
startMonitoring()

applyInitialTheme()

createRoot(document.getElementById('root')!, {
  onUncaughtError: reportReactError,
  onCaughtError: reportReactError,
  onRecoverableError: reportReactError,
}).render(
  <StrictMode>
    <CrashScreen>
      <App />
    </CrashScreen>
  </StrictMode>,
)
