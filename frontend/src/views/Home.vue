<template>
  <div class="home-container">
    <!-- 背景装饰 -->
    <div class="bg-decoration">
      <div class="circle circle-1"></div>
      <div class="circle circle-2"></div>
      <div class="circle circle-3"></div>
    </div>

    <!-- 页面标题 -->
    <div class="page-header">
      <div class="icon-wrapper">
        <span class="icon">✈️</span>
      </div>
      <h1 class="page-title">智能旅行助手</h1>
      <p class="page-subtitle">基于AI的个性化旅行规划,让每一次出行都完美无忧</p>
    </div>

    <a-card class="form-card" :bordered="false">
      <a-form
        :model="formData"
        layout="vertical"
        @finish="handleSubmit"
      >
        <!-- 第一步:目的地和日期 -->
        <div class="form-section">
          <div class="section-header">
            <span class="section-icon">📍</span>
            <span class="section-title">目的地与日期</span>
          </div>

          <a-row :gutter="24">
            <a-col :span="8">
              <a-form-item name="city" :rules="[{ required: true, message: '请输入目的地城市' }]">
                <template #label>
                  <span class="form-label">目的地城市</span>
                </template>
                <a-input
                  v-model:value="formData.city"
                  placeholder="例如: 北京"
                  size="large"
                  class="custom-input"
                >
                  <template #prefix>
                    <span style="color: #1890ff;">🏙️</span>
                  </template>
                </a-input>
              </a-form-item>
            </a-col>
            <a-col :span="6">
              <a-form-item name="start_date" :rules="[{ required: true, message: '请选择开始日期' }]">
                <template #label>
                  <span class="form-label">开始日期</span>
                </template>
                <a-date-picker
                  v-model:value="formData.start_date"
                  style="width: 100%"
                  size="large"
                  class="custom-input"
                  placeholder="选择日期"
                />
              </a-form-item>
            </a-col>
            <a-col :span="6">
              <a-form-item name="end_date" :rules="[{ required: true, message: '请选择结束日期' }]">
                <template #label>
                  <span class="form-label">结束日期</span>
                </template>
                <a-date-picker
                  v-model:value="formData.end_date"
                  style="width: 100%"
                  size="large"
                  class="custom-input"
                  placeholder="选择日期"
                />
              </a-form-item>
            </a-col>
            <a-col :span="4">
              <a-form-item>
                <template #label>
                  <span class="form-label">旅行天数</span>
                </template>
                <div class="days-display-compact">
                  <span class="days-value">{{ formData.travel_days }}</span>
                  <span class="days-unit">天</span>
                </div>
              </a-form-item>
            </a-col>
          </a-row>
        </div>

        <!-- 第二步:偏好设置 -->
        <div class="form-section">
          <div class="section-header">
            <span class="section-icon">⚙️</span>
            <span class="section-title">偏好设置</span>
          </div>

          <a-row :gutter="24">
            <a-col :span="8">
              <a-form-item name="transportation">
                <template #label>
                  <span class="form-label">交通方式</span>
                </template>
                <a-select v-model:value="formData.transportation" size="large" class="custom-select">
                  <a-select-option value="公共交通">🚇 公共交通</a-select-option>
                  <a-select-option value="自驾">🚗 自驾</a-select-option>
                  <a-select-option value="步行">🚶 步行</a-select-option>
                  <a-select-option value="混合">🔀 混合</a-select-option>
                </a-select>
              </a-form-item>
            </a-col>
            <a-col :span="8">
              <a-form-item name="accommodation">
                <template #label>
                  <span class="form-label">住宿偏好</span>
                </template>
                <a-select v-model:value="formData.accommodation" size="large" class="custom-select">
                  <a-select-option value="经济型酒店">💰 经济型酒店</a-select-option>
                  <a-select-option value="舒适型酒店">🏨 舒适型酒店</a-select-option>
                  <a-select-option value="豪华酒店">⭐ 豪华酒店</a-select-option>
                  <a-select-option value="民宿">🏡 民宿</a-select-option>
                </a-select>
              </a-form-item>
            </a-col>
            <a-col :span="8">
              <a-form-item name="preferences">
                <template #label>
                  <span class="form-label">旅行偏好</span>
                </template>
                <div class="preference-tags">
                  <a-checkbox-group v-model:value="formData.preferences" class="custom-checkbox-group">
                    <a-checkbox value="历史文化" class="preference-tag">🏛️ 历史文化</a-checkbox>
                    <a-checkbox value="自然风光" class="preference-tag">🏞️ 自然风光</a-checkbox>
                    <a-checkbox value="美食" class="preference-tag">🍜 美食</a-checkbox>
                    <a-checkbox value="购物" class="preference-tag">🛍️ 购物</a-checkbox>
                    <a-checkbox value="艺术" class="preference-tag">🎨 艺术</a-checkbox>
                    <a-checkbox value="休闲" class="preference-tag">☕ 休闲</a-checkbox>
                  </a-checkbox-group>
                </div>
              </a-form-item>
            </a-col>
          </a-row>
        </div>

        <!-- 第三步:额外要求 -->
        <div class="form-section">
          <div class="section-header">
            <span class="section-icon">💬</span>
            <span class="section-title">额外要求</span>
          </div>

          <a-form-item name="free_text_input">
            <a-textarea
              v-model:value="formData.free_text_input"
              placeholder="请输入您的额外要求,例如:想去看升旗、需要无障碍设施、对海鲜过敏等..."
              :rows="3"
              size="large"
              class="custom-textarea"
            />
          </a-form-item>
        </div>

        <!-- 第四步:参考来源(可选) -->
        <div class="form-section">
          <div class="section-header">
            <span class="section-icon">📌</span>
            <span class="section-title">参考来源（可选）</span>
            <a-tag color="purple" style="margin-left: 8px;">多来源可叠加</a-tag>
          </div>

          <a-collapse :bordered="false" class="ref-collapse">
            <!-- 🔗 小红书链接 -->
            <a-collapse-panel key="url" header="🔗 小红书链接 (提取网页内容)">
              <div v-for="(_item, idx) in urlList" :key="idx" class="url-row">
                <a-input
                  v-model:value="urlList[idx]"
                  :placeholder="`粘贴小红书链接 #${idx + 1}...`"
                  size="large"
                  :disabled="loading"
                  class="url-input"
                />
                <a-button
                  size="large"
                  :loading="extractingUrlIdx === idx"
                  :disabled="!urlList[idx] || loading"
                  @click="handleExtractUrl(idx)"
                  class="url-extract-btn"
                >
                  提取
                </a-button>
                <a-button
                  v-if="urlList.length > 1"
                  size="large"
                  danger
                  :disabled="loading"
                  @click="removeUrl(idx)"
                >
                  ✕
                </a-button>
              </div>
              <a-button type="dashed" size="large" block :disabled="loading" @click="addUrl" class="add-url-btn">
                + 添加更多链接
              </a-button>
              <div class="url-hint">
                💡 小红书等动态页面无法自动抓取时，请直接在浏览器打开链接 → 全选复制正文 → 粘贴到下方「参考内容汇总」文本框
              </div>
            </a-collapse-panel>

            <!-- 📷 截图上传 + 裁剪 -->
            <a-collapse-panel key="screenshot" header="📷 截图上传 (支持裁剪识别)">
              <div class="screenshot-upload-row">
                <a-upload
                  accept="image/png,image/jpeg,image/jpg,image/webp"
                  :before-upload="handleScreenshotAdd"
                  :show-upload-list="false"
                  :disabled="loading"
                >
                  <a-button size="large" :disabled="loading">
                    + 上传截图
                  </a-button>
                </a-upload>
                <span class="screenshot-hint">上传后可裁剪感兴趣的区域进行 OCR</span>
              </div>

              <!-- 截图列表 -->
              <div v-for="(shot, shotIdx) in screenshots" :key="shotIdx" class="screenshot-item">
                <div class="screenshot-header">
                  <span class="screenshot-label">截图 #{{ shotIdx + 1 }}</span>
                  <a-space>
                    <a-button size="small" type="primary" @click="openCropModal(shotIdx)" :disabled="loading">
                      ✂️ 裁剪区域
                    </a-button>
                    <a-button size="small" danger @click="removeScreenshot(shotIdx)" :disabled="loading">
                      删除截图
                    </a-button>
                  </a-space>
                </div>
                <img :src="shot.base64" class="screenshot-thumb" alt="截图预览" />

                <!-- 已裁剪区域 -->
                <div v-if="shot.crops.length > 0" class="crop-list">
                  <div v-for="(crop, cropIdx) in shot.crops" :key="cropIdx" class="crop-item">
                    <div class="crop-header">
                      <span class="crop-label">✂️ 区域 {{ cropIdx + 1 }}</span>
                      <a-tag v-if="crop.label" color="blue">{{ crop.label }}</a-tag>
                      <a-space size="small">
                        <a-button size="small" :loading="crop.ocrLoading" @click="runCropOCR(shotIdx, cropIdx)" :disabled="loading">
                          🔍 识别
                        </a-button>
                        <a-button size="small" danger @click="removeCrop(shotIdx, cropIdx)" :disabled="loading">
                          删除
                        </a-button>
                      </a-space>
                    </div>
                    <img :src="crop.dataUrl" class="crop-thumb" alt="裁剪区域" />
                    <div v-if="crop.ocrText" class="crop-ocr-text">{{ crop.ocrText }}</div>
                  </div>
                </div>
              </div>
            </a-collapse-panel>
          </a-collapse>

          <!-- 提取状态提示 -->
          <div v-if="extractMessage" :class="['extract-message', extractSuccess ? 'extract-success' : 'extract-error']">
            {{ extractMessage }}
          </div>

          <!-- 📝 参考内容汇总文本区 -->
          <div v-if="hasReferenceSources" class="ref-text-area" style="margin-top: 16px;">
            <div class="ref-text-header">
              <span>📝 参考内容汇总（可手动编辑）</span>
              <a-button size="small" danger @click="clearAllReferences" :disabled="loading">清空全部</a-button>
            </div>
            <a-textarea
              v-model:value="formData.reference_content"
              :rows="6"
              size="large"
              placeholder="所有提取的内容将汇总在这里，你可以手动编辑、补充或删除..."
              :disabled="loading"
            />
          </div>

          <!-- 严格程度 -->
          <div v-if="hasReferenceSources" class="ref-mode-toggle" style="margin-top: 12px;">
            <span class="ref-mode-label">规划策略：</span>
            <a-radio-group v-model:value="formData.reference_mode" :disabled="loading">
              <a-radio value="flexible">💡 AI自主规划（参考攻略风格，不直接采用具体地点）</a-radio>
              <a-radio value="hybrid">🔗 小红书为主 + AI补充（攻略为主体，AI填补交通/天气/时间等）</a-radio>
              <a-radio value="strict">📋 严格按攻略执行（完全照搬攻略中的景点和餐厅）</a-radio>
            </a-radio-group>
          </div>
        </div>

        <!-- 裁剪模态框 -->
        <a-modal
          v-model:open="cropModalVisible"
          title="✂️ 拖动鼠标框选要识别的区域"
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

        <!-- 提交按钮 -->
        <a-form-item>
          <a-button
            type="primary"
            html-type="submit"
            :loading="loading"
            size="large"
            block
            class="submit-button"
          >
            <template v-if="!loading">
              <span class="button-icon">🚀</span>
              <span>开始规划我的旅行</span>
            </template>
            <template v-else>
              <span>正在生成中...</span>
            </template>
          </a-button>
        </a-form-item>

        <!-- 加载进度条 -->
        <a-form-item v-if="loading">
          <div class="loading-container">
            <a-progress
              :percent="loadingProgress"
              status="active"
              :stroke-color="{
                '0%': '#667eea',
                '100%': '#764ba2',
              }"
              :stroke-width="10"
            />
            <p class="loading-status">
              {{ loadingStatus }}
            </p>
          </div>
        </a-form-item>
      </a-form>
    </a-card>
  </div>
