<template>
  <div class="result-container">
    <!-- 页面头部 -->
    <div class="page-header">
      <a-button class="back-button" size="large" @click="goBack">
        ← 返回首页
      </a-button>
      <a-space size="middle">
        <a-button v-if="!editMode && !diyMode" @click="toggleEditMode" type="default">
          ✏️ 编辑行程
        </a-button>
        <a-button v-if="!diyMode && !editMode" @click="toggleDiyMode" type="default">
          🔧 DIY调整
        </a-button>
        <a-button v-if="editMode" @click="saveChanges" type="primary">
          💾 保存修改
        </a-button>
        <a-button v-if="diyMode" @click="saveDiyChanges" type="primary">
          💾 保存DIY
        </a-button>
        <a-button v-if="editMode || diyMode" @click="cancelEdit" type="default">
          ❌ 取消
        </a-button>

        <!-- 导出按钮 -->
        <a-dropdown v-if="!editMode && !diyMode">
          <template #overlay>
            <a-menu>
              <a-menu-item key="image" @click="exportAsImage">
                📷 导出为图片
              </a-menu-item>
              <a-menu-item key="pdf" @click="exportAsPDF">
                📄 导出为PDF
              </a-menu-item>
            </a-menu>
          </template>
          <a-button type="default">
            📥 导出行程 <DownOutlined />
          </a-button>
        </a-dropdown>
      </a-space>
    </div>

    <div v-if="tripPlan" class="content-wrapper">
      <!-- 侧边导航 -->
      <div class="side-nav">
        <a-affix :offset-top="80">
          <a-menu mode="inline" :selected-keys="[activeSection]" @click="scrollToSection">
            <a-menu-item key="overview">
              <span>📋 行程概览</span>
            </a-menu-item>
            <a-menu-item key="budget" v-if="tripPlan.budget">
              <span>💰 预算明细</span>
            </a-menu-item>
            <a-menu-item key="map">
              <span>📍 景点地图</span>
            </a-menu-item>
            <a-sub-menu key="days" title="📅 每日行程">
              <a-menu-item v-for="(day, index) in tripPlan.days" :key="`day-${index}`">
                第{{ day.day_index + 1 }}天
              </a-menu-item>
            </a-sub-menu>
            <a-menu-item key="weather" v-if="tripPlan.weather_info && tripPlan.weather_info.length > 0">
              <span>🌤️ 天气信息</span>
            </a-menu-item>
          </a-menu>
        </a-affix>
      </div>

      <!-- 主内容区 -->
      <div class="main-content">
        <!-- 顶部信息区:左侧概览+预算,右侧地图 -->
        <div class="top-info-section">
          <!-- 左侧:行程概览和预算明细 -->
          <div class="left-info">
            <!-- 行程概览 -->
            <a-card id="overview" :title="`${tripPlan.city}旅行计划`" :bordered="false" class="overview-card">
              <div class="overview-content">
                <div class="info-item">
                  <span class="info-label">📅 日期:</span>
                  <span class="info-value">{{ tripPlan.start_date }} 至 {{ tripPlan.end_date }}</span>
                </div>
                <div class="info-item">
                  <span class="info-label">💡 建议:</span>
                  <span class="info-value">{{ tripPlan.overall_suggestions }}</span>
                </div>
              </div>
            </a-card>

            <!-- 预算明细 -->
            <a-card id="budget" v-if="tripPlan.budget" title="💰 预算明细" :bordered="false" class="budget-card">
              <div class="budget-grid">
                <div class="budget-item">
                  <div class="budget-label">景点门票</div>
                  <div class="budget-value">¥{{ tripPlan.budget.total_attractions }}</div>
                </div>
                <div class="budget-item">
                  <div class="budget-label">酒店住宿</div>
                  <div class="budget-value">¥{{ tripPlan.budget.total_hotels }}</div>
                </div>
                <div class="budget-item">
                  <div class="budget-label">餐饮费用</div>
                  <div class="budget-value">¥{{ tripPlan.budget.total_meals }}</div>
                </div>
                <div class="budget-item">
                  <div class="budget-label">交通费用</div>
                  <div class="budget-value">¥{{ tripPlan.budget.total_transportation }}</div>
                </div>
              </div>
              <div class="budget-total">
                <span class="total-label">预估总费用</span>
                <span class="total-value">¥{{ tripPlan.budget.total }}</span>
              </div>
            </a-card>
          </div>

          <!-- 右侧:地图 -->
          <div class="right-map">
            <a-card id="map" title="🗺️ 景点规划地图" :bordered="false" class="map-card">
              <div id="amap-container" style="width: 100%; height: 100%"></div>
            </a-card>
          </div>
        </div>

        <!-- 每日行程:可折叠 -->
        <a-card title="📅 每日行程" :bordered="false" class="days-card">
          <a-collapse v-model:activeKey="activeDays">
            <a-collapse-panel
              v-for="(day, index) in tripPlan.days"
              :key="index"
              :id="`day-${index}`"
            >
              <template #header>
                <div class="day-header">
                  <span class="day-title">第{{ day.day_index + 1 }}天</span>
                  <span class="day-date">{{ day.date }}</span>
                </div>
              </template>

              <!-- 行程基本信息 -->
              <div class="day-info">
                <div class="info-row">
                  <span class="label">📝 行程描述:</span>
                  <span class="value">{{ day.description }}</span>
                </div>
                <div v-if="day.route_notes" class="info-row route-notes-row">
                  <span class="label">🗺️ 路线优化:</span>
                  <span class="value route-notes-value">{{ day.route_notes }}</span>
                </div>
                <div class="info-row">
                  <span class="label">🚗 交通方式:</span>
                  <span class="value">{{ day.transportation }}</span>
                </div>
                <div class="info-row">
                  <span class="label">🏨 住宿:</span>
                  <span class="value">{{ day.accommodation }}</span>
                </div>
              </div>

              <!-- 景点安排 -->
              <a-divider orientation="left">🎯 景点安排</a-divider>
              <a-list
                :data-source="day.attractions"
                :grid="{ gutter: 16, column: 2 }"
              >
                <template #renderItem="{ item, index }">
                  <a-list-item>
                    <a-card :title="item.name" size="small" class="attraction-card">
                      <!-- DIY/编辑模式下的操作按钮 -->
                      <template #extra v-if="editMode || diyMode">
                        <a-space>
                          <a-button
                            size="small"
                            @click="moveAttraction(day.day_index, index, 'up')"
                            :disabled="index === 0"
                          >
                            ↑
                          </a-button>
                          <a-button
                            size="small"
                            @click="moveAttraction(day.day_index, index, 'down')"
                            :disabled="index === day.attractions.length - 1"
                          >
                            ↓
                          </a-button>
                          <a-button
                            v-if="diyMode"
                            size="small"
                            type="primary"
                            @click="openAlternatives('attraction', day.day_index, index, item)"
                          >
                            🔄 换
                          </a-button>
                          <a-button
                            size="small"
                            danger
                            @click="deleteAttraction(day.day_index, index)"
                          >
                            🗑️
                          </a-button>
                        </a-space>
                      </template>

                      <!-- 景点图片 -->
                      <div class="attraction-image-wrapper">
                        <img
                          :src="getAttractionImage(item.name, index)"
                          :alt="item.name"
                          class="attraction-image"
                          @error="handleImageError"
                        />
                        <div class="attraction-badge">
                          <span class="badge-number">{{ index + 1 }}</span>
                        </div>
                        <div v-if="item.ticket_price" class="price-tag">
                          ¥{{ item.ticket_price }}
                        </div>
                      </div>

                      <!-- 编辑模式下可编辑的字段 -->
                      <div v-if="editMode">
                        <p><strong>地址:</strong></p>
                        <a-input v-model:value="item.address" size="small" style="margin-bottom: 8px" />

                        <p><strong>游览时长(分钟):</strong></p>
                        <a-input-number v-model:value="item.visit_duration" :min="10" :max="480" size="small" style="width: 100%; margin-bottom: 8px" />

                        <p><strong>描述:</strong></p>
                        <a-textarea v-model:value="item.description" :rows="2" size="small" style="margin-bottom: 8px" />
                      </div>

                      <!-- 查看模式 -->
                      <div v-else>
                        <p><strong>地址:</strong> {{ item.address }}</p>
                        <p><strong>游览时长:</strong> {{ item.visit_duration }}分钟</p>
                        <p><strong>描述:</strong> {{ item.description }}</p>
                        <p v-if="item.rating"><strong>评分:</strong> {{ item.rating }}⭐</p>
                      </div>
                    </a-card>
                  </a-list-item>
                </template>
              </a-list>

              <!-- DIY: 添加景点 -->
              <div v-if="diyMode" style="margin-bottom: 16px;">
                <a-button type="dashed" size="small" @click="openAddModal('attraction', day.day_index)">
                  + 添加景点
                </a-button>
              </div>

              <!-- 酒店推荐 -->
              <a-divider v-if="day.hotel" orientation="left">🏨 住宿推荐</a-divider>
              <a-card v-if="day.hotel" size="small" class="hotel-card">
                <template #title>
                  <span class="hotel-title">{{ day.hotel.name }}</span>
                </template>
                <a-descriptions :column="2" size="small">
                  <a-descriptions-item label="地址">{{ day.hotel.address }}</a-descriptions-item>
                  <a-descriptions-item label="类型">{{ day.hotel.type }}</a-descriptions-item>
                  <a-descriptions-item label="价格范围">{{ day.hotel.price_range }}</a-descriptions-item>
                  <a-descriptions-item label="评分">{{ day.hotel.rating }}⭐</a-descriptions-item>
                  <a-descriptions-item label="距离" :span="2">{{ day.hotel.distance }}</a-descriptions-item>
                </a-descriptions>
              </a-card>

              <!-- 餐饮安排 -->
              <a-divider orientation="left">🍽️ 餐饮安排</a-divider>
              <a-list
                :data-source="day.meals"
                :grid="{ gutter: 16, column: 3 }"
              >
                <template #renderItem="{ item, index }">
                  <a-list-item>
                    <a-card size="small" class="meal-card">
                      <div class="meal-type-badge">{{ getMealLabel(item.type) }}</div>
                      <a-button v-if="diyMode" size="small" type="primary" class="meal-swap-btn" @click="openAlternatives('meal', day.day_index, index, item)" title="换一个餐厅">
                        🔄
                      </a-button>
                      <!-- 美食图片 -->
                      <div class="meal-image-wrapper">
                        <img
                          :src="getMealImage(item, day.day_index)"
                          :alt="item.name"
                          class="meal-image"
                          @error="(e: Event) => handleImageError(e)"
                        />
                      </div>
                      <div class="meal-info">
                        <div class="meal-name">{{ item.restaurant || item.name }}</div>
                        <div v-if="item.recommended_dish" class="meal-dish">
                          🥢 {{ item.recommended_dish }}
                        </div>
                        <div v-if="item.address" class="meal-address">
                          📍 {{ item.address }}
                        </div>
                        <div v-if="item.description" class="meal-desc">
                          {{ item.description }}
                        </div>
                        <div class="meal-cost" v-if="item.estimated_cost">
                          💰 人均 ¥{{ item.estimated_cost }}
                        </div>
                      </div>
                    </a-card>
                  </a-list-item>
                </template>
              </a-list>

              <!-- DIY: 添加餐厅 + 优化路线 -->
              <div v-if="diyMode" style="margin-bottom: 12px; display: flex; gap: 8px; flex-wrap: wrap;">
                <a-button type="dashed" size="small" @click="openAddModal('meal', day.day_index)">
                  + 添加餐厅
                </a-button>
                <a-button type="primary" size="small" @click="handleReoptimize(day.day_index)" :loading="reoptLoading === day.day_index">
                  🔄 智能优化路线
                </a-button>
              </div>

              <!-- 当日路线地图 -->
              <a-divider orientation="left">🗺️ 当日完整路线</a-divider>
              <div :id="'day-mini-map-' + day.day_index" class="day-mini-map"></div>
              <div v-if="dayRouteInfo[day.day_index] && dayRouteInfo[day.day_index].length > 0" class="route-summary">
                <div class="route-summary-title">📍 路线详情</div>
                <div v-for="(seg, segIdx) in dayRouteInfo[day.day_index]" :key="segIdx" class="route-segment">
                  <div class="seg-header">
                    <span class="seg-mode">{{ seg.mode }}</span>
                    <span class="seg-duration">{{ seg.duration }}分钟</span>
                    <span class="seg-distance">{{ seg.distance }}</span>
                  </div>
                  <div class="seg-stops">
                    <span class="seg-from">{{ seg.from }}</span>
                    <span class="seg-arrow">→</span>
                    <span class="seg-to">{{ seg.to }}</span>
                  </div>
                  <div v-if="seg.stations" class="seg-transit-detail">
                    🚌 上车站: {{ seg.stations.boarding }} → 下车站: {{ seg.stations.alighting }} ({{ seg.stations.line }})
                  </div>
                </div>
              </div>
            </a-collapse-panel>
          </a-collapse>
        </a-card>

        <a-card id="weather" v-if="tripPlan.weather_info && tripPlan.weather_info.length > 0" title="天气信息" style="margin-top: 20px" :bordered="false">
        <a-list
          :data-source="tripPlan.weather_info"
          :grid="{ gutter: 16, column: 3 }"
        >
          <template #renderItem="{ item }">
            <a-list-item>
              <a-card size="small" class="weather-card">
                <div class="weather-date">{{ item.date }}</div>
                <div class="weather-info-row">
                  <span class="weather-icon">☀️</span>
                  <div>
                    <div class="weather-label">白天</div>
                    <div class="weather-value">{{ item.day_weather }} {{ item.day_temp }}°C</div>
                  </div>
                </div>
                <div class="weather-info-row">
                  <span class="weather-icon">🌙</span>
                  <div>
                    <div class="weather-label">夜间</div>
                    <div class="weather-value">{{ item.night_weather }} {{ item.night_temp }}°C</div>
                  </div>
                </div>
                <div class="weather-wind">
                  💨 {{ item.wind_direction }} {{ item.wind_power }}
                </div>
              </a-card>
            </a-list-item>
          </template>
        </a-list>
        </a-card>
      </div>
    </div>

    <a-empty v-else description="没有找到旅行计划数据">
      <template #image>
        <div style="font-size: 80px;">🗺️</div>
      </template>
      <template #description>
        <span style="color: #999;">暂无旅行计划数据,请先创建行程</span>
      </template>
      <a-button type="primary" @click="goBack">返回首页创建行程</a-button>
    </a-empty>

    <!-- DIY添加景点/餐厅弹窗 -->
    <a-modal v-model:open="addModalVisible" :title="`添加${addType === 'attraction' ? '景点' : '餐厅'}`" width="600px" :footer="null">
      <a-input-search
        v-model:value="addSearchKeyword"
        :placeholder="`搜索${addType === 'attraction' ? '景点' : '餐厅'}名称...`"
        enter-button="搜索"
        size="large"
        :loading="addSearchLoading"
        @search="handleAddSearch"
      />
      <div v-if="addResults.length > 0" style="margin-top: 16px;">
        <a-list :data-source="addResults" size="small">
          <template #renderItem="{ item }">
            <a-list-item>
              <a-list-item-meta :title="item.name" :description="item.address">
              </a-list-item-meta>
              <a-button type="primary" size="small" @click="confirmAddItem(item)">添加</a-button>
            </a-list-item>
          </template>
        </a-list>
      </div>
      <div v-if="addSearchDone && addResults.length === 0" style="margin-top: 16px; text-align: center; color: #999;">
        未找到结果，可手动输入名称添加
      </div>
      <a-divider>或手动输入</a-divider>
      <a-input v-model:value="addManualName" placeholder="手动输入名称" size="large" style="margin-bottom: 8px;" />
      <a-input v-model:value="addManualAddress" placeholder="地址（可选）" size="large" />
      <a-button type="dashed" block style="margin-top: 8px;" @click="confirmManualAdd">手动添加</a-button>
    </a-modal>

    <!-- 回到顶部按钮 -->
    <a-back-top :visibility-height="300">
      <div class="back-top-button">
        ↑
      </div>
    </a-back-top>

    <!-- 备选项选择模态框 -->
    <a-modal
      v-model:open="altModalVisible"
      :title="`选择备选${altType === 'attraction' ? '景点' : '餐厅'}`"
      width="700px"
      :footer="null"
    >
      <a-spin :spinning="altLoading" tip="AI正在搜索最优备选...">
        <div v-if="alternatives.length > 0" class="alt-list">
          <a-card
            v-for="(alt, idx) in alternatives"
            :key="idx"
            size="small"
            hoverable
            class="alt-card"
            @click="selectAlternative(idx)"
          >
            <a-row align="middle" :gutter="16">
              <a-col :flex="'auto'">
                <div class="alt-name">
                  {{ altType === 'attraction' ? '🏛️' : '🍽️' }} {{ alt.name }}
                  <a-tag v-if="alt.category" color="blue" style="margin-left: 8px;">{{ alt.category }}</a-tag>
                </div>
                <div class="alt-address">📍 {{ alt.address }}</div>
                <div v-if="altType === 'attraction' && alt.visit_duration" class="alt-meta">
                  ⏱️ {{ alt.visit_duration }}分钟 | 🎫 ¥{{ alt.estimated_cost || 0 }}
                </div>
                <div v-if="altType === 'meal' && alt.recommended_dish" class="alt-meta">
                  🥢 {{ alt.recommended_dish }} | 💰 ¥{{ alt.estimated_cost || 0 }}/人
                </div>
                <div class="alt-reason">💡 {{ alt.reason }}</div>
              </a-col>
              <a-col :flex="'80px'" style="text-align: right;">
                <a-button type="primary" size="small">选择</a-button>
              </a-col>
            </a-row>
          </a-card>
        </div>
        <a-empty v-else-if="!altLoading" description="未找到合适的备选项" />
      </a-spin>
    </a-modal>
  </div>
