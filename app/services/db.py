"""关系数据库访问层：优先 Oracle，失败自动降级为本地 SQLite。

两张后端使用**同一套 DDL 与同一套命名绑定参数（:name）**，
因此业务代码不需要关心底层是 Oracle 还是 SQLite。
"""
from __future__ import annotations

import sqlite3
import threading
import uuid
from contextlib import contextmanager
from datetime import datetime, timedelta
from typing import Any, Iterator

from app.core.config import settings
from app.core.logging import get_logger

log = get_logger(__name__)


def is_placeholder(value: Any) -> bool:
    """判断一个取值是否是「占位/示例」值。

    MCP 未配置时 `_mock` 会返回 `示例值`、`MOCK_ORG_ID` 这类占位字符串，
    写库或生成报告时必须识别出来，避免覆盖用户确认过的真实数据。
    """
    text = str(value or "").strip()
    if not text:
        return True
    upper = text.upper()
    return ("示例" in text) or upper.startswith("MOCK_") or ("待接入" in text)

# 表结构：字段名同时是 MCP 取数字段落地点（见 docs/financial_data_mcp_research.md 第 4 节）
DDL: list[str] = [
    """CREATE TABLE CP_COUNTERPARTY (
        CP_ID VARCHAR2(40) PRIMARY KEY,
        ORG_ID VARCHAR2(80),
        CREDIT_CODE VARCHAR2(24),
        FULL_NAME VARCHAR2(200),
        SHORT_NAME VARCHAR2(120),
        INST_TYPE VARCHAR2(40),
        REG_CAPITAL VARCHAR2(40),
        FOUND_DATE VARCHAR2(20),
        LEGAL_PERSON VARCHAR2(80),
        LISTED_FLAG VARCHAR2(2),
        RISK_LEVEL VARCHAR2(20),
        REMARK CLOB,
        UPDATE_TIME VARCHAR2(20)
    )""",
    """CREATE TABLE CP_RATING (
        CP_ID VARCHAR2(40),
        RATING_DATE VARCHAR2(20),
        AGENCY VARCHAR2(80),
        ISSUER_RATING VARCHAR2(12),
        ISSUER_OUTLOOK VARCHAR2(20),
        BOND_RATING VARCHAR2(12),
        RATING_HISTORY CLOB
    )""",
    """CREATE TABLE CP_FINANCE (
        CP_ID VARCHAR2(40),
        PERIOD VARCHAR2(12),
        TOTAL_ASSET VARCHAR2(40),
        TOTAL_LIAB VARCHAR2(40),
        REVENUE VARCHAR2(40),
        NET_PROFIT VARCHAR2(40),
        DEBT_RATIO VARCHAR2(20),
        ROE VARCHAR2(20)
    )""",
    """CREATE TABLE CP_RISK_EVENT (
        CP_ID VARCHAR2(40),
        EVENT_ID VARCHAR2(60),
        EVENT_TYPE VARCHAR2(40),
        EVENT_DATE VARCHAR2(20),
        EVENT_AMOUNT VARCHAR2(40),
        EVENT_DESC CLOB,
        SOURCE VARCHAR2(80)
    )""",
    """CREATE TABLE CP_COMMENT (
        COMMENT_ID VARCHAR2(40) PRIMARY KEY,
        CP_ID VARCHAR2(40),
        SESSION_ID VARCHAR2(60),
        CONTENT CLOB,
        STYLE VARCHAR2(80),
        ROLE VARCHAR2(80),
        CREATED_AT VARCHAR2(20)
    )""",
    """CREATE TABLE CHAT_SESSION (
        SESSION_ID VARCHAR2(60) PRIMARY KEY,
        TITLE VARCHAR2(200),
        USER_ID VARCHAR2(60),
        SUMMARY CLOB,
        UPDATED_AT VARCHAR2(20)
    )""",
    """CREATE TABLE CHAT_MESSAGE (
        MSG_ID VARCHAR2(40) PRIMARY KEY,
        SESSION_ID VARCHAR2(60),
        ROLE VARCHAR2(16),
        CONTENT CLOB,
        META CLOB,
        CREATED_AT VARCHAR2(20)
    )""",
    """CREATE TABLE LT_MEMORY (
        MEM_ID VARCHAR2(40) PRIMARY KEY,
        SESSION_ID VARCHAR2(60),
        CP_ID VARCHAR2(40),
        MEM_TYPE VARCHAR2(40),
        CONTENT CLOB,
        EXPIRE_AT VARCHAR2(20),
        VERSION VARCHAR2(10),
        LAST_REFRESHED VARCHAR2(20)
    )""",
    """CREATE TABLE REPORT_RECORD (
        REPORT_ID VARCHAR2(40) PRIMARY KEY,
        CP_ID VARCHAR2(40),
        COUNTERPARTY VARCHAR2(200),
        STATUS VARCHAR2(20),
        PDF_PATH VARCHAR2(400),
        MARKDOWN CLOB,
        ISSUES CLOB,
        SCORES CLOB,
        CREATED_AT VARCHAR2(20)
    )""",
]