</template>

<script setup lang="ts">
import { ref, reactive, watch } from 'vue'
import { useRouter } from 'vue-router'
import { message } from 'ant-design-vue'
import { generateTripPlan, generateTripPlanStream } from '@/services/api'
import type { TripFormData } from '@/types'
import type { Dayjs } from 'dayjs'

const router = useRouter()
const loading = ref(false)
const loadingProgress = ref(0)
const loadingStatus = ref('')

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

const formData = reactive<TripFormData & { start_date: Dayjs | null; end_date: Dayjs | null }>({
  city: '',
  start_date: null,
  end_date: null,
  travel_days: 1,
  transportation: '公共交通',
  accommodation: '经济型酒店',
  preferences: [],
  free_text_input: '',
  reference_type: 'none',
  reference_content: '',
  reference_mode: 'flexible'
})

const clearAllReferences = () => {
  formData.reference_content = ''
  formData.reference_type = 'none'
  formData.reference_mode = 'flexible'
  urlList.value = ['']
  screenshots.value = []
  extractMessage.value = ''
  extractSuccess.value = false
  message.info('已清空全部参考内容')
}

// 监听参考内容变化
const hasReferenceSources = ref(false)
watch(() => formData.reference_content, (val) => {
  hasReferenceSources.value = !!(val && val.trim())
})

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

