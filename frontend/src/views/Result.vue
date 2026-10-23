<template>
  <div class="result-container travel-redesign">
    <header v-if="tripPlan" class="result-head">
      <div class="result-title">
        <button type="button" class="back-link" @click="goBack">← 返回规划</button>
        <p class="result-eyebrow">为你安排的这一程</p>
        <h1>{{ tripPlan.city }}<span>旅行计划</span></h1>
        <p class="travel-dates">{{ formatDayDate(tripPlan.start_date) }} — {{ formatDayDate(tripPlan.end_date) }}</p>
        <div class="trip-at-a-glance" aria-label="旅行概览"><span><strong>{{ tripPlan.days.length }}</strong> 天旅行</span><span><strong>{{ attractionCount }}</strong> 个景点</span><span><strong>{{ mealCount }}</strong> 餐安排</span></div>
        <p class="travel-cost">{{ costSummary }}</p>
      </div>
      <div class="head-actions">
        <template v-if="editMode || diyMode">
          <span class="editing-badge">编辑中</span>
          <a-button :disabled="saving || !draftUndo.length" title="Ctrl+Z" @click="undoDraft">↶ 撤销一步</a-button>
          <a-button :disabled="saving" @click="cancelEdit">取消</a-button>
          <a-button type="primary" :loading="saving" @click="editMode ? saveChanges() : saveDiyChanges()">保存修改</a-button>
        </template>
        <template v-else>
          <a-button :disabled="busy" @click="toggleEditMode">编辑行程</a-button>
          <button type="button" class="mobile-map-toggle" @click="openMobileMap">当天地图 ↗</button>
          <a-button type="primary" @click="openAgent">问助手</a-button>
          <a-dropdown :trigger="['click']">
            <template #overlay>
              <a-menu @click="onMoreAction">
                <a-menu-item key="recheck" :disabled="busy">重新校验时间</a-menu-item>
                <a-menu-item key="enrich" :disabled="busy || enriching">补充价格与开放时间</a-menu-item>
                <a-menu-item key="weather" :disabled="busy || weatherLoading">天气与雨天备选</a-menu-item>
                <a-menu-item key="prep" :disabled="busy || prepLoading">出行准备清单</a-menu-item>
                <a-menu-divider />
                <a-menu-item key="history">修改记录 · v{{ savedVersion }}</a-menu-item>
                <a-menu-item key="undo" :disabled="busy || versions.length < 2">撤销上次修改</a-menu-item>
                <a-menu-divider />
                <a-menu-item key="handbook">导出出行手册（离线网页）</a-menu-item>
                <a-menu-item key="calendar">导出到日历（.ics）</a-menu-item>
                <a-menu-item key="image">导出为图片</a-menu-item>
                <a-menu-item key="pdf">导出为 PDF</a-menu-item>
              </a-menu>
            </template>
            <a-button aria-label="更多操作">更多 ⋯</a-button>
          </a-dropdown>
        </template>
      </div>
    </header>

    <section v-if="activeRun && ['queued', 'running'].includes(activeRun.status)" class="planning-progress" role="status" aria-live="polite">
      <div><strong>{{ activeRun.status === 'queued' ? '任务正在排队' : activeRun.kind === 'optimize' ? '正在优化当天路线' : '旅行助手正在处理' }}</strong><p>{{ runConnectionIssue || activeRun.stage }} · <span aria-live="off">{{ runElapsed }} 秒</span><span v-if="activeRun.queries"> · 已检查 {{ activeRun.queries }} 条候选路段</span></p><small>{{ activeRun.status === 'queued' ? '等待时间已计入耗时；可以取消任务，或稍后回来查看。' : '可以切换日期或刷新页面，回来后继续查看进度。' }}</small></div>
      <a-button size="small" @click="cancelCurrentRun">取消任务</a-button>
    </section>
    <p v-if="loadError" role="alert" class="persistence-error">{{ loadError }}</p>
    <div v-if="tripPlan && savedId && checksState !== 'ready'" class="checks-feedback" role="status" aria-live="polite">
      <span>{{ checksState === 'error' ? '行程检查暂时不可用，费用与风险提示尚未更新。' : '正在检查当前行程的费用与出行安排…' }}</span>
      <a-button v-if="checksState === 'error'" size="small" :disabled="busy" @click="loadChecks">重试检查</a-button>
    </div>
    <ResultStatus v-if="tripPlan" :items="statusItems" :hints="ruleHints" :summary="routeCoverage" :busy="busy" @goto="gotoDay" @fix="openFixes" @recheck="recheckSchedule" />
    <ConflictFixPanel v-if="tripPlan && fixDay != null" :day-index="fixDay" :original="fixOriginal" :proposals="fixProposals" :loading="fixLoading" :stale="fixStale" :disabled="busy || fixLoading || editMode || diyMode" @apply="applyFix" @close="closeFixes" />
    <section v-if="tripPlan && (historyOpen || lastSummary.length)" class="version-panel">
      <div v-if="lastSummary.length" aria-live="polite" class="last-summary"><strong>本次修改</strong><p v-for="line in lastSummary" :key="line">{{ line }}</p>
        <div class="summary-actions"><button type="button" :disabled="busy || versions.length < 2" @click="undoLast">撤销这次修改</button><button type="button" @click="lastSummary = []">知道了</button></div></div>
      <div v-if="historyOpen"><h2>修改记录 <button type="button" class="panel-close" aria-label="关闭修改记录" @click="historyOpen = false">×</button></h2><article v-for="item in versions" :key="item.version"><div><strong>版本 {{ item.version }} · {{ originLabel(item.origin) }}</strong><small>{{ new Date(item.created_at).toLocaleString() }}</small><p>{{ item.summary.join('；') }}</p></div><button type="button" :disabled="saving || chatLoading || editMode || diyMode || item.version === savedVersion" @click="restoreVersion(item.version)">{{ item.version === savedVersion ? '当前版本' : '恢复此版本' }}</button></article></div>
    </section>
    <TripPrepPanel v-if="tripPlan && prep" :prep="prep" :trip-id="savedId" @close="prep = null" />
    <TicketQueryPanel v-if="ticketContext" :open="true" :trip-id="savedId" :version="savedVersion" :day-date="ticketContext.date" :item-id="ticketContext.id" :name="ticketContext.name" :fixed-time="ticketPlace?.fixed_start_time" :entry-window="ticketPlace?.entry_window" :bookings="ticketBookings" :disabled="busy || editMode || diyMode" @saved="entrySaved" @close="ticketContext = null" />
    <WeatherAdvicePanel v-if="tripPlan && weatherAdvice" :advice="weatherAdvice" :disabled="saving || chatLoading || runBusy" @apply="applyWeatherCandidate" @close="weatherAdvice = null" />
    <div v-if="tripPlan" class="result-layout">
      <button v-if="!agentOpen" type="button" class="agent-fab" @click="openAgent">问助手</button>
      <div class="main-content">
        <section id="daily-itinerary" class="daily-itinerary" aria-label="每日行程">
          <div class="itinerary-toolbar"><div><p>一天，一段新发现。</p><h2>逐日探索。</h2></div><button type="button" :aria-pressed="dayMotion" @click="toggleDayMotion">切换动效 · {{ dayMotion ? '开' : '关' }}</button></div>
          <div class="day-tabs" role="tablist" aria-label="选择旅行日期">
            <button v-for="(day, index) in tripPlan.days" :key="index" :id="`day-tab-${index}`" type="button" role="tab" :aria-selected="activeDays[0] === index" :aria-controls="`day-${index}`" :tabindex="activeDays[0] === index ? 0 : -1" :class="{ selected: activeDays[0] === index, 'drop-target': dropDay === index }" @click="selectDay(index)" @keydown="handleDayTabKey($event, index)" @dragover="onTabDragOver($event, index)" @dragleave="dropDay === index && (dropDay = -1)" @drop="onTabDrop($event, index)"><span class="tab-date"><span class="day-tab-number" aria-hidden="true">{{ String(index + 1).padStart(2, '0') }}</span>第 {{ index + 1 }} 天 · {{ formatDayDate(day.date) }}</span><strong :title="dayTheme(day)">{{ dayTheme(day) }}</strong><small class="tab-state" :class="day.schedule?.status">{{ dayState(day) }}</small></button>
          </div>
          <div ref="dayPanels" class="day-panels" :class="[dayDirection, dayMotion ? 'motion-on' : 'motion-off']">
            <div class="day-progress" aria-hidden="true"><span :style="{ transform: `scaleX(${((activeDays[0] || 0) + 1) / tripPlan.days.length})` }"></span></div>
            <template v-for="(day, index) in tripPlan.days" :key="index">
            <Transition name="day-switch" :css="dayMotion" :duration="{ enter: dayMotion ? 520 : 0, leave: dayMotion ? 180 : 0 }">
            <article v-show="activeDays.includes(index)" :id="`day-${index}`" class="day-panel" role="tabpanel" :aria-labelledby="`day-tab-${index}`" :aria-hidden="!activeDays.includes(index)" :inert="!activeDays.includes(index)" tabindex="0">
              <header class="day-panel-heading">
                <div>
                  <p class="day-chapter">第 {{ index + 1 }} 天 <span> / </span> {{ formatDayDate(day.date) }}</p>
                  <h3>{{ dayTheme(day) }}</h3>
                  <p class="day-facts">{{ day.attractions.length }} 个景点 · {{ visibleMeals(day).length }} 餐<template v-if="day.transportation"> · {{ day.transportation }}</template><template v-if="dayFare(day)"> · 公交地铁约 ¥{{ dayFare(day) }}</template><template v-if="day.hotel"> · 住 {{ day.hotel.name }}</template><span v-if="dayWeather(day)" class="day-weather" :class="{ wet: dayWeather(day)?.wet }"> · {{ dayWeather(day)?.text }}</span></p>
                </div>
                <div v-if="!editMode && !diyMode" class="day-actions">
                  <a-button size="small" :loading="activeRun?.kind === 'optimize' && runBusy" :disabled="busy" @click="optimizeCurrentDay">优化这一天</a-button>
                  <a-dropdown :trigger="['click']">
                    <template #overlay><a-menu @click="recommendHandler(day.day_index)"><a-menu-item v-for="kind in placeKinds" :key="kind.value">推荐{{ kind.label }}</a-menu-item></a-menu></template>
                    <a-button size="small" :disabled="saving || chatLoading">推荐地点 ▾</a-button>
                  </a-dropdown>
                </div>
              </header>
              <details v-if="day.description || day.route_notes" class="day-notes"><summary>今日安排说明</summary><p>{{ day.description }}</p><p v-if="day.route_notes">{{ day.route_notes }}</p></details>
              <details v-if="dayAdjustments(day).length" class="day-notes"><summary>路线调整说明 · {{ dayAdjustments(day).length }} 项</summary><p v-for="note in dayAdjustments(day)" :key="note">{{ note }}</p></details>
              <p v-if="editMode" class="editor-note">拖动调整顺序，或在下方“地点详情”中新增、替换、移除。保存后重新校验时间，取消可还原。</p>
              <DayArrange v-if="editMode || diyMode" :day="day" :day-index="index" :day-count="tripPlan.days.length" v-model:auto-optimize="autoOptimize"
                @reorder="(from, to) => reorderVisit(index, from, to)" @move="(item, target) => moveVisit(index, item, target)" @toggle-lock="item => toggleVisitLock(index, item)" />
              <DayBudget v-if="!editMode && !diyMode && dayReports[day.date]" :report="dayReports[day.date]" :availability-issue="dayVenueIssue(day)" :busy="busy" @fix="openFixes(index)" @recheck="recheckSchedule" />
              <aside v-if="!editMode && !diyMode && dayPreparation(day).length" class="visit-preparation" aria-label="当天参观准备">
                <div><strong>参观前先确认</strong><span>{{ dayPreparation(day).map(item => item.name).join('、') }} · 预约与入内条件尚需确认</span></div>
                <button type="button" :disabled="prepLoading" @click="openPrep">{{ prepLoading ? '读取中…' : '查看要求与清单 ↗' }}</button>
              </aside>
              <DayTimeline :availability-issue="dayVenueIssue(day)" :day="day" :editing="editMode" :selected-id="selectedStop" :ticket-enabled="!!savedId && !busy && !diyMode" @select="focusStop" @tickets="id => openTickets(day, id)" />
              <div v-if="!day.schedule" class="day-flow" aria-label="当天游览和用餐顺序">
                <div v-for="(stop, stopIndex) in dayFlow(day)" :key="`${stopIndex}-${stop.label}`" class="day-flow-stop" :class="`day-flow-${stop.type}`" :title="stop.label">
                  <span class="day-flow-kicker">{{ stop.kicker }}</span>
                  <span class="day-flow-name">{{ stop.label }}</span>
                </div>
              </div>

              <details class="place-details" :open="editMode || diyMode">
                <summary><span>{{ editMode || diyMode ? '编辑当天地点' : '地点详情' }}</span><small>地址、图片与费用</small></summary>
              <!-- 景点安排 -->
              <a-divider orientation="left"> 景点安排</a-divider>
              <a-list
                :data-source="day.attractions"
                :grid="{ gutter: 16, xs: 1, sm: 1, md: 2, lg: 2, xl: 2, xxl: 2 }"
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
                            v-if="editMode || diyMode"
                            size="small"
                            type="primary"
                            @click="openAlternatives('attraction', day.day_index, index, item)"
                          >
                             换地点
                          </a-button>
                          <a-button
                            size="small"
                            danger
                            @click="deleteAttraction(day.day_index, index)"
                          >
                            移除景点
                          </a-button>
                        </a-space>
                      </template>

                      <!-- 景点图片 -->
                      <div class="attraction-image-wrapper">
                        <img
                          :src="getAttractionImage(item.name, index)"
                          :alt="item.name"
                          class="attraction-image"
                          loading="lazy"
                          decoding="async"
                          @error="handleImageError"
                        />
                        <div class="attraction-badge">
                          <span class="badge-number">{{ index + 1 }}</span>
                        </div>
                        <span v-if="attractionPhotos[item.name]" class="image-disclaimer">配图示意</span>
                        <div class="price-tag">
                          {{ costLabel(item, "ticket_price", day.date) }}
                        </div>
                      </div>

                      <!-- 编辑模式下可编辑的字段 -->
                      <div v-if="editMode">
                        <a-checkbox v-model:checked="item.locked">优化时保留此安排</a-checkbox>
                        <p><strong>地址:</strong></p>
                        <a-input v-model:value="item.address" size="small" style="margin-bottom: 8px" />

                        <p><strong>游览时长(分钟):</strong></p>
                        <a-input-number v-model:value="item.visit_duration" :min="10" :max="480" size="small" style="width: 100%; margin-bottom: 8px" />

                        <p v-if="item.entry_window">入场时段 {{ item.entry_window.start }}—{{ item.entry_window.end }}（可退出编辑后在查票窗口调整）<button type="button" @click="item.entry_window = null">清除时段</button></p>
                        <label>固定预约时间（可留空）<input type="time" :value="item.fixed_start_time || ''" @input="item.fixed_start_time = ($event.target as HTMLInputElement).value || null; item.entry_window = null" /></label>
                        <CostInput :place="item" field="ticket_price" label="门票" />
                        <p><strong>游览建议 / 备注:</strong></p>
                        <a-textarea v-model:value="item.description" :rows="2" size="small" style="margin-bottom: 8px" />
                      </div>

                      <!-- 查看模式 -->
                      <div v-else>
                        <p><strong>地址:</strong> {{ item.address }}</p>
                        <p><strong>游览时长:</strong> {{ item.visit_duration }}分钟</p>
                        <p><strong>游览建议 / 备注:</strong> {{ item.description }}</p>
                        <p v-if="adviceHasClaims(item.item_id, day.day_index)" class="advice-reference-note">备注中的时间、价格、交通或预约信息未凭文字作事实核验，请以时间线、费用来源及官方须知为准。</p>
                        <p v-if="item.rating"><strong>评分:</strong> {{ item.rating }}⭐</p>
                        <p v-if="item.open_hours" class="open-hours"><strong>开放时间（参考）:</strong> {{ item.open_hours }} <small>（未作官方核实，来源与记录时间见数据来源说明）</small></p>
                      </div>
                    </a-card>
                  </a-list-item>
                </template>
              </a-list>

              <!-- DIY: 添加景点 -->
              <div v-if="editMode || diyMode" style="margin-bottom: 16px;">
                <a-button type="dashed" size="small" @click="openAddModal('attraction', day.day_index)">
                  + 添加景点
                </a-button>
              </div>

              <!-- 酒店推荐 -->
              <a-divider v-if="day.hotel" orientation="left"> 住宿推荐</a-divider>
              <a-card v-if="day.hotel" size="small" class="hotel-card">
                <a-checkbox v-if="editMode" v-model:checked="day.hotel.locked">保留此酒店</a-checkbox>
                <CostInput v-if="editMode && !applicableHotelQuote(day.hotel,day.date)" :place="day.hotel" field="estimated_cost" label="每间每晚住宿费" />
                <p v-else-if="editMode">本晚已填写日期房型报价，可在“费用”页修改或清除。</p>
                <template #extra v-if="editMode || diyMode"><a-space><a-button size="small" @click="openAlternatives('hotel', day.day_index, 0, day.hotel)">换酒店</a-button><a-button size="small" danger @click="removeHotel(day.day_index)">移除</a-button></a-space></template>
                <template #title>
                  <span class="hotel-title">{{ day.hotel.name }}</span>
                </template>
                <a-descriptions :column="1" size="small">
                  <a-descriptions-item v-if="day.hotel.address" label="地址">{{ day.hotel.address }}</a-descriptions-item>
                  <a-descriptions-item v-if="day.hotel.type" label="类型">{{ day.hotel.type }}</a-descriptions-item>
                  <a-descriptions-item label="住宿费用（每间/晚）">{{ costLabel(day.hotel, "estimated_cost", day.date) }}</a-descriptions-item>
                  <a-descriptions-item v-if="day.hotel.rating" label="评分">{{ day.hotel.rating }}⭐</a-descriptions-item>
                  <a-descriptions-item v-if="day.hotel.distance" label="距离">{{ day.hotel.distance }}</a-descriptions-item>
                </a-descriptions>
              </a-card>

              <!-- 餐饮安排 -->
              <a-button v-if="!day.hotel && (editMode || diyMode)" type="dashed" @click="openAddModal('hotel', day.day_index)">+ 添加酒店</a-button>
              <a-divider orientation="left"> 餐饮安排</a-divider>
              <a-list
                v-if="visibleMeals(day).length"
                :data-source="visibleMeals(day)"
                :grid="{ gutter: 16, xs: 1, sm: 2, md: 2, lg: 2, xl: 3, xxl: 3 }"
              >
                <template #renderItem="{ item }">
                  <a-list-item>
                    <a-card size="small" class="meal-card">
                      <div class="meal-type-badge">{{ getMealLabel(item.type) }}</div>
                      <div v-if="editMode || diyMode" class="meal-edit-actions"><a-button size="small" @click="openAlternatives('meal', day.day_index, day.meals.indexOf(item), item)">换餐厅</a-button><a-button size="small" danger @click="removeMeal(day.day_index, day.meals.indexOf(item))">移除餐厅</a-button></div>
                      <!-- 美食图片 -->
                      <div class="meal-image-wrapper">
                        <img
                          :src="getMealImage(item, day.day_index)"
                          :alt="item.name"
                          class="meal-image"
                          loading="lazy"
                          decoding="async"
                          @error="(e: Event) => handleImageError(e)"
                        />
                        <span v-if="mealPhotos[`${item.restaurant || item.name}_${day.day_index}`]" class="image-disclaimer">配图示意</span>
                      </div>
                      <div class="meal-info">
                        <div class="meal-name">{{ item.restaurant || item.name }}</div>
                        <div v-if="item.recommended_dish" class="meal-dish">
                           {{ item.recommended_dish }}
                        </div>
                        <div v-if="item.address" class="meal-address">
                           {{ item.address }}
                        </div>
                        <div v-if="item.description" class="meal-desc">
                          {{ item.description }}
                        </div>
                        <div v-if="item.open_hours" class="meal-desc open-hours">营业时间：{{ item.open_hours }}</div>
                        <CostInput v-if="editMode" :place="item" field="estimated_cost" label="单人餐费" />
                        <div class="meal-cost">
                          {{ costLabel(item, "estimated_cost", day.date) }}
                        </div>
                      </div>
                    </a-card>
                  </a-list-item>
                </template>
              </a-list>
              <div v-else class="meal-empty">暂无可核实的餐厅推荐，可在 DIY 模式中补充。</div>

              <!-- DIY: 添加餐厅 -->
              <div v-if="editMode || diyMode" style="margin-bottom: 12px; display: flex; gap: 8px; flex-wrap: wrap;">
                <a-button type="dashed" size="small" @click="openAddModal('meal', day.day_index)">
                  + 添加餐厅
                </a-button>
              </div>

              </details>
              <footer class="day-pager"><button type="button" :disabled="index === 0" @click="selectDay(index - 1)">← 前一天</button><span>第 {{ index + 1 }} 天 / 共 {{ tripPlan.days.length }} 天</span><button type="button" :disabled="index === tripPlan.days.length - 1" @click="selectDay(index + 1)">后一天 →</button></footer>
            </article>
            </Transition>
            </template>
          </div>
        </section>

        <section v-reveal class="trip-extras" aria-label="行程补充信息">
          <div class="extras-tabs" role="tablist" aria-label="补充信息">
            <button v-for="tab in extraTabs" :key="tab.key" type="button" role="tab" :aria-selected="extrasTab === tab.key" :class="{ selected: extrasTab === tab.key }" @click="extrasTab = tab.key">{{ tab.label }}</button>
          </div>
          <div v-show="extrasTab === 'overview'" id="overview" class="extras-panel">
            <p class="suggestion-copy">{{ currentAssessment }}</p>
            <div v-if="tripPlan.adjustment_notes?.length" class="suggestion-copy"><strong>本次规划的调整</strong><p v-for="note in tripPlan.adjustment_notes" :key="note">{{ note }}</p></div>
            <button v-if="tripPlan.overall_suggestions" class="suggestion-toggle" type="button" :aria-expanded="suggestionsExpanded" @click="suggestionsExpanded = !suggestionsExpanded">{{ suggestionsExpanded ? '收起原生成建议' : '查看原生成建议' }}</button>
            <template v-if="suggestionsExpanded"><p class="suggestion-copy">原生成建议可能未反映后续修改，当前状态请以上方校验为准。</p><p class="suggestion-copy">{{ tripPlan.overall_suggestions }}</p></template>
          </div>
          <div v-show="extrasTab === 'budget'" id="budget" class="extras-panel">
            <TripCostPanel :trip-id="savedId" :version="savedVersion" :plan="tripPlan" :request="savedRequest" :report="costReport" :disabled="busy || editMode || diyMode" :enriching="enriching" @report="applyCostPreview" @enrich="enrichPrices" @saved="hotelQuotesSaved" />
          </div>
          <div v-if="savedRequest?.reference_sources?.length" v-show="extrasTab === 'references'" class="extras-panel reference-usage">
            <details v-for="source in savedRequest.reference_sources" :key="source.id"><summary>{{ source.title }} · 查看原文</summary><p class="source-original">{{ source.text }}</p></details>
            <div class="usage-list"><article v-for="candidate in savedRequest.reference_candidates" :key="candidate.id"><strong>{{ candidate.place?.name || candidate.name }}</strong><span>{{ referenceUsage(candidate) }}</span><small>来源：{{ candidate.source_ids.map(id => savedRequest?.reference_sources?.find(s => s.id === id)?.title || '攻略').join('、') }}</small></article></div>
          </div>
          <div v-show="extrasTab === 'about'" class="extras-panel data-note">
            <ul>
              <li>地点名称、地址和坐标来自高德地图查询；描述和停留时长是建议值。</li>
              <li>时间线左侧是建议开始时间，已计入交通、停留、缓冲和用餐或预约前的等待。</li>
              <li>交通耗时是查询时的结果，不预测出行当天的路况和班次；地图连线只表示顺序，不是导航路线。</li>
              <li>价格标注了来源：用户填写、高德人均或最低价参考、模型估算，后两者均未核实。公交地铁票价与打车价来自高德查询方案，实际以乘车时为准。</li>
              <li>开放时间可能来自地图登记或用户填写，预约名额、实时票价和节假日规则请以官方渠道为准。</li>
              <li>系统推荐地点来自地图检索，行程修改会保存为可撤销的版本。</li>
            </ul>
            <PlaceFactsPanel :report="factsReport" :disabled="busy || editMode || diyMode" :loading="enriching" @refresh="enrichPrices" />
          </div>
        </section>
      </div>
      <aside class="result-aside" :class="{ 'assistant-open': agentOpen }" aria-label="地图与旅行助手">
        <section id="map" class="aside-map" :class="{ 'map-open': mobileMapOpen }" aria-label="行程地图">
          <header class="map-heading"><div><small>当天路线</small><strong>第 {{ (activeDays[0] || 0) + 1 }} 天 · {{ dayTheme(tripPlan.days[activeDays[0] || 0]) }}</strong></div><button type="button" class="map-overview" @click="selectedStop = ''; redrawDayMap()">全日路线</button><button class="map-close" type="button" aria-label="收起地图" @click="mobileMapOpen = false">×</button></header>
          <div id="amap-container" v-show="!mapError" style="width: 100%; height: 100%"></div>
          <div v-if="mapError" class="map-fallback">{{ mapError }}</div>
          <footer class="map-caption">深色标记为景点 · 棕色为餐饮 · 灰色为住宿<br />实线为已查询轨迹，淡色直线仅表示顺序；入/出标记为门址参考，场内游览未绘制为交通。</footer>
        </section>
        <AgentPanel v-show="agentOpen" ref="agentPanel" v-model="chatInput" :messages="chatMessages" :loading="chatLoading" :disabled="chatLoading || saving || editMode || diyMode" :prompts="agentPrompts" :format-actions="formatAgentActions" :open="agentOpen" @send="sendAgentMessage" @close="agentOpen = false" />
      </aside>
    </div>

    <a-empty v-else class="empty-state">
      <template #image>
        <div class="empty-icon"></div>
      </template>
      <template #description>
        <span>还没有行程，先从一个目的地开始吧。</span>
      </template>
      <a-button type="primary" @click="goBack">开始规划</a-button>
    </a-empty>

    <!-- DIY添加景点/餐厅弹窗 -->
    <a-modal v-model:open="addModalVisible" :title="`添加${placeLabel(addType)}`" width="600px" :footer="null">
      <a-input-search
        v-model:value="addSearchKeyword"
        :placeholder="`搜索${placeLabel(addType)}名称...`"
        enter-button="搜索"
        size="large"
        :loading="addSearchLoading"
        @search="handleAddSearch"
      />
      <a-button class="ask-recommend" :disabled="addSearchLoading" @click="openDayRecommendations(addType, addTargetDay)">没有想好的地点？让助手推荐</a-button>
      <label v-if="addType === 'meal'" class="meal-slot-label">餐次 <a-select v-model:value="addMealSlot" :options="mealSlotOptions" /></label>
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
        未找到结果，可以换个关键词或询问助手
      </div>
      <a-divider>或手动输入（地址与路线尚未核实）</a-divider>
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
      :title="`${altTargetIdx < 0 ? '添加' : '替换'}${placeLabel(altType)} · 第${altTargetDay + 1}天`"
      width="700px"
      :footer="null"
    >
      <div class="recommend-controls"><label for="recommend-preference">想要什么样的{{ placeLabel(altType) }}？</label><a-input id="recommend-preference" v-model:value="altPreference" :maxlength="500" placeholder="例如：人少一些、适合亲子；酒店靠近地铁" :disabled="altLoading" /><div><a-button :disabled="altLoading || !altPreference.trim()" @click="searchAlternatives">搜索名称</a-button><a-button type="primary" :loading="altLoading && altTask === 'agent'" :disabled="altLoading" @click="askPlaceRecommendations">{{ altFromAgent ? '再问助手，换一批' : '询问助手推荐' }}</a-button></div><p>地点来自高德；助手仅提供选择建议，价格与营业信息仍需核实。选择后再保存，不会自动替换。</p><label v-if="altType === 'meal' && altTargetIdx < 0" class="meal-slot-label">餐次 <a-select v-model:value="addMealSlot" :options="mealSlotOptions" /></label></div>
      <a-spin :spinning="altLoading" :tip="altTask === 'agent' ? '助手正在筛选地图候选...' : '正在查询地点...'">
        <div v-if="alternatives.length > 0" class="alt-list">
          <a-card
            v-for="(alt, idx) in alternatives"
            :key="idx"
            size="small"
            hoverable
            class="alt-card"

          >
            <a-row align="middle" :gutter="16">
              <a-col :flex="'auto'">
                <div class="alt-name">
                  {{ altType === 'attraction' ? '' : '' }} {{ alt.name }}
                  <a-tag v-if="alt.category" color="blue" style="margin-left: 8px;">{{ alt.category }}</a-tag>
                </div>
                <div class="alt-address"> {{ alt.address }}</div>
                <div v-if="altType === 'attraction' && alt.visit_duration" class="alt-meta">
                  ⏱ {{ alt.visit_duration }}分钟 |  {{ alt.estimated_cost == null || alt.estimated_cost === 0 ? "费用待核实" : `估算 ¥${alt.estimated_cost}` }}
                </div>
                <div v-if="altType === 'meal' && alt.recommended_dish" class="alt-meta">
                   {{ alt.recommended_dish }} |  {{ alt.estimated_cost == null || alt.estimated_cost === 0 ? "费用待核实" : `估算 ¥${alt.estimated_cost}/人` }}
                </div>
                <div class="alt-reason"> {{ alt.reason }}</div>
              </a-col>
              <a-col :flex="'80px'" style="text-align: right;">
                <a-button type="primary" size="small" :disabled="altLoading" @click="selectAlternative(idx)">选择</a-button>
              </a-col>
            </a-row>
          </a-card>
        </div>
        <a-empty v-else-if="!altLoading" description="可搜索指定地点，或输入偏好询问助手" />
      </a-spin>
    </a-modal>
  </div>
