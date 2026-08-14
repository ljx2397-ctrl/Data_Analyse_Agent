<template>
  <div class="chat-header">
    <h2>💬 数据智能分析</h2>
    <div class="header-right">
      <span class="meta" v-if="store.overview">
        {{ store.overview.rows.toLocaleString() }} 行 × {{ store.overview.cols }} 列
        <span v-if="store.multiTable" class="multi-table-badge">
          {{ store.datasets.length }} 张表
        </span>
      </span>
      <span
        v-if="store.currentUsage && store.currentUsage.total_tokens > 0"
        class="meta usage-total"
        :title="`输入 ${store.currentUsage.input_tokens.toLocaleString()} / 输出 ${store.currentUsage.output_tokens.toLocaleString()} tokens · ${store.currentUsage.turns} 轮${store.currentUsage.estimated ? ' · 含估算值' : ''}`"
      >
        ⚡ {{ fmtTokens(store.currentUsage.total_tokens) }} tokens
      </span>
      <el-button size="small" text @click="traceVisible = true" title="查看 Agent 执行轨迹">
        🔍 轨迹
      </el-button>
    </div>
  </div>

  <div class="chat-messages" ref="msgListRef" @scroll="onScroll">
    <template v-if="store.messages.length === 0 && !store.streaming">
      <div class="empty-state" style="flex: 1">
        <div class="emoji">📊</div>
        <p>数据已加载，输入你的分析需求开始对话</p>
        <p style="font-size: 12px">例如：各支行交易金额排名、画一个 AUM 分布饼图、导出异常检测报告</p>
        <p v-if="store.multiTable" style="font-size: 12px; color: var(--c-primary); margin-top: 8px">
          💡 当前已加载 {{ store.datasets.length }} 张表，可进行多表关联分析（如"把交易表和客户表 join 起来"）
        </p>
      </div>
    </template>
    <template v-else>
      <MessageBubble
        v-for="(m, i) in store.messages"
        :key="i"
        :msg="m"
        :index="i"
        :actions-disabled="store.streaming"
        @edit="startEdit"
        @delete="handleDelete"
      />
    </template>
    <div v-if="store.toolStatus" class="tool-status">
      <span class="dot">●</span>{{ store.toolStatus }}
    </div>
  </div>

  <div class="chat-input-area">
    <!-- 编辑模式提示条 -->
    <div v-if="editingIndex !== null" class="editing-banner">
      <span class="editing-text">
        ✏️ 正在编辑第 {{ editingIndex + 1 }} 条消息，发送后将重新生成回复
      </span>
      <el-button text size="small" @click="cancelEdit">取消编辑</el-button>
    </div>
    <!-- 多表标签 -->
    <div v-if="store.multiTable" class="table-tags">
      <el-tag
        v-for="ds in store.datasets"
        :key="ds.id"
        size="small"
        :type="ds.id === store.datasets[0]?.id ? 'primary' : 'info'"
        effect="plain"
      >
        {{ ds.name }}
      </el-tag>
    </div>
    <div class="chat-input-row">
      <el-button
        class="upload-btn"
        :loading="false"
        :disabled="store.streaming"
        @click="uploadDialogVisible = true"
        title="上传追加数据文件"
      >
        📎
      </el-button>
      <!-- 模式选择 -->
      <el-popover
        placement="top"
        trigger="click"
        :width="280"
        popper-class="mode-popover"
        :show-arrow="false"
        :offset="8"
      >
        <template #reference>
          <el-button
            class="mode-btn"
            :type="store.activeMode !== 'auto' ? 'primary' : 'default'"
            :disabled="store.streaming"
            :title="activeModeTitle"
          >
            {{ activeModeIcon }} {{ activeModeLabel }}
          </el-button>
        </template>
        <div class="mode-menu">
          <div class="mode-menu-title">选择推理模式</div>
          <div
            v-for="opt in modeOptions"
            :key="opt.value"
            class="mode-menu-item"
            :class="{ active: store.activeMode === opt.value }"
            @click="store.setActiveMode(opt.value)"
          >
            <div class="mode-menu-header">
              <span class="mode-menu-icon">{{ opt.icon }}</span>
              <span class="mode-menu-name">{{ opt.label }}</span>
              <el-icon v-if="store.activeMode === opt.value" class="mode-menu-check">
                <Check />
              </el-icon>
            </div>
            <div class="mode-menu-desc">{{ opt.desc }}</div>
          </div>
        </div>
      </el-popover>
      <!-- Skills 选择：按钮放在上传按钮旁边，点击向上展开下拉选择 -->
      <el-popover
        v-if="store.enabledSkills.length > 0"
        placement="top"
        trigger="click"
        :width="240"
        popper-class="skill-popover"
        :show-arrow="false"
        :offset="8"
      >
        <template #reference>
          <el-button
            class="skill-btn"
            :type="store.activeSkillId ? 'primary' : 'default'"
            :disabled="store.streaming"
            :title="activeSkillName || '选择技能'"
          >
            🛠️ Skills
          </el-button>
        </template>
        <div class="skill-menu">
          <div class="skill-menu-title">
            选择技能（点击切换 / 取消）
          </div>
          <div
            v-for="sk in store.enabledSkills"
            :key="sk.id"
            class="skill-menu-item"
            :class="{ active: store.activeSkillId === sk.id }"
            :title="sk.description || sk.name"
            @click="store.setActiveSkill(sk.id)"
          >
            <span class="skill-menu-name">{{ sk.name }}</span>
            <el-icon v-if="store.activeSkillId === sk.id" class="skill-menu-check">
              <Check />
            </el-icon>
          </div>
          <div v-if="store.activeSkillId" class="skill-menu-footer">
            <el-button link size="small" @click="store.setActiveSkill(store.activeSkillId)">
              取消选择
            </el-button>
          </div>
        </div>
      </el-popover>
      <el-input
        v-model="input"
        type="textarea"
        :autosize="{ minRows: 1, maxRows: 5 }"
        :placeholder="inputPlaceholder"
        :disabled="store.streaming"
        @keydown.enter.exact.prevent="send"
      />
      <el-button
        v-if="store.streaming"
        type="danger"
        @click="store.stopStreaming()"
      >
        ⏹ 停止
      </el-button>
      <el-button
        v-else
        type="primary"
        :disabled="!input.trim()"
        @click="send"
      >
        {{ editingIndex !== null ? '更新' : '发送' }}
      </el-button>
    </div>
  </div>

  <ChatUploadDialog v-model:visible="uploadDialogVisible" />
  <TraceDrawer v-model:visible="traceVisible" />
