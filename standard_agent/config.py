"""
配置加载模块 - 从 .env 文件读取 LLM 和应用配置。
"""

import os
from dataclasses import dataclass
from dotenv import load_dotenv

load_dotenv()


@dataclass(frozen=True)
class ModelProfile:
    """One independently configurable OpenAI-compatible model endpoint."""

    name: str
    api_key: str
    base_url: str
    model: str
    temperature: float

    def validate(self) -> bool:
        return bool(self.api_key and self.base_url and self.model)


class Settings:
    """应用配置单例，从环境变量读取所有配置项。"""

    # LLM 配置
    # 密钥只从 .env / 环境变量读取，禁止硬编码到代码
    OPENAI_API_KEY: str = os.getenv("OPENAI_API_KEY", "")
    OPENAI_BASE_URL: str = os.getenv("OPENAI_BASE_URL", "https://api.deepseek.com")
    MODEL_NAME: str = os.getenv("MODEL_NAME", "deepseek-v4-pro")
    TEMPERATURE: float = float(os.getenv("TEMPERATURE", "0.7"))
    MODEL_PROFILE: str = os.getenv("MODEL_PROFILE", "default")
    MODEL_PROFILES: tuple[str, ...] = tuple(
        name.strip() for name in os.getenv("MODEL_PROFILES", "").split(",") if name.strip()
    )

    # Observability. Full prompt logging is opt-in because AX trees can be large.
    TRACE_LLM_IO: bool = os.getenv("TRACE_LLM_IO", "0").lower() in {"1", "true", "yes"}
    TRACE_PROMPTS: bool = os.getenv("TRACE_PROMPTS", "0").lower() in {"1", "true", "yes"}
    TRACE_CONSOLE: bool = os.getenv("TRACE_CONSOLE", "0").lower() in {"1", "true", "yes"}

    # Agent 配置
    MAX_STEPS: int = int(os.getenv("MAX_STEPS", "30"))

    @classmethod
    def get_model_profiles(cls) -> dict[str, ModelProfile]:
        """Load named profiles from MODEL_<NAME>_* environment variables."""
        names = cls.MODEL_PROFILES or ((cls.MODEL_PROFILE,) if cls.MODEL_PROFILE != "default" else ())
        profiles = {}
        for name in names:
            key = name.upper().replace("-", "_")
            profiles[name] = ModelProfile(
                name=name,
                api_key=os.getenv(f"MODEL_{key}_API_KEY", ""),
                base_url=os.getenv(f"MODEL_{key}_BASE_URL", ""),
                model=os.getenv(f"MODEL_{key}_NAME", ""),
                temperature=float(os.getenv(f"MODEL_{key}_TEMPERATURE", str(cls.TEMPERATURE))),
            )
        return profiles

    @classmethod
    def get_model_profile(cls, name: str | None = None) -> ModelProfile:
        """Resolve a named profile, falling back to legacy single-model settings."""
        profile_name = name or cls.MODEL_PROFILE
        profiles = cls.get_model_profiles()
        if profile_name in profiles:
            return profiles[profile_name]
        if name is not None and name != "default":
            key = name.upper().replace("-", "_")
            return ModelProfile(
                name=name,
                api_key=os.getenv(f"MODEL_{key}_API_KEY", ""),
                base_url=os.getenv(f"MODEL_{key}_BASE_URL", ""),
                model=os.getenv(f"MODEL_{key}_NAME", ""),
                temperature=float(os.getenv(
                    f"MODEL_{key}_TEMPERATURE", str(cls.TEMPERATURE)
                )),
            )
        return ModelProfile(
            name="default",
            api_key=cls.OPENAI_API_KEY,
            base_url=cls.OPENAI_BASE_URL,
            model=cls.MODEL_NAME,
            temperature=cls.TEMPERATURE,
        )

    @classmethod
    def validate(cls, profile_name: str | None = None) -> bool:
        """验证实际选中的模型 profile，而不是只检查旧式配置。"""
        profile = cls.get_model_profile(profile_name)
        if not profile.api_key:
            print(f"⚠️  警告: model profile '{profile.name}' 的 API key 未设置")
            return False
        if not profile.base_url:
            print(f"⚠️  警告: model profile '{profile.name}' 的 base URL 未设置")
            return False
        if not profile.model:
            print(f"⚠️  警告: model profile '{profile.name}' 的模型名未设置")
            return False
        return True


settings = Settings()
