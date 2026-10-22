<template>
      <a-form
        :model="formData"
        layout="vertical"
        @finish="emit('submit')"
      >
        <!-- 第一步:目的地和日期 -->
        <div class="form-section">
          <div class="section-header">
            <span class="section-icon"></span>
            <span class="section-title">目的地与日期</span>
          </div>

          <a-row :gutter="[20, 4]">
            <a-col :xs="24" :sm="12" :lg="8">
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
                    <span>⌖</span>
                  </template>
                </a-input>
              </a-form-item>
            </a-col>
            <a-col :xs="24" :sm="12" :lg="6">
              <a-form-item name="start_date" :rules="[{ required: true, message: '请选择开始日期' }]">
                <template #label>
                  <span class="form-label">开始日期</span>
                </template>
                <a-date-picker
                  :value="pickerValue(formData.start_date)" @update:value="value => setDate('start_date', value)"
                  style="width: 100%"
                  size="large"
                  class="custom-input"
                  placeholder="选择日期"
                />
              </a-form-item>
            </a-col>
            <a-col :xs="24" :sm="12" :lg="6">
              <a-form-item name="end_date" :rules="[{ required: true, message: '请选择结束日期' }]">
                <template #label>
                  <span class="form-label">结束日期</span>
                </template>
                <a-date-picker
                  :value="pickerValue(formData.end_date)" @update:value="value => setDate('end_date', value)"
                  style="width: 100%"
                  size="large"
                  class="custom-input"
                  placeholder="选择日期"
                />
              </a-form-item>
            </a-col>
            <a-col :xs="24" :sm="12" :lg="4">
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
            <span class="section-icon"></span>
            <span class="section-title">偏好设置</span>
          </div>

          <a-row :gutter="[20, 4]">
            <a-col :xs="24" :md="12" :lg="7">
              <a-form-item name="transportation">
                <template #label>
                  <span class="form-label">交通方式</span>
                </template>
                <a-select v-model:value="formData.transportation" size="large" class="custom-select">
                  <a-select-option value="公共交通"> 公共交通</a-select-option>
                  <a-select-option value="自驾"> 自驾</a-select-option>
                  <a-select-option value="步行"> 步行</a-select-option>
                  <a-select-option value="混合"> 混合</a-select-option>
                </a-select>
              </a-form-item>
            </a-col>
            <a-col :xs="24" :md="12" :lg="7">
              <a-form-item name="accommodation">
                <template #label>
                  <span class="form-label">住宿偏好</span>
                </template>
                <a-select v-model:value="formData.accommodation" size="large" class="custom-select">
                  <a-select-option value="经济型酒店"> 经济型酒店</a-select-option>
                  <a-select-option value="舒适型酒店"> 舒适型酒店</a-select-option>
                  <a-select-option value="豪华酒店">⭐ 豪华酒店</a-select-option>
                  <a-select-option value="民宿"> 民宿</a-select-option>
                </a-select>
              </a-form-item>
            </a-col>
            <a-col :xs="24" :lg="10">
              <a-form-item name="preferences">
                <template #label>
                  <span class="form-label">旅行偏好</span>
                </template>
                <div class="preference-tags">
                  <a-checkbox-group v-model:value="formData.preferences" class="custom-checkbox-group">
                    <a-checkbox value="历史文化" class="preference-tag"> 历史文化</a-checkbox>
                    <a-checkbox value="自然风光" class="preference-tag"> 自然风光</a-checkbox>
                    <a-checkbox value="美食" class="preference-tag"> 美食</a-checkbox>
                    <a-checkbox value="购物" class="preference-tag"> 购物</a-checkbox>
                    <a-checkbox value="艺术" class="preference-tag"> 艺术</a-checkbox>
                    <a-checkbox value="休闲" class="preference-tag"> 休闲</a-checkbox>
                  </a-checkbox-group>
                </div>
              </a-form-item>
            </a-col>
          </a-row>
        </div>

        <div class="form-section">
          <div class="section-header"><span class="section-title">每日可用时间</span></div>
          <a-row :gutter="16"><a-col :xs="12" :sm="8"><a-form-item label="出发"><input type="time" v-model="formData.daily_start_time" /></a-form-item></a-col><a-col :xs="12" :sm="8"><a-form-item label="结束"><input type="time" v-model="formData.daily_end_time" /></a-form-item></a-col><a-col :xs="24" :sm="8"><a-form-item label="每段交通缓冲（分钟）"><a-input-number v-model:value="formData.buffer_minutes" :min="0" :max="60" /></a-form-item></a-col></a-row>
        </div>
        <div class="form-section">
          <div class="section-header"><span class="section-title">人数与费用参考</span></div>
          <a-row :gutter="16">
            <a-col :xs="12" :sm="6"><a-form-item label="同行人数"><a-input-number aria-label="同行人数" :value="formData.party_size ?? undefined" @update:value="value => formData.party_size = value == null || value === '' ? null : Number(value)" :min="1" :max="50" :precision="0" placeholder="待确定" style="width:100%" /></a-form-item></a-col>
            <a-col :xs="12" :sm="6"><a-form-item label="每晚房间数"><a-input-number aria-label="每晚房间数" :value="formData.room_count ?? undefined" @update:value="value => formData.room_count = value == null || value === '' ? null : Number(value)" :min="1" :max="50" :precision="0" placeholder="待确定" style="width:100%" /></a-form-item></a-col>
            <a-col :xs="24" :sm="12"><a-form-item label="目的地内全体预算（元）"><a-input-number aria-label="目的地内全体预算" :value="formData.destination_budget ?? undefined" @update:value="value => formData.destination_budget = value == null || value === '' ? null : Number(value)" :disabled="formData.budget_mode === 'deferred'" :min="0" :max="10000000" :precision="2" placeholder="选填，不含往返大交通" style="width:100%" /></a-form-item></a-col>
          </a-row>
          <label><input type="checkbox" :checked="formData.budget_mode === 'deferred'" @change="formData.budget_mode = ($event.target as HTMLInputElement).checked ? 'deferred' : 'active'" /> 预算稍后再定</label>
          <p class="known-cost-note">门票和乘车按成人参考价测算，儿童、学生等优惠另核实。住宿默认计算返程日前的各晚。</p>
        </div>
        <!-- 第三步:额外要求 -->
        <div class="form-section">
          <div class="section-header">
            <span class="section-icon"></span>
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
              <span class="button-icon"></span>
              <span>开始规划我的旅行</span>
            </template>
            <template v-else>
              <span>正在生成中...</span>
            </template>
          </a-button>
        </a-form-item>

      </a-form>
