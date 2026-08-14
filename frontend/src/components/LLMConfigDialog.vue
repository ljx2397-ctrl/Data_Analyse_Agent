<template>
  <el-dialog
    v-model="visible"
    title="大模型配置"
    width="680px"
    append-to-body
    @open="onOpen"
    @closed="reset"
  >
    <div v-loading="loading" class="llm-config-body">
      <div class="llm-layout">
        <!-- 左侧：已保存档案列表 -->
        <div class="llm-sidebar">
          <div class="llm-sidebar-title">
            <span>已保存配置</span>
            <el-button size="small" type="primary" plain @click="newProfile">➕</el-button>
          </div>
          <div class="profile-list">
            <div
              v-for="p in profiles"
              :key="p.id"
              class="profile-item"
              :class="{ active: p.active, editing: editingId === p.id }"
              @click="selectProfile(p)"
            >
              <div class="profile-item-main">
                <span class="profile-name">{{ p.name || '未命名' }}</span>
                <span class="profile-model">{{ p.model }}</span>
              </div>
              <el-tag v-if="p.active" size="small" type="success" effect="plain">使用中</el-tag>
            </div>
            <div v-if="profiles.length === 0" class="profile-empty">
              暂无保存的配置<br/>点击 ➕ 新建
            </div>
          </div>
        </div>

        <!-- 右侧：编辑表单 -->
        <div class="llm-form-area">
          <div v-if="formVisible" class="form-header">
            <span class="form-title">{{ editingId ? '编辑配置' : '新建配置' }}</span>
            <div class="form-actions" v-if="editingId">
              <el-button
                size="small"
                type="success"
                plain
                :disabled="isCurrentActive"
                @click="activate"
              >设为当前</el-button>
              <el-button size="small" type="danger" plain @click="remove">删除</el-button>
            </div>
          </div>

          <el-form v-if="formVisible" label-position="top" class="llm-form">
            <el-form-item label="配置名称">
              <el-input v-model="form.name" placeholder="如 通义千问 / DeepSeek" clearable />
            </el-form-item>

            <el-form-item label="模型名称">
              <el-input
                v-model="form.model"
                placeholder="如 qwen-plus / gpt-4o / deepseek-chat"
                clearable
              />
            </el-form-item>

            <el-form-item label="API 地址 (Base URL)">
              <el-input
                v-model="form.api_base"
                placeholder="如 https://dashscope.aliyuncs.com/compatible-mode/v1"
                clearable
              />
            </el-form-item>

            <el-form-item label="API Key">
              <el-input
                v-model="form.api_key"
                type="password"
                show-password
                :placeholder="apiKeyPlaceholder"
                clearable
              />
              <div class="form-hint" v-if="form.apiKeyMasked">
                当前 Key：{{ form.apiKeyMasked }}（留空不修改）
              </div>
            </el-form-item>

            <el-form-item label="温度 (Temperature)">
              <el-slider
                v-model="form.temperature"
                :min="0"
                :max="2"
                :step="0.1"
                show-input
                style="padding-right: 8px"
              />
            </el-form-item>
          </el-form>

          <!-- 预设快捷导入 -->
          <div v-if="formVisible" class="preset-section">
            <div class="preset-title">快捷导入预设</div>
            <div class="preset-list">
              <button
                v-for="p in presets"
                :key="p.name"
                class="preset-btn"
                @click="applyPreset(p)"
              >
                {{ p.name }}
              </button>
            </div>
          </div>

          <!-- 空状态提示 -->
          <div v-else class="form-placeholder">
            <div class="emoji">🤖</div>
            <p>选择左侧配置查看详情，或点击 ➕ 新建</p>
          </div>
        </div>
      </div>
    </div>

    <template #footer>
      <el-button @click="visible = false">关闭</el-button>
      <el-button
        v-if="formVisible"
        type="primary"
        :loading="saving"
        @click="save"
      >保存配置</el-button>
    </template>
  </el-dialog>
</template>

<script setup lang="ts">
import { ref, computed } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import { configApi } from '../api/config'
import type { LLMProfile, LLMProfileSavePayload } from '../types'

const visible = defineModel<boolean>('visible', { default: false })
const emit = defineEmits<{ (e: 'saved', cfg: LLMProfile): void }>()

