import { Component, type ReactNode } from 'react'

/**
 * The last line of defence: if a screen fails to draw, say so and offer a way
 * back, instead of leaving a blank page. Reporting is done at React's root; this
 * only decides what the person sees.
 */
export class CrashScreen extends Component<{ children: ReactNode }, { failed: boolean }> {
  state = { failed: false }

  static getDerivedStateFromError(): { failed: boolean } {
    return { failed: true }
  }

  render(): ReactNode {
    if (!this.state.failed) return this.props.children
    return (
      <main className="crash" role="alert">
        <h1>Something went wrong on this page.</h1>
        <p>
          Nothing you had already saved is affected. Reloading usually puts it right; anything
          typed into an open form since the last save may need typing again.
        </p>
        <div className="actions">
          <button type="button" className="btn primary" onClick={() => window.location.reload()}>
            Reload the page
          </button>
          <a className="btn" href="/">
            Go to your library
          </a>
        </div>
      </main>
    )
  }
}
