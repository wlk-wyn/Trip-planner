import { pageRanges, type TextBand } from './exportPages'

function normalized(source: HTMLElement) {
  const clone=source.cloneNode(true) as HTMLElement
  clone.querySelectorAll('.day-tabs,.day-pager,.itinerary-toolbar,.day-progress,.day-actions,.day-route-section,.panel-close,.quote-editor').forEach(el=>el.remove())
  clone.style.cssText='display:block;width:100%;max-width:none'
  clone.querySelectorAll<HTMLElement>('.day-panels').forEach(el=>{el.style.display='block'})
  clone.querySelectorAll<HTMLElement>('.day-panel').forEach(el=>{
    el.removeAttribute('inert');el.removeAttribute('aria-hidden');el.removeAttribute('hidden')
    el.style.cssText='display:block;position:relative;grid-area:auto;width:100%;opacity:1;transform:none;margin-bottom:24px'
  })
  clone.querySelectorAll('details').forEach(el=>{el.open=true})
  // Export text facts rather than lazy photos, placeholders or editing controls.
  clone.querySelectorAll('.attraction-image-wrapper').forEach(el=>{
    const price=el.querySelector('.price-tag')
    if(price){const line=document.createElement('p');line.className='export-price';line.textContent=`门票参考：${price.textContent?.trim() || '待补充'}`;el.before(line)}
    el.remove()
  })
  clone.querySelectorAll('.meal-image-wrapper,.ant-card-extra,.meal-edit-actions,.cost-context,.cost-note:has(input)').forEach(el=>el.remove())
  clone.querySelectorAll<HTMLButtonElement>('button').forEach(button=>{
    if(button.closest('.stop-content')){const text=document.createElement('span');text.textContent=button.textContent;button.replaceWith(text)}
    else button.remove()
  })
  clone.querySelectorAll<HTMLElement>('.extras-panel').forEach(panel=>{
    panel.style.display='block'
    const heading=document.createElement('h2')
    heading.textContent=panel.id==='overview'?'整体建议':panel.id==='budget'?'费用参考':panel.classList.contains('reference-usage')?'参考攻略':'数据来源说明'
    panel.prepend(heading)
  })
  clone.querySelectorAll('.extras-tabs').forEach(el=>el.remove())
  clone.querySelectorAll<HTMLElement>('*').forEach(el=>{
    el.style?.setProperty('animation','none','important')
    el.style?.setProperty('transition','none','important')
  })
  return clone
}

/** Separate document fixes both responsive media rules and the selected theme.
 * Only stylesheet markup is copied; no application scripts or live state run. */
async function surface(content: HTMLElement, title: string) {
  const frame=document.createElement('iframe')
  frame.setAttribute('aria-hidden','true')
  frame.style.cssText='position:absolute;left:-12000px;top:0;width:900px;height:1000px;border:0;pointer-events:none'
  document.body.appendChild(frame)
  const doc=frame.contentDocument!
  try {
    doc.documentElement.dataset.theme='light';doc.documentElement.dataset.motion='off'
    doc.body.style.cssText='margin:0;background:#fff;color:#202020'
    const loads:Promise<void>[]=[]
    document.querySelectorAll('style,link[rel=stylesheet]').forEach(style=>{
      if (style instanceof HTMLLinkElement) {
        const link=doc.createElement('link');link.rel='stylesheet';link.href=style.href
        loads.push(new Promise(resolve=>{link.onload=()=>resolve();link.onerror=()=>resolve()}))
        doc.head.appendChild(link)
      } else doc.head.appendChild(doc.importNode(style,true))
    })
    const css=doc.createElement('style')
    css.textContent=`
      *{animation:none!important;transition:none!important}
      details::details-content{block-size:auto!important;opacity:1!important;content-visibility:visible!important;transition:none!important}
      .day-panels{display:block!important}.day-panel{display:block!important;position:relative!important;grid-area:auto!important}
      .export-document *{color:inherit!important}
      .export-document :is(.ant-card,.ant-card-head,.ant-card-body,.ant-descriptions-item-label,.ant-descriptions-item-content,input,textarea){background:#fff!important;border-color:#e5e5e7!important}
      #app .export-document .ant-card-head{display:flex!important;min-height:0;padding:12px 14px}
      #app .export-document .ant-card-head-title{white-space:normal;overflow:visible}
      #app .export-document .ant-card-head-title,#app .export-document .ant-card-head-title *{color:#202020!important;-webkit-text-fill-color:#202020!important}
      #app .export-document .ant-card-body{padding:12px 14px}
      #app .export-document .ant-card{height:auto!important;min-height:0!important}
      #app .export-document .meal-type-badge{position:static;display:inline-block;margin-bottom:8px}
      #app .export-document .export-price{margin:12px 14px 0;font-size:12px}
      #app .export-document .timeline-stop{padding-top:6px;padding-bottom:12px}
      #app .export-document .timeline{margin:16px 0}
      #app .export-document .transfer{max-width:none;margin-bottom:10px;padding:8px 10px}
      #app .export-document .extras-panel{padding:14px 0}
      #app .export-document .extras-panel h2{font-size:18px;margin:0 0 12px}
      .export-document .ant-input-number-handler-wrap{display:none!important}
      button{pointer-events:none}
    `
    doc.head.appendChild(css)
    const app=doc.createElement('div');app.id='app';doc.body.appendChild(app)
    const root=doc.createElement('div');root.className='travel-redesign export-document'
    root.style.cssText='box-sizing:border-box;width:860px;padding:24px;background:#fff;color:#202020;font-family:system-ui,sans-serif'
    const heading=doc.createElement('h1');heading.textContent=title;heading.style.cssText='font-size:26px;font-weight:600;margin:0 0 24px'
    root.appendChild(heading);root.appendChild(doc.importNode(content,true));app.appendChild(root)
    await Promise.race([Promise.all(loads),new Promise(resolve=>setTimeout(resolve,5000))])
    await doc.fonts.ready
    await new Promise<void>(resolve=>requestAnimationFrame(()=>resolve()))
    return {root,remove:()=>frame.remove()}
  } catch(error) {frame.remove();throw error}
}