</template>

<script setup lang="ts">
import { dayGatePoints, endpointCoordinates } from '@/utils/routingPoints'
import { readApiJson } from '@/utils/httpResponse'
import { forecastDisplay } from '@/utils/forecast'
import type { PlanningRun, WeatherAdvice, WeatherCandidate, PlanIssue, TripPrep, StatusItem, FixProposal } from '@/types'
import { ref, computed, watch, onMounted, onUnmounted, nextTick } from 'vue'
import { useRouter, useRoute } from 'vue-router'
import message from 'ant-design-vue/es/message'
import AMapLoader from '@amap/amap-jsapi-loader'
import type { TripPlan, Alternative, ChatTurn, TripFormData, SavedTrip, TripVersion, ReferenceCandidate } from '@/types'
import { createTrip, readTrip, commitTrip, tripVersions, restoreTrip, selectSavedTrip, validateTrip, startRun, readRun, listRuns, cancelRun, watchRun, weatherCheck, planChecks, tripPrep, enrichTrip, dayFixes } from '@/services/trips'
import { runMissing, runWatchRejected } from '@/utils/serviceError'
import DayTimeline from '@/components/DayTimeline.vue'
import CostInput from '@/components/CostInput.vue'
import TripCostPanel from '@/components/TripCostPanel.vue'
import PlaceFactsPanel from '@/components/PlaceFactsPanel.vue'
import type { CostReport, FactReport } from '@/types'
import DayArrange, { VISIT_MIME } from '@/components/DayArrange.vue'
import WeatherAdvicePanel from '@/components/WeatherAdvicePanel.vue'
import TripPrepPanel from '@/components/TripPrepPanel.vue'
import AgentPanel from '@/components/AgentPanel.vue'
import ResultStatus from '@/components/ResultStatus.vue'
import ConflictFixPanel from '@/components/ConflictFixPanel.vue'
import DayBudget from '@/components/DayBudget.vue'
import { useMotionPreference } from '@/utils/motionPreference'
import { officialPriceNeedsUpdate } from '@/utils/factDescription'
import { applicableHotelQuote } from '@/utils/hotelQuote'
import type { DayDiagnosis } from '@/types'
import { buildHandbookHtml, buildIcs, downloadFile } from '@/utils/handbook'
import { apiUrl } from '@/services/api'
import { lazyFeature } from '@/utils/lazyFeature'

