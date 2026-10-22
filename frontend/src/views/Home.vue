<template>
  <div class="home-container home-redesign">
    <div class="page-header">
      <div class="hero-copy">
        <div class="eyebrow">旅途有方 · 你的旅行助手</div>
        <h1 class="page-title">下一程。<br class="home-title-break"><span>刚刚好。</span></h1>
        <p class="page-subtitle">从目的地到每日路线，把收藏的攻略变成走得通的行程。</p>
      </div>
    </div>

    <button v-if="latestTrip && !loading" type="button" class="resume-trip" @click="library?.open(latestTrip.id)">
      <span>继续上次的旅行</span><strong>{{ latestTrip.city }} · {{ latestTrip.start_date }} — {{ latestTrip.end_date }}</strong><span aria-hidden="true">→</span>
    </button>
    <section v-if="!loading && (pendingPlan || recoveryError)" class="planning-recovery" aria-label="恢复上次规划" role="status">
      <div><strong>上次规划还未确认完成</strong>
        <p v-if="pendingPlan?.request">{{ pendingPlan.request.city }} · {{ pendingPlan.request.start_date }} — {{ pendingPlan.request.end_date }}</p>
        <p>{{ recoveryError || '继续查询上次提交的需求，当前草稿不会覆盖它。' }}</p>
      </div>
      <button type="button" @click="resumePendingIfAny">继续上次规划</button>
    </section>
    <div class="travel-workspace">
    <section class="form-card" aria-label="规划旅行"><div class="planner-body">
      <div class="planner-title"><h2>规划一段新旅程</h2><span>先定方向，再安排细节</span></div>
      <div class="trip-quick-fields">
        <label>目的地<a-input v-model:value="formData.city" placeholder="想去哪个城市？" :disabled="loading || intakeLoading" aria-label="旅行目的地" /></label>
        <label>出发日期<a-date-picker :value="pickerValue(formData.start_date)" @update:value="value => setDate('start_date', value)" placeholder="选择日期" :disabled-date="disabledDate" :disabled="loading || intakeLoading" aria-label="旅行出发日期" /></label>
        <label>返程日期<a-date-picker :value="pickerValue(formData.end_date)" @update:value="value => setDate('end_date', value)" placeholder="选择日期" :disabled-date="disabledDate" :disabled="loading || intakeLoading" aria-label="旅行返程日期" /></label>
      </div>
      <div class="idea-composer">
        <label class="idea-label sr-only" for="trip-idea">告诉我你的旅行想法</label>
        <a-textarea
          id="trip-idea"
          v-model:value="tripIdea"
          class="idea-input"
          :rows="3"
          :maxlength="1500"
          :disabled="intakeLoading || loading"
          placeholder="这次想怎么玩？比如喜欢博物馆、想吃本地美食，或每天轻松一点…"
          @keydown.enter.exact="handleIdeaKeydown"
        />
        <div class="idea-actions">
          <span>按 Enter 发送 · Shift + Enter 换行</span>
          <button type="button" class="reference-trigger" :aria-expanded="referenceOpen" :disabled="intakeLoading || loading" @click="toggleReferencePanel">
            <span class="reference-trigger-title">＋ 添加攻略</span>
            <span class="reference-trigger-meta">{{ hasReferenceSources ? `${formData.reference_content.trim().length} 字` : screenshots.length ? '待识别' : '可选' }}</span>
          </button>
          <a-button class="idea-send" type="primary" :loading="intakeLoading || loading || refsAnalyzing" :disabled="(!pendingPlan && !tripIdea.trim() && !formData.city.trim()) || referenceBusy" @click="startPlanning">
            {{ intakeLoading ? '正在理解…' : refsAnalyzing ? '正在解析攻略…' : loading ? '正在规划…' : pendingPlan ? '继续上次规划' : '开始规划' }} <span aria-hidden="true">↗</span>
          </a-button>
        </div>
      </div>
      <div v-if="referenceOpen" class="reference-shortcut">
        <div class="reference-quick-panel">
          <p class="reference-help">把喜欢的攻略放在这里，再用上方一句话告诉我去哪、什么时候出发。小红书链接可能无法直接抓取，复制正文通常最稳妥。</p>
          <div class="reference-tabs" aria-label="添加参考内容方式">
            <button type="button" :aria-pressed="referenceTab === 'paste'" :class="{ active: referenceTab === 'paste' }" @click="referenceTab = 'paste'">粘贴正文</button>
            <button type="button" :aria-pressed="referenceTab === 'link'" :class="{ active: referenceTab === 'link' }" @click="referenceTab = 'link'">添加链接</button>
            <button type="button" :aria-pressed="referenceTab === 'image'" :class="{ active: referenceTab === 'image' }" @click="referenceTab = 'image'">上传截图</button>
          </div>
          <div v-if="referenceTab === 'paste'" class="reference-tab-content">
            <label class="reference-field-label" for="reference-paste">攻略正文或你的摘录</label>
            <a-textarea id="reference-paste" v-model:value="pasteText" :rows="5" :disabled="loading || refsAnalyzing || intakeLoading" placeholder="从小红书复制笔记正文，或直接粘贴想去的地点、餐厅和注意事项…" />
            <button type="button" class="reference-text-button" :disabled="!pasteText.trim() || loading || refsAnalyzing" @click="addPastedSource">添加这篇攻略</button>
          </div>
          <div v-else-if="referenceTab === 'link'" class="reference-tab-content">
            <div v-for="(_item, idx) in urlList" :key="idx" class="url-row">
              <a-input v-model:value="urlList[idx]" :placeholder="`粘贴攻略链接 #${idx + 1}`" size="large" :disabled="loading" class="url-input" />
              <a-button size="large" :loading="extractingUrlIdx === idx" :disabled="!urlList[idx] || loading" @click="handleExtractUrl(idx)" class="url-extract-btn">提取</a-button>
              <a-button v-if="urlList.length > 1" size="large" danger :disabled="loading" @click="removeUrl(idx)"></a-button>
            </div>
            <button type="button" class="reference-text-button" :disabled="loading" @click="addUrl">+ 再加一条链接</button>
            <p class="reference-small-hint">若链接提取失败，请切换到“粘贴正文”。</p>
          </div>
          <div v-else class="reference-tab-content">
            <div class="screenshot-upload-row">
              <a-upload accept="image/png,image/jpeg,image/jpg,image/webp" :before-upload="handleScreenshotAdd" :show-upload-list="false" :disabled="loading">
                <a-button size="large" :disabled="loading">+ 上传截图</a-button>
              </a-upload>
              <span class="screenshot-hint">可直接识别整张截图，也可先裁剪；识别后的正文会保存为独立攻略。</span>
            </div>
            <div v-for="(shot, shotIdx) in screenshots" :key="shotIdx" class="screenshot-item">
              <div class="screenshot-header">
                <span class="screenshot-label">截图 #{{ shotIdx + 1 }}</span>
                <a-space>
                  <a-button size="small" :loading="shot.ocrLoading" :disabled="loading" @click="runWholeOCR(shotIdx)">识别整张</a-button>
                  <a-button size="small" type="primary" @click="openCropModal(shotIdx)" :disabled="loading"> 裁剪区域</a-button>
                  <a-button size="small" danger @click="removeScreenshot(shotIdx)" :disabled="loading">删除截图</a-button>
                </a-space>
              </div>
              <img :src="shot.base64" class="screenshot-thumb" alt="截图预览" />
              <div v-if="shot.crops.length" class="crop-list">
                <div v-for="(crop, cropIdx) in shot.crops" :key="cropIdx" class="crop-item">
                  <div class="crop-header">
                    <span class="crop-label">区域 {{ cropIdx + 1 }}</span>
                    <a-tag v-if="crop.label" color="blue">{{ crop.label }}</a-tag>
                    <a-space size="small">
                      <a-button size="small" :loading="crop.ocrLoading" @click="runCropOCR(shotIdx, cropIdx)" :disabled="loading">识别</a-button>
                      <a-button size="small" danger @click="removeCrop(shotIdx, cropIdx)" :disabled="loading">删除</a-button>
                    </a-space>
                  </div>
                  <img :src="crop.dataUrl" class="crop-thumb" alt="裁剪区域" />
                  <div v-if="crop.ocrText" class="crop-ocr-text">{{ crop.ocrText }}</div>
                </div>
              </div>
            </div>
          </div>
          <div v-if="extractMessage" :class="['extract-message', extractSuccess ? 'extract-success' : 'extract-error']">{{ extractMessage }}</div>
          <ReferenceCards v-model:sources="referenceSources" v-model:candidates="referenceCandidates" :city="formData.city" :busy="refsAnalyzing || loading || intakeLoading" :error="refsError" @analyze="prepareReferences" />
          <div class="reference-footer">
            <div v-if="hasReferenceSources" class="reference-mode-control">
              <label for="reference-mode">如何使用攻略</label>
              <select id="reference-mode" class="reference-mode-select" v-model="formData.reference_mode" :disabled="loading">
                <option value="hybrid">攻略为主，AI 补充</option>
                <option value="flexible">只参考风格和偏好</option>
                <option value="strict">尽量按攻略安排</option>
              </select>
            </div>
            <button v-if="hasReferenceSources || screenshots.length" type="button" class="reference-text-button danger" :disabled="loading" @click="clearAllReferences">清空参考</button>
          </div>
        </div>
      </div>
      <section v-if="!intakeHistory.length" v-reveal class="travel-scenes" aria-label="旅行场景">
        <span class="scene-label">还没想好怎么玩？从一个旅行主题开始</span>
        <div class="scene-grid"><button v-for="scene in travelScenes" :key="scene.title" type="button" :disabled="loading || intakeLoading || referenceBusy" @click="tripIdea = scene.idea">
          <svg viewBox="0 0 24 24" fill="none" aria-hidden="true"><path :d="scene.icon" stroke="currentColor" stroke-width="1.4" stroke-linecap="round" stroke-linejoin="round" /></svg>
          <strong>{{ scene.title }}</strong><small>{{ scene.caption }}</small><span class="scene-arrow" aria-hidden="true">↗</span>
        </button></div>
      </section>
      <div v-if="intakeHistory.length" class="intake-thread" aria-live="polite">
        <div v-for="(turn, index) in intakeHistory" :key="index" :class="['intake-bubble', turn.role]">
          <span class="bubble-author">{{ turn.role === 'user' ? '你' : '旅行 Agent' }}</span>
          <p>{{ turn.content }}</p>
        </div>
        <div v-if="intakeResult" class="intake-summary">
          <span v-if="intakeResult.city"> {{ intakeResult.city }}</span>
          <span v-if="intakeResult.start_date"> {{ intakeResult.start_date }}{{ intakeResult.end_date && intakeResult.end_date !== intakeResult.start_date ? ` — ${intakeResult.end_date}` : '' }}</span>
          <span v-if="intakeResult.travel_days">{{ intakeResult.travel_days }} 天</span>
          <span v-if="intakeResult.party_size">{{ intakeResult.party_size }} 人</span>
          <span v-if="intakeResult.room_count">每晚 {{ intakeResult.room_count }} 间</span>
          <span v-if="intakeResult.budget_mode === 'deferred'">预算稍后再定</span>
          <span v-else-if="intakeResult.destination_budget != null">目的地内全体预算 ¥{{ intakeResult.destination_budget }}</span>
          <span v-if="!intakeResult.ready">其余信息可以继续告诉我</span>
        </div>
        <button v-if="budgetPending" class="reference-text-button" type="button" :disabled="intakeLoading || loading || referenceBusy" @click="submitIdea('先规划行程，预算稍后再定。')">先规划，预算稍后再定 →</button>
      </div>
      <div class="advanced-entry">
        <button type="button" class="advanced-toggle" :aria-expanded="detailsOpen" @click="detailsOpen = !detailsOpen">
          <span>{{ detailsOpen ? '收起详细设置' : '详细设置' }}</span><span aria-hidden="true">{{ detailsOpen ? '−' : '+' }}</span>
        </button>
        <span>日期、交通与住宿，按需填写</span>
      </div>
      <div class="settings-disclosure" :class="{ 'is-open': detailsOpen }" :inert="!detailsOpen" :aria-hidden="!detailsOpen">
        <div class="settings-disclosure-inner">
          <AdvancedTripSettings v-if="detailsMounted" :form-data="formData" :loading="loading" @submit="handleSubmit" />
        </div>
      </div>
      <a-modal
        v-model:open="cropModalVisible"
        title=" 拖动鼠标框选要识别的区域"
        width="800px"
        :footer="null"
        @cancel="closeCropModal"
      >
        <div class="crop-canvas-wrapper">
          <canvas
            ref="cropCanvasRef"
            @mousedown="onCropMouseDown"
            @mousemove="onCropMouseMove"
            @mouseup="onCropMouseUp"
            @mouseleave="onCropMouseUp"
            class="crop-canvas"
          />
        </div>
        <div class="crop-modal-actions">
          <a-space>
            <a-input v-model:value="cropLabel" placeholder="区域标签（如：美食推荐、景点信息）" style="width: 240px;" size="small" />
            <a-button @click="closeCropModal">取消</a-button>
            <a-button type="primary" @click="confirmCrop" :disabled="!cropHasSelection">确认裁剪</a-button>
          </a-space>
        </div>
      </a-modal>
      <div v-if="loading" class="loading-container" role="status" aria-live="polite">
        <a-spin size="large" />
        <p class="loading-status">{{ planningStage }} · 已等待 {{ elapsedSeconds }} 秒</p>
        <p class="loading-hint">正在查询真实地点并安排路线，完成后自动保存为独立旅行。刷新或稍后回到此页会继续显示进度。</p>
        <a-button v-if="planRunId" size="small" class="loading-cancel" :loading="cancellingPlan" @click="cancelPlanning">取消生成</a-button>
      </div>
    </div></section>
    <aside class="trip-brief" aria-label="当前旅行草稿">
      <div class="brief-topline"><span>这次旅行</span><span class="draft-state">草稿</span></div>
      <h2>{{ formData.city || '目的地，等你决定' }}</h2>
      <p>{{ formData.start_date ? formData.start_date.format('MM月DD日') : '出发日期待定' }} <span>—</span> {{ formData.end_date ? formData.end_date.format('MM月DD日') : '返程日期待定' }}</p>
      <div class="route-sketch" aria-label="每日路线示意，非实际导航">
        <svg viewBox="0 0 300 140" fill="none" aria-hidden="true"><path d="M0 40H300M0 95H300M70 0V140M190 0V140" stroke="var(--color-border)"/><path d="M0 140 300 0M0 0 300 140" stroke="var(--color-border)" opacity=".45"/><path d="M45 95C45 35 115 38 135 65S225 120 252 43" stroke="var(--color-muted)" stroke-width="2" stroke-dasharray="4 5"/><circle cx="45" cy="95" r="6" fill="var(--color-surface)" stroke="var(--color-text)" stroke-width="2"/><circle cx="135" cy="65" r="6" fill="var(--color-surface)" stroke="var(--color-text)" stroke-width="2"/><circle cx="252" cy="43" r="6" fill="var(--color-text)"/></svg>
        <span>每日路线示意</span>
      </div>
      <dl class="brief-facts"><div><dt>旅行天数</dt><dd>{{ formData.start_date && formData.end_date ? `${formData.travel_days} 天` : '待确定' }}</dd></div><div><dt>交通方式</dt><dd>{{ formData.transportation }}</dd></div><div><dt>参考攻略</dt><dd>{{ referenceSources.length ? `${referenceSources.length} 篇已添加` : '可添加收藏的攻略' }}</dd></div></dl>
      <div class="brief-preferences"><span v-if="!formData.preferences.length">告诉我你喜欢的旅行节奏</span><span v-for="preference in formData.preferences" :key="preference">{{ preference }}</span></div>
      <div class="trip-output"><strong>规划完成后，你会得到</strong><p>每日行程与地图路线<br />游览和交通时间 · 费用参考</p></div>
    </aside>
    </div>

    <TripLibrary ref="library" @loaded="trips => latestTrip = trips[0] || null" />
  </div>
