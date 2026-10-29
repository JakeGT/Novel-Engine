import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { useEffect, useRef, useState, type FormEvent } from 'react'
import {
  api,
  type CheckProfile,
  type SetAssistantSettings,
  type StoryAssistantSettings,
  type Studio,
} from '../api/client'
import { PROFILE_LABEL, asked, useAssistantSettings } from '../lib/assistantSettings'
import { setSpendAsker, type SpendQuestion } from '../lib/spend'
import { plural } from '../lib/words'
import { scopedKey, useProject } from '../project'
import { SendPreview } from './SendPreview'
import { Button, ConfirmDialog, Dialog, ErrorNotice, Field, Loading } from './ui'

const number = (value: number) => value.toLocaleString('en-GB')
const clock = (when: string) => new Date(when).toLocaleTimeString('en-GB', { hour: '2-digit', minute: '2-digit' })

const SCENE_ALLOWANCES = [20_000, 40_000, 100_000, 250_000]
const MINUTES = [5, 10, 15, 30, 60]
const CONFIRM_SIZES = [10_000, 25_000, 50_000, 100_000]
const CONVERSATION_SIZES = [20_000, 50_000, 100_000, 250_000]

function withCurrent(options: number[], current: number | null | undefined): number[] {
  return current && !options.includes(current) ? [...options, current].sort((a, b) => a - b) : options
}


/**
 * How much checking with the assistant runs on its own in this story (CON-009), each
 * choice with what it costs, said before choosing. Checks without the assistant always
 * run. With a scene, the cost of one check of it is worked out (no model is called).
 */
export function CheckingChoice({ planId, onSaved }: { planId?: string; onSaved?: () => void }) {
  const { scope, canWrite } = useProject()
  const { settings, save } = useAssistantSettings()
  const estimate = useQuery({
    queryKey: scopedKey(scope, 'check-estimate', planId),
    queryFn: () => api.checkEstimate(scope, planId as string),
    enabled: Boolean(planId),
    staleTime: 60_000,
  })
  const [draft, setDraft] = useState<SetAssistantSettings | null>(null)
  const saved = settings.data
  if (settings.isPending) return <Loading>Opening the settings…</Loading>
  if (!saved) return <ErrorNotice error={settings.error} prefix="Could not open the settings" />
  const current = draft ?? asked(saved)
  const changed = draft !== null && JSON.stringify(draft) !== JSON.stringify(asked(saved))
  const set = (part: Partial<SetAssistantSettings>) => {
    setDraft({ ...current, ...part })
    save.reset()
  }
  const per = estimate.data?.total_tokens ?? null
  const scene = current.scene_tokens ?? 40_000
  const minutes = current.check_minutes ?? 15
  const checks = per ? Math.max(1, Math.floor(scene / per)) : null
  const eachCheck = per
    ? `about ${number(per)} tokens each check of this scene`
    : 'a few thousand tokens each check, depending on the scene'

  const options: { value: CheckProfile; label: string; what: string; cost: string }[] = [
    {
      value: 'on_request',
      label: PROFILE_LABEL.on_request,
      what: 'The assistant checks a scene only when you press “Check with the assistant”.',
      cost: 'Costs nothing on its own.',
    },
    {
      value: 'balanced',
      label: PROFILE_LABEL.balanced,
      what: 'After you keep a draft, the assistant checks the scene for you.',
      cost: `Uses ${eachCheck}, and never more than ${number(scene)} tokens a day for one scene.`,
    },
    {
      value: 'thorough',
      label: PROFILE_LABEL.thorough,
      what: `Also after you save, at most once every ${minutes} minutes.`,
      cost: `Uses ${eachCheck}${checks ? `: up to ${plural(checks, 'check')}` : ''} and ${number(scene)} tokens a day for one scene.`,
    },
  ]

  const onSubmit = (event: FormEvent) => {
    event.preventDefault()
    if (!changed) return
    save.mutate(current, {
      onSuccess: () => {
        setDraft(null)
        onSaved?.()
      },
    })
  }

  return (
    <form className="stack" onSubmit={onSubmit} aria-label="Checking on its own">
      <p className="muted small">
        Checks that need no assistant always run, for free. This chooses whether the assistant
        also checks on its own, for the whole story. It starts only after you save or keep a
        draft, and it never changes your text.
      </p>
      <fieldset className="stack tight">
        <legend>How much the assistant checks on its own</legend>
        {options.map((option) => (
          <label key={option.value} className={`check profile-option ${current.check_profile === option.value ? 'chosen' : ''}`}>
            <input
              type="radio"
              name="check-profile"
              value={option.value}
              checked={current.check_profile === option.value}
              disabled={!canWrite}
              onChange={() => set({ check_profile: option.value })}
            />
            <span className="stack tight">
              <strong>{option.label}</strong>
              <span className="small">{option.what}</span>
              <span className="small muted">{option.cost}</span>
            </span>
          </label>
        ))}
      </fieldset>
      {current.check_profile !== 'on_request' && (
        <div className="grid-2">
          <Field label="Most for one scene in a day" hint="Checks on their own stop for the day at this. Asking by hand still works.">
            {(props) => (
              <select
                {...props}
                value={String(scene)}
                disabled={!canWrite}
                onChange={(event) => set({ scene_tokens: Number(event.target.value) })}
              >
                {withCurrent(SCENE_ALLOWANCES, saved.scene_tokens).map((value) => (
                  <option key={value} value={String(value)}>
                    {number(value)} tokens
                  </option>
                ))}
              </select>
            )}
          </Field>
          {current.check_profile === 'thorough' && (
            <Field label="After saving, at most once every">
              {(props) => (
                <select
                  {...props}
                  value={String(minutes)}
                  disabled={!canWrite}
                  onChange={(event) => set({ check_minutes: Number(event.target.value) })}
                >
                  {withCurrent(MINUTES, saved.check_minutes).map((value) => (
                    <option key={value} value={String(value)}>
                      {value} minutes
                    </option>
                  ))}
                </select>
              )}
            </Field>
          )}
        </div>
      )}
      <p className="hint">
        A token is roughly three-quarters of a word. Checks on their own count towards your
        allowance like any other, and wait when you have something else under way.
        {saved.effective_confirm_above_tokens
          ? ` A check larger than ${number(saved.effective_confirm_above_tokens)} tokens waits for you to ask.`
          : ''}
      </p>
      <ErrorNotice error={save.error} prefix="Not saved" />
      {save.isSuccess && !changed && (
        <p className="notice ok" role="status">
          Saved. {saved.check_profile === 'on_request' ? 'The assistant checks only when you ask.' : `${PROFILE_LABEL[saved.check_profile]} checking is on for this story.`}
        </p>
      )}
      {canWrite && (
        <div className="actions">
          <Button type="submit" variant="primary" disabled={!changed || save.isPending}>
            {save.isPending ? 'Saving…' : 'Save how much it checks'}
          </Button>
        </div>
      )}
    </form>
  )
}

