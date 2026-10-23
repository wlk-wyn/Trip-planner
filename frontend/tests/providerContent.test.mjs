import test from 'node:test'
import assert from 'node:assert/strict'
import { providerTokens, providerUrl, officialVisitUrl, ticketOffers, ticketPriceReferences } from '../src/utils/providerContent.ts'

test('provider prices stay exact and returned platform links become clickable', () => {
  const tokens=providerTokens('门票 **￥4XX起** [查看商品](http://dpurl.cn/demo)')
  assert.equal(tokens.map(token=>token.text).join(''),'门票 ￥4XX起 查看商品')
  assert.equal(tokens.find(token=>token.kind==='link').url,'http://dpurl.cn/demo')
  assert.equal(providerUrl('https://i.meituan.com/a?x=1'),'https://i.meituan.com/a?x=1')
  const bare = providerTokens('入口 https://i.meituan.com/demo。')
  assert.equal(bare.find(token=>token.kind==='link').url,'https://i.meituan.com/demo')
  assert.equal(bare.at(-1).text,'。')
})

test('executable HTML and lookalike domains never become live HTML or links', () => {
  const content='<script>alert(1)</script> [x](javascript:alert(1)) [x](https://meituan.com.evil.invalid/a)'
  assert.ok(providerTokens(content).every(token=>token.kind==='text'))
  for(const url of ['javascript:alert(1)','https://meituan.com@evil.invalid/a','https://evil.meituan.com:444/a','http://meituan.com/a','https://meituan.com\\@evil.invalid/a','https://meituan.com/a\nb']) assert.equal(providerUrl(url),null)
})

test('images use separate HTTPS platform CDN rules and preserve other text', () => {
  const tokens=providerTokens('图片 ![门票](https://p0.meituan.net/demo.jpg) 原价不变')
  assert.equal(tokens[1].kind,'image'); assert.equal(tokens[1].text,'门票')
  assert.equal(providerUrl('http://p0.meituan.net/a.jpg',true),null)
  assert.equal(providerUrl('https://untrusted.invalid/a.jpg',true),null)
  assert.equal(providerTokens('原文 [官方](https://example.invalid/)')[1].kind,'text')
})

test('official reference entry is limited to the reviewed policy websites', () => {
  assert.equal(officialVisitUrl('https://www.dpm.org.cn/singles_detail/259831.html'),'https://www.dpm.org.cn/singles_detail/259831.html')
  assert.equal(officialVisitUrl('javascript:alert(1)'),null)
  assert.equal(officialVisitUrl('https://www.dpm.org.cn.evil.invalid/a'),null)
  assert.equal(officialVisitUrl(undefined),null)
})

test('ticket cards preserve conflicting title and total prices without choosing one as payable', () => {
  const text='门票说明\n\n- [【成人票】上午场门票+精讲 ¥118](http://dpurl.cn/one)\n合计**¥148**（原价¥198，优惠¥50）\n\n- [【不限人群票】纯讲解服务（不含门票） ¥88](http://dpurl.cn/two)\n合计**¥88**\n\n[其他符合要求的商品](http://dpurl.cn/more)'
  const offers=ticketOffers(text)
  assert.equal(offers.length,2)
  assert.equal(offers[0].title,'【成人票】上午场门票+精讲')
  assert.equal(offers[0].titlePrice,'¥118')
  assert.equal(offers[0].priceNote,'合计**¥148**（原价¥198，优惠¥50）')
  assert.equal(offers[0].excludesAdmission,false)
  assert.equal(offers[1].excludesAdmission,true)
})

test('masked prices and quantities stay as supplied, missing prices stay missing', () => {
  const offers=ticketOffers('[【成人】门票 x2 ￥4XX起/人](https://i.meituan.com/one)\n\n[家庭联票](https://i.meituan.com/two)')
  assert.equal(offers[0].title,'【成人】门票 x2')
  assert.equal(offers[0].titlePrice,'￥4XX起/人')
  assert.equal(offers[0].priceNote,null)
  assert.equal(offers[1].titlePrice,null)
})

test('unsafe links and plain prose cannot create ticket products', () => {
  assert.deepEqual(ticketOffers('成人门票60元\n[官方门票](https://meituan.com.evil.invalid/a)\n![门票](https://p0.meituan.net/a.jpg)'),[])
  assert.equal(ticketOffers('[讲解服务](http://dpurl.cn/service)')[0].titlePrice,null)
})

test('policy price rows retain season, audience and units without becoming bookable offers', () => {
  const content='**普通门票与票价**：\n- **旺季门票（4月1日-10月31日）**：60元/人（10月27日处于旺季）。\n- **淡季门票（11月1日-次年3月31日）**：40元/人。\n- **珍宝馆/钟表馆**：各10元/人，需在大门票基础上单独预约。'
  const rows=ticketPriceReferences(content)
  assert.equal(rows.length,3)
  assert.equal(rows[0].title,'旺季门票（4月1日-10月31日）')
  assert.equal(rows[0].price,'60元/人')
  assert.equal(rows[0].condition,'（10月27日处于旺季）')
  assert.equal(rows[1].price,'40元/人')
  assert.equal(rows[2].price,'各10元/人')
  assert.ok(rows.every(row=>!('url' in row)))
  assert.equal(ticketOffers(content).length,0)
})

test('reference prices skip advertisements, ambiguous multiple prices and missing amounts', () => {
  const content='- [门票 ￥120](http://dpurl.cn/a)\n合计￥148（原价￥198）\n电话：123456789\n门票：成人60元，学生30元\n门票：价格待确认\n讲解票：￥4XX起/人，按场次预约'
  assert.deepEqual(ticketPriceReferences(content),[{title:'讲解票',price:'￥4XX起/人',condition:'按场次预约'}])
})

test('card inclusion details stay with their own product and preserve explicit exclusions', () => {
  const cards=ticketOffers('[成人门票 ¥120](http://dpurl.cn/a)\n包含：入园门票，1张。\n不含：讲解服务。\n\n## 联票\n[联票 ¥180](http://dpurl.cn/b)\n适用人群：成人，需实名预约。')
  assert.deepEqual(cards[0].details,['包含：入园门票，1张。','不含：讲解服务。'])
  assert.deepEqual(cards[1].details,['适用人群：成人，需实名预约。'])
})

test('reference ranges are not reduced to their last number', () => {
  assert.deepEqual(ticketPriceReferences('门票：60—80元/人，按场次选择'),[{title:'门票',price:'60—80元/人',condition:'按场次选择'}])
})
