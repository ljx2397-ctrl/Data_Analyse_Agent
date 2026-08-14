<template>
  <div class="chat-row" :class="msg.role">
    <div class="chat-avatar">{{ msg.role === 'user' ? '🧑' : '🏦' }}</div>
    <div class="chat-bubble-col">
    <div class="chat-bubble" :class="msg.role">
      <!-- 路由模式 + 计划步骤（plan 模式才显示） -->
      <div v-if="msg.role === 'assistant' && msg.mode === 'plan' && msg.plan_steps && msg.plan_steps.length" class="plan-card">
        <div class="plan-header">
          <span class="plan-badge">📋 Plan-Execute-Replan</span>
          <span class="plan-step-count">{{ msg.plan_steps.length }} 步</span>
        </div>
        <ol class="plan-steps">
          <li v-for="(step, i) in msg.plan_steps" :key="i" class="plan-step">
            <span class="plan-step-tool">{{ toolIcon(step.tool) }}</span>
            <span class="plan-step-intent">{{ step.intent }}</span>
            <span v-if="step.result_name" class="plan-step-name">→ {{ step.result_name }}</span>
          </li>
        </ol>
        <!-- 重规划事件 -->
        <div v-if="msg.replan_events && msg.replan_events.length" class="replan-section">
          <div v-for="(ev, i) in msg.replan_events" :key="i" class="replan-event">
            <span class="replan-badge" :class="ev.action">{{ replanLabel(ev.action) }}</span>
            <span class="replan-reason">{{ ev.reason }}</span>
            <div v-if="ev.new_steps && ev.new_steps.length" class="replan-new-steps">
              <span class="replan-new-label">新步骤：</span>
              <span v-for="(ns, j) in ev.new_steps" :key="j" class="replan-new-step">
                {{ toolIcon(ns.tool) }} {{ ns.intent }}
              </span>
            </div>
          </div>
        </div>
      </div>

      <!-- 正文 markdown -->
      <div v-if="msg.content" class="markdown-body" v-html="renderMd(msg.content)"></div>

      <!-- 表格（后端解析出的 markdown 表格字符串） -->
      <div
        v-for="(tbl, i) in msg.tables"
        :key="'t' + i"
        class="table-block"
        style="margin-top: 8px"
      >
        <div class="markdown-body" v-html="renderMd(tbl)"></div>
        <el-button
          type="primary"
          size="small"
          plain
          style="margin-top: 6px"
          @click="downloadTable(tbl, i)"
        >⬇️ 下载 CSV</el-button>
      </div>

      <!-- 图表 -->
      <div v-for="(cp, i) in msg.charts" :key="'c' + i" style="margin-top: 8px">
        <img :src="cp" style="max-width: 100%; border-radius: 6px" alt="图表" />
      </div>

      <!-- Excel 下载 -->
      <div v-for="(xp, i) in msg.xlsxs" :key="'x' + i" style="margin-top: 8px">
        <el-button type="success" size="small" @click="download(xp)">⬇️ 下载报告</el-button>
      </div>

      <!-- 可编辑代码 + 重新执行 -->
      <CodeRunner
        v-for="(code, i) in msg.code_exec"
        :key="'r' + i"
        :code="code"
      />

      <!-- 本轮 token 用量与耗时 -->
      <div v-if="msg.role === 'assistant' && msg.usage" class="usage-meta">
        ⚡ {{ fmtTokens(msg.usage.total_tokens) }} tokens{{ msg.usage.estimated ? '（估算）' : '' }}
        <template v-if="msg.usage.duration_ms != null">
          · {{ fmtDuration(msg.usage.duration_ms) }}
        </template>
        <span v-if="msg.mode" class="mode-tag">{{ msg.mode === 'plan' ? ' · plan-execute-replan' : ' · react' }}</span>
      </div>
    </div>

      <!-- 消息操作按钮：气泡外部，hover 整条消息时浮现 -->
      <div
        v-if="!actionsDisabled && msg.content"
        class="msg-actions"
        :class="msg.role"
      >
        <el-button
          text
          size="small"
          class="msg-action-btn"
          :title="copied ? '已复制' : '复制'"
          @click="copyContent"
        >
          <el-icon v-if="copied"><Check /></el-icon>
          <el-icon v-else><CopyDocument /></el-icon>
          <span class="msg-action-label">{{ copied ? '已复制' : '复制' }}</span>
        </el-button>
        <el-button
          v-if="msg.role === 'user'"
          text
          size="small"
          class="msg-action-btn"
          title="编辑后重新发送"
          @click="$emit('edit', index)"
        >
          <el-icon><EditPen /></el-icon>
          <span class="msg-action-label">修改</span>
        </el-button>
        <el-button
          text
          size="small"
          class="msg-action-btn msg-action-danger"
          title="删除该消息"
          @click="$emit('delete', index)"
        >
          <el-icon><Delete /></el-icon>
          <span class="msg-action-label">删除</span>
        </el-button>
      </div>
    </div>
  </div>