</template>

<script setup lang="ts">
import { ref, reactive, watch, onMounted, onUnmounted, computed } from 'vue'
import { useRouter } from 'vue-router'
import message from 'ant-design-vue/es/message'
import { interpretTripIdea, apiUrl } from '@/services/api'
import type { ChatTurn, TripFormData, TripIntakeData, ReferenceSource, ReferenceCandidate, PlanningRun } from '@/types'
import { applyIntakeCosts, intakeCostConditions, needsBudgetClarification } from '@/utils/intakeCosts'
import { lazyFeature } from '@/utils/lazyFeature'
import { readApiJson } from '@/utils/httpResponse'
import { runMissing } from '@/utils/serviceError'
import { connectPlan, readPendingPlan, clearPendingPlan, PENDING_PLAN_KEY, type PendingPlan } from '@/utils/pendingPlan'
import type { TripFormDraft } from '@/utils/tripDraft'
import ReferenceCards from '@/components/ReferenceCards.vue'
import TripLibrary from '@/components/TripLibrary.vue'
import type { SavedTripSummary } from '@/types'
import { analyzeReferences, selectSavedTrip, startCreateRun, readCreateRun, cancelCreateRun, watchRun } from '@/services/trips'
import type { Dayjs } from 'dayjs'
import dayjs from 'dayjs'
import 'dayjs/locale/zh-cn'

dayjs.locale('zh-cn')

const router = useRouter()
const AdvancedTripSettings = lazyFeature(() => import('@/components/AdvancedTripSettings.vue'), '详细设置')
const loading = ref(false)
const library = ref<InstanceType<typeof TripLibrary> | null>(null)
const latestTrip = ref<SavedTripSummary | null>(null)
const pendingPlan = ref<PendingPlan | null>(null)
const recoveryError = ref('')
const planningStage = ref('正在规划你的旅程')
const intakeLoading = ref(false)
const detailsOpen = ref(false)
const detailsMounted = ref(false)
watch(detailsOpen, open => { if (open) detailsMounted.value = true })
const referenceOpen = ref(false)
const referenceTab = ref<'paste' | 'link' | 'image'>('paste')
const travelScenes = [
  { title: '城市漫游', caption: '人文景点 · 本地美食', idea: '想安排一次城市漫游，喜欢博物馆和本地美食，每天不要太赶。', icon: 'M3 21h18M5 21V9h6v12m0-17h7v17M7 12h2m-2 4h2m5-8h2m-2 4h2m-2 4h2' },
  { title: '亲子时光', caption: '少些奔波 · 多些陪伴', idea: '想安排一次亲子旅行，减少长时间交通，多安排适合孩子的活动和休息。', icon: 'M4 21v-5a4 4 0 0 1 8 0v5m0 0v-3a3 3 0 0 1 6 0v3M11 7a3 3 0 1 1-6 0 3 3 0 0 1 6 0Zm7 5a2 2 0 1 1-4 0 2 2 0 0 1 4 0Z' },
  { title: '自然放空', caption: '山野散步 · 轻松节奏', idea: '想去自然风光好的地方放空，安排轻松的散步路线，留出休息和拍照时间。', icon: 'm2 20 7-13 5 8 3-5 5 10H2Zm5-9 2 2 2-2M18 3h.01' }
]
const disabledDate = (current: Dayjs) => current.isBefore(dayjs().startOf('day'))
const tripIdea = ref('')
const intakeHistory = ref<ChatTurn[]>([])
const intakeResult = ref<TripIntakeData | null>(null)
const elapsedSeconds = ref(0)
let loadingTimer: ReturnType<typeof setInterval> | null = null