</template>

<script setup lang="ts">
import { ref, onMounted, nextTick } from 'vue'
import { useRouter } from 'vue-router'
import { message } from 'ant-design-vue'
import { DownOutlined } from '@ant-design/icons-vue'
import AMapLoader from '@amap/amap-jsapi-loader'
import html2canvas from 'html2canvas'
import jsPDF from 'jspdf'
import type { TripPlan, Alternative } from '@/types'

// API基址: 本地开发时为空(走Vite代理)，生产环境为后端URL
const API_BASE_URL = import.meta.env.VITE_API_BASE_URL || ''

const router = useRouter()
const tripPlan = ref<TripPlan | null>(null)
const editMode = ref(false)
const diyMode = ref(false)
const originalPlan = ref<TripPlan | null>(null)

// DIY备选项
const altModalVisible = ref(false)
const altLoading = ref(false)
const altType = ref<'attraction' | 'meal'>('attraction')
const alternatives = ref<Alternative[]>([])
const altTargetDay = ref(-1)
const altTargetIdx = ref(-1)

// DIY添加景点/餐厅
const addModalVisible = ref(false)
const addType = ref<'attraction' | 'meal'>('attraction')
const addTargetDay = ref(-1)
const addSearchKeyword = ref('')
const addSearchLoading = ref(false)
const addSearchDone = ref(false)
const addResults = ref<any[]>([])
const addManualName = ref('')
const addManualAddress = ref('')
const reoptLoading = ref(-1)

