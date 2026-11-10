"""同花顺 iFinD MCP 客户端（JSON-RPC over HTTP）。

协议要点（详见 docs/financial_data_mcp_research.md）：
    initialize（protocolVersion=2025-03-26，响应头取 Mcp-Session-Id）
    → notifications/initialized → tools/list → tools/call
    鉴权：请求头 Authorization: <token>（裸密钥，不带 Bearer）

配置缺失策略：
    token 为占位符 / 网络失败 / 无权益时，返回**明确标注的示例数据**（data_source="mock"），
    保证 Agent1 之后的三段链路仍可跑通，且报告里会显示"示例数据，需接入真实 MCP"。
"""
from __future__ import annotations

from typing import Any

import httpx

from app.core.config import settings
from app.core.logging import get_logger

log = get_logger(__name__)


class MCPError(RuntimeError):
    pass


class MCPClient:
    """一个实例管理多个 server 的会话（session id 按 server 隔离）。"""

    def __init__(self) -> None:
        self.base = settings.mcp_base_url.rstrip("/")
        self.token = settings.mcp_auth_token
        self._sessions: dict[str, str] = {}
        self._req_id: dict[str, int] = {}
        self.last_error: str | None = None

    # ---------- 判断是否具备真实调用条件 ----------
    @property
    def configured(self) -> bool:
        return (
            settings.mcp_enabled
            and bool(self.token)
            and "placeholder" not in self.token.lower()
        )

    def _url(self, server: str) -> str:
        return f"{self.base}/{server}"

    def _headers(self, server: str) -> dict[str, str]:
        h = {
            "Content-Type": "application/json",
            "Accept": "application/json, text/event-stream",
            "Authorization": self.token,
            "User-Agent": "ai-trading-assistant",
        }
        if server in self._sessions:
            h["Mcp-Session-Id"] = self._sessions[server]
        return h

    def _next_id(self, server: str) -> int:
        self._req_id[server] = self._req_id.get(server, 0) + 1
        return self._req_id[server]

    def _post(self, server: str, payload: dict[str, Any]) -> tuple[httpx.Response, Any]:
        with httpx.Client(timeout=settings.mcp_timeout) as client:
            resp = client.post(self._url(server), json=payload, headers=self._headers(server))
        try:
            data = resp.json()
        except Exception:  # noqa: BLE001 - SSE 流可能不是 JSON
            data = {"raw": resp.text[:2000]}
        return resp, data

    # ---------- 握手 ----------
    def initialize(self, server: str) -> None:
        if server in self._sessions:
            return
        payload = {
            "jsonrpc": "2.0", "id": self._next_id(server), "method": "initialize",
            "params": {
                "protocolVersion": "2025-03-26",
                "capabilities": {},
                "clientInfo": {"name": "ai-trading-assistant", "version": "1.0.0"},
            },
        }
        resp, data = self._post(server, payload)
        sid = resp.headers.get("Mcp-Session-Id")
        if not sid:
            raise MCPError(f"initialize 未返回 Mcp-Session-Id：{str(data)[:200]}")
        self._sessions[server] = sid

        with httpx.Client(timeout=10) as client:
            client.post(self._url(server),
                        json={"jsonrpc": "2.0", "method": "notifications/initialized"},
                        headers=self._headers(server))

    def list_tools(self, server: str) -> list[str]:
        """列出工具（不消耗计费次数，可用于探测权益）。"""
        self.initialize(server)
        resp, data = self._post(server, {
            "jsonrpc": "2.0", "id": self._next_id(server),
            "method": "tools/list", "params": {},
        })
        tools = (((data or {}).get("result") or {}).get("tools")) or []
        return [t.get("name") for t in tools if isinstance(t, dict) and t.get("name")]

    def call_tool(self, server: str, tool: str, arguments: dict[str, Any]) -> Any:
        self.initialize(server)
        resp, data = self._post(server, {
            "jsonrpc": "2.0", "id": self._next_id(server),
            "method": "tools/call",
            "params": {"name": tool, "arguments": arguments},
        })
        if isinstance(data, dict) and data.get("error"):
            raise MCPError(f"{tool} 调用失败：{data['error']}")
        return (data.get("result") if isinstance(data, dict) else None) or data

    # ---------- 业务：对手方取数 ----------
    def fetch_counterparty(self, name: str) -> dict[str, Any]:
        """按对手方名称取数：身份 / 评级 / 财务 / 风险事件 / 债券 / 舆情。"""
        if not self.configured:
            self.last_error = "MCP 未配置真实 token（占位符）"
            log.info("MCP 使用示例数据：%s", name)
            return self._mock(name, reason=self.last_error)

        try:
            tools_used: list[str] = []
            # 1) 名称归一：简称/品牌名 → orgid / creditcode
            assoc = self.call_tool(
                settings.mcp_server_enterprise, "kuaicha_tool_call",
                {"tool_id": "basic_get_enterprise_associate",
                 "params_to_tool": f'{{"query": "{name}"}}'},
            )
            tools_used.append("enterprise:basic_get_enterprise_associate")

            # 2) 工商风险（discover → call 两步）
            risk_raw: list[dict[str, Any]] = []
            for ability in ("被执行人查询", "失信名单", "经营异常", "行政处罚", "破产重整"):
                found = self.call_tool(settings.mcp_server_enterprise,
                                       "kuaicha_tool_discover", {"query": ability})
                tool_id = _first_tool_id(found)
                if not tool_id:
                    continue
                res = self.call_tool(settings.mcp_server_enterprise, "kuaicha_tool_call",
                                     {"tool_id": tool_id,
                                      "params_to_tool": f'{{"query": "{name}"}}'})
                tools_used.append(f"enterprise:{tool_id}")
                risk_raw.append({"ability": ability, "result": res})

            # 3) 债券与评级
            bond = self.call_tool(settings.mcp_server_bond, "bond_basic_info",
                                  {"query": f"{name}的存续债券与主体评级"})
            tools_used.append("bond:bond_basic_info")
            fin = self.call_tool(settings.mcp_server_bond, "bond_financial_data",
                                 {"query": f"{name}最新的资产负债率和ROE"})
            tools_used.append("bond:bond_financial_data")

            # 4) 公告舆情
            news = self.call_tool(settings.mcp_server_news, "search_news",
                                  {"query": f"{name} 评级 违约 风险 公告"})
            tools_used.append("news:search_news")

            return {
                "counterparty": name,
                "identity": _extract_identity(assoc, name),
                "rating": _extract_rating(bond),
                "finance": _extract_finance(fin),
                "bonds": _as_list(bond)[:5],
                "risk_events": _extract_risks(risk_raw),
                "news": _as_list(news)[:5],
                "tools": tools_used,
                "data_source": "mcp",
                "errors": [],
            }
        except Exception as exc:  # noqa: BLE001
            self.last_error = str(exc)
            log.warning("MCP 取数失败，回落示例数据：%s", exc)
            mock = self._mock(name, reason=str(exc))
            mock["tools"] = []
            return mock

    def _mock(self, name: str, reason: str) -> dict[str, Any]:
        """示例数据：字段结构真实、数值标注为示例，用于离线演示与联调。"""
        return {
            "counterparty": name,
            "identity": {
                "org_id": "MOCK_ORG_ID",
                "credit_code": "MOCK_CREDIT_CODE",
                "full_name": name,
                "short_name": name,
                "inst_type": "商业银行",
                "reg_capital": "示例值（待 MCP 同步）",
                "found_date": "示例值",
                "legal_person": "示例值",
                "listed_flag": "Y",
            },
            "rating": {
                "rating_date": "示例日期",
                "agency": "示例评级机构",
                "issuer_rating": "AAA（示例）",
                "issuer_outlook": "稳定（示例）",
                "bond_rating": "AAA（示例）",
                "rating_history": "示例：近三年评级未下调",
            },
            "finance": [{
                "period": "示例期间",
                "total_asset": "示例值",
                "total_liab": "示例值",
                "revenue": "示例值",
                "net_profit": "示例值",
                "debt_ratio": "示例值",
                "roe": "示例值",
            }],
            "bonds": [{"bond_code": "示例代码", "bond_name": f"{name}示例存单",
                       "issue_amount": "示例值", "maturity": "示例日期"}],
            "risk_events": [{
                "event_id": "ev_mock_1",
                "event_type": "示例风险事件",
                "event_date": "示例日期",
                "event_amount": "示例值",
                "event_desc": "示例数据：接入真实 MCP 后此处为工商/司法风险明细",
                "source": "MOCK",
            }],
            "news": [{"title": f"{name}示例公告", "publish_time": "示例日期",
                      "summary": "示例数据：接入真实 MCP 后此处为公告舆情摘要"}],
            "data_source": "mock",
            "reason": reason,
            "errors": [reason],
        }

    def sync(self, cp_id: str, name: str) -> dict[str, Any]:
        """把取数结果落到关系库（供 /api/counterparty/sync 调用）。

        降级时 `_mock` 会给「示例值」，`db.upsert_counterparty` 内部会保留已有真实字段，
        因此这里可以整体提交。
        """
        from app.services.db import db

        data = self.fetch_counterparty(name)
        db.upsert_counterparty({**data["identity"], "cp_id": cp_id,
                                "risk_level": _risk_level(data)})
        db.save_rating(cp_id, data["rating"])
        for f in data.get("finance") or []:
            db.save_finance(cp_id, f)
        for ev in data.get("risk_events") or []:
            db.save_risk_event(cp_id, ev)
        return {
            "synced": True,
            "tools": data.get("tools", []),
            "records": 1 + 1 + len(data.get("finance") or []) + len(data.get("risk_events") or []),
            "data_source": data.get("data_source"),
        }

    def health(self) -> dict[str, Any]:
        return {"configured": self.configured, "token_placeholder": not self.configured,
                "base_url": self.base, "last_error": self.last_error}