// ============ 参考来源相关 ============

// URL 提取
const urlList = ref([""])
const extractingUrlIdx = ref(-1)
const extractMessage = ref("")
const extractSuccess = ref(false)

const addUrl = () => urlList.value.push("")
const removeUrl = (idx: number) => urlList.value.splice(idx, 1)

// 截图管理
interface CropRegion {
  dataUrl: string      // 裁剪后的 base64
  x: number; y: number; w: number; h: number
  ocrText: string
  ocrLoading: boolean
  label: string
}
interface Screenshot {
  ocrLoading?: boolean
  base64: string
  crops: CropRegion[]
}
const screenshots = ref<Screenshot[]>([])

// 裁剪模态框
const cropModalVisible = ref(false)
const cropCanvasRef = ref<HTMLCanvasElement | null>(null)
const cropActiveShotIdx = ref(-1)
const cropLabel = ref("")
let cropCtx: CanvasRenderingContext2D | null = null
let cropImg: HTMLImageElement | null = null
const cropStart = ref({ x: 0, y: 0 })
const cropEnd = ref({ x: 0, y: 0 })
const cropHasSelection = ref(false)
const isDragging = ref(false)

const draftKey = 'tripFormDraft'
const readDraft = (): Partial<TripFormData> & { intake_needs_budget?: boolean; intake_history?: ChatTurn[] } => {
  try {
    const parsed = JSON.parse(sessionStorage.getItem(draftKey) || '{}')
    return parsed && typeof parsed === 'object' && !Array.isArray(parsed) ? parsed : {}
  } catch {
    return {}
  }
}
const draft = readDraft()
const intakeNeedsBudget = ref(draft.intake_needs_budget === true)
intakeHistory.value = Array.isArray(draft.intake_history) ? draft.intake_history
  .filter(turn => turn && ['user', 'assistant'].includes(turn.role) && typeof turn.content === 'string')
  .slice(-12).map(turn => ({ role: turn.role, content: turn.content.slice(0, 1500) })) : []
// Date pickers take Dayjs | undefined; the form keeps null for "not chosen".
const pickerValue = (value: unknown) => (value ?? undefined) as Dayjs | undefined
const setDate = (key: 'start_date' | 'end_date', value: unknown) => { formData[key] = value ? dayjs(value as Dayjs) : null }
const draftDate = (value: unknown): Dayjs | null =>
  typeof value === 'string' && dayjs(value).isValid() ? dayjs(value) : null

const formData = reactive<TripFormDraft>({
  city: draft.city || '',
  start_date: draftDate(draft.start_date),
  end_date: draftDate(draft.end_date),
  travel_days: draft.travel_days || 1,
  daily_start_time: draft.daily_start_time || '09:00',
  daily_end_time: draft.daily_end_time || '20:00',
  buffer_minutes: draft.buffer_minutes ?? 15,
  party_size: draft.party_size ?? null,
  room_count: draft.room_count ?? null,
  destination_budget: draft.destination_budget ?? null,
  budget_mode: draft.budget_mode === 'deferred' ? 'deferred' : 'active',
  transportation: draft.transportation || '公共交通',
  accommodation: draft.accommodation || '经济型酒店',
  preferences: Array.isArray(draft.preferences) ? draft.preferences : [],
  free_text_input: draft.free_text_input || '',
  reference_type: draft.reference_type || 'none',
  reference_content: draft.reference_content || '',
  reference_mode: draft.reference_mode || 'flexible',
  reference_sources: draft.reference_sources || [],
  reference_candidates: draft.reference_candidates || []
})
const budgetPending = computed(() => needsBudgetClarification(formData, formData.travel_days, intakeNeedsBudget.value))

const pasteText = ref('')
const referenceSources = ref<ReferenceSource[]>(draft.reference_sources?.length ? draft.reference_sources : draft.reference_content?.trim() ? [{ id: crypto.randomUUID(), type: 'text', title: '之前的攻略', url: '', text: draft.reference_content }] : [])
const referenceCandidates = ref<ReferenceCandidate[]>(draft.reference_candidates || [])
const refsAnalyzing = ref(false)
const refsError = ref('')
let analyzedKey = ''
const refsKey = () => JSON.stringify([formData.city, referenceSources.value])
analyzedKey = draft.reference_candidates?.length ? refsKey() : ''
let firstSourceWatch = true
const rememberedCandidates = ref<ReferenceCandidate[]>(draft.reference_candidates || [])
function addSource(text: string, title: string, type: ReferenceSource['type'], url = '') {
  const existing = referenceSources.value.find(source => type !== 'text' && source.type === type && (url ? source.url === url : source.title === title))
  if (existing) { existing.text = text.trim().slice(0, 15000); return }
  if (referenceSources.value.length >= 12) { message.warning('最多添加 12 篇攻略'); return }
  referenceSources.value.push({ id: crypto.randomUUID(), type, title, url, text: text.trim().slice(0, 15000) })
}
function addPastedSource() {
  if (!pasteText.value.trim()) return
  if (referenceSources.value.length >= 12) { message.warning('最多添加 12 篇攻略'); return }
  addSource(pasteText.value, `攻略 ${referenceSources.value.length + 1}`, 'text')
  pasteText.value = ''
  formData.reference_mode = formData.reference_mode === 'flexible' ? 'hybrid' : formData.reference_mode
}
watch(referenceSources, () => {
  if (!firstSourceWatch) { analyzedKey = ''; referenceCandidates.value = [] }
  firstSourceWatch = false
  refsError.value = ''
  formData.reference_sources = referenceSources.value
  formData.reference_content = referenceSources.value.map(s => `【${s.title}】\n${s.text}`).join('\n\n')
}, { deep: true, immediate: true })
watch(referenceCandidates, value => { formData.reference_candidates = value; if (value.length) rememberedCandidates.value = JSON.parse(JSON.stringify(value)) }, { deep: true })
watch(() => formData.city, () => { analyzedKey = ''; referenceCandidates.value = [] })
async function prepareReferences(): Promise<boolean> {
  if (pasteText.value.trim()) { addPastedSource(); await Promise.resolve(); if (pasteText.value.trim()) return false }
  if (!referenceSources.value.length) return true
  if (!formData.city.trim()) { refsError.value = '请先确认目的地城市'; return false }
  if (analyzedKey === refsKey()) return true
  refsAnalyzing.value = true; refsError.value = ''
  const key = refsKey()
  try {
    const data = await analyzeReferences(formData.city, referenceSources.value)
    if (key !== refsKey()) { refsError.value = '攻略或城市已变化，请重新提取'; return false }
    referenceCandidates.value = data.candidates.map(candidate => {
      const previous = rememberedCandidates.value.find(c => c.id === candidate.id)
      if (!previous) return candidate
      const place = candidate.options.find(p => p.poi_id && p.poi_id === previous.place?.poi_id)
      return { ...candidate, priority: previous.priority, ...(place ? { place, status: 'matched' as const } : {}) }
    })
    analyzedKey = key
    if (!data.candidates.length) message.info('没有提取到具体地点，原文仍会作为偏好参考')
    return true
  } catch(e: any) { refsError.value = e.message; return false }
  finally { refsAnalyzing.value = false }
}
const referenceBusy = computed(() => refsAnalyzing.value || extractingUrlIdx.value >= 0 || screenshots.value.some(s => s.ocrLoading || s.crops.some(c => c.ocrLoading)))

const handleIdeaKeydown = (event: KeyboardEvent) => {
  if (event.isComposing || window.matchMedia('(pointer: coarse)').matches) return
  event.preventDefault()
  void submitIdea()
}

const startPlanning = async () => {
  if (intakeLoading.value || loading.value || referenceBusy.value) return
  if (await resumePendingIfAny()) return
  if (tripIdea.value.trim()) { await submitIdea(); return }
  if (formData.city.trim() && formData.start_date && formData.end_date) { await handleSubmit(); return }
  tripIdea.value = '帮我安排这次旅行。'
  await submitIdea()
}

const submitIdea = async (suggested?: string) => {
  const content = (suggested || tripIdea.value).trim()
  if (!content || intakeLoading.value || loading.value || referenceBusy.value) return
  if (await resumePendingIfAny()) return
  intakeHistory.value.push({ role: 'user', content })
  intakeHistory.value = intakeHistory.value.slice(-12)
  tripIdea.value = ''
  intakeLoading.value = true
  try {
    const supplied = [formData.city && `目的地：${formData.city}`, formData.start_date && `出发日期：${formData.start_date.format('YYYY-MM-DD')}`, formData.end_date && `返程日期：${formData.end_date.format('YYYY-MM-DD')}`, formData.preferences.length && `偏好：${formData.preferences.join('、')}`, `交通：${formData.transportation}`, `住宿：${formData.accommodation}`, ...intakeCostConditions(formData)].filter(Boolean).join('；')
    const history = intakeHistory.value.map(turn => ({ ...turn }))
    if (supplied && history.length) history[history.length - 1].content += `\n已填写条件（本条新描述中的更改优先）：${supplied}`
    const response = await interpretTripIdea(history)
    if (!response.success || !response.data) throw new Error(response.message || '暂时无法理解需求')
    const result = response.data
    intakeResult.value = result
    intakeHistory.value.push({ role: 'assistant', content: result.reply })
    intakeHistory.value = intakeHistory.value.slice(-12)
    formData.city = result.city || ''
    formData.start_date = result.start_date ? dayjs(result.start_date) : null
    formData.end_date = result.end_date ? dayjs(result.end_date) : null
    if (result.travel_days) formData.travel_days = result.travel_days
    formData.transportation = result.transportation || '公共交通'
    formData.accommodation = result.accommodation || '经济型酒店'
    formData.preferences = result.preferences
    formData.free_text_input = result.free_text_input
    applyIntakeCosts(formData, result)
    if (result.budget_clarification !== undefined) intakeNeedsBudget.value = result.budget_clarification
    if (!result.ready) return

    intakeLoading.value = false
    await handleSubmit()
  } catch (error: any) {
    intakeHistory.value.pop()
    tripIdea.value = content
    message.error(error.message || '暂时无法理解需求，请重试或详细设置')
  } finally {
    intakeLoading.value = false
  }
}

