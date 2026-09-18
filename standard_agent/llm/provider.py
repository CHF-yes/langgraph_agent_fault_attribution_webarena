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


def extract_served_metadata(response: object, requested_model: str = "") -> dict:
    """What the endpoint says it served, as opposed to the id we asked for.

    ``get_llm_metadata`` records the *requested* model id.  That is not the same
    thing, and the difference is not hypothetical for this project: a third-party
    GPT endpoint may have been reselling a weaker model under a flagship name,
    and DeepSeek's own ids alias across models (``deepseek-chat`` and
    ``deepseek-reasoner`` were retired 2026-07-24 and now error; ``deepseek-v4-pro``
    was slated for silent re-routing to V4.1 Flash during the 2026-09 flip-flop).
    An artifact that stores only the requested id cannot tell either apart, so a
    "model comparison" could silently be a comparison of a model against itself.

    Every field is best-effort.  Providers differ in what they echo back, and a
    missing field must never be the reason a trial dies, so anything unreadable
    degrades to ``""``/``None`` rather than raising.

    Args:
        response: a LangChain ``AIMessage`` (or anything with ``response_metadata``).
        requested_model: the id we sent, used to flag a possible substitution.

    Returns:
        A JSON-serialisable dict, safe to merge straight into a trace event.
    """
    meta = getattr(response, "response_metadata", None)
    if not isinstance(meta, dict):
        meta = {}
    usage = getattr(response, "usage_metadata", None)
    if not isinstance(usage, dict):
        usage = {}
    token_usage = meta.get("token_usage")
    if not isinstance(token_usage, dict):
        token_usage = {}

    # OpenAI-compatible endpoints use "model"; some gateways use "model_name".
    served = meta.get("model") or meta.get("model_name") or ""
    served = str(served).strip() if served else ""

    return {
        "served_model": served,
        "system_fingerprint": str(meta.get("system_fingerprint") or ""),
        # A *hint*, not a verdict: providers legitimately answer with a dated
        # variant of the requested id (e.g. "deepseek-v4-pro-0813" for
        # "deepseek-v4-pro"), which trips this flag harmlessly.  An empty served
        # id means "unknown", not "match" -- never read this as proof of honesty.
        "served_model_differs": bool(
            served and requested_model and served != requested_model
        ),
        "prompt_tokens": token_usage.get("prompt_tokens", usage.get("input_tokens")),
        "completion_tokens": token_usage.get(
            "completion_tokens", usage.get("output_tokens")
        ),
        "total_tokens": token_usage.get("total_tokens", usage.get("total_tokens")),
    }


def create_structured_llm() -> ChatOpenAI:
    """
    创建用于结构化输出的 LLM 实例。
    使用较低的温度以保证解析稳定性。
    """
    return create_llm(temperature=0.2)
