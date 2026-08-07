"""旅行规划API路由"""

import os
import re
import json
import base64
import requests
from io import BytesIO
from bs4 import BeautifulSoup
from fastapi import APIRouter, HTTPException
from sse_starlette.sse import EventSourceResponse
from pydantic import BaseModel, Field
from typing import Optional, AsyncIterator
from PIL import Image
from ...models.schemas import (
    TripRequest,
    TripPlanResponse,
    ErrorResponse
)
from ...agents.langgraph_planner import get_langgraph_planner

router = APIRouter(prefix="/trip", tags=["旅行规划"])

# 规划引擎: 使用 LangGraph
PLANNER_ENGINE = os.getenv("PLANNER_ENGINE", "langgraph")


@router.post(
    "/plan",
    response_model=TripPlanResponse,
    summary="生成旅行计划",
    description="根据用户输入的旅行需求,生成详细的旅行计划"
)
async def plan_trip(request: TripRequest):
    """
    生成旅行计划 (默认使用LangGraph引擎)

    Args:
        request: 旅行请求参数

    Returns:
        旅行计划响应
    """
    try:
        print(f"\n{'='*60}")
        print(f"📥 收到旅行规划请求 (引擎: {PLANNER_ENGINE})")
        print(f"   城市: {request.city}")
        print(f"   日期: {request.start_date} - {request.end_date}")
        print(f"   天数: {request.travel_days}")
        print(f"{'='*60}\n")

        print("🔄 使用 LangGraph 引擎...")
        planner = get_langgraph_planner()
        trip_plan = await planner.plan_trip_async(request)

        print("✅ 旅行计划生成成功,准备返回响应\n")

        return TripPlanResponse(
            success=True,
            message="旅行计划生成成功",
            data=trip_plan
        )

    except Exception as e:
        print(f"❌ 生成旅行计划失败: {str(e)}")
        import traceback
        traceback.print_exc()
        raise HTTPException(
            status_code=500,
            detail=f"生成旅行计划失败: {str(e)}"
        )


@router.post(
    "/plan/stream",
    summary="流式生成旅行计划",
    description="SSE流式响应，实时推送规划进度",
    response_class=EventSourceResponse
)
async def plan_trip_stream(request: TripRequest) -> EventSourceResponse:
    """
    流式生成旅行计划 (P4: SSE支持)

    通过Server-Sent Events实时推送规划进度事件:
    - progress: 进度更新事件
    - complete: 规划完成事件
    - error: 错误事件
    """
    import asyncio
    
    print(f"\n{'='*60}")
    print(f"📡 SSE流式规划请求 (引擎: {PLANNER_ENGINE})")
    print(f"   城市: {request.city}")
    print(f"   天数: {request.travel_days}")
    print(f"{'='*60}\n")

    # 使用队列进行进度消息传递
    progress_queue: asyncio.Queue = asyncio.Queue()

    async def progress_callback(percent: int, message: str):
        """进度回调: 将进度消息放入队列"""
        await progress_queue.put({
            "event": "progress",
            "data": json.dumps({
                "progress": percent,
                "message": message,
            }, ensure_ascii=False)
        })

    async def run_planning():
        """在线程中执行规划"""
        try:
            planner = get_langgraph_planner()
            trip_plan = await planner.plan_trip_stream(
                request=request,
                progress_callback=progress_callback
            )
            # 发送完成事件
            await progress_queue.put({
                "event": "complete",
                "data": json.dumps({
                    "success": True,
                    "message": "旅行计划生成成功",
                    "data": trip_plan.model_dump()
                }, ensure_ascii=False)
            })
        except Exception as e:
            print(f"❌ SSE流式规划失败: {str(e)}")
            import traceback
            traceback.print_exc()
            await progress_queue.put({
                "event": "error",
                "data": json.dumps({
                    "success": False,
                    "message": f"生成旅行计划失败: {str(e)}"
                }, ensure_ascii=False)
            })
        finally:
            # 发送结束信号
            await progress_queue.put(None)

    async def event_generator() -> AsyncIterator[dict]:
        """SSE事件生成器"""
        # 启动规划任务
        planning_task = asyncio.create_task(run_planning())

        while True:
            # 从队列获取消息
            event = await progress_queue.get()
            
            if event is None:
                # 结束信号
                break
                
            yield event

        # 等待规划任务完成
        await planning_task

    return EventSourceResponse(event_generator())