const appendToReference = (text: string, sourceLabel: string) => {
  const header = `\n\n【${sourceLabel}】\n`
  const existing = formData.reference_content
  if (existing && existing.trim()) {
    formData.reference_content = existing.trim() + header + text
  } else {
    formData.reference_content = header + text
  }
  formData.reference_type = 'url' // mark as having reference
}

const handleExtractUrl = async (idx: number) => {
  const url = urlList.value[idx]
  if (!url) { message.warning('请先输入链接'); return }

  extractingUrlIdx.value = idx
  extractMessage.value = ''
  extractSuccess.value = false

  try {
    const res = await fetch('/api/trip/extract-url', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ url })
    })
    const data = await res.json()

    if (data.success && data.data?.content) {
      appendToReference(data.data.content, `链接 #${idx + 1}`)
      extractMessage.value = `链接 #${idx + 1} 提取成功`
      extractSuccess.value = true
      message.success(`链接 #${idx + 1} 内容提取成功`)
    } else {
      extractMessage.value = `链接 #${idx + 1}: ` + (data.message || '提取失败')
      extractSuccess.value = false
      message.warning(`链接 #${idx + 1} 提取失败`)
    }
  } catch (err: any) {
    extractMessage.value = '网络请求失败，请检查后端是否启动'
    extractSuccess.value = false
    message.error('网络请求失败')
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
  return { x: e.clientX - rect.left, y: e.clientY - rect.top }
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
  const scale = canvas.width / cropImg.width
  cropCtx.drawImage(cropImg, 0, 0, canvas.width, canvas.height)

  // 绘制选择框
  const x = Math.min(cropStart.value.x, cropEnd.value.x)
  const y = Math.min(cropStart.value.y, cropEnd.value.y)
  const w = Math.abs(cropEnd.value.x - cropStart.value.x)
  const h = Math.abs(cropEnd.value.y - cropStart.value.y)

  if (w > 5 && h > 5) {
    cropCtx.strokeStyle = '#667eea'
    cropCtx.lineWidth = 2
    cropCtx.setLineDash([6, 3])
    cropCtx.strokeRect(x, y, w, h)
    cropCtx.fillStyle = 'rgba(102, 126, 234, 0.1)'
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
    const res = await fetch('/api/trip/extract-image', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ image_base64: crop.dataUrl })
    })
    const data = await res.json()

    if (data.success && data.data?.content) {
      crop.ocrText = data.data.content
      const label = crop.label || `截图 #${shotIdx + 1} 区域 ${cropIdx + 1}`
      appendToReference(data.data.content, label)
      extractMessage.value = `"${label}" 识别成功`
      extractSuccess.value = true
      message.success('区域文字识别成功')
    } else {
      message.warning(data.message || 'OCR识别失败')
    }
  } catch (err: any) {
    message.error('OCR请求失败')
  } finally {
    crop.ocrLoading = false
  }
}

