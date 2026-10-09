import { useMutation, useQuery } from '@tanstack/react-query'
import { useState } from 'react'
import {
  api,
  errorMessage,
  isApiError,
  type SchemaValidationReport,
  type SchemaVersion,
} from '../api/client'
import { Badge, ErrorNotice, Field, Json, ReadOnlyNotice } from '../components/ui'
import { scopedKey, useInvalidateProject, useProject } from '../project'

const EMPTY = { label: 'Project schema', description: '', entity_extensions: [], predicate_extensions: [] }

const COMPATIBILITY: Record<string, { label: string; tone: 'ok' | 'warn' | 'danger' }> = {
  additive: { label: 'Additive — existing canon stays valid', tone: 'ok' },
  migration_required: { label: 'Migration required — existing canon needs a plan', tone: 'warn' },
  forbidden: { label: 'Forbidden — this change cannot be published', tone: 'danger' },
  invalid: { label: 'Invalid definition', tone: 'danger' },
}

function parseDefinition(text: string): { definition?: Record<string, unknown>; error?: string } {
  try {
    const value: unknown = JSON.parse(text)
    if (value === null || typeof value !== 'object' || Array.isArray(value)) {
      return { error: 'The definition must be a JSON object.' }
    }
    return { definition: value as Record<string, unknown> }
  } catch (failure) {
    return { error: `Not valid JSON: ${errorMessage(failure)}` }
  }
}

function PublishProblem({ error }: { error: unknown }) {
  if (!error) return null
  if (isApiError(error) && (error.status === 409 || error.status === 403)) {
    return (
      <p className="notice danger" role="alert">
        <strong>
          {error.status === 403 ? 'You are not allowed to publish.' : 'This version cannot be published.'}
        </strong>{' '}
        {error.message} Canon was not changed.
      </p>
    )
  }
  return <ErrorNotice error={error} prefix="Not published" />
}

