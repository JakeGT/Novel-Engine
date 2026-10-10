import { useQuery } from '@tanstack/react-query'
import { api, type AssetLink, type Scope } from '../api/client'
import { scopedKey } from '../project'

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

/**
 * Every entry's portrait, from one request: the newest picture linked to each.
 * Shared by every list that shows entries, so an uploaded picture appears everywhere.
 */
export function usePortraits(scope: Scope): Map<string, AssetLink> {
  const links = useQuery({
    queryKey: scopedKey(scope, 'links', 'all'),
    queryFn: () => api.listLinks(scope, { include_retired: false }),
  })
  const portraits = new Map<string, AssetLink>()
  for (const link of links.data?.items ?? []) {
    const current = portraits.get(link.target_record_id)
    if (isPicture(link) && (!current || link.created_revision > current.created_revision)) {
      portraits.set(link.target_record_id, link)
    }
  }
  return portraits
}