</template>

<script setup lang="ts">
import { ref, computed, watch, nextTick, onMounted } from 'vue'
import { Check } from '@element-plus/icons-vue'
import { ElMessageBox } from 'element-plus'
import { useSessionStore } from '../stores/session'
import { fmtTokens } from '../utils/format'
import MessageBubble from './MessageBubble.vue'
import ChatUploadDialog from './ChatUploadDialog.vue'
import TraceDrawer from './TraceDrawer.vue'

const store = useSessionStore()
const input = ref('')
const msgListRef = ref<HTMLElement | null>(null)
const uploadDialogVisible = ref(false)
const traceVisible = ref(false)

// ── 模式选择 ──────────────────────────────────────────────────
interface ModeOption {
  value: 'auto' | 'react' | 'plan'
  label: string       // 菜单项 / placeholder 显示的完整名称
  shortLabel: string  // 按钮上显示的简称
  icon: string
  desc: string
  title: string
}
const modeOptions: ModeOption[] = [
  {
    value: 'auto',
    label: '自动路由',
    shortLabel: '自动',
    icon: '🤖',
    desc: '根据问题复杂度自动选择：简单问题用 ReAct（快），复杂多步任务用 Plan-Execute-Replan（稳）',
    title: '自动路由：根据任务复杂度智能选择模式',
  },
  {
    value: 'react',
    label: 'ReAct',
    shortLabel: 'ReAct',
    icon: '⚡',
    desc: '边推理边执行，单步快速响应。适合简单查询、单一统计、看图等 1~2 步能完成的任务',
    title: 'ReAct 模式：快速单步推理',
  },
  {
    value: 'plan',
    label: 'Plan-Execute-Replan',
    shortLabel: 'Plan',
    icon: '📋',
    desc: '先制定完整计划再逐步执行，遇到错误自动重规划。适合多维度交叉分析、导出完整报告、多图表组合等复杂任务',
    title: 'Plan-Execute-Replan 模式：先规划 → 分步执行 → 遇错自动重规划',
  },
]
const activeModeOption = computed<ModeOption>(
  () => modeOptions.find((o) => o.value === store.activeMode) || modeOptions[0],
)
// 按钮上用短标签，防止 Plan-Execute-Replan 太长放不下
const activeModeLabel = computed(() => activeModeOption.value.shortLabel)
// placeholder 用完整标签
const activeModeFullLabel = computed(() => activeModeOption.value.label)
const activeModeIcon = computed(() => activeModeOption.value.icon)
const activeModeTitle = computed(() => activeModeOption.value.title)
// ──────────────────────────────────────────────────────────────

