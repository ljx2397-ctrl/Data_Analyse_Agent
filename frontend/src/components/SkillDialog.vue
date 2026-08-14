<template>
  <el-dialog
    v-model="visible"
    title="🛠️ 技能管理"
    width="720px"
    append-to-body
    @open="onOpen"
    @closed="reset"
  >
    <div class="skill-tip">
      Skill（Layer 3）：高频确定性流程，命中关键词即直接执行，不让 LLM 规划。
      内置 Skill 不可删除，但可编辑关键词 / 启停 / 重置。
    </div>

    <div class="skill-toolbar">
      <span class="skill-meta">共 {{ list.length }} 个技能 · {{ enabledCount }} 个启用</span>
      <el-button type="primary" size="small" @click="openCreate">➕ 新建技能</el-button>
    </div>

    <div v-loading="loading" class="skill-list">
      <div v-for="s in list" :key="s.id" class="skill-item">
        <div class="skill-item-main">
          <div class="skill-item-header">
            <span class="skill-name">{{ s.name }}</span>
            <div class="skill-tags">
              <el-tag v-if="s.builtin" size="small" type="warning" effect="plain">内置</el-tag>
              <el-tag v-else size="small" type="info" effect="plain">自定义</el-tag>
              <el-tag v-if="s.runner" size="small" type="success" effect="plain">
                runner: {{ s.runner }}
              </el-tag>
              <el-tag v-if="!s.enabled" size="small" type="danger" effect="plain">已停用</el-tag>
            </div>
          </div>
          <div class="skill-desc">{{ s.description || '（无描述）' }}</div>
          <div class="skill-keywords">
            <span class="kw-label">命中关键词：</span>
            <el-tag
              v-for="k in s.keywords"
              :key="k"
              size="small"
              type="info"
              effect="plain"
            >{{ k }}</el-tag>
            <span v-if="!s.keywords.length" class="kw-empty">（无）</span>
          </div>
        </div>
        <div class="skill-item-actions">
          <el-switch
            :model-value="s.enabled"
            @change="(v: boolean | string | number) => toggle(s, !!v)"
            size="small"
          />
          <el-button text size="small" @click="openEdit(s)">✏️ 编辑</el-button>
          <el-button v-if="s.builtin" text size="small" @click="resetSkill(s)">↺ 重置</el-button>
          <el-button v-else text size="small" type="danger" @click="remove(s)">🗑️ 删除</el-button>
        </div>
      </div>

      <div v-if="!loading && list.length === 0" class="skill-empty">
        <div class="emoji">🧩</div>
        <p>暂无技能，点击「新建技能」添加</p>
      </div>
    </div>

    <!-- 新增/编辑子弹窗 -->
    <el-dialog
      v-model="editVisible"
      :title="editingId ? '编辑技能' : '新建技能'"
      width="540px"
      append-to-body
    >
      <el-form :model="form" label-position="top">
        <el-form-item label="技能名称" required>
          <el-input v-model="form.name" placeholder="如：数据质量检测" />
        </el-form-item>
        <el-form-item label="描述">
          <el-input
            v-model="form.description"
            type="textarea"
            :autosize="{ minRows: 2, maxRows: 4 }"
            placeholder="说明这个技能做什么，方便后续维护"
          />
        </el-form-item>
        <el-form-item label="命中关键词" required>
          <el-input
            v-model="keywordsInput"
            placeholder="多个关键词用逗号或换行分隔，如：数据质量,质检,空值"
            type="textarea"
            :autosize="{ minRows: 3, maxRows: 6 }"
          />
          <div class="form-hint">
            用户输入包含任一关键词即命中该技能（不区分大小写）。
          </div>
        </el-form-item>
        <el-form-item label="启用">
          <el-switch v-model="form.enabled" />
        </el-form-item>
        <div v-if="!editingBuiltin" class="form-hint">
          自定义技能目前仅支持关键词命中后由 LLM 处理（runner 留空）。
          要接入确定性 runner 需在后端实现并注册。
        </div>
      </el-form>
      <template #footer>
        <el-button @click="editVisible = false">取消</el-button>
        <el-button type="primary" :loading="saving" @click="save">保存</el-button>
      </template>
    </el-dialog>
  </el-dialog>
</template>

<script setup lang="ts">
import { ref, computed } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import { skillsApi } from '../api/skills'
import type { SkillItem, SkillSavePayload } from '../types'

const props = defineProps<{ visible: boolean }>()
const emit = defineEmits<{
  (e: 'update:visible', v: boolean): void
  (e: 'changed'): void
}>()

const visible = computed({
  get: () => props.visible,
  set: (v) => emit('update:visible', v),
})

const list = ref<SkillItem[]>([])
const loading = ref(false)
const enabledCount = computed(() => list.value.filter((s) => s.enabled).length)

async function load() {
  loading.value = true
  try {
    list.value = await skillsApi.list()
  } catch (e: any) {
    ElMessage.error('加载技能失败：' + (e?.message || e))
  } finally {
    loading.value = false
  }
}

async function onOpen() {
  await load()
}

function reset() {
  list.value = []
  editVisible.value = false
}

// ── 启停 ──────────────────────────────────────────────────────
async function toggle(s: SkillItem, enabled: boolean) {
  try {
    const updated = await skillsApi.toggle(s.id)
    const i = list.value.findIndex((x) => x.id === s.id)
    if (i >= 0) list.value[i] = updated
    emit('changed')
    ElMessage.success(`${updated.name} 已${updated.enabled ? '启用' : '停用'}`)
  } catch (e: any) {
    ElMessage.error(e?.response?.data?.detail || e?.message || '切换失败')
  }
}