// 每日迷你地图
const dayMiniMaps: Record<number, any> = {}
const dayRouteInfo = ref<Record<number, any[]>>({})
const dayMapsInitialized = ref<Record<number, boolean>>({})
const attractionPhotos = ref<Record<string, string>>({})
const mealPhotos = ref<Record<string, string>>({})
const activeSection = ref('overview')
const activeDays = ref<number[]>([0]) // 默认展开第一天
let map: any = null

onMounted(async () => {
  const data = sessionStorage.getItem('tripPlan')
  if (data) {
    const planData = JSON.parse(data)
    
    // 验证和修正days数据
    if (planData.days && Array.isArray(planData.days)) {
      console.log(`📅 接收到 ${planData.days.length} 天数据`)
      
      // 检查day_index是否正确
      const dayIndices = planData.days.map((d: any, i: number) => ({
        index: i,
        day_index: d.day_index,
        date: d.date
      }))
      console.log('📅 days数据:', dayIndices)
      
      // 修正day_index：确保从0开始连续
      let needsFix = false
      for (let i = 0; i < planData.days.length; i++) {
        if (planData.days[i].day_index !== i) {
          console.warn(`⚠️ day_index不匹配: 期望${i}, 实际${planData.days[i].day_index}`)
          needsFix = true
          break
        }
      }
      
      if (needsFix) {
        console.log('🔧 修正day_index...')
        planData.days.forEach((day: any, i: number) => {
          day.day_index = i
        })
      }
      
      // 检查是否有重复的day_index
      const uniqueIndices = new Set(planData.days.map((d: any) => d.day_index))
      if (uniqueIndices.size !== planData.days.length) {
        console.warn('⚠️ 检测到重复的day_index，重新分配...')
        planData.days.forEach((day: any, i: number) => {
          day.day_index = i
        })
      }
    }
    
    tripPlan.value = planData
    activeDays.value = tripPlan.value.days.map((_, i) => i)
    await loadAttractionPhotos()
    await nextTick()
    initMap()
    // 初始化所有天的迷你地图
    tripPlan.value.days.forEach((_, i) => initDayMiniMap(i))
  }
})

const goBack = () => {
  router.push('/')
}

// 滚动到指定区域
const scrollToSection = ({ key }: { key: string }) => {
  activeSection.value = key
  const element = document.getElementById(key)
  if (element) {
    element.scrollIntoView({ behavior: 'smooth', block: 'start' })
  }
}

// 切换编辑模式
const toggleEditMode = () => {
  editMode.value = true
  // 保存原始数据用于取消编辑
  originalPlan.value = JSON.parse(JSON.stringify(tripPlan.value))
  message.info('进入编辑模式')
}

// 保存修改
const saveChanges = () => {
  editMode.value = false
  // 更新sessionStorage
  if (tripPlan.value) {
    sessionStorage.setItem('tripPlan', JSON.stringify(tripPlan.value))
  }
  message.success('修改已保存')

  // 重新初始化地图以反映更改
  if (map) {
    map.destroy()
  }
  nextTick(() => {
    initMap()
  })
}

// 取消编辑
const cancelEdit = () => {
  if (editMode.value && originalPlan.value) {
    tripPlan.value = JSON.parse(JSON.stringify(originalPlan.value))
  }
  editMode.value = false
  diyMode.value = false
  message.info('已取消')
}

// ============ DIY模式 ============
const toggleDiyMode = () => {
  diyMode.value = true
  editMode.value = false
  originalPlan.value = JSON.parse(JSON.stringify(tripPlan.value))
  message.info('进入DIY调整模式，点击🔄按钮替换景点或餐厅')
}

const saveDiyChanges = () => {
  diyMode.value = false
  if (tripPlan.value) {
    sessionStorage.setItem('tripPlan', JSON.stringify(tripPlan.value))
    // 重建地图标记
    if (map) map.destroy()
    nextTick(() => initMap())
  }
  message.success('DIY调整已保存')
}

const openAlternatives = async (type: 'attraction' | 'meal', dayIdx: number, itemIdx: number, item: any) => {
  altType.value = type
  altTargetDay.value = dayIdx
  altTargetIdx.value = itemIdx
  altModalVisible.value = true
  altLoading.value = true
  alternatives.value = []

  // 构建当天上下文
  const day = tripPlan.value!.days[dayIdx]
  const ctxParts = [`第${dayIdx + 1}天，已有景点: ${day.attractions.map((a: any) => a.name).join('、')}`]
  if (day.meals) ctxParts.push(`已安排餐厅: ${day.meals.map((m: any) => m.restaurant || m.name).join('、')}`)

  try {
    const res = await fetch(`${API_BASE_URL}/api/trip/alternatives`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        city: tripPlan.value!.city,
        day_index: dayIdx,
        replace_type: type,
        current_name: item.name || item.restaurant || '',
        current_category: item.category || '',
        context: ctxParts.join('; ')
      })
    })
    const data = await res.json()
    if (data.success && data.data?.alternatives) {
      alternatives.value = data.data.alternatives
    } else {
      message.warning(data.message || '暂无合适备选')
    }
  } catch (err) {
    message.error('获取备选项失败')
  } finally {
    altLoading.value = false
  }
}

const selectAlternative = (idx: number) => {
  const alt = alternatives.value[idx]
  if (!alt || !tripPlan.value) return

  const day = tripPlan.value.days[altTargetDay.value]
  if (altType.value === 'attraction') {
    day.attractions[altTargetIdx.value] = {
      name: alt.name,
      address: alt.address,
      location: alt.location,
      visit_duration: alt.visit_duration || 120,
      description: alt.reason,
      category: alt.category,
      rating: alt.rating,
      ticket_price: alt.estimated_cost
    }
  } else {
    day.meals[altTargetIdx.value] = {
      ...day.meals[altTargetIdx.value],
      name: alt.recommended_dish || alt.name,
      restaurant: alt.name,
      address: alt.address,
      location: alt.location,
      description: alt.reason,
      recommended_dish: alt.recommended_dish || '',
      estimated_cost: alt.estimated_cost || 0
    }
  }

  altModalVisible.value = false
  message.success(`已替换为: ${alt.name}`)
}

// ============ DIY 添加景点/餐厅 ============
const openAddModal = (type: 'attraction' | 'meal', dayIdx: number) => {
  addType.value = type
  addTargetDay.value = dayIdx
  addSearchKeyword.value = ''
  addResults.value = []
  addSearchDone.value = false
  addManualName.value = ''
  addManualAddress.value = ''
  addModalVisible.value = true
}

const handleAddSearch = async () => {
  const kw = addSearchKeyword.value.trim()
  if (!kw) return
  addSearchLoading.value = true
  addResults.value = []
  addSearchDone.value = false
  try {
    const res = await fetch(`${API_BASE_URL}/api/trip/search-poi`, {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ keyword: kw, city: tripPlan.value!.city })
    })
    const data = await res.json()
    if (data.success && data.data?.results) {
      addResults.value = data.data.results
    }
  } catch (e) { message.error('搜索失败') }
  finally { addSearchLoading.value = false; addSearchDone.value = true }
}

const confirmAddItem = (item: any) => {
  if (!tripPlan.value) return
  const day = tripPlan.value.days[addTargetDay.value]
  if (addType.value === 'attraction') {
    day.attractions.push({
      name: item.name, address: item.address || '',
      location: item.location || { longitude: 116.4, latitude: 39.9 },
      visit_duration: item.visit_duration || 120,
      description: item.reason || '', category: item.category || '景点',
      ticket_price: item.estimated_cost || 0
    })
  } else {
    day.meals.push({
      type: 'lunch', name: item.recommended_dish || item.name,
      restaurant: item.name, address: item.address || '',
      location: item.location || { longitude: 116.4, latitude: 39.9 },
      description: item.reason || '', recommended_dish: item.recommended_dish || '',
      estimated_cost: item.estimated_cost || 0
    })
  }
  addModalVisible.value = false
  message.success(`已添加: ${item.name}`)
}

const confirmManualAdd = () => {
  const name = addManualName.value.trim()
  if (!name) { message.warning('请输入名称'); return }
  confirmAddItem({
    name, address: addManualAddress.value || `${tripPlan.value!.city}市`,
    location: { longitude: 116.4, latitude: 39.9 },
    reason: '手动添加', estimated_cost: 0
  })
}

// ============ 智能优化路线 ============
const handleReoptimize = async (dayIdx: number) => {
  if (!tripPlan.value) return
  reoptLoading.value = dayIdx
  const day = tripPlan.value.days[dayIdx]
  const points: any[] = []

  // 收集所有有坐标的点
  day.attractions.forEach((a, i) => {
    if (a.location?.latitude) points.push({ ...a, type: 'attraction', lat: a.location.latitude, lng: a.location.longitude, origIdx: i, origArray: 'attractions' })
  })
  day.meals.forEach((m, i) => {
    if (m.location?.latitude) points.push({ ...m, type: 'meal', lat: m.location.latitude, lng: m.location.longitude, origIdx: i, origArray: 'meals' })
  })

  if (points.length < 2) { reoptLoading.value = -1; message.warning('至少需要2个有坐标的点才能优化'); return }

  try {
    const res = await fetch(`${API_BASE_URL}/api/trip/reoptimize`, {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ day_points: points.map(p => ({ lat: p.lat, lng: p.lng, type: p.type })) })
    })
    const data = await res.json()
    if (data.success && data.data?.optimized) {
      const optimized = data.data.optimized
      // 按优化后的顺序重排
      const newAttr: any[] = [], newMeal: any[] = []
      optimized.forEach((opt: any) => {
        const orig = points.find(p => Math.abs(p.lat - opt.lat) < 0.0001 && Math.abs(p.lng - opt.lng) < 0.0001)
        if (orig) {
          if (orig.origArray === 'attractions') newAttr.push(day.attractions[orig.origIdx])
          else newMeal.push(day.meals[orig.origIdx])
        }
      })
      if (newAttr.length === day.attractions.length) day.attractions = newAttr
      if (newMeal.length === day.meals.length) day.meals = newMeal
      message.success('路线已优化')
    }
  } catch (e) { message.error('优化失败') }
  finally { reoptLoading.value = -1 }
}

