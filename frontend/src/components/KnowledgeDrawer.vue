<template>
  <el-drawer
    :model-value="visible"
    title="📚 业务知识库"
    direction="rtl"
    size="620px"
    @update:model-value="$emit('update:visible', $event)"
  >
    <!-- 检索模式切换 -->
    <div class="kb-mode-bar">
      <span class="kb-mode-label">检索方式</span>
      <el-radio-group v-model="retrieveMode" size="small" @change="onModeChange">
        <el-radio-button value="vector">向量化</el-radio-button>
        <el-radio-button value="name">文件名匹配</el-radio-button>
      </el-radio-group>
    </div>

    <!-- 分类筛选 -->
    <div class="kb-category-bar">
      <span class="kb-mode-label">分类筛选</span>
      <el-select
        v-model="selectedCategory"
        size="small"
        placeholder="全部分类"
        clearable
        style="width: 220px"
      >
        <el-option label="全部分类" value="" />
        <el-option
          v-for="cat in categories"
          :key="cat"
          :label="`${cat} (${categoryCount(cat)})`"
          :value="cat"
        />
      </el-select>
    </div>

    <!-- 顶部工具栏：检索 + 导入 + 新增 -->
    <div class="kb-toolbar">
      <el-input
        v-model="searchQuery"
        placeholder="输入业务概念检索，如 AUM 口径"
        clearable
        @keyup.enter="doSearch"
        @clear="clearSearch"
      >
        <template #append>
          <el-button :loading="searching" @click="doSearch">🔍 检索</el-button>
        </template>
      </el-input>
      <div class="kb-toolbar-btns">
        <el-button :loading="uploading" @click="triggerFileInput">📤 导入</el-button>
        <el-button type="primary" @click="openCreate">➕ 新增</el-button>
      </div>
      <input
        ref="fileInputRef"
        type="file"
        accept=".txt,.md,.csv,.xlsx"
        style="display: none"
        @change="handleFileChange"
      />
    </div>

    <div class="kb-meta">
      <span v-if="searchMode">检索结果：{{ displayList.length }} 条</span>
      <span v-else>共 {{ list.length }} 条知识</span>
      <el-button v-if="searchMode" text size="small" @click="clearSearch">返回全部</el-button>
    </div>

    <!-- 列表 -->
    <div v-loading="loading" class="kb-list">
      <div v-for="item in displayList" :key="item.id" class="kb-item">
        <div class="kb-item-header">
          <span class="kb-title">{{ item.title }}</span>
          <div class="kb-tags">
            <el-tag v-if="item.category" size="small" type="warning">{{ item.category }}</el-tag>
            <el-tag v-if="item.score != null" size="small" type="success">
              相关度 {{ item.score }}
            </el-tag>
          </div>
        </div>
        <div class="kb-content">{{ item.content }}</div>
        <div v-if="item.tags && item.tags.length" class="kb-item-taglist">
          <el-tag v-for="t in item.tags" :key="t" size="small" type="info" effect="plain">
            {{ t }}
          </el-tag>
        </div>
        <div class="kb-item-footer">
          <span class="kb-time">{{ item.created_at }}</span>
          <div>
            <el-button text size="small" @click="openEdit(item)">✏️ 编辑</el-button>
            <el-button text size="small" type="danger" @click="remove(item.id)">🗑️ 删除</el-button>
          </div>
        </div>
      </div>

      <div v-if="!loading && displayList.length === 0" class="kb-empty">
        <div class="emoji">{{ searchMode ? '🔍' : '📭' }}</div>
        <p>{{ searchMode ? '未检索到相关内容' : '暂无知识条目，点击「新增」添加业务知识' }}</p>
      </div>
    </div>

    <!-- 新增/编辑对话框 -->
    <el-dialog
      v-model="dialogVisible"
      :title="editingId ? '编辑知识' : '新增知识'"
      width="520px"
      append-to-body
    >
      <el-form :model="form" label-width="72px">
        <el-form-item label="标题" required>
          <el-input v-model="form.title" placeholder="如：AUM 口径定义" />
        </el-form-item>
        <el-form-item label="分类" required>
          <el-input v-model="form.category" placeholder="如：字段说明 / 业务术语 / 业务规则" />
        </el-form-item>
        <el-form-item label="内容" required>
          <el-input
            v-model="form.content"
            type="textarea"
            :rows="6"
            placeholder="详细描述业务知识，将作为语义检索的文本..."
          />
        </el-form-item>
        <el-form-item label="标签">
          <el-input v-model="tagsInput" placeholder="多个标签用逗号分隔，如：零售,客户,指标" />
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="dialogVisible = false">取消</el-button>
        <el-button type="primary" :loading="saving" @click="save">保存</el-button>
      </template>
    </el-dialog>
  </el-drawer>