watch([formData, intakeNeedsBudget, intakeHistory], () => {
  try {
    sessionStorage.setItem(draftKey, JSON.stringify({
      ...formData,
      intake_needs_budget: intakeNeedsBudget.value,
      intake_history: intakeHistory.value,
      start_date: formData.start_date?.format('YYYY-MM-DD') || '',
      end_date: formData.end_date?.format('YYYY-MM-DD') || ''
    }))
  } catch { /* 浏览器禁用存储时仍可正常填写 */ }
}, { deep: true, immediate: true })

const clearAllReferences = () => {
  referenceSources.value = []
  referenceCandidates.value = []
  pasteText.value = ''
  formData.reference_content = ''
  formData.reference_type = 'none'
  formData.reference_mode = 'flexible'
  urlList.value = ['']
  screenshots.value = []
  referenceTab.value = 'paste'
  extractMessage.value = ''
  extractSuccess.value = false
  message.info('已清空全部参考内容')
}

// 监听参考内容变化
const hasReferenceSources = ref(false)
watch(() => formData.reference_content, (val) => {
  hasReferenceSources.value = !!(val && val.trim())
}, { immediate: true })

const toggleReferencePanel = () => {
  if (!referenceOpen.value && !hasReferenceSources.value && formData.reference_mode === 'flexible') {
    formData.reference_mode = 'hybrid'
  }
  referenceOpen.value = !referenceOpen.value
}

// 监听日期变化,自动计算旅行天数
watch([() => formData.start_date, () => formData.end_date], ([start, end]) => {
  if (start && end) {
    const days = end.diff(start, 'day') + 1
    if (days > 0 && days <= 30) {
      formData.travel_days = days
    } else if (days > 30) {
      message.warning('旅行天数不能超过30天')
      formData.end_date = null
    } else {
      message.warning('结束日期不能早于开始日期')
      formData.end_date = null
    }
  }
})

// ============ URL 提取 ============

const appendToReference = (text: string, sourceLabel: string, type: ReferenceSource['type'] = 'screenshot', url = '') => {
  addSource(text, sourceLabel, type, url)
  formData.reference_type = type === 'screenshot' ? 'screenshot' : 'url'
}

const handleExtractUrl = async (idx: number) => {
  const url = urlList.value[idx]
  if (!url) { message.warning('请先输入链接'); return }

  extractingUrlIdx.value = idx
  extractMessage.value = ''
  extractSuccess.value = false

  try {
    const res = await fetch(apiUrl('/api/trip/extract-url'), {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ url })
    })
    const data = await readApiJson(res)

    if (data.success && data.data?.content) {
      appendToReference(data.data.content, `链接 #${idx + 1}`, 'url', url)
      referenceTab.value = 'paste'
      extractMessage.value = `链接 #${idx + 1} 提取成功`
      extractSuccess.value = true
      message.success(`链接 #${idx + 1} 内容提取成功`)
    } else {
      extractMessage.value = `链接 #${idx + 1}: ` + (data.message || '提取失败')
      extractSuccess.value = false
      message.warning(`链接 #${idx + 1} 提取失败`)
    }
  } catch (err: any) {
    extractMessage.value = err.message || '网络请求失败，请检查后端是否启动'
    extractSuccess.value = false
    message.error(extractMessage.value)
  } finally {
    extractingUrlIdx.value = -1
  }
}

// ============ 截图管理 ============

const handleScreenshotAdd = async (file: File): Promise<boolean> => {
  const allowedTypes = ['image/png', 'image/jpeg', 'image/jpg', 'image/webp']
  if (!allowedTypes.includes(file.type)) { message.error('仅支持 PNG / JPG / WebP 格式'); return false }
  if (file.size > 10 * 1024 * 1024) { message.error('图片不能超过 10MB'); return false }

  const reader = new FileReader()
  const base64 = await new Promise<string>((resolve, reject) => {
    reader.onload = () => resolve(reader.result as string)
    reader.onerror = () => reject(new Error('读取失败'))
    reader.readAsDataURL(file)
  })

  screenshots.value.push({ base64, crops: [] })
  message.success('截图上传成功，可点击"裁剪区域"选择要识别的部分')
  return false
}

const removeScreenshot = (idx: number) => screenshots.value.splice(idx, 1)

// ============ 裁剪模态框 ============

const openCropModal = (shotIdx: number) => {
  cropActiveShotIdx.value = shotIdx
  cropLabel.value = ''
  cropStart.value = { x: 0, y: 0 }
  cropEnd.value = { x: 0, y: 0 }
  cropHasSelection.value = false
  cropModalVisible.value = true

  // 下一帧渲染 canvas
  setTimeout(() => {
    const canvas = cropCanvasRef.value
    if (!canvas) return
    const shot = screenshots.value[shotIdx]
    if (!shot) return

    cropImg = new Image()
    cropImg.onload = () => {
      const maxW = 760
      const scale = Math.min(maxW / cropImg!.width, 1)
      canvas.width = cropImg!.width * scale
      canvas.height = cropImg!.height * scale
      cropCtx = canvas.getContext('2d')
      cropCtx!.drawImage(cropImg!, 0, 0, canvas.width, canvas.height)
    }
    cropImg.src = shot.base64
  }, 100)
}

const closeCropModal = () => {
  cropModalVisible.value = false
  cropActiveShotIdx.value = -1
  cropImg = null
  cropCtx = null
}

const getCanvasPos = (e: MouseEvent): { x: number; y: number } => {
  const canvas = cropCanvasRef.value!
  const rect = canvas.getBoundingClientRect()
  return {
    x: (e.clientX - rect.left) * canvas.width / rect.width,
    y: (e.clientY - rect.top) * canvas.height / rect.height
  }
}

const onCropMouseDown = (e: MouseEvent) => {
  isDragging.value = true
  const pos = getCanvasPos(e)
  cropStart.value = pos
  cropEnd.value = pos
  cropHasSelection.value = false
}

const onCropMouseMove = (e: MouseEvent) => {
  if (!isDragging.value || !cropCtx || !cropImg) return
  const pos = getCanvasPos(e)
  cropEnd.value = pos

  const canvas = cropCanvasRef.value!
  cropCtx.clearRect(0, 0, canvas.width, canvas.height)
  cropCtx.drawImage(cropImg, 0, 0, canvas.width, canvas.height)

  // 绘制选择框
  const x = Math.min(cropStart.value.x, cropEnd.value.x)
  const y = Math.min(cropStart.value.y, cropEnd.value.y)
  const w = Math.abs(cropEnd.value.x - cropStart.value.x)
  const h = Math.abs(cropEnd.value.y - cropStart.value.y)

  if (w > 5 && h > 5) {
    cropCtx.strokeStyle = '#176b65'
    cropCtx.lineWidth = 2
    cropCtx.setLineDash([6, 3])
    cropCtx.strokeRect(x, y, w, h)
    cropCtx.fillStyle = 'rgba(23, 107, 101, 0.1)'
    cropCtx.fillRect(x, y, w, h)
    cropHasSelection.value = true
  }
}

const onCropMouseUp = () => {
  isDragging.value = false
}

const confirmCrop = () => {
  if (!cropHasSelection.value || !cropImg || cropActiveShotIdx.value < 0) return

  const canvas = cropCanvasRef.value!
  const scale = cropImg.width / canvas.width

  const x = Math.min(cropStart.value.x, cropEnd.value.x) * scale
  const y = Math.min(cropStart.value.y, cropEnd.value.y) * scale
  const w = Math.abs(cropEnd.value.x - cropStart.value.x) * scale
  const h = Math.abs(cropEnd.value.y - cropStart.value.y) * scale

  // 裁剪到临时 canvas
  const tmpCanvas = document.createElement('canvas')
  tmpCanvas.width = w
  tmpCanvas.height = h
  const tmpCtx = tmpCanvas.getContext('2d')!
  tmpCtx.drawImage(cropImg, x, y, w, h, 0, 0, w, h)
  const dataUrl = tmpCanvas.toDataURL('image/png')

  screenshots.value[cropActiveShotIdx.value].crops.push({
    dataUrl,
    x, y, w, h,
    ocrText: '',
    ocrLoading: false,
    label: cropLabel.value
  })

  closeCropModal()
  message.success('裁剪区域已添加，点击"识别"进行 OCR')
}

const removeCrop = (shotIdx: number, cropIdx: number) => {
  screenshots.value[shotIdx].crops.splice(cropIdx, 1)
}

const runCropOCR = async (shotIdx: number, cropIdx: number) => {
  const crop = screenshots.value[shotIdx].crops[cropIdx]
  crop.ocrLoading = true

  try {
    const res = await fetch(apiUrl('/api/trip/extract-image'), {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ image_base64: crop.dataUrl })
    })
    const data = await readApiJson(res)

    if (data.success && data.data?.content) {
      crop.ocrText = data.data.content
      const label = crop.label || `截图 #${shotIdx + 1} 区域 ${cropIdx + 1}`
      appendToReference(data.data.content, label)
      referenceTab.value = 'paste'
      extractMessage.value = `"${label}" 识别成功`
      extractSuccess.value = true
      message.success('区域文字识别成功')
    } else {
      message.warning(data.message || 'OCR识别失败')
    }
  } catch (err: any) {
    message.error(err.message || 'OCR请求失败')
  } finally {
    crop.ocrLoading = false
  }
}

