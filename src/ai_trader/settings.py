"""运行时配置。

高风险功能采用失效关闭（fail-closed）默认值：禁止交易，且仅允许纸面交易。
"""

from functools import cached_property

from pydantic import AliasChoices, Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """从环境变量读取的系统配置。"""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        populate_by_name=True,
    )

    trading_enabled: bool = False
    paper_trading_only: bool = True
    feishu_webhook: str = ""
    anthropic_base_url: str = "https://token-plan-cn.xiaomimimo.com/anthropic"
    anthropic_api_key: str = Field(
        default="",
        validation_alias=AliasChoices("ANTHROPIC_AUTH_TOKEN", "ANTHROPIC_API_KEY"),
    )
    anthropic_model: str = "mimo-v2-pro"
    runtime_mode: str = Field(default="legacy", validation_alias="AI_TRADER_RUNTIME_MODE")
    runtime_database_url: str = "sqlite:///data/agent_runtime.db"

    @cached_property
    def live_execution_allowed(self) -> bool:
        """仅当显式开启交易并关闭纸面模式时允许真实执行。"""
        return self.trading_enabled and not self.paper_trading_only


settings = Settings()