</template>

<script setup lang="ts">
import { ref, computed, watch } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import { knowledgeApi } from '../api/knowledge'
import type { KnowledgeItem } from '../types'

const props = defineProps<{ visible: boolean }>()
const emit = defineEmits<{ (e: 'update:visible', v: boolean): void }>()

const list = ref<KnowledgeItem[]>([])
const searchResults = ref<KnowledgeItem[]>([])
const searchMode = ref(false)
const searchQuery = ref('')
const loading = ref(false)
const searching = ref(false)
const selectedCategory = ref('')

// 从 list 动态提取所有分类
const categories = computed(() => {
  const set = new Set<string>()
  list.value.forEach((item) => {
    set.add(item.category || '未分类')
  })
  return Array.from(set).sort()
})

function categoryCount(cat: string): number {
  return list.value.filter((item) => (item.category || '未分类') === cat).length
}

const displayList = computed(() => {
  let base = searchMode.value ? searchResults.value : list.value
  if (selectedCategory.value) {
    base = base.filter((item) => (item.category || '未分类') === selectedCategory.value)
  }
  return base
})

// 检索模式：vector=向量化 / name=文件名匹配
const retrieveMode = ref<'vector' | 'name'>('vector')

async function loadMode() {
  try {
    const res = await knowledgeApi.getMode()
    retrieveMode.value = res.mode as 'vector' | 'name'
  } catch {
    // 静默失败，用默认值
  }
}

async function onModeChange(mode: 'vector' | 'name') {
  try {
    await knowledgeApi.setMode(mode)
    ElMessage.success(`已切换为${mode === 'vector' ? '向量化检索' : '文件名匹配'}`)
  } catch (e: any) {
    ElMessage.error('切换失败：' + (e?.message || e))
    retrieveMode.value = mode === 'vector' ? 'name' : 'vector'  // 回退
  }
}

// 抽屉打开时加载列表 + 模式
watch(
  () => props.visible,
  (v) => {
    if (v) {
      loadList()
      loadMode()
    }
  }
)

async function loadList() {
  loading.value = true
  try {
    list.value = await knowledgeApi.list()
  } catch (e: any) {
    ElMessage.error('加载知识库失败：' + (e?.message || e))
  } finally {
    loading.value = false
  }
}

async function doSearch() {
  if (!searchQuery.value.trim()) {
    clearSearch()
    return
  }
  searching.value = true
  searchMode.value = true
  try {
    searchResults.value = await knowledgeApi.search(
      searchQuery.value,
      undefined,
      selectedCategory.value || undefined,
    )
  } catch (e: any) {
    ElMessage.error('检索失败：' + (e?.message || e))
  } finally {
    searching.value = false
  }
}

// ── 文件导入 ──────────────────────────────────────────────────
const fileInputRef = ref<HTMLInputElement | null>(null)
const uploading = ref(false)
const pendingCategory = ref('')

async function triggerFileInput() {
  try {
    const { value } = await ElMessageBox.prompt('请输入分类（必填，用于同分类内检索比较）', '文件分类', {
      confirmButtonText: '确定',
      cancelButtonText: '取消',
      inputPlaceholder: '如：字段说明、业务术语、业务规则',
      inputValidator: (v: string) => !!v?.trim() || '分类不能为空',
    })
    pendingCategory.value = value.trim()
    fileInputRef.value?.click()
  } catch {
    // 用户取消
  }
}

async function handleFileChange(e: Event) {
  const target = e.target as HTMLInputElement
  const file = target.files?.[0]
  if (!file) return

  const ext = file.name.split('.').pop()?.toLowerCase()
  if (ext === 'xls') {
    ElMessage.warning('暂不支持旧版 .xls，请在 Excel 中另存为 .xlsx 后上传')
    target.value = ''
    return
  }
  if (!ext || !['txt', 'md', 'csv', 'xlsx'].includes(ext)) {
    ElMessage.warning('仅支持 .txt / .md / .csv / .xlsx 文件')
    target.value = ''
    return
  }

  uploading.value = true
  try {
    const items = await knowledgeApi.upload(file, pendingCategory.value)
    ElMessage.success(`成功导入 ${items.length} 条知识`)
    await loadList()
  } catch (err: any) {
    const detail = err?.response?.data?.detail || err?.message || err
    ElMessage.error('导入失败：' + detail)
  } finally {
    uploading.value = false
    target.value = ''   // 重置，允许重复上传同一文件
  }
}