</template>

<script setup lang="ts">
import { ref } from 'vue'
import { ElMessage } from 'element-plus'
import { Check, CopyDocument, EditPen, Delete } from '@element-plus/icons-vue'
import { renderMd } from '../utils/markdown'
import { fmtTokens, fmtDuration } from '../utils/format'
import type { Message } from '../types'
import CodeRunner from './CodeRunner.vue'

const props = defineProps<{
  msg: Message
  index: number
  actionsDisabled?: boolean
}>()

defineEmits<{
  (e: 'edit', index: number): void
  (e: 'delete', index: number): void
}>()

// 复制消息内容到剪贴板（含 1.5s 成功反馈）
const copied = ref(false)
async function copyContent() {
  try {
    await navigator.clipboard.writeText(props.msg.content || '')
    copied.value = true
    setTimeout(() => { copied.value = false }, 1500)
  } catch {
    ElMessage.error('复制失败，请手动选择文本复制')
  }
}

// 工具图标映射
function toolIcon(tool: string): string {
  switch (tool) {
    case 'schema_tool': return '🔎'
    case 'analyse_tool': return '📊'
    case 'chart_tool': return '📈'
    case 'excel_tool': return '📑'
    case 'knowledge_search_tool': return '📚'
    default: return '🔧'
  }
}

function replanLabel(action: string): string {
  switch (action) {
    case 'replan': return '🔄 重规划'
    case 'finish': return '✅ 完成'
    default: return action
  }
}

function download(url: string) {
  const a = document.createElement('a')
  a.href = url
  a.download = ''
  a.target = '_blank'
  document.body.appendChild(a)
  a.click()
  document.body.removeChild(a)
}

// 把 markdown 表格解析成二维数组（跳过分隔行 | --- |）
function parseMdTable(md: string): string[][] {
  const lines = md.trim().split('\n').filter(l => l.trim().startsWith('|'))
  const rows: string[][] = []
  for (const line of lines) {
    if (/^\s*\|?[\s:|-]+\|?\s*$/.test(line)) continue
    const cells = line.split('|').slice(1).map(c => c.trim())
    if (cells.length && cells[cells.length - 1] === '') cells.pop()
    rows.push(cells)
  }
  return rows
}

