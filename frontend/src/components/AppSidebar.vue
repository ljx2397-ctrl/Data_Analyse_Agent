<template>
  <aside class="app-sidebar">
    <div class="sidebar-header">
      <div class="sidebar-header-row">
        <h2>🏦 数据分析 Agent</h2>
        <div class="sidebar-header-actions">
          <button
            class="sidebar-collapse-btn"
            title="收起侧边栏"
            @click="emit('collapse')"
          >
            <svg viewBox="0 0 24 24" width="16" height="16" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round">
              <rect x="3" y="3" width="18" height="18" rx="2"/>
              <path d="M15 3v18"/>
              <path d="m10 9-3 3 3 3"/>
            </svg>
          </button>
          <button
            class="theme-toggle"
            :title="isDark ? '切换到浅色模式' : '切换到深色模式'"
            @click="toggleTheme"
          >
            {{ isDark ? '☀️' : '🌙' }}
          </button>
        </div>
      </div>
      <div class="caption">
        <template v-if="store.overview && store.overview.rows > 0">
          样本量：{{ store.overview.rows.toLocaleString() }} 条 | 字段数：{{ store.overview.cols }} 个
          <span v-if="store.multiTable" style="margin-left: 6px; color: var(--c-primary)">
            · {{ store.datasets.length }} 张表
          </span>
        </template>
        <template v-else-if="store.overview">
          字段数：{{ store.overview.cols }} 个
        </template>
        <template v-else>未加载数据</template>
      </div>
    </div>

    <div class="sidebar-section">
      <el-button
        type="primary"
        plain
        style="width: 100%"
        :loading="creating"
        @click="createNew"
      >
        ➕ 新建对话
      </el-button>
      <el-button plain style="width: 100%; margin-top: 8px; margin-left: 0" @click="emit('open-knowledge')">
        📚 业务知识库
      </el-button>
      <el-button plain style="width: 100%; margin-top: 8px; margin-left: 0" @click="llmDialogVisible = true">
        🤖 大模型配置<span class="llm-model-tag" :class="{ unconfigured: !llmModel }">{{ llmModel || '未配置' }}</span>
      </el-button>
      <el-button plain style="width: 100%; margin-top: 8px; margin-left: 0" @click="skillDialogVisible = true">
        🛠️ 技能管理
      </el-button>
    </div>

    <div class="sidebar-section sidebar-body">
      <el-collapse v-model="activeNames" class="sidebar-collapse">
        <!-- 历史会话 -->
        <el-collapse-item name="sessions">
          <template #title>
            <div class="collapse-title">
              <span>📋 历史会话</span>
              <span class="collapse-count">{{ store.sessions.length }}</span>
            </div>
          </template>
          <div class="session-list">
            <div
              v-for="s in store.sessions"
              :key="s.sid"
              class="session-item"
              :class="{ active: s.sid === store.currentSid }"
              @click="selectSession(s.sid)"
            >
              <span class="title">{{ s.sid === store.currentSid ? '▶️' : '💬' }} {{ s.title }}</span>
              <span class="session-badges">
                <span
                  v-if="store.streamingSids[s.sid]"
                  class="gen-dot"
                  title="正在生成回复"
                ></span>
                <span
                  v-else-if="store.unreadReplies[s.sid]"
                  class="unread-dot"
                  title="有新回复"
                ></span>
                <span class="del-btn" @click.stop="del(s.sid)" title="删除">🗑️</span>
              </span>
            </div>
            <div v-if="store.sessions.length === 0" class="caption empty-tip">
              暂无会话
            </div>
          </div>
        </el-collapse-item>

        <!-- 字段参考 -->
        <el-collapse-item name="fields" v-if="hasFieldRef">
          <template #title>
            <div class="collapse-title">
              <span>🗂️ 字段参考</span>
              <span class="collapse-count">{{ fieldGroups.length }}</span>
            </div>
          </template>
          <div class="field-ref-wrap">
            <div
              v-for="(g, i) in fieldGroups"
              :key="g.key"
              class="field-ref-group"
            >
              <div class="field-ref-title">
                <span class="field-ref-name">{{ g.name }}</span>
                <el-tag v-if="g.primary" size="small" type="primary" effect="plain">主表</el-tag>
              </div>
              <div class="field-ref-meta">{{ g.rows.toLocaleString() }} 行 · {{ g.cols }} 列</div>
              <div class="field-ref">{{ g.columns.join('\n') }}</div>
            </div>
          </div>
        </el-collapse-item>
      </el-collapse>
    </div>

    <LLMConfigDialog v-model:visible="llmDialogVisible" @saved="onLLMSaved" />
    <SkillDialog v-model:visible="skillDialogVisible" @changed="onSkillsChanged" />
  </aside>
