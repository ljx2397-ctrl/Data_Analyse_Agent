<template>
  <el-dialog
    v-model="visible"
    title="上传追加数据文件"
    width="560px"
    append-to-body
    @closed="reset"
  >
    <!-- 步骤 1：选择数据文件 -->
    <div class="upload-step">
      <h4>1️⃣ 选择数据文件（CSV / Excel）</h4>
      <el-upload
        :auto-upload="false"
        :show-file-list="false"
        accept=".csv,.xlsx,.xlsm"
        :on-change="onDataFileChange"
      >
        <el-button type="primary">选择文件</el-button>
      </el-upload>
      <span v-if="dataFileName" style="margin-left: 12px; color: #67c23a">
        ✅ {{ dataFileName }}
      </span>
    </div>

    <!-- 步骤 2：选择 Sheet -->
    <div v-if="sheets && sheets.length > 0" class="upload-step">
      <h4>2️⃣ 选择工作表</h4>
      <el-select v-model="selectedSheet" placeholder="选择 Sheet" style="width: 100%">
        <el-option v-for="s in sheets" :key="s" :label="s" :value="s" />
      </el-select>
    </div>

    <!-- 步骤 3：可选字段文件（支持多文件） -->
    <div v-if="dataFile" class="upload-step">
      <h4>3️⃣ 字段说明文件（可选，支持多文件）</h4>
      <div style="display: flex; gap: 8px; align-items: center; flex-wrap: wrap">
        <el-upload
          :auto-upload="false"
          :show-file-list="false"
          accept=".csv,.txt"
          multiple
          :on-change="onHeaderFileChange"
        >
          <el-button size="small">选择字段文件</el-button>
        </el-upload>
        <el-button
          v-if="pendingHeaderFiles.length > 0"
          size="small"
          :loading="parsing"
          @click="doParseHeader"
        >
          🤖 解析字段（{{ pendingHeaderFiles.length }} 个文件）
        </el-button>
      </div>
      <div v-if="headerFileNames.length > 0" style="margin-top: 6px">
        <span v-for="name in headerFileNames" :key="name" class="header-file-tag">
          {{ name }}
        </span>
      </div>
    </div>

    <!-- 步骤 4：字段确认 -->
    <div v-if="columnsDraft.length > 0" class="upload-step">
      <h4>4️⃣ 确认字段名（共 {{ columnsDraft.length }} 个）</h4>
      <div class="col-editor">
        <div v-for="(_, i) in columnsDraft" :key="i" class="col-editor-row">
          <el-input v-model="columnsDraft[i]" size="small" />
          <el-button size="small" type="danger" plain @click="columnsDraft.splice(i, 1)">✕</el-button>
        </div>
      </div>
      <el-button size="small" style="margin-top: 8px" @click="columnsDraft.push('')">➕ 添加</el-button>
    </div>

    <!-- 已加载的表列表 -->
    <div v-if="store.datasets.length > 0" class="upload-step">
      <el-divider content-position="left">已加载的数据表</el-divider>
      <div v-for="(ds, idx) in store.datasets" :key="ds.id" class="ds-item">
        <span class="ds-name">
          {{ ds.name }}
          <el-tag v-if="idx === 0" size="small" type="primary" effect="plain" style="margin-left: 6px">主表</el-tag>
        </span>
        <span class="ds-info">
          {{ ds.kind }}
          <el-button
            v-if="idx > 0"
            size="small"
            type="danger"
            text
            :loading="removingId === ds.id"
            @click="doRemoveDataset(ds.id, ds.name)"
          >
            🗑 删除
          </el-button>
        </span>
      </div>
    </div>

    <!-- API 数据源入口 -->
    <el-divider content-position="left">或</el-divider>
    <el-button plain style="width: 100%" @click="apiDialogVisible = true">
      🌐 接入 API 数据源
    </el-button>

    <template #footer>
      <el-button @click="visible = false">取消</el-button>
      <el-button
        type="success"
        :loading="confirming"
        :disabled="!dataFile"
        @click="doConfirm"
      >
        确认导入
      </el-button>
    </template>
  </el-dialog>

  <!-- API 数据源对话框（追加场景） -->
  <ApiSourceDialog
    v-model:visible="apiDialogVisible"
    :sid="store.currentSid"
    :is-new="false"
    @fetched="onApiFetched"
  />
</template>

<script setup lang="ts">
import { ref } from 'vue'
import { ElMessage } from 'element-plus'
import type { UploadFile } from 'element-plus'
import { useSessionStore } from '../stores/session'
import ApiSourceDialog from './ApiSourceDialog.vue'
import type { ApiFetchResult } from '../types'

