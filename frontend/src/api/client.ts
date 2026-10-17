import { noteSignedIn, shouldHoldFor401, waitForSignIn } from '../lib/reauth'
import type { components } from './schema'

type Schemas = components['schemas']

// Contract types come straight from the generated OpenAPI schema. Only endpoints
// the backend declares as an untyped object are typed locally, next to the call.
export type CanonEntity = Schemas['CanonEntityModel']
export type CreateEntityRequest = Schemas['CreateEntityRequest']
export type UpdateEntityRequest = Schemas['UpdateEntityRequest']
export type Workspace = Schemas['WorkspaceModel']
export type Project = Schemas['ProjectModel']
export type Fact = Schemas['FactModel']
export type Belief = Schemas['BeliefModel']
export type CanonEvent = Schemas['EventModel']
export type AssertFactRequest = Schemas['AssertFactRequest']
export type Vocabulary = Schemas['VocabularyModel']
export type VocabularyEntityType = Schemas['EntityTypeModel']
export type VocabularyPredicate = Schemas['PredicateModel']
export type SchemaVersion = Schemas['SchemaVersionModel']
export type SchemaValidationReport = Schemas['ValidationReportModel']
export type SchemaDraftResponse = Schemas['SchemaDraftResponse']
export type MutationSummary = Schemas['MutationSummaryModel']
export type MutationDetail = Schemas['MutationDetailModel']
export type MutationOperation = Schemas['MutationOperationModel']
export type MutationValidation = Schemas['MutationValidationModel']
export type MutationCommit = Schemas['MutationCommitModel']
export type MutationList = Schemas['MutationListResponse']
export type ProposeMutationRequest = Schemas['ProposeMutationRequest']
export type CharacterQuestionSheet = Schemas['CharacterQuestionSheetModel']
export type CharacterProposal = Schemas['CharacterProposalModel']
export type CharacterProposalRequest = Schemas['CharacterProposalRequest']
export type EntityListResponse = Schemas['CanonEntityListResponse']
export type AssistBrief = Schemas['AssistBriefRequest']
export type AssistQuestionSheet = Schemas['AssistQuestionSheetModel']
export type AssistProposalRequest = Schemas['AssistProposalRequest']
export type ChangeProposal = Schemas['ChangeProposalModel']
export type PlanItem = Schemas['PlanItemModel']
export type PlanRef = Schemas['PlanRefModel']
export type SceneCondition = Schemas['SceneCondition']
export type Outline = Schemas['OutlineModel']
export type PlanKind = Schemas['AddPlanItemRequest']['kind']
export type AssistSession = Schemas['AssistSessionModel']
export type SourceText = Schemas['TextModel']
export type StoryRule = Schemas['RuleModel']
export type RuleBody = Schemas['RuleBody']
export type RulePattern = Schemas['Pattern']
export type RuleCheck = Schemas['RuleCheckModel']
export type RuleDraft = Schemas['RuleDraftModel']
export type RuleExample = Schemas['RuleExample']
export type SourceTextDetail = Schemas['TextDetailModel']
export type TextMention = Schemas['MentionModel']
export type NameQuestion = Schemas['NameQuestionModel']
export type Passage = Schemas['PassageModel']
export type PassageRead = Schemas['ReadPassageModel']
export type SchemaSuggestion = Schemas['SchemaSuggestionModel']
export type AiProvider = Schemas['ProviderModel']
export type AiChoice = Schemas['AiChoiceModel']
export type Member = Schemas['MembershipModel']
export type Invitation = Schemas['InvitationModel']
export type MemberRole = Member['role']
export type WorldTime = NonNullable<Schemas['EventModel']['when']>
export type FactVisibility = Schemas['FactModel']['visibility']
export type WhatIf = Schemas['WhatIfModel']
export type WhatIfComparison = Schemas['ComparisonModel']
export type WhatIfChange = Schemas['ChangeModel']
export type Timeline = Schemas['TimelineModel']
export type PlacedEvent = Schemas['PlacedEventModel']
export type Moment = Schemas['MomentModel']
export type CharacterKnowledge = Schemas['CharacterKnowledgeModel']
export type FactKnowledge = Schemas['FactKnowledgeModel']
export type ReaderJourney = Schemas['JourneyModel']
export type ReaderStep = Schemas['ReaderStepModel']
export type Reveal = Schemas['Reveal']
export type SceneContext = Schemas['SceneContextModel']
export type ContextItem = Schemas['ContextItemModel']
export type ContextSettings = Schemas['ContextSettings']
export type WritingIndex = Schemas['IndexStatusModel']

export interface Scope {
  workspaceId: string | null
  projectId: string
  /** The what-if being worked in; absent or null for the story itself (ADR-020). */
  branchId?: string | null
}

/** A list endpoint the backend returns as an untyped `{items}` object. */
export interface Items<T> {
  items: T[]
}

export interface VersionName {
  name: string
  revision: number
}

export interface HistoryEntry {
  record_type: string
  record_id: string
  revision: number
  operation_type: string
  commit_id: string
  change: Record<string, unknown>
  created_at: string
}

export interface ImportSummary {
  id: string
  status: 'importing' | 'completed' | 'failed' | 'removed'
  stage: string
  commits_total: number
  commits_applied: number
  objects_imported: number
  error: string | null
}

