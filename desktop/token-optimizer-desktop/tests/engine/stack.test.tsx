// The band shares AbovePrompt (#263): another mod's band later in the chain is drawn
// beneath ours, in both sizes; with nothing beneath, ours is drawn alone, as before.
import type { On } from 'claude-code'
import { expect, type Engine } from 'claude-code/testing'

import { BAND, START, stub, test } from './world.ts'

type Drawn = { type: string; props?: Record<string, unknown>; part?: { plugin: string }; children?: Drawn[] }

/**
 * A second mod drawing in AbovePrompt, as the issue's probe does: it passes the band on
 * itself. The test's hooks sit beneath the plugin in the order they are added.
 */
function probe(on: On): void {
  on('ui.render', { component: 'AbovePrompt' }, async ($, e, next) => {
    const { Box, Text } = $.ui.resolve(e)
    const beneath = await next(e)
    const mine = <Text key="probe">probe band</Text>
    return beneath.type === 'engine' ? mine : <Box flexDirection="column">{mine}{beneath}</Box>
  })
}

/** The engine's own AbovePrompt at the bottom, as in a session: its reference, nothing drawn. */
function engineBand(on: On): void {
  on('ui.render', { component: 'AbovePrompt' }, async () => ({ type: 'engine', ref: 0 }) as never)
}

async function mount($: Engine, on: On, size: 'full' | 'slim', beneath: (on: On) => void) {
  const w = stub(on, { store: { 'status-bar-size': size } })
  beneath(on)
  await $.session.start(START)
  await w.clock.settle()
  const ui = await $.ui.mount({ ...BAND, surface: 'desktop' })
  return { ui, root: (await ui.drawn()) as Drawn }
}

const label = (size: 'full' | 'slim') => (size === 'slim' ? '+' : '–')
const isBand = (el: Drawn | undefined) => el?.type === 'Box' && el.props?.key === 'band'
const isProbe = (el: Drawn | undefined) => el?.type === 'Text' && el.part?.plugin === 'test'

for (const size of ['full', 'slim'] as const) {
  test(`${size} band: another mod's band above the prompt is drawn too, beneath ours`, async ($, on) => {
    const { ui, root } = await mount($, on, size, on => {
      probe(on)
      engineBand(on)
    })
    // Ours (its size button says which band drew) and the probe's, in one column: ours first.
    expect((await ui.find({ key: 'size' }))?.props.label).toBe(label(size))
    expect(await ui.find({ type: 'Text', text: 'probe band' })).toBeDefined()
    expect(root.type).toBe('Box')
    expect(root.props?.flexDirection).toBe('column')
    expect(root.children?.length).toBe(2)
    expect(isBand(root.children?.[0])).toBe(true)
    expect(isProbe(root.children?.[1])).toBe(true)
  })

  test(`${size} band: with only the engine beneath, ours is drawn alone, unwrapped`, async ($, on) => {
    const { ui, root } = await mount($, on, size, engineBand)
    expect((await ui.find({ key: 'size' }))?.props.label).toBe(label(size))
    expect(await ui.find({ type: 'Text', text: 'probe band' })).toBeUndefined()
    // No wrapper: the band's own keyed Box is the whole tree, exactly as before #263.
    expect(isBand(root)).toBe(true)
  })

  test(`${size} band: a mod beneath that fails costs its band, never ours`, async ($, on) => {
    const { ui, root } = await mount($, on, size, on => {
      on('ui.render', { component: 'AbovePrompt' }, async () => {
        throw new Error('a broken band beneath')
      })
    })
    expect((await ui.find({ key: 'size' }))?.props.label).toBe(label(size))
    expect(isBand(root)).toBe(true)
  })
}

test('the band keeps theirs beneath across size presses', async ($, on) => {
  const { ui } = await mount($, on, 'full', on => {
    probe(on)
    engineBand(on)
  })
  for (const size of ['slim', 'full'] as const) {
    await ui.press({ key: 'size' })
    expect((await ui.find({ key: 'size' }))?.props.label).toBe(label(size))
    const root = (await ui.drawn()) as Drawn
    expect(isBand(root.children?.[0])).toBe(true)
    expect(isProbe(root.children?.[1])).toBe(true)
  }
})
