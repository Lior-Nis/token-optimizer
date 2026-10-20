#!/usr/bin/env node
// Type-checks hooks/ and tests/engine/ against Claude Code's own plugin API declarations
// (`npm run typecheck:engine`). `npm run typecheck` (CI) covers src/ and tests/pure/ only:
// the API declarations are written by the claude CLI, never published as a package, so a
// CI runner without the CLI cannot resolve them. Run this locally before shipping a change
// to register.tsx or the engine tests.
//
// Where the declarations come from, first found wins:
//   1. CLAUDE_CODE_TYPES: a claude-code.d.ts (or the folder holding claude-code/index.d.ts)
//   2. .claude-plugin/types/ beside this mod or at the plugin root, where the engine lays
//      them when it loads the mod (a session started with --plugin-dir)
//   3. the copy the plugin-authoring skill writes under the temp folder when it loads
import { execFileSync } from 'node:child_process'
import { existsSync, mkdirSync, readdirSync, statSync, writeFileSync } from 'node:fs'
import { tmpdir } from 'node:os'
import { dirname, join, resolve } from 'node:path'
import { fileURLToPath } from 'node:url'

const mod = resolve(dirname(fileURLToPath(import.meta.url)), '..')
const root = resolve(mod, '..', '..')

function fromEnv() {
  const given = process.env.CLAUDE_CODE_TYPES
  if (!given) return null
  const path = statSync(given, { throwIfNoEntry: false })?.isDirectory() ? join(given, 'claude-code', 'index.d.ts') : given
  if (!existsSync(path)) {
    console.error(`CLAUDE_CODE_TYPES names ${given}, which holds no declarations.`)
    process.exit(1)
  }
  return path
}

function laid() {
  for (const base of [mod, root]) {
    const path = join(base, '.claude-plugin', 'types', 'claude-code', 'index.d.ts')
    if (existsSync(path)) return path
  }
  return null
}

/** The newest skill copy: <tmp>/claude-<uid>/bundled-skills/<version>/<hash>/plugin-authoring/types/claude-code.d.ts */
function skillCopy() {
  const found = []
  for (const tmp of new Set([tmpdir(), '/tmp', '/private/tmp'])) {
    for (const user of list(tmp).filter(n => n.startsWith('claude-'))) {
      const skills = join(tmp, user, 'bundled-skills')
      for (const version of list(skills)) {
        for (const hash of list(join(skills, version))) {
          const path = join(skills, version, hash, 'plugin-authoring', 'types', 'claude-code.d.ts')
          const stat = statSync(path, { throwIfNoEntry: false })
          if (stat) found.push({ path, mtime: stat.mtimeMs })
        }
      }
    }
  }
  return found.sort((a, b) => b.mtime - a.mtime)[0]?.path ?? null
}

function list(dir) {
  try {
    return readdirSync(dir)
  } catch {
    return []
  }
}

const types = fromEnv() ?? laid() ?? skillCopy()
if (!types) {
  console.error(
    'No Claude Code plugin API declarations found. Start a session with ' +
      '`claude --plugin-dir desktop/token-optimizer-desktop` (the engine lays them in .claude-plugin/types/), ' +
      'or point CLAUDE_CODE_TYPES at a claude-code.d.ts.',
  )
  process.exit(1)
}

// The options the declarations' own header recommends for a hooks module.
const config = {
  compilerOptions: {
    target: 'es2023',
    lib: ['es2023'],
    types: [],
    module: 'esnext',
    moduleResolution: 'bundler',
    strict: true,
    noUncheckedIndexedAccess: true,
    noEmit: true,
    skipLibCheck: true,
    allowImportingTsExtensions: true,
    jsx: 'react',
    jsxFactory: 'h',
    jsxFragmentFactory: 'Fragment',
  },
  files: [types],
  include: ['hooks', 'types', 'src', 'tests/engine'].map(d => join(mod, d)),
}
const out = join(mod, 'node_modules', '.cache', 'typecheck-engine')
mkdirSync(out, { recursive: true })
writeFileSync(join(out, 'tsconfig.json'), JSON.stringify(config, null, 2))

console.log(`Type-checking hooks/ and tests/engine/ against ${types}`)
try {
  execFileSync(join(mod, 'node_modules', '.bin', process.platform === 'win32' ? 'tsc.cmd' : 'tsc'), ['-p', join(out, 'tsconfig.json')], {
    stdio: 'inherit',
    windowsHide: true,
    shell: process.platform === 'win32',
  })
} catch {
  process.exit(1)
}
