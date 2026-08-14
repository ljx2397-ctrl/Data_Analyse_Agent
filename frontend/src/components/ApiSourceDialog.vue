<template>
  <el-dialog
    v-model="visible"
    width="560px"
    append-to-body
    @open="onOpen"
    @closed="onClosed"
    class="api-source-dialog"
  >
    <template #header>
      <div class="api-title">
        <span class="api-title-icon">🔗</span>
        <span>连接自定义 API</span>
      </div>
    </template>

    <div v-loading="loading" class="api-body">
      <!-- 已保存的连接记录列表（始终显示） -->
      <div v-if="savedOptions.length > 0" class="saved-list">
        <div class="saved-list-title">已保存配置（{{ savedOptions.length }}）</div>
        <div
          v-for="p in savedOptions"
          :key="p.id"
          class="saved-item"
          :class="{ active: p.id === activeProfileId }"
          @click="onPick(p.id)"
        >
          <div class="saved-item-main">
            <span class="saved-item-name">{{ p.name || '未命名' }}</span>
            <span class="saved-item-url">{{ shortUrl(p.url) }}</span>
            <span class="saved-item-time" v-if="p.last_connected_at">
              最近连接：{{ formatTime(p.last_connected_at) }}
            </span>
            <span class="saved-item-time" v-else>未连接过</span>
          </div>
          <div class="saved-item-actions" @click.stop>
            <el-button
              size="small"
              type="primary"
              plain
              :loading="connectingId === p.id"
              @click="connectById(p.id)"
            >连接</el-button>
            <el-button
              size="small"
              type="danger"
              plain
              @click="removeById(p.id)"
            >删除</el-button>
          </div>
        </div>
      </div>
      <div v-else class="saved-list-empty">
        暂无保存的配置，填写下方信息后点「保存配置」
      </div>

      <el-form label-position="top" class="api-form" @submit.prevent="onConnect">
        <el-form-item label="显示名称（可选）">
          <el-input
            v-model="form.name"
            placeholder="例如：业务数据 API"
            clearable
          />
        </el-form-item>

        <el-form-item label="API URL" required>
          <el-input
            v-model="form.url"
            placeholder="请输入数据接口地址"
            clearable
          />
        </el-form-item>

        <el-form-item label="认证方式">
          <el-select v-model="form.auth_type" class="auth-select" @change="onAuthChange">
            <el-option label="无认证" value="none" />
            <el-option label="Bearer Token" value="bearer" />
            <el-option label="API Key (Header)" value="api_key_header" />
          </el-select>
        </el-form-item>

        <el-form-item v-if="form.auth_type !== 'none'" label="Token">
          <el-input
            v-model="form.auth_token"
            type="password"
            show-password
            :placeholder="authTokenPlaceholder"
            clearable
          />
        </el-form-item>
      </el-form>

      <!-- 预览区：深色背景，字段 + 样本行 -->
      <div
        class="preview-box"
        :class="{ empty: !preview && !previewing }"
        v-loading="previewing"
        element-loading-text="正在预览..."
      >
        <pre v-if="preview?.preview_md" class="preview-md">{{ preview.preview_md }}</pre>
        <div v-else-if="!previewing" class="preview-empty">
          填写 URL 并认证后，此处会显示数据结构预览
        </div>
      </div>
      <div v-if="preview && preview.rows > 0" class="preview-meta">
        共 {{ preview.rows.toLocaleString() }} 行 · {{ preview.cols }} 列
      </div>
    </div>

    <template #footer>
      <el-button @click="visible = false">取消</el-button>
      <el-button plain @click="newProfile">➕ 新建</el-button>
      <el-button
        type="info"
        plain
        :loading="saving"
        :disabled="!form.url.trim()"
        @click="onSave"
      >💾 保存配置</el-button>
      <el-button
        type="primary"
        :loading="connecting"
        :disabled="!canConnect"
        @click="onConnect"
      >连接</el-button>
    </template>
  </el-dialog>
