import { http } from './index'
import type { SkillItem, SkillSavePayload } from '../types'

export const skillsApi = {
  list: () => http.get<SkillItem[]>('/skills').then((r) => r.data),

  get: (sid: string) => http.get<SkillItem>(`/skills/${sid}`).then((r) => r.data),

  save: (payload: SkillSavePayload) =>
    http.post<SkillItem>('/skills', payload).then((r) => r.data),

  toggle: (sid: string) =>
    http.post<SkillItem>(`/skills/${sid}/toggle`).then((r) => r.data),

  reset: (sid: string) =>
    http.post<SkillItem>(`/skills/${sid}/reset`).then((r) => r.data),

  remove: (sid: string) =>
    http.delete<{ ok: boolean; id: string }>(`/skills/${sid}`).then((r) => r.data),
}
