<template>
  <div class="upload-flow">
    <div class="upload-card">
      <div class="upload-card-header">
        <h2>📂 上传数据文件</h2>
        <div class="cancel-btn-wrap" @click="cancelCreate">
          <el-button type="danger" plain round>
            <span class="cancel-x">✕</span>
            <span>取消</span>
          </el-button>
          <div class="cancel-hint">
            <span class="hint-icon">💡</span>
            取消新建，返回上一会话
          </div>
        </div>
      </div>

      <!-- 已上传的表列表 -->
      <div v-if="uploadedTables.length > 0" class="uploaded-section">
        <div class="section-title">已上传的表（{{ uploadedTables.length }}）</div>
        <div class="uploaded-list">
          <div
            v-for="(t, i) in uploadedTables"
            :key="i"
            class="uploaded-item"
          >
            <div class="uploaded-item-header">
              <span class="uploaded-item-name">✅ {{ t.name }}</span>
              <el-tag size="small" :type="i === 0 ? 'primary' : 'info'" effect="plain">
                {{ i === 0 ? '主表' : `副表${i}` }}
              </el-tag>
              <span class="uploaded-item-meta">{{ t.cols }} 列</span>
            </div>
            <div class="uploaded-item-cols">
              {{ t.columns.slice(0, 6).join(' · ') }}{{ t.columns.length > 6 ? ' ...' : '' }}
            </div>
          </div>
        </div>
      </div>

      <!-- 当前表的上传表单 -->
      <div class="current-upload">
        <div class="current-upload-title">
          {{ uploadedTables.length === 0 ? '1️⃣ 上传主表' : `${uploadedTables.length + 1}️⃣ 上传副表 ${uploadedTables.length}` }}
        </div>

        <!-- 步骤 1：数据文件 -->
        <div class="upload-step">
          <h4>选择数据文件（CSV / Excel）</h4>
          <el-upload
            :auto-upload="false"
            :show-file-list="false"
            accept=".csv,.xlsx,.xlsm"
            :on-change="onDataFileChange"
          >
            <el-button type="primary">选择文件</el-button>
          </el-upload>
          <span v-if="dataFileName" style="margin-left: 12px; color: #67c23a">
            ✅ {{ dataFileName }}（{{ dataKind }}）
          </span>
          <el-button
            v-if="pendingDataFile"
            style="margin-left: 12px"
            :loading="uploading"
            @click="doUploadData"
          >
            上传
          </el-button>
        </div>

        <!-- 步骤 2：选择 Sheet（xlsx 时） -->
        <div v-if="sheets && sheets.length > 0" class="upload-step">
          <h4>选择工作表</h4>
          <el-select v-model="selectedSheet" placeholder="选择 Sheet" style="width: 100%">
            <el-option v-for="s in sheets" :key="s" :label="s" :value="s" />
          </el-select>
        </div>

        <!-- 步骤 3：可选字段说明文件 -->
        <div v-if="dataUploaded" class="upload-step">
          <h4>字段说明文件（可选，无标题行时使用）</h4>
          <el-upload
            :auto-upload="false"
            :show-file-list="false"
            accept=".csv,.txt"
            multiple
            :on-change="onHeaderFileChange"
          >
            <el-button>选择字段说明文件</el-button>
          </el-upload>
          <el-button
            v-if="pendingHeaderFiles.length > 0"
            style="margin-left: 12px"
            :loading="parsing"
            @click="doParseHeader"
          >
            🤖 大模型解析（{{ pendingHeaderFiles.length }} 个文件）
          </el-button>
          <div v-if="headerFileNames.length > 0" style="margin-top: 4px">
            <span v-for="name in headerFileNames" :key="name" class="header-file-tag">
              {{ name }}
            </span>
          </div>
        </div>

        <!-- 步骤 4：字段确认/编辑 -->
        <div v-if="columnsDraft.length > 0" class="upload-step">
          <h4>确认或修改字段名</h4>
          <p style="font-size: 12px; color: #909399; margin-bottom: 8px">
            共 {{ columnsDraft.length }} 个字段，可直接编辑、增删行。
          </p>
          <div class="col-editor">
            <div v-for="(_, i) in columnsDraft" :key="i" class="col-editor-row">
              <el-input v-model="columnsDraft[i]" size="small" />
              <el-button size="small" type="danger" plain @click="columnsDraft.splice(i, 1)">✕</el-button>
            </div>
          </div>
          <el-button size="small" style="margin-top: 8px" @click="columnsDraft.push('')">➕ 添加字段</el-button>
        </div>

        <!-- 确认导入此表 -->
        <div v-if="dataUploaded" class="upload-step">
          <el-button
            type="success"
            :loading="confirming"
            @click="doConfirm"
          >
            ✅ 确认导入此表
          </el-button>
          <p style="font-size: 12px; color: #909399; margin-top: 8px">
            未编辑字段名时，将使用文件首行作为列名。确认后可继续上传下一张表。
          </p>
        </div>
      </div>

      <!-- 完成 -->
      <div v-if="uploadedTables.length > 0" class="finish-section">
        <el-divider />
        <el-button
          type="primary"
          size="large"
          :loading="finishing"
          @click="finish"
        >
          完成，开始分析 →
        </el-button>
        <p style="font-size: 12px; color: #909399; margin-top: 8px">
          已上传 {{ uploadedTables.length }} 张表，可继续上传或开始分析。
        </p>
      </div>
    </div>
  </div>
