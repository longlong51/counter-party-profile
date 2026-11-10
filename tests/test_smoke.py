"""冒烟测试：覆盖健康检查、知识问答、会话、报告生成、录入中断。

运行：python -m pytest tests -q
说明：外部依赖（LLM/MCP/Oracle/Redis/Kafka）均为占位配置，测试验证的是**降级链路**。
"""
from __future__ import annotations

import json

import pytest
from fastapi.testclient import TestClient

from app.main import app

client = TestClient(app)


def test_health():
    r = client.get("/health")
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "ok"
    for key in ("llm", "chroma", "redis", "oracle", "mcp", "kafka"):
        assert key in body["components"]


def test_knowledge_stats():
    r = client.get("/api/knowledge/stats")
    assert r.status_code == 200
    assert r.json()["documents"] >= 100


def test_knowledge_ask():
    r = client.post("/api/knowledge/ask", json={"question": "什么是货银对付（DVP）？"})
    assert r.status_code == 200
    body = r.json()
    assert "answer" in body
    assert body["citations"] is not None


def test_knowledge_reject():
    r = client.post("/api/knowledge/ask", json={"question": "帮我预测一下明天上证指数的点位。"})
    assert r.status_code == 200
    assert r.json()["grounded"] is False


def test_session_and_chat():
    s = client.post("/api/chat/sessions", json={"title": "冒烟测试"}).json()
    sid = s["session_id"]
    r = client.post("/api/chat/messages", json={"session_id": sid, "content": "中央对手方是什么？"})
    assert r.status_code == 200
    assert r.json()["reply"]
    msgs = client.get(f"/api/chat/sessions/{sid}/messages").json()
    assert len(msgs["items"]) >= 2


def test_intake_interrupt_flow():
    s = client.post("/api/chat/sessions", json={"title": "录入测试"}).json()
    r = client.post("/api/counterparty/intake", json={
        "session_id": s["session_id"],
        "text": "中国银行，主体评级 AAA，注册资本 2943 亿元，机构类型 银行。",
    })
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "pending_confirm"
    assert body["thread_id"]

    c = client.post("/api/counterparty/intake/confirm", json={
        "thread_id": body["thread_id"], "action": "confirm",
        "patched_fields": {"full_name": "中国银行", "inst_type": "商业银行"},
    })
    assert c.status_code == 200
    assert c.json()["status"] in ("saved", "failed")


def test_report_generate():
    r = client.post("/api/reports/generate", json={"counterparty": "中国银行", "max_retry": 1})
    assert r.status_code == 200
    body = r.json()
    assert body["markdown"]
    assert isinstance(body["scores"], dict)
    assert body["trace"]


def test_meta():
    r = client.get("/api/meta")
    assert r.status_code == 200
    assert len(r.json()["modules"]) == 5
