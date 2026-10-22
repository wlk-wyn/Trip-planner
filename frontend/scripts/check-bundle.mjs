import { readFileSync } from 'node:fs'
import { createHash } from 'node:crypto'
import { staticClosure } from './bundle-report.mjs'

const report = JSON.parse(readFileSync('var/bundle-report.json','utf8'))
for (const chunk of report.chunks) {
  const bytes = readFileSync(`dist/${chunk.file}`)
  if (createHash('sha256').update(bytes).digest('hex') !== chunk.sha256) throw new Error(`Stale bundle report: ${chunk.file}`)
}
const limits = {Home:270000, Result:320000}
for (const name of Object.keys(limits)) {
  const roots = report.chunks.filter(c => c.modules.some(m => m.id === `src/views/${name}.vue` || m.id.startsWith(`src/views/${name}.vue?`)))
  if (!roots.length) throw new Error(`Missing ${name} route`)
  const files = staticClosure(report.chunks,[...report.entries,...roots.map(c=>c.file)])
  const chunks = report.chunks.filter(c=>files.includes(c.file))
  const gzipBytes = chunks.reduce((n,c)=>n+c.gzipBytes,0)
  if (gzipBytes > limits[name]) throw new Error(`${name} static dependency gzip ${gzipBytes} exceeds ${limits[name]} bytes`)
  for (const chunk of chunks) {
    if (chunk.modules.some(m=>/node_modules\/(?:jspdf|html2canvas)\//.test(m.id))) throw new Error(`Export dependency loaded before export: ${chunk.file}`)
    if (name === 'Home' && chunk.modules.some(m=>m.id.includes('AdvancedTripSettings.vue') || m.id.includes('src/views/Result.vue') || m.id.includes('node_modules/@amap/'))) {
      throw new Error(`Home contains a deferred feature: ${chunk.file}`)
    }
  }
  console.log(`${name} full static dependency gzip: ${gzipBytes} / ${limits[name]} bytes`)
}