// 删除景点
const deleteAttraction = (dayIndex: number, attrIndex: number) => {
  if (!tripPlan.value) return

  const day = tripPlan.value.days[dayIndex]
  if (day.attractions.length <= 1) {
    message.warning('每天至少需要保留一个景点')
    return
  }

  day.attractions.splice(attrIndex, 1)
  message.success('景点已删除')
}

// 移动景点顺序
const moveAttraction = (dayIndex: number, attrIndex: number, direction: 'up' | 'down') => {
  if (!tripPlan.value) return

  const day = tripPlan.value.days[dayIndex]
  const attractions = day.attractions

  if (direction === 'up' && attrIndex > 0) {
    [attractions[attrIndex], attractions[attrIndex - 1]] = [attractions[attrIndex - 1], attractions[attrIndex]]
  } else if (direction === 'down' && attrIndex < attractions.length - 1) {
    [attractions[attrIndex], attractions[attrIndex + 1]] = [attractions[attrIndex + 1], attractions[attrIndex]]
  }
}

const getMealLabel = (type: string): string => {
  const labels: Record<string, string> = {
    breakfast: '☀️ 早餐',
    lunch: '🌤️ 午餐',
    dinner: '🌙 晚餐',
    snack: '🍿 小吃'
  }
  return labels[type] || type
}

// 获取美食图片 - 优先级: LLM提供的image_url > API获取 > SVG占位图
const getMealImage = (meal: any, dayIndex: number): string => {
  // 优先级1: LLM结构化输出提供的image_url
  if (meal.image_url) {
    return meal.image_url
  }

  // 优先级2: 缓存的API获取图片
  const searchName = meal.restaurant || meal.name
  const key = `${searchName}_${dayIndex}`
  if (mealPhotos.value[key]) {
    return mealPhotos.value[key]
  }

  // 优先级3: 使用SVG占位图
  const gradients = [
    { start: '#e74c3c', end: '#c0392b', icon: '🍜', pattern: 'noodles' },
    { start: '#f39c12', end: '#d35400', icon: '🥘', pattern: 'hotpot' },
    { start: '#e91e63', end: '#ad1457', icon: '🍖', pattern: 'meat' },
    { start: '#ff5722', end: '#bf360c', icon: '🦐', pattern: 'seafood' },
    { start: '#ff9800', end: '#e65100', icon: '🥟', pattern: 'dimsum' },
    { start: '#8e44ad', end: '#6a1b9a', icon: '🍰', pattern: 'dessert' },
  ]
  const g = gradients[dayIndex % gradients.length]
  const showName = (meal.restaurant || meal.name || '').slice(0, 12)
  const showDish = (meal.recommended_dish || '').slice(0, 10)

  const svg = `<svg xmlns="http://www.w3.org/2000/svg" width="400" height="280">
    <defs>
      <linearGradient id="mg" x1="0%" y1="0%" x2="100%" y2="100%">
        <stop offset="0%" style="stop-color:${g.start}"/><stop offset="100%" style="stop-color:${g.end}"/>
      </linearGradient>
      <pattern id="dots" width="20" height="20" patternUnits="userSpaceOnUse">
        <circle cx="10" cy="10" r="1.5" fill="rgba(255,255,255,0.15)"/>
      </pattern>
    </defs>
    <rect width="400" height="280" fill="url(#mg)"/>
    <rect width="400" height="280" fill="url(#dots)"/>
    <circle cx="200" cy="100" r="52" fill="rgba(255,255,255,0.18)" stroke="rgba(255,255,255,0.3)" stroke-width="2"/>
    <text x="200" y="116" text-anchor="middle" font-size="52" fill="white">${g.icon}</text>
    ${showName ? `<text x="200" y="188" text-anchor="middle" font-family="system-ui,sans-serif" font-size="16" font-weight="800" fill="white">${showName}</text>` : ''}
    ${showDish ? `<text x="200" y="212" text-anchor="middle" font-family="system-ui,sans-serif" font-size="13" fill="rgba(255,255,255,0.9)">🥢 ${showDish}</text>` : ''}
    <text x="200" y="248" text-anchor="middle" font-family="system-ui,sans-serif" font-size="11" fill="rgba(255,255,255,0.5)">${showName ? '美食推荐' : '当地美食'}</text>
  </svg>`

  return svgToDataUrl(svg)
}

// 加载所有景点图片和美食图片
const loadAttractionPhotos = async () => {
  if (!tripPlan.value) return

  const city = encodeURIComponent(tripPlan.value.city)
  const promises: Promise<void>[] = []

  tripPlan.value.days.forEach(day => {
    // 景点图片 - 使用相对路径，通过Vite代理
    day.attractions.forEach(attraction => {
      const promise = fetch(`${API_BASE_URL}/api/poi/photo?name=${encodeURIComponent(attraction.name)}&city=${city}`)
        .then(res => res.json())
        .then(data => {
          if (data.success && data.data.photo_url) {
            attractionPhotos.value[attraction.name] = data.data.photo_url
          }
        })
        .catch(err => {
          console.error(`获取${attraction.name}图片失败:`, err)
        })
      promises.push(promise)
    })

    // 美食图片 - 多策略短查询
    day.meals.forEach(meal => {
      const restName = (meal.restaurant || meal.name || '').slice(0, 6)  // 短名称
      const dishName = (meal.recommended_dish || '').slice(0, 6)
      const key = `${restName}_${day.day_index}`

      // 策略1: 短餐厅名
      const p1 = restName
        ? fetch(`${API_BASE_URL}/api/poi/photo?name=${encodeURIComponent(restName)}&city=${city}`)
            .then(res => res.json()).then(data => {
              if (data.success && data.data?.photo_url) mealPhotos.value[key] = data.data.photo_url
            }).catch(() => {})
        : Promise.resolve()

      // 策略2: 推荐菜
      const p2 = dishName
        ? fetch(`${API_BASE_URL}/api/poi/photo?name=${encodeURIComponent(dishName)}&city=${city}`)
            .then(res => res.json()).then(data => {
              if (data.success && data.data?.photo_url && !mealPhotos.value[key]) mealPhotos.value[key] = data.data.photo_url
            }).catch(() => {})
        : Promise.resolve()

      // 策略3: 通用美食
      const p3 = !mealPhotos.value[key]
        ? fetch(`${API_BASE_URL}/api/poi/photo?name=food&city=${city}`)
            .then(res => res.json()).then(data => {
              if (data.success && data.data?.photo_url && !mealPhotos.value[key]) mealPhotos.value[key] = data.data.photo_url
            }).catch(() => {})
        : Promise.resolve()

      promises.push(p1, p2, p3)
    })
  })

  await Promise.all(promises)
}

// 获取景点图片
const getAttractionImage = (name: string, index: number): string => {
  if (attractionPhotos.value[name]) {
    return attractionPhotos.value[name]
  }

  const gradients = [
    { start: '#667eea', end: '#764ba2', icon: '🏛️' },
    { start: '#f093fb', end: '#f5576c', icon: '🌸' },
    { start: '#4facfe', end: '#00f2fe', icon: '🏔️' },
    { start: '#43e97b', end: '#38f9d7', icon: '🌿' },
    { start: '#fa709a', end: '#fee140', icon: '🎨' },
  ]
  const g = gradients[index % gradients.length]
  const shortName = name.length > 8 ? name.slice(0, 8) + '...' : name

  const svg = `<svg xmlns="http://www.w3.org/2000/svg" width="400" height="300">
    <defs><linearGradient id="g" x1="0%" y1="0%" x2="100%" y2="100%">
      <stop offset="0%" style="stop-color:${g.start}"/><stop offset="100%" style="stop-color:${g.end}"/>
    </linearGradient></defs>
    <rect width="400" height="300" fill="url(#g)"/>
    <circle cx="200" cy="110" r="55" fill="rgba(255,255,255,0.2)"/>
    <text x="200" y="125" text-anchor="middle" font-size="48" fill="white">${g.icon}</text>
    <text x="200" y="195" text-anchor="middle" font-family="system-ui,sans-serif" font-size="18" font-weight="700" fill="white">${shortName}</text>
    <text x="200" y="220" text-anchor="middle" font-family="system-ui,sans-serif" font-size="11" fill="rgba(255,255,255,0.7)">景点图片</text>
  </svg>`

  return svgToDataUrl(svg)
}

// SVG转data URL (安全替代unescape)
const svgToDataUrl = (svg: string): string => {
  const encoded = new TextEncoder().encode(svg)
  let binary = ''
  encoded.forEach(b => binary += String.fromCharCode(b))
  return 'data:image/svg+xml;base64,' + btoa(binary)
}

// 图片加载失败时的处理
const handleImageError = (event: Event) => {
  const img = event.target as HTMLImageElement
  // 使用灰色占位图
  img.src = 'data:image/svg+xml,%3Csvg xmlns="http://www.w3.org/2000/svg" width="400" height="300"%3E%3Crect width="400" height="300" fill="%23f0f0f0"/%3E%3Ctext x="50%25" y="50%25" dominant-baseline="middle" text-anchor="middle" font-family="sans-serif" font-size="18" fill="%23999"%3E图片加载失败%3C/text%3E%3C/svg%3E'
}



