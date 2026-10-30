import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { Link } from 'react-router-dom'
import { storyViewsApi, type StoryView } from '../api/client'
import { Badge, Button, ErrorNotice, Loading } from '../components/ui'
import { timeAgo } from '../lib/time'
import { plural } from '../lib/words'
import { scopedKey, useProject } from '../project'

/** Each ready view in the author's words: what question it answers. */
const VIEW_WORDS: Record<string, { name: string; answers: string }> = {
  location: { name: 'Where everyone is', answers: 'Where a character or thing is at a moment.' },
  possession: { name: 'Who holds what', answers: 'Who owns or carries each thing, and when.' },
  relationship: { name: 'Who is connected to whom', answers: 'Family, friends, members and enemies.' },
  timeline: { name: 'The timeline', answers: 'Events in story order, as on the Timeline page.' },
  disclosure: { name: 'What the reader is shown', answers: 'Which scene first shows the reader each fact.' },
  dependency: { name: 'What leans on what', answers: 'Which records depend on an entry or a fact.' },
}

const STATE_TONE: Record<string, 'ok' | 'warn' | 'danger' | 'neutral'> = {
  Ready: 'ok',
  'Catching up': 'warn',
  'Not checked yet': 'neutral',
  'Not built yet': 'neutral',
  'Differed when checked': 'danger',
}

function seconds(value: number): string {
  if (value < 60) return plural(Math.max(1, Math.round(value)), 'second')
  if (value < 3600) return plural(Math.round(value / 60), 'minute')
  return plural(Math.round(value / 3600), 'hour')
}

function ViewRow({ view }: { view: StoryView }) {
  const words = VIEW_WORDS[view.view] ?? { name: view.view, answers: '' }
  const state = view.state || 'Not built yet'
  const facts: string[] = []
  if (view.version != null) {
    facts.push(
      view.behind
        ? `Built at version ${view.version}; the story is at version ${view.latest_version}, ${plural(view.behind, 'change')} behind`
        : `Up to date at version ${view.version}`,
    )
  }
  if (view.lag_seconds != null) facts.push(`waiting ${seconds(view.lag_seconds)} to catch up`)
  else if (view.built_at) facts.push(`built ${timeAgo(view.built_at)}`)
  if (view.version != null) facts.push(plural(view.rows, 'item'))
  const checked =
    view.verified_version == null
      ? 'Not yet checked against the story’s history, so reads work the answer out from the records for now.'
      : view.outcome === 'matched'
        ? `Checked against the story’s history at version ${view.verified_version}${view.verified_at ? `, ${timeAgo(view.verified_at)}` : ''}: it matched.`
        : `Checked at version ${view.verified_version}: ${plural(view.differences ?? 0, 'item')} differed, so it is not used until it is built again and matches.`
  const reads = view.answers_reads
    ? ' Reads use it now.'
    : view.outcome === 'matched' && view.behind
      ? ' Until it catches up, reads work the answer out from the records.'
      : ''
  return (
    <li className="story-view">
      <div className="story-view-head">
        <h2 className="story-view-name">{words.name}</h2>
        <Badge tone={STATE_TONE[state] ?? 'neutral'}>{state}</Badge>
      </div>
      <p className="small muted">{words.answers}</p>
      {facts.length > 0 && <p className="small">{facts.join(' · ')}.</p>}
      <p className="small">
        {checked}
        {reads}
      </p>
    </li>
  )
}

/**
 * The story's ready views (QRY-001): for each, whether it is built, checked against the
 * story's history, at which version, and how far behind. Reads only.
 */
export function StoryViewsPage() {
  const { scope, canAdmin: canCheck } = useProject()
  const client = useQueryClient()
  const check = useMutation({
    mutationFn: () => storyViewsApi.check(scope),
    onSuccess: () => void client.invalidateQueries({ queryKey: scopedKey(scope, 'story-views') }),
  })
  const views = useQuery({
    queryKey: scopedKey(scope, 'story-views'),
    queryFn: () => storyViewsApi.status(scope),
    // While one is catching up, look again shortly.
    refetchInterval: (query) => ((query.state.data?.views ?? []).some((view) => view.behind > 0) ? 5000 : false),
  })
  const data = views.data
  const list = data?.views ?? []
  const ready = list.filter((view) => view.answers_reads).length
  return (
    <>
      <div className="page-head">
        <div>
          <p className="eyebrow">
            <Link to={`/projects/${scope.projectId}/timeline`}>Timeline</Link> · Ready views
          </p>
          <h1>Ready views of the story</h1>
          <p className="lede">
            Some questions, such as where someone is or what happened when, are answered from views
            kept ready from your story’s records. A view is used only once it matches a check
            against the story’s history and is up to date; until then the answer is worked out
            from the records, which is slower but always right.
          </p>
        </div>
      </div>
      <div className="section-gap">
        <ErrorNotice error={views.error} prefix="Could not load the ready views" />
        {views.isPending && <Loading>Loading the ready views…</Loading>}
        {data && (
          <>
            <p>
              For {data.main_story ? 'the main story' : `the what-if “${data.line}”`}.{' '}
              {ready === list.length
                ? 'All of them are ready.'
                : `${ready} of ${list.length} ${ready === 1 ? 'is' : 'are'} ready.`}
            </p>
            <ul className="rows story-views">
              {list.map((view) => (
                <ViewRow key={view.view} view={view} />
              ))}
            </ul>
            {canCheck && (
              <div className="stack tight">
                <div className="actions">
                  <Button onClick={() => check.mutate()} disabled={check.isPending}>
                    {check.isPending ? 'Checking against the history…' : 'Check them against the history now'}
                  </Button>
                </div>
                <p className="hint">
                  Builds each view again from the story’s history and compares. Nothing in the story
                  changes; a view that differs is built again before it is used.
                </p>
                <ErrorNotice error={check.error} prefix="Could not check the views" />
                {check.data && (
                  <p className="small" role="status">
                    Checked {plural(check.data.checked, 'view')}: {check.data.matched} matched
                    {check.data.differed ? `, ${check.data.differed} differed` : ''}
                    {check.data.skipped ? `, ${check.data.skipped} could not be checked yet (try again shortly)` : ''}.
                  </p>
                )}
              </div>
            )}
          </>
        )}
      </div>
    </>
  )
}
