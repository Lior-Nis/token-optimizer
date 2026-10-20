// Pure tests run under Node (npm test). Guards on the engine tests' own wiring, which CI
// cannot run (`claude plugin test` needs the claude CLI).
import { test } from 'node:test'
import assert from 'node:assert/strict'
import { readdirSync, readFileSync } from 'node:fs'

const dir = new URL('../engine/', import.meta.url)
const files = readdirSync(dir).filter(name => /\.test\.tsx?$/.test(name))

test('every engine test file takes `test` from world.ts, which lets its held waits go at the end', () => {
  // The kit's own `test` leaves a hung engine call held on the mocked clock; the kit lets
  // it go ten seconds later, inside whichever test then runs, which fails about 1 run in 4.
  assert.ok(files.length > 0)
  for (const name of files) {
    const source = readFileSync(new URL(name, dir), 'utf8')
    const fromKit = /import\s*\{([^}]*)\}\s*from\s*'claude-code\/testing'/.exec(source)?.[1] ?? ''
    assert.ok(!/(^|[\s,])test([\s,]|$)/.test(fromKit), `${name} imports test from the kit`)
    assert.match(source, /import\s*\{[^}]*\btest\b[^}]*\}\s*from\s*'\.\/world\.ts'/, `${name} does not import test from world.ts`)
  }
})
