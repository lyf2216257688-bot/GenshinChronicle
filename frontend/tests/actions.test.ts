import assert from 'node:assert/strict'
import test from 'node:test'
import { applyEvent, createActions, DraftEditor } from '../src/chat/actions.ts'
import type { Client, Draft, Turn } from '../src/api/client.ts'

const draft: Draft = { token: 't', revision: 0, text: '', kind: 'question', target: null }
test('pending saves preserve newer edits; double send uses one persisted identity', async () => {
  const revisions: number[] = [],
    submitted: Draft[] = []
  const editor = new DraftEditor(
    {
      async save(_, d) {
        revisions.push(d.revision)
        await new Promise((r) => setTimeout(r, 5))
        return { ...d, revision: d.revision + 1 }
      },
      async submit(_, d) {
        submitted.push(d)
        return { id: 'turn' } as Turn
      },
    },
    'chat',
    draft,
  )
  editor.edit({ text: '上个问题' })
  const pending = editor.flush()
  editor.edit({ text: '上个问题的依据呢？' })
  await Promise.all([pending, editor.send(), editor.send()])
  assert.deepEqual(revisions, [0, 1])
  assert.equal(submitted.length, 1)
  assert.deepEqual(submitted[0], { ...draft, text: '上个问题的依据呢？', revision: 2 })
})
test('stream duplicates never repeat text or publish a draft', () => {
  let t = { cursor: 4, draft: '', answer: null, has_answer: false } as Turn
  const e = { kind: 'draft' as const, text: '片段', sequence: 5 }
  t = applyEvent(applyEvent(t, e), e)
  assert.equal(t.draft, '片段')
  assert.equal(t.has_answer, false)
  assert.equal(t.answer, null)
})
test('durable command and citation bindings gate every action', () => {
  const a = createActions({} as Client)
  const t = {
    id: 'new',
    citations: [{ turn_id: 'new', packet_id: 'newpacket', citation_id: 'E01' }],
    can_resume: false,
  } as Turn
  assert.throws(() => a.control(t, 'resume'))
  assert.throws(() => a.cite(t, { turn_id: 'old', packet_id: 'oldpacket', citation_id: 'E01' }))
})
test('retry after a lost submit response reuses the original submission token', async () => {
  const seen: Draft[] = []
  const editor = new DraftEditor(
    {
      async save(_, d) {
        return { ...d, revision: d.revision + 1 }
      },
      async submit(_, d) {
        seen.push(d)
        if (seen.length === 1) throw Error('connection lost')
        return { id: 'same' } as Turn
      },
    },
    'chat',
    draft,
  )
  editor.edit({ text: '重试' })
  await assert.rejects(editor.send())
  await editor.send()
  assert.deepEqual(seen[0], seen[1])
})
