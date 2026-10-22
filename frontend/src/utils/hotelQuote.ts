import type {Hotel} from '../types/index'
export function applicableHotelQuote(hotel: Pick<Hotel,'nightly_quote'|'place_id'>,night?: string){
  const quote=hotel.nightly_quote
  return quote && quote.night_date===night && quote.hotel_place_id===hotel.place_id && Number.isFinite(quote.price_per_room) && quote.price_per_room>=0 ? quote : null
}
