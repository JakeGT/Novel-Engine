import { Icon } from '../components/icons'
import { InkCanvas } from '../components/InkCanvas'
import { Logo } from '../components/Logo'
import { ThemeToggle } from '../components/ThemeToggle'

const REMEMBERS: { title: string; text: string }[] = [
  {
    title: 'Facts, not notes',
    text: '“Mara is a member of the Harbor Guild” is a fact the bible understands. It shows on both entries, and you end it when it stops being true.',
  },
  {
    title: 'Belief and truth, apart',
    text: 'Record what a character thinks happened. The bible shows whether the story agrees, contradicts it, or has not said.',
  },
  {
    title: 'Nothing behind your back',
    text: 'Whatever the assistant drafts waits for you. You read it as plain sentences, keep or skip each one, and only then is it true.',
  },
  {
    title: 'Every version kept',
    text: 'Each time you accept something, a version is saved. See what changed and why, and go back to any earlier point.',
  },
  {
    title: 'Loose threads, listed',
    text: 'Mention someone new and they get a place in the bible and a spot on your to-do list, ready to be filled in when you know more.',
  },
  {
    title: 'Yours, and private',
    text: 'It runs on your own AI key, so you control the cost. Access is by invitation, with owner, editor and read-only roles.',
  },
]

const LADDER: { name: string; text: string; ready: boolean }[] = [
  { name: 'Start', text: 'Describe an idea. Get a story bible.', ready: true },
  { name: 'Shape', text: 'Fill in, connect, and correct your world.', ready: true },
  { name: 'Plan', text: 'Talk your way to arcs, beats, and scenes.', ready: false },
  { name: 'Write', text: 'Draft scenes with the bible beside you.', ready: false },
  { name: 'Control', text: 'Timelines, secrets, what-ifs, and rules.', ready: false },
]