// 导出为图片
const exportAsImage = async () => {
  try {
    message.loading({ content: '正在生成图片...', key: 'export', duration: 0 })

    const element = document.querySelector('.main-content') as HTMLElement
    if (!element) {
      throw new Error('未找到内容元素')
    }

    // 创建一个独立的容器
    const exportContainer = document.createElement('div')
    exportContainer.style.width = element.offsetWidth + 'px'
    exportContainer.style.backgroundColor = '#f5f7fa'
    exportContainer.style.padding = '20px'

    // 复制所有内容
    exportContainer.innerHTML = element.innerHTML

    // 处理地图截图
    const mapContainer = document.getElementById('amap-container')
    if (mapContainer && map) {
      const mapCanvas = mapContainer.querySelector('canvas')
      if (mapCanvas) {
        const mapSnapshot = mapCanvas.toDataURL('image/png')
        const exportMapContainer = exportContainer.querySelector('#amap-container')
        if (exportMapContainer) {
          exportMapContainer.innerHTML = `<img src="${mapSnapshot}" style="width:100%;height:100%;object-fit:cover;" />`
        }
      }
    }

    // 移除所有ant-card类,替换为纯div
    const cards = exportContainer.querySelectorAll('.ant-card')
    cards.forEach((card) => {
      const cardEl = card as HTMLElement
      try {
        cardEl.className = '' // 移除所有类
        cardEl.style.setProperty('background-color', '#ffffff')
        cardEl.style.setProperty('border-radius', '12px')
        cardEl.style.setProperty('box-shadow', '0 4px 12px rgba(0, 0, 0, 0.1)')
        cardEl.style.setProperty('margin-bottom', '20px')
        cardEl.style.setProperty('overflow', 'hidden')
      } catch (err) {
        console.error('设置卡片样式失败:', err)
      }
    })

    // 处理卡片头部
    const cardHeads = exportContainer.querySelectorAll('.ant-card-head')
    cardHeads.forEach((head) => {
      const headEl = head as HTMLElement
      try {
        headEl.style.setProperty('background-color', '#667eea')
        headEl.style.setProperty('color', '#ffffff')
        headEl.style.setProperty('padding', '16px 24px')
        headEl.style.setProperty('font-size', '18px')
        headEl.style.setProperty('font-weight', '600')
      } catch (err) {
        console.error('设置卡片头部样式失败:', err)
      }
    })

    // 处理卡片内容
    const cardBodies = exportContainer.querySelectorAll('.ant-card-body')
    cardBodies.forEach((body) => {
      const bodyEl = body as HTMLElement
      bodyEl.style.setProperty('background-color', '#ffffff')
      bodyEl.style.setProperty('padding', '24px')
    })

    // 处理酒店卡片头部
    const hotelCards = exportContainer.querySelectorAll('.hotel-card')
    hotelCards.forEach((card) => {
      const head = card.querySelector('.ant-card-head') as HTMLElement
      if (head) {
        head.style.setProperty('background-color', '#1976d2')
      }
      (card as HTMLElement).style.setProperty('background-color', '#e3f2fd')
    })

    // 处理天气卡片
    const weatherCards = exportContainer.querySelectorAll('.weather-card')
    weatherCards.forEach((card) => {
      (card as HTMLElement).style.setProperty('background-color', '#e0f7fa')
    })

    // 处理预算总计
    const budgetTotal = exportContainer.querySelector('.budget-total')
    if (budgetTotal) {
      const el = budgetTotal as HTMLElement
      el.style.setProperty('background-color', '#667eea')
      el.style.setProperty('color', '#ffffff')
      el.style.setProperty('padding', '20px')
      el.style.setProperty('border-radius', '12px')
      el.style.setProperty('margin-bottom', '20px')
    }

    // 处理预算项
    const budgetItems = exportContainer.querySelectorAll('.budget-item')
    budgetItems.forEach((item) => {
      const el = item as HTMLElement
      el.style.setProperty('background-color', '#f5f7fa')
      el.style.setProperty('padding', '16px')
      el.style.setProperty('border-radius', '8px')
      el.style.setProperty('margin-bottom', '12px')
    })

    // 添加到body(隐藏)
    exportContainer.style.position = 'absolute'
    exportContainer.style.left = '-9999px'
    document.body.appendChild(exportContainer)

    const canvas = await html2canvas(exportContainer, {
      backgroundColor: '#f5f7fa',
      scale: 2,
      logging: false,
      useCORS: true,
      allowTaint: true
    })

    // 移除容器
    document.body.removeChild(exportContainer)

    // 转换为图片并下载
    const link = document.createElement('a')
    link.download = `旅行计划_${tripPlan.value?.city}_${new Date().getTime()}.png`
    link.href = canvas.toDataURL('image/png')
    link.click()

    message.success({ content: '图片导出成功!', key: 'export' })
  } catch (error: any) {
    console.error('导出图片失败:', error)
    message.error({ content: `导出图片失败: ${error.message}`, key: 'export' })
  }
}

// 导出为PDF
const exportAsPDF = async () => {
  try {
    message.loading({ content: '正在生成PDF...', key: 'export', duration: 0 })

    const element = document.querySelector('.main-content') as HTMLElement
    if (!element) {
      throw new Error('未找到内容元素')
    }

    // 创建一个独立的容器
    const exportContainer = document.createElement('div')
    exportContainer.style.width = element.offsetWidth + 'px'
    exportContainer.style.backgroundColor = '#f5f7fa'
    exportContainer.style.padding = '20px'

    // 复制所有内容
    exportContainer.innerHTML = element.innerHTML

    // 处理地图截图
    const mapContainer = document.getElementById('amap-container')
    if (mapContainer && map) {
      const mapCanvas = mapContainer.querySelector('canvas')
      if (mapCanvas) {
        const mapSnapshot = mapCanvas.toDataURL('image/png')
        const exportMapContainer = exportContainer.querySelector('#amap-container')
        if (exportMapContainer) {
          exportMapContainer.innerHTML = `<img src="${mapSnapshot}" style="width:100%;height:100%;object-fit:cover;" />`
        }
      }
    }

    // 移除所有ant-card类,替换为纯div
    const cards = exportContainer.querySelectorAll('.ant-card')
    cards.forEach((card) => {
      const cardEl = card as HTMLElement
      try {
        cardEl.className = ''
        cardEl.style.setProperty('background-color', '#ffffff')
        cardEl.style.setProperty('border-radius', '12px')
        cardEl.style.setProperty('box-shadow', '0 4px 12px rgba(0, 0, 0, 0.1)')
        cardEl.style.setProperty('margin-bottom', '20px')
        cardEl.style.setProperty('overflow', 'hidden')
      } catch (err) {
        console.error('设置卡片样式失败:', err)
      }
    })

    // 处理卡片头部
    const cardHeads = exportContainer.querySelectorAll('.ant-card-head')
    cardHeads.forEach((head) => {
      const headEl = head as HTMLElement
      try {
        headEl.style.setProperty('background-color', '#667eea')
        headEl.style.setProperty('color', '#ffffff')
        headEl.style.setProperty('padding', '16px 24px')
        headEl.style.setProperty('font-size', '18px')
        headEl.style.setProperty('font-weight', '600')
      } catch (err) {
        console.error('设置卡片头部样式失败:', err)
      }
    })

    // 处理卡片内容
    const cardBodies = exportContainer.querySelectorAll('.ant-card-body')
    cardBodies.forEach((body) => {
      const bodyEl = body as HTMLElement
      bodyEl.style.setProperty('background-color', '#ffffff')
      bodyEl.style.setProperty('padding', '24px')
    })

    // 处理酒店卡片头部
    const hotelCards = exportContainer.querySelectorAll('.hotel-card')
    hotelCards.forEach((card) => {
      const head = card.querySelector('.ant-card-head') as HTMLElement
      if (head) {
        head.style.setProperty('background-color', '#1976d2')
      }
      (card as HTMLElement).style.setProperty('background-color', '#e3f2fd')
    })

    // 处理天气卡片
    const weatherCards = exportContainer.querySelectorAll('.weather-card')
    weatherCards.forEach((card) => {
      (card as HTMLElement).style.setProperty('background-color', '#e0f7fa')
    })

    // 处理预算总计
    const budgetTotal = exportContainer.querySelector('.budget-total')
    if (budgetTotal) {
      const el = budgetTotal as HTMLElement
      el.style.setProperty('background-color', '#667eea')
      el.style.setProperty('color', '#ffffff')
      el.style.setProperty('padding', '20px')
      el.style.setProperty('border-radius', '12px')
      el.style.setProperty('margin-bottom', '20px')
    }

    // 处理预算项
    const budgetItems = exportContainer.querySelectorAll('.budget-item')
    budgetItems.forEach((item) => {
      const el = item as HTMLElement
      el.style.setProperty('background-color', '#f5f7fa')
      el.style.setProperty('padding', '16px')
      el.style.setProperty('border-radius', '8px')
      el.style.setProperty('margin-bottom', '12px')
    })

    // 添加到body(隐藏)
    exportContainer.style.position = 'absolute'
    exportContainer.style.left = '-9999px'
    document.body.appendChild(exportContainer)

    const canvas = await html2canvas(exportContainer, {
      backgroundColor: '#f5f7fa',
      scale: 2,
      logging: false,
      useCORS: true,
      allowTaint: true
    })

    // 移除容器
    document.body.removeChild(exportContainer)

    const imgData = canvas.toDataURL('image/png')
    const pdf = new jsPDF({
      orientation: 'portrait',
      unit: 'mm',
      format: 'a4'
    })

    const imgWidth = 210 // A4宽度(mm)
    const imgHeight = (canvas.height * imgWidth) / canvas.width

    // 如果内容高度超过一页,分页处理
    let heightLeft = imgHeight
    let position = 0

    pdf.addImage(imgData, 'PNG', 0, position, imgWidth, imgHeight)
    heightLeft -= 297 // A4高度

    while (heightLeft > 0) {
      position = heightLeft - imgHeight
      pdf.addPage()
      pdf.addImage(imgData, 'PNG', 0, position, imgWidth, imgHeight)
      heightLeft -= 297
    }

    pdf.save(`旅行计划_${tripPlan.value?.city}_${new Date().getTime()}.pdf`)

    message.success({ content: 'PDF导出成功!', key: 'export' })
  } catch (error: any) {
    console.error('导出PDF失败:', error)
    message.error({ content: `导出PDF失败: ${error.message}`, key: 'export' })
  }
}

