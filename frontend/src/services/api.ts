import axios from 'axios'
import type { TripFormData, TripPlanResponse } from '@/types'

const API_BASE_URL = import.meta.env.VITE_API_BASE_URL || ''

const apiClient = axios.create({
  baseURL: API_BASE_URL,
  timeout: 300000, // 5分钟超时
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
    return Promise.reject(error)
  }
)

/**
 * 生成旅行计划
 */
export async function generateTripPlan(formData: TripFormData): Promise<TripPlanResponse> {
  try {
    const response = await apiClient.post<TripPlanResponse>('/api/trip/plan', formData)
    return response.data
  } catch (error: any) {
    console.error('生成旅行计划失败:', error)
    throw new Error(error.response?.data?.detail || error.message || '生成旅行计划失败')
  }
}

/**
 * 流式生成旅行计划 (P4: SSE支持)
 * @param formData 旅行请求数据
 * @param onProgress 进度回调函数
 * @returns 返回Promise，完成后resolve旅行计划
 */
export function generateTripPlanStream(
  formData: TripFormData,
  onProgress: (progress: number, message: string) => void
): Promise<TripPlanResponse> {
  return new Promise((resolve, reject) => {
    const url = `${API_BASE_URL}/api/trip/plan/stream`
    
    // 超时机制: 如果60秒内没有收到进度更新，就认为SSE失败
    let lastProgressTime = Date.now()
    const timeoutTimer = setInterval(() => {
      if (Date.now() - lastProgressTime > 60000) {
        clearInterval(timeoutTimer)
        reject(new Error('SSE流式响应超时'))
      }
    }, 5000)

    // 使用fetch + ReadableStream实现SSE
    async function fetchStream() {
      try {
        const response = await fetch(url, {
          method: 'POST',
          headers: {
            'Content-Type': 'application/json',
            'Accept': 'text/event-stream'
          },
          body: JSON.stringify(formData)
        })

        if (!response.ok) {
          throw new Error(`HTTP error! status: ${response.status}`)
        }

        const reader = response.body?.getReader()
        const decoder = new TextDecoder()
        let buffer = ''

        if (!reader) {
          throw new Error('No reader available')
        }

        while (true) {
          const { done, value } = await reader.read()
          
          if (done) {
            break
          }

          buffer += decoder.decode(value, { stream: true })
          
          // 解析SSE事件 (以\n\n分隔)
          const events = buffer.split('\n\n')
          buffer = events.pop() || ''

          for (const event of events) {
            const lines = event.split('\n')
            let eventType = 'message'
            let eventData = ''

            for (const line of lines) {
              if (line.startsWith('event:')) {
                eventType = line.slice(6).trim()
              } else if (line.startsWith('data:')) {
                eventData = line.slice(5).trim()
              }
            }

            if (eventData) {
              try {
                const data = JSON.parse(eventData)
                lastProgressTime = Date.now()
                
                if (eventType === 'progress') {
                  onProgress(data.progress, data.message)
                } else if (eventType === 'complete') {
                  clearInterval(timeoutTimer)
                  resolve(data)
                  return
                } else if (eventType === 'error') {
                  clearInterval(timeoutTimer)
                  reject(new Error(data.message))
                  return
                }
              } catch (e) {
                console.error('SSE parse error:', e, eventData)
              }
            }
          }
        }

        // 如果没有收到complete事件，返回错误
        clearInterval(timeoutTimer)
        reject(new Error('流式响应未完成'))
      } catch (error) {
        clearInterval(timeoutTimer)
        reject(error)
      }
    }

    fetchStream()
  })
}

/**
 * 健康检查
 */
export async function healthCheck(): Promise<any> {
  try {
    const response = await apiClient.get('/health')
    return response.data
  } catch (error: any) {
    console.error('健康检查失败:', error)
    throw new Error(error.message || '健康检查失败')
  }
}

export default apiClient

