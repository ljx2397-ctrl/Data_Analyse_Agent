<template>
  <el-dialog
    v-model="visible"
    width="480px"
    append-to-body
    :close-on-click-modal="false"
    class="new-session-dialog"
  >
    <template #header>
      <div class="dialog-title">
        <span class="dialog-title-icon">🚀</span>
        <span>选择数据源</span>
      </div>
    </template>

    <p class="dialog-hint">请选择数据来源，接入后开始分析</p>

    <div class="source-cards">
      <div class="source-card" @click="emit('select', 'upload')">
        <div class="source-icon">📂</div>
        <div class="source-name">上传文件</div>
        <div class="source-desc">CSV / Excel，支持多表关联</div>
      </div>
      <div class="source-card" @click="emit('select', 'api')">
        <div class="source-icon">🌐</div>
        <div class="source-name">API 数据源</div>
        <div class="source-desc">连接外部接口，支持历史配置</div>
      </div>
    </div>
  </el-dialog>
</template>

<script setup lang="ts">
const visible = defineModel<boolean>('visible', { default: false })
const emit = defineEmits<{ (e: 'select', kind: 'upload' | 'api'): void }>()
</script>

<style scoped>
.dialog-title {
  display: flex;
  align-items: center;
  gap: 6px;
  font-size: 16px;
  font-weight: 600;
}
.dialog-title-icon {
  font-size: 18px;
}
.dialog-hint {
  font-size: 13px;
  color: var(--c-text-placeholder);
  margin: 0 0 16px;
}
.source-cards {
  display: flex;
  gap: 16px;
}
.source-card {
  flex: 1;
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  padding: 28px 16px;
  border: 2px solid var(--c-border-light);
  border-radius: 12px;
  cursor: pointer;
  transition: all 0.2s;
  text-align: center;
}
.source-card:hover {
  border-color: var(--c-primary);
  background: var(--c-bg-active);
  transform: translateY(-2px);
}
.source-icon {
  font-size: 36px;
  margin-bottom: 10px;
}
.source-name {
  font-size: 15px;
  font-weight: 600;
  color: var(--c-text);
  margin-bottom: 4px;
}
.source-desc {
  font-size: 12px;
  color: var(--c-text-placeholder);
  line-height: 1.5;
}
</style>