</template>

<script setup lang="ts">
import { ref } from 'vue'
import { ElMessage } from 'element-plus'
import type { UploadFile } from 'element-plus'
import { useSessionStore } from '../stores/session'
import { uploadApi } from '../api/upload'

const store = useSessionStore()

// 已上传的表列表
interface UploadedTable {
  name: string
  kind: string
  cols: number
  columns: string[]
}
const uploadedTables = ref<UploadedTable[]>([])
const finishing = ref(false)

// 当前表的上传状态
const pendingDataFile = ref<File | null>(null)
const dataFileName = ref('')
const dataKind = ref('')
const dataUploaded = ref(false)
const uploading = ref(false)
const sheets = ref<string[] | null>(null)
const selectedSheet = ref<string | null>(null)

// 字段说明文件状态
const pendingHeaderFiles = ref<File[]>([])
const headerFileNames = ref<string[]>([])
const parsing = ref(false)

// 字段草稿
const columnsDraft = ref<string[]>([])
const confirming = ref(false)

function onDataFileChange(file: UploadFile) {
  if (!file.raw) return
  pendingDataFile.value = file.raw
  dataFileName.value = file.name
  // 重置后续状态
  dataUploaded.value = false
  dataKind.value = ''
  sheets.value = null
  selectedSheet.value = null
  columnsDraft.value = []
  pendingHeaderFiles.value = []
  headerFileNames.value = []
}

async function doUploadData() {
  if (!pendingDataFile.value || !store.currentSid) return
  uploading.value = true
  try {
    const res = await uploadApi.uploadData(store.currentSid, pendingDataFile.value)
    dataKind.value = res.kind
    sheets.value = res.sheets
    if (res.sheets && res.sheets.length > 0) {
      selectedSheet.value = res.sheets[0]
    }
    dataUploaded.value = true
    ElMessage.success('文件已上传')
  } catch (e: any) {
    ElMessage.error(e?.response?.data?.detail || '上传失败')
  } finally {
    uploading.value = false
  }
}

function onHeaderFileChange(file: UploadFile) {
  if (!file.raw) return
  pendingHeaderFiles.value.push(file.raw)
  headerFileNames.value.push(file.name)
}

async function doParseHeader() {
  if (pendingHeaderFiles.value.length === 0 || !store.currentSid) return
  parsing.value = true
  try {
    const allCols: string[] = []
    for (const f of pendingHeaderFiles.value) {
      const res = await uploadApi.parseHeader(store.currentSid, f)
      allCols.push(...res.columns)
    }
    if (allCols.length > 0) {
      columnsDraft.value = allCols
      ElMessage.success(`从 ${pendingHeaderFiles.value.length} 个文件解析到 ${allCols.length} 个字段`)
    } else {
      ElMessage.warning('未能解析到字段，请手动填写')
    }
  } catch (e: any) {
    ElMessage.error(e?.response?.data?.detail || '解析失败，可手动填写字段名')
  } finally {
    parsing.value = false
  }
}