const TicketQueryPanel = lazyFeature(() => import('@/components/TicketQueryPanel.vue'), '门票查询')
const ticketContext = ref<{ date: string; id: string; name: string } | null>(null)
const ticketPlace = computed(() => tripPlan.value?.days.find(day => day.date === ticketContext.value?.date)?.attractions.find(place => place.item_id === ticketContext.value?.id))
const ticketBookings = computed(() => (tripPlan.value?.ticket_bookings || []).filter(b => b.day_date === ticketContext.value?.date && b.item_id === ticketContext.value?.id))
async function entrySaved(trip: SavedTrip) {
  applySaved(trip); ticketContext.value = null; lastSummary.value = trip.change_summary || []
  message.success('已保存记录与行程更新，可在历史版本中恢复')
  await loadVersions(); await refreshMaps()
}
function openTickets(day: TripPlan['days'][number], id: string) {
  const place = day.attractions.find(place => place.item_id === id)
  if (!place || !savedId.value || busy.value) return
  ticketContext.value = { date: day.date, id, name: place.name }
}

const router = useRouter()
const route = useRoute()
const savedId = ref('')
const savedVersion = ref(1)
const savedRequest = ref<TripFormData | undefined>()
const versions = ref<TripVersion[]>([])
const historyOpen = ref(false)
const saving = ref(false)
const loadError = ref('')
const lastSummary = ref<string[]>([])

const tripPlan = ref<TripPlan | null>(null)
const visibleMeals = (day: TripPlan['days'][number]) => day.meals.filter(meal => (meal.restaurant || meal.name || '').trim())
const dayFlow = (day: TripPlan['days'][number]) => {
  const stops: { type: string; kicker: string; label: string }[] = []
  const meal = (type: string) => visibleMeals(day).find(item => item.type === type)
  const addMeal = (type: string, kicker: string) => {
    const item = meal(type)
    if (item) stops.push({ type: 'meal', kicker, label: item.restaurant || item.name })
  }
  addMeal('breakfast', '早餐')
  const lunchAfter = Math.ceil(day.attractions.length / 2)
  day.attractions.forEach((item, index) => {
    stops.push({ type: 'attraction', kicker: `景点 ${index + 1}`, label: item.name })
    if (index + 1 === lunchAfter) addMeal('lunch', '午餐')
  })
  addMeal('dinner', '晚餐')
  return stops
}
const routeCoverage = computed(() => {
  const segments = tripPlan.value?.days.flatMap(day => day.schedule?.segments || []) || []
  return `交通耗时已查询 ${segments.filter(s => s.status === 'verified').length} / ${segments.length} 段`
})
const currentAssessment = computed(() => {
  if (savedId.value && checksState.value !== 'ready') return checksState.value === 'error'
    ? '当前版本的行程检查未完成，请重试检查后再确认时间安排与费用。'
    : '正在检查当前版本的行程，时间安排与费用结论稍后更新。'
  const days = tripPlan.value?.days || []
  const conflicts = days.filter(day => day.schedule?.conflicts.length).map(day => day.day_index + 1)
  const incomplete = days.filter(day => !day.schedule || day.schedule.status === 'unverified' || day.schedule.segments.some(s => s.status !== 'verified' || s.duration_minutes == null)).length
  if (conflicts.length) return `第 ${conflicts.join('、')} 天仍有时间冲突，建议先查看调整方案。${incomplete ? `另有 ${incomplete} 天尚未完成交通校验。` : ''}地点和交通已查询不代表当天一定走得完，开放时间和预约名额仍需确认。`
  if (incomplete || !days.length) return '当前行程尚未完成时间与交通校验，暂不能判断是否走得完。可以先补齐数据，再调整节奏。'
  const limited = ruleIssues.value.filter(issue => issue.code === 'daily_attraction_limit')
  if (limited.length) return `${limited.map(issue => issue.message).join(' ')}交通与时间安排的校验不能代替对旅行要求的核对。`
  const venueConflicts = ruleIssues.value.filter(issue => venueTimingIssues.has(issue.code))
  if (venueConflicts.length) return `${venueConflicts.map(issue => issue.message).join('；')}。请先调整参观安排，再确认当天是否可行。`
  return '按已查询交通与建议停留时长，各天时间安排通过校验。出行前仍需确认开放时间、预约和当天交通，费用请以实际预订为准。'
})
/** Only what needs action, each with a way to act on it. */
const statusItems = computed<StatusItem[]>(() => {
  if (!tripPlan.value) return []
  const days = tripPlan.value.days
  const items: StatusItem[] = []
  const unchecked = days.filter(day => !day.schedule).length
  const missing = days.flatMap(day => day.schedule?.segments || []).filter(s => s.status !== 'verified').length
  if (unchecked) items.push({ text: `${unchecked} 天尚未校验时间`, action: 'recheck' })
  if (missing) items.push({ text: `${missing} 段交通耗时缺失，相关到达时间无法计算`, action: 'recheck' })
  const stale = Object.values(dayReports.value).reduce((sum, day) => sum + (day.stale_routes || 0), 0)
  const undated = Object.values(dayReports.value).reduce((sum, day) => sum + (day.unknown_age_routes || 0), 0)
  if (stale || undated) items.push({ text: stale ? `${stale} 段交通查询已超过 24 小时，建议出行前更新` : `${undated} 段交通缺少查询时间，建议更新`, action: 'recheck' })
  for (const day of days) {
    if (day.schedule?.conflicts.length) items.push({ text: `第${day.day_index + 1}天：${day.schedule.conflicts[0]}`, day: day.day_index, action: 'fix' })
  }
  for (const issue of ruleIssues.value) {
    if (issue.severity === 'info') continue
    items.push({ text: issue.message, day: issue.day_index ?? undefined, action: venueTimingIssues.has(issue.code) && issue.day_index != null ? 'fix' : issue.code === 'schedule_integrity' ? 'recheck' : issue.day_index != null ? 'day' : 'none' })
  }
  return items
})
const busy = computed(() => saving.value || chatLoading.value || runBusy.value)
const costSummary = computed(() => !costReport.value || (!costReport.value.known_total && costReport.value.unknown_count) ? '费用待补充' : `已列参考小计 ¥${costReport.value.known_total.toLocaleString('zh-CN')}${costReport.value.assumptions.length ? '（含人数或房间数假设）' : ''}`)
const agentOpen = ref(false)
const agentPanel = ref<InstanceType<typeof AgentPanel> | null>(null)
/** Desktop: focus the docked assistant. Narrow screens: open it as a bottom drawer. */
async function openAgent() {
  agentOpen.value = true
  await nextTick()
  document.getElementById('agent-input')?.focus({ preventScroll: true })
}
async function gotoDay(index: number) {
  await selectDay(index)
  document.getElementById('daily-itinerary')?.scrollIntoView({ behavior: 'smooth', block: 'start' })
}
function onMoreAction({ key }: { key: string | number }) {
  const actions: Record<string, () => unknown> = {
    recheck: recheckSchedule, enrich: enrichPrices, weather: checkWeather, prep: openPrep,
    history: () => { historyOpen.value = !historyOpen.value }, undo: undoLast,
    image: exportAsImage, pdf: exportAsPDF, handbook: exportHandbook, calendar: exportCalendar,
  }
  void actions[String(key)]?.()
}
/** Weather for a day from the latest check, falling back to forecast stored with the plan. */
function dayWeather(day: TripPlan['days'][number]) {
  const advised = weatherAdvice.value?.days.find(item => item.date === day.date)
  const fresh=forecastDisplay(advised?.weather)
  if (fresh) return fresh
  const stored = tripPlan.value?.weather_info?.find(item => item.date === day.date && item.day_weather)
  return stored ? forecastDisplay({...stored,day:stored.day_weather,night:stored.night_weather,high:stored.day_temp,low:stored.night_temp}) : null
}
/** Sum of queried transit fares for a day (adult single tickets per Amap plan). */
const dayFare = (day: TripPlan['days'][number]) => Math.round((day.schedule?.segments || []).reduce((total, segment) => total + (segment.fare ?? 0), 0) * 10) / 10
// ============ 冲突调整方案 ============
const fixDay = ref<number | null>(null)
const fixProposals = ref<FixProposal[]>([])
const fixLoading = ref(false)
const fixOriginal = ref<TripPlan['days'][number] | null>(null)
const fixBaseVersion = ref<number | null>(null)
const fixTripId = ref('')
const fixStale = computed(() => fixBaseVersion.value != null && (savedVersion.value !== fixBaseVersion.value || savedId.value !== fixTripId.value))
let fixRequest = 0
let fixReturnFocus: HTMLElement | null = null
function closeFixes(restoreFocus = true) {
  fixRequest++
  fixDay.value = null; fixLoading.value = false
  fixProposals.value = []; fixOriginal.value = null; fixBaseVersion.value = null
  if (restoreFocus && fixReturnFocus?.isConnected) fixReturnFocus.focus({ preventScroll: true })
}
async function openFixes(dayIndex: number) {
  const day = tripPlan.value?.days[dayIndex]
  if (!day || busy.value || editMode.value || diyMode.value) return
  closeFixes(false)
  fixReturnFocus = document.activeElement instanceof HTMLElement ? document.activeElement : null
  const request = ++fixRequest
  fixDay.value = dayIndex; fixLoading.value = true
  await nextTick()
  document.getElementById('fix-comparison')?.focus()
  try {
    await ensureSaved()
    if (request !== fixRequest) return
    const current = tripPlan.value?.days[dayIndex]
    if (!current || current.date !== day.date) { closeFixes(); return }
    fixOriginal.value = JSON.parse(JSON.stringify(current))
    fixBaseVersion.value = savedVersion.value; fixTripId.value = savedId.value
    const result = await dayFixes(fixTripId.value, fixBaseVersion.value!, current.date)
    if (request === fixRequest) fixProposals.value = result.proposals
  } catch (e: any) { if (request === fixRequest) { message.error(e.message); closeFixes() } }
  finally { if (request === fixRequest) fixLoading.value = false }
}
/** Save the chosen proposal as one undoable version (the server re-validates the changed day). */
async function applyFix(proposal: FixProposal) {
  if (!tripPlan.value || fixDay.value == null || busy.value || fixLoading.value || editMode.value || diyMode.value) return
  if (fixStale.value || fixBaseVersion.value == null) { message.warning('行程已更新，请重新查看调整方案'); return }
  if (!fixProposals.value.includes(proposal) || proposal.day.date !== fixOriginal.value?.date) return
  const dayIndex = tripPlan.value.days.findIndex(day => day.date === fixOriginal.value?.date)
  if (dayIndex < 0) return
  const plan = JSON.parse(JSON.stringify(tripPlan.value)) as TripPlan
  plan.days[dayIndex] = proposal.day
  saving.value = true
  try {
    const saved = await commitTrip(fixTripId.value, fixBaseVersion.value, plan, 'fix')
    applySaved(saved); lastSummary.value = saved.change_summary || []
    closeFixes()
    await loadVersions(); await refreshMaps()
    const status = saved.plan.days[dayIndex]?.schedule?.status
    message.success(status === 'feasible' ? '已调整，当天时间可以安排' : status === 'unverified' ? '已保存调整，部分交通仍待核实' : '已保存调整，请留意剩余冲突')
  } catch (e: any) { message.error(e.message) }
  finally { saving.value = false }
}

// ============ 出行手册与日历 ============
const exportName = () => `${tripPlan.value?.city || '旅行'}-${tripPlan.value?.start_date || ''}`
function exportHandbook() {
  if (!tripPlan.value) return
  downloadFile(`${exportName()}-出行手册.html`, buildHandbookHtml(tripPlan.value), 'text/html;charset=utf-8')
  message.success('出行手册已下载，可离线打开；“导航”链接会打开高德地图')
}
function exportCalendar() {
  if (!tripPlan.value) return
  const { ics, events } = buildIcs(tripPlan.value)
  if (!events) { message.warning('还没有校验过的时间表，请先“重新校验时间”'); return }
  downloadFile(`${exportName()}.ics`, ics, 'text/calendar;charset=utf-8')
  message.success(`已导出 ${events} 个日程，可导入手机或电脑日历`)
}