</template>

<script setup lang="ts">
import { ref, computed, watch, onMounted } from 'vue'
import { ElMessageBox, ElMessage } from 'element-plus'
import { useSessionStore } from '../stores/session'
import LLMConfigDialog from './LLMConfigDialog.vue'
import SkillDialog from './SkillDialog.vue'
import { configApi } from '../api/config'
import type { LLMProfile } from '../types'

interface FieldGroup {
  key: string
  name: string
  columns: string[]
  rows: number
  cols: number
  primary: boolean
}

const store = useSessionStore()
const creating = ref(false)
const emit = defineEmits<{
  (e: 'open-knowledge'): void
  (e: 'new-session'): void
  (e: 'collapse'): void
}>()

// 深色模式：初始值与 html.dark 同步（main.ts 已在挂载前恢复）
const isDark = ref(document.documentElement.classList.contains('dark'))
function toggleTheme() {
  isDark.value = !isDark.value
  document.documentElement.classList.toggle('dark', isDark.value)
  localStorage.setItem('theme', isDark.value ? 'dark' : 'light')
}

// LLM 配置：展示当前模型 + 弹窗
const llmDialogVisible = ref(false)
const llmModel = ref('')
async function loadLLMModel(retries = 10, intervalMs = 1000) {
  for (let i = 0; i < retries; i++) {
    try {
      const cfg = await configApi.getLLMConfig()
      llmModel.value = cfg.model || ''
      return
    } catch {
      // 后端未就绪时等待后重试
      if (i < retries - 1) await new Promise((r) => setTimeout(r, intervalMs))
    }
  }
}
function onLLMSaved(cfg: LLMProfile) {
  llmModel.value = cfg.model
}
onMounted(() => loadLLMModel())

// 技能管理弹窗：管理 Skill（Layer 3）的 CRUD + 启停
// skills 数据存在 session store 里，ChatPanel 直接读取用于对话框选择 chips
const skillDialogVisible = ref(false)
async function onSkillsChanged() {
  await store.loadSkills()
}

// 默认展开历史会话；字段参考在首次有数据时自动加入展开
const activeNames = ref<string[]>(['sessions'])
// 追踪是否已自动展开过字段参考，避免用户折叠后被重新打开
let fieldsAutoExpanded = false

// 字段参考分组：优先用 tableOverviews，降级到 overview
const fieldGroups = computed<FieldGroup[]>(() => {
  if (store.tableOverviews.length > 0) {
    return store.tableOverviews.map((t, i) => ({
      key: t.dataset_id || `t${i}`,
      name: t.table_name || `表 ${i + 1}`,
      columns: t.columns,
      rows: t.rows,
      cols: t.cols,
      primary: i === 0,
    }))
  }
  if (store.overview) {
    return [{
      key: 'main',
      name: store.currentSession?.df_name || '主表',
      columns: store.overview.columns,
      rows: store.overview.rows,
      cols: store.overview.cols,
      primary: true,
    }]
  }
  return []
})

const hasFieldRef = computed(() => fieldGroups.value.length > 0)

// 首次出现字段数据时自动展开字段参考（之后不再自动操作，尊重用户折叠）
watch(hasFieldRef, (v) => {
  if (v && !fieldsAutoExpanded) {
    fieldsAutoExpanded = true
    if (!activeNames.value.includes('fields')) {
      activeNames.value = [...activeNames.value, 'fields']
    }
  }
}, { immediate: true })

async function createNew() {
  emit('new-session')
}

async function selectSession(sid: string) {
  if (sid === store.currentSid) return
  await store.selectSession(sid)
}

async function del(sid: string) {
  try {
    await ElMessageBox.confirm('确定删除该会话？', '提示', { type: 'warning' })
  } catch {
    return
  }
  await store.deleteSession(sid)
  ElMessage.success('已删除')
}
</script>