function clearSearch() {
  searchMode.value = false
  searchResults.value = []
  searchQuery.value = ''
}

// ── 新增/编辑 ──────────────────────────────────────────────────
const dialogVisible = ref(false)
const editingId = ref('')
const form = ref({ title: '', content: '', category: '' })
const tagsInput = ref('')
const saving = ref(false)

function openCreate() {
  editingId.value = ''
  form.value = { title: '', content: '', category: '' }
  tagsInput.value = ''
  dialogVisible.value = true
}

function openEdit(item: KnowledgeItem) {
  editingId.value = item.id
  form.value = {
    title: item.title,
    content: item.content,
    category: item.category,
  }
  tagsInput.value = (item.tags || []).join(',')
  dialogVisible.value = true
}

async function save() {
  if (!form.value.title.trim() || !form.value.content.trim()) {
    ElMessage.warning('标题和内容不能为空')
    return
  }
  if (!form.value.category.trim()) {
    ElMessage.warning('分类不能为空（用于同分类内检索比较）')
    return
  }
  const tags = tagsInput.value
    .split(/[,，]/)
    .map((t) => t.trim())
    .filter(Boolean)
  const payload = { ...form.value, tags }
  saving.value = true
  try {
    if (editingId.value) {
      await knowledgeApi.update(editingId.value, payload)
      ElMessage.success('已更新')
    } else {
      await knowledgeApi.create(payload)
      ElMessage.success('已新增')
    }
    dialogVisible.value = false
    await loadList()
  } catch (e: any) {
    ElMessage.error('保存失败：' + (e?.message || e))
  } finally {
    saving.value = false
  }
}

async function remove(id: string) {
  try {
    await ElMessageBox.confirm('确定删除该知识条目？', '提示', { type: 'warning' })
  } catch {
    return
  }
  try {
    await knowledgeApi.remove(id)
    ElMessage.success('已删除')
    await loadList()
  } catch (e: any) {
    ElMessage.error('删除失败：' + (e?.message || e))
  }
}
</script>

<style scoped>
.kb-mode-bar {
  display: flex;
  align-items: center;
  gap: 8px;
  margin-bottom: 10px;
  padding-bottom: 8px;
  border-bottom: 1px solid var(--c-border);
}
.kb-mode-label {
  font-size: 13px;
  color: var(--c-text-placeholder);
  flex-shrink: 0;
}

.kb-category-bar {
  display: flex;
  align-items: center;
  gap: 8px;
  margin-bottom: 10px;
}

.kb-toolbar {
  display: flex;
  gap: 8px;
  margin-bottom: 8px;
}
.kb-toolbar .el-input {
  flex: 1;
}
.kb-toolbar-btns {
  display: flex;
  gap: 6px;
  flex-shrink: 0;
}
.kb-meta {
  display: flex;
  justify-content: space-between;
  align-items: center;
  font-size: 12px;
  color: var(--c-text-placeholder);
  margin-bottom: 12px;
}
.kb-list {
  display: flex;
  flex-direction: column;
  gap: 10px;
}
.kb-item {
  border: 1px solid var(--c-border);
  border-radius: 6px;
  padding: 12px;
  background: var(--c-bg-surface);
  transition: box-shadow 0.2s;
}
.kb-item:hover {
  box-shadow: 0 2px 8px rgba(0, 0, 0, 0.08);
}
.kb-item-header {
  display: flex;
  justify-content: space-between;
  align-items: center;
  margin-bottom: 6px;
}
.kb-title {
  font-weight: 600;
  font-size: 14px;
  color: var(--c-text);
}
.kb-tags {
  display: flex;
  gap: 4px;
}
.kb-content {
  font-size: 13px;
  color: var(--c-text-secondary);
  line-height: 1.6;
  white-space: pre-wrap;
  word-break: break-word;
  max-height: 120px;
  overflow: hidden;
  display: -webkit-box;
  -webkit-line-clamp: 5;
  -webkit-box-orient: vertical;
}
.kb-item-taglist {
  display: flex;
  flex-wrap: wrap;
  gap: 4px;
  margin-top: 6px;
}
.kb-item-footer {
  display: flex;
  justify-content: space-between;
  align-items: center;
  margin-top: 8px;
  padding-top: 8px;
  border-top: 1px dashed var(--c-border);
}
.kb-time {
  font-size: 11px;
  color: var(--c-text-disabled);
}
.kb-empty {
  text-align: center;
  padding: 48px 0;
  color: var(--c-text-placeholder);
}
.kb-empty .emoji {
  font-size: 40px;
  margin-bottom: 8px;
}
</style>