</template>

<script setup lang="ts">
import { ref, computed, watch } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import { apiSourceApi } from '../api/apiSource'
import type {
  ApiSourceProfile,
  ApiSourceSavePayload,
  ApiPreviewResult,
  ApiFetchResult,
} from '../types'

const visible = defineModel<boolean>('visible', { default: false })
const props = withDefaults(defineProps<{ sid: string | null; isNew?: boolean }>(), { isNew: false })
const emit = defineEmits<{
  (e: 'fetched', result: ApiFetchResult): void
  (e: 'closed'): void
}>()

const loading = ref(false)
const previewing = ref(false)
const connecting = ref(false)
const saving = ref(false)
const connectingId = ref<string | null>(null)

const savedOptions = ref<ApiSourceProfile[]>([])
const pickedId = ref<string | null>(null)

const form = ref({
  name: '',
  url: '',
  auth_type: 'none',
  auth_token: '',
  // 辅助：已选配置时保存的脱敏 token，用于 placeholder 显示
  authTokenMasked: '',
})
const preview = ref<ApiPreviewResult | null>(null)
// 当前"已保存"的 profile id，用于 preview / fetch（新建未保存时为 null）
const activeProfileId = ref<string | null>(null)

const authTokenPlaceholder = computed(() =>
  form.value.authTokenMasked
    ? `当前已配置（${form.value.authTokenMasked}），留空不修改`
    : '请输入 Token',
)

const canConnect = computed(() =>
  !!form.value.url.trim() && !!props.sid,
)

function shortUrl(url: string): string {
  if (!url) return ''
  try {
    const u = new URL(url)
    return u.host + (u.pathname.length > 1 ? u.pathname.slice(0, 20) : '')
  } catch {
    return url.length > 30 ? url.slice(0, 30) + '…' : url
  }
}

