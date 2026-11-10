"""全局配置中心。

所有外部依赖的地址/密钥都集中在这里，默认值一律为**占位字符串**，
保证"缺配置也能启动"，替换成真实值即可生效（无需改代码）。
"""
from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

# 项目根目录：app/core/config.py -> 上溯两级
BASE_DIR = Path(__file__).resolve().parents[2]


class Settings(BaseSettings):
    """应用配置（优先读环境变量 / .env，其次用下方默认值）。"""

    model_config = SettingsConfigDict(
        env_file=str(BASE_DIR / ".env"),
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
    )

    # ---------- 基础 ----------
    app_name: str = "AI Trading Assistant"
    app_version: str = "1.0.0"
    debug: bool = False

    # ---------- 目录 ----------
    base_dir: Path = BASE_DIR
    data_dir: Path = BASE_DIR / "data"
    kb_dir: Path = BASE_DIR / "data" / "knowledge_base"
    kb_jsonl: Path = BASE_DIR / "data" / "knowledge_base" / "knowledge_points.jsonl"
    chroma_dir: Path = BASE_DIR / "data" / "chroma"
    reports_dir: Path = BASE_DIR / "data" / "reports"
    uploads_dir: Path = BASE_DIR / "data" / "uploads"
    reference_dir: Path = BASE_DIR / "data" / "reference_reports"
    sqlite_path: Path = BASE_DIR / "data" / "app.db"
    skill_path: Path = BASE_DIR / "app" / "skills" / "counterparty_report_skill.md"

    # ---------- LLM（主）----------
    llm_provider: str = "zhipu"
    llm_api_key: str = "sk-placeholder-zhipu-key"          # 占位
    llm_base_url: str = "https://open.bigmodel.cn/api/paas/v4"
    llm_model: str = "glm-4.7-flash"
    llm_vision_model: str = "glm-4.6v-flash"
    llm_timeout: int = 60
    llm_max_retries: int = 2

    # ---------- LLM（备）----------
    llm_fallback_api_key: str = "sk-placeholder-dashscope-key"  # 占位
    llm_fallback_base_url: str = "https://dashscope.aliyuncs.com/compatible-mode/v1"
    llm_fallback_model: str = "qwen-plus"

    # ---------- Redis ----------
    redis_url: str = "redis://localhost:6379/0"            # 占位；连不上自动降级
    redis_enabled: bool = True
    doc_cache_ttl: int = 12 * 3600
    answer_cache_ttl: int = 3600

    # ---------- Oracle ----------
    oracle_dsn: str = "127.0.0.1:1521/XEPDB1"              # 占位
    oracle_user: str = "counterparty"
    oracle_password: str = "placeholder_password"
    oracle_enabled: bool = True

    # ---------- Kafka ----------
    kafka_bootstrap: str = "localhost:9092"                # 占位
    kafka_topic: str = "ai_trading_events"
    kafka_enabled: bool = True

    # ---------- MCP（同花顺 iFinD，详见 docs/financial_data_mcp_research.md）----------
    mcp_enabled: bool = True
    mcp_base_url: str = "https://api-mcp.51ifind.com:8643/ds-mcp-servers"
    mcp_auth_token: str = "ifind_mcp_placeholder_key"      # 占位
    mcp_timeout: int = 30
    mcp_server_enterprise: str = "kuaicha-enterprise-mcp"
    mcp_server_bond: str = "hexin-ifind-ds-bond-mcp"
    mcp_server_news: str = "hexin-ifind-ds-news-mcp"

    # ---------- RAG ----------
    chunk_size: int = 320
    chunk_overlap: int = 64
    embed_dim: int = 256
    retrieval_top_k: int = 6
    recall_size: int = 20
    rrf_k: int = 60
    min_cosine: float = 0.30
    # 关键词路只有在「语义也不算太弱」时才可单独放行，避免只靠高频词误召回
    min_bm25: float = 10.0
    weak_similarity: float = 0.22

    # ---------- 记忆 ----------
    short_window_turns: int = 6
    summary_trigger_turns: int = 12
    long_memory_ttl_days: int = 30
    long_memory_refresh_hours: int = 24

    # ---------- Agent ----------
    max_verify_retry: int = 2

    def ensure_dirs(self) -> None:
        """确保运行时目录存在（幂等）。"""
        for p in (
            self.data_dir, self.chroma_dir, self.reports_dir,
            self.uploads_dir, self.kb_dir, self.sqlite_path.parent,
        ):
            Path(p).mkdir(parents=True, exist_ok=True)


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """单例配置，避免重复解析 .env。"""
    s = Settings()
    s.ensure_dirs()
    return s


settings = get_settings()
