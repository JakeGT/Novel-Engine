import { useQuery } from '@tanstack/react-query'
import { api } from '../api/client'
import { ErrorNotice, Loading } from './ui'

/** Where the author's writing goes to be searchable by meaning. Set for the whole site by
 * whoever runs it, so it is said the same way to everyone. */
export function SearchWhereCard() {
  const where = useQuery({ queryKey: ['ai-search-where'], queryFn: () => api.searchWhere(), staleTime: 60_000 })
  const data = where.data
  return (
    <section className="card" aria-labelledby="search-where-heading">
      <div className="card-head">
        <div>
          <h2 id="search-where-heading">Search by meaning</h2>
          <p className="muted">
            Finds passages of your writing by what they are about, not only by the words in them. It is set
            up for the whole site by whoever runs it.
          </p>
        </div>
      </div>
      {where.isPending && <Loading>Checking…</Loading>}
      <ErrorNotice error={where.error} prefix="Could not check search by meaning" />
      {data?.kind === 'service' && (
        <p className="notice plain" role="note">
          This site sends your writing to {data.name || data.host}
          {data.name && data.host && data.name !== data.host ? ` (${data.host})` : ''} to make it searchable.
          It uses the site’s own account there, not your key.
          {!data.in_use && ' It is not switched on yet, so for now passages are found by the words they use.'}
        </p>
      )}
      {data?.kind === 'server' && (
        <p className="notice plain" role="note">
          This is done on the site’s own server. Your writing is not sent anywhere for it.
          {!data.in_use && ' It is not switched on yet, so for now passages are found by the words they use.'}
        </p>
      )}
      {data?.kind === 'off' && (
        <p className="notice plain" role="note">
          Search by meaning is off on this site, so passages are found by the names and words they use. Nothing is
          sent anywhere for search.
        </p>
      )}
    </section>
  )
}