export interface ProjectionStatus {
  import: ImportSummary | null
  projection: string
  branch_id: string
  head_revision: number
  applied_revision: number
  lag: number
  status: string
  current: boolean
  last_error: string | null
  failed_deliveries: number
}

export interface ProjectAccess {
  role: string
  permissions: string[]
  can_write: boolean
  can_admin: boolean
  auth_mode: string
  hosted: boolean
}

export interface RevisionSummary {
  revision: number
  commit_id: string
  expected_head: number
  result_head: number
  schema_version_id: string | null
  operation_count: number
  created_at: string
  origin: string | null
  intent: string | null
  mutation_id: string | null
  actor_user_id: string | null
  decision_count: number
  evidence_count: number
  status: string
}

export interface ProvenanceEntry {
  record_type: string
  record_id: string
  revision: number
  commit_id: string
  operation_type: string
  origin: string | null
  intent: string | null
  schema_version_id: string | null
  actor_user_id: string | null
  decisions: { decision: string; actor_user_id: string | null; note: string | null; created_at: string }[]
  evidence: { source_artefact_id: string; location: string | null }[]
  redacted: boolean
  created_at: string
}

export interface RevertProposal {
  from_revision: number
  to_revision: number
  head_revision: number
  operations: Record<string, unknown>[]
  changes: {
    revision: number
    record_type: string
    record_id: string
    operation_type: string
    inverse_operation: string | null
    reason: string
  }[]
  conflicts: string[]
  summary: string
}

export interface Asset {
  id: string
  name: string
  lifecycle: string
  created_revision: number
  retired_revision: number | null
  created_at: string
}

export interface AssetVersion {
  id: string
  asset_id: string
  version: number
  content_type: string
  byte_length: number
  checksum: string
  source: string | null
  licence: string | null
  attribution: string | null
  authority: string | null
  visibility: string | null
  created_revision: number
  retired_revision: number | null
}

export interface AssetLink {
  id: string
  asset_id: string
  asset_version_id: string | null
  role: string
  target_record_type: string
  target_record_id: string
  created_revision: number
  retired_revision: number | null
}

export interface AssetLifecycleEvent {
  action: string
  asset_id: string
  asset_version_id: string | null
  actor_user_id: string | null
  canon_revision: number | null
  created_at: string
}

export interface Credential {
  id: string
  provider: string
  label: string
  key_last4: string
  status: string
  created_at: string
  last_used_at: string | null
}

export interface SessionInfo {
  user: { id: string; email: string | null; display_name: string | null }
  auth_mode: string
  /** Present only in local (single-user) mode, which has exactly one project. */
  local?: { workspace_id: string; project_id: string }
}

/** A failed request, carrying the stable problem `code` so callers can branch on it. */
export class ApiError extends Error {
  status: number
  code: string
  fieldErrors: Record<string, string>

  constructor(status: number, message: string, code = '', fieldErrors: Record<string, string> = {}) {
    super(message)
    this.status = status
    this.code = code
    this.fieldErrors = fieldErrors
  }
}

export function isApiError(error: unknown, code?: string): error is ApiError {
  return error instanceof ApiError && (code === undefined || error.code === code)
}

/** The branch head moved (or the commit raced); entered data is still valid to resubmit. */
export function isStaleHead(error: unknown): boolean {
  return isApiError(error, 'revision_conflict')
}

export function errorMessage(error: unknown, fallback = 'Request failed'): string {
  if (!(error instanceof Error) || !error.message) return fallback
  // Server validation text can carry bracketed finding codes; an author gets the sentence.
  const text = error.message.replace(/\[[a-z0-9_.:-]+\]\s*/gi, '').trim()
  return text ? text.charAt(0).toUpperCase() + text.slice(1) : fallback
}

const CSRF_COOKIE = 'novel_csrf'
// Requests whose "not signed in" answer is the answer, not an interruption.
const UNHELD = new Set(['/auth/me', '/auth/logout'])

function readCookie(name: string): string {
  const match = document.cookie.match(new RegExp('(?:^|; )' + name + '=([^;]*)'))
  return match ? decodeURIComponent(match[1]) : ''
}

interface RequestOptions extends RequestInit {
  workspaceId?: string | null
  json?: unknown
}

