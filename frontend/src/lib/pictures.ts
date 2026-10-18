import { useQuery } from '@tanstack/react-query'
import { api, type AssetLink, type Scope } from '../api/client'
import { scopedKey } from '../project'

/** A link that shows its target: a current "depicts" link to one stored image. */
export function isPicture(link: AssetLink): boolean {
  return link.role === 'DEPICTS' && link.retired_revision == null && Boolean(link.asset_version_id)
}

/** A picture shown on a scene of the plan (AST-EXP-018). */
export function isScenePicture(link: AssetLink): boolean {
  return isPicture(link) && link.target_record_type === 'plan_item'
}

/** Every picture in the story, including those shown nowhere now; one request, shared. */
export function usePictureLibrary(scope: Scope, enabled = true) {
  return useQuery({
    queryKey: scopedKey(scope, 'pictures'),
    queryFn: () => api.pictureLibrary(scope),
    enabled,
  })
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
    if (
      isPicture(link) &&
      link.target_record_type === 'entity' &&
      (!current || link.created_revision > current.created_revision)
    ) {
      portraits.set(link.target_record_id, link)
    }
  }
  return portraits
}

/** A stored picture as a local address the page can show; a stored version never changes. */
export function usePictureUrl(scope: Scope, picture: { assetId: string; versionId: string | null } | undefined) {
  return useQuery({
    queryKey: scopedKey(scope, 'picture', picture?.assetId, picture?.versionId),
    queryFn: () => api.pictureUrl(scope, picture!.assetId, picture!.versionId as string),
    enabled: Boolean(picture?.versionId),
    staleTime: Infinity,
    gcTime: Infinity,
  })
}

/** The pictures shown on a scene, newest first, with the names the library gives them. */
export function useScenePictures(scope: Scope, sceneId: string) {
  const links = useQuery({
    queryKey: scopedKey(scope, 'links', sceneId),
    queryFn: () => api.listLinks(scope, { target_id: sceneId, include_retired: false }),
  })
  const library = usePictureLibrary(scope)
  const names = new Map((library.data?.items ?? []).map((picture) => [picture.asset_id, picture.name]))
  const pictures = (links.data?.items ?? [])
    .filter(isScenePicture)
    .sort((a, b) => b.created_revision - a.created_revision)
  return { pictures, names, library, error: links.error }
}