// 截取地图图片
const captureMapImage = async () => {
  if (!map) return

  try {
    // 获取地图容器
    const mapContainer = document.getElementById('amap-container')
    if (!mapContainer) return

    // 使用高德地图的截图功能
    const mapCanvas = mapContainer.querySelector('canvas')
    if (mapCanvas) {
      // 创建一个img元素替换地图容器
      const img = document.createElement('img')
      img.src = mapCanvas.toDataURL('image/png')
      img.style.width = '100%'
      img.style.height = '500px'
      img.style.objectFit = 'cover'
      img.id = 'map-snapshot'

      // 隐藏原地图,显示截图
      mapContainer.style.display = 'none'
      mapContainer.parentElement?.appendChild(img)
    }
  } catch (error) {
    console.error('截取地图失败:', error)
  }
}

// 恢复地图
const restoreMap = () => {
  const mapContainer = document.getElementById('amap-container')
  const snapshot = document.getElementById('map-snapshot')

  if (mapContainer) {
    mapContainer.style.display = 'block'
  }

  if (snapshot) {
    snapshot.remove()
  }
}

// 初始化地图
const initMap = async () => {
  try {
    const AMap = await AMapLoader.load({
      key: import.meta.env.VITE_AMAP_WEB_JS_KEY,  // 高德地图Web端(JS API) Key
      version: '2.0',
      plugins: ['AMap.Marker', 'AMap.Polyline', 'AMap.InfoWindow']
    })

    // 创建地图实例
    map = new AMap.Map('amap-container', {
      zoom: 12,
      center: [116.397128, 39.916527], // 默认中心点(北京)
      viewMode: '3D'
    })

    // 添加景点标记
    addAttractionMarkers(AMap)

    message.success('地图加载成功')
  } catch (error) {
    console.error('地图加载失败:', error)
    message.error('地图加载失败')
  }
}

// 初始化每日迷你地图
const initDayMiniMap = async (dayIndex: number) => {
  if (!tripPlan.value || dayMapsInitialized.value[dayIndex]) return
  const day = tripPlan.value.days[dayIndex]
  const containerId = `day-mini-map-${dayIndex}`

  // 等待DOM
  await nextTick()
  await new Promise(r => setTimeout(r, 200))

  const container = document.getElementById(containerId)
  if (!container || container.offsetParent === null) return

  try {
    const AMap = await AMapLoader.load({
      key: import.meta.env.VITE_AMAP_WEB_JS_KEY,
      version: '2.0',
      plugins: ['AMap.Marker', 'AMap.Polyline', 'AMap.DrivingRoute', 'AMap.WalkingRoute', 'AMap.Transfer']
    })

    const miniMap = new AMap.Map(containerId, { zoom: 13, viewMode: '2D' })
    dayMiniMaps[dayIndex] = miniMap
    dayMapsInitialized.value[dayIndex] = true

    // 收集所有途径点（按时间顺序排列）
    const waypoints: any[] = []

    // 早餐
    const breakfast = day.meals.find((m: any) => m.type === 'breakfast')
    if (breakfast?.location?.longitude) {
      waypoints.push({ ...breakfast, type: 'breakfast', label: '早餐', icon: '☀️', color: '#FF9800', time: '08:00' })
    }
    
    // 将景点按时段分组
    const morningAttractions = day.attractions.filter((a: any) => a.time_period === 'morning')
    const afternoonAttractions = day.attractions.filter((a: any) => a.time_period === 'afternoon' || a.time_period === 'evening')
    const uncategorizedAttractions = day.attractions.filter((a: any) => !a.time_period)
    
    // 上午景点
    morningAttractions.forEach((attr: any) => {
      if (attr.location?.longitude) {
        waypoints.push({ ...attr, type: 'attraction', label: attr.name, icon: '🏛️', color: '#4CAF50', time: attr.time })
      }
    })
    
    // 未分类景点：前半部分放上午
    if (uncategorizedAttractions.length > 0) {
      const midpoint = Math.ceil(uncategorizedAttractions.length / 2)
      const morningHalf = uncategorizedAttractions.slice(0, midpoint)
      
      morningHalf.forEach((attr: any) => {
        if (attr.location?.longitude) {
          waypoints.push({ ...attr, type: 'attraction', label: attr.name, icon: '🏛️', color: '#4CAF50', time: attr.time })
        }
      })
    }
    
    // 午餐
    const lunch = day.meals.find((m: any) => m.type === 'lunch')
    if (lunch?.location?.longitude) {
      waypoints.push({ ...lunch, type: 'lunch', label: '午餐', icon: '🌤️', color: '#FF9800', time: '12:00' })
    }
    
    // 下午景点
    afternoonAttractions.forEach((attr: any) => {
      if (attr.location?.longitude) {
        waypoints.push({ ...attr, type: 'attraction', label: attr.name, icon: '🏛️', color: '#4CAF50', time: attr.time })
      }
    })
    
    // 未分类的后半部分景点放到下午
    if (uncategorizedAttractions.length > 0) {
      const midpoint = Math.ceil(uncategorizedAttractions.length / 2)
      const afternoonHalf = uncategorizedAttractions.slice(midpoint)
      afternoonHalf.forEach((attr: any) => {
        if (attr.location?.longitude) {
          waypoints.push({ ...attr, type: 'attraction', label: attr.name, icon: '🏛️', color: '#4CAF50', time: attr.time })
        }
      })
    }
    
    // 晚餐
    const dinner = day.meals.find((m: any) => m.type === 'dinner')
    if (dinner?.location?.longitude) {
      waypoints.push({ ...dinner, type: 'dinner', label: '晚餐', icon: '🌙', color: '#FF9800', time: '18:00' })
    }
    
    // 酒店
    if (day.hotel?.location?.longitude) {
      waypoints.push({ ...day.hotel, type: 'hotel', label: day.hotel.name, icon: '🏨', color: '#1976d2' })
    }

    if (waypoints.length === 0) return

    // 添加标记
    const markers: any[] = []
    waypoints.forEach((wp, i) => {
      const isMeal = ['breakfast', 'lunch', 'dinner'].includes(wp.type)
      const markerColor = isMeal ? '#FF6B35' : (wp.type === 'hotel' ? '#1976d2' : '#4CAF50')
      const marker = new AMap.Marker({
        position: [wp.location.longitude, wp.location.latitude],
        title: wp.label,
        label: {
          content: `<div style="background:${markerColor};color:white;padding:2px 6px;border-radius:4px;font-size:11px;">${wp.icon} ${wp.label}</div>`,
          offset: new AMap.Pixel(0, -25)
        }
      })
      markers.push(marker)
    })
    miniMap.add(markers)
    miniMap.setFitView(markers, false, [60, 60, 60, 60])

    // 绘制路线
    if (waypoints.length >= 2) {
      const path = waypoints.map((wp: any) => [wp.location.longitude, wp.location.latitude])
      const polyline = new AMap.Polyline({
        path, strokeColor: '#667eea', strokeWeight: 4, strokeOpacity: 0.8,
        strokeStyle: 'solid', showDir: true
      })
      miniMap.add(polyline)
    }

    // 用高德公交API获取真实线路信息
    const routeSegments = await computeRealTransit(AMap, waypoints, day.transportation)
    dayRouteInfo.value[dayIndex] = routeSegments

  } catch (err) {
    console.error(`第${dayIndex + 1}天迷你地图加载失败:`, err)
  }
}

