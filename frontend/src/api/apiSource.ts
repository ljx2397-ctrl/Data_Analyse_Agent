import { http } from './index'
import type {
  ApiSourceProfile,
  ApiSourceSavePayload,
  ApiPreviewResult,
  ApiFetchResult,
} from '../types'

export const apiSourceApi = {
  listProfiles(): Promise<ApiSourceProfile[]> {
    return http.get('/api-source/profiles').then((r) => r.data)
  },

  saveProfile(payload: ApiSourceSavePayload): Promise<ApiSourceProfile> {
    return http.post('/api-source/profiles', payload).then((r) => r.data)
  },

  activateProfile(profileId: string): Promise<ApiSourceProfile> {
    return http.post(`/api-source/profiles/${profileId}/activate`).then((r) => r.data)
  },

  deleteProfile(profileId: string): Promise<{ remaining: number; active_id: string | null }> {
    return http.delete(`/api-source/profiles/${profileId}`).then((r) => r.data)
  },

  previewProfile(profileId: string): Promise<ApiPreviewResult> {
    return http.post(`/api-source/profiles/${profileId}/preview`).then((r) => r.data)
  },

  fetchToSession(profileId: string, sid: string): Promise<ApiFetchResult> {
    return http
      .post(`/api-source/profiles/${profileId}/fetch`, null, { params: { sid } })
      .then((r) => r.data)
  },
}
