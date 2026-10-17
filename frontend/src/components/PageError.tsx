import * as Sentry from '@sentry/react'
import { Component, type ErrorInfo, type ReactNode } from 'react'
import { useLocation } from 'react-router-dom'

interface Props {
  children: ReactNode
  /** Where "Go back to the start" leads: the story's home, or the list of stories. */
  home: string
  resetKey: string
}

interface State {
  failed: boolean
}

/**
 * A page that stops working shows this instead of a blank screen. Nothing the author
 * saved is affected, and words not yet saved in the studio are kept in this browser.
 * The error is reported (when reporting is on) with no page content.
 */
class Boundary extends Component<Props, State> {
  state: State = { failed: false }

  static getDerivedStateFromError(): State {
    return { failed: true }
  }

  componentDidCatch(error: Error, info: ErrorInfo) {
    Sentry.captureException(error, { contexts: { react: { componentStack: info.componentStack ?? '' } } })
  }

  componentDidUpdate(previous: Props) {
    // Going to another page tries again.
    if (this.state.failed && previous.resetKey !== this.props.resetKey) this.setState({ failed: false })
  }

  render() {
    if (!this.state.failed) return this.props.children
    return (
      <section className="page-error stack" role="alert" aria-labelledby="page-error-title">
        <h1 id="page-error-title">This page stopped working</h1>
        <p className="lede">
          Nothing you saved is affected. Words you had not yet saved in a scene are kept in this
          browser and come back when you open the scene again.
        </p>
        <div className="actions">
          <button type="button" className="btn primary" onClick={() => window.location.reload()}>
            Reload the page
          </button>
          <a className="btn" href={this.props.home}>
            Go back to the start
          </a>
        </div>
      </section>
    )
  }
}

export function PageError({ children, home }: { children: ReactNode; home: string }) {
  const location = useLocation()
  return (
    <Boundary home={home} resetKey={location.pathname}>
      {children}
    </Boundary>
  )
}