// 当前选中的技能名（按钮 title / placeholder 提示用）
const activeSkillName = computed(() => {
  if (!store.activeSkillId) return ''
  const sk = store.skills.find((s) => s.id === store.activeSkillId)
  return sk?.name || ''
})

// 选中技能时给输入框提示，让用户清楚知道这条消息会走 skill
// mode=react 是默认值，不写"模式："前缀避免多余提示；只有显式选 auto/plan 时才显示
const inputPlaceholder = computed(() => {
  const parts: string[] = []
  if (store.activeMode === 'plan') {
    parts.push(`模式：${activeModeFullLabel.value}`)
  } else if (store.activeMode === 'auto') {
    parts.push(`模式：${activeModeFullLabel.value}`)
  }
  if (activeSkillName.value) {
    parts.push(`技能：${activeSkillName.value}`)
  }
  const prefix = parts.length ? `[${parts.join(' · ')}] ` : ''
  return `${prefix}请描述你的分析需求... (Enter 发送，Shift+Enter 换行)`
})

async function send() {
  const q = input.value.trim()
  if (!q || store.streaming) return
  // 编辑重发模式：截断到该消息后重新发送（ChatGPT 式编辑）
  if (editingIndex.value !== null) {
    const idx = editingIndex.value
    editingIndex.value = null
    input.value = ''
    await store.resendFromEdit(idx, q)
    return
  }
  input.value = ''
  // activeSkillId 由 store.sendQuery 内部消费并清空
  await store.sendQuery(q)
}

// ── 消息编辑/删除 ──────────────────────────────────────────────
const editingIndex = ref<number | null>(null)

function startEdit(index: number) {
  const m = store.messages[index]
  if (!m) return
  editingIndex.value = index
  input.value = m.content
  // 聚焦输入框末尾，便于直接续编
  nextTick(() => {
    const ta = document.querySelector<HTMLTextAreaElement>(
      '.chat-input-row .el-textarea__inner',
    )
    if (ta) {
      ta.focus()
      ta.setSelectionRange(ta.value.length, ta.value.length)
    }
  })
}

function cancelEdit() {
  editingIndex.value = null
  input.value = ''
}

async function handleDelete(index: number) {
  try {
    await ElMessageBox.confirm(
      '确定删除这条消息吗？删除后不可恢复。',
      '删除消息',
      { type: 'warning', confirmButtonText: '删除', cancelButtonText: '取消' },
    )
  } catch {
    return  // 用户取消
  }
  // 删除会影响正在编辑的消息索引，需同步调整
  if (editingIndex.value === index) {
    cancelEdit()
  } else if (editingIndex.value !== null && editingIndex.value > index) {
    editingIndex.value = editingIndex.value - 1
  }
  await store.deleteMessage(index)
}

// 用户是否手动上滑查看（不在底部时阻止自动滚动）
const userScrolledUp = ref(false)
const SCROLL_THRESHOLD = 80 // 距底部多少 px 内算"在底部"

function onScroll() {
  const el = msgListRef.value
  if (!el) return
  const nearBottom = el.scrollHeight - el.scrollTop - el.clientHeight < SCROLL_THRESHOLD
  userScrolledUp.value = !nearBottom
}

// 刷新页面后自动滚动到底部（等 MessageBubble 的 markdown 完全渲染）
onMounted(() => {
  setTimeout(() => {
    const el = msgListRef.value
    if (el) el.scrollTop = el.scrollHeight
  }, 300)
})

// 会话切换时强制滚到底部；流式更新时仅在用户未上滑时自动滚动
watch(
  () => [store.currentSid, store.messages.length, store.streamingText],
  ([newSid], [oldSid]) => {
    if (newSid !== oldSid) {
      userScrolledUp.value = false
      // 切换会话时退出编辑模式，避免索引错位
      editingIndex.value = null
      input.value = ''
    }
    if (userScrolledUp.value) return
    nextTick(() => {
      const el = msgListRef.value
      if (el) el.scrollTop = el.scrollHeight
    })
  },
)
</script>

<style scoped>
.chat-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 12px 20px;
  border-bottom: 1px solid var(--c-border);
}
.chat-header h2 {
  margin: 0;
  font-size: 16px;
  color: var(--c-text);
}
.chat-header .meta {
  font-size: 13px;
  color: var(--c-text-placeholder);
}
.header-right {
  display: flex;
  align-items: center;
  gap: 12px;
}
.usage-total {
  white-space: nowrap;
}
.multi-table-badge {
  display: inline-block;
  margin-left: 8px;
  padding: 1px 8px;
  background: var(--c-bg-active);
  color: var(--c-primary);
  border-radius: 10px;
  font-size: 12px;
}