# ---------------- 辅助函数 ----------------
def _first_tool_id(found: Any) -> str | None:
    """从 discover 返回结构中取第一个 tool_id（结构随版本可能变化，做多形态兼容）。"""
    if isinstance(found, dict):
        for key in ("tool_id", "toolId", "id", "name"):
            if isinstance(found.get(key), str) and found[key]:
                return found[key]
        for v in found.values():
            got = _first_tool_id(v)
            if got:
                return got
    elif isinstance(found, list):
        for item in found:
            got = _first_tool_id(item)
            if got:
                return got
    return None


def _as_list(x: Any) -> list[dict[str, Any]]:
    if isinstance(x, list):
        return [i for i in x if isinstance(i, dict)]
    if isinstance(x, dict):
        for key in ("data", "items", "rows", "result"):
            if isinstance(x.get(key), list):
                return [i for i in x[key] if isinstance(i, dict)]
    return []


def _extract_identity(assoc: Any, name: str) -> dict[str, Any]:
    rows = _as_list(assoc)
    first = rows[0] if rows else {}
    return {
        "org_id": str(first.get("orgid") or first.get("org_id") or ""),
        "credit_code": str(first.get("creditcode") or first.get("credit_code") or ""),
        "full_name": str(first.get("name") or first.get("fullname") or name),
        "short_name": name,
        "inst_type": str(first.get("industry") or first.get("inst_type") or ""),
        "reg_capital": str(first.get("regcapital") or first.get("reg_capital") or ""),
        "found_date": str(first.get("estiblishTime") or first.get("found_date") or ""),
        "legal_person": str(first.get("legalPersonName") or first.get("legal_person") or ""),
        "listed_flag": "Y" if first.get("listed") else "",
    }


