import { readdirSync } from 'node:fs'
import { spawnSync } from 'node:child_process'
import { fileURLToPath } from 'node:url'

const directory = fileURLToPath(new URL('../tests/', import.meta.url))
const files = readdirSync(directory).filter(name => name.endsWith('.test.mjs')).sort()
if (!files.length) throw new Error('No frontend tests found')
const failures = []
for (const file of files) {
  const result = spawnSync(process.execPath, [fileURLToPath(new URL(`../tests/${file}`, import.meta.url))], {
    stdio: 'inherit', cwd: fileURLToPath(new URL('..', import.meta.url)),
  })
  if (result.error || result.status !== 0) failures.push(file)
}
console.log(`\n${files.length - failures.length}/${files.length} test files passed`)
if (failures.length) {
  console.error('Failed:', failures.join(', '))
  process.exitCode = 1
}
