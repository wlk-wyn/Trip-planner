"""旅行规划API路由"""

import os
import re
import base64
import ipaddress
import socket
import requests
from urllib.parse import urljoin, urlsplit
from io import BytesIO
from bs4 import BeautifulSoup
from fastapi import APIRouter, HTTPException, Depends
from starlette.concurrency import run_in_threadpool
from pydantic import BaseModel, Field
from typing import Optional, Literal
from PIL import Image
from ...models.schemas import (
    Location,
    TripRequest,
    TripPlanResponse,
    TripChatRequest,
    TripChatResponse,
    TripChatData,
    TripIntakeRequest,
    TripIntakeResponse,
    ErrorResponse
)
from ...agents.trip_planner_agent import get_trip_planner_agent
from ...agents.langgraph_planner import get_langgraph_planner
from ...agents.travel_agent import get_travel_agent
from ...agents.trip_intake import interpret_trip_intake
from ...services.schedule_validator import validate_schedule
from ...services.model_errors import model_service_error
from ...services.request_gate import protect_request

router = APIRouter(prefix="/trip", tags=["旅行规划"], dependencies=[Depends(protect_request)])

# 默认运行可自主选择工具的 LangGraph Agent；旧引擎仍可显式切换。
PLANNER_ENGINE = os.getenv("PLANNER_ENGINE", "agent")


@router.post("/intake", response_model=TripIntakeResponse, summary="理解自然语言旅行需求")
async def intake_trip(request: TripIntakeRequest):
    """提取已知条件，缺少目的地或具体日期时只追问，不擅自生成日期。"""
    if request.history[-1].role != "user":
        raise HTTPException(status_code=422, detail="最后一条消息必须来自用户")
    try:
        data = await interpret_trip_intake(request.history)
        return TripIntakeResponse(success=True, message="需求理解完成", data=data)
    except Exception as exc:
        print(f"❌ 理解旅行需求失败: {exc}")
        status, detail = model_service_error(exc) or (502, "暂时无法理解旅行需求，请重试或使用详细设置")
        raise HTTPException(status_code=status, detail=detail) from exc


def _fetch_public_page(url: str, headers: dict) -> requests.Response:
    """只抓取公网 HTTP(S) 页面，并逐跳检查重定向地址。"""
    current = url.strip()
    for _ in range(5):
        parsed = urlsplit(current)
        if parsed.scheme not in ("http", "https") or not parsed.hostname or parsed.username or parsed.password:
            raise ValueError("仅支持公开的 HTTP 或 HTTPS 网页链接")
        try:
            addresses = socket.getaddrinfo(
                parsed.hostname, parsed.port or (443 if parsed.scheme == "https" else 80),
                type=socket.SOCK_STREAM
            )
            if not addresses or any(
                not ipaddress.ip_address(item[4][0]).is_global for item in addresses
            ):
                raise ValueError("不能提取本机或内网地址")
        except (socket.gaierror, OverflowError) as exc:
            raise ValueError("无法解析网页地址") from exc

        response = requests.get(current, headers=headers, timeout=15, allow_redirects=False)
        if not response.is_redirect:
            return response
        location = response.headers.get("Location")
        response.close()
        if not location:
            raise ValueError("网页重定向地址无效")
        current = urljoin(current, location)
    raise ValueError("网页重定向次数过多")


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

        if PLANNER_ENGINE == "agent":
            print("🔄 使用旅行规划 Agent...")
            trip_plan, _, _, _ = await get_travel_agent().run(request)
        elif PLANNER_ENGINE == "langgraph":
            print("🔄 使用 LangGraph 引擎...")
            planner = get_langgraph_planner()
            trip_plan = await planner.plan_trip_async(request)
        else:
            print("🔄 使用 HelloAgents 引擎...")
            agent = await run_in_threadpool(get_trip_planner_agent)
            trip_plan = await run_in_threadpool(agent.plan_trip, request)

        if PLANNER_ENGINE != "agent":
            await validate_schedule(trip_plan, request)
        from ...services.visit_advice import normalize_generated_advice
        normalize_generated_advice(trip_plan)
        from ...services.place_facts import fill_ticket_prices
        await fill_ticket_prices(trip_plan, get_langgraph_planner().fast_llm)
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
        status, detail = model_service_error(e) or (
            500, "规划未能完成，请稍后重试，或减少旅行天数和参考内容"
        )
        raise HTTPException(status_code=status, detail=detail) from e


@router.post("/chat", response_model=TripChatResponse, summary="与旅行规划 Agent 对话")
async def chat_with_trip_agent(request: TripChatRequest):
    """基于当前行程连续对话；Agent 自主判断回答还是修改。"""
    plan = request.plan
    # This legacy client payload has no repository-owned provider provenance.
    for day in plan.days:
        if day.schedule:
            for segment in day.schedule.segments:
                segment.from_endpoint=None
                segment.to_endpoint=None
        for place in [*day.attractions, *day.meals, *([day.hotel] if day.hotel else [])]:
            place.parent_poi_id = None
            place.parent_evidence = None
    original = request.original_request
    if not original or (original.city, original.start_date, original.end_date) != (
        plan.city, plan.start_date, plan.end_date
    ):
        original = TripRequest(
            city=plan.city, start_date=plan.start_date, end_date=plan.end_date,
            travel_days=len(plan.days),
            transportation=plan.days[0].transportation if plan.days else "公共交通",
            accommodation=plan.days[0].accommodation if plan.days else "酒店",
        )
    try:
        updated, reply, changed, actions = await get_travel_agent().run(
            original, plan, request.message,
            [turn.model_dump() for turn in request.history],
        )
        return TripChatResponse(
            success=True, message="智能体已回复",
            data=TripChatData(reply=reply, plan=updated, changed=changed, actions=actions),
        )
    except Exception as exc:
        print(f"❌ Agent 对话失败: {exc}")
        raise HTTPException(status_code=500, detail="智能体暂时无法完成这次调整，请稍后重试") from exc