function formatTime(iso: string): string {
  if (!iso) return ''
  try {
    const d = new Date(iso)
    const now = new Date()
    const diff = (now.getTime() - d.getTime()) / 1000
    if (diff < 60) return '刚刚'
    if (diff < 3600) return `${Math.floor(diff / 60)} 分钟前`
    if (diff < 86400) return `${Math.floor(diff / 3600)} 小时前`
    if (diff < 86400 * 7) return `${Math.floor(diff / 86400)} 天前`
    return d.toLocaleString('zh-CN', { month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit' })
  } catch {
    return iso
  }
}

let previewTimer: ReturnType<typeof setTimeout> | null = null

async function onOpen() {
  loading.value = true
  try {
    savedOptions.value = await apiSourceApi.listProfiles()
    // 默认选中 active 的
    const active = savedOptions.value.find((p) => p.active)
    if (active) {
      applyProfile(active)
    } else if (savedOptions.value.length > 0) {
      applyProfile(savedOptions.value[0])
    }
  } catch (e: any) {
    ElMessage.error('加载配置失败：' + (e?.message || e))
  } finally {
    loading.value = false
  }
}

function applyProfile(p: ApiSourceProfile) {
  pickedId.value = p.id
  activeProfileId.value = p.id
  form.value.name = p.name
  form.value.url = p.url
  form.value.auth_type = p.auth_type
  form.value.auth_token = ''
  form.value.authTokenMasked = p.auth_token_masked
  // 切换到已有配置后自动预览
  void doPreview()
}

function onPick(id: string | null) {
  if (!id) return
  const p = savedOptions.value.find((x) => x.id === id)
  if (p) applyProfile(p)
}

function onAuthChange() {
  if (form.value.auth_type === 'none') {
    form.value.auth_token = ''
    form.value.authTokenMasked = ''
  }
}

// 监听 URL 变化（用户输入时清空旧预览）
watch(
  () => [form.value.url, form.value.auth_type, form.value.auth_token],
  () => {
    if (!visible.value) return
    preview.value = null
  },
)

/**
 * 确保当前表单内容已保存为 profile（必要时新建/更新）。
 * 返回 profile_id。
 */
async function ensureSaved(): Promise<string> {
  const payload: ApiSourceSavePayload = {
    id: activeProfileId.value || null,
    name: form.value.name,
    url: form.value.url,
    auth_type: form.value.auth_type,
    activate: true,
  }
  const token = form.value.auth_token.trim()
  if (token && !token.includes('*')) {
    payload.auth_token = token
  }
  const saved = await apiSourceApi.saveProfile(payload)
  activeProfileId.value = saved.id
  // 刷新 savedOptions
  savedOptions.value = savedOptions.value.map((x) => (x.id === saved.id ? saved : x))
  if (!savedOptions.value.find((x) => x.id === saved.id)) {
    savedOptions.value.push(saved)
  }
  form.value.authTokenMasked = saved.auth_token_masked
  if (form.value.auth_token && !form.value.auth_token.includes('*')) {
    form.value.auth_token = ''
  }
  return saved.id
}

/** 保存配置（不连接） */
async function onSave() {
  if (!form.value.url.trim()) {
    ElMessage.warning('请填写 API URL')
    return
  }
  saving.value = true
  try {
    await ensureSaved()
    ElMessage.success('配置已保存')
    // 保存后自动预览
    await doPreview()
  } catch (e: any) {
    ElMessage.error('保存失败：' + (e?.response?.data?.detail || e?.message || e))
  } finally {
    saving.value = false
  }
}

/** 新建配置：清空表单 */
function newProfile() {
  activeProfileId.value = null
  pickedId.value = null
  form.value = {
    name: '',
    url: '',
    auth_type: 'none',
    auth_token: '',
    authTokenMasked: '',
  }
  preview.value = null
}

async function doPreview() {
  if (!activeProfileId.value) return
  previewing.value = true
  try {
    preview.value = await apiSourceApi.previewProfile(activeProfileId.value)
  } catch (e: any) {
    ElMessage.error('预览失败：' + (e?.response?.data?.detail || e?.message || e))
    preview.value = null
  } finally {
    previewing.value = false
  }
}

async function onConnect() {
  if (!form.value.url.trim()) {
    ElMessage.warning('请填写 API URL')
    return
  }
  if (!props.sid) {
    ElMessage.warning('请先创建会话')
    return
  }
  connecting.value = true
  try {
    const pid = activeProfileId.value || (await ensureSaved())
    const result = await apiSourceApi.fetchToSession(pid, props.sid)
    ElMessage.success(`连接成功：${result.rows.toLocaleString()} 行 × ${result.cols} 列`)
    emit('fetched', result)
    // 刷新连接记录的 last_connected_at
    try {
      savedOptions.value = await apiSourceApi.listProfiles()
    } catch { /* 静默 */ }
    visible.value = false
  } catch (e: any) {
    ElMessage.error('连接失败：' + (e?.response?.data?.detail || e?.message || e))
  } finally {
    connecting.value = false
  }
}

/** 从记录列表直接连接某个已保存的配置 */
async function connectById(id: string) {
  if (!props.sid) {
    ElMessage.warning('请先创建会话')
    return
  }
  connectingId.value = id
  try {
    const result = await apiSourceApi.fetchToSession(id, props.sid)
    ElMessage.success(`连接成功：${result.rows.toLocaleString()} 行 × ${result.cols} 列`)
    emit('fetched', result)
    try {
      savedOptions.value = await apiSourceApi.listProfiles()
    } catch { /* 静默 */ }
    visible.value = false
  } catch (e: any) {
    ElMessage.error('连接失败：' + (e?.response?.data?.detail || e?.message || e))
  } finally {
    connectingId.value = null
  }
}

/** 删除某条已保存的配置 */
async function removeById(id: string) {
  try {
    await ElMessageBox.confirm('确定删除该配置？', '提示', { type: 'warning' })
  } catch {
    return
  }
  try {
    await apiSourceApi.deleteProfile(id)
    ElMessage.success('已删除')
    savedOptions.value = savedOptions.value.filter((p) => p.id !== id)
    if (activeProfileId.value === id) {
      newProfile()
    }
  } catch (e: any) {
    ElMessage.error('删除失败：' + (e?.response?.data?.detail || e?.message || e))
  }
}

function onClosed() {
  reset()
  emit('closed')
}

function reset() {
  pickedId.value = null
  activeProfileId.value = null
  preview.value = null
  form.value = {
    name: '',
    url: '',
    auth_type: 'none',
    auth_token: '',
    authTokenMasked: '',
  }
}
</script>

<style scoped>
.api-body {
  padding: 0 4px;
}
.api-title {
  display: flex;
  align-items: center;
  gap: 6px;
  font-size: 16px;
  font-weight: 600;
}
.api-title-icon {
  font-size: 18px;
}
.configured-url {
  color: #23c06a;
  font-size: 13px;
  margin-bottom: 12px;
  padding: 6px 10px;
  background: rgba(35, 192, 106, 0.08);
  border-radius: 6px;
  word-break: break-all;
  display: flex;
  justify-content: space-between;
  align-items: center;
  gap: 8px;
}
.choose-another {
  flex-shrink: 0;
  font-size: 12px;
}
.saved-list {
  margin-bottom: 14px;
  max-height: 180px;
  overflow-y: auto;
  border: 1px solid var(--c-border-light);
  border-radius: 8px;
  padding: 6px;
}
.saved-list-title {
  font-size: 12px;
  color: var(--c-text-placeholder);
  padding: 2px 6px 6px;
  font-weight: 600;
}
.saved-item {
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 8px 10px;
  border-radius: 6px;
  cursor: pointer;
  margin-bottom: 2px;
  transition: background 0.15s;
  border: 1px solid transparent;
  gap: 8px;
}
.saved-item:hover {
  background: var(--c-bg-hover);
}
.saved-item.active {
  background: var(--c-bg-active);
  border-color: var(--c-primary-border);
}
.saved-item-main {
  display: flex;
  flex-direction: column;
  gap: 2px;
  overflow: hidden;
  flex: 1;
}
.saved-item-actions {
  display: flex;
  gap: 4px;
  flex-shrink: 0;
}
.saved-item-name {
  font-size: 13px;
  font-weight: 600;
  color: var(--c-text);
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}
.saved-item-url {
  font-size: 11px;
  color: var(--c-text-placeholder);
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}
.saved-item-time {
  font-size: 11px;
  color: var(--c-text-placeholder);
}
.saved-list-empty {
  text-align: center;
  color: var(--c-text-placeholder);
  font-size: 12px;
  padding: 16px 0;
  margin-bottom: 14px;
}
.api-form :deep(.el-form-item) {
  margin-bottom: 14px;
}
.auth-select {
  width: 100%;
}

/* 预览区：深色，模拟截图里的终端风格 */
.preview-box {
  background: #1e293b;
  color: #e2e8f0;
  border-radius: 8px;
  padding: 12px 14px;
  max-height: 240px;
  overflow: auto;
  margin-top: 8px;
  border: 1px solid #334155;
  font-family: 'Consolas', 'Monaco', 'Source Code Pro', monospace;
}
.preview-box.empty {
  background: #111827;
  border: 1px dashed #334155;
}
.preview-md {
  margin: 0;
  font-size: 12px;
  line-height: 1.7;
  white-space: pre;
  color: #cbd5e1;
}
.preview-empty {
  color: #64748b;
  font-size: 12px;
  text-align: center;
  padding: 16px 0;
  font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif;
}
.preview-meta {
  font-size: 12px;
  color: var(--c-text-placeholder);
  text-align: right;
  margin-top: 4px;
}

/* 深色模式下预览区保持深灰，和浅色下的深蓝区分就够 */
:global(html.dark) .preview-box {
  background: #0f172a;
  border-color: #1e293b;
}
</style>
