<template>
  <div
    class="app-layout"
    :class="{ 'sidebar-collapsed': sidebarCollapsed }"
    :style="{ '--sidebar-width': sidebarWidth + 'px' }"
  >
    <AppSidebar v-show="!sidebarCollapsed" @open-knowledge="kbVisible = true" @new-session="createNew" @collapse="sidebarCollapsed = true" />
    <div
      v-show="!sidebarCollapsed"
      class="sidebar-resizer"
      :class="{ resizing: isResizing }"
      @mousedown="startResize"
    ></div>
    <button
      class="sidebar-toggle"
      :title="sidebarCollapsed ? '展开侧边栏' : '收起侧边栏'"
      @click="sidebarCollapsed = !sidebarCollapsed"
    >
      <span class="toggle-arrow">{{ sidebarCollapsed ? '▶' : '◀' }}</span>
    </button>
    <main class="app-main">
      <!-- 后端未就绪时的加载状态 -->
      <div v-if="waiting" class="empty-state">
        <div class="emoji">⏳</div>
        <p>正在连接后端服务...</p>
        <p style="font-size: 12px; color: var(--c-text-disabled)">首次启动需要几秒钟</p>
      </div>
      <!-- 后端就绪后 -->
      <template v-else-if="store.currentSid">
        <ChatPanel v-if="store.hasData" />
        <UploadFlow v-else />
      </template>
      <div v-else class="empty-state">
        <div class="emoji">🏦</div>
        <p>点击「新建对话」开始使用</p>
        <el-button type="primary" :loading="creating" @click="createNew">➕ 新建对话</el-button>
      </div>
    </main>

    <KnowledgeDrawer v-model:visible="kbVisible" />
    <!-- 新建对话：数据源选择 -->
    <NewSessionDialog v-model:visible="newDialogVisible" @select="onSelectSource" />
    <!-- API 数据源对话框（新建场景由 App 触发，追加场景由 ChatUploadDialog 自持） -->
    <ApiSourceDialog
      v-model:visible="apiDialogVisible"
      :sid="store.currentSid"
      :is-new="apiIsNew"
      @fetched="onApiFetched"
      @closed="onApiDialogClosed"
    />
  </div>
</template>

<script setup lang="ts">
import { onMounted, ref } from 'vue'
import axios from 'axios'
import { useSessionStore } from './stores/session'
import AppSidebar from './components/AppSidebar.vue'
import ChatPanel from './components/ChatPanel.vue'
import UploadFlow from './components/UploadFlow.vue'
import KnowledgeDrawer from './components/KnowledgeDrawer.vue'
import NewSessionDialog from './components/NewSessionDialog.vue'
import ApiSourceDialog from './components/ApiSourceDialog.vue'
import type { ApiFetchResult } from './types'

const store = useSessionStore()
const creating = ref(false)
const sidebarCollapsed = ref(false)
const waiting = ref(true)
const kbVisible = ref(false)
const sidebarWidth = ref(parseInt(localStorage.getItem('sidebarWidth') || '280'))
const isResizing = ref(false)

// 新建对话流程：数据源选择 + API 对话框
const newDialogVisible = ref(false)
const apiDialogVisible = ref(false)
const apiIsNew = ref(false)  // 标记 API 对话框是新建场景还是追加场景

function startResize(e: MouseEvent) {
  e.preventDefault()
  isResizing.value = true
  const startX = e.clientX
  const startWidth = sidebarWidth.value
  const onMove = (ev: MouseEvent) => {
    const newWidth = Math.max(200, Math.min(500, startWidth + ev.clientX - startX))
    sidebarWidth.value = newWidth
  }
  const onUp = () => {
    isResizing.value = false
    document.removeEventListener('mousemove', onMove)
    document.removeEventListener('mouseup', onUp)
    localStorage.setItem('sidebarWidth', String(sidebarWidth.value))
  }
  document.addEventListener('mousemove', onMove)
  document.addEventListener('mouseup', onUp)
}

async function createNew() {
  newDialogVisible.value = true
}

// 数据源选择后：创建会话并进入对应流程
async function onSelectSource(kind: 'upload' | 'api') {
  creating.value = true
  try {
    await store.createSession()
    newDialogVisible.value = false
    if (kind === 'api') {
      apiIsNew.value = true
      apiDialogVisible.value = true
    }
    // kind === 'upload' 时 App.vue 自动显示 UploadFlow（hasData=false）
  } finally {
    creating.value = false
  }
}

// API 数据源接入成功：刷新概览
async function onApiFetched(_result: ApiFetchResult) {
  await store.refreshCurrent()
}

// API 对话框关闭：新建场景下用户未接入数据 → 清理空会话
async function onApiDialogClosed() {
  if (apiIsNew.value && store.currentSid && !store.hasData) {
    await store.deleteSession(store.currentSid)
  }
  apiIsNew.value = false
}

// 轮询健康检查，等后端就绪后再加载历史会话。
// 解决：一键启动时前端先于后端就绪，导致首次进页面拉不到历史、需手动刷新的问题。
async function waitForBackend(maxAttempts = 30, intervalMs = 1000): Promise<boolean> {
  for (let i = 0; i < maxAttempts; i++) {
    try {
      await axios.get('/api/health', { timeout: 2000 })
      return true
    } catch {
      await new Promise((r) => setTimeout(r, intervalMs))
    }
  }
  return false
}

onMounted(async () => {
  await waitForBackend()
  waiting.value = false
  await store.loadSessions()
  // 并行加载 Skill 列表（ChatPanel 对话框选择 chips 需要）
  store.loadSkills()
  // 有历史会话则选中第一个；无会话则显示空状态页（不自动创建空会话）
  if (store.sessions.length > 0) {
    await store.selectSession(store.sessions[0].sid)
  }
})
</script>