const loading = ref(false)
const saving = ref(false)

const profiles = ref<LLMProfile[]>([])
const editingId = ref<string | null>(null)
const formVisible = ref(false)

const form = ref({
  name: '',
  model: '',
  api_base: '',
  api_key: '',
  temperature: 0,
  apiKeyMasked: '',
})

const apiKeyPlaceholder = computed(() =>
  form.value.apiKeyMasked
    ? `当前已配置（${form.value.apiKeyMasked}），留空不修改`
    : '请输入 API Key',
)

const isCurrentActive = computed(() => {
  if (!editingId.value) return false
  const p = profiles.value.find((x) => x.id === editingId.value)
  return !!p?.active
})

// 常见服务商预设
const presets = [
  { name: '通义千问', model: 'qwen-plus', api_base: 'https://dashscope.aliyuncs.com/compatible-mode/v1' },
  { name: 'DeepSeek', model: 'deepseek-chat', api_base: 'https://api.deepseek.com/v1' },
  { name: 'OpenAI', model: 'gpt-4o', api_base: 'https://api.openai.com/v1' },
  { name: '智谱 GLM', model: 'glm-4-plus', api_base: 'https://open.bigmodel.cn/api/paas/v4' },
  { name: 'Moonshot', model: 'moonshot-v1-8k', api_base: 'https://api.moonshot.cn/v1' },
]

async function onOpen() {
  await loadProfiles()
}

async function loadProfiles() {
  loading.value = true
  try {
    profiles.value = await configApi.listProfiles()
    // 若无档案，自动进入新建模式
    if (profiles.value.length === 0) {
      newProfile()
    } else if (!editingId.value) {
      // 默认选中激活的档案
      const active = profiles.value.find((p) => p.active)
      selectProfile(active || profiles.value[0])
    } else {
      // 刷新后保持选中，更新脱敏 key
      const cur = profiles.value.find((p) => p.id === editingId.value)
      if (cur) {
        form.value.apiKeyMasked = cur.api_key_masked
      }
    }
  } catch (e: any) {
    ElMessage.error('加载配置失败：' + (e?.message || e))
  } finally {
    loading.value = false
  }
}

function selectProfile(p: LLMProfile) {
  editingId.value = p.id
  form.value.name = p.name
  form.value.model = p.model
  form.value.api_base = p.api_base
  form.value.api_key = ''
  form.value.temperature = p.temperature
  form.value.apiKeyMasked = p.api_key_masked
  formVisible.value = true
}

function newProfile() {
  editingId.value = null
  form.value.name = ''
  form.value.model = ''
  form.value.api_base = ''
  form.value.api_key = ''
  form.value.temperature = 0
  form.value.apiKeyMasked = ''
  formVisible.value = true
}

function applyPreset(p: { name: string; model: string; api_base: string }) {
  form.value.model = p.model
  form.value.api_base = p.api_base
  if (!form.value.name) {
    form.value.name = p.name
  }
  ElMessage.success(`已应用 ${p.name} 预设，请补充 API Key`)
}

async function save() {
  if (!form.value.model.trim()) {
    ElMessage.warning('请填写模型名称')
    return
  }
  if (!form.value.api_base.trim()) {
    ElMessage.warning('请填写 API 地址')
    return
  }
  saving.value = true
  try {
    const key = form.value.api_key.trim()
    const payload: LLMProfileSavePayload = {
      id: editingId.value || null,
      name: form.value.name,
      model: form.value.model,
      api_base: form.value.api_base,
      temperature: form.value.temperature,
      activate: true, // 保存即激活
    }
    if (key && !key.includes('*')) {
      payload.api_key = key
    }
    const saved = await configApi.saveProfile(payload)
    ElMessage.success(editingId.value ? '配置已更新并设为当前使用' : '新配置已保存并设为当前使用')
    emit('saved', saved)
    await loadProfiles()
  } catch (e: any) {
    ElMessage.error('保存失败：' + (e?.response?.data?.detail || e?.message || e))
  } finally {
    saving.value = false
  }
}

