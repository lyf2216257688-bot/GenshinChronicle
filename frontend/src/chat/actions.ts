import type {
  Chat,
  Citation,
  Client,
  Draft,
  Mode,
  ReportInput,
  Turn,
  TurnEvent,
} from '../api/client.ts'

type DraftFields = Pick<Draft, 'text' | 'target' | 'kind'>
const sameFields = (a: DraftFields, b: DraftFields) =>
  a.text === b.text && a.target === b.target && a.kind === b.kind

/** One editor per open chat. Acknowledgements only advance the revision they saved. */
export class DraftEditor {
  readonly sid: string
  private api: Pick<Client, 'save' | 'submit'>
  private saved: Draft
  fields: DraftFields
  private saving?: Promise<void>
  private sending?: Promise<Turn>
  constructor(api: Pick<Client, 'save' | 'submit'>, sid: string, draft: Draft) {
    this.api = api
    this.sid = sid
    this.saved = { ...draft }
    this.fields = { text: draft.text, target: draft.target, kind: draft.kind }
  }
  snapshot(): Draft {
    return { ...this.saved, ...this.fields }
  }
  get dirty() {
    return !sameFields(this.fields, this.saved)
  }
  edit(patch: Partial<DraftFields>) {
    this.fields = { ...this.fields, ...patch }
  }
  async flush(): Promise<void> {
    if (!this.saving) {
      const drain = async () => {
        while (this.dirty) this.saved = await this.api.save(this.sid, this.snapshot())
      }
      this.saving = drain().finally(() => {
        this.saving = undefined
      })
    }
    await this.saving
    if (this.dirty) await this.flush()
  }
  send(): Promise<Turn> {
    if (!this.sending) {
      this.sending = this.flush()
        .then(() => this.api.submit(this.sid, this.snapshot()))
        .finally(() => {
          this.sending = undefined
        })
    }
    return this.sending
  }
}

export function applyEvent(turn: Turn, e: TurnEvent): Turn {
  if (e.sequence <= turn.cursor) return turn
  return {
    ...turn,
    state: e.state ?? turn.state,
    cursor: e.sequence,
    stage: e.kind === 'stage' ? e.text : turn.stage,
    draft: e.kind === 'draft' ? turn.draft + e.text : turn.draft,
  }
}

export function createActions(api: Client) {
  return {
    async open(chat: Chat) {
      const [draft, page] = await Promise.all([api.draft(chat.id), api.history(chat.id)])
      return { editor: new DraftEditor(api, chat.id, draft), page }
    },
    async create(mode: Mode) {
      if (mode === 'MOCK') {
        const { items } = await api.batches()
        if (!items.some((b) => b.mode === mode && b.state === 'open'))
          await api.openBatch({ mode, requests: 10, minutes: 120, confirm_live: false })
      }
      return api.create(mode)
    },
    control(turn: Turn, action: 'stop' | 'resume') {
      if (!(action === 'stop' ? turn.can_stop : turn.can_resume))
        throw Error('此轮次当前不支持该操作。')
      return api[action](turn.id)
    },
    cite(turn: Turn, c: Citation) {
      if (
        !turn.citations.some(
          (x) =>
            x.turn_id === c.turn_id &&
            x.packet_id === c.packet_id &&
            x.citation_id === c.citation_id,
        )
      )
        throw Error('本轮没有绑定这处引用。')
      return api.source(c)
    },
    report: (turn: Turn, data: ReportInput) => api.report(turn.id, data),
  }
}
