<template>
  <el-drawer
    :model-value="visible"
    title="🔍 运行轨迹"
    size="440px"
    @update:model-value="emit('update:visible', $event)"
    @open="load"
  >
    <div v-if="loading" class="trace-empty">加载中...</div>
    <div v-else-if="events.length === 0" class="trace-empty">
      暂无轨迹记录，发送一条消息后这里会展示 Agent 的执行过程
    </div>
    <el-timeline v-else class="trace-timeline">
      <el-timeline-item
        v-for="(ev, i) in events"
        :key="i"
        :type="itemType(ev)"
        :timestamp="fmtTime(ev.ts)"
        placement="top"
      >
        <div class="ev-title">{{ evTitle(ev) }}</div>
        <div v-if="evDetail(ev)" class="ev-detail">{{ evDetail(ev) }}</div>
      </el-timeline-item>
    </el-timeline>
  </el-drawer>
</template>

<script setup lang="ts">
import { ref, nextTick } from 'vue'
import { statsApi } from '../api/stats'
import { useSessionStore } from '../stores/session'
import { fmtTokens, fmtDuration } from '../utils/format'
import type { TraceEvent } from '../types'

defineProps<{ visible: boolean }>()
const emit = defineEmits<{ 'update:visible': [boolean] }>()

const store = useSessionStore()
const events = ref<TraceEvent[]>([])
const loading = ref(false)

async function load() {
  if (!store.currentSid) return
  loading.value = true
  try {
    events.value = await statsApi.getTrace(store.currentSid)
  } catch {
    events.value = []
  } finally {
    loading.value = false
    // 数据渲染后自动滚到底部，展示最新事件
    await nextTick()
    const body = document.querySelector('.el-drawer__body')
    if (body) body.scrollTop = body.scrollHeight
  }
}

function fmtTime(ts: string): string {
  // ISO → HH:mm:ss
  const m = ts.match(/T(\d{2}:\d{2}:\d{2})/)
  return m ? m[1] : ts
}

function itemType(ev: TraceEvent): 'primary' | 'success' | 'warning' | 'danger' | 'info' {
  switch (ev.type) {
    case 'turn_start': return 'primary'
    case 'turn_end': return 'success'
    case 'skill': return 'success'
    case 'tool': return 'warning'
    case 'route': return 'primary'
    case 'plan': return 'primary'
    case 'replan': return 'warning'
    case 'retry': return 'danger'
    case 'error': return 'danger'
    default: return 'info'
  }
}

function evTitle(ev: TraceEvent): string {
  switch (ev.type) {
    case 'turn_start': return `💬 用户提问：${ev.query || ''}`
    case 'tool': return `🔧 工具调用：${ev.name || 'tool'}`
    case 'skill': return `🛠️ 技能执行：${ev.name || ev.skill_id || ''}`
    case 'route': {
      const modeName = ev.mode === 'plan' ? 'Plan-Execute-Replan' : 'ReAct'
      // 用户主动选择模式时显示"用户决策"，自动路由时显示"路由决策"
      const label = ev.user_override ? '👤 用户决策' : '🧭 路由决策'
      // 用户决策不显示复杂度/步数（这些是占位值，无参考意义）
      if (ev.user_override) {
        return `${label}：${modeName}`
      }
      return `${label}：${modeName}（复杂度 ${ev.complexity ?? '-'}，预计 ${ev.estimated_steps ?? '-'} 步）`
    }
    case 'plan': return `📋 生成计划（${ev.step_count || 0} 步）`
    case 'replan': return `🔄 重规划：${ev.action || ''}`
    case 'retry': return `🔁 自动重试（第 ${ev.attempt} 次）`
    case 'summary': return '🗜️ 上下文滚动摘要'
    case 'turn_end': return '✅ 本轮完成'
    case 'error': return '❌ 执行出错'
    default: return ev.type
  }
}

function evDetail(ev: TraceEvent): string {
  const parts: string[] = []
  if (ev.type === 'tool' && ev.preview) {
    parts.push(String(ev.preview).slice(0, 120))
  }
  if (ev.type === 'route') {
    if (ev.query) parts.push(`问题：${String(ev.query).slice(0, 80)}`)
    if (ev.reason) parts.push(`理由：${String(ev.reason).slice(0, 120)}`)
  }
  if (ev.type === 'replan' && ev.reason) {
    parts.push(`原因：${String(ev.reason).slice(0, 120)}`)
  }
  if (ev.type === 'retry' && ev.error) {
    parts.push(String(ev.error).slice(0, 120))
  }
  if (ev.type === 'error' && ev.message) {
    parts.push(String(ev.message).slice(0, 120))
  }
  if (ev.type === 'turn_end') {
    const tok = (ev.input_tokens ?? 0) + (ev.output_tokens ?? 0)
    parts.push(
      `${fmtTokens(tok)} tokens${ev.estimated ? '（估算）' : ''}` +
      (ev.duration_ms != null ? ` · ${fmtDuration(ev.duration_ms)}` : '') +
      (ev.mode ? ` · ${ev.mode}` : ''),
    )
  }
  if (ev.type === 'skill' && ev.duration_ms != null) {
    parts.push(`耗时 ${fmtDuration(ev.duration_ms)} · 0 tokens（确定性流程）`)
  }
  if (ev.type === 'summary' && ev.input_tokens != null) {
    parts.push(`消耗 ${fmtTokens((ev.input_tokens ?? 0) + (ev.output_tokens ?? 0))} tokens`)
  }
  return parts.join('\n')
}
</script>

<style scoped>
.trace-empty {
  text-align: center;
  color: var(--c-text-placeholder, #909399);
  font-size: 13px;
  padding: 40px 0;
}
.trace-timeline {
  padding-left: 4px;
}
.ev-title {
  font-size: 13px;
  color: var(--c-text, #303133);
  word-break: break-all;
}
.ev-detail {
  margin-top: 4px;
  font-size: 12px;
  color: var(--c-text-placeholder, #909399);
  white-space: pre-wrap;
  word-break: break-all;
}
</style>