async function activate() {
  if (!editingId.value) return
  try {
    const p = await configApi.activateProfile(editingId.value)
    ElMessage.success(`已切换到「${p.name}」`)
    emit('saved', p)
    await loadProfiles()
  } catch (e: any) {
    ElMessage.error('切换失败：' + (e?.response?.data?.detail || e?.message || e))
  }
}

async function remove() {
  if (!editingId.value) return
  try {
    await ElMessageBox.confirm('确定删除该配置档案？', '提示', { type: 'warning' })
  } catch {
    return
  }
  try {
    await configApi.deleteProfile(editingId.value)
    ElMessage.success('已删除')
    editingId.value = null
    formVisible.value = false
    await loadProfiles()
    // 通知父组件刷新当前模型
    const active = profiles.value.find((p) => p.active)
    if (active) emit('saved', active)
  } catch (e: any) {
    ElMessage.error('删除失败：' + (e?.response?.data?.detail || e?.message || e))
  }
}

function reset() {
  editingId.value = null
  formVisible.value = false
  form.value = {
    name: '',
    model: '',
    api_base: '',
    api_key: '',
    temperature: 0,
    apiKeyMasked: '',
  }
}
</script>

<style scoped>
.llm-config-body {
  min-height: 360px;
}
.llm-layout {
  display: flex;
  gap: 16px;
  height: 380px;
}
/* 左侧档案列表 */
.llm-sidebar {
  width: 200px;
  flex-shrink: 0;
  display: flex;
  flex-direction: column;
  border: 1px solid var(--c-border-light);
  border-radius: 8px;
  overflow: hidden;
}
.llm-sidebar-title {
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 8px 10px;
  font-size: 13px;
  font-weight: 600;
  color: var(--c-text-secondary);
  background: var(--c-bg-inset);
  border-bottom: 1px solid var(--c-border-light);
}
.profile-list {
  flex: 1;
  overflow-y: auto;
  padding: 6px;
}
.profile-item {
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 8px 10px;
  border-radius: 6px;
  cursor: pointer;
  margin-bottom: 4px;
  transition: background 0.15s;
  border: 1px solid transparent;
}
.profile-item:hover {
  background: var(--c-bg-hover);
}
.profile-item.active {
  background: var(--c-bg-active);
  border-color: var(--c-primary-border);
}
.profile-item.editing {
  border-color: var(--c-primary);
}
.profile-item-main {
  display: flex;
  flex-direction: column;
  gap: 2px;
  overflow: hidden;
}
.profile-name {
  font-size: 13px;
  font-weight: 600;
  color: var(--c-text);
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}
.profile-model {
  font-size: 11px;
  color: var(--c-text-placeholder);
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}
.profile-empty {
  text-align: center;
  color: var(--c-text-placeholder);
  font-size: 12px;
  padding: 24px 8px;
  line-height: 1.8;
}
/* 右侧表单区 */
.llm-form-area {
  flex: 1;
  display: flex;
  flex-direction: column;
  overflow-y: auto;
}
.form-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  margin-bottom: 12px;
}
.form-title {
  font-size: 14px;
  font-weight: 600;
  color: var(--c-text);
}
.form-actions {
  display: flex;
  gap: 6px;
}
.llm-form .form-hint {
  font-size: 12px;
  color: var(--c-text-placeholder);
  margin-top: 4px;
}
.preset-section {
  margin-top: 8px;
  padding-top: 12px;
  border-top: 1px solid var(--c-border-light);
}
.preset-title {
  font-size: 13px;
  color: var(--c-text-secondary);
  margin-bottom: 8px;
  font-weight: 600;
}
.preset-list {
  display: flex;
  flex-wrap: wrap;
  gap: 8px;
}
.preset-btn {
  padding: 5px 12px;
  border: 1px solid var(--c-border-lighter);
  border-radius: 16px;
  background: var(--c-bg-inset);
  color: var(--c-text-secondary);
  font-size: 12px;
  cursor: pointer;
  transition: all 0.15s;
}
.preset-btn:hover {
  border-color: var(--c-primary);
  color: var(--c-primary);
  background: var(--c-bg-active);
}
/* 空状态 */
.form-placeholder {
  flex: 1;
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  color: var(--c-text-placeholder);
  gap: 8px;
}
.form-placeholder .emoji {
  font-size: 40px;
}
</style>