const recommendHandler = (dayIdx: number) => ({ key }: { key: string | number }) => openDayRecommendations(key as PlaceKind, dayIdx)
const extrasTab = ref('overview')
const extraTabs = computed(() => [
  { key: 'overview', label: '整体建议' }, { key: 'budget', label: '费用' },
  ...(savedRequest.value?.reference_sources?.length ? [{ key: 'references', label: `攻略来源 · ${savedRequest.value.reference_sources.length}` }] : []),
  { key: 'about', label: '数据来源说明' },
])
// Server-side rule checks for the saved version (duplicates, meals, lodging, outliers, budget).
const ruleIssues = ref<PlanIssue[]>([])
const checksState = ref<'idle' | 'loading' | 'ready' | 'error'>('idle')
let checksGeneration = 0
const adviceHasClaims = (id: string | undefined, day: number) => ruleIssues.value.some(issue => issue.code === 'visit_advice_claim' && issue.item_id === id && issue.day_index === day)
const costReport = ref<CostReport | null>(null)
const factsReport = ref<FactReport | null>(null)
let costGeneration = 0
function applyCostPreview(report: CostReport | null) { costGeneration++; costReport.value = report }
async function hotelQuotesSaved(saved: SavedTrip) { applySaved(saved); await loadVersions(); message.success('入住日期报价已保存，可在修改记录中撤销。') }
const dayReports = ref<Record<string, DayDiagnosis>>({})
const visitReminders = ref<TripPrep['reminders']>([])
function dayPreparation(day: TripPlan['days'][number]) { return visitReminders.value.filter(item => item.date === day.date) }
const ruleHints = computed(() => ruleIssues.value.filter(issue => issue.severity === 'info').map(issue => issue.message))
async function loadChecks() {
  const checkGeneration = ++checksGeneration
  checksState.value = savedId.value ? 'loading' : 'idle'
  factsReport.value = null
  const generation = ++costGeneration
  costReport.value = null
  dayReports.value = {}
  visitReminders.value = []
  ruleIssues.value = []
  if (!savedId.value) return
  const id = savedId.value, version = savedVersion.value
  try {
    const result = await planChecks(id)
    if (checkGeneration !== checksGeneration || savedId.value !== id || savedVersion.value !== version) return
    if (result.version !== version) { checksState.value = 'error'; return }
    visitReminders.value = result.visit_reminders || []
    ruleIssues.value = result.issues
    factsReport.value = result.fact_report
    if (generation === costGeneration) costReport.value = result.cost_report
    dayReports.value = Object.fromEntries((result.days || []).map(day => [day.date, day]))
    checksState.value = 'ready'
  } catch {
    if (checkGeneration === checksGeneration && savedId.value === id && savedVersion.value === version) checksState.value = 'error'
  }
}
watch([savedId, savedVersion], () => { void loadChecks(); if (prep.value) { prep.value = null; void openPrep() } })
const enriching = ref(false)
/** Backfill Amap prices/opening hours and model ticket estimates for older trips; saved as one version. */
async function enrichPrices() {
  if (enriching.value) return
  enriching.value = true
  try {
    await ensureSaved()
    const result = await enrichTrip(savedId.value, savedVersion.value)
    applySaved(result)
    lastSummary.value = result.change_summary || []
    await loadVersions()
    const e = result.enrichment
    message.success(`已查询 ${e.looked_up} 个地图地点${e.failed ? `（${e.failed} 个暂时失败）` : ''}，补充 ${e.official_updated || 0} 处官网门票参考、${e.tickets_estimated} 处模型估算${e.official_failed ? `；${e.official_failed} 个官方页面未获取` : ''}`)
  } catch (e: any) { message.error(e.message) }
  finally { enriching.value = false }
}
const prep = ref<TripPrep | null>(null)
const prepLoading = ref(false)
async function openPrep() {
  if (prepLoading.value) return
  prepLoading.value = true
  try {
    await ensureSaved()
    const id = savedId.value, version = savedVersion.value
    const result = await tripPrep(id)
    if (savedId.value !== id || savedVersion.value !== version || result.version !== version) return
    prep.value = result
    await nextTick()
    document.querySelector('.prep-panel')?.scrollIntoView({ behavior: 'smooth', block: 'nearest' })
  } catch (e: any) { message.error(e.message) }
  finally { prepLoading.value = false }
}
const attractionCount = computed(() => tripPlan.value?.days.reduce((total, day) => total + day.attractions.length, 0) || 0)
const mealCount = computed(() => tripPlan.value?.days.reduce((total, day) => total + visibleMeals(day).length, 0) || 0)
function costKnown(place: any, field: string) { return place[field] != null && (place.cost_status === 'confirmed' || place.cost_status === 'estimated' || (!place.cost_status && place[field] > 0)) }
function costLabel(place: any, field: string, visitDate?: string) {
  const quote=field==='estimated_cost'?applicableHotelQuote(place,visitDate):null
  if(quote)return `用户提供 ¥${quote.price_per_room} · ${quote.room_type}${quote.taxes_included===true?' · 注明含税':' · 税费待确认'}`
  if (officialPriceNeedsUpdate(place.price_evidence, visitDate)) return '游览日期已变化，价格待更新'
  if (!costKnown(place, field)) return '费用待核实'
  // cost_source names where the number came from: 用户填写 / 高德人均参考 / 高德最低价参考 / 模型估算
  const source = (place.cost_source || (place.cost_status === 'confirmed' ? '用户提供' : '估算')).replace(/，?未(独立)?核实$/, '')
  if (place[field] === 0) return `免费 · ${source}`
  return `${source} ¥${place[field]}${place.cost_status === 'confirmed' ? '' : ' · 未核实'}`
}
async function recheckSchedule() {
  if (saving.value || chatLoading.value || editMode.value || diyMode.value) return
  saving.value = true
  try { await ensureSaved(); applySaved(await validateTrip(savedId.value, savedVersion.value)); await loadVersions(); message.success('时间校验完成，结果已保存') }
  catch (e: any) { message.error(e.message) } finally { saving.value = false }
}
const suggestionsExpanded = ref(false)
const editMode = ref(false)
const diyMode = ref(false)
const originalPlan = ref<TripPlan | null>(null)
type AgentMessage = ChatTurn & { actions?: string[]; changed?: boolean }
const chatMessages = ref<AgentMessage[]>([])
const chatInput = ref('')
const chatLoading = ref(false)
const activeRun = ref<PlanningRun | null>(null)
const runElapsed = ref(0)
let runClock: number | undefined
watch(activeRun, run => {
  window.clearInterval(runClock)
  runClock = undefined
  runElapsed.value = run?.elapsed_seconds || 0
  if (!run || !['queued', 'running'].includes(run.status)) return
  const baseline = runElapsed.value, received = performance.now()
  // Continue an observed duration while SSE sends only heartbeat comments.
  runClock = window.setInterval(() => {
    runElapsed.value = baseline + Math.floor((performance.now()-received)/1000)
  }, 1000)
})
onUnmounted(() => window.clearInterval(runClock))
const runBusy = computed(() => !!activeRun.value && ['queued', 'running'].includes(activeRun.value.status))
const runConnectionIssue = ref('')
let runDisposed = false
const pendingRunKey = () => `pendingRun:${savedId.value}`
async function awaitRun(run: PlanningRun) {
  activeRun.value = run
  if (['queued', 'running'].includes(run.status)) {
    const tripId = savedId.value
    try {
      activeRun.value = await watchRun(run.id, async () => {
        try { const latest = await readRun(tripId, run.id); runConnectionIssue.value = ''; return latest }
        catch (e) { runConnectionIssue.value = '连接暂时中断，正在重新连接…'; throw e }
      }, latest => { activeRun.value = latest }, () => runDisposed)
    } catch (error) {
      if (runWatchRejected(error) && activeRun.value?.id === run.id) {
        activeRun.value = null
        runConnectionIssue.value = ''
        const key = `pendingRun:${tripId}`
        if (runMissing(error) && localStorage.getItem(key) === run.id) localStorage.removeItem(key)
      }
      throw error
    }
  }
  if (runDisposed) throw new Error('页面已关闭，任务继续执行')
  localStorage.removeItem(pendingRunKey())
  if (activeRun.value.status !== 'succeeded' || !activeRun.value.result) throw new Error(activeRun.value.error || (activeRun.value.status === 'cancelled' ? '任务已取消，原行程保留' : '任务未完成，原行程保留'))
  return activeRun.value.result
}
async function beginRun(kind: 'chat' | 'optimize', value: string | string[]) {
  const requestId = crypto.randomUUID()
  localStorage.setItem(pendingRunKey(), requestId)
  const run = await startRun(savedId.value, savedVersion.value, kind, value, requestId)
  localStorage.setItem(pendingRunKey(), run.id)
  return awaitRun(run)
}
async function cancelCurrentRun() {
  if (!activeRun.value) return
  try { activeRun.value = await cancelRun(savedId.value, activeRun.value.id) }
  catch (e: any) { message.error(e.message) }
}
async function optimizeCurrentDay() {
  if (saving.value || chatLoading.value || editMode.value || diyMode.value || !tripPlan.value) return
  const day = tripPlan.value.days[Number(activeDays.value[0] || 0)]
  if (!day) return
  saving.value = true
  try {
    await ensureSaved()
    await optimizeDates([day.date])
  } catch(e: any) { if (!runDisposed) message.error(e.message) }
  finally { saving.value = false }
}
/** Optimize one or more days in a single run; the result is one undoable version. */
async function optimizeDates(dates: string[]) {
  const result = await beginRun('optimize', dates.length === 1 ? dates[0] : dates)
  if (runDisposed) return
  applySaved(await readTrip(savedId.value))
  lastSummary.value = [result.optimization?.note || '路线已检查', ...(result.change_summary || [])]
  await loadVersions(); await refreshMaps()
  if (result.optimization?.status === 'feasible_under_assumptions') message.success(dates.length > 1 ? '变动日期的路线已优化，可撤销' : '当天路线已优化，可撤销')
  else message.info(result.optimization?.note || '保留原安排')
}
async function reconnectRun() {
  if (!savedId.value) return
  try {
    const pending = localStorage.getItem(pendingRunKey())
    const runs = await listRuns(savedId.value)
    const run = runs.find(item => item.id === pending) || runs.find(item => ['queued', 'running'].includes(item.status))
    if (!run) { localStorage.removeItem(pendingRunKey()); return }
    if (run.kind === 'chat') chatLoading.value = true
    else saving.value = true
    const result = await awaitRun(run)
    applySaved(await readTrip(savedId.value))
    lastSummary.value = result.change_summary || []
    await loadVersions(); await refreshMaps()
    message.success('任务已完成，结果已恢复')
  } catch(e: any) { if (!runDisposed) message.warning(e.message) }
  finally { chatLoading.value = false; saving.value = false }
}
const agentPrompts = computed(() => [`第${(activeDays.value[0] || 0) + 1}天轻松一点`, `第${(activeDays.value[0] || 0) + 1}天附近有什么餐厅推荐？`, '这趟行程需要注意什么？'])
const agentActionLabels: Record<string, string> = {
  search_attractions: '景点', search_hotels: '酒店', search_restaurants: '餐厅', check_weather: '天气', read_reference: '攻略'
}
const formatAgentActions = (actions: string[]) => [...new Set(actions.map(action => agentActionLabels[action] || action))].join('、')
const normalizeDayIndices = (plan: TripPlan): TripPlan => ({
  ...plan,
  days: plan.days.map((day, index) => ({ ...day, day_index: index }))
})

const originLabel = (origin: string) => ({ initial: '生成初稿', agent: '对话调整', manual: '手动编辑', diy: 'DIY 调整', route: '路线优化', optimize: '路线优化', chat: '对话调整', restore: '恢复历史', validation: '时间校验', enrich: '补充价格', fix: '冲突调整',hotel_quote:'入住日期报价', appointment: '入场时间', ticket_booking: '门票预订记录' }[origin] || origin)
function applySaved(trip: SavedTrip) {
  savedId.value = trip.id
  savedVersion.value = trip.version
  savedRequest.value = trip.request
  tripPlan.value = normalizeDayIndices(trip.plan)
  chatMessages.value = trip.chat
  selectSavedTrip(trip)
}
async function loadVersions() {
  if (savedId.value) versions.value = await tripVersions(savedId.value)
}
async function ensureSaved() {
  if (savedId.value || !tripPlan.value) return
  const stored = sessionStorage.getItem('tripRequest')
  const request = stored ? JSON.parse(stored) as TripFormData : {
    city: tripPlan.value.city, start_date: tripPlan.value.start_date, end_date: tripPlan.value.end_date,
    travel_days: tripPlan.value.days.length, transportation: tripPlan.value.days[0]?.transportation || '公共交通',
    accommodation: tripPlan.value.days[0]?.accommodation || '酒店', preferences: [], free_text_input: '',
    reference_type: 'none', reference_content: '', reference_mode: 'flexible'
  } as TripFormData
  const saved = await createTrip(tripPlan.value, request, chatMessages.value.slice(-20))
  applySaved(saved)
  await router.replace({ path: '/result', query: { trip: saved.id } })
}
async function persistPlan(origin: string) {
  if (!tripPlan.value) return
  // Legacy migration must save the previous snapshot so edits remain undoable.
  const edited = JSON.parse(JSON.stringify(tripPlan.value)) as TripPlan
  if (!savedId.value && originalPlan.value) tripPlan.value = originalPlan.value
  try { await ensureSaved() } finally { tripPlan.value = edited }
  const saved = await commitTrip(savedId.value, savedVersion.value, edited, origin)
  applySaved(saved)
  lastSummary.value = saved.change_summary || []
  try { await loadVersions() } catch { message.warning('行程已保存，修改记录暂时无法读取') }
  await refreshMaps()
}
async function restoreVersion(target: number) {
  if (!savedId.value || saving.value || chatLoading.value || editMode.value || diyMode.value) return
  saving.value = true
  try {
    const trip = await restoreTrip(savedId.value, savedVersion.value, target)
    applySaved(trip)
    lastSummary.value = [`已恢复版本 ${target}，并保存为版本 ${trip.version}`]
    attractionPhotos.value = {}; mealPhotos.value = {}
    try { await loadVersions() } catch { message.warning('已恢复，修改记录暂时无法读取') }
    await refreshMaps()
    message.success('行程已恢复')
  } catch(e: any) { message.error(e.message) }
  finally { saving.value = false }
}
const undoLast = () => { if (versions.value[1]) void restoreVersion(versions.value[1].version) }
function referenceUsage(candidate: ReferenceCandidate) {
  if (candidate.priority === 'ignored') return '已按你的选择忽略'
  const name = candidate.place?.name || candidate.name
  const included = candidate.place && tripPlan.value?.days.some(day => [...day.attractions, ...day.meals, ...(day.hotel ? [day.hotel] : [])].some(place => {
    if ((('restaurant' in place && place.restaurant) || place.name) !== name) return false
    if ('poi_id' in place && place.poi_id && candidate.place?.poi_id) return place.poi_id === candidate.place.poi_id
    return place.location && candidate.place && Math.abs(place.location.longitude - candidate.place.longitude) < 0.0001 && Math.abs(place.location.latitude - candidate.place.latitude) < 0.0001
  }))
  if (included) return candidate.priority === 'required' ? '必去 · 已安排' : '已安排'
  if (candidate.status !== 'matched') return '地图匹配待确认 · 未安排'
  return candidate.priority === 'required' ? '必去 · 当前未安排，请调整' : '可选 · 本版未采用'
}

const sendAgentMessage = async (preset?: string) => {
  const prompt = (preset ?? chatInput.value).trim()
  if (!prompt || !tripPlan.value || chatLoading.value || saving.value || editMode.value || diyMode.value) return
  chatLoading.value = true
  const beforeMessages = [...chatMessages.value]
  try {
    await ensureSaved()
    chatInput.value = ''
    chatMessages.value.push({ role: 'user', content: prompt })
    await nextTick()
    void agentPanel.value?.scrollToEnd()
    const response = await beginRun('chat', prompt)
    applySaved(await readTrip(savedId.value))
    if (response.changed) {
      lastSummary.value = response.change_summary || []
      attractionPhotos.value = {}; mealPhotos.value = {}
      try { await loadVersions() } catch { message.warning('修改已保存，记录暂时无法读取') }
      await refreshMaps()
      message.success('行程已更新，可撤销上次修改')
    }
    await nextTick()
    void agentPanel.value?.scrollToEnd()
  } catch(e: any) {
    if (runDisposed) return
    chatMessages.value = beforeMessages
    chatInput.value = prompt
    message.error(e.message || '本次调整失败，原行程保留')
  } finally { chatLoading.value = false }
}

// DIY备选项
const altModalVisible = ref(false)
const altLoading = ref(false)
const altTask = ref<'nearby' | 'agent' | 'search'>('nearby')
type PlaceKind = 'attraction' | 'meal' | 'hotel'
const placeKinds: { value: PlaceKind; label: string }[] = [{ value: 'attraction', label: '景点' }, { value: 'meal', label: '餐厅' }, { value: 'hotel', label: '酒店' }]
const placeLabel = (kind: PlaceKind) => placeKinds.find(item => item.value === kind)?.label || '地点'
const altType = ref<PlaceKind>('attraction')
const altPreference = ref('')
const altFromAgent = ref(false)
let altRequestId = 0
const addMealSlot = ref<'breakfast' | 'lunch' | 'dinner' | 'snack'>('snack')
const mealSlotOptions = [{ value: 'breakfast', label: '早餐' }, { value: 'lunch', label: '午餐' }, { value: 'dinner', label: '晚餐' }, { value: 'snack', label: '加餐' }]
const alternatives = ref<Alternative[]>([])
const altTargetDay = ref(-1)
const altTargetIdx = ref(-1)

// DIY添加景点/餐厅
const addModalVisible = ref(false)
const addType = ref<PlaceKind>('attraction')
const addTargetDay = ref(-1)
const addSearchKeyword = ref('')
const addSearchLoading = ref(false)
const addSearchDone = ref(false)
const addResults = ref<any[]>([])
const addManualName = ref('')
const addManualAddress = ref('')

