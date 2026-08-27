"""
LLM 提供商工厂 - 创建 ChatOpenAI 实例，支持所有 OpenAI-compatible API。
"""

from langchain_openai import ChatOpenAI

from standard_agent.config import ModelProfile, settings


def create_llm(temperature: float = None, profile_name: str = None) -> ChatOpenAI:
    """
    创建 LLM 实例。

    通过 config.settings 读取 .env 中的配置：
    - OPENAI_API_KEY: API 密钥
    - OPENAI_BASE_URL: 自定义端点（如 Ollama、DeepSeek、阿里百炼等）
    - MODEL_NAME: 模型名称
    - TEMPERATURE: 温度参数

    Args:
        temperature: 覆盖默认温度，不传则使用配置值

    Returns:
        ChatOpenAI 实例
    """
    profile = settings.get_model_profile(profile_name)
    if temperature is None:
        temperature = profile.temperature

    kwargs = {
        "model": profile.model,
        "temperature": temperature,
        "api_key": profile.api_key,
        "timeout": profile.request_timeout,
        "max_retries": profile.max_retries,
    }

    # 如果设置了自定义 base_url，使用它
    if profile.base_url:
        kwargs["base_url"] = profile.base_url

    return ChatOpenAI(**kwargs)


def get_llm_metadata(profile_name: str = None) -> dict:
    """Return non-secret model metadata for traces and experiment reports."""
    profile = settings.get_model_profile(profile_name)
    return {
        "model_profile": profile.name,
        "provider_base_url": profile.base_url,
        "model": profile.model,
        "temperature": profile.temperature,
        "request_timeout": profile.request_timeout,
        "max_retries": profile.max_retries,
    }


def create_structured_llm() -> ChatOpenAI:
    """
    创建用于结构化输出的 LLM 实例。
    使用较低的温度以保证解析稳定性。
    """
    return create_llm(temperature=0.2)