/** The studio's way in to the checking choice. */
export function CheckingDialog({ planId, onClose }: { planId: string; onClose: () => void }) {
  return (
    <Dialog title="Checking on its own" onClose={onClose} wide>
      <CheckingChoice planId={planId} />
    </Dialog>
  )
}

const SKIPPED: Record<string, string> = {
  scene_allowance: 'This scene has used today’s allowance for checks on their own.',
  needs_confirmation: 'A check of this scene is larger than the size you asked to be asked about, so it waits for you to ask.',
  allowance: 'Your assistant allowance is used up for now.',
  no_provider: 'No AI model is set up, so nothing could check on its own.',
  job_cap: 'You have three pieces of work under way in this story already.',
  not_started: 'The check could not be started just now.',
}

/**
 * Beside "Check with the assistant": whether the story checks on its own, what that has
 * used in this scene today, and why the last save or kept draft did not start a check.
 */
export function CheckingStatus({
  checking,
  automatic,
  onChange,
}: {
  checking: Studio['checking']
  automatic: string | null
  onChange?: () => void
}) {
  if (!checking) return null
  const on = checking.profile !== 'on_request'
  const skipped = automatic ? SKIPPED[automatic] : undefined
  return (
    <div className="stack tight small checking-status">
      <p>
        {on ? (
          <>
            <strong>{PROFILE_LABEL[checking.profile]}:</strong>{' '}
            {checking.profile === 'thorough'
              ? `checks on its own after you keep a draft or save, at most every ${checking.check_minutes} minutes.`
              : 'checks on its own after you keep a draft.'}{' '}
            {number(checking.spent_tokens)} of {number(checking.scene_tokens)} tokens used for this scene today.
          </>
        ) : (
          'Checks with the assistant only when you ask.'
        )}{' '}
        {onChange && (
          <button type="button" className="link-button" onClick={onChange}>
            Change how much it checks
          </button>
        )}
      </p>
      {on && checking.next_after_save && automatic === 'too_soon' && (
        <p className="hint">
          The next check after saving can start from {clock(checking.next_after_save)}.
        </p>
      )}
      {skipped && (
        <p className="hint" role="status">
          Not checked on its own this time. {skipped} You can still ask.
        </p>
      )}
    </div>
  )
}

