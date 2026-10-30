import { useQuery } from '@tanstack/react-query'
import { useState } from 'react'
import { Link } from 'react-router-dom'
import { api } from '../api/client'
import { plural } from '../lib/words'
import { scopedKey, useProject } from '../project'
import { Button } from './ui'

/** A project has enough in it to plan from once it holds this many entries. */
const ENOUGH_TO_PLAN = 5
const declinedKey = (projectId: string) => `novel-engine.plan.declined.${projectId}`

function declined(projectId: string): boolean {
  try {
    return window.localStorage.getItem(declinedKey(projectId)) === 'yes'
  } catch {
    return false
  }
}

/**
 * The plan on the project's home page: what it still needs, once there is one;
 * or, once the story bible has enough in it, a single offer to start one, which
 * can be turned down for good. An author who does not outline is never nagged.
 */
export function PlanNudge({ entryCount }: { entryCount: number }) {
  const { scope, canWrite } = useProject()
  const outline = useQuery({
    queryKey: scopedKey(scope, 'plan'),
    queryFn: () => api.outline(scope),
  })
  const [hidden, setHidden] = useState(() => declined(scope.projectId))
  if (!outline.data) return null
  const to = `/projects/${scope.projectId}/plan`
  const { items, open } = outline.data

  if (items.length > 0) {
    if (open.length === 0) return null
    return (
      <section className="card" aria-labelledby="plan-todo-heading">
        <div className="card-head">
          <div>
            <h2 id="plan-todo-heading">Your plan has {plural(open.length, 'gap')}</h2>
            <p className="muted">
              Things you have not decided yet, such as a scene with no place or a question the
              story never answers. They are there for when you are ready.
            </p>
          </div>
          <span className="badge accent">{open.length}</span>
        </div>
        <ul className="rows">
          {open.slice(0, 4).map((entry, index) => (
            <li className="row" key={index}>
              <span className="grow">{items.find((item) => item.id === entry.plan_id)?.title}</span>
              <span className="muted small">{entry.message}</span>
            </li>
          ))}
        </ul>
        <Link className="btn" to={to}>
          Open the story plan
        </Link>
      </section>
    )
  }

  if (hidden || !canWrite || entryCount < ENOUGH_TO_PLAN) return null
  return (
    <section className="card quiet" aria-labelledby="plan-offer-heading">
      <div className="card-head">
        <div>
          <h2 id="plan-offer-heading">Ready to plan the story?</h2>
          <p className="muted">
            Your story bible has enough in it to plan from. You can talk the story through with
            the assistant, or outline it by hand. Planning is optional: plenty of writers never do.
          </p>
        </div>
      </div>
      <div className="actions">
        <Link className="btn" to={to}>
          Open the story plan
        </Link>
        <Button
          variant="ghost"
          onClick={() => {
            try {
              window.localStorage.setItem(declinedKey(scope.projectId), 'yes')
            } catch {
              // Without storage the offer simply shows again next time.
            }
            setHidden(true)
          }}
        >
          Not for this story
        </Button>
      </div>
    </section>
  )
}
