import type { AssetLink } from '../api/client'

/** A link that shows its target: a current "depicts" link to one stored image. */
export function isPicture(link: AssetLink): boolean {
  return link.role === 'DEPICTS' && link.retired_revision == null && Boolean(link.asset_version_id)
}

/** The picture that represents an entry: the most recently added one. */
export function mainPicture(links: AssetLink[]): AssetLink | undefined {
  return links
    .filter(isPicture)
    .sort((a, b) => b.created_revision - a.created_revision || b.id.localeCompare(a.id))[0]
}