def _extract_rating(bond: Any) -> dict[str, Any]:
    rows = _as_list(bond)
    first = rows[0] if rows else {}
    return {
        "rating_date": str(first.get("评级日期") or first.get("rating_date") or ""),
        "agency": str(first.get("评级机构") or first.get("agency") or ""),
        "issuer_rating": str(first.get("主体评级") or first.get("issuer_rating") or ""),
        "issuer_outlook": str(first.get("评级展望") or first.get("issuer_outlook") or ""),
        "bond_rating": str(first.get("债项评级") or first.get("bond_rating") or ""),
        "rating_history": str(first.get("评级历史") or ""),
    }


def _extract_finance(fin: Any) -> list[dict[str, Any]]:
    rows = _as_list(fin)
    out = []
    for r in rows[:3]:
        out.append({
            "period": str(r.get("报告期") or r.get("period") or ""),
            "total_asset": str(r.get("总资产") or ""),
            "total_liab": str(r.get("总负债") or ""),
            "revenue": str(r.get("营业收入") or ""),
            "net_profit": str(r.get("净利润") or ""),
            "debt_ratio": str(r.get("资产负债率") or ""),
            "roe": str(r.get("ROE") or r.get("净资产收益率") or ""),
        })
    return out


def _extract_risks(risk_raw: list[dict[str, Any]]) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for item in risk_raw:
        for row in _as_list(item.get("result")):
            out.append({
                "event_id": str(row.get("id") or ""),
                "event_type": item["ability"],
                "event_date": str(row.get("date") or row.get("立案日期") or ""),
                "event_amount": str(row.get("amount") or row.get("执行标的") or ""),
                "event_desc": str(row.get("title") or row.get("案号") or item["ability"])[:500],
                "source": "MCP",
            })
    return out


def _risk_level(data: dict[str, Any]) -> str:
    """按风险事件数量与评级给出粗粒度风险等级（规则法，非模型臆断）。"""
    n = len(data.get("risk_events") or [])
    rating = str((data.get("rating") or {}).get("issuer_rating") or "")
    if n >= 5 or rating.startswith(("BB", "B", "CCC")):
        return "高"
    if n >= 2:
        return "中"
    return "低"


mcp = MCPClient()