@router.get(
    "/health",
    summary="健康检查",
    description="检查旅行规划服务是否正常"
)
async def health_check():
    """Only inspect configuration; a health poll must not initialize model/MCP clients."""
    from ...config import get_settings
    configured = bool(os.getenv('LLM_API_KEY') or os.getenv('OPENAI_API_KEY') or get_settings().openai_api_key)
    if not configured:
        raise HTTPException(503, '模型服务尚未配置')
    return {'status': 'configured', 'service': 'trip-planner', 'engine': PLANNER_ENGINE, 'upstreams': 'not_probed'}


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

        resp = await run_in_threadpool(_fetch_public_page, request.url, headers)
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

    except ValueError as e:
        return {
            "success": False,
            "message": str(e),
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
            text = await run_in_threadpool(pytesseract.image_to_string, image, lang="chi_sim+eng")
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


class PlaceRecommendationsRequest(BaseModel):
    city: str = Field(min_length=1, max_length=80)
    kind: Literal["attraction", "meal", "hotel"]
    preference: str = Field(default="", max_length=500)
    context: str = Field(default="", max_length=1500)
    anchor: Optional[Location] = None
    excluded_names: list[str] = Field(default_factory=list, max_length=50)


@router.post("/recommend-places", summary="询问助手推荐地点，不修改行程")
async def recommend_trip_places(request: PlaceRecommendationsRequest):
    from ...services.place_recommendations import recommend_places
    try:
        items = await recommend_places(request.city, request.kind, request.preference,
            request.context, request.anchor, request.excluded_names, get_langgraph_planner().fast_llm)
        return {"success": True, "data": {"alternatives": items}}
    except Exception as exc:
        status, detail = model_service_error(exc) or (502, "地点推荐暂时失败，请重试或直接搜索地点")
        raise HTTPException(status, detail) from exc


class AlternativesRequest(BaseModel):
    """备选项请求"""
    city: str = Field(..., description="城市")
    day_index: int = Field(..., description="第几天(0开始)")
    replace_type: Literal["attraction", "meal", "hotel"] = Field(..., description="替换类型")
    current_name: str = Field(..., description="当前景点/餐厅名称")
    current_category: Optional[str] = Field(default="", description="当前类别")
    current_location: Optional[dict] = Field(default=None, description="当前地点经纬度")
    context: Optional[str] = Field(default="", description="当天其他行程上下文")


@router.post(
    "/alternatives",
    summary="获取地点备选项",
    description="为DIY替换提供高德地图地点候选，并按原地点距离排序"
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
        if PLANNER_ENGINE in ("agent", "langgraph"):
            planner = get_langgraph_planner()
            alternatives = await planner.get_alternatives_async(
                city=request.city, day_index=request.day_index,
                replace_type=request.replace_type, current_name=request.current_name,
                current_category=request.current_category, context=request.context,
                current_location=request.current_location,
            )
        else:
            agent = await run_in_threadpool(get_trip_planner_agent)
            alternatives = await run_in_threadpool(agent.get_alternatives,
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
    kind: Literal["attraction", "meal", "hotel"] = "attraction"


class ReoptimizeRequest(BaseModel):
    """路线重优化请求"""
    day_points: list = Field(..., description="当日途径点 [{name, lat, lng, type}, ...]")


@router.post("/search-poi", summary="搜索POI用于DIY添加")
async def search_poi_for_diy(request: SearchPoiRequest):
    """搜索景点/餐厅用于DIY手动添加"""
    try:
        if PLANNER_ENGINE in ("agent", "langgraph"):
            planner = get_langgraph_planner()
            from ...services.amap_rest import search_pois, hotel_candidates
            from ...agents.langgraph_planner import _poi_as_alternative
            category = {"attraction": "", "meal": "050100", "hotel": "100100"}[request.kind]
            pois = await search_pois(request.city, request.keyword, 15, category=category)
            if request.kind == "hotel":
                pois = hotel_candidates(pois)
            results = [{**_poi_as_alternative(p), "poi_id": p["poi_id"]} for p in pois]
        else:
            results = []
        return {"success": True, "data": {"results": results}}
    except Exception as e:
        return {"success": False, "message": str(e), "data": {"results": []}}


@router.post("/reoptimize", summary="智能优化当日路线")
async def reoptimize_route(request: ReoptimizeRequest):
    """对当日途径点重新排序，优化路线"""
    try:
        if PLANNER_ENGINE in ("agent", "langgraph"):
            planner = get_langgraph_planner()
            optimized = await planner.reoptimize_async(request.day_points)
        else:
            optimized = request.day_points
        return {"success": True, "data": {"optimized": optimized}}
    except Exception as e:
        return {"success": False, "message": str(e), "data": {"optimized": request.day_points}}