const runWholeOCR = async (shotIdx: number) => {
  const shot = screenshots.value[shotIdx]
  shot.ocrLoading = true
  try {
    const response = await fetch(apiUrl('/api/trip/extract-image'), { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ image_base64: shot.base64 }) })
    const data = await readApiJson(response)
    if (!data.success || !data.data?.content) throw new Error(data.message || '截图识别失败')
    appendToReference(data.data.content, `截图 ${shotIdx + 1}`)
    message.success('整张截图已识别，可在攻略卡片中修正正文')
  } catch(e: any) { message.error(e.message || '截图识别失败') }
  finally { shot.ocrLoading = false }
}

const handleSubmit = async () => {
  if (loading.value || referenceBusy.value) return
  if (await resumePendingIfAny()) return
  if (budgetPending.value) {
    message.info('请补充预算信息，或选择“先规划，预算稍后再定”。也可以在详细设置中填写人数、房间数和目的地内全体预算。')
    document.getElementById('trip-idea')?.focus()
    return
  }
  if (!formData.city.trim()) {
    message.error('请告诉我目的地城市')
    return
  }
  if (!formData.start_date || !formData.end_date) {
    message.error('请选择日期')
    return
  }

  planningStage.value = '正在提取攻略地点'
  if (pasteText.value.trim() || referenceSources.value.length) referenceOpen.value = true
  if (!await prepareReferences()) { referenceOpen.value = true; return }
  if (referenceCandidates.value.some(c => c.priority === 'required' && c.status !== 'matched')) {
    referenceOpen.value = true
    message.warning('请先确认必去地点的地图匹配，或改为可选')
    return
  }
  // 自动判断参考来源类型
  if (formData.reference_content && formData.reference_content.trim()) {
    formData.reference_type = screenshots.value.some(shot => shot.crops.some(crop => !!crop.ocrText)) ? 'screenshot' : 'url'
  }

  const requestData: TripFormData = {
    city: formData.city,
    start_date: formData.start_date.format('YYYY-MM-DD'),
    end_date: formData.end_date.format('YYYY-MM-DD'),
    travel_days: formData.travel_days,
    daily_start_time: formData.daily_start_time,
    daily_end_time: formData.daily_end_time,
    buffer_minutes: formData.buffer_minutes,
    party_size: formData.party_size,
    room_count: formData.room_count,
    destination_budget: formData.destination_budget,
    budget_mode: formData.budget_mode,
    transportation: formData.transportation,
    accommodation: formData.accommodation,
    preferences: formData.preferences,
    free_text_input: formData.free_text_input,
    reference_type: formData.reference_type,
    reference_content: formData.reference_content,
    reference_mode: formData.reference_mode,
    reference_sources: referenceSources.value,
    reference_candidates: referenceCandidates.value
  }

  await runPlanning(requestData)
}

const planRunId = ref('')
const cancellingPlan = ref(false)
let homeDisposed = false

function stopPlanningUi() {
  if (loadingTimer) clearInterval(loadingTimer)
  loadingTimer = null
  loading.value = false
  planRunId.value = ''
  cancellingPlan.value = false
}

function refreshPendingPlan() {
  pendingPlan.value = readPendingPlan(localStorage)
}

async function runPlanning(request: TripFormData | null) {
  if (loading.value || homeDisposed) return
  loading.value = true
  recoveryError.value = ''
  planningStage.value = request ? '正在提交规划需求' : '正在连接上次规划'
  elapsedSeconds.value = 0
  loadingTimer = setInterval(() => elapsedSeconds.value += 1, 1000)
  try {
    const run = await connectPlan(localStorage, request, { read: readCreateRun, submit: startCreateRun }, () => crypto.randomUUID())
    if (run && !homeDisposed) await followPlanRun(run)
  } catch (error: any) {
    if (homeDisposed) return
    refreshPendingSafely()
    if (runMissing(error) && pendingPlan.value?.accepted) {
      await clearPendingPlan(localStorage, pendingPlan.value.id)
      pendingPlan.value = null
      message.warning('上次规划记录已不存在，请重新提交需求。')
    } else {
      recoveryError.value = pendingPlan.value ? '暂时无法确认上次规划的状态，请恢复连接后继续查看。系统会沿用原需求，不会另开一份规划。' : recoveryError.value
      message.error(error.message || '暂时无法连接规划服务，请稍后继续查看')
    }
  } finally {
    refreshPendingSafely()
    stopPlanningUi()
  }
}

function refreshPendingSafely() {
  try { refreshPendingPlan() }
  catch (error: any) { recoveryError.value = error.message || '无法读取浏览器恢复记录' }
}

async function resumePendingIfAny(): Promise<boolean> {
  if (loading.value) return true
  try { refreshPendingPlan() }
  catch (error: any) { recoveryError.value = error.message || '无法读取浏览器恢复记录'; return true }
  if (!pendingPlan.value) { recoveryError.value = ''; return false }
  await runPlanning(null)
  return true
}

function pendingStorageChanged(event: StorageEvent) {
  if (event.key === PENDING_PLAN_KEY || event.key === null) refreshPendingSafely()
}

async function followPlanRun(run: PlanningRun) {
  planRunId.value = run.id
  elapsedSeconds.value = run.elapsed_seconds || 0
  planningStage.value = run.stage
  const final = await watchRun(run.id, () => readCreateRun(run.id), latest => {
    planningStage.value = latest.stage || planningStage.value
  }, () => homeDisposed)
  if (homeDisposed) return
  if (final.status !== 'succeeded' || !final.result) {
    await clearPendingPlan(localStorage, run.id)
    throw new Error(final.error || (final.status === 'cancelled' ? '已取消生成' : '生成未完成，请稍后重试'))
  }
  selectSavedTrip(final.result)
  await clearPendingPlan(localStorage, run.id)
  sessionStorage.removeItem('tripChat')
  message.success('旅行计划已生成并保存')
  router.push({ path: '/result', query: { trip: final.result.id } })
}

async function cancelPlanning() {
  if (!planRunId.value) return
  cancellingPlan.value = true
  try { await cancelCreateRun(planRunId.value) }
  catch (e: any) { message.error(e.message); cancellingPlan.value = false }
}

// A generation keeps running on the server; reattach after a refresh or navigation.
onMounted(async () => {
  window.addEventListener('storage', pendingStorageChanged)
  await resumePendingIfAny()
})

onUnmounted(() => {
  homeDisposed = true
  window.removeEventListener('storage', pendingStorageChanged)
  if (loadingTimer) clearInterval(loadingTimer)
})
</script>

<style scoped>
.planning-recovery{display:flex;align-items:center;justify-content:space-between;gap:20px;max-width:1120px;margin:0 auto 24px;padding:20px 24px;border:1px solid var(--color-border);border-radius:16px;background:var(--color-surface);color:var(--color-text)}
.planning-recovery strong{font-size:14px}.planning-recovery p{font-size:13px;color:var(--color-muted);line-height:1.7;margin:6px 0 0}.planning-recovery button{flex-shrink:0;border:1px solid var(--color-border);border-radius:22px;padding:10px 16px;background:var(--color-subtle);color:var(--color-text);font:inherit;font-size:13px;cursor:pointer}.planning-recovery button:focus-visible{outline:2px solid var(--travel-accent);outline-offset:3px}@media(max-width:640px){.planning-recovery{align-items:flex-start;flex-direction:column;padding:18px}}
.home-container {
  min-height: 100vh;
  background: var(--color-subtle);
  padding: 60px 20px;
  position: relative;
  overflow: hidden;
}

/* 背景装饰 */
.bg-decoration {
  position: absolute;
  top: 0;
  left: 0;
  width: 100%;
  height: 100%;
  pointer-events: none;
  overflow: hidden;
}

.circle {
  position: absolute;
  border-radius: 50%;
  background: rgba(255, 255, 255, 0.1);
  animation: float 20s infinite ease-in-out;
}

.circle-1 {
  width: 300px;
  height: 300px;
  top: -100px;
  left: -100px;
  animation-delay: 0s;
}

.circle-2 {
  width: 200px;
  height: 200px;
  top: 50%;
  right: -50px;
  animation-delay: 5s;
}

.circle-3 {
  width: 150px;
  height: 150px;
  bottom: -50px;
  left: 30%;
  animation-delay: 10s;
}

@keyframes float {
  0%, 100% {
    transform: translateY(0) rotate(0deg);
  }
  50% {
    transform: translateY(-30px) rotate(180deg);
  }
}

/* 页面标题 */
.page-header {
  text-align: center;
  margin-bottom: 50px;
  animation: fadeInDown 0.8s ease-out;
  position: relative;
  z-index: 1;
}

.icon-wrapper {
  margin-bottom: 20px;
}

.icon {
  font-size: 80px;
  display: inline-block;
  animation: bounce 2s infinite;
}

@keyframes bounce {
  0%, 100% {
    transform: translateY(0);
  }
  50% {
    transform: translateY(-20px);
  }
}

.page-title {
  font-size: 56px;
  font-weight: 800;
  color: var(--color-on-primary);
  margin-bottom: 16px;
  text-shadow: 3px 3px 6px rgba(0, 0, 0, 0.3);
  letter-spacing: 2px;
}

.page-subtitle {
  font-size: 20px;
  color: rgba(255, 255, 255, 0.95);
  margin: 0;
  font-weight: 300;
}

/* 表单卡片 */
.form-card {
  max-width: 1400px;
  margin: 0 auto;
  border-radius: 24px;
  box-shadow: 0 30px 80px rgba(0, 0, 0, 0.4);
  animation: fadeInUp 0.8s ease-out;
  position: relative;
  z-index: 1;
  backdrop-filter: blur(10px);
  background: rgba(255, 255, 255, 0.98) !important;
}

