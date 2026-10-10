import { Icon, type IconName } from '../components/icons'
import { ThemeToggle } from '../components/ThemeToggle'

const PILLARS: { icon: IconName; tint: string; title: string; text: string }[] = [
  {
    icon: 'book',
    tint: 'violet',
    title: 'It remembers everything',
    text: 'Every character, place, object and secret lives in one story bible. Connect them with plain facts — who knows whom, who owns what, where everyone is.',
  },
  {
    icon: 'shield',
    tint: 'teal',
    title: 'Nothing changes behind your back',
    text: 'Suggestions from the AI assistant or an import never touch your canon directly. You see each change, keep or skip it, and only then does it become true.',
  },
  {
    icon: 'clock',
    tint: 'amber',
    title: 'Every change has a history',
    text: 'Each save is a version. See what changed, when, and why — and wind the whole bible back to any earlier point without losing a thing.',
  },
]

const FEATURES: { icon: IconName; title: string; text: string }[] = [
  {
    icon: 'link',
    title: 'Facts, not just notes',
    text: '“Mara is a member of the Harbor Guild” is a fact the bible understands: it shows on both entries, and you end it when it stops being true.',
  },
  {
    icon: 'eye',
    title: 'What they believe vs. what is true',
    text: 'Record what a character thinks happened. The bible shows whether canon agrees, contradicts it, or says nothing yet.',
  },
  {
    icon: 'inbox',
    title: 'A review inbox',
    text: 'Bigger or riskier changes wait for you in one place, written as plain sentences with a before and after.',
  },
  {
    icon: 'sliders',
    title: 'Shaped to your world',
    text: 'Add your own kinds of entries — ships, spells, bloodlines — with the fields that matter to your story.',
  },
  {
    icon: 'chat',
    title: 'A guided character interview',
    text: 'Optional. Connect your own AI key and it asks questions about a character, then drafts suggestions for you to review.',
  },
  {
    icon: 'key',
    title: 'Private and by invitation',
    text: 'Your workspace is yours. Access is by invitation, with owner, editor and read-only roles.',
  },
]

function Collage() {
  return (
    <div className="collage" aria-hidden="true">
      <div className="mock mock-entry">
        <div className="mock-head">
          <span className="avatar lg tint-violet">M</span>
          <div>
            <div className="mock-title">Mara Venn</div>
            <span className="badge accent">Character</span>{' '}
            <span className="muted small">also “The Fox”</span>
          </div>
        </div>
        <div className="mock-lines">
          <span>
            <span>is a member of</span> <strong>the Harbor Guild</strong>
          </span>
          <span>
            <span>is located at</span> <strong>Saltmere</strong>
          </span>
          <span>
            <span>owns</span> <strong>the brass sextant</strong>
          </span>
        </div>
      </div>

      <div className="mock mock-web">
        <svg className="web" viewBox="0 0 220 170" width="100%" role="presentation">
          <line x1="110" y1="85" x2="42" y2="34" />
          <line x1="110" y1="85" x2="182" y2="40" />
          <line x1="110" y1="85" x2="50" y2="138" />
          <line x1="110" y1="85" x2="176" y2="134" />
          <line x1="42" y1="34" x2="182" y2="40" />
          <circle cx="110" cy="85" r="19" fill="var(--tint-violet-bg)" />
          <circle cx="42" cy="34" r="13" fill="var(--tint-rose-bg)" />
          <circle cx="182" cy="40" r="13" fill="var(--tint-teal-bg)" />
          <circle cx="50" cy="138" r="13" fill="var(--tint-amber-bg)" />
          <circle cx="176" cy="134" r="13" fill="var(--tint-blue-bg)" />
          <text x="110" y="88.5" textAnchor="middle">Mara</text>
          <text x="42" y="14" textAnchor="middle" className="lbl">Harbor Guild</text>
          <text x="182" y="20" textAnchor="middle" className="lbl">Saltmere</text>
          <text x="50" y="164" textAnchor="middle" className="lbl">Sextant</text>
          <text x="176" y="160" textAnchor="middle" className="lbl">The Storm</text>
        </svg>
      </div>

      <div className="mock mock-review">
        <span className="eyebrow">Suggested change · AI assistant</span>
        <div className="mock-row">
          <span>
            Mara <span className="muted">knows</span> <strong>Tobin Ashe</strong>
          </span>
          <span className="mock-toggle">
            <span className="on">Keep</span>
            <span>Skip</span>
          </span>
        </div>
        <div className="mock-row">
          <span>
            Mara <span className="muted">owns</span> <strong>a second ship</strong>
          </span>
          <span className="mock-toggle">
            <span>Keep</span>
            <span className="on" style={{ color: 'var(--danger)' }}>
              Skip
            </span>
          </span>
        </div>
      </div>
    </div>
  )
}