export function SchemaPage() {
  const { scope, canWrite, canAdmin } = useProject()
  const invalidate = useInvalidateProject(scope)
  const versions = useQuery({
    queryKey: scopedKey(scope, 'schemas'),
    queryFn: () => api.listSchemaVersions(scope),
  })
  const items = [...(versions.data ?? [])].sort((a, b) => b.version - a.version)
  const published = items.find((version) => version.status === 'published')

  const [text, setText] = useState<string | null>(null)
  const [problem, setProblem] = useState<string | null>(null)
  const [report, setReport] = useState<SchemaValidationReport | null>(null)
  const draftText = text ?? JSON.stringify(published?.definition ?? EMPTY, null, 2)

  const validate = useMutation({
    mutationFn: (definition: Record<string, unknown>) => api.validateSchema(scope, definition),
    onSuccess: (result) => setReport(result),
    onError: (failure) => {
      // Older servers reject an ill-formed definition outright instead of reporting it.
      setReport({
        valid: false,
        content_hash: '',
        compatibility: 'invalid',
        errors: [errorMessage(failure)],
        migration_plan: [],
        entity_types: [],
        predicates: [],
      })
    },
  })
  const createDraft = useMutation({
    mutationFn: (definition: Record<string, unknown>) => api.createSchemaDraft(scope, definition),
    onSuccess: () => invalidate(),
  })
  const publish = useMutation({
    mutationFn: (version: SchemaVersion) => api.publishSchema(scope, version.id),
    onSuccess: () => invalidate(),
  })

  const withDefinition = (action: (definition: Record<string, unknown>) => void) => {
    const parsed = parseDefinition(draftText)
    setProblem(parsed.error ?? null)
    if (parsed.definition) action(parsed.definition)
  }
  const compatibility = report ? (COMPATIBILITY[report.compatibility] ?? null) : null

  return (
    <>
      <section className="panel" aria-labelledby="schema-heading">
        <h2 id="schema-heading">Schema versions</h2>
        <p className="muted">
          These versions apply only to project <strong>{scope.projectId}</strong>. A published
          version is immutable, and canon keeps the version it was accepted under.
        </p>
        <ErrorNotice error={versions.error} prefix="Could not load schema versions" />
        {items.length === 0 && !versions.isLoading && (
          <p className="notice" role="note">
            No schema version is published yet. Entities cannot be created until one is.
          </p>
        )}
        <ul className="list">
          {items.map((version) => (
            <li key={version.id}>
              <strong>v{version.version}</strong>{' '}
              <Badge tone={version.status === 'published' ? 'ok' : 'neutral'}>
                {version.status === 'published' ? 'Published' : 'Draft'}
              </Badge>{' '}
              <span className="muted">
                {version.compatibility} · {String(version.definition?.label ?? '')}
              </span>{' '}
              {version.status !== 'published' && canAdmin && (
                <button
                  type="button"
                  className="button secondary"
                  disabled={publish.isPending}
                  onClick={() => publish.mutate(version)}
                >
                  Publish v{version.version}
                </button>
              )}
              {version.status !== 'published' && (
                <button
                  type="button"
                  className="button secondary"
                  onClick={() => setText(JSON.stringify(version.definition ?? EMPTY, null, 2))}
                >
                  Load into editor
                </button>
              )}
            </li>
          ))}
        </ul>
        {!canAdmin && items.some((version) => version.status !== 'published') && (
          <p className="muted">Only a project administrator can publish a draft.</p>
        )}
        <PublishProblem error={publish.error} />
      </section>

      <section className="panel" aria-labelledby="schema-draft-heading">
        <h2 id="schema-draft-heading">Draft a new version</h2>
        <ReadOnlyNotice canWrite={canWrite} />
        <Field
          label="Definition (JSON)"
          hint="Declarative only: entity types with typed fields, and predicates. No code or patterns."
          error={problem}
        >
          {(props) => (
            <textarea
              {...props}
              className="code"
              rows={14}
              spellCheck={false}
              value={draftText}
              readOnly={!canWrite}
              onChange={(event) => {
                setText(event.target.value)
                setReport(null)
              }}
            />
          )}
        </Field>
        {canWrite && (
          <div className="actions">
            <button
              type="button"
              className="button"
              disabled={validate.isPending}
              onClick={() => withDefinition((definition) => validate.mutate(definition))}
            >
              Validate
            </button>
            <button
              type="button"
              className="button secondary"
              disabled={createDraft.isPending || !report?.valid}
              title={report?.valid ? undefined : 'Validate the definition first'}
              onClick={() => withDefinition((definition) => createDraft.mutate(definition))}
            >
              Save as draft
            </button>
          </div>
        )}
        {report && (
          <div className={`notice ${report.valid ? '' : 'danger'}`} role="status">
            <p>
              <strong>{report.valid ? 'The definition is valid.' : 'The definition is not valid.'}</strong>{' '}
              {compatibility && <Badge tone={compatibility.tone}>{compatibility.label}</Badge>}
            </p>
            {report.errors.length > 0 && (
              <ul>
                {report.errors.map((item, index) => (
                  <li key={index} className="error">
                    {item}
                  </li>
                ))}
              </ul>
            )}
            {report.migration_plan.length > 0 && (
              <>
                <p>Migration plan:</p>
                <ol>
                  {report.migration_plan.map((step, index) => (
                    <li key={index}>{step}</li>
                  ))}
                </ol>
              </>
            )}
            {report.valid && (
              <p className="muted">
                Types: {report.entity_types.join(', ') || 'core only'} · Predicates:{' '}
                {report.predicates.join(', ') || 'core only'}
              </p>
            )}
          </div>
        )}
        {createDraft.isSuccess && (
          <p className="notice" role="status">
            Saved as draft v{createDraft.data.version.version} ({createDraft.data.compatibility}). It
            changes nothing until it is published.
          </p>
        )}
        <ErrorNotice error={createDraft.error} prefix="Draft not saved" />
      </section>

      {published && (
        <section className="panel" aria-labelledby="schema-current-heading">
          <h2 id="schema-current-heading">Published definition (v{published.version})</h2>
          <Json value={published.definition} />
        </section>
      )}
    </>
  )
}
