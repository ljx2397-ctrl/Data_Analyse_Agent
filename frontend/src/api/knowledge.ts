import { http } from './index'
import type { KnowledgeItem, KnowledgePayload } from '../types'

export const knowledgeApi = {
  list: () => http.get<KnowledgeItem[]>('/knowledge').then((r) => r.data),

  get: (kid: string) =>
    http.get<KnowledgeItem>(`/knowledge/${kid}`).then((r) => r.data),

  create: (payload: KnowledgePayload) =>
    http.post<KnowledgeItem>('/knowledge', payload).then((r) => r.data),

  update: (kid: string, payload: Partial<KnowledgePayload>) =>
    http.put<KnowledgeItem>(`/knowledge/${kid}`, payload).then((r) => r.data),

  remove: (kid: string) =>
    http.delete(`/knowledge/${kid}`).then((r) => r.data),

  search: (query: string, top_k?: number, category?: string) =>
    http.post<KnowledgeItem[]>('/knowledge/search', { query, top_k, category }).then((r) => r.data),

  upload: (file: File, category: string) => {
    const form = new FormData()
    form.append('file', file)
    form.append('category', category)
    return http.post<KnowledgeItem[]>('/knowledge/upload', form).then((r) => r.data)
  },

  count: () =>
    http.get<{ count: number }>('/knowledge/stats/count').then((r) => r.data),

  getMode: () =>
    http.get<{ mode: string }>('/knowledge/search-mode').then((r) => r.data),

  setMode: (mode: string) =>
    http.put<{ mode: string }>('/knowledge/search-mode', { mode }).then((r) => r.data),
}
