import test from 'node:test'
import assert from 'node:assert/strict'
import {pageRanges} from '../src/utils/exportPages.ts'

test('PDF ranges cover the complete image once with no missing or repeated pixels',()=>{
  const pages=pageRanges(2500,1000,[])
  assert.deepEqual(pages,[[0,1000],[1000,2000],[2000,2500]])
})
test('page breaks move before wrapped text lines including overlapping inline spans',()=>{
  const pages=pageRanges(2100,1000,[{top:990,bottom:1010},{top:985,bottom:1003}])
  assert.equal(pages[0][1],983)
  for(let i=1;i<pages.length;i++)assert.equal(pages[i][0],pages[i-1][1])
  assert.equal(pages.at(-1)[1],2100)
})
test('oversized blocks cannot stall pagination and invalid dimensions do not export',()=>{
  assert.equal(pageRanges(2500,1000,[{top:0,bottom:2500}]).length,3)
  assert.deepEqual(pageRanges(Infinity,1000,[]),[])
  assert.deepEqual(pageRanges(20,0,[]),[])
})

test('a place card stays on one page when it fits, while oversized cards still flow',()=>{
  const pages=pageRanges(2200,1000,[],[{top:800,bottom:1300},{top:1400,bottom:2800}])
  assert.equal(pages[0][1],798)
  assert.equal(pages.at(-1)[1],2200)
  assert.ok(pages.every(([start,end])=>end>start && end-start<=1000))
})

test('a valid text line near the start of a page is preserved instead of being cut',()=>{
  const pages=pageRanges(1500,1000,[{top:100,bottom:1050}])
  assert.equal(pages[0][1],98)
  assert.equal(pages[1][0],98)
  assert.equal(pages.at(-1)[1],1500)
})
