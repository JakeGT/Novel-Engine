import type { CanonEntity } from '../api/client'
import { importanceLabel, importanceOf, needsDetail, tagsOf } from '../lib/marks'

/**
 * What tells one entry from another at a glance: how central it is, its tags, and
 * whether it is still waiting to be filled in.
 */
export function EntryMarks({ entity, limit = 3 }: { entity: CanonEntity; limit?: number }) {
  const importance = importanceOf(entity)
  const tags = tagsOf(entity)
  const waiting = needsDetail(entity)
  if (!importance && tags.length === 0 && !waiting) return null
  const hidden = tags.length - limit
  return (
    <span className="marks">
      {importance && <span className={`mark rank ${importance}`}>{importanceLabel(importance)}</span>}
      {tags.slice(0, limit).map((tag) => (
        <span className="mark" key={tag}>
          {tag}
        </span>
      ))}
      {hidden > 0 && <span className="mark quiet">and {hidden} more</span>}
      {waiting && <span className="mark todo">To fill in</span>}
    </span>
  )
}
