"""Expose actionable model failures without provider response bodies or secrets."""
from openai import APIConnectionError, APITimeoutError, AuthenticationError, RateLimitError


def model_service_error(exc: Exception) -> tuple[int, str] | None:
    seen = set()
    current = exc
    while current is not None and id(current) not in seen:
        seen.add(id(current))
        if isinstance(current, APITimeoutError):
            return 504, "模型服务响应超时，请稍后重试。"
        if isinstance(current, APIConnectionError):
            return 502, "无法连接模型服务，请检查后端网络及代理配置后重试。"
        if isinstance(current, AuthenticationError):
            return 502, "模型服务鉴权失败，请检查后端 LLM_API_KEY 配置。"
        if isinstance(current, RateLimitError):
            return 503, "模型服务额度不足或请求受限，请检查账户额度或稍后重试。"
        current = current.__cause__
    return None