</template>
<script setup lang="ts">
import dayjs, { type Dayjs } from 'dayjs'
import type { TripFormDraft } from '../utils/tripDraft'
const props = defineProps<{ formData: TripFormDraft; loading: boolean }>()
const emit = defineEmits<{ submit: [] }>()
const pickerValue = (value: unknown) => (value ?? undefined) as Dayjs | undefined
const setDate = (key: 'start_date' | 'end_date', value: unknown) => { props.formData[key] = value ? dayjs(value as Dayjs) : null }
</script>
<style scoped>
.form-section {padding:27px 0 29px;border-top:1px solid var(--color-border)}
.section-header {display:flex;align-items:center;gap:12px;margin-bottom:22px}
.section-icon {display:grid;place-items:center;width:38px;height:38px;border-radius:12px;background:var(--color-subtle);font-size:18px}
.section-title {font-size:17px;font-weight:720;color:var(--color-text);letter-spacing:-.02em}
.form-label {color:var(--color-muted);font-size:13px;font-weight:650}
.days-display-compact {display:flex;align-items:baseline;gap:6px;padding:8px 14px;background:var(--color-subtle);border-radius:10px}
.days-value {font-size:24px;font-weight:600;color:var(--color-text)}
.days-unit,.known-cost-note {font-size:12px;color:var(--color-muted)}
.known-cost-note {line-height:1.8;margin-top:12px}
.preferences-grid {display:flex;flex-wrap:wrap;gap:10px}
input[type=time] {font:inherit;color:var(--color-text);background:var(--color-surface);border:1px solid var(--color-border);border-radius:10px;padding:8px}
.submit-button {border-radius:24px}
</style>