@router.get(
    "/health",
    summary="健康检查",
    description="检查旅行规划服务是否正常"
)
async def health_check():
    """健康检查"""
    try:
        from ...services.mcp_manager import get_mcp_manager
        manager = get_mcp_manager()

        return {
            "status": "healthy",
            "service": "trip-planner",
            "mcp_tools_count": manager.get_tools_count()
        }
    except Exception as e:
        raise HTTPException(
            status_code=503,
            detail=f"服务不可用: {str(e)}"
        )


@router.get(
    "/mcp/status",
    summary="MCP连接状态",
    description="获取MCP连接管理器的状态信息 (P5优化)"
)
async def mcp_status():
    """获取 MCP 连接状态 (P5: MCP 连接优化)"""
    from ...services.mcp_manager import get_mcp_manager
    manager = get_mcp_manager()
    
    return {
        "status": "success",
        "data": manager.get_status()
    }


@router.post(
    "/mcp/reconnect",
    summary="MCP重新连接",
    description="强制重新建立MCP连接 (P5优化)"
)
async def mcp_reconnect():
    """强制重新建立 MCP 连接 (P5: MCP 连接优化)"""
    from ...services.mcp_manager import get_mcp_manager
    manager = get_mcp_manager()

    success = await manager.ensure_connected()

    return {
        "status": "success" if success else "error",
        "message": "MCP 重连成功" if success else "MCP 重连失败",
        "data": manager.get_status()
    }


@router.get(
    "/debug-search",
    summary="调试: 搜索景点原始结果",
    description="直接调用MCP搜索工具，返回原始结果（调试用）"
)
async def debug_search(city: str = "北京", keywords: str = "景点"):
    """调试接口: 直接调用 MCP 工具，返回原始结果"""
    from ...services.mcp_manager import get_mcp_manager
    manager = get_mcp_manager()
    await manager.ensure_connected()

    tool = manager.get_tool("maps_text_search")
    if tool is None:
        return {"error": "未找到 maps_text_search 工具", "tools_count": manager.get_tools_count()}

    result = await tool.ainvoke({
        "keywords": keywords,
        "city": city,
        "citylimit": "true"
    })

    # 提取文本
    if isinstance(result, str):
        content = result
    else:
        content = getattr(result, "content", str(result))
        if isinstance(content, list):
            parts = []
            for part in content:
                if isinstance(part, dict) and "text" in part:
                    parts.append(str(part["text"]))
                elif isinstance(part, str):
                    parts.append(part)
            content = "\n".join(parts)

    return {
        "city": city,
        "keywords": keywords,
        "result_type": type(result).__name__,
        "content_length": len(content) if content else 0,
        "content_preview": content[:2000] if content else "(空)",
        "tools_available": [t.name for t in manager.get_all_tools()][:20]
    }


class ExtractUrlRequest(BaseModel):
    """URL提取请求"""
    url: str = Field(..., description="小红书或其他网页URL")


class ExtractImageRequest(BaseModel):
    """图片提取请求"""
    image_base64: str = Field(..., description="base64编码的图片数据")


