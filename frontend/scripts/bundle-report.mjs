import { mkdirSync, readFileSync, writeFileSync } from 'node:fs'
import { dirname, relative, resolve } from 'node:path'
import { gzipSync } from 'node:zlib'
import { createHash } from 'node:crypto'

/** Count the entire static dependency graph, rather than just the renamed entry.
 * Dynamic imports stay out until that route or feature is actually requested. */
export function staticClosure(chunks, roots) {
  const byName = new Map(chunks.map(chunk => [chunk.file, chunk]))
  const seen = new Set()
  function visit(file) {
    if (seen.has(file)) return
    if (!byName.has(file)) throw new Error(`Cannot measure missing or external static chunk: ${file}`)
    seen.add(file)
    for (const dependency of byName.get(file).imports) visit(dependency)
  }
  for (const root of roots) visit(root)
  return [...seen].sort()
}

export function bundleReport() {
  let root
  let directory
  let chunks
  return {
    name: 'local-bundle-report', apply: 'build',
    configResolved(config) { root = config.root; directory = resolve(root, config.build.outDir) },
    generateBundle(_options, bundle) {
      chunks = Object.values(bundle).filter(item => item.type === 'chunk').map(chunk => ({
        name: chunk.name,
        file: chunk.fileName, entry: chunk.isEntry, dynamicEntry: chunk.isDynamicEntry,
        imports: chunk.imports, dynamicImports: chunk.dynamicImports,
        bytes: Buffer.byteLength(chunk.code), gzipBytes: gzipSync(chunk.code).length,
        styles: [...(chunk.viteMetadata?.importedCss || [])],
        modules: Object.entries(chunk.modules).map(([id, info]) => ({
          id: relative(root, id).replaceAll('\\', '/'), renderedBytes: info.renderedLength,
        })).sort((a,b) => b.renderedBytes-a.renderedBytes),
      }))
    },
    writeBundle() {
      // Vite injects preload dependencies after generateBundle. Measure files
      // after every plugin has finished writing, not that earlier code string.
      for (const chunk of chunks) {
        const bytes = readFileSync(resolve(directory, chunk.file))
        chunk.bytes = bytes.length; chunk.gzipBytes = gzipSync(bytes).length
        chunk.sha256 = createHash('sha256').update(bytes).digest('hex')
      }
      const entryFiles = chunks.filter(chunk => chunk.entry).map(chunk => chunk.file)
      const initialFiles = staticClosure(chunks, entryFiles)
      const initial = chunks.filter(chunk => initialFiles.includes(chunk.file))
      const report = { note: 'gzip sums use separately compressed files; not measured network latency. Module sizes precede minification.',
        entries: entryFiles, initialFiles, initialJsBytes: initial.reduce((n,c) => n+c.bytes,0),
        initialJsGzipBytes: initial.reduce((n,c) => n+c.gzipBytes,0), chunks,
        routes: Object.fromEntries(['Home','Result'].map(name => {
          const views = chunks.filter(c => c.modules.some(m => m.id === `src/views/${name}.vue` || m.id.startsWith(`src/views/${name}.vue?`)))
          if (!views.length) throw new Error(`Cannot locate ${name} route in emitted modules`)
          const files = staticClosure(chunks, [...entryFiles, ...views.map(c => c.file)])
          return [name, {files, jsBytes:chunks.filter(c => files.includes(c.file)).reduce((n,c) => n+c.bytes,0),
            gzipBytes:chunks.filter(c => files.includes(c.file)).reduce((n,c) => n+c.gzipBytes,0)}]
        })) }
      const output = resolve(root, 'var/bundle-report.json')
      mkdirSync(dirname(output), {recursive:true})
      writeFileSync(output, JSON.stringify(report, null, 2))
      this.info(`Initial static JS: ${report.initialJsBytes} bytes / ${report.initialJsGzipBytes} gzip bytes; report: var/bundle-report.json`)
    },
  }
}