// 使用高德公交API获取真实线路
const computeRealTransit = async (AMap: any, waypoints: any[], transportation: string) => {
  const segments: any[] = []

  for (let i = 0; i < waypoints.length - 1; i++) {
    const from = waypoints[i]
    const to = waypoints[i + 1]
    const fromLngLat = [from.location.longitude, from.location.latitude]
    const toLngLat = [to.location.longitude, to.location.latitude]

    // 计算直线距离
    const distKm = haversine(from.location.latitude, from.location.longitude, to.location.latitude, to.location.longitude)
    const distDisplay = distKm < 1 ? `${Math.round(distKm * 1000)}m` : `${distKm.toFixed(1)}km`

    // 根据距离和交通方式选择搜索策略
    if (distKm < 1.0) {
      // 短距离: 步行
      segments.push({
        mode: '🚶 步行',
        from: from.label || from.name,
        to: to.label || to.name,
        distance: distDisplay,
        duration: Math.round((distKm / 5) * 60),
        stations: null
      })
    } else if (transportation === '自驾') {
      // 驾车路线
      try {
        const result = await new Promise<any>((resolve) => {
          const driving = new AMap.DrivingRoute({ map: null, policy: AMap.DrivingPolicy.LEAST_TIME })
          driving.search(fromLngLat, toLngLat, (status: string, res: any) => {
            resolve(status === 'complete' ? res : null)
          })
        })
        if (result?.routes?.[0]) {
          const route = result.routes[0]
          const steps = route.steps || []
          const roadNames = steps.map((s: any) => s.road || s.instruction).filter(Boolean).slice(0, 3).join(' → ')
          segments.push({
            mode: '🚗 驾车',
            from: from.label || from.name,
            to: to.label || to.name,
            distance: distDisplay,
            duration: Math.round(route.time / 60),
            stations: roadNames ? { boarding: '出发', alighting: '到达', line: roadNames } : null
          })
        } else { segments.push(fallbackSegment(from, to, distKm, distDisplay, '🚗 驾车')) }
      } catch { segments.push(fallbackSegment(from, to, distKm, distDisplay, '🚗 驾车')) }

    } else {
      // 公交/地铁: 使用TransitRoute获取真实线路
      try {
        const result = await new Promise<any>((resolve) => {
          const transfer = new AMap.Transfer({ map: null, policy: AMap.TransferPolicy.LEAST_TIME, city: '全国' })
          transfer.search(fromLngLat, toLngLat, (status: string, res: any) => {
            resolve(status === 'complete' ? res : null)
          })
        })

        if (result?.plans?.[0]) {
          const plan = result.plans[0]
          const route = plan.routes || []
          // 提取公交/地铁线路名
          const lines: string[] = []
          let boarding = '', alighting = ''
          route.forEach((seg: any) => {
            if (seg.transit?.lines) {
              seg.transit.lines.forEach((l: any) => {
                lines.push(l.name || l.type || '公交')
              })
            }
            if (!boarding && seg.walking?.steps?.[0]) {
              boarding = seg.walking.steps[0].road || '起点'
            }
          })
          const lineStr = lines.length > 0 ? lines.join(' → ') : '公交/地铁'

          segments.push({
            mode: '🚌 公交/地铁',
            from: from.label || from.name,
            to: to.label || to.name,
            distance: distDisplay,
            duration: Math.round(plan.time / 60),
            stations: {
              boarding: from.label || from.name,
              alighting: to.label || to.name,
              line: lineStr
            }
          })
        } else {
          segments.push(fallbackSegment(from, to, distKm, distDisplay, '🚌 公交/地铁'))
        }
      } catch {
        segments.push(fallbackSegment(from, to, distKm, distDisplay, '🚌 公交/地铁'))
      }
    }
  }

  return segments
}

const fallbackSegment = (from: any, to: any, distKm: number, distDisplay: string, mode: string) => ({
  mode,
  from: from.label || from.name,
  to: to.label || to.name,
  distance: distDisplay,
  duration: Math.round((distKm / 20) * 60),
  stations: { boarding: from.label || from.name, alighting: to.label || to.name, line: '公交/地铁线路' }
})

const haversine = (lat1: number, lon1: number, lat2: number, lon2: number) => {
  const R = 6371
  const dLat = (lat2 - lat1) * Math.PI / 180
  const dLon = (lon2 - lon1) * Math.PI / 180
  const a = Math.sin(dLat / 2) ** 2 + Math.cos(lat1 * Math.PI / 180) * Math.cos(lat2 * Math.PI / 180) * Math.sin(dLon / 2) ** 2
  return R * 2 * Math.atan2(Math.sqrt(a), Math.sqrt(1 - a))
}

// 添加所有标记（景点+餐厅）
const addAttractionMarkers = (AMap: any) => {
  if (!tripPlan.value) return

  const markers: any[] = []
  const allPoints: any[] = []
  let markerIdx = 0

  // 只收集景点（概览地图不显示餐厅）
  tripPlan.value.days.forEach((day, dayIndex) => {
    day.attractions.forEach((attraction, attrIndex) => {
      if (attraction.location?.longitude && attraction.location?.latitude) {
        allPoints.push({ ...attraction, dayIndex, attrIndex, type: 'attraction' })

        const marker = new AMap.Marker({
          position: [attraction.location.longitude, attraction.location.latitude],
          title: attraction.name,
          label: {
            content: `<div style="background: #4CAF50; color: white; padding: 4px 8px; border-radius: 4px; font-size: 12px;">D${dayIndex + 1}-${attrIndex + 1}</div>`,
            offset: new AMap.Pixel(0, -30)
          }
        })
        marker.on('click', () => {
          new AMap.InfoWindow({
            content: `<div style="padding:10px;"><h4>🏛️ ${attraction.name}</h4><p>📍 ${attraction.address}</p><p>⏱️ ${attraction.visit_duration}分钟</p><p>第${dayIndex + 1}天 景点${attrIndex + 1}</p></div>`,
            offset: new AMap.Pixel(0, -30)
          }).open(map, marker.getPosition())
        })
        markers.push(marker)
        markerIdx++
      }
    })
  })

  map.add(markers)
  if (markers.length > 0) map.setFitView(markers)
  drawRoutes(AMap, allPoints)
}

// 绘制路线（景点+餐厅作为途经点）
const drawRoutes = (AMap: any, points: any[]) => {
  // 按天分组
  const dayGroups: any = {}
  points.forEach(p => {
    if (!dayGroups[p.dayIndex]) dayGroups[p.dayIndex] = []
    dayGroups[p.dayIndex].push(p)
  })

  Object.values(dayGroups).forEach((dayPoints: any) => {
    if (dayPoints.length < 2) return

    const path = dayPoints.map((p: any) => [p.location.longitude, p.location.latitude])

    const polyline = new AMap.Polyline({
      path,
      strokeColor: '#1890ff',
      strokeWeight: 4,
      strokeOpacity: 0.8,
      strokeStyle: 'solid',
      showDir: true
    })

    map.add(polyline)
  })
}
</script>