const store = useSessionStore()

const visible = defineModel<boolean>('visible', { default: false })

// API 数据源对话框（追加场景）
const apiDialogVisible = ref(false)
async function onApiFetched(_result: ApiFetchResult) {
  await store.refreshCurrent()
  visible.value = false
}

const dataFile = ref<File | null>(null)
const dataFileName = ref('')
const sheets = ref<string[] | null>(null)
const selectedSheet = ref<string | null>(null)

const pendingHeaderFiles = ref<File[]>([])
const headerFileNames = ref<string[]>([])
const parsing = ref(false)
const columnsDraft = ref<string[]>([])
const confirming = ref(false)
const removingId = ref<string | null>(null)

function reset() {
  dataFile.value = null
  dataFileName.value = ''
  sheets.value = null
  selectedSheet.value = null
  pendingHeaderFiles.value = []
  headerFileNames.value = []
  columnsDraft.value = []
}

async function onDataFileChange(file: UploadFile) {
  if (!file.raw) return
  dataFile.value = file.raw
  dataFileName.value = file.name
  sheets.value = null
  selectedSheet.value = null
  columnsDraft.value = []
  // 字段文件与新数据文件绑定，重置字段文件列表
  pendingHeaderFiles.value = []
  headerFileNames.value = []

  // 上传到后端
  try {
    const res = await store.uploadData(file.raw)
    if (res?.sheets && res.sheets.length > 0) {
      sheets.value = res.sheets
      selectedSheet.value = res.sheets[0]
    }
  } catch (e: any) {
    ElMessage.error(e?.response?.data?.detail || '上传失败')
    reset()
  }
}

function onHeaderFileChange(file: UploadFile) {
  if (!file.raw) return
  pendingHeaderFiles.value.push(file.raw)
  headerFileNames.value.push(file.name)
}

async function doParseHeader() {
  if (pendingHeaderFiles.value.length === 0) return
  parsing.value = true
  try {
    // 逐个解析字段文件，合并结果
    const allCols: string[] = []
    for (const f of pendingHeaderFiles.value) {
      const res = await store.parseHeader(f)
      if (res) {
        allCols.push(...res.columns)
      }
    }
    if (allCols.length > 0) {
      columnsDraft.value = allCols
      ElMessage.success(`从 ${pendingHeaderFiles.value.length} 个文件解析到 ${allCols.length} 个字段`)
    } else {
      ElMessage.warning('未能解析到字段，请手动填写')
    }
  } catch (e: any) {
    ElMessage.error(e?.response?.data?.detail || '解析失败，可手动填写')
  } finally {
    parsing.value = false
  }
}

async function doConfirm() {
  if (!dataFile.value) return
  confirming.value = true
  try {
    const cols = columnsDraft.value.map((c) => c.trim()).filter((c) => c !== '')
    await store.confirmData({
      sheet: selectedSheet.value,
      columns: cols.length > 0 ? cols : null,
    })
    ElMessage.success(`已追加数据表：${dataFileName.value}`)
    visible.value = false
  } catch (e: any) {
    ElMessage.error(e?.response?.data?.detail || '导入失败')
  } finally {
    confirming.value = false
  }
}

async function doRemoveDataset(dsId: string, dsName: string) {
  removingId.value = dsId
  try {
    await store.removeDataset(dsId)
    ElMessage.success(`已删除副表：${dsName}`)
  } catch (e: any) {
    ElMessage.error(e?.response?.data?.detail || '删除失败')
  } finally {
    removingId.value = null
  }
}
</script>

<style scoped>
.upload-step {
  margin-bottom: 16px;
}
.upload-step h4 {
  margin-bottom: 8px;
  font-size: 14px;
  color: var(--c-text);
}
.col-editor {
  max-height: 200px;
  overflow-y: auto;
  display: flex;
  flex-direction: column;
  gap: 4px;
}
.col-editor-row {
  display: flex;
  gap: 4px;
}
.ds-item {
  display: flex;
  justify-content: space-between;
  padding: 4px 8px;
  background: var(--c-bg-inset);
  border-radius: 4px;
  margin-bottom: 4px;
  font-size: 13px;
}
.ds-name {
  color: var(--c-text);
}
.ds-info {
  color: var(--c-text-placeholder);
}
.header-file-tag {
  display: inline-block;
  margin-right: 6px;
  margin-bottom: 4px;
  padding: 1px 8px;
  background: var(--c-bg-inset-2);
  border-radius: 3px;
  font-size: 12px;
  color: var(--c-text-secondary);
}
</style>
