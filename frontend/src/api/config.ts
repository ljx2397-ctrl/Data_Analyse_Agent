import { http } from './index'
import type { LLMConfig, LLMConfigUpdate, LLMProfile, LLMProfileSavePayload } from '../types'

export const configApi = {
  /** 获取当前生效的 LLM 配置（api_key 脱敏） */
  getLLMConfig(): Promise<LLMConfig> {
    return http.get('/config/llm').then((r) => r.data)
  },

  /** 直接更新当前生效配置并热生效 */
  updateLLMConfig(payload: LLMConfigUpdate): Promise<LLMConfig> {
    return http.put('/config/llm', payload).then((r) => r.data)
  },

  /** 列出所有已保存的配置档案 */
  listProfiles(): Promise<LLMProfile[]> {
    return http.get('/config/llm/profiles').then((r) => r.data)
  },

  /** 新增或更新配置档案 */
  saveProfile(payload: LLMProfileSavePayload): Promise<LLMProfile> {
    return http.post('/config/llm/profiles', payload).then((r) => r.data)
  },

  /** 切换激活的配置档案 */
  activateProfile(profileId: string): Promise<LLMProfile> {
    return http.post(`/config/llm/profiles/${profileId}/activate`).then((r) => r.data)
  },

  /** 删除配置档案 */
  deleteProfile(profileId: string): Promise<{ remaining: number; active_id: string | null }> {
    return http.delete(`/config/llm/profiles/${profileId}`).then((r) => r.data)
  },
}