/* 表单分区 */
.form-section {
  margin-bottom: 32px;
  padding: 24px;
  background: var(--color-subtle);
  border-radius: 16px;
  border: 1px solid var(--color-border);
  transition: all 0.3s ease;
}

.form-section:hover {
  box-shadow: 0 8px 24px rgba(102, 126, 234, 0.15);
  transform: translateY(-2px);
}

.section-header {
  display: flex;
  align-items: center;
  margin-bottom: 20px;
  padding-bottom: 12px;
  border-bottom: 2px solid var(--color-border);
}

.section-icon {
  font-size: 24px;
  margin-right: 12px;
}

.section-title {
  font-size: 18px;
  font-weight: 600;
  color: var(--color-text);
}

/* 表单标签 */
.form-label {
  font-size: 15px;
  font-weight: 500;
  color: var(--color-text);
}

/* 自定义输入框 */
.custom-input :deep(.ant-input),
.custom-input :deep(.ant-picker) {
  border-radius: 12px;
  border: 2px solid var(--color-border);
  transition: all 0.3s ease;
}

.custom-input :deep(.ant-input:hover),
.custom-input :deep(.ant-picker:hover) {
  border-color: var(--color-border);
}

.custom-input :deep(.ant-input:focus),
.custom-input :deep(.ant-picker-focused) {
  border-color: var(--color-border);
  box-shadow: 0 0 0 3px rgba(102, 126, 234, 0.1);
}

/* 自定义选择框 */
.custom-select :deep(.ant-select-selector) {
  border-radius: 12px !important;
  border: 2px solid var(--color-border) !important;
  transition: all 0.3s ease;
}

.custom-select:hover :deep(.ant-select-selector) {
  border-color: var(--color-border) !important;
}

.custom-select :deep(.ant-select-focused .ant-select-selector) {
  border-color: var(--color-border) !important;
  box-shadow: 0 0 0 3px rgba(102, 126, 234, 0.1) !important;
}

/* 天数显示 - 紧凑版 */
.days-display-compact {
  display: flex;
  align-items: center;
  justify-content: center;
  height: 40px;
  padding: 8px 16px;
  background: var(--color-subtle);
  border-radius: 12px;
  color: var(--color-on-primary);
}

.days-display-compact .days-value {
  font-size: 24px;
  font-weight: 700;
  margin-right: 4px;
}

.days-display-compact .days-unit {
  font-size: 14px;
}

/* 偏好标签 */
.preference-tags {
  display: flex;
  flex-wrap: wrap;
  gap: 8px;
}

.custom-checkbox-group {
  display: flex;
  flex-wrap: wrap;
  gap: 8px;
  width: 100%;
}

.preference-tag :deep(.ant-checkbox-wrapper) {
  margin: 0 !important;
  padding: 8px 16px;
  border: 2px solid var(--color-border);
  border-radius: 20px;
  transition: all 0.3s ease;
  background: var(--color-surface);
  font-size: 14px;
}

.preference-tag :deep(.ant-checkbox-wrapper:hover) {
  border-color: var(--color-border);
  background: var(--color-surface);
}

.preference-tag :deep(.ant-checkbox-wrapper-checked) {
  border-color: var(--color-border);
  background: var(--color-subtle);
  color: var(--color-on-primary);
}

/* 自定义文本域 */
.custom-textarea :deep(.ant-input) {
  border-radius: 12px;
  border: 2px solid var(--color-border);
  transition: all 0.3s ease;
}

.custom-textarea :deep(.ant-input:hover) {
  border-color: var(--color-border);
}

.custom-textarea :deep(.ant-input:focus) {
  border-color: var(--color-border);
  box-shadow: 0 0 0 3px rgba(102, 126, 234, 0.1);
}

/* 参考来源 */
.ref-collapse {
  background: transparent;
  margin-bottom: 8px;
}

.ref-collapse :deep(.ant-collapse-item) {
  border: 1px solid var(--color-border);
  border-radius: 10px;
  margin-bottom: 8px;
  overflow: hidden;
}

.ref-collapse :deep(.ant-collapse-header) {
  font-weight: 600;
  font-size: 14px;
}

.url-row {
  display: flex;
  gap: 8px;
  margin-bottom: 8px;
  align-items: center;
}

.url-row .url-input { flex: 1; }

.url-extract-btn {
  background: var(--color-subtle);
  border: none;
  color: var(--color-on-primary);
  font-weight: 500;
}

.add-url-btn { margin-top: 4px; border-style: dashed; }

.url-hint {
  margin-top: 10px;
  padding: 8px 14px;
  background: var(--color-subtle);
  border: 1px solid var(--color-border);
  border-radius: 8px;
  font-size: 13px;
  color: var(--color-muted);
  line-height: 1.6;
}

/* 截图上传 */
.screenshot-upload-row {
  display: flex;
  align-items: center;
  gap: 12px;
  margin-bottom: 16px;
}

.screenshot-hint {
  font-size: 13px;
  color: var(--color-muted);
}

.screenshot-item {
  border: 1px solid var(--color-border);
  border-radius: 10px;
  padding: 12px;
  margin-bottom: 12px;
  background: var(--color-surface);
}

.screenshot-header {
  display: flex;
  justify-content: space-between;
  align-items: center;
  margin-bottom: 8px;
}

.screenshot-label { font-weight: 600; color: var(--color-text); }

.screenshot-thumb {
  width: 100%;
  max-height: 300px;
  object-fit: contain;
  border-radius: 8px;
  border: 1px solid var(--color-border);
  margin-bottom: 8px;
}

/* 裁剪区域列表 */
.crop-list {
  display: flex;
  flex-wrap: wrap;
  gap: 10px;
  margin-top: 8px;
}

.crop-item {
  width: 200px;
  border: 1px solid var(--color-border);
  border-radius: 8px;
  padding: 8px;
  background: var(--color-surface);
}

.crop-header {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 4px;
  margin-bottom: 6px;
}

.crop-label { font-size: 12px; font-weight: 600; color: var(--color-muted); }

.crop-thumb {
  width: 100%;
  height: 120px;
  object-fit: contain;
  border-radius: 4px;
  border: 1px solid var(--color-border);
  background: var(--color-subtle);
}

.crop-ocr-text {
  margin-top: 6px;
  font-size: 12px;
  color: var(--color-text);
  background: var(--color-surface);
  border-radius: 4px;
  padding: 6px;
  max-height: 80px;
  overflow-y: auto;
  white-space: pre-wrap;
  line-height: 1.4;
}

/* 裁剪画布 */
.crop-canvas-wrapper {
  background: var(--color-subtle);
  border-radius: 8px;
  overflow: hidden;
  margin-bottom: 12px;
}

.crop-canvas {
  display: block;
  cursor: crosshair;
  max-width: 100%;
}

.crop-modal-actions {
  display: flex;
  justify-content: flex-end;
}

/* 参考内容汇总 */
.ref-text-header {
  display: flex;
  justify-content: space-between;
  align-items: center;
  margin-bottom: 6px;
  font-weight: 600;
  color: var(--color-text);
}

.ref-mode-toggle {
  padding: 12px 16px;
  background: var(--color-surface);
  border-radius: 8px;
  border: 1px solid var(--color-border);
}

.ref-mode-label { font-weight: 600; color: var(--color-text); margin-right: 12px; }

.extract-message {
  margin-top: 8px;
  padding: 8px 16px;
  border-radius: 8px;
  font-size: 13px;
}

.extract-success {
  background: var(--color-subtle);
  border: 1px solid var(--color-border);
  color: var(--color-text);
}

.extract-error {
  background: var(--color-subtle);
  border: 1px solid var(--color-border);
  color: var(--color-text);
}

/* 提交按钮 */
.submit-button {
  height: 56px;
  border-radius: 28px;
  font-size: 18px;
  font-weight: 600;
  background: var(--color-subtle);
  border: none;
  box-shadow: 0 8px 24px rgba(102, 126, 234, 0.4);
  transition: all 0.3s ease;
}

.submit-button:hover {
  transform: translateY(-2px);
  box-shadow: 0 12px 32px rgba(102, 126, 234, 0.5);
}

.submit-button:active {
  transform: translateY(0);
}

.button-icon {
  margin-right: 8px;
  font-size: 20px;
}

/* 加载容器 */
.loading-container {
  text-align: center;
  padding: 24px;
  background: var(--color-subtle);
  border-radius: 16px;
  border: 2px dashed var(--color-border);
}

.loading-status {
  margin-top: 16px;
  color: var(--color-muted);
  font-size: 18px;
  font-weight: 500;
}
.loading-hint { margin: 7px 0 0; color: var(--color-muted); font-size: 13px; }
.loading-cancel { margin-top: 12px; }
.resume-trip { display: flex; align-items: center; gap: 12px; width: 100%; margin: -12px 0 24px; padding: 12px 16px; border: 1px solid var(--color-border); border-radius: 12px; background: var(--color-surface); color: var(--color-text); font: inherit; font-size: 13px; text-align: left; cursor: pointer; }
.resume-trip span:first-child { color: var(--color-muted); }
.resume-trip strong { font-weight: 500; flex: 1; }
.resume-trip:hover { border-color: var(--color-text); }

/* 动画 */
@keyframes fadeInDown {
  from {
    opacity: 0;
    transform: translateY(-30px);
  }
  to {
    opacity: 1;
    transform: translateY(0);
  }
}

@keyframes fadeInUp {
  from {
    opacity: 0;
    transform: translateY(30px);
  }
  to {
    opacity: 1;
    transform: translateY(0);
  }
}