/** A select of token sizes with "Off". */
function SizeField({
  label,
  hint,
  value,
  sizes,
  offLabel,
  disabled,
  onChange,
}: {
  label: string
  hint: string
  value: number | null
  sizes: number[]
  offLabel: string
  disabled: boolean
  onChange: (next: number | null) => void
}) {
  return (
    <Field label={label} hint={hint}>
      {(props) => (
        <select
          {...props}
          value={value === null ? '' : String(value)}
          disabled={disabled}
          onChange={(event) => onChange(event.target.value === '' ? null : Number(event.target.value))}
        >
          <option value="">{offLabel}</option>
          {withCurrent(sizes, value).map((size) => (
            <option key={size} value={String(size)}>
              {number(size)} tokens
            </option>
          ))}
        </select>
      )}
    </Field>
  )
}

/**
 * For this story: ask before any one request larger than a size, and the most one
 * conversation with the assistant may use (OPS-002, AST-EXP-011). A workspace owner's
 * lower limits win, and say so.
 */
export function SpendingCard() {
  const { canWrite } = useProject()
  const { settings, save } = useAssistantSettings()
  const [draft, setDraft] = useState<SetAssistantSettings | null>(null)
  const saved = settings.data
  return (
    <section className="card" aria-labelledby="spending-heading">
      <div className="card-head">
        <div>
          <h2 id="spending-heading">Spending in this story</h2>
          <p className="muted">
            Every request says about how many tokens it will use before you send it. These make the
            assistant stop and ask, so nothing large is spent by accident.
          </p>
        </div>
      </div>
      {settings.isPending && <Loading>Opening the settings…</Loading>}
      <ErrorNotice error={settings.error} prefix="Could not open the settings" />
      {saved && (
        <SpendingForm
          saved={saved}
          draft={draft}
          canWrite={canWrite}
          pending={save.isPending}
          error={save.error}
          done={save.isSuccess}
          onDraft={(next) => {
            setDraft(next)
            save.reset()
          }}
          onSave={(body) => save.mutate(body, { onSuccess: () => setDraft(null) })}
        />
      )}
      <details className="stack tight">
        <summary>How much the assistant checks on its own</summary>
        <CheckingChoice />
      </details>
    </section>
  )
}

function SpendingForm({
  saved,
  draft,
  canWrite,
  pending,
  error,
  done,
  onDraft,
  onSave,
}: {
  saved: StoryAssistantSettings
  draft: SetAssistantSettings | null
  canWrite: boolean
  pending: boolean
  error: unknown
  done: boolean
  onDraft: (next: SetAssistantSettings) => void
  onSave: (body: SetAssistantSettings) => void
}) {
  const current = draft ?? asked(saved)
  const changed = draft !== null && JSON.stringify(draft) !== JSON.stringify(asked(saved))
  const workspaceConfirm = saved.workspace_confirm_above_tokens ?? null
  const workspaceConversation = saved.workspace_conversation_tokens ?? null
  return (
    <form
      className="stack"
      onSubmit={(event) => {
        event.preventDefault()
        if (changed) onSave(current)
      }}
    >
      <div className="grid-2">
        <SizeField
          label="Ask me first when one request would use more than"
          hint={
            workspaceConfirm
              ? `Your workspace asks first above ${number(workspaceConfirm)} tokens; a lower size here takes its place.`
              : 'Drafts, checks, readings and replies alike. Nothing is sent until you agree.'
          }
          value={current.confirm_above_tokens ?? null}
          sizes={CONFIRM_SIZES}
          offLabel="Never ask"
          disabled={!canWrite}
          onChange={(next) => onDraft({ ...current, confirm_above_tokens: next })}
        />
        <SizeField
          label="Most one conversation may use"
          hint={
            workspaceConversation
              ? `Your workspace allows ${number(workspaceConversation)} tokens a conversation; a lower size here takes its place.`
              : 'A conversation stops here; you can start a new one.'
          }
          value={current.conversation_tokens ?? null}
          sizes={CONVERSATION_SIZES}
          offLabel="No limit"
          disabled={!canWrite}
          onChange={(next) => onDraft({ ...current, conversation_tokens: next })}
        />
      </div>
      <ErrorNotice error={error} prefix="Not saved" />
      {done && !changed && (
        <p className="notice ok" role="status">
          Saved.
        </p>
      )}
      {canWrite && (
        <div className="actions">
          <Button type="submit" variant="primary" disabled={!changed || pending}>
            {pending ? 'Saving…' : 'Save spending limits'}
          </Button>
        </div>
      )}
    </form>
  )
}

