import type { Dayjs } from 'dayjs'
import type { TripFormData } from '../types'

export type TripFormDraft = Omit<TripFormData, 'start_date' | 'end_date'> & {
  start_date: Dayjs | null
  end_date: Dayjs | null
}
