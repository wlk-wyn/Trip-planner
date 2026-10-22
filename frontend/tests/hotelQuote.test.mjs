import test from 'node:test'
import assert from 'node:assert/strict'
import {applicableHotelQuote} from '../src/utils/hotelQuote.ts'

test('hotel rate is displayed only for its exact night and place identity',()=>{
  const hotel={place_id:'amap:hotel-a',nightly_quote:{night_date:'2026-11-07',hotel_place_id:'amap:hotel-a',price_per_room:300}}
  assert.equal(applicableHotelQuote(hotel,'2026-11-07').price_per_room,300)
  assert.equal(applicableHotelQuote(hotel,'2026-11-08'),null)
  assert.equal(applicableHotelQuote({...hotel,place_id:'amap:hotel-b'},'2026-11-07'),null)
})
test('an explicit zero quote remains distinct from unknown or invalid data',()=>{
  const hotel={place_id:'a',nightly_quote:{night_date:'2026-11-07',hotel_place_id:'a',price_per_room:0}}
  assert.equal(applicableHotelQuote(hotel,'2026-11-07').price_per_room,0)
  assert.equal(applicableHotelQuote({place_id:'a'},'2026-11-07'),null)
  assert.equal(applicableHotelQuote({...hotel,nightly_quote:{...hotel.nightly_quote,price_per_room:NaN}},'2026-11-07'),null)
})