const handleSubmit = async () => {
  if (!formData.start_date || !formData.end_date) {
    message.error('请选择日期')
    return
  }

  loading.value = true
  loadingProgress.value = 0
  loadingStatus.value = '正在初始化...'

  // 自动判断参考来源类型
  if (formData.reference_content && formData.reference_content.trim()) {
    formData.reference_type = screenshots.value.length > 0 ? 'screenshot' : 'url'
  }

  const requestData: TripFormData = {
    city: formData.city,
    start_date: formData.start_date.format('YYYY-MM-DD'),
    end_date: formData.end_date.format('YYYY-MM-DD'),
    travel_days: formData.travel_days,
    transportation: formData.transportation,
    accommodation: formData.accommodation,
    preferences: formData.preferences,
    free_text_input: formData.free_text_input,
    reference_type: formData.reference_type,
    reference_content: formData.reference_content,
    reference_mode: formData.reference_mode
  }

  try {
    // P4: 使用SSE流式接口
    const response = await generateTripPlanStream(
      requestData,
      (progress: number, status: string) => {
        loadingProgress.value = progress
        loadingStatus.value = status
        console.log(`📊 进度: ${progress}% - ${status}`)
      }
    )

    loadingProgress.value = 100
    loadingStatus.value = '✅ 完成!'

    if (response.success && response.data) {
      // 保存到sessionStorage
      sessionStorage.setItem('tripPlan', JSON.stringify(response.data))

      message.success('旅行计划生成成功!')

      // 短暂延迟后跳转
      setTimeout(() => {
        router.push('/result')
      }, 500)
    } else {
      message.error(response.message || '生成失败')
      loading.value = false
      loadingProgress.value = 0
      loadingStatus.value = ''
    }
  } catch (error: any) {
    console.warn('SSE流式请求失败，回退到普通接口:', error?.message || error)
    
    // 回退到普通接口
    try {
      loadingStatus.value = '🔄 回退到普通模式...'
      
      // 显示模拟进度
      const progressInterval = setInterval(() => {
        if (loadingProgress.value < 90) {
          loadingProgress.value += 5
          if (loadingProgress.value <= 30) {
            loadingStatus.value = '🔍 正在搜索景点...'
          } else if (loadingProgress.value <= 50) {
            loadingStatus.value = '🌤️ 正在查询天气...'
          } else if (loadingProgress.value <= 70) {
            loadingStatus.value = '🏨 正在推荐酒店...'
          } else {
            loadingStatus.value = '📋 正在生成行程计划...'
          }
        }
      }, 800)

      const response = await generateTripPlan(requestData)
      
      clearInterval(progressInterval)
      loadingProgress.value = 100
      loadingStatus.value = '✅ 完成!'

      if (response.success && response.data) {
        sessionStorage.setItem('tripPlan', JSON.stringify(response.data))
        message.success('旅行计划生成成功!')
        setTimeout(() => {
          router.push('/result')
        }, 500)
      } else {
        message.error(response.message || '生成失败')
        loading.value = false
        loadingProgress.value = 0
        loadingStatus.value = ''
      }
    } catch (fallbackError: any) {
      console.error('生成失败:', fallbackError)
      message.error(fallbackError.message || '生成旅行计划失败,请稍后重试')
      loading.value = false
      loadingProgress.value = 0
      loadingStatus.value = ''
    }
  }
}
</script>

