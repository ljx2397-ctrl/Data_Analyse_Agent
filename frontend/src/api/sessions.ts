import { http } from './index'
import type { SessionOut, SessionDetail } from '../types'

export const sessionsApi = {
  list: () => http.get<SessionOut[]>('/sessions').then((r) => r.data),

  create: () => http.post<SessionOut>('/sessions').then((r) => r.data),

  get: (sid: string) =>
    http.get<SessionDetail>(`/sessions/${sid}`).then((r) => r.data),

  remove: (sid: string) => http.delete(`/sessions/${sid}`).then((r) => r.data),

  rename: (sid: string, title: string) =>
    http.put(`/sessions/${sid}/title`, null, { params: { title } }).then((r) => r.data),

  switch: (sid: string) =>
    http.post(`/sessions/${sid}/switch`).then((r) => r.data),

  // 删除单条消息（按索引）
  deleteMessage: (sid: string, index: number) =>
    http.delete(`/sessions/${sid}/messages/${index}`).then((r) => r.data),

  // 截断消息：删除 fromIndex 及之后所有（编辑重发用）
  truncateMessages: (sid: string, fromIndex: number) =>
    http.delete(`/sessions/${sid}/messages`, {
      params: { from_index: fromIndex },
    }).then((r) => r.data),
}