<style scoped>
.result-container {
  min-height: 100vh;
  background: linear-gradient(135deg, #f5f7fa 0%, #c3cfe2 100%);
  padding: 40px 20px;
}

.page-header {
  max-width: 1200px;
  margin: 0 auto 30px;
  display: flex;
  justify-content: space-between;
  align-items: center;
  animation: fadeInDown 0.6s ease-out;
}

.back-button {
  border-radius: 8px;
  font-weight: 500;
}

/* 内容布局 */
.content-wrapper {
  max-width: 1400px;
  margin: 0 auto;
  display: flex;
  gap: 24px;
}

.side-nav {
  width: 240px;
  flex-shrink: 0;
}

.side-nav :deep(.ant-menu) {
  border-radius: 12px;
  box-shadow: 0 4px 12px rgba(0, 0, 0, 0.08);
  background: white;
}

.side-nav :deep(.ant-menu-item) {
  margin: 4px 8px;
  border-radius: 8px;
  transition: all 0.3s ease;
}

.side-nav :deep(.ant-menu-item-selected) {
  background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
  color: white;
}

.side-nav :deep(.ant-menu-item:hover) {
  background: rgba(102, 126, 234, 0.1);
}

.main-content {
  flex: 1;
  min-width: 0;
}

/* 景点图片样式 */
.attraction-image-wrapper {
  position: relative;
  margin-bottom: 12px;
  border-radius: 8px;
  overflow: hidden;
}

.attraction-image {
  width: 100%;
  height: 200px;
  object-fit: cover;
  transition: transform 0.3s ease;
}

.attraction-image-wrapper:hover .attraction-image {
  transform: scale(1.05);
}

.attraction-badge {
  position: absolute;
  top: 12px;
  left: 12px;
  background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
  color: white;
  width: 36px;
  height: 36px;
  border-radius: 50%;
  display: flex;
  align-items: center;
  justify-content: center;
  font-weight: bold;
  box-shadow: 0 2px 8px rgba(0, 0, 0, 0.2);
}

.badge-number {
  font-size: 18px;
}

.price-tag {
  position: absolute;
  top: 12px;
  right: 12px;
  background: rgba(255, 77, 79, 0.9);
  color: white;
  padding: 4px 12px;
  border-radius: 12px;
  font-weight: bold;
  font-size: 14px;
  box-shadow: 0 2px 8px rgba(0, 0, 0, 0.2);
}

/* 天气卡片样式 */
.weather-card {
  background: linear-gradient(135deg, #e0f7fa 0%, #b2ebf2 100%);
  border: none !important;
  transition: all 0.3s ease;
}

.weather-card:hover {
  transform: translateY(-4px);
  box-shadow: 0 8px 16px rgba(0, 0, 0, 0.15);
}

.weather-date {
  font-size: 16px;
  font-weight: bold;
  color: #00796b;
  margin-bottom: 12px;
  text-align: center;
}

.weather-info-row {
  display: flex;
  align-items: center;
  gap: 12px;
  margin-bottom: 8px;
}

.weather-icon {
  font-size: 24px;
}

.weather-label {
  font-size: 12px;
  color: #666;
}

.weather-value {
  font-size: 16px;
  font-weight: 600;
  color: #00796b;
}

.weather-wind {
  margin-top: 8px;
  padding-top: 8px;
  border-top: 1px solid rgba(0, 121, 107, 0.2);
  text-align: center;
  color: #00796b;
  font-size: 14px;
}

/* 回到顶部按钮 */
.back-top-button {
  width: 50px;
  height: 50px;
  background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
  color: white;
  border-radius: 50%;
  display: flex;
  align-items: center;
  justify-content: center;
  font-size: 24px;
  font-weight: bold;
  box-shadow: 0 4px 12px rgba(0, 0, 0, 0.3);
  cursor: pointer;
  transition: all 0.3s ease;
}

.back-top-button:hover {
  transform: scale(1.1);
  box-shadow: 0 6px 16px rgba(0, 0, 0, 0.4);
}

/* 酒店卡片样式 */
.hotel-card {
  background: linear-gradient(135deg, #e3f2fd 0%, #bbdefb 100%);
  border: none !important;
}

.hotel-card :deep(.ant-card-head) {
  background: linear-gradient(135deg, #1976d2 0%, #1565c0 100%);
}

.hotel-title {
  color: white !important;
  font-weight: 600;
}

/* 顶部信息区布局 */
.top-info-section {
  display: flex;
  gap: 20px;
  margin-bottom: 20px;
}

.left-info {
  flex: 0 0 400px;
  display: flex;
  flex-direction: column;
  gap: 20px;
}

.right-map {
  flex: 1;
}

/* 行程概览卡片 */
.overview-card {
  height: fit-content;
}

.overview-content {
  display: flex;
  flex-direction: column;
  gap: 12px;
}

.info-item {
  display: flex;
  flex-direction: column;
  gap: 4px;
}

.info-label {
  font-size: 14px;
  font-weight: 600;
  color: #666;
}

.info-value {
  font-size: 15px;
  color: #333;
  line-height: 1.6;
}

/* 预算卡片 */
.budget-card {
  height: fit-content;
}

.budget-grid {
  display: grid;
  grid-template-columns: repeat(2, 1fr);
  gap: 16px;
  margin-bottom: 16px;
}

.budget-item {
  text-align: center;
  padding: 12px;
  background: linear-gradient(135deg, #f5f7fa 0%, #ffffff 100%);
  border-radius: 8px;
  border: 1px solid #e8e8e8;
}

.budget-label {
  font-size: 13px;
  color: #666;
  margin-bottom: 8px;
}

.budget-value {
  font-size: 20px;
  font-weight: 700;
  color: #1890ff;
}

.budget-total {
  display: flex;
  justify-content: space-between;
  align-items: center;
  padding: 16px;
  background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
  border-radius: 8px;
  color: white;
}

.total-label {
  font-size: 16px;
  font-weight: 600;
}

.total-value {
  font-size: 28px;
  font-weight: 700;
}

/* 地图卡片 */
.map-card {
  height: 100%;
  min-height: 500px;
}

.map-card :deep(.ant-card-body) {
  height: calc(100% - 57px);
  padding: 0;
}

/* 每日行程卡片 */
.days-card {
  margin-top: 20px;
}

.day-header {
  display: flex;
  justify-content: space-between;
  align-items: center;
  width: 100%;
}

.day-title {
  font-size: 18px;
  font-weight: 600;
  color: #333;
}

.day-date {
  font-size: 14px;
  color: #999;
}

.day-info {
  margin-bottom: 20px;
  padding: 16px;
  background: linear-gradient(135deg, #f5f7fa 0%, #ffffff 100%);
  border-radius: 8px;
  border: 1px solid #e8e8e8;
}

.info-row {
  display: flex;
  gap: 12px;
  margin-bottom: 8px;
}

.info-row:last-child {
  margin-bottom: 0;
}

.info-row .label {
  font-weight: 600;
  color: #666;
  min-width: 100px;
}

.info-row .value {
  color: #333;
  flex: 1;
}

/* 卡片样式优化 */
:deep(.ant-card) {
  border-radius: 12px;
  box-shadow: 0 4px 12px rgba(0, 0, 0, 0.08);
  margin-bottom: 20px;
  transition: all 0.3s ease;
  animation: fadeInUp 0.6s ease-out;
}

:deep(.ant-card:hover) {
  box-shadow: 0 8px 24px rgba(0, 0, 0, 0.12);
}

:deep(.ant-card-head) {
  background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
  color: white !important;
  border-radius: 12px 12px 0 0;
  font-weight: 600;
}

:deep(.ant-card-head-title) {
  color: white !important;
  font-size: 18px;
}

:deep(.ant-card-head-title span) {
  color: white !important;
}

/* Collapse样式 */
:deep(.ant-collapse) {
  border: none;
  background: transparent;
}

:deep(.ant-collapse-item) {
  margin-bottom: 16px;
  border: 1px solid #e8e8e8;
  border-radius: 12px;
  overflow: hidden;
}

:deep(.ant-collapse-header) {
  background: linear-gradient(135deg, #f5f7fa 0%, #ffffff 100%);
  padding: 16px 20px !important;
  font-weight: 600;
}

:deep(.ant-collapse-content) {
  border-top: 1px solid #e8e8e8;
}

:deep(.ant-collapse-content-box) {
  padding: 20px;
}

/* 统计卡片样式 */
:deep(.ant-statistic-title) {
  font-size: 14px;
  color: #666;
  margin-bottom: 8px;
}

:deep(.ant-statistic-content) {
  font-size: 24px;
  font-weight: 600;
  color: #1890ff;
}

/* 景点卡片样式 */
:deep(.ant-list-item) {
  transition: all 0.3s ease;
}

:deep(.ant-list-item:hover) {
  transform: scale(1.02);
}

/* 动画 */
@keyframes fadeInDown {
  from {
    opacity: 0;
    transform: translateY(-20px);
  }
  to {
    opacity: 1;
    transform: translateY(0);
  }
}

@keyframes fadeInUp {
  from {
    opacity: 0;
    transform: translateY(20px);
  }
  to {
    opacity: 1;
    transform: translateY(0);
  }
}

/* 路线优化提示 */
.route-notes-row {
  background: linear-gradient(135deg, #e3f2fd 0%, #bbdefb 100%);
  padding: 10px 14px;
  border-radius: 8px;
  border-left: 4px solid #1976d2;
}

.route-notes-value {
  color: #1565c0;
  font-weight: 500;
}

/* 美食卡片 */
.meal-card {
  height: 100%;
  transition: all 0.3s ease;
}

.meal-card:hover {
  transform: translateY(-4px);
  box-shadow: 0 8px 20px rgba(0, 0, 0, 0.12);
}

.meal-type-badge {
  position: absolute;
  top: 8px;
  left: 8px;
  background: rgba(0, 0, 0, 0.65);
  color: white;
  padding: 2px 10px;
  border-radius: 10px;
  font-size: 12px;
  font-weight: 600;
  z-index: 1;
}

.meal-image-wrapper {
  position: relative;
  border-radius: 8px;
  overflow: hidden;
  margin: -12px -12px 10px -12px;
}

.meal-image {
  width: 100%;
  height: 160px;
  object-fit: cover;
  transition: transform 0.3s ease;
}

.meal-card:hover .meal-image {
  transform: scale(1.05);
}

.meal-info {
  padding: 4px 0;
}

.meal-name {
  font-size: 15px;
  font-weight: 700;
  color: #333;
  margin-bottom: 4px;
}

.meal-dish {
  font-size: 13px;
  color: #e67e22;
  font-weight: 500;
  margin-bottom: 4px;
}

.meal-address {
  font-size: 12px;
  color: #666;
  margin-bottom: 4px;
  line-height: 1.4;
}

.meal-desc {
  font-size: 12px;
  color: #888;
  margin-bottom: 6px;
  line-height: 1.4;
}

.meal-cost {
  font-size: 14px;
  font-weight: 700;
  color: #e74c3c;
  text-align: right;
}

/* DIY备选项 */
.alt-list {
  display: flex;
  flex-direction: column;
  gap: 10px;
}

.alt-card {
  border: 1px solid #e8e8e8;
  border-radius: 10px;
  cursor: pointer;
  transition: all 0.25s ease;
}

.alt-card:hover {
  border-color: #667eea;
  box-shadow: 0 4px 14px rgba(102, 126, 234, 0.15);
  transform: translateY(-2px);
}

.alt-name {
  font-size: 16px;
  font-weight: 700;
  color: #333;
  margin-bottom: 4px;
}

.alt-address {
  font-size: 13px;
  color: #666;
  margin-bottom: 4px;
}

.alt-meta {
  font-size: 13px;
  color: #1890ff;
  margin-bottom: 4px;
}

.alt-reason {
  font-size: 12px;
  color: #999;
  font-style: italic;
}

.meal-swap-btn {
  position: absolute;
  top: 6px;
  right: 6px;
  z-index: 2;
  border-radius: 50%;
  width: 28px;
  height: 28px;
  padding: 0;
  font-size: 14px;
}

/* 每日迷你地图 */
.day-mini-map {
  width: 100%;
  height: 300px;
  border-radius: 10px;
  margin-bottom: 12px;
  border: 1px solid #e8e8e8;
}

.route-summary {
  background: #f8fafc;
  border-radius: 10px;
  padding: 14px;
  border: 1px solid #e8e8e8;
}

.route-summary-title {
  font-weight: 700;
  font-size: 14px;
  color: #333;
  margin-bottom: 10px;
}

.route-segment {
  padding: 10px 12px;
  background: white;
  border-radius: 8px;
  margin-bottom: 8px;
  border-left: 4px solid #667eea;
  transition: all 0.2s;
}

.route-segment:hover {
  box-shadow: 0 2px 8px rgba(0, 0, 0, 0.08);
}

.seg-header {
  display: flex;
  align-items: center;
  gap: 12px;
  margin-bottom: 4px;
}

.seg-mode {
  font-weight: 700;
  font-size: 14px;
  color: #667eea;
}

.seg-duration {
  font-size: 13px;
  color: #e67e22;
  font-weight: 600;
}

.seg-distance {
  font-size: 12px;
  color: #999;
}

.seg-stops {
  font-size: 13px;
  color: #555;
  margin: 4px 0;
}

.seg-arrow {
  margin: 0 6px;
  color: #667eea;
}

.seg-transit-detail {
  font-size: 12px;
  color: #1976d2;
  margin-top: 4px;
  padding: 4px 8px;
  background: #e3f2fd;
  border-radius: 4px;
}

/* 响应式设计 */
@media (max-width: 768px) {
  .result-container {
    padding: 20px 10px;
  }

  .page-header {
    flex-direction: column;
    gap: 16px;
  }
}
</style>