function textBands(root: HTMLElement):TextBand[] {
  const doc=root.ownerDocument, offset=root.getBoundingClientRect().top
  const walker=doc.createTreeWalker(root,NodeFilter.SHOW_TEXT)
  const bands:TextBand[]=[]
  while(walker.nextNode()) {
    const node=walker.currentNode
    if(!node.textContent?.trim())continue
    const range=doc.createRange();range.selectNodeContents(node)
    for(const rect of range.getClientRects())if(rect.width>0 && rect.height>0)bands.push({top:rect.top-offset,bottom:rect.bottom-offset})
  }
  return bands
}

function blockBands(root: HTMLElement):TextBand[] {
  const offset=root.getBoundingClientRect().top
  const bands=[...root.querySelectorAll('.timeline>li,.attraction-card,.meal-card,.hotel-card,.day-budget,.cost-lines article,.extras-panel h2')].map(el=>{
    const rect=el.getBoundingClientRect()
    return {top:rect.top-offset,bottom:rect.bottom-offset}
  })
  // A section title should travel with its first card, rather than becoming
  // the last line on an otherwise unrelated page.
  root.querySelectorAll('.place-details>.ant-divider,.extras-panel>h2').forEach(el=>{
    const next=el.nextElementSibling
    if(!next)return
    const first=next.querySelector('.ant-card,.cost-heading') || next
    bands.push({top:el.getBoundingClientRect().top-offset,bottom:first.getBoundingClientRect().bottom-offset})
  })
  return bands
}

const renderOptions={backgroundColor:'#ffffff',logging:false,useCORS:true,allowTaint:false,imageTimeout:5000,windowWidth:900}

export async function capturePlanImage(source:HTMLElement,title:string,name:string) {
  const {default:html2canvas}=await import('html2canvas')
  const view=await surface(normalized(source),title)
  try {
    const width=view.root.scrollWidth,height=view.root.scrollHeight
    const scale=Math.min(2,30000/height,Math.sqrt(32000000/(width*height)))
    if(scale<1)throw new Error('行程较长，请使用 PDF 或离线手册导出全部内容')
    const canvas=await html2canvas(view.root,{...renderOptions,scale})
    const blob=await new Promise<Blob>((resolve,reject)=>canvas.toBlob(value=>value?resolve(value):reject(new Error('图片编码失败')),'image/png'))
    const url=URL.createObjectURL(blob)
    const link=Object.assign(document.createElement('a'),{href:url,download:name})
    document.body.appendChild(link);link.click();link.remove()
    setTimeout(()=>URL.revokeObjectURL(url),1000)
    canvas.width=canvas.height=0
  } finally {view.remove()}
}

export async function capturePlanPdf(source:HTMLElement,title:string,name:string,progress:(part:number,total:number)=>void) {
  const [{default:html2canvas},{default:jsPDF}]=await Promise.all([import('html2canvas'),import('jspdf')])
  const clone=normalized(source)
  const days=[...clone.querySelectorAll<HTMLElement>('.day-panel')]
  const extras=clone.querySelector<HTMLElement>('.trip-extras')
  const parts=days.length?[...days,...(extras?[extras]:[])]:[clone]
  const pdf=new jsPDF({orientation:'portrait',unit:'mm',format:'a4',compress:true})
  let pages=0
  for(let part=0;part<parts.length;part++) {
    progress(part+1,parts.length)
    const view=await surface(parts[part],title)
    try {
      const bands=textBands(view.root)
      const blocks=blockBands(view.root)
      const height=view.root.scrollHeight,width=view.root.scrollWidth
      const scale=Math.min(1.5,30000/height,Math.sqrt(24000000/(height*width)))
      if(scale<.75)throw new Error('当天内容过长，请使用离线手册保留完整内容')
      const canvas=await html2canvas(view.root,{...renderOptions,scale})
      const ratio=canvas.height/height
      const capacity=285*canvas.width/198
      const pixels=(b:TextBand)=>({top:b.top*ratio,bottom:b.bottom*ratio})
      const ranges=pageRanges(canvas.height,capacity,bands.map(pixels),blocks.map(pixels))
      for(const [start,end] of ranges) {
        const slice=document.createElement('canvas');slice.width=canvas.width;slice.height=Math.ceil(end-start)
        slice.getContext('2d')!.drawImage(canvas,0,start,canvas.width,end-start,0,0,slice.width,slice.height)
        if(pages++)pdf.addPage()
        pdf.addImage(slice.toDataURL('image/jpeg',.86),'JPEG',6,6,198,slice.height*198/slice.width,undefined,'FAST')
        slice.width=slice.height=0
      }
      canvas.width=canvas.height=0
    } finally {view.remove()}
  }
  for(let page=1;page<=pages;page++){pdf.setPage(page);pdf.setFontSize(9);pdf.text(`${page} / ${pages}`,204,294,{align:'right'})}
  pdf.setProperties({title});pdf.save(name)
}