const attractionPhotos = ref<Record<string, string>>({})
const mealPhotos = ref<Record<string, string>>({})
const activeSection = ref('overview')
const activeDays = ref<number[]>([0])
const selectedStop = ref('')
const mobileMapOpen = ref(false)
const markerLookup = new Map<string, any>()
const dayTheme = (day: TripPlan['days'][number] | undefined) => day?.attractions.slice(0, 2).map(place => place.name).join(' · ') || '自由安排'
const dayAdjustments = (day: TripPlan['days'][number]) => (tripPlan.value?.adjustment_notes || []).filter(note => note.startsWith(`第${day.day_index + 1}天`))
const venueTimingIssues = new Set(['closed_day', 'opening_hours_conflict', 'opening_weekday_uncovered', 'park_last_entry', 'park_closing_time', 'inner_venue_closed', 'inner_venue_last_entry'])
const dayVenueIssue = (day: TripPlan['days'][number]) => ruleIssues.value.filter(issue => venueTimingIssues.has(issue.code) && issue.day_index === day.day_index).map(issue => issue.message).join('；')
const dayState = (day: TripPlan['days'][number]) => savedId.value && checksState.value !== 'ready' ? (checksState.value === 'error' ? '行程检查待重试' : '行程检查中') : ruleIssues.value.some(issue => venueTimingIssues.has(issue.code) && issue.day_index === day.day_index) ? '开放时间待调整' : ruleIssues.value.some(issue => issue.code === 'daily_attraction_limit' && issue.day_index === day.day_index) ? '景点数量待调整' : !day.schedule ? '时间待校验' : day.schedule.conflicts.length ? '时间待调整' : day.schedule.status === 'unverified' ? '交通待补齐' : '按已知条件可安排'
async function openMobileMap() {
  mobileMapOpen.value = true
  await nextTick()
  await new Promise<void>(resolve => requestAnimationFrame(() => requestAnimationFrame(() => resolve())))
  redrawDayMap()
}
async function focusStop(identity: string) {
  selectedStop.value = identity
  if (window.matchMedia('(max-width: 1023px)').matches) mobileMapOpen.value = true
  await nextTick()
  await new Promise<void>(resolve => requestAnimationFrame(() => requestAnimationFrame(() => resolve())))
  const marker = markerLookup.get(identity) || markerLookup.get(identity.replace(/-(start|end)$/, ''))
  if (map && marker) map.setFitView([marker], false, [60, 60, 60, 60])
  else message.info(mapError.value || (map ? '该地点暂无可定位的坐标，可在地点详情中核对。' : '地图正在加载，请稍后再试。'))
}
const dayDirection = ref('forward')
const dayPanels = ref<HTMLElement | null>(null)
let dayHeightAnimation: Animation | undefined
let daySwitchRevision = 0
function cancelDayHeightAnimation() {
  dayHeightAnimation?.cancel()
  dayHeightAnimation = undefined
}
onUnmounted(cancelDayHeightAnimation)
// The requested day-card animation has its own visible, remembered control.
const { enabled: dayMotion, toggle: toggleDayMotion } = useMotionPreference()
watch(dayMotion, () => { daySwitchRevision++; cancelDayHeightAnimation() })
const formatDayDate = (value: string) => {
  const date = new Date(`${value}T12:00:00`)
  return Number.isNaN(date.getTime()) ? value : new Intl.DateTimeFormat('zh-CN', { month: 'long', day: 'numeric', weekday: 'short' }).format(date)
}
const selectDay = async (index: number) => {
  if (!tripPlan.value || index < 0 || index >= tripPlan.value.days.length || index === activeDays.value[0]) return
  const revision = ++daySwitchRevision
  const previousHeight = dayPanels.value?.getBoundingClientRect().height
  cancelDayHeightAnimation()
  dayDirection.value = index > activeDays.value[0] ? 'forward' : 'backward'
  selectedStop.value = ''
  activeDays.value = [index]
  activeSection.value = `day-${index}`
  redrawDayMap()
  await nextTick()
  if (revision !== daySwitchRevision) return
  const panel = document.getElementById(`day-${index}`)
  if (dayMotion.value && previousHeight && panel && dayPanels.value?.animate) {
    // The grid overlaps outgoing and incoming days; animate its height separately
    // so a shorter day does not snap upward when the outgoing card disappears.
    dayHeightAnimation = dayPanels.value.animate([
      { height: `${previousHeight}px`, overflow: 'clip' },
      { height: `${panel.offsetHeight}px`, overflow: 'clip' }
    ], { duration: 520, easing: 'cubic-bezier(.22, 1, .36, 1)' })
  }
  document.getElementById(`day-tab-${index}`)?.scrollIntoView({ behavior: dayMotion.value ? 'smooth' : 'instant', block: 'nearest', inline: 'nearest' })
  await handleActiveDaysChange([index])
}
const handleDayTabKey = async (event: KeyboardEvent, index: number) => {
  const count = tripPlan.value?.days.length || 0
  const target = { ArrowRight: (index + 1) % count, ArrowLeft: (index - 1 + count) % count, Home: 0, End: count - 1 }[event.key]
  if (target === undefined || !count) return
  event.preventDefault()
  await selectDay(target)
  document.getElementById(`day-tab-${target}`)?.focus()
}
let map: any = null
let amapApi: any = null
function redrawDayMap() {
  if (!map || !amapApi) return
  map.clearMap()
  addAttractionMarkers(amapApi)
}
const mapError = ref('')

onMounted(async () => {
  const requested = typeof route.query.trip === 'string' ? route.query.trip : ''
  const id = requested || localStorage.getItem('activeTripId') || ''
  if (id) {
    try { applySaved(await readTrip(id)) }
    catch(e: any) { loadError.value = e.message + '。请返回规划页重新打开旅行。'; return }
    // Version history is secondary; load it alongside the map instead of before it.
    void loadVersions().catch(() => { loadError.value = '行程已载入，修改记录暂时无法读取' })
  } else {
    const data = sessionStorage.getItem('tripPlan')
    if (!data) return
    try {
      const parsed = JSON.parse(data) as TripPlan
      if (!Array.isArray(parsed.days)) throw new Error('行程数据不完整')
      tripPlan.value = normalizeDayIndices(parsed)
      const storedRequest = sessionStorage.getItem('tripRequest')
      if (storedRequest) savedRequest.value = JSON.parse(storedRequest)
      chatMessages.value = JSON.parse(sessionStorage.getItem('tripChat') || '[]').slice(-20)
      try { await ensureSaved(); await loadVersions() }
      catch(e: any) { loadError.value = '当前行程尚未长期保存：' + e.message }
    } catch { loadError.value = '行程数据无法读取，请返回规划页'; return }
  }
  await nextTick()
  void initMap()
  if (tripPlan.value?.days.length) void loadPhotosForDay(0)
  void reconnectRun()
  window.addEventListener('keydown', onUndoKey)
})

onUnmounted(() => {
  runDisposed = true
  window.removeEventListener('keydown', onUndoKey)
  map?.destroy()
})

const handleActiveDaysChange = async (keys: string | number | Array<string | number>) => {
  const opened = (Array.isArray(keys) ? keys : [keys]).map(Number).filter(Number.isInteger)
  await nextTick()
  opened.forEach(index => { void loadPhotosForDay(index) })
}

const goBack = () => {
  router.push('/')
}

const refreshMaps = async () => {
  mapError.value = ''
  await nextTick()
  void initMap()
}

// 滚动到指定区域
// ============ 草稿撤销与拖拽排列 ============
const draftUndo = ref<string[]>([])
let lastDraft: string | null = null
const autoOptimize = ref(readAutoOptimize())
function readAutoOptimize() { try { return localStorage.getItem('autoOptimizeAfterEdit') !== 'off' } catch { return true } }
watch(autoOptimize, value => { try { localStorage.setItem('autoOptimizeAfterEdit', value ? 'on' : 'off') } catch { /* preference only */ } })
/** Snapshot after each structural draft change; undo restores the previous snapshot. */
function recordDraft() {
  if (!tripPlan.value) return
  const previous = lastDraft ?? (originalPlan.value ? JSON.stringify(originalPlan.value) : null)
  if (previous) draftUndo.value = [...draftUndo.value.slice(-49), previous]
  lastDraft = JSON.stringify(tripPlan.value)
}
function resetDraftHistory() { draftUndo.value = []; lastDraft = null }
function undoDraft() {
  const previous = draftUndo.value.at(-1)
  if (!previous) return
  draftUndo.value = draftUndo.value.slice(0, -1)
  tripPlan.value = JSON.parse(previous)
  lastDraft = previous
  void refreshMaps()
}
function onUndoKey(event: KeyboardEvent) {
  if (!(editMode.value || diyMode.value) || !(event.ctrlKey || event.metaKey) || event.key.toLowerCase() !== 'z' || event.shiftKey) return
  const target = event.target as HTMLElement | null
  if (target && (target.isContentEditable || ['INPUT', 'TEXTAREA', 'SELECT'].includes(target.tagName))) return
  event.preventDefault()
  undoDraft()
}
const reorderVisit = (dayIdx: number, from: number, to: number) => {
  const list = tripPlan.value?.days[dayIdx]?.attractions
  if (!list || to < 0 || to >= list.length) return
  const [moved] = list.splice(from, 1)
  list.splice(to, 0, moved)
  draftChanged(dayIdx)
}
const moveVisit = (dayIdx: number, itemIdx: number, targetDay: number) => {
  if (!tripPlan.value || dayIdx === targetDay) return
  const source = tripPlan.value.days[dayIdx], target = tripPlan.value.days[targetDay]
  const place = source?.attractions[itemIdx]
  if (!place || !target) return
  if ((tripPlan.value.ticket_bookings || []).some(b => b.status === 'recorded' && b.day_date === source.date && b.item_id === place.item_id)) { message.warning('该景点已录入预订，请先处理预订记录再跨日移动'); return }
  if (place.locked || place.fixed_start_time || place.entry_window) { message.warning('锁定或有固定预约的景点不能移动'); return }
  if (source.attractions.length < 2) { message.warning('每天至少保留一个景点'); return }
  source.attractions.splice(itemIdx, 1)
  target.attractions.push(place)
  draftChanged(targetDay, false); draftChanged(dayIdx)
  message.success(`已把${place.name}移到第 ${targetDay + 1} 天，保存后生效`)
}
const toggleVisitLock = (dayIdx: number, itemIdx: number) => {
  const place = tripPlan.value?.days[dayIdx]?.attractions[itemIdx]
  if (!place) return
  place.locked = !place.locked
  recordDraft()
}
const dropDay = ref(-1)
const onTabDragOver = (event: DragEvent, index: number) => {
  if (!(editMode.value || diyMode.value) || !event.dataTransfer?.types.includes(VISIT_MIME)) return
  event.preventDefault()
  dropDay.value = index
}
const onTabDrop = (event: DragEvent, index: number) => {
  dropDay.value = -1
  const raw = event.dataTransfer?.getData(VISIT_MIME)
  if (!raw) return
  event.preventDefault()
  const { day, index: itemIdx } = JSON.parse(raw) as { day: number; index: number }
  moveVisit(day, itemIdx, index)
}
/** Days whose visits were added, removed or reordered relative to the saved plan. */
function arrangementChangedDates(before: TripPlan | null, after: TripPlan | null) {
  if (!before || !after) return []
  const signature = (day: TripPlan['days'][number]) => JSON.stringify([day.attractions.map(p => p.item_id), day.meals.map(p => p.item_id), day.hotel?.item_id || ''])
  return after.days.filter((day, i) => before.days[i] && signature(day) !== signature(before.days[i])).map(day => day.date)
}
async function afterSaveOptimize(dates: string[]) {
  if (!autoOptimize.value || !dates.length) return
  try { await optimizeDates(dates) }
  catch (e: any) { if (!runDisposed) message.warning('修改已保存，但自动优化未完成：' + e.message) }
}

// ============ 天气与雨天备选 ============
const weatherAdvice = ref<WeatherAdvice | null>(null)
const weatherLoading = ref(false)
async function checkWeather() {
  if (weatherLoading.value) return
  weatherLoading.value = true
  try {
    await ensureSaved()
    const advice = await weatherCheck(savedId.value)
    weatherAdvice.value = advice
    // Always give a visible verdict; a repeated check with unchanged content otherwise looks like nothing happened.
    const count = advice.days.reduce((total, day) => total + day.suggestions.length, 0)
    if (!advice.forecast_available) message.info('出行日期超出高德短期预报范围（约 4 天），临近出发时再检查')
    else if (count) message.warning(`预报有雨雪，找到 ${count} 处户外安排可换成室内地点`)
    else message.success('预报期内没有需要调整的户外安排')
    await nextTick()
    const panel = document.querySelector('.weather-panel')
    panel?.scrollIntoView({ behavior: 'smooth', block: 'nearest' })
    panel?.animate([{ boxShadow: '0 0 0 3px var(--color-text)' }, { boxShadow: '0 0 0 0 transparent' }], { duration: 900 })
  }
  catch (e: any) { message.error(e.message) }
  finally { weatherLoading.value = false }
}
function applyWeatherCandidate(dayIdx: number, itemId: string, candidate: WeatherCandidate) {
  const day = tripPlan.value?.days[dayIdx]
  const itemIdx = day?.attractions.findIndex(place => place.item_id === itemId) ?? -1
  if (itemIdx < 0) { message.warning('该景点已不在当前行程中，请重新检查天气'); return }
  if (applyPlace('attraction', dayIdx, itemIdx, candidate)) void selectDay(dayIdx)
}

// 切换编辑模式
const toggleEditMode = () => {
  if (chatLoading.value || saving.value) return
  editMode.value = true
  // 保存原始数据用于取消编辑
  originalPlan.value = JSON.parse(JSON.stringify(tripPlan.value))
  resetDraftHistory()
  message.info('进入编辑模式')
}

// 保存修改
const saveChanges = async () => {
  if (saving.value) return
  saving.value = true
  try {
    const changedDates = arrangementChangedDates(originalPlan.value, tripPlan.value)
    await persistPlan('manual')
    editMode.value = false; originalPlan.value = null; resetDraftHistory()
    message.success('修改已保存，可在修改记录中恢复')
    await afterSaveOptimize(changedDates)
  } catch(e: any) { message.error(e.message + '；编辑内容仍保留，可重试或取消') }
  finally { saving.value = false }
}

// 取消编辑
const cancelEdit = () => {
  if ((editMode.value || diyMode.value) && originalPlan.value) {
    tripPlan.value = JSON.parse(JSON.stringify(originalPlan.value))
  }
  editMode.value = false
  diyMode.value = false
  originalPlan.value = null
  resetDraftHistory()
  void refreshMaps()
  if (tripPlan.value) void loadPhotosForDay(activeDays.value[0])
  message.info('已取消')
}

// ============ DIY模式 ============
const saveDiyChanges = async () => {
  if (saving.value) return
  saving.value = true
  try {
    const changedDates = arrangementChangedDates(originalPlan.value, tripPlan.value)
    await persistPlan('diy')
    diyMode.value = false; originalPlan.value = null; resetDraftHistory()
    await afterSaveOptimize(changedDates)
    message.success('DIY 调整已保存，可撤销')
  } catch(e: any) { message.error(e.message + '；编辑内容仍保留') }
  finally { saving.value = false }
}

const openAlternatives = async (type: PlaceKind, dayIdx: number, itemIdx: number, item: any) => {
  const token = ++altRequestId
  altTask.value = 'nearby'
  altType.value = type
  altPreference.value = ''
  altFromAgent.value = false
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
    const res = await fetch(apiUrl('/api/trip/alternatives'), {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        city: tripPlan.value!.city,
        day_index: dayIdx,
        replace_type: type,
        current_name: item.name || item.restaurant || '',
        current_category: item.category || '',
        current_location: item.location || null,
        context: ctxParts.join('; ')
      })
    })
    const data = await readApiJson(res)
    if (token !== altRequestId) return
    if (data.success && data.data?.alternatives) {
      alternatives.value = data.data.alternatives
    } else {
      message.warning(data.message || '暂无合适备选')
    }
  } catch (err: any) {
    if (token === altRequestId) message.error(err.message || '获取备选项失败')
  } finally {
    if (token === altRequestId) altLoading.value = false
  }
}