async function resetSkill(s: SkillItem) {
  try {
    await ElMessageBox.confirm(`确定重置「${s.name}」为默认配置？`, '提示', { type: 'warning' })
  } catch {
    return
  }
  try {
    const updated = await skillsApi.reset(s.id)
    const i = list.value.findIndex((x) => x.id === s.id)
    if (i >= 0) list.value[i] = updated
    emit('changed')
    ElMessage.success('已重置')
  } catch (e: any) {
    ElMessage.error(e?.response?.data?.detail || e?.message || '重置失败')
  }
}

async function remove(s: SkillItem) {
  try {
    await ElMessageBox.confirm(`确定删除技能「${s.name}」？`, '提示', { type: 'warning' })
  } catch {
    return
  }
  try {
    await skillsApi.remove(s.id)
    list.value = list.value.filter((x) => x.id !== s.id)
    emit('changed')
    ElMessage.success('已删除')
  } catch (e: any) {
    ElMessage.error(e?.response?.data?.detail || e?.message || '删除失败')
  }
}

// ── 新增/编辑 ──────────────────────────────────────────────────
const editVisible = ref(false)
const editingId = ref<string | null>(null)
const editingBuiltin = ref(false)
const keywordsInput = ref('')
const saving = ref(false)
const form = ref({
  name: '',
  description: '',
  enabled: true,
  runner: '',
})

function parseKeywords(text: string): string[] {
  return text
    .split(/[,\n，；;]/)
    .map((k) => k.trim())
    .filter((k) => k.length > 0)
}

function openCreate() {
  editingId.value = null
  editingBuiltin.value = false
  form.value = { name: '', description: '', enabled: true, runner: '' }
  keywordsInput.value = ''
  editVisible.value = true
}

function openEdit(s: SkillItem) {
  editingId.value = s.id
  editingBuiltin.value = s.builtin
  form.value = {
    name: s.name,
    description: s.description,
    enabled: s.enabled,
    runner: s.runner,
  }
  keywordsInput.value = s.keywords.join(', ')
  editVisible.value = true
}

async function save() {
  if (!form.value.name.trim()) {
    ElMessage.warning('请填写技能名称')
    return
  }
  const keywords = parseKeywords(keywordsInput.value)
  if (keywords.length === 0) {
    ElMessage.warning('至少填写 1 个关键词')
    return
  }
  const payload: SkillSavePayload = {
    id: editingId.value || null,
    name: form.value.name.trim(),
    description: form.value.description.trim(),
    keywords,
    enabled: form.value.enabled,
    runner: editingBuiltin.value ? undefined : form.value.runner.trim(),
  }
  saving.value = true
  try {
    const saved = await skillsApi.save(payload)
    const i = list.value.findIndex((x) => x.id === saved.id)
    if (i >= 0) list.value[i] = saved
    else list.value.push(saved)
    editVisible.value = false
    emit('changed')
    ElMessage.success('已保存')
  } catch (e: any) {
    ElMessage.error(e?.response?.data?.detail || e?.message || '保存失败')
  } finally {
    saving.value = false
  }
}
</script>

<style scoped>
.skill-tip {
  background: var(--c-bg-active, #f5f7fa);
  border-left: 3px solid var(--c-primary, #2e75b6);
  padding: 8px 12px;
  font-size: 12px;
  color: var(--c-text-secondary, #606266);
  border-radius: 4px;
  margin-bottom: 12px;
  line-height: 1.6;
}
.skill-toolbar {
  display: flex;
  justify-content: space-between;
  align-items: center;
  margin-bottom: 8px;
}
.skill-meta {
  font-size: 13px;
  color: var(--c-text-placeholder, #909399);
}
.skill-list {
  max-height: 460px;
  overflow-y: auto;
}
.skill-item {
  display: flex;
  justify-content: space-between;
  align-items: flex-start;
  padding: 12px;
  border: 1px solid var(--c-border, #ebeef5);
  border-radius: 6px;
  margin-bottom: 8px;
  gap: 12px;
}
.skill-item-main {
  flex: 1;
  min-width: 0;
}
.skill-item-header {
  display: flex;
  align-items: center;
  gap: 8px;
  flex-wrap: wrap;
  margin-bottom: 4px;
}
.skill-name {
  font-weight: 600;
  font-size: 14px;
  color: var(--c-text, #303133);
}
.skill-tags {
  display: flex;
  gap: 4px;
  flex-wrap: wrap;
}
.skill-desc {
  font-size: 12px;
  color: var(--c-text-secondary, #606266);
  margin-bottom: 6px;
  line-height: 1.5;
}
.skill-keywords {
  display: flex;
  align-items: center;
  gap: 4px;
  flex-wrap: wrap;
}
.kw-label {
  font-size: 12px;
  color: var(--c-text-placeholder, #909399);
}
.kw-empty {
  font-size: 12px;
  color: var(--c-text-placeholder, #909399);
  font-style: italic;
}
.skill-item-actions {
  display: flex;
  flex-direction: column;
  align-items: flex-end;
  gap: 6px;
  flex-shrink: 0;
}
.skill-empty {
  text-align: center;
  padding: 40px 0;
  color: var(--c-text-placeholder, #909399);
}
.skill-empty .emoji {
  font-size: 36px;
  margin-bottom: 8px;
}
.form-hint {
  font-size: 12px;
  color: var(--c-text-placeholder, #909399);
  line-height: 1.5;
  margin-top: 4px;
}
</style>
