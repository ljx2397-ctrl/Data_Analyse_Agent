import { http } from './index'
import type { DataOverview, DatasetInfo, TableOverview } from '../types'

export interface UploadDataResp {
  kind: string
  name: string
  sheets: string[] | null
}

export interface ConfirmDataResp {
  overview: DataOverview
  datasets: DatasetInfo[]
  table_overviews: TableOverview[]
}

export const uploadApi = {
  uploadData: (sid: string, file: File) => {
    const fd = new FormData()
    fd.append('file', file)
    return http
      .post<UploadDataResp>(`/sessions/${sid}/upload-data`, fd, {
        headers: { 'Content-Type': 'multipart/form-data' },
      })
      .then((r) => r.data)
  },

  parseHeader: (sid: string, file: File) => {
    const fd = new FormData()
    fd.append('file', file)
    return http
      .post<{ columns: string[] }>(`/sessions/${sid}/parse-header`, fd, {
        headers: { 'Content-Type': 'multipart/form-data' },
      })
      .then((r) => r.data)
  },

  confirmData: (sid: string, payload: { sheet?: string | null; columns?: string[] | null }) =>
    http
      .post<ConfirmDataResp>(`/sessions/${sid}/confirm-data`, payload)
      .then((r) => r.data),

  removeDataset: (sid: string, dsId: string) =>
    http
      .delete<ConfirmDataResp>(`/sessions/${sid}/datasets/${dsId}`)
      .then((r) => r.data),
}
