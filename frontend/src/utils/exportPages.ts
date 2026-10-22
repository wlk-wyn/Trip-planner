export interface TextBand { top: number; bottom: number }

/** Split at safe line boundaries; ranges cover the image exactly once.
 * Oversized non-text content may span pages, but no artificial content is lost. */
export function pageRanges(height: number, capacity: number, bands: TextBand[], blocks: TextBand[] = []): [number, number][] {
  if (!Number.isFinite(height) || !Number.isFinite(capacity) || height <= 0 || capacity <= 0) return []
  const valid = bands.filter(b => Number.isFinite(b.top) && Number.isFinite(b.bottom) && b.bottom > b.top && b.bottom-b.top <= capacity)
  const keep = blocks.filter(b => Number.isFinite(b.top) && Number.isFinite(b.bottom) && b.bottom>b.top && b.bottom-b.top<=capacity*.75)
  const pages: [number, number][] = []
  let start=0
  while (start < height) {
    let end=Math.min(height,start+capacity)
    if (end < height) {
      const crossingBlocks=keep.filter(b=>b.top>start+2 && b.top<end && b.bottom>end)
      if(crossingBlocks.length)end=Math.min(...crossingBlocks.map(b=>b.top))-2
      // Inline spans can overlap vertically. Move before every intersected line.
      for (let attempt=0;attempt<valid.length+1;attempt++) {
        const crossing=valid.filter(b=>b.top<end && b.bottom>end && b.bottom>start)
        if (!crossing.length) break
        const next=Math.min(...crossing.map(b=>b.top))-2
        if (next <= start || next >= end) break
        end=next
      }
    }
    pages.push([start,end]);start=end
  }
  return pages
}