const startEditDraft = () => {
  if (!editMode.value && !diyMode.value) {
    originalPlan.value = JSON.parse(JSON.stringify(tripPlan.value))
    editMode.value = true
    resetDraftHistory()
  }
}
const draftChanged = (dayIdx: number, record = true) => {
  if (record) recordDraft()
  const day = tripPlan.value!.days[dayIdx]
  day.schedule = null
  day.visit_order = []
  day.planned_start_times = {}
  day.route_notes = ''
  void refreshMaps()
  void loadPhotosForDay(dayIdx)
}
const applyPlace = (kind: PlaceKind, dayIdx: number, itemIdx: number, item: any) => {
  if (!tripPlan.value || saving.value || chatLoading.value) return false
  const day = tripPlan.value.days[dayIdx]
  if (kind === 'meal' && itemIdx < 0 && addMealSlot.value !== 'snack' && day.meals.some(meal => meal.type === addMealSlot.value)) {
    message.warning('该餐次已有安排，请使用现有餐厅的“替换”，或选择加餐')
    return false
  }
  startEditDraft()
  const previous = kind === 'hotel' ? day.hotel : kind === 'meal' ? day.meals[itemIdx] : day.attractions[itemIdx]
  const common = { item_id: previous?.item_id || crypto.randomUUID(), poi_id: item.poi_id || '', name: item.name, address: item.address || '', location: item.location || { longitude: 0, latitude: 0 }, cost_status: 'unknown' as const, cost_source: '', open_hours: item.open_hours || null }
  // Restaurants and hotels may carry an Amap reference price; attractions get a ticket estimate on save.
  const providerCost = kind !== 'attraction' && item.estimated_cost != null ? { estimated_cost: item.estimated_cost, cost_status: 'estimated' as const, cost_source: item.cost_source || '高德参考，未核实' } : { estimated_cost: null }
  if (kind === 'attraction') {
    const place = { ...common, visit_duration: item.visit_duration || 120, description: item.reason || '用户选择的地点', category: item.category || '景点', ticket_price: null, rating: item.rating ?? undefined }
    if (itemIdx < 0) day.attractions.push(place)
    else day.attractions[itemIdx] = place
  } else if (kind === 'meal') {
    const place = { ...common, type: itemIdx < 0 ? addMealSlot.value : day.meals[itemIdx].type, restaurant: item.name, description: item.reason || '用户选择的餐厅', ...providerCost }
    if (itemIdx < 0) day.meals.push(place)
    else day.meals[itemIdx] = place
  } else {
    day.hotel = { ...common, price_range: '', rating: item.rating != null ? String(item.rating) : '', distance: '', type: item.category || '酒店', ...providerCost }
    day.accommodation = item.name
  }
  draftChanged(dayIdx)
  message.success(`已${itemIdx < 0 ? '添加' : '替换'}${placeLabel(kind)}，保存后生效`)
  return true
}
const selectAlternative = (idx: number) => {
  const item = alternatives.value[idx]
  if (item && applyPlace(altType.value, altTargetDay.value, altTargetIdx.value, item)) altModalVisible.value = false
}
const openDayRecommendations = (kind: PlaceKind, dayIdx: number) => {
  altRequestId++
  addModalVisible.value = false
  altType.value = kind; altTargetDay.value = dayIdx; altTargetIdx.value = kind === 'hotel' && tripPlan.value?.days[dayIdx].hotel ? 0 : -1
  altPreference.value = ''; alternatives.value = []; altFromAgent.value = false; altLoading.value = false
  addMealSlot.value = 'snack'; altModalVisible.value = true
}
const queryPlaceCandidates = async (agent: boolean) => {
  if (!tripPlan.value || altLoading.value) return
  const token = ++altRequestId
  altTask.value = agent ? 'agent' : 'search'
  altLoading.value = true
  const day = tripPlan.value.days[altTargetDay.value]
  const excludedIds = [...day.attractions, ...day.meals, ...(day.hotel ? [day.hotel] : [])].map(place => place.poi_id || ('restaurant' in place && place.restaurant) || place.name)
  const existing = [...day.attractions.map(p => p.name), ...day.meals.map(p => p.restaurant || p.name), ...(day.hotel ? [day.hotel.name] : [])]
  const anchor = day.attractions[0]?.location || day.hotel?.location
  try {
    const response = await fetch(apiUrl(agent ? '/api/trip/recommend-places' : '/api/trip/search-poi'), {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(agent ? { city: tripPlan.value.city, kind: altType.value, preference: altPreference.value, context: `第${altTargetDay.value + 1}天，已有地点：${existing.join('、')}`.slice(0,1500), anchor, excluded_names: [...excludedIds, ...(altFromAgent.value ? alternatives.value.map(p => p.poi_id || p.name) : [])].slice(0,50) } : { city: tripPlan.value.city, kind: altType.value, keyword: altPreference.value })
    })
    const data = await readApiJson(response)
    if (token !== altRequestId) return
    if (!response.ok || !data.success) throw new Error(data.detail || data.message || '查询失败')
    alternatives.value = data.data?.[agent ? 'alternatives' : 'results'] || []
    altFromAgent.value = agent
    if (!alternatives.value.length) message.info('暂未找到合适候选，请换个偏好或直接搜索名称')
  } catch (error: any) { if (token === altRequestId) message.error(error.message || '查询失败，原行程保留') }
  finally { if (token === altRequestId) altLoading.value = false }
}
const askPlaceRecommendations = () => queryPlaceCandidates(true)
const searchAlternatives = () => queryPlaceCandidates(false)
const removeMeal = (dayIdx: number, index: number) => {
  if (!tripPlan.value) return
  tripPlan.value.days[dayIdx].meals.splice(index,1); draftChanged(dayIdx)
}
const removeHotel = (dayIdx: number) => {
  if (!tripPlan.value) return
  tripPlan.value.days[dayIdx].hotel = undefined; tripPlan.value.days[dayIdx].accommodation = '住宿待定'; draftChanged(dayIdx)
}

// ============ DIY 添加景点/餐厅 ============
const openAddModal = (type: PlaceKind, dayIdx: number) => {
  addType.value = type
  addMealSlot.value = 'snack'
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
    const res = await fetch(apiUrl('/api/trip/search-poi'), {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ keyword: kw, city: tripPlan.value!.city, kind: addType.value })
    })
    const data = await readApiJson(res)
    if (data.success && data.data?.results) {
      addResults.value = data.data.results
    }
  } catch (e: any) { message.error(e.message || '搜索失败') }
  finally { addSearchLoading.value = false; addSearchDone.value = true }
}

const confirmAddItem = (item: any) => {
  if (applyPlace(addType.value, addTargetDay.value, -1, item)) addModalVisible.value = false
}

const confirmManualAdd = () => {
  const name = addManualName.value.trim()
  if (!name) { message.warning('请输入名称'); return }
  confirmAddItem({
    name, address: addManualAddress.value || `${tripPlan.value!.city}市`,
    location: { longitude: 0, latitude: 0 },
    reason: '手动添加', estimated_cost: 0
  })
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
  draftChanged(dayIndex)
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
  draftChanged(dayIndex)
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

const escapeHtml = (value: unknown): string => String(value ?? '').replace(/[&<>"']/g, char => ({
  '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;'
})[char] || char)

// 获取美食图片
const getMealImage = (meal: any, dayIndex: number): string => {
  const searchName = meal.restaurant || meal.name
  const key = `${searchName}_${dayIndex}`

  if (mealPhotos.value[key]) {
    return mealPhotos.value[key]
  }

  const showName = escapeHtml((meal.restaurant || meal.name || '餐饮推荐').slice(0, 16))
  const svg = `<svg xmlns="http://www.w3.org/2000/svg" width="400" height="240"><style>rect{fill:#f5f5f7}text,path{fill:#727277}</style><rect width="400" height="240"/><path d="M183 68v27m8-27v27m8-27v27m-8 0v40m27-67v67" stroke="#888" stroke-width="2" fill="none"/><text x="200" y="175" text-anchor="middle" font-family="system-ui,sans-serif" font-size="14">${showName}</text><text x="200" y="199" text-anchor="middle" font-family="system-ui,sans-serif" font-size="10">暂无实景图片</text></svg>`

  return svgToDataUrl(svg)
}

// 同一个查询只请求一次；仅加载当前展开日期的图片
const photoRequests = new Map<string, Promise<string | null>>()
const fetchPhoto = (name: string, city: string): Promise<string | null> => {
  const key = `${city}:${name}`
  if (!photoRequests.has(key)) {
    const request = fetch(apiUrl(`/api/poi/photo?name=${encodeURIComponent(name)}&city=${encodeURIComponent(city)}`))
      .then(res => res.ok ? res.json() : Promise.reject(new Error('图片服务暂时不可用')))
      .then(data => data?.data?.photo_url || null)
      .catch(() => { photoRequests.delete(key); return null })
    photoRequests.set(key, request)
  }
  return photoRequests.get(key)!
}

const loadPhotosForDay = async (dayIndex: number) => {
  const plan = tripPlan.value
  const day = plan?.days[dayIndex]
  if (!plan || !day) return

  const attractionTasks = day.attractions.map(async attraction => {
    if (attractionPhotos.value[attraction.name]) return
    const photo = attraction.image_url || await fetchPhoto(attraction.name, plan.city)
    if (photo) attractionPhotos.value[attraction.name] = photo
  })
  const mealTasks = day.meals.map(async meal => {
    const name = meal.restaurant || meal.name || ''
    const key = `${name}_${day.day_index}`
    if (mealPhotos.value[key]) return
    if (meal.image_url) {
      mealPhotos.value[key] = meal.image_url
      return
    }
    for (const query of [name]) {
      if (!query) continue
      const photo = await fetchPhoto(query, plan.city)
      if (photo) {
        mealPhotos.value[key] = photo
        break
      }
    }
  })
  await Promise.all([...attractionTasks, ...mealTasks])
}