def now_str() -> str:
    """统一时间字符串（本项目所有时间字段均为字符串，便于跨库移植）。"""
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def new_id(prefix: str = "") -> str:
    return f"{prefix}{uuid.uuid4().hex[:16]}"


class Database:
    """轻量数据访问对象，线程安全（SQLite 侧加锁）。"""

    def __init__(self) -> None:
        self.backend = "sqlite"
        self._lock = threading.RLock()
        self._oracle_error: str | None = None
        self._init_backend()
        self.init_schema()

    # ---------- 连接管理 ----------
    def _init_backend(self) -> None:
        if not settings.oracle_enabled:
            return
        try:
            import oracledb  # 延迟导入

            # 仅做连通性探测；失败即降级，不影响启动
            with oracledb.connect(
                user=settings.oracle_user,
                password=settings.oracle_password,
                dsn=settings.oracle_dsn,
            ):
                pass
            self.backend = "oracle"
            log.info("已连接 Oracle：%s", settings.oracle_dsn)
        except Exception as exc:  # noqa: BLE001
            self._oracle_error = str(exc)
            log.warning("Oracle 不可用，降级 SQLite：%s", exc)

    @contextmanager
    def _cursor(self) -> Iterator[Any]:
        """产出游标；Oracle 用命名绑定，SQLite 同样支持 :name。"""
        if self.backend == "oracle":
            import oracledb

            conn = oracledb.connect(
                user=settings.oracle_user,
                password=settings.oracle_password,
                dsn=settings.oracle_dsn,
            )
            try:
                cur = conn.cursor()
                yield cur
                conn.commit()
            finally:
                conn.close()
        else:
            with self._lock:
                conn = sqlite3.connect(str(settings.sqlite_path), check_same_thread=False)
                try:
                    cur = conn.cursor()
                    yield cur
                    conn.commit()
                finally:
                    conn.close()

    # ---------- 建表 ----------
    def init_schema(self) -> None:
        for ddl in DDL:
            try:
                with self._cursor() as cur:
                    cur.execute(ddl)
            except Exception as exc:  # noqa: BLE001 - 表已存在属正常
                if "already exists" not in str(exc).lower() and "ora-00955" not in str(exc).lower():
                    log.debug("建表跳过/失败：%s", exc)

    # ---------- 基础读写 ----------
    def execute(self, sql: str, params: dict[str, Any] | None = None) -> int:
        with self._cursor() as cur:
            cur.execute(sql, params or {})
            return cur.rowcount

    def query(self, sql: str, params: dict[str, Any] | None = None) -> list[dict[str, Any]]:
        with self._cursor() as cur:
            cur.execute(sql, params or {})
            cols = [d[0].lower() for d in cur.description] if cur.description else []
            rows = cur.fetchall() or []
        return [dict(zip(cols, r)) for r in rows]

    # ---------- 会话 ----------
    def create_session(self, title: str = "新会话", user_id: str = "anonymous") -> dict[str, Any]:
        sid = new_id("s_")
        self.execute(
            "INSERT INTO CHAT_SESSION (SESSION_ID, TITLE, USER_ID, SUMMARY, UPDATED_AT) "
            "VALUES (:sid, :title, :uid, :summary, :ts)",
            {"sid": sid, "title": title[:200], "uid": user_id, "summary": "", "ts": now_str()},
        )
        return {"session_id": sid, "title": title, "created_at": now_str()}

    def list_sessions(self, limit: int = 20) -> list[dict[str, Any]]:
        rows = self.query(
            "SELECT s.SESSION_ID, s.TITLE, s.UPDATED_AT, "
            "(SELECT COUNT(1) FROM CHAT_MESSAGE m WHERE m.SESSION_ID = s.SESSION_ID) AS CNT "
            "FROM CHAT_SESSION s ORDER BY s.UPDATED_AT DESC",
        )
        out = [
            {
                "session_id": r["session_id"], "title": r["title"],
                "updated_at": r["updated_at"], "message_count": int(r.get("cnt") or 0),
            }
            for r in rows
        ]
        return out[:limit]

    def get_session(self, session_id: str) -> dict[str, Any] | None:
        rows = self.query(
            "SELECT SESSION_ID, TITLE, SUMMARY, UPDATED_AT FROM CHAT_SESSION WHERE SESSION_ID = :sid",
            {"sid": session_id},
        )
        return rows[0] if rows else None

    def touch_session(self, session_id: str, title: str | None = None) -> None:
        if title:
            self.execute(
                "UPDATE CHAT_SESSION SET UPDATED_AT = :ts, TITLE = :t WHERE SESSION_ID = :sid",
                {"ts": now_str(), "t": title[:200], "sid": session_id},
            )
        else:
            self.execute(
                "UPDATE CHAT_SESSION SET UPDATED_AT = :ts WHERE SESSION_ID = :sid",
                {"ts": now_str(), "sid": session_id},
            )

    def set_session_summary(self, session_id: str, summary: str) -> None:
        self.execute(
            "UPDATE CHAT_SESSION SET SUMMARY = :s, UPDATED_AT = :ts WHERE SESSION_ID = :sid",
            {"s": summary, "ts": now_str(), "sid": session_id},
        )

    def delete_session(self, session_id: str) -> None:
        self.execute("DELETE FROM CHAT_MESSAGE WHERE SESSION_ID = :sid", {"sid": session_id})
        self.execute("DELETE FROM CHAT_SESSION WHERE SESSION_ID = :sid", {"sid": session_id})

    # ---------- 消息 ----------
    def add_message(self, session_id: str, role: str, content: str, meta: str = "") -> dict[str, Any]:
        mid = new_id("m_")
        self.execute(
            "INSERT INTO CHAT_MESSAGE (MSG_ID, SESSION_ID, ROLE, CONTENT, META, CREATED_AT) "
            "VALUES (:mid, :sid, :role, :content, :meta, :ts)",
            {"mid": mid, "sid": session_id, "role": role, "content": content,
             "meta": meta, "ts": now_str()},
        )
        self.touch_session(session_id)
        return {"msg_id": mid, "role": role, "content": content, "created_at": now_str()}

    def list_messages(self, session_id: str, limit: int = 100) -> list[dict[str, Any]]:
        rows = self.query(
            "SELECT MSG_ID, ROLE, CONTENT, META, CREATED_AT FROM CHAT_MESSAGE "
            "WHERE SESSION_ID = :sid ORDER BY CREATED_AT ASC",
            {"sid": session_id},
        )
        return rows[-limit:]

    # ---------- 对手方 ----------
    def upsert_counterparty(self, data: dict[str, Any]) -> dict[str, Any]:
        cp_id = data.get("cp_id") or new_id("cp_")
        payload = {
            "cp_id": cp_id,
            "org_id": str(data.get("org_id") or ""),
            "credit_code": str(data.get("credit_code") or ""),
            "full_name": str(data.get("full_name") or ""),
            "short_name": str(data.get("short_name") or ""),
            "inst_type": str(data.get("inst_type") or ""),
            "reg_capital": str(data.get("reg_capital") or ""),
            "found_date": str(data.get("found_date") or ""),
            "legal_person": str(data.get("legal_person") or ""),
            "listed_flag": str(data.get("listed_flag") or ""),
            "risk_level": str(data.get("risk_level") or "待评估"),
            "remark": str(data.get("remark") or ""),
            "ts": now_str(),
        }
        exists = self.query(
            "SELECT CP_ID FROM CP_COUNTERPARTY WHERE FULL_NAME = :n", {"n": payload["full_name"]}
        )
        if exists and not data.get("cp_id"):
            payload["cp_id"] = exists[0]["cp_id"]
            cp_id = payload["cp_id"]

        # 已存在的记录：空值或「示例值」不得覆盖掉真实字段。
        # 场景：MCP 未配置时画像报告会带上 `_mock` 的示例身份信息，
        # 若直接整体替换，会把用户在录入流程里确认过的注册资本、成立日期冲掉。
        prev_rows = self.query(
            "SELECT * FROM CP_COUNTERPARTY WHERE CP_ID = :cp_id", {"cp_id": cp_id}
        )
        if prev_rows:
            prev = {str(k).lower(): v for k, v in prev_rows[0].items()}
            for key in ("org_id", "credit_code", "full_name", "short_name", "inst_type",
                        "reg_capital", "found_date", "legal_person", "listed_flag",
                        "risk_level", "remark"):
                new_val = payload[key]
                old_val = prev.get(key) or ""
                if is_placeholder(new_val):
                    payload[key] = str(old_val) if not is_placeholder(old_val) else ""
                    payload[key] = payload[key] if payload[key] else str(new_val)

        self.execute("DELETE FROM CP_COUNTERPARTY WHERE CP_ID = :cp_id", {"cp_id": cp_id})
        self.execute(
            "INSERT INTO CP_COUNTERPARTY (CP_ID, ORG_ID, CREDIT_CODE, FULL_NAME, SHORT_NAME, "
            "INST_TYPE, REG_CAPITAL, FOUND_DATE, LEGAL_PERSON, LISTED_FLAG, RISK_LEVEL, REMARK, UPDATE_TIME) "
            "VALUES (:cp_id, :org_id, :credit_code, :full_name, :short_name, :inst_type, "
            ":reg_capital, :found_date, :legal_person, :listed_flag, :risk_level, :remark, :ts)",
            payload,
        )
        return {"cp_id": cp_id, **{k: v for k, v in payload.items() if k != "cp_id"},
                "update_time": payload["ts"]}

    def get_counterparty(self, cp_id: str) -> dict[str, Any] | None:
        rows = self.query(
            "SELECT * FROM CP_COUNTERPARTY WHERE CP_ID = :cid", {"cid": cp_id}
        )
        return rows[0] if rows else None

    def find_counterparty_by_name(self, name: str) -> dict[str, Any] | None:
        rows = self.query(
            "SELECT * FROM CP_COUNTERPARTY WHERE FULL_NAME = :n OR SHORT_NAME = :n",
            {"n": name},
        )
        return rows[0] if rows else None

    def list_counterparties(self, keyword: str = "", limit: int = 20) -> list[dict[str, Any]]:
        if keyword:
            rows = self.query(
                "SELECT * FROM CP_COUNTERPARTY WHERE FULL_NAME LIKE :kw OR SHORT_NAME LIKE :kw "
                "ORDER BY UPDATE_TIME DESC",
                {"kw": f"%{keyword}%"},
            )
        else:
            rows = self.query("SELECT * FROM CP_COUNTERPARTY ORDER BY UPDATE_TIME DESC")
        return rows[:limit]

    def save_rating(self, cp_id: str, rating: dict[str, Any]) -> None:
        self.execute(
            "INSERT INTO CP_RATING (CP_ID, RATING_DATE, AGENCY, ISSUER_RATING, ISSUER_OUTLOOK, "
            "BOND_RATING, RATING_HISTORY) VALUES (:cid, :d, :a, :ir, :io, :br, :hist)",
            {
                "cid": cp_id, "d": str(rating.get("rating_date") or now_str()[:10]),
                "a": str(rating.get("agency") or ""),
                "ir": str(rating.get("issuer_rating") or ""),
                "io": str(rating.get("issuer_outlook") or ""),
                "br": str(rating.get("bond_rating") or ""),
                "hist": str(rating.get("rating_history") or ""),
            },
        )

    def save_finance(self, cp_id: str, fin: dict[str, Any]) -> None:
        self.execute(
            "INSERT INTO CP_FINANCE (CP_ID, PERIOD, TOTAL_ASSET, TOTAL_LIAB, REVENUE, NET_PROFIT, "
            "DEBT_RATIO, ROE) VALUES (:cid, :p, :ta, :tl, :rev, :np, :dr, :roe)",
            {
                "cid": cp_id, "p": str(fin.get("period") or ""),
                "ta": str(fin.get("total_asset") or ""), "tl": str(fin.get("total_liab") or ""),
                "rev": str(fin.get("revenue") or ""), "np": str(fin.get("net_profit") or ""),
                "dr": str(fin.get("debt_ratio") or ""), "roe": str(fin.get("roe") or ""),
            },
        )

    def save_risk_event(self, cp_id: str, ev: dict[str, Any]) -> None:
        self.execute(
            "INSERT INTO CP_RISK_EVENT (CP_ID, EVENT_ID, EVENT_TYPE, EVENT_DATE, EVENT_AMOUNT, "
            "EVENT_DESC, SOURCE) VALUES (:cid, :eid, :t, :d, :amt, :desc, :src)",
            {
                "cid": cp_id, "eid": str(ev.get("event_id") or new_id("ev_")),
                "t": str(ev.get("event_type") or ""), "d": str(ev.get("event_date") or ""),
                "amt": str(ev.get("event_amount") or ""), "desc": str(ev.get("event_desc") or "")[:4000],
                "src": str(ev.get("source") or "MCP"),
            },
        )

    def list_risk_events(self, cp_id: str, limit: int = 50) -> list[dict[str, Any]]:
        return self.query(
            "SELECT * FROM CP_RISK_EVENT WHERE CP_ID = :cid ORDER BY EVENT_DATE DESC",
            {"cid": cp_id},
        )[:limit]

    def list_finance(self, cp_id: str) -> list[dict[str, Any]]:
        return self.query("SELECT * FROM CP_FINANCE WHERE CP_ID = :cid", {"cid": cp_id})

    def list_ratings(self, cp_id: str) -> list[dict[str, Any]]:
        return self.query("SELECT * FROM CP_RATING WHERE CP_ID = :cid", {"cid": cp_id})

    # ---------- 评论 ----------
    def add_comment(self, cp_id: str, session_id: str, content: str,
                    style: str = "", role: str = "") -> dict[str, Any]:
        cid = new_id("cm_")
        self.execute(
            "INSERT INTO CP_COMMENT (COMMENT_ID, CP_ID, SESSION_ID, CONTENT, STYLE, ROLE, CREATED_AT) "
            "VALUES (:cid, :cp, :sid, :c, :st, :r, :ts)",
            {"cid": cid, "cp": cp_id, "sid": session_id, "c": content,
             "st": style, "r": role, "ts": now_str()},
        )
        return {"comment_id": cid, "content": content, "created_at": now_str()}

    def list_comments(self, cp_id: str, limit: int = 10) -> list[dict[str, Any]]:
        rows = self.query(
            "SELECT * FROM CP_COMMENT WHERE CP_ID = :cid ORDER BY CREATED_AT DESC", {"cid": cp_id}
        )
        return rows[:limit]

    # ---------- 长期记忆（带时效）----------
    def add_lt_memory(self, session_id: str, cp_id: str, mem_type: str,
                      content: str, ttl_days: int) -> dict[str, Any]:
        mid = new_id("lt_")
        expire = (datetime.now() + timedelta(days=ttl_days)).strftime("%Y-%m-%d %H:%M:%S")
        self.execute(
            "INSERT INTO LT_MEMORY (MEM_ID, SESSION_ID, CP_ID, MEM_TYPE, CONTENT, EXPIRE_AT, "
            "VERSION, LAST_REFRESHED) VALUES (:mid, :sid, :cp, :t, :c, :e, :v, :ts)",
            {"mid": mid, "sid": session_id, "cp": cp_id, "t": mem_type, "c": content,
             "e": expire, "v": "1", "ts": now_str()},
        )
        return {"mem_id": mid, "content": content, "expire_at": expire}

    def list_lt_memory(self, session_id: str, cp_id: str | None = None,
                       valid_only: bool = True) -> list[dict[str, Any]]:
        sql = "SELECT * FROM LT_MEMORY WHERE SESSION_ID = :sid"
        params: dict[str, Any] = {"sid": session_id}
        if cp_id:
            sql += " AND (CP_ID = :cid OR CP_ID = '')"
            params["cid"] = cp_id
        rows = self.query(sql, params)
        if not valid_only:
            return rows
        now = now_str()
        # 过期记忆自动失效（惰性清理，避免引入调度器依赖）
        return [r for r in rows if str(r.get("expire_at") or "9999") >= now]

    def delete_lt_memory(self, mem_id: str) -> None:
        self.execute("DELETE FROM LT_MEMORY WHERE MEM_ID = :mid", {"mid": mem_id})

    # ---------- 报告 ----------
    def save_report(self, cp_id: str, counterparty: str, status: str, pdf_path: str,
                    markdown: str, issues: str, scores: str) -> dict[str, Any]:
        rid = new_id("rp_")
        self.execute(
            "INSERT INTO REPORT_RECORD (REPORT_ID, CP_ID, COUNTERPARTY, STATUS, PDF_PATH, "
            "MARKDOWN, ISSUES, SCORES, CREATED_AT) "
            "VALUES (:rid, :cid, :cp, :st, :p, :md, :is, :sc, :ts)",
            {"rid": rid, "cid": cp_id, "cp": counterparty, "st": status, "p": pdf_path,
             "md": markdown, "is": issues, "sc": scores, "ts": now_str()},
        )
        return {"report_id": rid, "created_at": now_str()}

    def get_report(self, report_id: str) -> dict[str, Any] | None:
        rows = self.query("SELECT * FROM REPORT_RECORD WHERE REPORT_ID = :rid", {"rid": report_id})
        return rows[0] if rows else None

    def list_reports(self, limit: int = 20) -> list[dict[str, Any]]:
        return self.query("SELECT * FROM REPORT_RECORD ORDER BY CREATED_AT DESC")[:limit]

    # ---------- 健康 ----------
    def health(self) -> dict[str, Any]:
        return {
            "backend": self.backend,
            "degraded": self.backend != "oracle",
            "oracle_error": self._oracle_error,
        }


db = Database()