/* Travel studio visual system */
.home-container {
  min-height: auto;
  padding: 64px 24px 80px;
  overflow: visible;
  background:
    var(--color-subtle),
    var(--color-subtle),
    var(--color-bg);
}
.page-header {
  max-width: 1200px;
  margin: 0 auto 56px;
  display: grid;
  grid-template-columns: minmax(0, 1.25fr) minmax(320px, 0.75fr);
  gap: clamp(36px, 6vw, 100px);
  align-items: center;
  text-align: left;
  animation: fadeInUp 0.65s ease-out both;
}
.hero-copy { padding: 18px 0; }
.eyebrow, .form-kicker {
  display: inline-flex;
  align-items: center;
  gap: 9px;
  color: var(--color-primary);
  font-size: 12px;
  font-weight: 750;
  letter-spacing: 0.19em;
}
.eyebrow-dot { width: 7px; height: 7px; border-radius: 50%; background: var(--color-accent); }
.page-title {
  margin: 24px 0 18px;
  color: var(--color-text);
  font-size: clamp(46px, 5.2vw, 76px);
  font-weight: 760;
  line-height: 1.19;
  letter-spacing: -0.07em;
  text-shadow: none;
}
.page-title em { color: var(--color-primary); font-style: normal; }
.page-subtitle { color: var(--color-muted); font-size: 16px; font-weight: 400; line-height: 1.9; }
.hero-points { display: flex; flex-wrap: wrap; gap: 8px 22px; margin-top: 28px; color: var(--color-muted); font-size: 13px; }
.hero-preview {
  position: relative;
  max-width: 440px;
  width: 100%;
  justify-self: end;
  overflow: hidden;
  border: 1px solid rgba(255,255,255,.62);
  border-radius: 28px;
  background: var(--color-subtle);
  box-shadow: 0 26px 65px rgba(33, 87, 79, .17), 0 4px 10px rgba(33, 87, 79, .06);
  transform: rotate(2deg);
  transition: transform .45s ease, box-shadow .45s ease;
}
.hero-preview:hover { transform: rotate(0) translateY(-5px); box-shadow: var(--shadow-raised); }
.preview-topline, .preview-bottom { display: flex; align-items: center; justify-content: space-between; position: relative; z-index: 2; }
.preview-topline { padding: 24px 28px 0; color: var(--color-muted); font-size: 11px; font-weight: 750; letter-spacing: .2em; }
.preview-sparkle { font-size: 29px; line-height: 1; color: var(--color-muted); }
.preview-illustration { height: 262px; position: relative; overflow: hidden; }
.preview-sun { position: absolute; width: 108px; height: 108px; top: 44px; right: 25%; border-radius: 50%; background: var(--color-subtle); box-shadow: 0 0 0 22px rgba(255,255,255,.15); }
.preview-hill { position: absolute; width: 130%; height: 230px; left: -15%; border-radius: 50% 50% 0 0; }
.preview-hill-back { bottom: -137px; transform: rotate(-10deg); background: var(--color-subtle); }
.preview-hill-front { bottom: -157px; left: 2%; transform: rotate(12deg); background: var(--color-primary); }
.preview-path { position: absolute; z-index: 1; left: 43%; bottom: -50px; color: rgba(255,255,255,.7); font-size: 230px; line-height: 1; transform: rotate(-24deg); }
.preview-bottom { padding: 18px 28px 24px; background: var(--color-surface); }
.preview-label { font-size: 17px; font-weight: 700; letter-spacing: -.03em; }
.preview-arrow { display: grid; place-items: center; width: 36px; height: 36px; border-radius: 50%; background: var(--color-subtle); color: var(--color-primary); font-size: 20px; }

.form-card {
  max-width: 1200px;
  border: 1px solid var(--color-border);
  border-radius: 28px;
  background: var(--color-surface);
  box-shadow: var(--shadow-soft);
  backdrop-filter: none;
  animation: fadeInUp .7s .08s ease-out both;
}
.form-card :deep(.ant-card-body) { padding: clamp(24px, 4.2vw, 56px); }
.form-card > .planner-body { padding: 0; }
.reference-mode-select {min-width:200px;max-width:100%;padding:7px 10px;border:1px solid var(--color-border);border-radius:8px;background:var(--color-surface);color:var(--color-text);font:inherit;font-size:12px}
.form-intro { display: flex; justify-content: space-between; align-items: flex-start; gap: 20px; margin-bottom: 36px; }
.form-intro h2 { margin: 9px 0 6px; color: var(--color-text); font-size: clamp(25px, 2.5vw, 34px); font-weight: 750; letter-spacing: -.05em; }
.form-intro p { margin: 0; color: var(--color-muted); font-size: 14px; }
.form-intro-mark { color: var(--color-muted); font-size: 13px; letter-spacing: .13em; white-space: nowrap; }
.form-section {
  padding: 27px 0 29px;
  margin: 0;
  background: none;
  border: 0;
  border-top: 1px solid var(--color-border);
  border-radius: 0;
  box-shadow: none;
  transition: none;
}
.form-section:hover { transform: none; box-shadow: none; }
.section-header { gap: 12px; margin-bottom: 22px; padding: 0; border: 0; }
.section-icon { display: grid; place-items: center; width: 38px; height: 38px; margin: 0; border-radius: 12px; background: var(--color-subtle); font-size: 18px; }
.section-title { font-size: 17px; font-weight: 720; color: var(--color-text); letter-spacing: -.02em; }
.form-label { color: var(--color-muted); font-size: 13px; font-weight: 650; }
.form-card :deep(.ant-form-item) { margin-bottom: 14px; }
.form-card :deep(.ant-form-item-label) { padding-bottom: 7px; }
.custom-input.ant-input-affix-wrapper, .custom-input.ant-picker, .custom-select :deep(.ant-select-selector),
.custom-textarea.ant-input, .url-input.ant-input, .ref-text-area :deep(.ant-input) {
  border: 1px solid var(--color-border) !important;
  border-radius: 12px !important;
  background: var(--color-surface) !important;
  box-shadow: none !important;
  transition: border-color .22s ease, background .22s ease, box-shadow .22s ease !important;
}
.custom-input.ant-input-affix-wrapper:hover, .custom-input.ant-picker:hover,
.custom-select:hover :deep(.ant-select-selector), .custom-textarea.ant-input:hover { border-color: var(--color-border) !important; }
.custom-input.ant-input-affix-wrapper:focus-within, .custom-input.ant-picker-focused,
.custom-select.ant-select-focused :deep(.ant-select-selector), .custom-textarea.ant-input:focus {
  border-color: var(--color-primary) !important;
  background: var(--color-surface);
  box-shadow: 0 0 0 3px rgba(23,107,101,.1) !important;
}
.custom-input :deep(.ant-input) { border: 0; background: transparent; }
.custom-input :deep(.ant-input-prefix) { color: var(--color-primary); font-size: 21px; margin-right: 8px; }
.days-display-compact { height: 40px; background: var(--color-subtle); color: var(--color-primary-dark); border-radius: 12px; }
.days-display-compact .days-value { font-size: 22px; }
.custom-checkbox-group { gap: 8px; }
.preference-tag.ant-checkbox-wrapper {
  display: inline-flex; align-items: center;
  margin: 0; padding: 8px 12px;
  border: 1px solid var(--color-border);
  border-radius: 999px;
  background: var(--color-surface);
  color: var(--color-muted);
  font-size: 13px;
  transition: background .22s ease, border-color .22s ease, transform .22s ease;
}
.preference-tag.ant-checkbox-wrapper:hover { transform: translateY(-2px); border-color: var(--color-border); }
.preference-tag.ant-checkbox-wrapper-checked { color: var(--color-primary-dark); background: var(--color-subtle); border-color: var(--color-border); }
.preference-tag :deep(.ant-checkbox) { margin-right: 2px; }
.ref-collapse :deep(.ant-collapse-item) { border-color: var(--color-border); border-radius: 12px; background: var(--color-surface); }
.ref-collapse :deep(.ant-collapse-header) { color: var(--color-text); padding: 14px 18px !important; }
.url-extract-btn { background: var(--color-primary); }
.url-extract-btn:hover { color: var(--color-on-primary) !important; background: var(--color-primary-dark) !important; }
.url-hint { background: var(--color-subtle); border-color: var(--color-border); color: var(--color-muted); }
.ref-mode-toggle, .screenshot-item { background: var(--color-surface); border-color: var(--color-border); border-radius: 12px; }
.submit-button {
  height: 56px;
  margin-top: 15px;
  border-radius: 14px;
  background: var(--color-primary);
  box-shadow: 0 10px 24px rgba(23,107,101,.18);
  font-size: 16px;
  letter-spacing: .02em;
}
.submit-button:hover { background: var(--color-primary-dark) !important; box-shadow: 0 14px 28px rgba(23,107,101,.24); transform: translateY(-2px); }
.submit-button:active { transform: translateY(1px); box-shadow: 0 5px 12px rgba(23,107,101,.16); }
.loading-container { background: var(--color-subtle); border: 1px solid var(--color-border); }
.loading-status { color: var(--color-primary); }

@media (max-width: 900px) {
  .page-header { grid-template-columns: 1fr; gap: 30px; }
  .hero-preview { justify-self: start; max-width: 540px; transform: none; }
  .hero-preview:hover { transform: translateY(-4px); }
}
@media (max-width: 640px) {
  .home-container { padding: 38px 16px 48px; }
  .page-header { margin-bottom: 36px; }
  .page-title { margin: 17px 0 14px; font-size: clamp(42px, 11vw, 58px); }
  .page-subtitle { font-size: 14px; }
  .desktop-break { display: none; }
  .hero-points { gap: 8px 14px; margin-top: 20px; font-size: 12px; }
  .hero-preview { display: none; }
  .form-card { border-radius: 20px; }
  .form-card :deep(.ant-card-body) { padding: 22px 18px; }
  .form-intro { margin-bottom: 24px; }
  .form-intro-mark { display: none; }
  .form-section { padding: 23px 0; }
  .url-row { flex-wrap: wrap; }
  .url-row .url-input { flex-basis: 100%; }
  .screenshot-upload-row, .screenshot-header, .ref-text-header { align-items: flex-start; flex-wrap: wrap; }
  .crop-item { width: min(100%, 240px); }
  .ref-mode-toggle :deep(.ant-radio-wrapper) { display: flex; margin: 0 0 12px; line-height: 1.6; }
}