/** The public front page. Describes what the product does today, and labels what is still to come. */
export function Landing() {
  return (
    <div className="landing" id="top">
      <a className="skip" href="#main">
        Skip to content
      </a>
      <header className="landing-nav">
        <div className="inner">
          <a className="brand" href="#top" aria-label="Novel Engine, back to the top">
            <Logo />
            Novel Engine
          </a>
          <nav className="links" aria-label="Page sections">
            <a href="#trouble">Why</a>
            <a href="#how">How it works</a>
            <a href="#remembers">What it remembers</a>
          </nav>
          <ThemeToggle />
          <a className="btn primary" href="/auth/login">
            Sign in
          </a>
        </div>
      </header>

      <main id="main">
        <section className="hero">
          <InkCanvas className="hero-ink" />
          <div className="inner">
            <p className="eyebrow">A story bible that keeps itself</p>
            <h1>
              Every story
              <br />
              needs a <em>memory.</em>
            </h1>
            <p className="sub">
              Tell Novel Engine about your world the way you would tell a friend. It asks what it
              needs to know, writes it all down, and connects it — one place that holds what is
              true in your story, for as long as the story runs.
            </p>
            <div className="cta">
              <a className="btn primary lg" href="/auth/login">
                Open your library <Icon name="arrowRight" />
              </a>
              <a className="text-link" href="#how">
                See how it works
              </a>
            </div>
            <p className="fine">Invitation only for now. Sign in with the account you were invited with.</p>
          </div>
          <p className="figure-note" aria-hidden="true">
            Fig. 1 — ink, finding its threads.
          </p>
        </section>

        <section className="band" id="trouble">
          <div className="inner split">
            <h2 className="statement">
              Long stories <em>forget.</em>
              <br />
              This one won’t.
            </h2>
            <div>
              <p className="dropcap">
                Somewhere past the hundredth page, every novelist becomes their own continuity
                editor. Novel Engine doesn’t write your book. It remembers it — who everyone is,
                how they are connected, what they believe, and how each of those things came to be
                true.
              </p>
              <ol className="errata" aria-label="The kind of mistakes long stories make">
                <li>
                  <span>p. 14</span> Her eyes are green. <span>p. 311</span> Her eyes are grey.
                </li>
                <li>
                  <span>ch. 9</span> He dies at the bridge. <span>ch. 30</span> He toasts the bride.
                </li>
                <li>
                  <span>act ii</span> Nobody told her the secret. <span>act iii</span> She acts on it.
                </li>
              </ol>
            </div>
          </div>
        </section>

        <section className="band ruled" id="how">
          <div className="inner">
            <p className="eyebrow">How it works</p>
            <ol className="chapters">
              <li>
                <div className="chapter-text">
                  <span className="numeral" aria-hidden="true">
                    I
                  </span>
                  <h3>Describe it</h3>
                  <p>
                    One character or the whole premise, a sentence or a page. No forms, no fields,
                    nothing to learn first.
                  </p>
                </div>
                <figure className="specimen note" aria-hidden="true">
                  <p>
                    Mara Venn is a harbor pilot in Saltmere. She belongs to the Harbor Guild, owns a
                    brass sextant, and doesn’t trust Tobin Ashe
                    <span className="caret" />
                  </p>
                </figure>
              </li>
              <li>
                <div className="chapter-text">
                  <span className="numeral" aria-hidden="true">
                    II
                  </span>
                  <h3>Answer a few questions</h3>
                  <p>
                    The assistant asks only what your description left open — in your story’s own
                    names — and you answer as many as you like. Or skip them all.
                  </p>
                </div>
                <figure className="specimen script" aria-hidden="true">
                  <p>
                    <span>Assistant</span>Why doesn’t Mara trust Tobin?
                  </p>
                  <p>
                    <span>You</span>He sold the guild’s charts to the navy. She can’t prove it.
                  </p>
                  <p>
                    <span>Assistant</span>Does Tobin know she suspects him?
                  </p>
                </figure>
              </li>
              <li>
                <div className="chapter-text">
                  <span className="numeral" aria-hidden="true">
                    III
                  </span>
                  <h3>Keep what’s true</h3>
                  <p>
                    It drafts the entries and the links between them. You read each one as a plain
                    sentence, keep or skip it, and the rest becomes your story bible.
                  </p>
                </div>
                <figure className="specimen proof" aria-hidden="true">
                  <p className="kept">
                    <span>keep</span>Mara Venn is a member of the Harbor Guild
                  </p>
                  <p className="kept">
                    <span>keep</span>Mara Venn believes Tobin Ashe sold the charts
                  </p>
                  <p className="struck">
                    <span>skip</span>Tobin Ashe is located at the naval yard
                  </p>
                </figure>
              </li>
            </ol>
          </div>
        </section>

        <section className="band" id="remembers">
          <div className="inner split wide-left">
            <figure className="specimen entry" aria-hidden="true">
              <p className="headword">
                Mara Venn <span>character · also “The Fox”</span>
              </p>
              <p>
                <i>is a member of</i> the Harbor Guild
              </p>
              <p>
                <i>is located at</i> Saltmere
              </p>
              <p>
                <i>owns</i> a brass sextant
              </p>
              <p className="belief">
                <i>believes</i> Tobin Ashe sold the charts <span>the story hasn’t said</span>
              </p>
              <p className="margin">Version 12 · added by you · 3 changes</p>
            </figure>
            <div>
              <p className="eyebrow">What it remembers</p>
              <h2>One source of truth, written in sentences.</h2>
              <dl className="ledger">
                {REMEMBERS.map((item) => (
                  <div key={item.title}>
                    <dt>{item.title}</dt>
                    <dd>{item.text}</dd>
                  </div>
                ))}
              </dl>
            </div>
          </div>
        </section>

        <section className="band ruled">
          <div className="inner">
            <p className="eyebrow">It grows with the book</p>
            <h2 className="narrow">Start with a sentence. Learn the rest when you need it.</h2>
            <ol className="ladder">
              {LADDER.map((step) => (
                <li key={step.name} className={step.ready ? 'ready' : ''}>
                  <strong>{step.name}</strong>
                  <span>{step.text}</span>
                  <em>{step.ready ? 'Here today' : 'Being built'}</em>
                </li>
              ))}
            </ol>
          </div>
        </section>

        <section className="closing">
          <div className="inner">
            <h2>
              Begin with <em>a sentence.</em>
            </h2>
            <a className="btn lg" href="/auth/login">
              Open your library <Icon name="arrowRight" />
            </a>
          </div>
        </section>
      </main>

      <footer>
        <div className="inner">
          <a className="brand" href="#top" aria-label="Novel Engine, back to the top">
            <Logo />
            Novel Engine
          </a>
          <span>Set in Newsreader and Instrument Sans. Invitation only.</span>
        </div>
      </footer>
    </div>
  )
}
