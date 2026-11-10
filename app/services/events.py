"""事件总线：Kafka 推送，不可用时写本地事件日志（保证通知链路不断）。

事件类型：
    report.generated / report.failed
    counterparty.saved / counterparty.synced
    document.parsed
"""
from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Any

from app.core.config import settings
from app.core.logging import get_logger

log = get_logger(__name__)


class EventBus:
    def __init__(self) -> None:
        self._producer = None
        self.backend = "local"
        self._log_path = Path(settings.data_dir) / "logs" / "events.jsonl"
        self._log_path.parent.mkdir(parents=True, exist_ok=True)
        if settings.kafka_enabled:
            try:
                from kafka import KafkaProducer

                self._producer = KafkaProducer(
                    bootstrap_servers=settings.kafka_bootstrap.split(","),
                    value_serializer=lambda v: json.dumps(v, ensure_ascii=False).encode("utf-8"),
                    request_timeout_ms=3000, api_version_auto_timeout_ms=2000,
                    retries=0,
                )
                self.backend = "kafka"
            except Exception as exc:  # noqa: BLE001 - Kafka 不可用属预期
                log.info("Kafka 不可用，事件落本地日志：%s", exc)

    def publish(self, event_type: str, payload: dict[str, Any]) -> None:
        event = {"event_type": event_type, "ts": datetime.now().isoformat(timespec="seconds"),
                 **payload}
        if self._producer is not None:
            try:
                self._producer.send(settings.kafka_topic, event)
                self._producer.flush(timeout=2)
                return
            except Exception as exc:  # noqa: BLE001
                log.warning("Kafka 发送失败，落本地：%s", exc)
        with open(self._log_path, "a", encoding="utf-8") as f:
            f.write(json.dumps(event, ensure_ascii=False, default=str) + "\n")

    def health(self) -> dict[str, Any]:
        return {"backend": self.backend, "degraded": self.backend != "kafka",
                "topic": settings.kafka_topic}


bus = EventBus()