/* Agent-first entry */
.home-container { padding-top: 42px; }
.page-header { margin-bottom: 32px; }
.page-title { font-size: clamp(42px, 4.3vw, 60px); }
.hero-preview { max-width: 300px; }
.preview-illustration { height: 128px; }
.preview-sun { width: 68px; height: 68px; top: 21px; }
.preview-hill { height: 190px; }
.preview-hill-back { bottom: -124px; }
.preview-hill-front { bottom: -139px; }
.preview-topline { padding: 18px 22px 0; }
.preview-bottom { padding: 12px 22px 17px; }
.form-intro { margin-bottom: 27px; }
.idea-composer {
  padding: 22px;
  border: 1px solid var(--color-border);
  border-radius: 20px;
  background: var(--color-subtle);
  box-shadow: 0 10px 30px rgba(30, 83, 68, .06);
}
.idea-label { display: block; margin-bottom: 12px; color: var(--color-text); font-size: 15px; font-weight: 720; }
.idea-input.ant-input {
  min-height: 92px;
  padding: 14px 16px;
  border: 1px solid var(--color-border);
  border-radius: 13px;
  background: var(--color-surface);
  color: var(--color-text);
  font-size: 15px;
  line-height: 1.7;
  resize: vertical;
  box-shadow: none;
  transition: border-color .2s ease, box-shadow .2s ease;
}
.idea-input.ant-input:focus { border-color: var(--color-primary); box-shadow: 0 0 0 3px rgba(23,107,101,.1); }
.idea-actions { display: flex; align-items: center; justify-content: space-between; gap: 16px; margin-top: 15px; }
.idea-actions > span { color: var(--color-muted); font-size: 12px; }
.idea-send.ant-btn {
  min-width: 132px;
  height: 42px;
  border-radius: 11px;
  background: var(--color-primary);
  box-shadow: 0 7px 18px rgba(23,107,101,.18);
  font-weight: 650;
  transition: transform .2s ease, box-shadow .2s ease, background .2s ease;
}
.idea-send.ant-btn:not(:disabled):hover { transform: translateY(-2px); background: var(--color-primary-dark); box-shadow: 0 11px 22px rgba(23,107,101,.24); }
.idea-send.ant-btn:not(:disabled):active { transform: translateY(0); }
.idea-send.ant-btn:disabled { border-color: var(--color-border); background: var(--color-subtle); color: var(--color-muted); box-shadow: none; }
.idea-send span { margin-left: 5px; }
.reference-shortcut { margin-top: 12px; }
.reference-trigger {
  display: inline-flex; align-items: center; justify-content: center; gap: 9px;
  min-height: 42px; padding: 9px 13px; border: 1px solid var(--color-border); border-radius: 11px;
  background: var(--color-surface); color: var(--color-text); text-align: left; cursor: pointer;
  transition: border-color .2s ease, background .2s ease;
}
.reference-trigger:not(:disabled):hover { border-color: var(--color-border); background: var(--color-subtle); }
.reference-trigger:disabled { cursor: not-allowed; opacity: .55; }
.reference-trigger:focus-visible, .reference-tabs button:focus-visible, .reference-text-button:focus-visible { outline: 3px solid var(--color-border); outline-offset: 2px; }
.reference-trigger-title { font-size: 14px; font-weight: 700; }
.reference-trigger-meta { color: var(--color-muted); font-size: 12px; }
.reference-quick-panel { padding: 19px; border: 1px solid var(--color-border); border-radius: 16px; background: var(--color-surface); box-shadow: 0 12px 26px rgba(27,79,64,.06); }
.reference-help { margin: 0 0 16px; color: var(--color-muted); font-size: 13px; line-height: 1.65; }
.reference-tabs { display: flex; gap: 7px; margin-bottom: 16px; padding: 4px; border-radius: 11px; background: var(--color-subtle); }
.reference-tabs button { flex: 1; min-height: 34px; padding: 7px 10px; border: 0; border-radius: 8px; background: transparent; color: var(--color-muted); font-size: 13px; font-weight: 650; cursor: pointer; transition: background .2s ease, box-shadow .2s ease, color .2s ease; }
.reference-tabs button.active { background: var(--color-surface); color: var(--color-primary); box-shadow: 0 2px 7px rgba(29,77,62,.08); }
.reference-field-label { display: block; margin-bottom: 8px; color: var(--color-text); font-size: 13px; font-weight: 650; }
.reference-tab-content :deep(.ant-input) { border-color: var(--color-border); border-radius: 10px; background: var(--color-surface); }
.reference-tab-content :deep(.ant-input:focus) { border-color: var(--color-primary); box-shadow: 0 0 0 3px rgba(23,107,101,.1); }
.reference-small-hint { margin: 9px 0 0; color: var(--color-muted); font-size: 12px; }
.reference-text-button { padding: 6px 0; border: 0; background: none; color: var(--color-primary); font-size: 13px; font-weight: 650; cursor: pointer; }
.reference-text-button.danger { color: var(--color-muted); }
.reference-footer { display: flex; align-items: center; justify-content: space-between; gap: 12px; margin-top: 16px; padding-top: 13px; border-top: 1px solid var(--color-border); }
.reference-mode-control { display: flex; align-items: center; gap: 9px; flex-wrap: wrap; }
.reference-mode-control label { color: var(--color-muted); font-size: 12px; font-weight: 650; }
.reference-tab-content .screenshot-item { margin-top: 12px; }
.starter-ideas { margin-top: 23px; }
.starter-label { display: block; margin-bottom: 11px; color: var(--color-muted); font-size: 12px; font-weight: 700; letter-spacing: .08em; }
.starter-list { display: grid; grid-template-columns: repeat(3, minmax(0, 1fr)); gap: 10px; }
.starter-chip {
  display: flex; align-items: center; justify-content: space-between; gap: 12px;
  min-height: 65px; padding: 12px 15px;
  border: 1px solid var(--color-border); border-radius: 13px;
  background: var(--color-surface); color: var(--color-text); text-align: left; font-size: 13px; line-height: 1.55;
  cursor: pointer; transition: transform .2s ease, border-color .2s ease, box-shadow .2s ease;
}
.starter-chip span { color: var(--color-primary); font-size: 17px; }
.starter-chip:not(:disabled):hover { transform: translateY(-3px); border-color: var(--color-border); box-shadow: 0 9px 20px rgba(29,83,69,.1); }
.starter-chip:disabled { cursor: not-allowed; opacity: .55; }
.starter-chip:focus-visible, .advanced-toggle:focus-visible { outline: 3px solid var(--color-border); outline-offset: 3px; }
.intake-thread { display: flex; flex-direction: column; gap: 12px; margin-top: 23px; }
.intake-bubble { max-width: min(88%, 720px); padding: 13px 17px; border-radius: 16px; background: var(--color-subtle); color: var(--color-text); }
.intake-bubble.user { align-self: flex-end; border-bottom-right-radius: 5px; background: var(--color-primary); color: var(--color-on-primary); }
.intake-bubble.assistant { align-self: flex-start; border-bottom-left-radius: 5px; }
.bubble-author { display: block; margin-bottom: 5px; font-size: 11px; font-weight: 750; opacity: .72; }
.intake-bubble p { margin: 0; font-size: 14px; line-height: 1.65; white-space: pre-wrap; }
.intake-summary { display: flex; flex-wrap: wrap; gap: 8px; margin-top: 3px; }
.intake-summary span { padding: 5px 10px; border-radius: 999px; background: var(--color-subtle); color: var(--color-muted); font-size: 12px; }
.advanced-entry { display: flex; align-items: center; gap: 14px; margin: 24px 0 0; padding-top: 19px; border-top: 1px solid var(--color-border); }
.advanced-entry > span { color: var(--color-muted); font-size: 12px; }
.advanced-toggle { display: inline-flex; align-items: center; gap: 10px; padding: 5px 0; border: 0; background: none; color: var(--color-primary); font-size: 14px; font-weight: 700; cursor: pointer; }
.advanced-toggle span:last-child { font-size: 19px; line-height: 1; }
.loading-container { margin-top: 22px; }

@media (max-width: 900px) {
  .starter-list { grid-template-columns: repeat(2, minmax(0, 1fr)); }
}
@media (max-width: 640px) {
  .home-container { padding-top: 34px; }
  .page-header { margin-bottom: 28px; }
  .page-title em { display: block; }
  .idea-composer { padding: 16px; border-radius: 16px; }
  .idea-actions { align-items: stretch; flex-direction: column; gap: 10px; }
  .idea-actions > span { display: none; }
  .idea-send.ant-btn { order: 1; width: 100%; }
  .reference-trigger { order: 2; width: 100%; }
  .reference-quick-panel { padding: 14px; }
  .reference-tabs button { font-size: 12px; padding: 7px 4px; }
  .reference-footer { align-items: flex-start; flex-direction: column; }
  .starter-list { display: flex; overflow-x: auto; padding-bottom: 8px; scroll-snap-type: x mandatory; }
  .starter-chip { min-width: 218px; flex: 0 0 218px; scroll-snap-align: start; }
  .advanced-entry { flex-wrap: wrap; gap: 4px 12px; }
  .intake-bubble { max-width: 94%; }
}
@media (prefers-reduced-motion: reduce) {
  .starter-chip, .idea-send.ant-btn { transition: none; }
  .starter-chip:not(:disabled):hover, .idea-send.ant-btn:not(:disabled):hover { transform: none; }
}
</style>

