<template>
  <div class="code-runner">
    <div class="code-runner-header" @click="collapsed = !collapsed" style="cursor: pointer; user-select: none;">
      <span><span class="collapse-icon" style="display: inline-block; width: 1em;">{{ collapsed ? '▶' : '▼' }}</span> 🛠️ 分析代码（可编辑后重新执行）</span>
      <el-button v-show="!collapsed" size="small" type="primary" :loading="running" @click.stop="run">▶️ 重新执行</el-button>
    </div>
    <textarea v-show="!collapsed" v-model="editedCode" spellcheck="false"></textarea>
    <div v-if="!collapsed && result" class="code-runner-result markdown-body" v-html="renderMd(result)"></div>
  </div>
</template>

<script setup lang="ts">
import { ref } from 'vue'
import { ElMessage } from 'element-plus'
import { renderMd } from '../utils/markdown'
import { useSessionStore } from '../stores/session'

const props = defineProps<{ code: string }>()
const store = useSessionStore()

const editedCode = ref(props.code)
const result = ref('')
const running = ref(false)
const collapsed = ref(true)

async function run() {
  if (!editedCode.value.trim()) return
  running.value = true
  result.value = ''
  try {
    result.value = await store.runCode(editedCode.value)
  } catch (e: any) {
    result.value = `❌ 执行出错：${e?.message || e}`
    ElMessage.error('代码执行失败')
  } finally {
    running.value = false
  }
}
</script>
