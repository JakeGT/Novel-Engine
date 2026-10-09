// Fixtures are typed from the generated contract, so a contract change breaks them at compile time.
import type {
  CanonEntity,
  Fact,
  MutationDetail,
  MutationOperation,
  ProjectionStatus,
  ProvenanceEntry,
  Vocabulary,
} from '../api/client'

export function entity(overrides: Partial<CanonEntity> = {}): CanonEntity {
  return {
    id: 'ent_mara',
    type: 'Character',
    name: 'Mara Venn',
    slug: 'mara-venn',
    lifecycle: 'active',
    schema_version_id: 'scv_1',
    created_revision: 1,
    retired_revision: null,
    attributes: { role: 'pilot' },
    aliases: ['The Fox'],
    external_ids: {},
    ...overrides,
  }
}

export const VOCABULARY: Vocabulary = {
  schema_version_id: 'scv_1',
  schema_version: 1,
  entity_types: [
    { name: 'Character', core: true, attributes_schema: null },
    { name: 'Location', core: true, attributes_schema: null },
    {
      name: 'Ship',
      core: false,
      attributes_schema: {
        type: 'object',
        additionalProperties: false,
        properties: {
          tonnage: { title: 'Tonnage', type: 'integer' },
          rig: { title: 'Rig', type: 'string', enum: ['sloop', 'brig'], default: null },
          motto: { title: 'Motto', type: 'string', maxLength: 10, default: null },
        },
        required: ['tonnage'],
      },
    },
  ],
  predicates: [
    {
      predicate: 'LOCATED_AT',
      core: true,
      subject_types: ['Character'],
      object_shape: 'entity_ref',
      object_types: ['Location'],
      cardinality: 'one',
      temporal_allowed: true,
    },
    {
      predicate: 'HAS_NAME',
      core: true,
      subject_types: ['Character', 'Location'],
      object_shape: 'string',
      object_types: [],
      cardinality: 'one',
      temporal_allowed: false,
    },
  ],
}

export function fact(overrides: Partial<Fact> = {}): Fact {
  return {
    id: 'fct_1',
    subject_id: 'ent_mara',
    predicate: 'LOCATED_AT',
    object: { kind: 'entity_ref', entity_id: 'ent_harbor', project_id: 'prj_1' },
    status: 'asserted',
    confidence: 1,
    provenance_id: 'prov_1',
    source_ref: null,
    created_revision: 2,
    closed_revision: null,
    valid_from: null,
    valid_until: null,
    ...overrides,
  }
}

export function operation(overrides: Partial<MutationOperation> = {}): MutationOperation {
  return {
    ordinal: 0,
    revision: 1,
    operation_type: 'create_entity',
    payload: { op: 'create_entity', id: 'tmp:a', type: 'Character', name: 'Iven' },
    decision: null,
    target_before: null,
    ...overrides,
  }
}

export function mutation(overrides: Partial<MutationDetail> = {}): MutationDetail {
  return {
    id: 'mut_1',
    status: 'needs_review',
    origin: 'model',
    intent: 'edit',
    actor_user_id: 'usr_1',
    expected_revision: 3,
    schema_version_id: 'scv_1',
    created_at: '2026-10-09T10:00:00Z',
    updated_at: '2026-10-09T10:00:00Z',
    head_revision: 3,
    is_stale: false,
    requires_review: true,
    review_reasons: ['protected_origin'],
    operations: [
      operation(),
      operation({
        ordinal: 1,
        operation_type: 'update_entity',
        payload: { op: 'update_entity', id: 'ent_mara', name: 'Mara Venn-Marsh' },
        target_before: { id: 'ent_mara', name: 'Mara Venn', type: 'Character' },
      }),
    ],
    decisions: [],
    transitions: [
      {
        from_status: 'draft',
        to_status: 'needs_review',
        actor_user_id: 'usr_1',
        reason: 'submitted for review',
        correlation_id: 'c1',
        created_at: '2026-10-09T10:00:00Z',
      },
    ],
    commit_id: null,
    committed_revision: null,
    ...overrides,
  }
}

export function projection(overrides: Partial<ProjectionStatus> = {}): ProjectionStatus {
  return {
    import: null,
    projection: 'graph',
    branch_id: 'brn_1',
    head_revision: 3,
    applied_revision: 3,
    lag: 0,
    status: 'current',
    current: true,
    last_error: null,
    failed_deliveries: 0,
    ...overrides,
  }
}

export function provenanceEntry(overrides: Partial<ProvenanceEntry> = {}): ProvenanceEntry {
  return {
    record_type: 'entity',
    record_id: 'ent_mara',
    revision: 1,
    commit_id: 'cmt_1',
    operation_type: 'create_entity',
    origin: 'model',
    intent: 'edit',
    schema_version_id: 'scv_1',
    actor_user_id: 'usr_editor',
    decisions: [
      {
        decision: 'approved',
        actor_user_id: 'usr_editor',
        note: 'checked against the outline',
        created_at: '2026-10-09T10:00:00Z',
      },
    ],
    evidence: [{ source_artefact_id: 'scene:ch01', location: 'chars:10-40' }],
    redacted: false,
    created_at: '2026-10-09T10:00:00Z',
    ...overrides,
  }
}