/** An owner's limits for everyone in the workspace (hosted). */
export function WorkspaceLimitsCard({ workspaceId }: { workspaceId: string }) {
  const client = useQueryClient()
  const limits = useQuery({
    queryKey: ['ai-limits', workspaceId],
    queryFn: () => api.workspaceLimits(workspaceId),
  })
  const [confirm, setConfirm] = useState<number | null | undefined>(undefined)
  const [conversation, setConversation] = useState<number | null | undefined>(undefined)
  const save = useMutation({
    mutationFn: () =>
      api.setWorkspaceLimits(workspaceId, {
        confirm_above_tokens: confirm === undefined ? (limits.data?.confirm_above_tokens ?? null) : confirm,
        conversation_tokens:
          conversation === undefined ? (limits.data?.conversation_tokens ?? null) : conversation,
      }),
    onSuccess: async (saved) => {
      client.setQueryData(['ai-limits', workspaceId], saved)
      setConfirm(undefined)
      setConversation(undefined)
      await client.invalidateQueries({ queryKey: ['assistant-settings'] })
    },
  })
  if (!limits.data) return <ErrorNotice error={limits.error} prefix="Could not load the workspace limits" />
  const changed = confirm !== undefined || conversation !== undefined
  return (
    <section className="card" aria-labelledby="limits-heading">
      <div className="card-head">
        <div>
          <h2 id="limits-heading">Limits for everyone</h2>
          <p className="muted">For every story in this workspace. A story can choose lower limits, never higher.</p>
        </div>
      </div>
      <form
        className="stack"
        onSubmit={(event) => {
          event.preventDefault()
          if (changed) save.mutate()
        }}
      >
        <div className="grid-2">
          <SizeField
            label="Ask first when one request would use more than"
            hint="Everyone is asked before sending anything larger."
            value={confirm === undefined ? (limits.data.confirm_above_tokens ?? null) : confirm}
            sizes={CONFIRM_SIZES}
            offLabel="Never ask"
            disabled={save.isPending}
            onChange={setConfirm}
          />
          <SizeField
            label="Most one conversation may use"
            hint="Counted from what your provider reports."
            value={conversation === undefined ? (limits.data.conversation_tokens ?? null) : conversation}
            sizes={CONVERSATION_SIZES}
            offLabel="No limit"
            disabled={save.isPending}
            onChange={setConversation}
          />
        </div>
        <ErrorNotice error={save.error} prefix="The limits were not changed" />
        {save.isSuccess && !changed && (
          <p className="notice ok" role="status">
            Saved.
          </p>
        )}
        <div className="actions">
          <Button type="submit" variant="primary" disabled={!changed || save.isPending}>
            {save.isPending ? 'Saving…' : 'Save limits for everyone'}
          </Button>
        </div>
      </form>
    </section>
  )
}

/**
 * Asks before a request larger than the author chose (lib/spend). Mounted once; any
 * request refused for its size waits here for the author's answer, then is sent again
 * or not at all.
 */
export function SpendConfirmHost() {
  const [question, setQuestion] = useState<SpendQuestion | null>(null)
  const answer = useRef<((agreed: boolean) => void) | null>(null)
  useEffect(() => {
    setSpendAsker(
      (next) =>
        new Promise<boolean>((resolve) => {
          // A second question while one is open is answered "no": one thing at a time.
          if (answer.current) return resolve(false)
          answer.current = resolve
          setQuestion(next)
        }),
    )
    return () => setSpendAsker(null)
  }, [])
  if (!question) return null
  const reply = (agreed: boolean) => {
    answer.current?.(agreed)
    answer.current = null
    setQuestion(null)
  }
  const sending = question.sending
  const pictures = sending?.pictures.length ?? 0
  // Asked only because pictures would leave the site, not for the size.
  const forPictures = pictures > 0 && question.thresholdTokens === null
  return (
    <ConfirmDialog
      title={
        forPictures && sending
          ? `Send ${pictures === 1 ? 'a picture' : `${pictures} pictures`} to ${sending.provider_label}?`
          : `Use about ${number(question.estimatedTokens)} tokens?`
      }
      confirmLabel={`Send it (about ${number(question.estimatedTokens)} tokens)`}
      keepLabel="Don’t send"
      danger={false}
      onConfirm={() => reply(true)}
      onCancel={() => reply(false)}
    >
      <p>{question.detail}</p>
      {sending && <SendPreview sending={sending} scope={question.scope} />}
      <p className="muted small">
        Nothing has been sent yet. The size is an estimate from what it will read and the longest
        reply it may write.{' '}
        {forPictures
          ? 'You are asked each time pictures from your story would go to a company’s servers.'
          : 'You can change when you are asked on the AI assistant page.'}
      </p>
    </ConfirmDialog>
  )
}
