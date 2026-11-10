"""LLM 客户端：OpenAI 兼容协议 + 主备切换 + 重试 + 流式 + 视觉。

三家服务（智谱 / 百炼 / 硅基流动）都兼容 OpenAI 的 /chat/completions，
因此只需切换 base_url 与 model 名即可，无需为每家写适配层。
"""
from __future__ import annotations

import json
import time
from typing import Any, Generator

from app.core.config import settings
from app.core.logging import get_logger

log = get_logger(__name__)


class LLMError(RuntimeError):
    """LLM 调用失败（上层据此降级到模板/规则实现）。"""


class LLMClient:
    def __init__(self) -> None:
        self._client = None
        self._fallback = None
        self.degraded = False
        self.last_error: str | None = None
        self._init()

    def _init(self) -> None:
        # 占位密钥直接标记降级：避免每次请求都打网络并等待超时（无 key 时立即走本地降级链路）
        if "placeholder" in settings.llm_api_key.lower():
            self.degraded = True
            self.last_error = "LLM 使用占位密钥，未发起真实请求"
            log.warning(self.last_error)
            return
        try:
            from openai import OpenAI

            self._client = OpenAI(
                api_key=settings.llm_api_key,
                base_url=settings.llm_base_url,
                timeout=settings.llm_timeout,
                max_retries=0,  # 重试由本层控制，便于记录日志
            )
            self._fallback = OpenAI(
                api_key=settings.llm_fallback_api_key,
                base_url=settings.llm_fallback_base_url,
                timeout=settings.llm_timeout,
                max_retries=0,
            )
        except Exception as exc:  # noqa: BLE001
            self.degraded = True
            self.last_error = str(exc)
            log.warning("LLM 客户端初始化失败：%s", exc)

    # ---------- 内部：单次调用带重试 ----------
    def _call(self, messages: list[dict[str, Any]], model: str | None = None,
              temperature: float = 0.3, max_tokens: int = 1200,
              use_fallback: bool = False) -> str:
        if self.degraded or self._client is None:
            raise LLMError(self.last_error or "LLM 未初始化")

        client = self._fallback if use_fallback else self._client
        model_name = model or (
            settings.llm_fallback_model if use_fallback else settings.llm_model
        )

        last_exc: Exception | None = None
        for attempt in range(settings.llm_max_retries + 1):
            try:
                t0 = time.time()
                resp = client.chat.completions.create(
                    model=model_name, messages=messages,
                    temperature=temperature, max_tokens=max_tokens,
                )
                content = (resp.choices[0].message.content or "").strip()
                usage = getattr(resp, "usage", None)
                log.info("LLM ok model=%s tokens=%s/%s latency=%.0fms",
                         model_name,
                         getattr(usage, "prompt_tokens", "?"),
                         getattr(usage, "completion_tokens", "?"),
                         (time.time() - t0) * 1000)
                return content
            except Exception as exc:  # noqa: BLE001
                last_exc = exc
                log.warning("LLM 调用失败（第 %d 次，model=%s）：%s", attempt + 1, model_name, exc)
                time.sleep(min(2 ** attempt, 4))

        # 主链路失败后尝试备用 provider（避免递归）
        if not use_fallback:
            try:
                return self._call(messages, model=settings.llm_fallback_model,
                                  temperature=temperature, max_tokens=max_tokens,
                                  use_fallback=True)
            except Exception as exc:  # noqa: BLE001
                last_exc = exc

        raise LLMError(f"LLM 调用失败：{last_exc}")

    # ---------- 对外 ----------
    def chat(self, system: str, user: str, temperature: float = 0.3,
             max_tokens: int = 1200) -> str:
        return self._call(
            [{"role": "system", "content": system}, {"role": "user", "content": user}],
            temperature=temperature, max_tokens=max_tokens,
        )

    def chat_json(self, system: str, user: str, temperature: float = 0.1,
                  max_tokens: int = 1200) -> dict[str, Any]:
        """要求模型输出 JSON；解析失败时尝试截取首个 {...} 片段。"""
        system_json = system + "\n\n只输出 JSON，不要 Markdown 代码块，不要任何解释。"
        raw = self._call(
            [{"role": "system", "content": system_json}, {"role": "user", "content": user}],
            temperature=temperature, max_tokens=max_tokens,
        )
        return _parse_json(raw)

    def stream(self, system: str, user: str, temperature: float = 0.3,
               max_tokens: int = 1200) -> Generator[str, None, None]:
        """流式输出；失败时抛 LLMError，由 API 层决定是否退回非流式。"""
        if self.degraded or self._client is None:
            raise LLMError(self.last_error or "LLM 未初始化")
        stream = self._client.chat.completions.create(
            model=settings.llm_model,
            messages=[{"role": "system", "content": system},
                      {"role": "user", "content": user}],
            temperature=temperature, max_tokens=max_tokens, stream=True,
        )
        for chunk in stream:
            delta = chunk.choices[0].delta.content if chunk.choices else None
            if delta:
                yield delta

    def vision(self, prompt: str, image_b64: str, mime: str = "image/jpeg") -> str:
        """视觉模型（图片 OCR / 图表理解）。"""
        messages = [{
            "role": "user",
            "content": [
                {"type": "text", "text": prompt},
                {"type": "image_url", "image_url": {"url": f"data:{mime};base64,{image_b64}"}},
            ],
        }]
        return self._call(messages, model=settings.llm_vision_model,
                          temperature=0.1, max_tokens=1500)

    def health(self) -> dict[str, Any]:
        return {"provider": settings.llm_provider, "model": settings.llm_model,
                "base_url": settings.llm_base_url, "degraded": self.degraded,
                "error": self.last_error}


def _parse_json(raw: str) -> dict[str, Any]:
    """容错 JSON 解析：去掉 ```json 围栏，退化到首尾花括号切片。"""
    text = raw.strip()
    if text.startswith("```"):
        text = text.split("```")[1] if "```" in text[3:] else text[3:]
        text = text.lstrip("json").strip()
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        start, end = text.find("{"), text.rfind("}")
        if start >= 0 and end > start:
            try:
                return json.loads(text[start:end + 1])
            except json.JSONDecodeError:
                pass
    raise LLMError(f"模型未返回合法 JSON：{raw[:120]}")


llm = LLMClient()
