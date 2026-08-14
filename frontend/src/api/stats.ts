import { http } from './index'
import type { SessionUsage, TraceEvent } from '../types'

export const statsApi = {
  getUsage: (sid: string) =>
    http.get<SessionUsage>(`/sessions/${sid}/usage`).then((r) => r.data),

  getTrace: (sid: string, limit = 200) =>
    http
      .get<{ events: TraceEvent[] }>(`/sessions/${sid}/trace`, { params: { limit } })
      .then((r) => r.data.events),
}