@router.post(
    "/extract-url",
    summary="提取URL文本内容",
    description="从小红书或其他网页提取正文内容"
)
async def extract_url_content(request: ExtractUrlRequest):
    """
    抓取URL页面并提取纯文本内容

    注意: 小红书等SPA页面无法通过简单HTTP请求获取内容，
    因为正文由JavaScript动态加载。对这类页面会提示手动粘贴。
    """
    try:
        # 检测小红书链接，提前告知限制
        is_xiaohongshu = "xiaohongshu.com" in request.url or "xhslink.com" in request.url

        # 多UA轮换
        user_agents = [
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
            "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/123.0.0.0 Safari/537.36 Edg/123.0.0.0",
        ]
        import random
        headers = {
            "User-Agent": random.choice(user_agents),
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8",
            "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
            "Accept-Encoding": "gzip, deflate, br",
            "Cache-Control": "no-cache",
            "Sec-Ch-Ua": '"Chromium";v="124", "Google Chrome";v="124"',
            "Sec-Ch-Ua-Mobile": "?0",
            "Sec-Ch-Ua-Platform": '"Windows"',
        }

        resp = requests.get(request.url, headers=headers, timeout=15, allow_redirects=True, verify=True)
        resp.encoding = resp.apparent_encoding or "utf-8"

        if resp.status_code != 200:
            return {
                "success": False,
                "message": f"页面返回 {resp.status_code}，请直接在浏览器打开链接，复制正文粘贴到下方文本框中",
                "data": None
            }

        html = resp.text

        # 检测反爬/验证页面
        block_signs = ["访问验证", "请完成安全验证", "captcha", "滑块验证", "请先登录", "请登录",
                        "Request Blocked", "访问被拒绝", "请稍后再试", "系统检测到异常流量"]
        for sign in block_signs:
            if sign in html:
                hint = "小红书链接请直接在浏览器打开后复制正文粘贴" if is_xiaohongshu else "请直接在浏览器打开后复制正文粘贴"
                return {
                    "success": False,
                    "message": f"页面需要验证或登录（检测到「{sign}」），无法自动提取。{hint}",
                    "data": None
                }

        soup = BeautifulSoup(html, "html.parser")

        # 移除无用标签
        for tag in soup(["script", "style", "nav", "footer", "header", "noscript", "meta", "link", "iframe"]):
            tag.decompose()

        # 策略1: 小红书专用选择器
        if is_xiaohongshu:
            note_selectors = [
                "#detail-desc", ".note-content", ".note-scroller", ".content",
                "[class*='note-text']", "[class*='desc']", ".note-text",
                "#noteContainer", "[id*='detail']", "[class*='Detail']",
            ]
            for sel in note_selectors:
                elements = soup.select(sel)
                if elements:
                    texts = [el.get_text(separator="\n", strip=True) for el in elements[:5] if len(el.get_text(strip=True)) > 30]
                    if texts:
                        content = "\n\n".join(texts)
                        content = re.sub(r'\n{3,}', '\n\n', content)
                        if len(content) > 100:
                            return {
                                "success": True,
                                "message": "内容提取成功",
                                "data": {"content": content[:5000]}
                            }

        # 策略2: 通用正文选择器
        note_elements = soup.select("article, .post-content, .article-content, .entry-content, "
                                     ".note-content, .note-text, .content, .desc, "
                                     "[class*='note'], [class*='post'], [class*='article'], "
                                     "main, [role='main']")
        if note_elements:
            texts = []
            for el in note_elements[:5]:
                t = el.get_text(separator="\n", strip=True)
                if len(t) > 50:
                    texts.append(t)
            if texts:
                content = "\n\n".join(texts)
                content = re.sub(r'\n{3,}', '\n\n', content)
                return {
                    "success": True,
                    "message": "内容提取成功",
                    "data": {"content": content[:5000]}
                }

        # 策略3: 提取 body 全部文本
        body = soup.body
        if body:
            text = body.get_text(separator="\n", strip=True)
            lines = [line.strip() for line in text.split("\n") if len(line.strip()) > 10]
            content = "\n".join(lines[:200])
            content = re.sub(r'\n{3,}', '\n\n', content)

            # SPA 检测: 页面文本很少 = 很可能是JS渲染的
            if len(content) < 200:
                spa_hint = ""
                if is_xiaohongshu:
                    spa_hint = ("\n\n小红书是动态渲染页面，无法通过链接自动抓取。"
                                "请在浏览器中打开该链接，Ctrl+A 全选 → Ctrl+C 复制 → 粘贴到下方文本框中。")
                return {
                    "success": False,
                    "message": f"页面内容极少（仅{len(content)}字符），可能是JS动态渲染页面。" + spa_hint,
                    "data": None
                }

            if len(content) < 100:
                spa_hint = ("请直接在浏览器打开链接，复制正文粘贴到文本框中" if is_xiaohongshu else "请尝试手动粘贴内容")
                return {
                    "success": False,
                    "message": f"未能提取到足够正文。" + spa_hint,
                    "data": None
                }

            return {
                "success": True,
                "message": "内容提取成功（通用模式）",
                "data": {"content": content[:5000]}
            }
        else:
            return {
                "success": False,
                "message": "无法解析页面内容，请尝试手动粘贴",
                "data": None
            }

    except requests.exceptions.Timeout:
        return {
            "success": False,
            "message": "请求超时，请在浏览器打开链接后复制正文粘贴到文本框中",
            "data": None
        }
    except requests.exceptions.RequestException as e:
        return {
            "success": False,
            "message": f"请求失败，请直接在浏览器打开链接后复制正文粘贴",
            "data": None
        }
    except Exception as e:
        print(f"❌ URL内容提取失败: {str(e)}")
        return {
            "success": False,
            "message": "提取失败，请尝试手动粘贴内容",
            "data": None
        }


