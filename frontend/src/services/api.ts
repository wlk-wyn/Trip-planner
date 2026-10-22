import axios from 'axios'
import { TripServiceError } from '@/utils/serviceError'
import { httpFailure } from '@/utils/httpResponse'
import type { ChatTurn, TripFormData, TripPlan, TripPlanResponse, TripChatResponse, TripIntakeResponse } from '@/types'

// Same-origin by default: local Vite proxies /api and production can use a
// reverse proxy. An explicit remote API origin remains configurable.
const API_BASE_URL = (import.meta.env.VITE_API_BASE_URL || '').replace(/\/$/, '')

export const apiUrl = (path: string): string => `${API_BASE_URL}/${path.replace(/^\//, '')}`

const apiClient = axios.create({
  baseURL: API_BASE_URL,
  timeout: 30000,
  headers: {
    'Content-Type': 'application/json'
  }
})

// 请求拦截器
apiClient.interceptors.request.use(
  (config) => {
    console.log('发送请求:', config.method?.toUpperCase(), config.url)
    return config
  },
  (error) => {
    console.error('请求错误:', error)
    return Promise.reject(error)
  }
)

// 响应拦截器
apiClient.interceptors.response.use(
  (response) => {
    console.log('收到响应:', response.status, response.config.url)
    return response
  },
  (error) => {
    console.error('响应错误:', error.response?.status, error.message)
    if (error.response) return Promise.reject(httpFailure(error.response.status, error.response.data?.detail, error.response.headers?.['retry-after']))
    return Promise.reject(error)
  }
)

/**
 * 生成旅行计划
 */
export async function generateTripPlan(formData: TripFormData): Promise<TripPlanResponse> {
  try {
    const response = await apiClient.post<TripPlanResponse>('/api/trip/plan', formData, {
      timeout: 300000 // 多阶段 AI 规划可能需要数分钟
    })
    return response.data
  } catch (error: any) {
    console.error('生成旅行计划失败:', error)
    if (error instanceof TripServiceError) throw error
    if (error.code === 'ECONNABORTED') {
      throw new Error('规划用时较长，请稍后重试，或减少旅行天数和参考内容')
    }
    if (!error.response) {
      throw new Error('无法连接规划服务，请检查本地后端是否运行')
    }
    throw new Error(error.response?.data?.detail || error.message || '生成旅行计划失败')
  }
}

export async function interpretTripIdea(history: ChatTurn[]): Promise<TripIntakeResponse> {
  try {
    const response = await apiClient.post<TripIntakeResponse>('/api/trip/intake', { history }, { timeout: 45000 })
    return response.data
  } catch (error: any) {
    if (error instanceof TripServiceError) throw error
    if (error.code === 'ECONNABORTED') throw new Error('理解需求用时较长，请重试')
    if (!error.response) throw new Error('无法连接规划服务，请检查本地后端是否运行')
    if (error.response.status === 404) throw new Error('本地后端尚未加载新入口，请重启后端，或使用详细设置')
    throw new Error(error.response?.data?.detail || error.message || '暂时无法理解需求')
  }
}

export async function chatWithTripAgent(
  plan: TripPlan, message: string, history: ChatTurn[], originalRequest?: TripFormData
): Promise<TripChatResponse> {
  try {
    const response = await apiClient.post<TripChatResponse>('/api/trip/chat', {
      plan, message, history, original_request: originalRequest
    }, { timeout: 300000 })
    return response.data
  } catch (error: any) {
    if (error instanceof TripServiceError) throw error
    if (error.code === 'ECONNABORTED') throw new Error('调整时间过长，请稍后重试')
    if (!error.response) throw new Error('无法连接本地规划服务')
    throw new Error(error.response?.data?.detail || error.message || '智能体调整失败')
  }
}

/**
 * 健康检查
 */
export async function healthCheck(): Promise<any> {
  try {
    const response = await apiClient.get('/health')
    return response.data
  } catch (error: any) {
    if (error instanceof TripServiceError) throw error
    console.error('健康检查失败:', error)
    throw new Error(error.message || '健康检查失败')
  }
}

export default apiClient

