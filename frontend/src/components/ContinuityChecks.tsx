import { Link } from 'react-router-dom'
import type { Continuity, ContinuityFinding, FindingRecord, Scope } from '../api/client'
import { entryPath, useProject } from '../project'
import { Icon } from './icons'

/**
 * Where a record a finding names can be opened: an entry's page (a fact shows on the
 * page of the entry it is about), the timeline for an event, the plan for a scene.
 */
function recordHref(scope: Scope, record: FindingRecord): string {
  const projectId = scope.projectId
  if (record.kind === 'entry') return entryPath(scope, record.id)
  if (record.kind === 'fact') return record.entry_id ? entryPath(scope, record.entry_id) : `/projects/${projectId}/bible`
  if (record.kind === 'event') return `/projects/${projectId}/timeline#event-${record.id}`
  return `/projects/${projectId}/plan#plan-${record.id}`
}

const KIND_WORDS: Record<string, string> = {
  entry: 'In the story bible',
  fact: 'A fact',
  event: 'An event',
  scene: 'A scene',
}

/** The records a finding comes from, each a link to where it can be changed. */
export function RecordLinks({ records, skip }: { records: FindingRecord[]; skip?: string }) {
  const { scope } = useProject()
  const shown = records.filter((record) => record.id !== skip)
  if (shown.length === 0) return null
  return (
    <span className="record-links small">
      <span className="muted">Open: </span>
      {shown.map((record, index) => (
        <span key={`${record.kind}-${record.id}`}>
          {index > 0 && ', '}
          <Link to={recordHref(scope, record)} title={KIND_WORDS[record.kind]}>
            {record.label}
          </Link>
        </span>
      ))}
    </span>
  )
}

function FindingRow({ finding }: { finding: ContinuityFinding }) {
  const warning = finding.severity === 'warning'
  return (
    <li className={`row continuity-row ${warning ? 'is-warning' : 'is-note'}`}>
      <span className={`severity-tag ${warning ? 'warning' : 'note'}`}>
        {warning ? <Icon name="flag" size={14} aria-hidden="true" /> : null}
        {warning ? 'Cannot all be true' : 'Worth a look'}
      </span>
      <span className="grow continuity-message">
        {finding.message}
        <RecordLinks records={finding.records} />
      </span>
    </li>
  )
}

/** Continuity across story time and place: warnings first, then notes, then what each check covers. */
export function ContinuityChecks({ data }: { data: Continuity }) {
  const warnings = data.findings.filter((item) => item.severity === 'warning')
  const notes = data.findings.filter((item) => item.severity !== 'warning')
  const plural = (count: number, word: string) => `${count} ${word}${count === 1 ? '' : 's'}`
  // Warnings and notes are each capped; this many more were found than are listed.
  const notShown = data.not_shown ?? 0
  return (
    <section className="stack tight" aria-labelledby="continuity-heading">
      <h2 id="continuity-heading">Continuity</h2>
      <p className="muted small" role="status">
        {data.findings.length === 0
          ? 'Nothing out of place: no one is in two places at once, and nothing happens too soon after something else.'
          : [warnings.length ? plural(warnings.length, 'thing') + ' that cannot all be true' : '', notes.length ? plural(notes.length, 'note') : '']
              .filter(Boolean)
              .join(', ') + '. These never stop you saving anything.'}
        {notShown > 0 &&
          ` ${notShown} more ${notShown === 1 ? 'is' : 'are'} not shown; fixing the ones listed brings the rest into view.`}
      </p>
      {data.findings.length > 0 && (
        <ul className="rows" aria-label="Continuity findings">
          {[...warnings, ...notes].map((finding, index) => (
            <FindingRow key={`${finding.code}-${index}`} finding={finding} />
          ))}
        </ul>
      )}
      <details className="continuity-coverage">
        <summary>What these checks look at ({data.coverage.length})</summary>
        <dl className="coverage-list">
          {data.coverage.map((item) => (
            <div key={item.check}>
              <dt>{item.title}</dt>
              <dd>{item.covers}</dd>
            </div>
          ))}
        </dl>
        <p className="muted small">{data.always}</p>
      </details>
    </section>
  )
}