/** The public front page. Describes only what the product does today. */
export function Landing() {
  return (
    <div className="landing">
      <a className="skip" href="#main">
        Skip to content
      </a>
      <header className="landing-nav">
        <div className="inner">
          <span className="brand">
            <span className="brand-mark">
              <Icon name="feather" size={17} />
            </span>
            Novel Engine
          </span>
          <nav className="links" aria-label="Page sections">
            <a href="#why">Why</a>
            <a href="#how">How it works</a>
            <a href="#features">Features</a>
          </nav>
          <ThemeToggle />
          <a className="btn primary" href="/auth/login">
            Sign in
          </a>
        </div>
      </header>

      <main id="main">
        <section className="hero">
          <div className="inner">
            <div>
              <span className="hero-kicker">
                <Icon name="spark" size={15} /> A story bible that keeps itself straight
              </span>
              <h1>
                Your story’s <em>memory</em>.
              </h1>
              <p className="sub">
                Novel Engine keeps every character, place and secret consistent across a long
                book, so you can write chapter thirty without rereading the first twenty-nine.
              </p>
              <div className="cta">
                <a className="btn primary lg" href="/auth/login">
                  Open your library <Icon name="arrowRight" />
                </a>
                <a className="btn lg" href="#how">
                  See how it works
                </a>
              </div>
              <p className="fine">Invitation only for now. Sign in with the account you were invited with.</p>
            </div>
            <Collage />
          </div>
        </section>

        <section className="band tinted" id="why">
          <div className="inner">
            <p className="quote">
              Long stories <em>forget things</em>. Novel Engine doesn’t write your book — it
              remembers it.
            </p>
            <div className="slips">
              <div className="slip">
                <strong>Eye colours drift.</strong>
                Green in chapter two, grey by chapter nineteen.
              </div>
              <div className="slip">
                <strong>The dead walk back in.</strong>
                A character killed off-page turns up at the wedding.
              </div>
              <div className="slip">
                <strong>Secrets leak.</strong>
                Someone knows a thing they were never told.
              </div>
              <div className="slip">
                <strong>Notes rot.</strong>
                The wiki says one thing, the draft says another, and nobody knows which won.
              </div>
            </div>
          </div>
        </section>

        <section className="band">
          <div className="inner">
            <h2>One source of truth for your world.</h2>
            <p className="lede">
              Not a pile of notes. A record of what is true in your story, that only changes when
              you say so.
            </p>
            <div className="pillars">
              {PILLARS.map((pillar) => (
                <article className="pillar" key={pillar.title}>
                  <span className={`glyph tint-${pillar.tint}`}>
                    <Icon name={pillar.icon} size={22} />
                  </span>
                  <h3>{pillar.title}</h3>
                  <p>{pillar.text}</p>
                </article>
              ))}
            </div>
          </div>
        </section>

        <section className="band tinted" id="how">
          <div className="inner">
            <h2>From blank page to living bible in three steps.</h2>
            <ol className="how">
              <li>
                <h3>Add who and what matters</h3>
                <p>
                  Start with a name. Characters, places, factions, objects — add detail whenever
                  you have it, not before.
                </p>
              </li>
              <li>
                <h3>Connect them with facts</h3>
                <p>
                  Build sentences from a menu: <em>Mara · is a member of · the Harbor Guild.</em>{' '}
                  No forms to learn, no syntax.
                </p>
              </li>
              <li>
                <h3>Review, then it’s canon</h3>
                <p>
                  Keep the changes you want, skip the rest. Everything you accept is saved as a new
                  version you can always return to.
                </p>
              </li>
            </ol>
          </div>
        </section>

        <section className="band" id="features">
          <div className="inner">
            <h2>Built for the long haul of a novel.</h2>
            <div className="features">
              {FEATURES.map((feature) => (
                <div className="feature" key={feature.title}>
                  <span className="glyph">
                    <Icon name={feature.icon} size={19} />
                  </span>
                  <h3>{feature.title}</h3>
                  <p>{feature.text}</p>
                </div>
              ))}
            </div>
          </div>
        </section>

        <section className="band closing">
          <div className="inner">
            <div className="panel">
              <h2>Stop rereading. Start writing.</h2>
              <p>Your characters, your world, your rules — kept straight for as long as the story runs.</p>
              <a className="btn primary lg" href="/auth/login">
                Open your library <Icon name="arrowRight" />
              </a>
            </div>
          </div>
        </section>
      </main>

      <footer>
        <div className="inner">
          <span className="brand">
            <span className="brand-mark">
              <Icon name="feather" size={17} />
            </span>
            Novel Engine
          </span>
          <span>Canon-first authoring for long fiction.</span>
        </div>
      </footer>
    </div>
  )
}