// 获取景点图片
const getAttractionImage = (name: string, _index: number): string => {
  if (attractionPhotos.value[name]) {
    return attractionPhotos.value[name]
  }

  const shortName = escapeHtml(name.slice(0, 16))
  const svg = `<svg xmlns="http://www.w3.org/2000/svg" width="400" height="240"><style>rect{fill:#f5f5f7}text{fill:#727277}</style><rect width="400" height="240"/><g fill="none" stroke="#888" stroke-width="1.8" stroke-linejoin="round"><path d="m175 82 25-12 25 12v6h-50v-6Zm0 48h50m-45-37v31m13-31v31m14-31v31m13-31v31"/></g><text x="200" y="171" text-anchor="middle" font-family="system-ui,sans-serif" font-size="14">${shortName}</text><text x="200" y="195" text-anchor="middle" font-family="system-ui,sans-serif" font-size="10">暂无实景图片</text></svg>`

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

// Capture only an export snapshot. The displayed day, theme and form remain unchanged.
const exporting = ref(false)
const exportDocument = async (kind: 'image' | 'pdf') => {
  if (exporting.value) return
  const source = document.querySelector<HTMLElement>('.main-content')
  if (!source || !tripPlan.value) return
  exporting.value = true
  message.loading({content: kind === 'pdf' ? '正在生成 PDF…' : '正在生成图片…', key:'export',duration:0})
  try {
    await nextTick()
    const { capturePlanImage, capturePlanPdf } = await import('@/utils/exportSnapshot')
    const title = `${tripPlan.value.city}旅行计划 · ${tripPlan.value.start_date} — ${tripPlan.value.end_date}`
    const name = `旅行计划_${tripPlan.value.city}_${Date.now()}`
    if (kind === 'image') await capturePlanImage(source,title,`${name}.png`)
    else await capturePlanPdf(source,title,`${name}.pdf`,(part,total)=>{
      message.loading({content:`正在生成 PDF（${part} / ${total}）…`,key:'export',duration:0})
    })
    message.success({content:kind === 'pdf'?'PDF 导出成功':'图片导出成功',key:'export'})
  } catch(error:any) {
    console.error('导出失败:',error)
    message.error({content:`导出失败：${error.message || '请稍后重试'}`,key:'export'})
  } finally {exporting.value=false}
}
const exportAsImage = () => exportDocument('image')
const exportAsPDF = () => exportDocument('pdf')

// 初始化地图
/** AMap computes NaN view bounds in a zero-size container; wait for layout first (max 3 s). */
function containerSized(element: HTMLElement | null) {
  return new Promise<void>(resolve => {
    if (!element || (element.clientWidth && element.clientHeight)) return resolve()
    const observer = new ResizeObserver(() => {
      if (element.clientWidth && element.clientHeight) { observer.disconnect(); resolve() }
    })
    observer.observe(element)
    setTimeout(() => { observer.disconnect(); resolve() }, 3000)
  })
}
// Single-flight: overlapping calls (mount + refresh) must not create two maps in one container.
let mapInit: Promise<void> | null = null
const initMap = () => {
  mapInit ??= buildMap().finally(() => { mapInit = null })
  return mapInit
}
const buildMap = async () => {
  const hasMapPoints = tripPlan.value?.days.some(day => [...day.attractions, ...day.meals, ...(day.hotel ? [day.hotel] : [])].some(
    place => place.location?.longitude && place.location?.latitude
  ))
  if (!hasMapPoints) {
    mapError.value = '目前没有可用的景点坐标，行程详情仍可正常查看。'
    return
  }
  try {
    await containerSized(document.getElementById('amap-container'))
    const AMap = await AMapLoader.load({
      key: import.meta.env.VITE_AMAP_WEB_JS_KEY,  // 高德地图Web端(JS API) Key
      version: '2.0',
      plugins: ['AMap.Marker', 'AMap.Polyline', 'AMap.InfoWindow']
    })

    amapApi = AMap
    // 已有地图时只替换标记，避免每次保存都销毁重建
    if (map && !map.getContainer?.()?.isConnected) { map.destroy(); map = null }
    if (map) map.clearMap()
    else map = new AMap.Map('amap-container', {
      zoom: 12,
      // 2D is enough for a day overview; the 3D renderer threw NaN pixel errors on key events.
      viewMode: '2D',
      // Map keyboard panning would compete with the day tabs' arrow keys.
      keyboardEnable: false
    })

    // 添加景点标记
    addAttractionMarkers(AMap)
  } catch (error) {
    console.error('地图加载失败:', error)
    mapError.value = '地图暂时无法加载，行程详情仍可正常查看。'
  }
}

/**
 * Draw each queried segment along its real Amap path: rides solid, walks dashed.
 * Segments without a stored path fall back to a faint straight line that only shows order.
 */
function drawDayRoutes(AMap: any, day: TripPlan['days'][number], stops: ReturnType<typeof orderedStops>) {
  const segments = day.schedule?.segments || []
  const byId: Record<string, [number, number]> = {}
  for (const stop of stops) {
    const id = stop.place.item_id || ''
    byId[id] = byId[`${id}-start`] = byId[`${id}-end`] = [stop.place.location.longitude, stop.place.location.latitude]
  }
  const straight = (path: [number, number][]) => map.add(new AMap.Polyline({ path, strokeColor: '#1f1f1f', strokeWeight: 2, strokeOpacity: 0.35, strokeStyle: 'dashed' }))
  if (!segments.length) {
    if (stops.length > 1) straight(stops.map(stop => [stop.place.location.longitude, stop.place.location.latitude]))
    return
  }
  for (const segment of segments) {
    const path = (segment.polyline || '').split(';').map(point => point.split(',').map(Number) as [number, number]).filter(p => p.length === 2 && p.every(Number.isFinite))
    if (segment.status === 'verified' && path.length > 1) {
      const ride = segment.legs?.some(leg => leg.kind === 'subway' || leg.kind === 'bus' || leg.kind === 'rail')
      map.add(new AMap.Polyline({ path, strokeColor: ride ? '#2563eb' : '#6b7280', strokeWeight: ride ? 5 : 4, strokeOpacity: 0.85,
        strokeStyle: ride ? 'solid' : 'dashed', showDir: !!ride, lineJoin: 'round' }))
      continue
    }
    const from = endpointCoordinates(segment.from_endpoint) || byId[segment.from_id || ''], to = endpointCoordinates(segment.to_endpoint) || byId[segment.to_id || '']
    if (from && to) straight([from, to])
  }
}

/** Stops of one day in visit order: the checked timeline when present, else attractions. */
function orderedStops(day: TripPlan['days'][number]) {
  const places: Record<string, { place: any; kind: 'attraction' | 'meal' | 'hotel' }> = {}
  day.attractions.forEach((place, index) => { places[place.item_id || ''] = places[`attraction-${index}`] = { place, kind: 'attraction' } })
  day.meals.forEach((place, index) => { places[place.item_id || ''] = places[`meal-${index}`] = places[place.type] = { place, kind: 'meal' } })
  if (day.hotel) places[day.hotel.item_id || ''] = places.hotel = { place: day.hotel, kind: 'hotel' }
  const items = day.schedule?.items || []
  const ordered = items.length
    ? items.filter(item => !item.id.endsWith('-end')).map(item => { const stop = places[item.id.replace(/-start$/, '')]; return stop ? { ...stop, timelineId: item.id } : null }).filter((stop): stop is NonNullable<typeof stop> => !!stop)
    : day.attractions.map(place => ({ place, kind: 'attraction' as const }))
  return ordered.filter(stop => stop.place.location?.longitude && stop.place.location?.latitude)
}

// The aside map follows the selected day; markers are swapped, the map instance is reused.
const addAttractionMarkers = (AMap: any) => {
  const day = tripPlan.value?.days[activeDays.value[0] ?? 0]
  if (!day || !map) return
  markerLookup.clear()
  const stops = orderedStops(day)
  const colors = { attraction: '#1f1f1f', meal: '#b45309', hotel: '#6b7280' }
  const names = { attraction: '游览', meal: '用餐', hotel: '住宿' }
  let order = 0
  const markers = stops.map(stop => {
    const label = stop.kind === 'hotel' ? '宿' : String(++order)
    const name = stop.place.restaurant || stop.place.name
    const marker = new AMap.Marker({
      position: [stop.place.location.longitude, stop.place.location.latitude],
      title: name,
      // A fixed pixel offset; anchor-based centring yields NaN offsets for HTML content on resize.
      offset: new AMap.Pixel(-12, -12),
      content: `<div style="width:24px;height:24px;border-radius:50%;background:${colors[stop.kind]};color:#fff;border:2px solid #fff;box-shadow:0 1px 4px rgba(0,0,0,.35);display:flex;align-items:center;justify-content:center;font-size:12px;font-weight:600">${label}</div>`,
    })
    marker.on('click', () => {
      selectedStop.value = 'timelineId' in stop ? stop.timelineId as string : stop.place.item_id || ''
      new AMap.InfoWindow({
        content: `<div style="padding:8px 10px;font-size:13px;line-height:1.7"><strong>${escapeHtml(name)}</strong><br>${names[stop.kind]} · 第${day.day_index + 1}天<br><span style="color:#666">${escapeHtml(stop.place.address || '')}</span></div>`,
        offset: new AMap.Pixel(0, -14),
      }).open(map, marker.getPosition())
    })
    markerLookup.set(stop.place.item_id, marker)
    if ('timelineId' in stop) markerLookup.set(stop.timelineId as string, marker)
    if (stop.kind === 'hotel') markerLookup.set('hotel', marker)
    return marker
  })
  const gateMarkers = dayGatePoints(day).map(point => {
    const label = point.role === 'entrance' ? '入' : '出'
    const marker = new AMap.Marker({
      position: [point.location.longitude, point.location.latitude], title: `${point.name} · ${label}口参考`,
      offset: new AMap.Pixel(-10, -10),
      content: `<div style="width:20px;height:20px;border-radius:5px;background:#fff;color:#0071e3;border:1px solid #0071e3;text-align:center;line-height:20px;font-size:11px">${label}</div>`,
    })
    marker.on('click', () => {
      const venue = day.attractions.find(place => place.poi_id === point.venue_poi_id)
      if (venue) selectedStop.value = venue.item_id || ''
      new AMap.InfoWindow({ content: `<div style="padding:8px 10px;font-size:12px;line-height:1.7"><strong>${escapeHtml(point.name)}</strong><br>${label}口门址参考，非实时开放保证<br>请按预约与官方公告核对。</div>`, offset: new AMap.Pixel(0, -12) }).open(map, marker.getPosition())
    })
    return marker
  })
  map.add([...markers, ...gateMarkers])
  drawDayRoutes(AMap, day, stops)
  const container = map.getContainer?.()
  if (markers.length && container?.clientWidth && container?.clientHeight) map.setFitView([...markers, ...gateMarkers], false, [40, 40, 40, 40])
}
</script>

<style scoped>
.checks-feedback { display: flex; flex-wrap: wrap; align-items: center; justify-content: space-between; gap: 10px; max-width: 1200px; margin: 16px auto; padding: 12px 16px; border: 1px solid var(--color-border); border-radius: 12px; background: var(--color-surface); color: var(--color-muted); font-size: 13px; line-height: 1.6; }
.advice-reference-note { color: var(--color-muted); font-size: 12px; line-height: 1.7; }
.visit-preparation { display: flex; justify-content: space-between; align-items: center; gap: 16px; margin: 14px 0; padding: 14px 16px; border: 1px solid var(--color-border); border-radius: 12px; background: var(--color-surface); }
.visit-preparation strong { display: block; font-size: 13px; margin-bottom: 4px; }
.visit-preparation span { color: var(--color-muted); font-size: 12px; line-height: 1.6; }
.visit-preparation button { flex-shrink: 0; border: 0; background: none; color: var(--color-primary, #0071e3); cursor: pointer; font-size: 12px; }
@media (max-width: 600px) { .visit-preparation { align-items: flex-start; flex-direction: column; gap: 8px; } }

.version-panel { max-width:1200px; margin:16px auto; background:var(--color-surface); border:1px solid var(--color-border); border-radius:14px; padding:18px; }
.version-panel h2 { font-size:16px; color:var(--color-text); }
.version-panel article { display:flex; justify-content:space-between; gap:12px; align-items:center; border-top:1px solid var(--color-border); padding:12px 0; flex-wrap:wrap; }
.version-panel p,.version-panel small { font-size:12px; color:var(--color-muted); }
.version-panel small { display:block; margin-top:5px; }
.version-panel button { border:1px solid var(--color-border); border-radius:8px; background:var(--color-subtle); padding:7px 10px; color:var(--color-text); cursor:pointer; }
.version-panel button:disabled { opacity:.5; cursor:default; }
.reference-usage { margin:16px 0; }
.reference-usage details { margin:10px 0; font-size:13px; }
.reference-usage summary { cursor:pointer; }
.source-original { white-space:pre-wrap; max-height:220px; overflow:auto; }
.usage-list article { display:grid; grid-template-columns:1fr auto; font-size:13px; }
.usage-list small { grid-column:1 / -1; }
.persistence-error { text-align:center; color:var(--color-muted); padding:15px; }

.result-container {
  min-height: 100vh;
  background: var(--color-subtle);
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
  background: var(--color-surface);
}

.side-nav :deep(.ant-menu-item) {
  margin: 4px 8px;
  border-radius: 8px;
  transition: all 0.3s ease;
}

.side-nav :deep(.ant-menu-item-selected) {
  background: var(--color-subtle);
  color: var(--color-on-primary);
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
  background: var(--color-subtle);
  color: var(--color-on-primary);
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
  color: var(--color-on-primary);
  padding: 4px 12px;
  border-radius: 12px;
  font-weight: bold;
  font-size: 14px;
  box-shadow: 0 2px 8px rgba(0, 0, 0, 0.2);
}

/* 天气卡片样式 */
.weather-card {
  background: var(--color-subtle);
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
  color: var(--color-text);
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
  color: var(--color-muted);
}

.weather-value {
  font-size: 16px;
  font-weight: 600;
  color: var(--color-text);
}

.weather-wind {
  margin-top: 8px;
  padding-top: 8px;
  border-top: 1px solid rgba(0, 121, 107, 0.2);
  text-align: center;
  color: var(--color-text);
  font-size: 14px;
}

/* 回到顶部按钮 */
.back-top-button {
  width: 50px;
  height: 50px;
  background: var(--color-subtle);
  color: var(--color-on-primary);
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
  background: var(--color-subtle);
  border: none !important;
}

.hotel-card :deep(.ant-card-head) {
  background: var(--color-subtle);
}

.hotel-title {
  color: var(--color-on-primary) !important;
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
  color: var(--color-muted);
}

.info-value {
  font-size: 15px;
  color: var(--color-text);
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
  background: var(--color-subtle);
  border-radius: 8px;
  border: 1px solid var(--color-border);
}

.budget-label {
  font-size: 13px;
  color: var(--color-muted);
  margin-bottom: 8px;
}

.budget-value {
  font-size: 20px;
  font-weight: 700;
  color: var(--color-muted);
}

.budget-total {
  display: flex;
  justify-content: space-between;
  align-items: center;
  padding: 16px;
  background: var(--color-subtle);
  border-radius: 8px;
  color: var(--color-on-primary);
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
  color: var(--color-text);
}

.day-date {
  font-size: 14px;
  color: var(--color-muted);
}

.day-info {
  margin-bottom: 20px;
  padding: 16px;
  background: var(--color-subtle);
  border-radius: 8px;
  border: 1px solid var(--color-border);
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
  color: var(--color-muted);
  min-width: 100px;
}

.info-row .value {
  color: var(--color-text);
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
  background: var(--color-subtle);
  color: var(--color-on-primary) !important;
  border-radius: 12px 12px 0 0;
  font-weight: 600;
}

:deep(.ant-card-head-title) {
  color: var(--color-on-primary) !important;
  font-size: 18px;
}

:deep(.ant-card-head-title span) {
  color: var(--color-on-primary) !important;
}

/* Collapse样式 */
:deep(.ant-collapse) {
  border: none;
  background: transparent;
}

:deep(.ant-collapse-item) {
  margin-bottom: 16px;
  border: 1px solid var(--color-border);
  border-radius: 12px;
  overflow: hidden;
}

:deep(.ant-collapse-header) {
  background: var(--color-subtle);
  padding: 16px 20px !important;
  font-weight: 600;
}

:deep(.ant-collapse-content) {
  border-top: 1px solid var(--color-border);
}

:deep(.ant-collapse-content-box) {
  padding: 20px;
}

/* 统计卡片样式 */
:deep(.ant-statistic-title) {
  font-size: 14px;
  color: var(--color-muted);
  margin-bottom: 8px;
}

:deep(.ant-statistic-content) {
  font-size: 24px;
  font-weight: 600;
  color: var(--color-muted);
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
  background: var(--color-subtle);
  padding: 10px 14px;
  border-radius: 8px;
  border-left: 4px solid var(--color-border);
}

.route-notes-value {
  color: var(--color-muted);
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
  color: var(--color-on-primary);
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
  color: var(--color-text);
  margin-bottom: 4px;
}

.meal-dish {
  font-size: 13px;
  color: var(--color-muted);
  font-weight: 500;
  margin-bottom: 4px;
}

.meal-address {
  font-size: 12px;
  color: var(--color-muted);
  margin-bottom: 4px;
  line-height: 1.4;
}

.meal-desc {
  font-size: 12px;
  color: var(--color-muted);
  margin-bottom: 6px;
  line-height: 1.4;
}

.meal-cost {
  font-size: 14px;
  font-weight: 700;
  color: var(--color-muted);
  text-align: right;
}

/* DIY备选项 */
.alt-list {
  display: flex;
  flex-direction: column;
  gap: 10px;
}

.alt-card {
  border: 1px solid var(--color-border);
  border-radius: 10px;
  cursor: pointer;
  transition: all 0.25s ease;
}

.alt-card:hover {
  border-color: var(--color-border);
  box-shadow: 0 4px 14px rgba(102, 126, 234, 0.15);
  transform: translateY(-2px);
}

.alt-name {
  font-size: 16px;
  font-weight: 700;
  color: var(--color-text);
  margin-bottom: 4px;
}

.alt-address {
  font-size: 13px;
  color: var(--color-muted);
  margin-bottom: 4px;
}

.alt-meta {
  font-size: 13px;
  color: var(--color-muted);
  margin-bottom: 4px;
}

.alt-reason {
  font-size: 12px;
  color: var(--color-muted);
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
  border: 1px solid var(--color-border);
}

.route-summary {
  background: var(--color-surface);
  border-radius: 10px;
  padding: 14px;
  border: 1px solid var(--color-border);
}

.route-summary-title {
  font-weight: 700;
  font-size: 14px;
  color: var(--color-text);
  margin-bottom: 10px;
}

.route-segment {
  padding: 10px 12px;
  background: var(--color-surface);
  border-radius: 8px;
  margin-bottom: 8px;
  border-left: 4px solid var(--color-border);
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
  color: var(--color-muted);
}

.seg-duration {
  font-size: 13px;
  color: var(--color-muted);
  font-weight: 600;
}

.seg-distance {
  font-size: 12px;
  color: var(--color-muted);
}

.seg-stops {
  font-size: 13px;
  color: var(--color-text);
  margin: 4px 0;
}

.seg-arrow {
  margin: 0 6px;
  color: var(--color-muted);
}

.seg-transit-detail {
  font-size: 12px;
  color: var(--color-muted);
  margin-top: 4px;
  padding: 4px 8px;
  background: var(--color-subtle);
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

/* Shared travel studio treatment for the itinerary */
.result-container {
  min-height: 70vh;
  padding: 46px 24px 80px;
  background:
    var(--color-subtle),
    var(--color-bg);
}
.page-header {
  max-width: 1280px;
  margin-bottom: 32px;
  align-items: flex-end;
  gap: 24px;
  animation: fadeInUp .55s ease-out both;
}
.result-heading { min-width: 0; }
.back-button {
  display: block;
  margin: 0 0 24px;
  padding: 0;
  border: 0;
  background: transparent;
  box-shadow: none;
  color: var(--color-primary);
  font-weight: 650;
}
.back-button:hover { color: var(--color-primary-dark) !important; background: transparent !important; }
.result-eyebrow { color: var(--color-primary); font-size: 11px; font-weight: 750; letter-spacing: .2em; }
.result-heading h1 { margin: 7px 0 7px; color: var(--color-text); font-size: clamp(28px, 3.2vw, 44px); line-height: 1.25; font-weight: 760; letter-spacing: -.055em; }
.result-heading p { margin: 0; color: var(--color-muted); font-size: 14px; }
.header-actions { justify-content: flex-end; padding-bottom: 4px; }
.header-actions :deep(.ant-btn) { border-radius: 10px; min-height: 36px; font-weight: 560; transition: transform .22s ease, box-shadow .22s ease, background .22s ease; }
.header-actions :deep(.ant-btn:hover) { transform: translateY(-2px); box-shadow: var(--shadow-soft); }
.header-actions :deep(.ant-btn:active) { transform: translateY(1px); }
.content-wrapper { max-width: 1280px; gap: 24px; align-items: flex-start; animation: fadeInUp .6s .06s ease-out both; }
.side-nav { width: 206px; }
.side-nav :deep(.ant-menu) { padding: 10px 6px; border: 1px solid var(--color-border); border-radius: 18px; box-shadow: var(--shadow-soft); }
.side-nav :deep(.ant-menu-item), .side-nav :deep(.ant-menu-submenu-title) { border-radius: 10px; margin: 3px 4px; width: calc(100% - 8px); color: var(--color-muted); }
.side-nav :deep(.ant-menu-item-selected) { background: var(--color-subtle); color: var(--color-primary-dark); font-weight: 700; }
.side-nav :deep(.ant-menu-item:hover), .side-nav :deep(.ant-menu-submenu-title:hover) { background: var(--color-subtle); color: var(--color-primary); }
.mobile-nav { display: none; }
.main-content { min-width: 0; }
.journey-stats { display: grid; grid-template-columns: repeat(4, minmax(0, 1fr)); gap: 12px; margin-bottom: 18px; }
.journey-stat { display: flex; flex-direction: column; gap: 3px; min-width: 0; padding: 16px 18px; border: 1px solid var(--color-border); border-radius: 16px; background: var(--color-surface); box-shadow: var(--shadow-soft); }
.journey-stat strong { color: var(--color-primary-dark); font-size: 23px; line-height: 1.2; letter-spacing: -.035em; }
.journey-stat span { color: var(--color-muted); font-size: 12px; line-height: 1.4; }
.agent-card { margin-bottom: 20px; padding: 22px; border: 1px solid var(--color-border); border-radius: 20px; background: var(--color-subtle); box-shadow: var(--shadow-soft); scroll-margin-top: 24px; }
.agent-heading { display: flex; align-items: center; gap: 14px; }
.agent-avatar { display: grid; flex: 0 0 44px; place-items: center; width: 44px; height: 44px; border-radius: 14px; background: var(--color-primary); color: var(--color-on-primary); font-size: 26px; box-shadow: 0 8px 18px rgba(23,107,101,.18); }
.agent-eyebrow { color: var(--color-primary); font-size: 10px; font-weight: 800; letter-spacing: .18em; }
.agent-heading h2 { margin: 3px 0; color: var(--color-text); font-size: 20px; letter-spacing: -.04em; }
.agent-heading p { margin: 0; color: var(--color-muted); font-size: 12px; line-height: 1.5; }
.agent-status { display: inline-flex; align-items: center; gap: 6px; margin-left: auto; color: var(--color-muted); font-size: 11px; white-space: nowrap; }
.agent-status span { width: 7px; height: 7px; border-radius: 50%; background: var(--color-primary); box-shadow: 0 0 0 4px #e2f4e9; }
.agent-messages { display: flex; flex-direction: column; gap: 14px; max-height: 390px; overflow-y: auto; margin: 20px 0 14px; padding: 17px; border: 1px solid var(--color-border); border-radius: 14px; background: rgba(255,255,255,.86); scrollbar-color: #c1d8cb transparent; }
.agent-welcome { color: var(--color-muted); font-size: 13px; line-height: 1.75; }
.agent-message { display: flex; flex-direction: column; align-items: flex-start; max-width: 88%; }
.agent-message.user { align-self: flex-end; align-items: flex-end; }
.agent-message-label { margin: 0 6px 5px; color: var(--color-muted); font-size: 11px; }
.agent-bubble { padding: 11px 14px; border: 1px solid var(--color-border); border-radius: 4px 14px 14px; background: var(--color-subtle); color: var(--color-text); font-size: 13px; line-height: 1.7; white-space: pre-wrap; overflow-wrap: anywhere; }
.agent-message.user .agent-bubble { border-color: var(--color-primary); border-radius: 14px 4px 14px 14px; background: var(--color-primary); color: var(--color-on-primary); }
.agent-actions, .agent-updated { margin: 5px 5px 0; color: var(--color-muted); font-size: 11px; }
.agent-updated { color: var(--color-primary); font-weight: 700; }
.agent-thinking { display: flex; align-items: center; gap: 9px; color: var(--color-muted); font-size: 12px; }
.agent-pulse { width: 9px; height: 9px; border-radius: 50%; background: var(--color-primary); animation: agentPulse 1.3s ease-in-out infinite; }
.agent-prompts { display: flex; flex-wrap: wrap; gap: 8px; margin-bottom: 14px; }
.agent-prompts button { padding: 8px 11px; border: 1px solid var(--color-border); border-radius: 999px; background: var(--color-surface); color: var(--color-primary-dark); font: inherit; font-size: 12px; cursor: pointer; transition: transform .2s ease, border-color .2s ease, box-shadow .2s ease; }
.agent-prompts button:hover:not(:disabled) { transform: translateY(-2px); border-color: var(--color-primary); box-shadow: var(--shadow-soft); }
.agent-compose { display: flex; align-items: flex-end; gap: 10px; padding: 7px; border: 1px solid var(--color-border); border-radius: 14px; background: var(--color-surface); transition: border-color .2s ease, box-shadow .2s ease; }
.agent-compose:focus-within { border-color: var(--color-primary); box-shadow: 0 0 0 3px rgba(23,107,101,.1); }
.agent-compose textarea { flex: 1; min-width: 0; min-height: 42px; max-height: 130px; padding: 9px; resize: vertical; border: 0; outline: 0; background: transparent; color: var(--color-text); font: inherit; font-size: 13px; line-height: 1.5; }
.agent-compose textarea::placeholder { color: var(--color-muted); }
.agent-compose button { min-width: 88px; height: 40px; padding: 0 14px; border: 0; border-radius: 10px; background: var(--color-primary); color: var(--color-on-primary); font: inherit; font-size: 13px; font-weight: 700; cursor: pointer; transition: transform .2s ease, background .2s ease, box-shadow .2s ease; }
.agent-compose button:hover:not(:disabled) { transform: translateY(-2px); background: var(--color-primary-dark); box-shadow: 0 6px 14px rgba(23,107,101,.2); }
.agent-compose button:disabled, .agent-prompts button:disabled { opacity: .5; cursor: not-allowed; }
.agent-note { margin-top: 9px; color: var(--color-muted); font-size: 11px; line-height: 1.5; }
.sr-only { position: absolute; width: 1px; height: 1px; padding: 0; margin: -1px; overflow: hidden; clip: rect(0,0,0,0); white-space: nowrap; border: 0; }
@keyframes agentPulse { 50% { opacity: .35; transform: scale(.75); } }
.top-info-section { display: grid; grid-template-columns: minmax(270px, .8fr) minmax(0, 1.2fr); gap: 20px; margin: 0 0 20px; }
.left-info { min-width: 0; display: grid; grid-template-rows: auto 1fr; gap: 20px; }
.right-map { min-width: 0; }
.main-content :deep(.ant-card) {
  margin-bottom: 0;
  overflow: hidden;
  border: 1px solid var(--color-border);
  border-radius: 18px;
  background: var(--color-surface);
  box-shadow: var(--shadow-soft);
  animation: none;
  transition: transform .25s ease, box-shadow .25s ease, border-color .25s ease;
}
.main-content :deep(.ant-card:hover) { box-shadow: 0 16px 38px rgba(27,61,66,.1); }
.main-content :deep(.ant-card-head) {
  min-height: 58px;
  padding: 0 22px;
  background: var(--color-surface);
  border-bottom: 1px solid var(--color-border);
  color: var(--color-text) !important;
}
.main-content :deep(.ant-card-head-title), .main-content :deep(.ant-card-head-title span) { color: var(--color-text) !important; font-size: 16px; font-weight: 710; letter-spacing: -.025em; }
.main-content :deep(.ant-card-body) { padding: 22px; }
.overview-card, .budget-card { height: auto; }
.overview-content { gap: 16px; }
.info-label { color: var(--color-primary); font-size: 12px; }
.info-value { color: var(--color-text); font-size: 14px; line-height: 1.8; }
.suggestion-copy.is-clamped { display: -webkit-box; -webkit-box-orient: vertical; -webkit-line-clamp: 5; overflow: hidden; }
.suggestion-toggle { align-self: flex-start; padding: 2px 0; border: 0; background: transparent; color: var(--color-primary); font: inherit; font-size: 12px; font-weight: 700; cursor: pointer; }
.suggestion-toggle:hover { color: var(--color-primary-dark); text-decoration: underline; }
.plan-caveat { padding: 12px 14px; border-radius: 11px; background: var(--color-subtle); color: var(--color-muted); font-size: 12px; line-height: 1.65; }
.budget-grid { gap: 10px; }
.budget-item { padding: 12px 8px; border: 1px solid var(--color-border); border-radius: 12px; background: var(--color-surface); }
.budget-label { color: var(--color-muted); font-size: 12px; margin-bottom: 3px; }
.budget-value { color: var(--color-text); font-size: 17px; }
.budget-total { padding: 14px 16px; border-radius: 12px; background: var(--color-primary); }
.total-label { font-size: 13px; }
.total-value { font-size: 24px; }
.known-cost-card :deep(.ant-card-body) { display: grid; gap: 10px; }
.known-cost-row { display: flex; justify-content: space-between; gap: 15px; color: var(--color-muted); font-size: 13px; }
.known-cost-row strong { color: var(--color-text); font-size: 14px; }
.known-cost-note { margin: 2px 0 0; color: var(--color-muted); font-size: 11px; line-height: 1.55; }
.map-card { min-height: 480px; height: 100%; }
.map-card :deep(.ant-card-body) { height: calc(100% - 58px); padding: 0; background: var(--color-subtle); }
.map-fallback { display: grid; place-items: center; height: 100%; min-height: 260px; padding: 24px; color: var(--color-muted); text-align: center; line-height: 1.7; }
.days-card { margin-top: 0; }
.days-card :deep(.ant-card-body) { padding: 18px 20px; }
.main-content :deep(.ant-collapse) { border: 0; background: transparent; }
.main-content :deep(.ant-collapse-item) { margin-bottom: 12px; border: 1px solid var(--color-border); border-radius: 14px; overflow: hidden; }
.main-content :deep(.ant-collapse-item:last-child) { margin-bottom: 0; }
.main-content :deep(.ant-collapse-header) { background: var(--color-surface); padding: 16px 18px !important; }
.main-content :deep(.ant-collapse-content) { border-top: 1px solid var(--color-border); }
.main-content :deep(.ant-collapse-content-box) { padding: 20px; }
.day-title { color: var(--color-text); font-size: 16px; font-weight: 720; }
.day-title small { margin-left: 10px; color: var(--color-muted); font-size: 12px; font-weight: 450; }
.day-date { color: var(--color-muted); }
.day-info { padding: 16px; border-color: var(--color-border); border-radius: 12px; background: var(--color-surface); }
.day-flow { display: flex; flex-wrap: wrap; align-items: stretch; gap: 8px; margin: 16px 0 8px; }
.day-flow-stop { display: flex; flex: 1 1 145px; min-width: 0; max-width: 240px; flex-direction: column; gap: 3px; padding: 10px 12px; border: 1px solid var(--color-border); border-radius: 10px; background: var(--color-surface); }
.day-flow-meal { border-color: var(--color-border); background: var(--color-subtle); }
.day-flow-kicker { color: var(--color-primary); font-size: 10px; font-weight: 750; letter-spacing: .06em; }
.day-flow-meal .day-flow-kicker { color: var(--color-muted); }
.day-flow-name { overflow: hidden; color: var(--color-text); font-size: 12px; font-weight: 600; text-overflow: ellipsis; white-space: nowrap; }
.info-row .label { color: var(--color-muted); }
.info-row .value { color: var(--color-text); }
.route-notes-row { background: var(--color-subtle); border-left: 3px solid var(--color-primary); }
.route-notes-value { color: var(--color-primary-dark); }
.main-content :deep(.ant-divider-inner-text) { color: var(--color-text); font-weight: 680; }
.attraction-card, .meal-card, .hotel-card, .weather-card { height: 100%; }
.main-content :deep(.ant-list-item) { height: 100%; transition: none; }
.main-content :deep(.ant-list-grid .ant-list-item) { padding: 0; }
.main-content :deep(.ant-list-item:hover) { transform: none; }
.main-content :deep(.ant-list-item .ant-card:hover) { transform: translateY(-3px); border-color: var(--color-border); box-shadow: var(--shadow-raised); }
.attraction-image-wrapper, .meal-image-wrapper { border-radius: 10px; }
.attraction-image { height: 180px; }
.image-disclaimer { position: absolute; right: 9px; bottom: 9px; z-index: 1; padding: 3px 7px; border-radius: 6px; background: rgba(26, 44, 44, .7); color: var(--color-on-primary); font-size: 10px; line-height: 1.3; }
.attraction-badge { background: var(--color-primary); box-shadow: 0 4px 12px rgba(16,83,79,.22); }
.price-tag { background: var(--color-primary); border-radius: 8px; box-shadow: none; }
.hotel-card { background: var(--color-subtle) !important; }
.hotel-card :deep(.ant-card-head) { background: var(--color-subtle); }
.hotel-card :deep(.ant-card-head-title) { white-space: normal; overflow: visible; text-overflow: unset; line-height: 1.45; }
.hotel-title { color: var(--color-text) !important; }
.hotel-note { margin: 8px 0 0; color: var(--color-muted); font-size: 11px; }
.meal-type-badge { background: rgba(24,65,60,.87); border-radius: 8px; }
.meal-name { color: var(--color-text); }
.meal-dish { color: var(--color-muted); }
.meal-cost { color: var(--color-primary); }
.meal-empty { padding: 24px; border: 1px dashed var(--color-border); border-radius: 12px; background: var(--color-surface); color: var(--color-muted); font-size: 13px; text-align: center; }
.weather-section { margin-top: 20px; }
.weather-card { background: var(--color-subtle) !important; }
.weather-date, .weather-value, .weather-wind { color: var(--color-primary-dark); }
.back-top-button { background: var(--color-primary); box-shadow: 0 8px 22px rgba(23,107,101,.25); }
.back-top-button:hover { box-shadow: 0 12px 28px rgba(23,107,101,.3); }
.alt-card { border-color: var(--color-border); }
.alt-card:hover { border-color: var(--color-border); box-shadow: var(--shadow-soft); }
.alt-name { color: var(--color-text); }
.alt-meta, .seg-mode, .seg-arrow { color: var(--color-primary); }
.day-mini-map { border-radius: 12px; border-color: var(--color-border); }
.day-route-section { margin-top: 22px; border: 1px solid var(--color-border); border-radius: 12px; background: var(--color-surface); overflow: hidden; }
.day-route-toggle { display: flex; justify-content: space-between; align-items: center; gap: 12px; width: 100%; padding: 14px 16px; border: 0; background: transparent; color: var(--color-text); font: inherit; font-size: 14px; font-weight: 700; text-align: left; cursor: pointer; }
.day-route-toggle:hover { background: var(--color-subtle); }
.day-route-toggle span:last-child { flex: 0 0 auto; color: var(--color-primary); font-size: 12px; font-weight: 600; }
.day-route-content { padding: 0 16px 16px; }
.route-caveat { margin: 0 0 12px; color: var(--color-muted); font-size: 12px; line-height: 1.55; }
.day-map-fallback { display: grid; place-items: center; min-height: 180px; margin-bottom: 12px; padding: 20px; border: 1px dashed var(--color-border); border-radius: 12px; background: var(--color-surface); color: var(--color-muted); text-align: center; }
.route-summary { background: var(--color-surface); border-color: var(--color-border); border-radius: 12px; }
.route-summary-title { color: var(--color-text); }
.route-segment { border-left-color: var(--color-primary); }
.seg-transit-detail { background: var(--color-subtle); color: var(--color-primary-dark); }
.empty-state { max-width: 560px; margin: 40px auto 0; padding: 52px 20px; border: 1px solid var(--color-border); border-radius: 20px; background: var(--color-surface); box-shadow: var(--shadow-soft); }
.empty-icon { display: grid; place-items: center; width: 70px; height: 70px; margin: auto; border-radius: 22px; background: var(--color-subtle); color: var(--color-primary); font-size: 40px; }
.empty-state :deep(.ant-empty-image) { height: 78px; }
.empty-state :deep(.ant-empty-description) { color: var(--color-muted); }
[id="overview"], [id="budget"], [id="map"], [id^="day-"], [id="weather"] { scroll-margin-top: 24px; }

@media (max-width: 1180px) {
  .side-nav { width: 180px; }
  .journey-stats { grid-template-columns: repeat(2, minmax(0, 1fr)); }
  .top-info-section { grid-template-columns: 1fr; }
  .left-info { display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); grid-template-rows: auto; }
  .map-card { height: 440px; min-height: 440px; }
}
@media (max-width: 900px) {
  .result-container { padding: 32px 20px 60px; }
  .page-header { align-items: flex-start; }
  .content-wrapper { display: block; }
  .side-nav { display: none; }
  .mobile-nav { display: flex; gap: 8px; overflow-x: auto; padding: 0 0 14px; margin-bottom: 8px; scrollbar-width: none; }
  .mobile-nav::-webkit-scrollbar { display: none; }
  .mobile-nav button { flex: 0 0 auto; padding: 8px 14px; border: 1px solid var(--color-border); border-radius: 999px; background: var(--color-surface); color: var(--color-primary-dark); font: inherit; font-size: 13px; cursor: pointer; transition: background .2s ease, transform .2s ease; }
  .mobile-nav button:hover { background: var(--color-subtle); transform: translateY(-2px); }
}
@media (max-width: 640px) {
  .result-container { padding: 25px 16px 48px; }
  .page-header { flex-direction: column; gap: 22px; margin-bottom: 22px; }
  .back-button { margin-bottom: 18px; }
  .header-actions { width: 100%; justify-content: flex-start; }
  .header-actions :deep(.ant-btn) { min-height: 36px; font-size: 12px; }
  .left-info { grid-template-columns: 1fr; }
  .top-info-section { gap: 14px; margin-bottom: 14px; }
  .map-card { height: 320px; min-height: 320px; }
  .main-content :deep(.ant-card-head) { padding: 0 16px; }
  .main-content :deep(.ant-card-body) { padding: 16px; }
  .map-card :deep(.ant-card-body) { padding: 0; }
  .main-content :deep(.ant-collapse-content-box) { padding: 14px; }
  .attraction-card :deep(.ant-card-head-wrapper) { flex-wrap: wrap; gap: 8px; padding: 10px 0; }
  .attraction-card :deep(.ant-card-extra) { margin: 0; }
  .attraction-card :deep(.ant-space) { flex-wrap: wrap; }
  .day-info { padding: 12px; }
  .day-flow { flex-wrap: nowrap; overflow-x: auto; margin-right: -14px; padding: 0 14px 8px 0; scrollbar-width: thin; }
  .day-flow-stop { flex: 0 0 138px; }
  .info-row { flex-direction: column; gap: 3px; }
  .info-row .label { min-width: 0; }
  .day-date { font-size: 12px; }
  .day-title small { display: block; margin: 2px 0 0; font-size: 11px; }
  .journey-stats { gap: 8px; }
  .agent-card { padding: 16px; }
  .agent-heading { align-items: flex-start; gap: 10px; }
  .agent-avatar { flex-basis: 38px; width: 38px; height: 38px; font-size: 22px; }
  .agent-heading h2 { font-size: 17px; }
  .agent-status { display: none; }
  .agent-message { max-width: 96%; }
  .agent-messages { padding: 13px; }
  .agent-compose button { min-width: 68px; }
  .journey-stat { padding: 12px; }
  .journey-stat strong { font-size: 20px; }
  .main-content :deep(.ant-descriptions-view table) { table-layout: fixed; }
  .main-content :deep(.ant-descriptions-item) { display: block; }
  .seg-header { flex-wrap: wrap; gap: 4px 10px; }
}
</style>


<style scoped>
.planning-progress{display:flex;align-items:center;justify-content:space-between;gap:16px;margin:16px 0;padding:18px 22px;border:1px solid var(--color-border);border-radius:12px;background:var(--color-surface);color:var(--color-text)}.planning-progress p{margin:8px 0;font-size:13px}.planning-progress small{color:var(--color-muted);font-size:11px}
</style>