@router.post(
    "/extract-image",
    summary="提取图片文字(OCR)",
    description="从上传的截图(base64)中提取文字"
)
async def extract_image_content(request: ExtractImageRequest):
    """
    对上传的截图进行OCR，提取文字

    Args:
        request: 包含base64图片数据的请求

    Returns:
        OCR提取的文字
    """
    try:
        # 解析base64（去除可能的 data:image/png;base64, 前缀）
        img_data = request.image_base64
        if "," in img_data and img_data.startswith("data:"):
            img_data = img_data.split(",", 1)[1]

        image_bytes = base64.b64decode(img_data)
        image = Image.open(BytesIO(image_bytes))

        # 尝试使用 pytesseract OCR
        try:
            import pytesseract
            # 配置中文识别
            text = pytesseract.image_to_string(image, lang="chi_sim+eng")
        except ImportError:
            return {
                "success": False,
                "message": "OCR服务未安装，请尝试手动粘贴内容",
                "data": None
            }
        except Exception as tesseract_err:
            print(f"❌ OCR识别失败: {str(tesseract_err)}")
            return {
                "success": False,
                "message": f"OCR识别失败: {str(tesseract_err)[:100]}，请确认已安装Tesseract及中文语言包，或手动粘贴内容",
                "data": None
            }

        # 清理识别结果
        text = text.strip()
        if not text or len(text) < 10:
            return {
                "success": False,
                "message": "未能从截图中提取到足够文字，请尝试手动粘贴内容",
                "data": None
            }

        # 基本清理：合并多余空行
        lines = [line.strip() for line in text.split("\n") if line.strip()]
        cleaned = "\n".join(lines)

        return {
            "success": True,
            "message": "图片文字提取成功",
            "data": {"content": cleaned[:5000]}
        }

    except base64.binascii.Error:
        return {
            "success": False,
            "message": "图片数据格式错误，请重新上传",
            "data": None
        }
    except Exception as e:
        print(f"❌ 图片文字提取失败: {str(e)}")
        return {
            "success": False,
            "message": "提取失败，请尝试手动粘贴内容",
            "data": None
        }


class AlternativesRequest(BaseModel):
    """备选项请求"""
    city: str = Field(..., description="城市")
    day_index: int = Field(..., description="第几天(0开始)")
    replace_type: str = Field(..., description="替换类型: attraction/meal")
    current_name: str = Field(..., description="当前景点/餐厅名称")
    current_category: Optional[str] = Field(default="", description="当前类别")
    context: Optional[str] = Field(default="", description="当天其他行程上下文")


@router.post(
    "/alternatives",
    summary="获取AI优化的备选项",
    description="为DIY替换提供Agent筛选后的备选景点或餐厅"
)
async def get_alternatives(request: AlternativesRequest):
    """
    获取备选景点或餐厅

    Agent会根据:
    1. 地理位置(与其他景点距离合理)
    2. 类别匹配(同类型)
    3. 时间可行性
    筛选出3-5个最优备选项

    Args:
        request: 备选项请求

    Returns:
        备选项列表
    """
    try:
        planner = get_langgraph_planner()
        alternatives = await planner.get_alternatives_async(
            city=request.city, day_index=request.day_index,
            replace_type=request.replace_type, current_name=request.current_name,
            current_category=request.current_category, context=request.context
        )
        return {
            "success": True,
            "message": f"找到{len(alternatives)}个备选",
            "data": {"alternatives": alternatives}
        }
    except Exception as e:
        print(f"❌ 获取备选项失败: {str(e)}")
        import traceback
        traceback.print_exc()
        return {
            "success": False,
            "message": f"获取备选项失败: {str(e)}",
            "data": {"alternatives": []}
        }


class SearchPoiRequest(BaseModel):
    """POI搜索请求"""
    keyword: str = Field(..., description="搜索关键词")
    city: str = Field(..., description="城市")


class ReoptimizeRequest(BaseModel):
    """路线重优化请求"""
    day_points: list = Field(..., description="当日途径点 [{name, lat, lng, type}, ...]")


@router.post("/search-poi", summary="搜索POI用于DIY添加")
async def search_poi_for_diy(request: SearchPoiRequest):
    """搜索景点/餐厅用于DIY手动添加"""
    try:
        if PLANNER_ENGINE == "langgraph":
            planner = get_langgraph_planner()
            results = await planner.search_poi_async(request.keyword, request.city)
        else:
            results = []
        return {"success": True, "data": {"results": results}}
    except Exception as e:
        return {"success": False, "message": str(e), "data": {"results": []}}


@router.post("/reoptimize", summary="智能优化当日路线")
async def reoptimize_route(request: ReoptimizeRequest):
    """对当日途径点重新排序，优化路线"""
    try:
        if PLANNER_ENGINE == "langgraph":
            planner = get_langgraph_planner()
            optimized = await planner.reoptimize_async(request.day_points)
        else:
            optimized = request.day_points
        return {"success": True, "data": {"optimized": optimized}}
    except Exception as e:
        return {"success": False, "message": str(e), "data": {"optimized": request.day_points}}