<style scoped>
.home-container {
  min-height: 100vh;
  background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
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
  color: #ffffff;
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
  background: linear-gradient(135deg, #f5f7fa 0%, #ffffff 100%);
  border-radius: 16px;
  border: 1px solid #e8e8e8;
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
  border-bottom: 2px solid #667eea;
}

.section-icon {
  font-size: 24px;
  margin-right: 12px;
}

.section-title {
  font-size: 18px;
  font-weight: 600;
  color: #333;
}

/* 表单标签 */
.form-label {
  font-size: 15px;
  font-weight: 500;
  color: #555;
}

/* 自定义输入框 */
.custom-input :deep(.ant-input),
.custom-input :deep(.ant-picker) {
  border-radius: 12px;
  border: 2px solid #e8e8e8;
  transition: all 0.3s ease;
}

.custom-input :deep(.ant-input:hover),
.custom-input :deep(.ant-picker:hover) {
  border-color: #667eea;
}

.custom-input :deep(.ant-input:focus),
.custom-input :deep(.ant-picker-focused) {
  border-color: #667eea;
  box-shadow: 0 0 0 3px rgba(102, 126, 234, 0.1);
}

/* 自定义选择框 */
.custom-select :deep(.ant-select-selector) {
  border-radius: 12px !important;
  border: 2px solid #e8e8e8 !important;
  transition: all 0.3s ease;
}

.custom-select:hover :deep(.ant-select-selector) {
  border-color: #667eea !important;
}

.custom-select :deep(.ant-select-focused .ant-select-selector) {
  border-color: #667eea !important;
  box-shadow: 0 0 0 3px rgba(102, 126, 234, 0.1) !important;
}

/* 天数显示 - 紧凑版 */
.days-display-compact {
  display: flex;
  align-items: center;
  justify-content: center;
  height: 40px;
  padding: 8px 16px;
  background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
  border-radius: 12px;
  color: white;
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
  border: 2px solid #e8e8e8;
  border-radius: 20px;
  transition: all 0.3s ease;
  background: white;
  font-size: 14px;
}

.preference-tag :deep(.ant-checkbox-wrapper:hover) {
  border-color: #667eea;
  background: #f5f7ff;
}

.preference-tag :deep(.ant-checkbox-wrapper-checked) {
  border-color: #667eea;
  background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
  color: white;
}

/* 自定义文本域 */
.custom-textarea :deep(.ant-input) {
  border-radius: 12px;
  border: 2px solid #e8e8e8;
  transition: all 0.3s ease;
}

.custom-textarea :deep(.ant-input:hover) {
  border-color: #667eea;
}

.custom-textarea :deep(.ant-input:focus) {
  border-color: #667eea;
  box-shadow: 0 0 0 3px rgba(102, 126, 234, 0.1);
}

/* 参考来源 */
.ref-collapse {
  background: transparent;
  margin-bottom: 8px;
}

.ref-collapse :deep(.ant-collapse-item) {
  border: 1px solid #e8e8e8;
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
  background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
  border: none;
  color: white;
  font-weight: 500;
}

.add-url-btn { margin-top: 4px; border-style: dashed; }

.url-hint {
  margin-top: 10px;
  padding: 8px 14px;
  background: #fffbe6;
  border: 1px solid #ffe58f;
  border-radius: 8px;
  font-size: 13px;
  color: #ad6800;
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
  color: #999;
}

.screenshot-item {
  border: 1px solid #e8e8e8;
  border-radius: 10px;
  padding: 12px;
  margin-bottom: 12px;
  background: #fafafa;
}

.screenshot-header {
  display: flex;
  justify-content: space-between;
  align-items: center;
  margin-bottom: 8px;
}

.screenshot-label { font-weight: 600; color: #333; }

.screenshot-thumb {
  width: 100%;
  max-height: 300px;
  object-fit: contain;
  border-radius: 8px;
  border: 1px solid #e8e8e8;
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
  border: 1px solid #d9d9d9;
  border-radius: 8px;
  padding: 8px;
  background: #fff;
}

.crop-header {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 4px;
  margin-bottom: 6px;
}

.crop-label { font-size: 12px; font-weight: 600; color: #667eea; }

.crop-thumb {
  width: 100%;
  height: 120px;
  object-fit: contain;
  border-radius: 4px;
  border: 1px solid #eee;
  background: #f5f5f5;
}

.crop-ocr-text {
  margin-top: 6px;
  font-size: 12px;
  color: #333;
  background: #f9f9f9;
  border-radius: 4px;
  padding: 6px;
  max-height: 80px;
  overflow-y: auto;
  white-space: pre-wrap;
  line-height: 1.4;
}

/* 裁剪画布 */
.crop-canvas-wrapper {
  background: #f0f0f0;
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
  color: #555;
}

.ref-mode-toggle {
  padding: 12px 16px;
  background: #fafafa;
  border-radius: 8px;
  border: 1px solid #e8e8e8;
}

.ref-mode-label { font-weight: 600; color: #555; margin-right: 12px; }

.extract-message {
  margin-top: 8px;
  padding: 8px 16px;
  border-radius: 8px;
  font-size: 13px;
}

.extract-success {
  background: #f6ffed;
  border: 1px solid #b7eb8f;
  color: #389e0d;
}

.extract-error {
  background: #fff2f0;
  border: 1px solid #ffccc7;
  color: #cf1322;
}

/* 提交按钮 */
.submit-button {
  height: 56px;
  border-radius: 28px;
  font-size: 18px;
  font-weight: 600;
  background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
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
  background: linear-gradient(135deg, #f5f7fa 0%, #ffffff 100%);
  border-radius: 16px;
  border: 2px dashed #667eea;
}

.loading-status {
  margin-top: 16px;
  color: #667eea;
  font-size: 18px;
  font-weight: 500;
}

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
</style>