// 表格转 CSV（含 BOM 头，Excel 打开正确识别 UTF-8 中文）
function tableToCsv(md: string): string {
  const rows = parseMdTable(md)
  return rows.map(row =>
    row.map(cell => {
      if (/[",\n]/.test(cell)) {
        return '"' + cell.replace(/"/g, '""') + '"'
      }
      return cell
    }).join(',')
  ).join('\n')
}

function downloadTable(md: string, index: number) {
  const csv = '\ufeff' + tableToCsv(md)
  const blob = new Blob([csv], { type: 'text/csv;charset=utf-8' })
  const url = URL.createObjectURL(blob)
  const a = document.createElement('a')
  a.href = url
  a.download = `表格_${index + 1}.csv`
  document.body.appendChild(a)
  a.click()
  document.body.removeChild(a)
  URL.revokeObjectURL(url)
}
</script>

<style scoped>
.usage-meta {
  margin-top: 8px;
  padding-top: 6px;
  border-top: 1px dashed var(--c-border, #ebeef5);
  font-size: 11px;
  color: var(--c-text-placeholder, #909399);
}
.mode-tag {
  color: var(--c-primary, #2e75b6);
}

/* 气泡列：纵向包裹 气泡 + 操作按钮，宽度跟随气泡内容 */
.chat-bubble-col {
  display: flex;
  flex-direction: column;
  min-width: 0;
  max-width: 80%;
}
/* 覆盖全局 .chat-bubble 的 max-width，改由 col 统一控制宽度 */
.chat-bubble-col .chat-bubble {
  max-width: none;
}

/* 消息操作按钮：气泡外部，hover 整条消息时浮现 */
.msg-actions {
  display: flex;
  gap: 2px;
  width: 100%;
  margin-top: 4px;
  padding: 0 2px;
  opacity: 0;
  transition: opacity 0.15s;
  /* assistant 靠左、user 靠右，跟随气泡对齐方向 */
  justify-content: flex-start;
}
.msg-actions.user {
  justify-content: flex-end;
}
.chat-row:hover .msg-actions {
  opacity: 1;
}
.msg-action-btn {
  height: 22px;
  padding: 0 5px;
  font-size: 12px;
  color: var(--c-text-placeholder, #909399);
}
.msg-action-btn:hover {
  color: var(--c-primary, #2e75b6);
}
.msg-action-btn.msg-action-danger:hover {
  color: var(--el-color-danger, #f56c6c);
}
.msg-action-label {
  margin-left: 2px;
}

/* Plan-Execute 计划卡片 */
.plan-card {
  margin-bottom: 10px;
  padding: 10px 12px;
  background: var(--c-bg-active, #f5f7fa);
  border: 1px solid var(--c-border, #ebeef5);
  border-left: 3px solid var(--c-primary, #2e75b6);
  border-radius: 6px;
  font-size: 12px;
}
.plan-header {
  display: flex;
  align-items: center;
  gap: 8px;
  margin-bottom: 6px;
}
.plan-badge {
  font-weight: 600;
  color: var(--c-primary, #2e75b6);
}
.plan-step-count {
  color: var(--c-text-placeholder, #909399);
  font-size: 11px;
}
.plan-steps {
  margin: 0;
  padding-left: 18px;
  list-style: decimal;
}
.plan-step {
  margin: 3px 0;
  line-height: 1.5;
  color: var(--c-text, #303133);
}
.plan-step-tool {
  margin-right: 4px;
}
.plan-step-intent {
  color: var(--c-text, #303133);
}
.plan-step-name {
  margin-left: 6px;
  color: var(--c-primary, #2e75b6);
  font-size: 11px;
}

/* 重规划事件 */
.replan-section {
  margin-top: 8px;
  padding-top: 6px;
  border-top: 1px dashed var(--c-border, #ebeef5);
}
.replan-event {
  margin: 4px 0;
  font-size: 11px;
}
.replan-badge {
  display: inline-block;
  padding: 1px 6px;
  border-radius: 3px;
  font-weight: 600;
  margin-right: 6px;
}
.replan-badge.replan {
  background: #fff3e0;
  color: #e65100;
}
.replan-badge.finish {
  background: #e8f5e9;
  color: #2e7d32;
}
.replan-reason {
  color: var(--c-text-placeholder, #909399);
}
.replan-new-steps {
  margin-top: 2px;
  padding-left: 12px;
}
.replan-new-label {
  color: var(--c-text-placeholder, #909399);
}
.replan-new-step {
  display: inline-block;
  margin-right: 8px;
  color: var(--c-text, #303133);
}
</style>