async function doConfirm() {
  if (!store.currentSid) return
  confirming.value = true
  try {
    const cols = columnsDraft.value
      .map((c) => c.trim())
      .filter((c) => c !== '')
    const resp = await uploadApi.confirmData(store.currentSid, {
      sheet: selectedSheet.value,
      columns: cols.length > 0 ? cols : null,
    })
    // 从 table_overviews 最后一条取刚导入的表信息
    const lastTable = resp.table_overviews[resp.table_overviews.length - 1]
    if (lastTable) {
      uploadedTables.value.push({
        name: lastTable.table_name || dataFileName.value,
        kind: dataKind.value,
        cols: lastTable.cols,
        columns: lastTable.columns,
      })
    } else {
      uploadedTables.value.push({
        name: dataFileName.value,
        kind: dataKind.value,
        cols: cols.length || 0,
        columns: cols,
      })
    }
    const label = uploadedTables.value.length === 1 ? '主表' : `副表${uploadedTables.value.length - 1}`
    ElMessage.success(`${label}导入成功`)
    // 清空当前表单，准备上传下一张表
    resetCurrentForm()
  } catch (e: any) {
    ElMessage.error(e?.response?.data?.detail || '导入失败')
  } finally {
    confirming.value = false
  }
}

function resetCurrentForm() {
  pendingDataFile.value = null
  dataFileName.value = ''
  dataKind.value = ''
  dataUploaded.value = false
  sheets.value = null
  selectedSheet.value = null
  pendingHeaderFiles.value = []
  headerFileNames.value = []
  columnsDraft.value = []
}

async function finish() {
  finishing.value = true
  try {
    // 刷新 store 状态，overview 有值后页面自动切换到 ChatPanel
    await store.refreshCurrent()
    await store.loadSessions()
  } catch (e: any) {
    ElMessage.error('刷新失败：' + (e?.message || e))
  } finally {
    finishing.value = false
  }
}

// 取消新建：删除当前空会话，回退到上一个会话或空状态页
async function cancelCreate() {
  if (store.currentSid) {
    await store.deleteSession(store.currentSid)
  }
}
</script>

<style scoped>
.upload-card-header {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  margin-bottom: 4px;
}
.cancel-btn-wrap {
  display: flex;
  flex-direction: column;
  align-items: flex-end;
  gap: 6px;
  cursor: pointer;
  user-select: none;
}
.cancel-btn-wrap :deep(.el-button) {
  font-weight: 600;
  padding: 8px 18px !important;
}
.cancel-x {
  margin-right: 4px;
  font-weight: 700;
}
.cancel-hint {
  display: inline-flex;
  align-items: center;
  gap: 4px;
  padding: 4px 10px;
  background: var(--c-bg-inset, #f5f7fa);
  border: 1px solid var(--c-border-light, #ebeef5);
  border-radius: 6px;
  font-size: 12px;
  color: var(--c-text-secondary, #606266);
  line-height: 1.5;
  white-space: nowrap;
}
.hint-icon {
  font-size: 13px;
}
.uploaded-section {
  margin-bottom: 20px;
}
.section-title {
  font-size: 14px;
  font-weight: 600;
  color: var(--c-text);
  margin-bottom: 8px;
}
.uploaded-list {
  display: flex;
  flex-direction: column;
  gap: 8px;
}
.uploaded-item {
  padding: 10px 12px;
  background: var(--c-bg-inset);
  border-radius: 8px;
  border: 1px solid var(--c-border-light);
}
.uploaded-item-header {
  display: flex;
  align-items: center;
  gap: 8px;
  margin-bottom: 4px;
}
.uploaded-item-name {
  font-size: 13px;
  font-weight: 600;
  color: var(--c-text);
}
.uploaded-item-meta {
  font-size: 12px;
  color: var(--c-text-placeholder);
}
.uploaded-item-cols {
  font-size: 11px;
  color: var(--c-text-placeholder);
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.current-upload {
  margin-top: 8px;
}
.current-upload-title {
  font-size: 15px;
  font-weight: 600;
  color: var(--c-primary);
  margin-bottom: 14px;
  padding-bottom: 8px;
  border-bottom: 1px solid var(--c-border-light);
}
.header-file-tag {
  display: inline-block;
  margin-right: 6px;
  padding: 1px 8px;
  background: var(--c-bg-inset-2);
  border-radius: 3px;
  font-size: 12px;
  color: var(--c-text-secondary);
}
.col-editor {
  display: flex;
  flex-direction: column;
  gap: 4px;
}
.col-editor-row {
  display: flex;
  gap: 6px;
  align-items: center;
}
.finish-section {
  margin-top: 20px;
  text-align: center;
}
</style>
