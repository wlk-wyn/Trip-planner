"""配置管理模块"""

import os
from pathlib import Path
from typing import List
from pydantic_settings import BaseSettings
from pydantic import Field, SecretStr
from dotenv import load_dotenv

# 加载环境变量
# 首先尝试加载当前目录的.env
load_dotenv()

# 然后尝试加载HelloAgents的.env(如果存在)
helloagents_env = Path(__file__).parent.parent.parent.parent / "HelloAgents" / ".env"
if helloagents_env.exists():
    load_dotenv(helloagents_env, override=False)  # 不覆盖已有的环境变量


class Settings(BaseSettings):
    """应用配置"""

    # 应用基本配置
    app_name: str = "智能旅行助手"
    app_version: str = "1.0.0"
    debug: bool = False

    # 服务器配置
    host: str = "0.0.0.0"
    port: int = 8000

    # CORS配置 - 使用字符串,在代码中分割
    cors_origins: str = "http://localhost:5173,http://localhost:3000,http://127.0.0.1:5173,http://127.0.0.1:3000"

    # 高德地图API配置
    amap_api_key: str = ""
    amap_route_qps: float = 1.0
    # Place search/detail share a separate, usually higher, per-account limit.
    amap_poi_qps: float = 3.0
    # Weather, district and address geocoding share a conservative metadata lane.
    amap_metadata_qps: float = 1.0

    # Optional, server-side only. Never included in health/config responses.
    meituan_travel_token: SecretStr = Field(default=SecretStr(''), repr=False)

    # Unsplash API配置
    unsplash_access_key: str = ""
    unsplash_secret_key: str = ""

    # LLM配置 (从环境变量读取,由HelloAgents管理)
    openai_api_key: str = ""
    openai_base_url: str = "https://api.openai.com/v1"
    openai_model: str = "gpt-4"

    # 日志配置
    log_level: str = "INFO"
    planning_max_running: int = Field(default=2, ge=1, le=20)
    planning_max_pending: int = Field(default=12, ge=1, le=1000)
    planning_max_pending_per_owner: int = Field(default=2, ge=1, le=50)
    planning_queue_timeout_seconds: float = Field(default=180, ge=10, le=1800)
    planning_lease_seconds: float = Field(default=60, ge=10, le=600)
    planning_run_timeout_seconds: float = Field(default=300, ge=30, le=1800)
    planning_max_attempts: int = Field(default=3, ge=1, le=10)
    # Interactive HTTP work; durable planning jobs have their own queue limits.
    http_interactive_running: int = Field(default=2, ge=1, le=50)
    http_interactive_per_peer_running: int = Field(default=2, ge=1, le=20)
    http_interactive_per_peer_minute: int = Field(default=12, ge=1, le=1000)
    http_interactive_per_minute: int = Field(default=60, ge=1, le=10000)
    http_lookup_running: int = Field(default=12, ge=1, le=100)
    http_lookup_per_peer_running: int = Field(default=6, ge=1, le=50)
    http_lookup_per_peer_minute: int = Field(default=120, ge=1, le=5000)
    http_lookup_per_minute: int = Field(default=600, ge=1, le=20000)
    http_request_lease_seconds: float = Field(default=60, ge=10, le=600)

    class Config:
        env_file = ".env"
        case_sensitive = False
        extra = "ignore"  # 忽略额外的环境变量

    def get_cors_origins_list(self) -> List[str]:
        """获取CORS origins列表"""
        return [origin.strip() for origin in self.cors_origins.split(',')]


# 创建全局配置实例
settings = Settings()


def get_settings() -> Settings:
    """获取配置实例"""
    return settings


# 验证必要的配置
def validate_config():
    """验证配置是否完整"""
    errors = []
    warnings = []

    if not settings.amap_api_key:
        errors.append("AMAP_API_KEY未配置")

    # HelloAgentsLLM会自动从LLM_API_KEY读取,不强制要求OPENAI_API_KEY
    llm_api_key = os.getenv("LLM_API_KEY") or os.getenv("OPENAI_API_KEY")
    if not llm_api_key:
        warnings.append("LLM_API_KEY或OPENAI_API_KEY未配置,LLM功能可能无法使用")

    if errors:
        error_msg = "配置错误:\n" + "\n".join(f"  - {e}" for e in errors)
        raise ValueError(error_msg)

    if warnings:
        print("\n⚠️  配置警告:")
        for w in warnings:
            print(f"  - {w}")

    return True


# 打印配置信息(用于调试)
def print_config():
    """打印当前配置(隐藏敏感信息)"""
    print(f"应用名称: {settings.app_name}")
    print(f"版本: {settings.app_version}")
    print(f"服务器: {settings.host}:{settings.port}")
    print(f"高德地图API Key: {'已配置' if settings.amap_api_key else '未配置'}")

    # 检查LLM配置
    llm_api_key = os.getenv("LLM_API_KEY") or os.getenv("OPENAI_API_KEY")
    llm_base_url = os.getenv("LLM_BASE_URL") or settings.openai_base_url
    llm_model = os.getenv("LLM_MODEL_ID") or settings.openai_model

    print(f"LLM API Key: {'已配置' if llm_api_key else '未配置'}")
    print(f"LLM Base URL: {llm_base_url}")
    print(f"LLM Model: {llm_model}")
    print(f"日志级别: {settings.log_level}")

