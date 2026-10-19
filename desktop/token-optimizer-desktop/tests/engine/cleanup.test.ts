// Clean up's outcome through register.tsx inside the engine (#207): "Cleaned up." only
// on evidence that a compaction happened, never from the words of /compact's reply.
import { expect, test, type Engine } from 'claude-code/testing'

import { BAND, START, stub, type World } from './world.ts'

const has = async (ui: { findAll: (q: { type: string }) => Promise<{ text: string }[]> }, text: string) =>
  (await ui.findAll({ type: 'Text' })).some(t => t.text.includes(text))

const pressClean = async ($: Engine, on: Parameters<typeof stub>[0], compact: World['compact']) => {
  const w = stub(on, { compact })
  await $.session.start(START)
  await w.clock.settle() // the status read runs just after the start
  const ui = await $.ui.mount({ ...BAND, surface: 'desktop' })
  await ui.press({ key: 'card-quality-clean' })
  await w.clock.settle()
  return { w, ui }
}

test('a compaction that resets the live window is cleaned up, though its reply is empty', async ($, on) => {
  const { w, ui } = await pressClean($, on, 'ok')
  expect(w.compacts).toBe(1)
  expect(w.toasts).toEqual([])
  expect(await has(ui, 'Cleaned up.')).toBe(true)
})

test('a compaction whose reply is not English is still cleaned up', async ($, on) => {
  const { w, ui } = await pressClean($, on, 'ok-foreign')
  expect(w.toasts).toEqual([])
  expect(await has(ui, 'Cleaned up.')).toBe(true)
})

test('a compaction the band watched land is cleaned up, even before the window reads as reset', async ($, on) => {
  const w = stub(on, { compact: 'ok-watched' })
  w.compactBeneath = async () => {
    await $.session.compact({ trigger: 'manual', messages: [{ role: 'user', text: 'Earlier work.', toolUses: [], handle: 'm-1' }] } as never)
  }
  await $.session.start(START)
  await w.clock.settle() // the status read runs just after the start
  const ui = await $.ui.mount({ ...BAND, surface: 'desktop' })
  await ui.press({ key: 'card-quality-clean' })
  await w.clock.settle()
  expect(w.toasts).toEqual([])
  expect(await has(ui, 'Cleaned up.')).toBe(true)
})

test('a refusal in another language is named, not reported as cleaned up', async ($, on) => {
  const { w, ui } = await pressClean($, on, 'refused-foreign')
  expect(w.toasts).toEqual(['Clean up skipped: Pas assez de messages à compacter.'])
  expect(await has(ui, 'Cleaned up.')).toBe(false)
})

test('a /compact that resolves with no text and no compaction cannot say "Cleaned up."', async ($, on) => {
  const { w, ui } = await pressClean($, on, 'refused-silent')
  expect(w.toasts).toEqual(['Clean up could not confirm a compaction.'])
  expect(await has(ui, 'Cleaned up.')).toBe(false)
})

test('a reply that says "Compacted" with nothing behind it is not trusted', async ($, on) => {
  const { w, ui } = await pressClean($, on, 'said-compacted')
  expect(await has(ui, 'Cleaned up.')).toBe(false)
  expect(w.toasts.length).toBe(1)
})