async function request<T>(path: string, options: RequestOptions = {}): Promise<T> {
  const { workspaceId, json, ...rest } = options
  const method = (rest.method ?? 'GET').toUpperCase()

  const send = () => {
    const headers = new Headers(rest.headers)
    headers.set('Accept', 'application/json')
    if (workspaceId) headers.set('X-Workspace-Id', workspaceId)
    if (json !== undefined) headers.set('Content-Type', 'application/json')
    // Read at the moment of sending: after signing in again the token is a new one.
    if (method !== 'GET' && method !== 'HEAD') headers.set('X-CSRF-Token', readCookie(CSRF_COOKIE))
    return fetch(path, {
      credentials: 'include',
      ...rest,
      method,
      headers,
      body: json !== undefined ? JSON.stringify(json) : rest.body,
    })
  }

  let response = await send()
  // Signed out in the middle of work: hold the request, keep the page as it is,
  // and send it again once the author has signed back in. Asking "am I signed in?"
  // and signing out are answered as they are.
  while (response.status === 401 && shouldHoldFor401() && !UNHELD.has(path)) {
    await waitForSignIn()
    response = await send()
  }
  if (response.ok) noteSignedIn()

  if (!response.ok) {
    let detail = response.statusText
    let code = ''
    let fieldErrors: Record<string, string> = {}
    try {
      const body = (await response.json()) as {
        detail?: string
        code?: string
        field_errors?: Record<string, string>
      }
      detail = body.detail ?? detail
      code = body.code ?? ''
      fieldErrors = body.field_errors ?? {}
    } catch {
      // Non-JSON error body; keep the status text.
    }
    throw new ApiError(response.status, detail, code, fieldErrors)
  }
  if (response.status === 204) return undefined as T
  return (await response.json()) as T
}

type QueryParams = Record<string, string | number | boolean | undefined | null>

function query(params: QueryParams): string {
  const search = new URLSearchParams()
  for (const [key, value] of Object.entries(params)) {
    if (value !== undefined && value !== null && value !== '') search.set(key, String(value))
  }
  const encoded = search.toString()
  return encoded ? `?${encoded}` : ''
}

function project(scope: Scope): string {
  return `/api/v1/projects/${encodeURIComponent(scope.projectId)}`
}

/** Every project request says which line of the story it works in. */
function branchHeaders(scope: Scope): Record<string, string> {
  return scope.branchId ? { 'X-Story-Branch': scope.branchId } : {}
}

function get<T>(scope: Scope, path: string, params: QueryParams = {}): Promise<T> {
  return request<T>(`${project(scope)}${path}${query(params)}`, {
    workspaceId: scope.workspaceId,
    headers: branchHeaders(scope),
  })
}

function send<T>(scope: Scope, method: string, path: string, json?: unknown): Promise<T> {
  return request<T>(`${project(scope)}${path}`, {
    method,
    workspaceId: scope.workspaceId,
    headers: branchHeaders(scope),
    json,
  })
}

const id = encodeURIComponent