.chat-messages {
  flex: 1;
  overflow-y: auto;
  padding: 20px;
}

.empty-state {
  text-align: center;
  color: var(--c-text-placeholder);
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
}
.empty-state .emoji {
  font-size: 48px;
  margin-bottom: 12px;
}

.tool-status {
  padding: 8px 20px;
  font-size: 13px;
  color: var(--c-primary);
}
.tool-status .dot {
  animation: blink 1s infinite;
  margin-right: 4px;
}
@keyframes blink {
  0%, 100% { opacity: 1; }
  50% { opacity: 0.3; }
}

.chat-input-area {
  border-top: 1px solid var(--c-border);
  padding: 12px 20px;
}

.editing-banner {
  display: flex;
  align-items: center;
  justify-content: space-between;
  margin-bottom: 8px;
  padding: 6px 12px;
  background: var(--el-color-warning-light-9, #fdf6ec);
  border: 1px solid var(--el-color-warning-light-5, #f5dab1);
  border-radius: 4px;
  font-size: 12px;
  color: var(--el-color-warning, #e6a23c);
}
.editing-text {
  flex: 1;
}
.table-tags {
  display: flex;
  gap: 6px;
  flex-wrap: wrap;
  margin-bottom: 8px;
}
.chat-input-row {
  display: flex;
  gap: 8px;
  align-items: flex-end;
}
.chat-input-row .el-input {
  flex: 1;
}
.upload-btn {
  flex-shrink: 0;
  font-size: 18px;
  padding: 8px 12px;
}
.mode-btn {
  flex-shrink: 0;
  font-size: 13px;
  padding: 8px 12px;
  white-space: nowrap;
}
.skill-btn {
  flex-shrink: 0;
  font-size: 13px;
  padding: 8px 12px;
  white-space: nowrap;
}
</style>

<!-- popover 内容渲染在 body，scoped 不生效，用独立样式块 -->
<style>
.mode-popover.el-popover.el-popper {
  padding: 0 !important;
}
.mode-menu {
  padding: 4px 0;
}
.mode-menu-title {
  font-size: 12px;
  color: var(--c-text-placeholder, #909399);
  padding: 6px 12px;
  border-bottom: 1px solid var(--c-border, #ebeef5);
}
.mode-menu-item {
  padding: 8px 12px;
  cursor: pointer;
  transition: background 0.15s;
}
.mode-menu-item:hover {
  background: var(--c-bg-active, #f5f7fa);
}
.mode-menu-item.active {
  background: var(--c-bg-active, #f5f7fa);
}
.mode-menu-header {
  display: flex;
  align-items: center;
  gap: 6px;
  font-size: 13px;
  color: var(--c-text, #303133);
  font-weight: 500;
}
.mode-menu-item.active .mode-menu-header {
  color: var(--c-primary, #2e75b6);
  font-weight: 600;
}
.mode-menu-icon {
  font-size: 14px;
}
.mode-menu-name {
  flex: 1;
}
.mode-menu-check {
  color: var(--c-primary, #2e75b6);
  margin-left: 8px;
}
.mode-menu-desc {
  margin-top: 4px;
  padding-left: 20px;
  font-size: 12px;
  line-height: 1.5;
  color: var(--c-text-placeholder, #909399);
}
.skill-popover.el-popover.el-popper {
  padding: 0 !important;
}
.skill-menu {
  padding: 4px 0;
}
.skill-menu-title {
  font-size: 12px;
  color: var(--c-text-placeholder, #909399);
  padding: 6px 12px;
  border-bottom: 1px solid var(--c-border, #ebeef5);
}
.skill-menu-item {
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 8px 12px;
  cursor: pointer;
  font-size: 13px;
  color: var(--c-text, #303133);
  transition: background 0.15s;
}
.skill-menu-item:hover {
  background: var(--c-bg-active, #f5f7fa);
}
.skill-menu-item.active {
  color: var(--c-primary, #2e75b6);
  font-weight: 600;
}
.skill-menu-name {
  flex: 1;
  min-width: 0;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.skill-menu-check {
  color: var(--c-primary, #2e75b6);
  margin-left: 8px;
}
.skill-menu-footer {
  border-top: 1px solid var(--c-border, #ebeef5);
  padding: 4px 8px;
  text-align: right;
}
</style>