export const api = {
  session: () => request<SessionInfo>('/auth/me'),

  listWorkspaces: () => request<Workspace[]>('/api/v1/workspaces'),

  /** A person who owns no workspace may create one of their own. */
  createWorkspace: (body: Schemas['CreateWorkspaceRequest']) =>
    request<Workspace>('/api/v1/workspaces', { method: 'POST', json: body }),

  /** Ends the session. Hosted mode answers with the identity provider's sign-out address. */
  logout: () => request<{ status: string; logout_url?: string }>('/auth/logout', { method: 'POST' }),

  listProjects: (workspaceId: string) =>
    request<Project[]>(`/api/v1/workspaces/${id(workspaceId)}/projects`, { workspaceId }),

  createProject: (workspaceId: string, body: Schemas['CreateProjectRequest']) =>
    request<Project>(`/api/v1/workspaces/${id(workspaceId)}/projects`, {
      method: 'POST',
      workspaceId,
      json: body,
    }),

  // --- people: members and invitations (hosted mode) ---------------------------
  listMembers: (workspaceId: string) =>
    request<Member[]>(`/api/v1/workspaces/${id(workspaceId)}/members`, { workspaceId }),
  setMemberRole: (workspaceId: string, userId: string, role: MemberRole) =>
    request<Member>(`/api/v1/workspaces/${id(workspaceId)}/members/${id(userId)}`, {
      method: 'PATCH',
      workspaceId,
      json: { role },
    }),
  removeMember: (workspaceId: string, userId: string) =>
    request<void>(`/api/v1/workspaces/${id(workspaceId)}/members/${id(userId)}`, {
      method: 'DELETE',
      workspaceId,
    }),
  listInvitations: (workspaceId: string) =>
    request<Invitation[]>(`/api/v1/workspaces/${id(workspaceId)}/invitations`, { workspaceId }),
  /** The token in the answer is shown once and cannot be fetched again. */
  createInvitation: (workspaceId: string, body: { role: MemberRole; email: string | null }) =>
    request<{ invitation: Invitation; token: string }>(
      `/api/v1/workspaces/${id(workspaceId)}/invitations`,
      { method: 'POST', workspaceId, json: body },
    ),
  revokeInvitation: (workspaceId: string, invitationId: string) =>
    request<{ revoked: boolean }>(
      `/api/v1/workspaces/${id(workspaceId)}/invitations/${id(invitationId)}/revoke`,
      { method: 'POST', workspaceId },
    ),
  acceptInvitation: (token: string) =>
    request<Member>('/api/v1/invitations/accept', { method: 'POST', json: { token } }),

  // --- AI provider keys (workspace owners; hosted mode) -------------------------
  listCredentials: (workspaceId: string) =>
    request<Credential[]>('/api/v1/credentials', { workspaceId }),
  createCredential: (workspaceId: string, body: { provider: string; label: string; key: string }) =>
    request<Credential>('/api/v1/credentials', { method: 'POST', workspaceId, json: body }),
  // --- which AI provider and model the assistant uses ---------------------------
  aiProviders: () => request<AiProvider[]>('/api/v1/ai/providers'),
  aiSettings: (workspaceId: string) => request<AiChoice>('/api/v1/ai/settings', { workspaceId }),
  chooseAi: (workspaceId: string, body: { provider: string; model: string }) =>
    request<AiChoice>('/api/v1/ai/settings', { method: 'PUT', workspaceId, json: body }),
  /** What the signed-in person has spent of their assistant allowance. */
  aiUsage: (workspaceId: string) =>
    request<Schemas['AllowanceModel']>('/api/v1/ai/usage', { workspaceId }),
  setAiUsage: (workspaceId: string, dailyTokens: number | null) =>
    request<Schemas['AllowanceModel']>('/api/v1/ai/usage', {
      method: 'PUT',
      workspaceId,
      json: { daily_tokens: dailyTokens },
    }),
  /** The models the workspace's key can use, read live from the provider. */
  aiModels: (workspaceId: string, provider: string) =>
    request<Schemas['ModelListModel']>(
      `/api/v1/ai/models?provider=${encodeURIComponent(provider)}`,
      { workspaceId },
    ),
  revokeCredential: (workspaceId: string, credentialId: string) =>
    request<Credential>(`/api/v1/credentials/${id(credentialId)}/revoke`, {
      method: 'POST',
      workspaceId,
    }),

  access: (scope: Scope) => get<ProjectAccess>(scope, '/access'),

  projection: (scope: Scope) => get<ProjectionStatus>(scope, '/projection'),

  // --- schema ---------------------------------------------------------------
  vocabulary: (scope: Scope) => get<Vocabulary>(scope, '/schemas/vocabulary'),
  listSchemaVersions: (scope: Scope) => get<SchemaVersion[]>(scope, '/schemas'),
  validateSchema: (scope: Scope, definition: Record<string, unknown>) =>
    send<SchemaValidationReport>(scope, 'POST', '/schemas/validate', { definition }),
  createSchemaDraft: (scope: Scope, definition: Record<string, unknown>) =>
    send<SchemaDraftResponse>(scope, 'POST', '/schemas/drafts', { definition }),
  publishSchema: (scope: Scope, versionId: string) =>
    send<SchemaVersion>(scope, 'POST', `/schemas/${id(versionId)}/publish`),

  // --- entities ---------------------------------------------------------------
  listEntities: (
    scope: Scope,
    params: { limit?: number; offset?: number; include_retired?: boolean; entity_type?: string } = {},
  ) => get<EntityListResponse>(scope, '/entities', params),

  searchEntities: (
    scope: Scope,
    params: { q: string; entity_type?: string; include_retired?: boolean; limit?: number },
  ) => get<EntityListResponse>(scope, '/entities/search', params),

  nameCollisions: (scope: Scope, entityType: string, name: string) =>
    get<EntityListResponse>(scope, '/entities/name-collisions', { entity_type: entityType, name }),

  getEntity: (scope: Scope, entityId: string) =>
    get<CanonEntity>(scope, `/entities/${id(entityId)}`),

  createEntity: (scope: Scope, body: CreateEntityRequest) =>
    send<CanonEntity>(scope, 'POST', '/entities', body),

  // --- the story plan: what the author means to happen; never read as fact -------
  outline: (scope: Scope) => get<Outline>(scope, '/plan'),
  addPlanItem: (scope: Scope, body: Schemas['AddPlanItemRequest']) =>
    send<PlanItem>(scope, 'POST', '/plan', body),
  changePlanItem: (scope: Scope, planId: string, body: Schemas['ChangePlanItemRequest']) =>
    send<PlanItem>(scope, 'PATCH', `/plan/${id(planId)}`, body),
  movePlanItem: (scope: Scope, planId: string, body: Schemas['MovePlanItemRequest']) =>
    send<PlanItem>(scope, 'POST', `/plan/${id(planId)}/move`, body),
  setSceneConditions: (scope: Scope, planId: string, body: Schemas['SetConditionsRequest']) =>
    send<PlanItem>(scope, 'PUT', `/plan/${id(planId)}/conditions`, body),
  removePlanItem: (scope: Scope, planId: string, expectedRevision: number) =>
    send<Schemas['RemovedModel']>(scope, 'POST', `/plan/${id(planId)}/remove`, {
      expected_revision: expectedRevision,
    } satisfies Schemas['RemovePlanItemRequest']),

  // --- planning with the assistant: always a proposal or options, never a change ---
  planQuestions: (scope: Scope, body: Schemas['PlanBriefRequest']) =>
    send<AssistQuestionSheet>(scope, 'POST', '/assist/plan/questions', body),
  planProposal: (scope: Scope, body: Schemas['PlanProposalRequest']) =>
    send<Schemas['PlanProposalModel']>(scope, 'POST', '/assist/plan/proposals', body),
  planDirections: (scope: Scope, aboutId: string | null) =>
    send<Schemas['DirectionsModel']>(scope, 'POST', '/assist/plan/directions', { about_id: aboutId }),
  chooseDirection: (scope: Scope, draft: Schemas['PlanDraft'], expectedRevision: number) =>
    send<Schemas['PlanProposalModel']>(scope, 'POST', '/assist/plan/directions/choose', {
      draft,
      expected_revision: expectedRevision,
    }),

  // --- the author's own writing: kept as given; evidence, never canon by itself -----
  texts: (scope: Scope) => get<Schemas['TextListModel']>(scope, '/texts'),
  text: (scope: Scope, sourceId: string, versionId?: string) =>
    get<SourceTextDetail>(scope, `/texts/${id(sourceId)}`, versionId ? { version_id: versionId } : {}),
  addText: (scope: Scope, body: Schemas['AddTextRequest']) =>
    send<SourceText>(scope, 'POST', '/texts', body),
  reviseText: (scope: Scope, sourceId: string, body: Schemas['ReviseTextRequest']) =>
    send<SourceText>(scope, 'PATCH', `/texts/${id(sourceId)}`, body),
  removeText: (scope: Scope, sourceId: string) =>
    send<void>(scope, 'DELETE', `/texts/${id(sourceId)}`),
  /** The words a change points back to. */
  passage: (scope: Scope, versionId: string, location?: string | null) =>
    get<Passage>(scope, '/texts/passage', { version_id: versionId, ...(location ? { location } : {}) }),
  addEntryFromText: (scope: Scope, sourceId: string, body: Schemas['AddEntryFromTextRequest']) =>
    send<Schemas['AddedEntryModel']>(scope, 'POST', `/texts/${id(sourceId)}/entries`, body),
  nameMemory: (scope: Scope) => get<Schemas['NameMemoryModel']>(scope, '/names'),
  decideName: (scope: Scope, body: Schemas['NameDecisionRequest']) =>
    send<Schemas['NameDecisionModel']>(scope, 'POST', '/names', body),
  forgetName: (scope: Scope, bindingId: string) =>
    send<void>(scope, 'DELETE', `/names/${id(bindingId)}`),
  /** The assistant reads a passage into suggested changes. Nothing is added by reading. */
  readPassage: (scope: Scope, sourceId: string, body: Schemas['ReadPassageRequest']) =>
    send<PassageRead>(scope, 'POST', `/assist/texts/${id(sourceId)}/read`, body),

  // --- story rules: the author's own continuity rules; they warn, never stop -------
  rules: (scope: Scope) => get<Schemas['RuleListModel']>(scope, '/rules'),
  checkRule: (scope: Scope, body: Schemas['RuleCheckRequest']) =>
    send<RuleCheck>(scope, 'POST', '/rules/check', body),
  addRule: (scope: Scope, body: Schemas['AddRuleRequest']) =>
    send<StoryRule>(scope, 'POST', '/rules', body),
  changeRule: (scope: Scope, ruleId: string, body: Schemas['ChangeRuleRequest']) =>
    send<StoryRule>(scope, 'PATCH', `/rules/${id(ruleId)}`, body),
  removeRule: (scope: Scope, ruleId: string) => send<void>(scope, 'DELETE', `/rules/${id(ruleId)}`),
  draftRule: (scope: Scope, sentence: string) =>
    send<RuleDraft>(scope, 'POST', '/assist/rules/draft', { sentence }),

  /** Fill an empty project with the short sample story, as one version. */
  loadSample: (scope: Scope, expectedRevision: number) =>
    send<Schemas['SampleLoadedModel']>(scope, 'POST', '/entities/sample', {
      expected_revision: expectedRevision,
    } satisfies Schemas['LoadSampleRequest']),

  updateEntity: (scope: Scope, entityId: string, body: UpdateEntityRequest) =>
    send<CanonEntity>(scope, 'PATCH', `/entities/${id(entityId)}`, body),

  retireEntity: (
    scope: Scope,
    entityId: string,
    expectedRevision: number,
    acknowledgeHighImpact: boolean,
  ) =>
    send<CanonEntity>(scope, 'POST', `/entities/${id(entityId)}/retire`, {
      expected_revision: expectedRevision,
      acknowledge_high_impact: acknowledgeHighImpact,
    } satisfies Schemas['RetireEntityRequest']),

  entityHistory: (scope: Scope, entityId: string) =>
    get<Items<HistoryEntry>>(scope, `/entities/${id(entityId)}/history`),

  /** Names an author has given to versions ("First draft done"). */
  versionNames: (scope: Scope) => get<Items<VersionName>>(scope, '/tags'),
  nameVersion: (scope: Scope, name: string, revision: number) =>
    send<VersionName>(scope, 'POST', '/tags', { name, revision }),
  removeVersionName: (scope: Scope, name: string) => send<void>(scope, 'DELETE', `/tags/${id(name)}`),
  /** Every change saved after one version, up to and including another. */
  changesBetween: (scope: Scope, after: number, upTo: number) =>
    get<Items<HistoryEntry>>(scope, `/revisions/${after + 1}/diff/${upTo}`),

  provenance: (scope: Scope, recordType: string, recordId: string) =>
    get<Items<ProvenanceEntry>>(scope, `/provenance/${id(recordType)}/${id(recordId)}`),

  // --- facts, beliefs, events -------------------------------------------------
  listFacts: (
    scope: Scope,
    params: {
      subject_id?: string
      object_entity_id?: string
      include_closed?: boolean
      limit?: number
    } = {},
  ) => get<Items<Fact>>(scope, '/facts', params),

  assertFact: (scope: Scope, body: AssertFactRequest) => send<Fact>(scope, 'POST', '/facts', body),

  closeFact: (scope: Scope, factId: string, expectedRevision: number) =>
    send<Fact>(scope, 'POST', `/facts/${id(factId)}/close`, {
      expected_revision: expectedRevision,
    } satisfies Schemas['CloseRequest']),

  assertBelief: (scope: Scope, body: Schemas['BeliefRequest']) =>
    send<Belief>(scope, 'POST', '/beliefs', body),

  closeBelief: (scope: Scope, beliefId: string, expectedRevision: number) =>
    send<Belief>(scope, 'POST', `/beliefs/${id(beliefId)}/close`, {
      expected_revision: expectedRevision,
    } satisfies Schemas['CloseRequest']),

  listBeliefs: (scope: Scope, params: { holder_entity_id?: string; limit?: number } = {}) =>
    get<Items<Belief>>(scope, '/beliefs', params),

  truthAgreement: (scope: Scope, beliefId: string) =>
    get<{ belief_id: string; truth_agreement: 'agrees' | 'disagrees' | 'unknown' }>(
      scope,
      `/beliefs/${id(beliefId)}/truth-agreement`,
    ),

  listEvents: (scope: Scope, params: { entity_id?: string; limit?: number } = {}) =>
    get<Items<CanonEvent>>(scope, '/events', params),
  createEvent: (scope: Scope, body: Schemas['EventRequest']) =>
    send<CanonEvent>(scope, 'POST', '/events', body),
  amendEvent: (scope: Scope, eventId: string, body: Schemas['AmendEventRequest']) =>
    send<CanonEvent>(scope, 'PATCH', `/events/${id(eventId)}`, body),
  /** When a fact is true, and who may know it. */
  amendFact: (scope: Scope, factId: string, body: Schemas['AmendFactRequest']) =>
    send<Fact>(scope, 'POST', `/facts/${id(factId)}/amend`, body),

  // --- story time (ADR-020) ----------------------------------------------------
  timeline: (scope: Scope) => get<Timeline>(scope, '/timeline'),
  moment: (scope: Scope, at: number) => get<Moment>(scope, '/timeline/moment', { at }),
  setTimeUnit: (scope: Scope, unit: string) =>
    send<{ unit: string }>(scope, 'PUT', '/timeline/unit', { unit }),

  // --- who knows what, and what the reader is shown ----------------------------
  characterKnowledge: (scope: Scope, entityId: string) =>
    get<CharacterKnowledge>(scope, `/knowledge/characters/${id(entityId)}`),
  factKnowledge: (scope: Scope, factId: string) =>
    get<FactKnowledge>(scope, `/knowledge/facts/${id(factId)}`),
  tell: (scope: Scope, body: Schemas['TellRequest']) =>
    send<Schemas['TellResult']>(scope, 'POST', '/knowledge/tell', body),
  readerJourney: (scope: Scope) => get<ReaderJourney>(scope, '/reader'),
  readerGuide: (scope: Scope, sceneId: string, viewpoint?: string) =>
    get<Schemas['GuideModel']>(scope, '/reader/guide', { scene: sceneId, viewpoint }),
  setSceneReveals: (scope: Scope, planId: string, reveals: Reveal[], expectedRevision: number) =>
    send<PlanItem>(scope, 'PUT', `/plan/${id(planId)}/reveals`, {
      reveals,
      expected_revision: expectedRevision,
    }),
  setSceneTime: (scope: Scope, planId: string, when: WorldTime | null, expectedRevision: number) =>
    send<PlanItem>(scope, 'PUT', `/plan/${id(planId)}/when`, {
      when,
      expected_revision: expectedRevision,
    }),

  // --- what the assistant would be given for a scene (ADR-021) ------------------
  sceneContext: (scope: Scope, planId: string, params: { budget?: number; secrets?: boolean } = {}) =>
    get<SceneContext>(scope, `/plan/${id(planId)}/context`, params),
  setSceneContext: (scope: Scope, planId: string, settings: ContextSettings, expectedRevision: number) =>
    send<SceneContext>(scope, 'PUT', `/plan/${id(planId)}/context`, {
      ...settings,
      expected_revision: expectedRevision,
    }),
  writingIndex: (scope: Scope) => get<WritingIndex>(scope, '/texts-index'),
  rebuildWritingIndex: (scope: Scope) =>
    send<{ passages: number }>(scope, 'POST', '/texts-index/rebuild'),

  // --- what-ifs: try a turn of the story without touching it (ADR-020) ----------
  // These are about the story's lines themselves, so they always speak to the main story.
  whatIfs: (scope: Scope) =>
    get<{ items: WhatIf[] }>({ ...scope, branchId: null }, '/what-ifs'),
  startWhatIf: (scope: Scope, body: { name: string; description?: string | null }) =>
    send<WhatIf>({ ...scope, branchId: null }, 'POST', '/what-ifs', body),
  setWhatIfAside: (scope: Scope, branchId: string, aside: boolean) =>
    send<WhatIf>({ ...scope, branchId: null }, 'POST', `/what-ifs/${id(branchId)}/set-aside`, {
      aside,
    }),
  compareWhatIf: (scope: Scope, branchId: string) =>
    get<WhatIfComparison>({ ...scope, branchId: null }, `/what-ifs/${id(branchId)}/compare`),
  bringBackWhatIf: (scope: Scope, branchId: string, keep: Record<string, 'what_if' | 'main'>) =>
    send<{ mutation_id: string }>(
      { ...scope, branchId: null },
      'POST',
      `/what-ifs/${id(branchId)}/bring-back`,
      { keep },
    ),

  // --- assisted creation: describe, answer a few questions, review the draft -----
  assistQuestions: (scope: Scope, body: AssistBrief) =>
    send<AssistQuestionSheet>(scope, 'POST', '/assist/questions', body),
  assistProposal: (scope: Scope, body: AssistProposalRequest) =>
    send<CharacterProposal>(scope, 'POST', '/assist/proposals', body),
  /** The God Hand: one instruction, every change it implies, as a proposal (or questions). */
  assistChange: (scope: Scope, body: Schemas['ChangeRequest']) =>
    send<ChangeProposal>(scope, 'POST', '/assist/changes', body),
  /** Roughly what the assistant will read, to show before a request is sent. */
  assistEstimate: (scope: Scope) => get<Schemas['EstimateModel']>(scope, '/assist/estimate'),
  /** Connections the entries describe but no fact records, as changes to review. */
  assistLinks: (scope: Scope, expectedRevision: number) =>
    send<ChangeProposal>(scope, 'POST', '/assist/links', {
      expected_revision: expectedRevision,
    } satisfies Schemas['LinkRequest']),
  /** Revise a set of suggested changes by asking; it stays waiting for review. */
  assistRevision: (scope: Scope, body: Schemas['RevisionRequest']) =>
    send<Schemas['RevisionModel']>(scope, 'POST', '/assist/revisions', body),
  // --- conversations with the assistant, kept so they can be resumed and read back ---
  /** The signed-in author's unfinished conversations in this project. */
  openAssistSessions: (scope: Scope) =>
    get<{ items: AssistSession[] }>(scope, '/assist/sessions'),
  /** One kept conversation, by its id. */
  assistSession: (scope: Scope, sessionId: string) =>
    get<AssistSession>(scope, `/assist/sessions/${id(sessionId)}`),
  /** The conversation a set of suggested changes came from, if one was kept. */
  assistSessionFor: async (scope: Scope, mutationId: string) =>
    (await get<{ items: AssistSession[] }>(scope, '/assist/sessions', { mutation_id: mutationId }))
      .items[0] ?? null,
  saveAssistAnswers: (scope: Scope, sessionId: string, answers: AssistSession['answers']) =>
    send<AssistSession>(scope, 'PUT', `/assist/sessions/${id(sessionId)}/answers`, { answers }),
  /** Add types and kinds of fact the assistant suggested to the project's setup. */
  applySuggestions: (scope: Scope, sessionId: string, names: string[]) =>
    send<Schemas['AppliedSuggestionsModel']>(
      scope,
      'POST',
      `/assist/sessions/${id(sessionId)}/suggestions/apply`,
      { names },
    ),
  closeAssistSession: (scope: Scope, sessionId: string) =>
    send<AssistSession>(scope, 'POST', `/assist/sessions/${id(sessionId)}/close`),
  /** Entries that are only a name so far: the to-do list for filling in. */
  needsDetail: (scope: Scope) => get<EntityListResponse>(scope, '/entities/needs-detail', { limit: 50 }),

  // --- guided character workflow ----------------------------------------------
  characterQuestions: (scope: Scope, entityId: string) =>
    send<CharacterQuestionSheet>(scope, 'POST', `/characters/${id(entityId)}/questions`),

  characterProposal: (scope: Scope, entityId: string, body: CharacterProposalRequest) =>
    send<CharacterProposal>(scope, 'POST', `/characters/${id(entityId)}/proposals`, body),

  // --- mutation review --------------------------------------------------------
  listMutations: (scope: Scope, params: { status?: string; limit?: number } = {}) =>
    get<MutationList>(scope, '/mutations', params),

  getMutation: (scope: Scope, mutationId: string) =>
    get<MutationDetail>(scope, `/mutations/${id(mutationId)}`),

  proposeMutation: (scope: Scope, body: ProposeMutationRequest) =>
    send<MutationDetail>(scope, 'POST', '/mutations', body),

  validateMutation: (scope: Scope, mutationId: string) =>
    send<MutationValidation>(scope, 'POST', `/mutations/${id(mutationId)}/validate`),

  /** Several keep/skip decisions in one request: all are recorded, or none. */
  decideOperations: (
    scope: Scope,
    mutationId: string,
    decisions: { ordinal: number; decision: 'accepted' | 'rejected'; note?: string | null }[],
  ) =>
    send<MutationDetail>(scope, 'POST', `/mutations/${id(mutationId)}/operations/decisions`, {
      decisions,
    } satisfies Schemas['OperationDecisionsRequest']),
  decideOperation: (
    scope: Scope,
    mutationId: string,
    ordinal: number,
    decision: 'accepted' | 'rejected',
    note: string | null,
  ) =>
    send<MutationDetail>(
      scope,
      'POST',
      `/mutations/${id(mutationId)}/operations/${ordinal}/decision`,
      { decision, note } satisfies Schemas['OperationDecisionRequest'],
    ),

  reviseOperation: (
    scope: Scope,
    mutationId: string,
    ordinal: number,
    operation: Record<string, unknown>,
    reason: string | null,
  ) =>
    send<MutationDetail>(scope, 'PUT', `/mutations/${id(mutationId)}/operations/${ordinal}`, {
      operation,
      reason,
    }),

  approveMutation: (scope: Scope, mutationId: string, note: string | null) =>
    send<MutationDetail>(scope, 'POST', `/mutations/${id(mutationId)}/approve`, {
      note,
    } satisfies Schemas['ReviewNoteRequest']),

  rejectMutation: (scope: Scope, mutationId: string, note: string | null) =>
    send<MutationDetail>(scope, 'POST', `/mutations/${id(mutationId)}/reject`, {
      note,
    } satisfies Schemas['ReviewNoteRequest']),

  retargetMutation: (scope: Scope, mutationId: string, expectedRevision: number) =>
    send<MutationDetail>(scope, 'POST', `/mutations/${id(mutationId)}/retarget`, {
      expected_revision: expectedRevision,
    } satisfies Schemas['RetargetRequest']),

  commitMutation: (scope: Scope, mutationId: string) =>
    send<MutationCommit>(scope, 'POST', `/mutations/${id(mutationId)}/commit`),

  // --- history ----------------------------------------------------------------
  listRevisions: (scope: Scope, params: { limit?: number; offset?: number } = {}) =>
    get<Items<RevisionSummary>>(scope, '/revisions', params),

  previewRevert: (scope: Scope, fromRevision: number, toRevision: number | null) =>
    send<RevertProposal>(scope, 'POST', '/reverts/preview', {
      from_revision: fromRevision,
      to_revision: toRevision,
    } satisfies Schemas['RevertPreviewRequest']),

  commitRevert: (
    scope: Scope,
    body: { fromRevision: number; toRevision: number | null; expectedRevision: number },
  ) =>
    send<MutationCommit>(scope, 'POST', '/reverts', {
      from_revision: body.fromRevision,
      to_revision: body.toRevision,
      expected_revision: body.expectedRevision,
      acknowledge_high_impact: true,
    } satisfies Schemas['RevertCommitRequest']),

  // --- media ------------------------------------------------------------------
  listAssets: (scope: Scope) => get<Items<Asset>>(scope, '/assets'),
  listAssetVersions: (scope: Scope, assetId: string) =>
    get<Items<AssetVersion>>(scope, `/assets/${id(assetId)}/versions`),
  assetLifecycle: (scope: Scope, assetId: string) =>
    get<Items<AssetLifecycleEvent>>(scope, `/assets/${id(assetId)}/lifecycle`),
  listLinks: (scope: Scope, params: { target_id?: string; include_retired?: boolean } = {}) =>
    get<Items<AssetLink>>(scope, '/links', params),
  listAssetLinks: (scope: Scope, assetId: string) =>
    get<Items<AssetLink>>(scope, `/assets/${id(assetId)}/links`, { include_retired: true }),

  // --- pictures on entries ----------------------------------------------------
  /** Upload an image (the file itself is the request body) and link it to an entry. */
  addPicture: (scope: Scope, entityId: string, file: Blob, expectedRevision: number, name?: string) =>
    request<{ link: AssetLink; version: AssetVersion }>(
      `${project(scope)}/entities/${id(entityId)}/pictures${query({
        expected_revision: expectedRevision,
        name,
      })}`,
      {
        method: 'POST',
        workspaceId: scope.workspaceId,
        body: file,
        headers: { 'Content-Type': file.type || 'application/octet-stream' },
      },
    ),
  /** Link an existing stored picture to an entry again (used to undo a removal). */
  relinkPicture: (scope: Scope, link: AssetLink, expectedRevision: number) =>
    send<AssetLink>(scope, 'POST', `/assets/${id(link.asset_id)}/links`, {
      target_id: link.target_record_id,
      target_record_type: link.target_record_type,
      asset_version_id: link.asset_version_id,
      role: link.role,
      expected_revision: expectedRevision,
    }),
  retireLink: (scope: Scope, linkId: string, expectedRevision: number) =>
    send<unknown>(scope, 'POST', `/links/${id(linkId)}/retire`, {
      expected_revision: expectedRevision,
    }),
  /**
   * A picture as a local object address. An <img> cannot send the workspace header
   * the server needs, so the bytes are fetched here and handed to the image.
   */
  pictureUrl: async (scope: Scope, assetId: string, versionId: string): Promise<string> => {
    const headers = new Headers(branchHeaders(scope))
    if (scope.workspaceId) headers.set('X-Workspace-Id', scope.workspaceId)
    const response = await fetch(
      `${project(scope)}/assets/${id(assetId)}/versions/${id(versionId)}/content`,
      { credentials: 'include', headers },
    )
    if (!response.ok) throw new ApiError(response.status, 'The picture could not be loaded.')
    return URL.createObjectURL(await response.blob())
  },
}
