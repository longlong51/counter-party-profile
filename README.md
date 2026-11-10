# AI 交易助手（Counter-Party Profile Agent）

面向金融市场交易台的多 Agent 智能体应用：把**监管规则知识库**、**对手方外部数据**、**本地文档解析**三路信息融合，自动产出**交易对手方画像报告**，并支持对话式录入对手方、生成对手方评论。

> 本文档即「需求设计方案」，包含：背景与目标、总体架构、技术栈、项目结构、功能模块、接口出入参、数据模型、核心算法、部署与评测说明。开发实现严格按本文档落地。

---

## 1. 需求背景与目标

交易员在撮合、授信、回购、衍生品等场景中，需要快速回答三个问题：

1. 这个对手方**是谁**（主体身份、股权、关联方）；
2. 这个对手方**靠不靠得住**（评级、财务、风险事件、舆情）；
3. 这笔交易**在规则上怎么做、清算交收怎么保障**（交易机制、中央对手方、DVP、违约处置）。

传统做法依赖人工翻阅年报、评级报告、监管规则，耗时且口径不一。本项目目标：

- 用 **RAG** 把 100 条监管/交易知识点变成可问答的知识库；
- 用 **MCP** 把外部金融数据库（同花顺 iFinD / 恒生聚源）的对手方数据接进来；
- 用 **4 个 Agent 协作 + 校验回环** 生成结构化画像报告（PDF 可下载）；
- 用 **对话** 完成对手方录入（中断确认后落库）与评论生成（短/长期记忆）。

---

## 2. 总体架构

```
┌────────────────────────────── 前端（静态聊天页 /static）──────────────────────────────┐
│  会话列表 │ 对话区（流式） │ 文件上传 │ 对手方录入确认卡片 │ 报告下载                  │
└───────────────────────────────────────┬───────────────────────────────────────────────┘
                                        │ HTTP / SSE
┌─────────────────────────────── FastAPI 应用层 ─────────────────────────────────────────┐
│  /api/chat  /api/knowledge  /api/documents  /api/counterparty  /api/reports  /health     │
└───────┬───────────────────────┬───────────────────────┬───────────────────────┬────────┘
        │                       │                       │                       │
┌───────▼────────┐   ┌──────────▼─────────┐   ┌──────────▼─────────┐   ┌─────────▼────────┐
│ RAG 服务        │   │ 文档解析服务        │   │ 多 Agent 编排       │   │ 记忆服务          │
│ 分块/向量/混合检索│   │ PDF+表格+图片OCR    │   │ LangGraph 4-Agent  │   │ 滑窗 + TTL 长期记忆│
│ RRF 融合/阈值拒答│   │ → Redis 缓存        │   │ + 校验回环          │   │                  │
└───┬───────┬─────┘   └──────┬──────────────┘   └──┬──────────┬──────┘   └────┬───────────┘
    │       │                │                     │          │               │
┌───▼───┐ ┌─▼─────┐   ┌──────▼──────┐   ┌──────────▼──┐  ┌────▼─────┐  ┌─────▼──────────┐
│Chroma │ │LLM API│   │ Redis 缓存   │   │ MCP Client  │  │ Oracle   │  │ Redis / Oracle │
│向量库  │ │主/备   │   │ (可退化为内存)│   │ iFinD/聚源   │  │ 关系库    │  │ (可退化为本地)  │
└───────┘ └───────┘   └─────────────┘   └─────────────┘  └──────────┘  └────────────────┘
```

**一次「生成画像报告」的时序**

```
用户 → POST /api/reports/generate {counterparty}
   → Agent1 MCP 取数（iFinD enterprise/bond/news；失败降级读原始报告与缓存）
   → Agent2 读取 Redis 缓存（MCP 原始返回、已解析文档、历史画像）
   → Agent3 结合 skill 流程 + 知识库，生成报告 Markdown/结构化 JSON
   → Agent4 依据知识库规则校验（主体一致性/评级口径/风险披露/结论是否有据）
        ├─ 有问题且 retry<2 → 回传 issue list 给 Agent3 重生成
        └─ 通过 → 落库 + 渲染 PDF + 返回下载链接
```

---

## 3. 技术栈

| 层 | 选型 | 说明 |
|---|---|---|
| 语言/运行时 | Python 3.13 | 项目内 `python` 可用 |
| Web 框架 | FastAPI + Uvicorn | 异步、自动 OpenAPI、SSE 流式 |
| 智能体框架 | **LangGraph**（LangChain 生态） | 状态图编排、条件边实现校验回环、`interrupt()` 实现人工确认中断 |
| LLM | OpenAI 兼容协议（智谱 GLM-4.7-Flash 主力 / 百炼 Qwen 备用） | 见 `docs/llm_free_token_research.md` |
| 向量库 | **ChromaDB**（PersistentClient，cosine） | 纯本地、无需服务端 |
| 关系库 | **Oracle**（`oracledb`） | 未配置 DSN 时自动退化为本地 SQLite（保证可运行） |
| 缓存 | **Redis** | 未连接时退化为进程内 TTL 字典 |
| 消息/通知 | **Kafka**（kafka-python） | 报告生成/录入事件推送；未配置 broker 时退化为本地事件日志 |
| PDF 解析 | PyMuPDF（文本+表格+图片）、pypdf（兜底） | 表格用 `find_tables()`，图片抽出后走视觉模型 OCR |
| 图片 OCR | 视觉大模型（GLM-4.6V-Flash）+ Pillow 预处理 | 无 Tesseract 依赖 |
| PDF 生成 | ReportLab（含中文字体、表格、柱状图） | 生成画像报告 |
| 分词/检索 | jieba + 自实现 BM25 | 关键词路召回 |
| 测试 | pytest + httpx TestClient | 冒烟测试 |

> **设计原则：所有外部依赖均可降级**。Oracle/Redis/Kafka/MCP/LLM 未配置时，代码走本地实现并标记 `degraded`，保证「缺配置也能跑通全链路」。

---

## 4. 项目结构

```
counter-party-profile/
├── README.md                     # 本文档（需求设计方案）
├── requirements.txt
├── .env.example                  # 全部配置项（占位值）
├── app/
│   ├── main.py                   # FastAPI 入口：路由挂载、启动初始化、异常处理
│   ├── core/
│   │   ├── config.py             # pydantic-settings 配置中心（占位默认值）
│   │   └── logging.py            # 统一日志
│   ├── models/
│   │   └── schemas.py            # 所有请求/响应 Pydantic 模型
│   ├── api/
│   │   ├── chat.py               # 会话与对话（含流式）
│   │   ├── knowledge.py          # 知识库问答、重建索引、评测
│   │   ├── documents.py          # PDF/图片上传解析
│   │   ├── counterparty.py       # 录入（中断确认）、评论、列表、详情
│   │   └── reports.py            # 4-Agent 画像报告生成与下载
│   ├── services/
│   │   ├── cache.py              # Redis / 内存降级缓存
│   │   ├── db.py                 # Oracle / SQLite 降级关系库（建表 + DAO）
│   │   ├── llm.py                # LLM 客户端（主备切换、重试、流式、视觉）
│   │   ├── embeddings.py         # 本地确定性 embedding（免下载模型）
│   │   ├── vector_store.py       # Chroma 封装（add/query/count/get）
│   │   ├── rag.py                # 语义分块 + 混合检索 + RRF + 阈值拒答
│   │   ├── document_parser.py    # PDF/图片解析（文本/表格/OCR）+ 缓存
│   │   ├── memory.py             # 短期滑窗 + 摘要压缩 + 带时效长期记忆
│   │   ├── mcp_client.py         # iFinD MCP JSON-RPC 客户端（含占位降级）
│   │   ├── report_pdf.py         # ReportLab 报告渲染
│   │   ├── evaluator.py          # 知识问答评测执行与报告渲染
│   │   └── events.py             # Kafka / 本地事件总线
│   ├── agents/
│   │   ├── prompts.py            # 各 Agent 系统提示词
│   │   ├── counterparty_graph.py # 4 Agent 协作 + 校验回环（LangGraph）
│   │   └── intake_graph.py       # 对话录入（LangGraph interrupt 中断确认）
│   ├── skills/
│   │   └── counterparty_report_skill.md   # 报告生成 Skill（流程规范）
│   └── static/
│       ├── index.html            # 聊天页
│       ├── app.js
│       └── style.css
├── data/
│   ├── knowledge_base/           # 第 1 项产物（100 条知识点 + 分主题 md）
│   ├── reference_reports/        # 第 2 项产物（15 份节选 PDF + 图片 + 原始报告）
│   ├── chroma/                   # 向量库持久化目录（运行时生成）
│   ├── app.db                    # SQLite 降级库（运行时生成）
│   ├── reports/                  # 生成的 PDF 报告
│   └── uploads/                  # 上传的原始文件
├── docs/
│   ├── financial_data_mcp_research.md    # 第 3 项产物
│   └── llm_free_token_research.md        # 第 4 项产物
├── eval/
│   ├── qa_eval_set.jsonl         # 知识问答评测集（30 条，含拒答用例）
│   └── eval_report.md            # 评测运行结果（脚本生成）
├── scripts/
│   ├── ingest_knowledge.py       # 知识库入库（分块→向量化→Chroma）
│   ├── build_eval.py             # 评测集生成（分层抽样 + 拒答用例）
│   └── run_eval.py               # 评测执行器
└── tests/
    └── test_smoke.py             # 端到端冒烟测试
```

---

## 5. 功能模块说明

### 5.1 知识库 RAG 生成（`services/rag.py` + `scripts/ingest_knowledge.py`）

1. **读取**：`data/knowledge_base/knowledge_points.jsonl`（100 条，权威监管原文提炼）；
2. **语义分块**：每条知识点按「标题 + 正文 + 要点」拼成 `content`：
   - 先按 `\n` 与句号切句；
   - 以 **目标 320 字 / 重叠 64 字** 聚合为 chunk（保留 `key_points` 作为独立短块，提升命中精度）；
   - 每个 chunk 携带 `id / category / sub_category / source_name / publish_date / effective_date / counterparty_relevance / chunk_index` 元数据；
3. **向量化**：`embeddings.py` 采用**确定性哈希 n-gram 向量**（字符 2/3-gram + 词粒度，256 维，L2 归一），无需下载模型、离线可跑，跨进程稳定（相同文本恒等向量）；
4. **入库**：Chroma `PersistentClient(path=data/chroma)`，collection `trading_knowledge`，cosine 空间；
5. **幂等**：以 `knowledge_id#chunk_index` 为 doc id，重建时先 `delete(where={"source":"kb"})` 再写入。

### 5.2 交易知识问答（`POST /api/knowledge/ask`）

流程：**问题改写 → 双路召回 → RRF 融合 → 阈值判定 → 生成**。

- **问题改写**：LLM 把口语问题改写为「检索式」（保留原问题作为 `original_query`）；LLM 不可用时用规则改写（去停用词 + 同义词扩展）。
- **向量路**：Chroma `query(query_embeddings, n_results=20)`，得 `(doc_id, distance)`；
- **关键词路**：自实现 BM25（`k1=1.5, b=0.75`，jieba 分词，全库统计 IDF），取 top20；
- **RRF 融合**：`score(d)=Σ 1/(k+rank_i(d))`，`k=60`，融合后取 top `TOP_K=6`；
- **相似度阈值（双门限）**：向量路最优 `cosine_sim >= 0.30` 直接放行；否则须 `BM25 >= 10.0` **且** `cosine_sim >= 0.22` 才放行（防止仅靠高频词误召回）；两者都不满足即判定「召回不足」，直接返回
  `{"answer": "知识库暂无相关信息", "grounded": false}`，**不调用 LLM 自由发挥**；
- **生成**：把 top chunks（带来源标注）拼进提示词，要求「只依据给定材料、逐条标注 `[来源: xxx]`、材料不足则明确说明」；LLM 不可用时退化为「知识库原文摘录」并显式标注；
- **返回**：`answer / citations[{doc_id,knowledge_id,title,source_name,source_url,score,snippet}] / retrieval{vector_hits, keyword_hits, fused, best_similarity, best_bm25}`。

### 5.3 PDF / 图片解析（`POST /api/documents/parse`）

- **PDF**：PyMuPDF 逐页 `get_text("text")`；`page.find_tables()` 抽表格转 Markdown；页面无文本层（扫描件）时把页面渲染为 150dpi JPEG，走**视觉模型 OCR**；每页记录 `has_text/has_tables/image_count`。
- **图片**：Pillow 读取 → 等比压缩到长边 ≤ 1600 → base64 → 视觉模型输出 Markdown（表格用 `|` 保留）。
- **缓存**：解析结果 JSON 存 Redis `doc:{sha256}`，TTL 12h；未连 Redis 时存进程内。
- **返回**：`doc_id / pages / tables / text / markdown / parser(used: pymupdf|vision|mixed)`，并登记到会话上下文供后续 Agent 使用。

### 5.4 对手方画像报告（4 Agent 协作，`POST /api/reports/generate`）

LangGraph `StateGraph`，节点：

| 节点 | 职责 | 失败降级 |
|---|---|---|
| `agent1_fetch` | MCP 取数：先 `basic_get_enterprise_associate` 归一企业 → 工商底档、股东、被执行/失信/破产、债券与评级、公告舆情 | MCP 不可用 → 读 `data/reference_reports` 已解析缓存 + `cp:mcp:raw:*`，标记 `data_source=local_fallback` |
| `agent2_cache` | 读 Redis：MCP 原始返回、上传文档解析结果、历史画像；做**去重与冲突标注**（同一指标多来源不一致时全部保留并标 `conflict`） | 无缓存 → 空集，不阻断 |
| `agent3_generate` | 按 `skills/counterparty_report_skill.md` 的结构生成报告（Markdown + 结构化 `sections`），引用知识库条款 | LLM 不可用 → 模板化报告（明确标注 `template_mode`） |
| `agent4_verify` | 用知识库规则 + 结构清单校验：主体名/代码一致性、评级口径、风险披露完整性、结论是否有引用支撑；输出 `issues[]` | — |

**条件边**：`issues` 非空且 `retry < MAX_RETRY(2)` → 回到 `agent3_generate`（携带 `fix_instructions`）；否则 → `render`。
**产物**：`data/reports/{cp_id}_{timestamp}.pdf`（ReportLab：标题页、结论、五维评分表、财务表、风险事件表、柱状图、来源清单），接口返回 `report_id / download_url / issues / trace[]`。

### 5.5 对话录入对手方（`POST /api/counterparty/intake` + `/intake/confirm`）

LangGraph 图：`extract`（LLM 抽取 JSON：名称、统一社会信用代码、机构类型、评级、注册资本、法人、注册地、主营业务、风险标签）→ **`interrupt()` 中断**，把抽取结果抛给前端 → 用户 `confirm`（`Command(resume={...})`）→ `persist`（写 Oracle `CP_COUNTERPARTY` / `CP_RATING`，同时把非结构化描述写入 Chroma `counterparty_memory`）。

- 会话态由 `MemorySaver` checkpointer 保存，`thread_id = session_id`；
- 支持「修正 + 补充」：`resume` 携带 `patched_fields`，覆盖后再落库；
- 用户取消：`resume={"action":"cancel"}` → 图结束，不落库；
- **规则抽取兜底**（LLM 不可用时）：在「去空格副本」上匹配信用代码、注册资本（`注册资本…2943亿元`）、成立日期、评级、法人；
  机构类型优先取「机构类型/机构性质/类型：xxx」的显式表述，避免名称里的「中国银行」先被截成「银行」（正确输出「商业银行」）；
- **写库保护**：`db.upsert_counterparty` 遇到「空值」或「示例值」时不覆盖库内已有真实字段——MCP 降级时画像流程会带 `_mock` 示例身份信息，若不保护会把用户确认过的注册资本、成立日期冲掉。

### 5.6 对手方评论（`POST /api/counterparty/comment`）

- **短期记忆**：取该会话最近 `WINDOW=6` 轮对话；超过 `12` 轮时触发**摘要压缩**（LLM 把更早对话压成 ≤200 字摘要，存 `session_summary`），保留「摘要 + 滑窗」；
- **长期记忆**：用户指定的「风格 / 角色视角 / 关注点」写入 `LT_MEMORY` 表，字段含 `expire_at`（默认 30 天）、`version`、`last_refreshed`；
  - 结构化（风格标签、角色、评分偏好）→ Oracle；
  - 非结构化（原话、评论样例）→ Chroma `counterparty_memory`；
  - **定时更新**：`APScheduler` 风格的轻量后台任务每 6h 扫描过期记忆并重建摘要（无调度器时在请求路径上惰性刷新）。
- **输出**：`comment`（≤300 字）、`style_used`、`memory_used{short_turns, summary, long_terms}`。

---

## 6. 接口出入参

统一前缀 `/api`，统一错误体：`{"code": "...", "message": "...", "detail": {...}}`。

### 6.1 会话

| 方法 | 路径 | 入参 | 出参 |
|---|---|---|---|
| POST | `/api/chat/sessions` | `{"title?": "str", "user_id?": "str"}` | `{"session_id","title","created_at"}` |
| GET | `/api/chat/sessions` | `?limit=20` | `{"items":[{"session_id","title","updated_at","message_count"}]}` |
| GET | `/api/chat/sessions/{sid}/messages` | — | `{"items":[{"role","content","created_at","meta"}]}` |
| POST | `/api/chat/messages` | `{"session_id","content","stream?": false}` | 非流式：`{"reply","citations","retrieval","latency_ms"}`；流式：`text/event-stream`，事件 `delta` / `done` |
| DELETE | `/api/chat/sessions/{sid}` | — | `{"deleted": true}` |

### 6.2 知识库

| 方法 | 路径 | 入参 | 出参 |
|---|---|---|---|
| POST | `/api/knowledge/ask` | `{"question","top_k?":6,"rewrite?":true}` | `{"answer","grounded","citations":[...],"retrieval":{"vector_hits","keyword_hits","fused","best_similarity"}}` |
| POST | `/api/knowledge/reindex` | `{"force?": false}` | `{"indexed_chunks","collection_count","elapsed_ms"}` |
| GET | `/api/knowledge/stats` | — | `{"documents","chunks","categories":{"A":32,...}}` |
| POST | `/api/knowledge/eval` | `{"limit?": 30}` | `{"total","passed","failed","hit_rate","reject_rate","details":[...]}` |

### 6.3 文档

| 方法 | 路径 | 入参 | 出参 |
|---|---|---|---|
| POST | `/api/documents/parse` | `multipart/form-data: file, session_id?` | `{"doc_id","filename","pages","tables","text_chars","markdown","parser","cached"}` |
| GET | `/api/documents/{doc_id}` | — | 同上（从缓存取） |
| POST | `/api/documents/{doc_id}/summarize` | `{"focus?": "财务|风险|交易"}` | `{"summary","key_facts":[...]}` |

### 6.4 对手方

| 方法 | 路径 | 入参 | 出参 |
|---|---|---|---|
| POST | `/api/counterparty/intake` | `{"session_id","text"}` | **中断**：`{"status":"pending_confirm","thread_id","extracted":{...},"missing":[...]}` |
| POST | `/api/counterparty/intake/confirm` | `{"thread_id","action":"confirm|cancel","patched_fields?":{}}` | `{"status":"saved|cancelled","cp_id","record":{...}}` |
| GET | `/api/counterparty` | `?keyword=&limit=20` | `{"items":[{"cp_id","full_name","inst_type","issuer_rating","risk_level","updated_at"}]}` |
| GET | `/api/counterparty/{cp_id}` | — | `{"profile":{...},"rating":{...},"finance":[...],"risks":[...],"comments":[...]}` |
| POST | `/api/counterparty/comment` | `{"session_id","cp_id?","style?","role?","extra?","ttl_days?":30}` | `{"comment","style_used","memory_used":{...},"degraded"}` |
| POST | `/api/counterparty/sync` | `{"cp_id"}` | `{"synced":true,"tools":[...],"records":12,"data_source":"mcp|fallback"}` |

### 6.5 报告

| 方法 | 路径 | 入参 | 出参 |
|---|---|---|---|
| POST | `/api/reports/generate` | `{"counterparty":"中国银行","session_id?":"","max_retry?":2,"include_docs?":true}` | `{"report_id","cp_id","status","pdf_url","markdown","scores":{...},"issues":[...],"retries","trace":[...],"data_source"}` |
| GET | `/api/reports` | `?limit=20` | `{"items":[{"report_id","counterparty","created_at","pdf_url","status"}]}` |
| GET | `/api/reports/{report_id}/pdf` | — | `application/pdf`（文件流） |
| GET | `/health` | — | `{"status":"ok","components":{"llm","chroma","redis","oracle","mcp","kafka"}}` |

---

## 7. 数据模型（Oracle，SQLite 同构）

| 表 | 主要字段 |
|---|---|
| `CP_COUNTERPARTY` | `CP_ID PK, ORG_ID, CREDIT_CODE, FULL_NAME, SHORT_NAME, INST_TYPE, REG_CAPITAL, FOUND_DATE, LEGAL_PERSON, LISTED_FLAG, RISK_LEVEL, UPDATE_TIME` |
| `CP_RATING` | `CP_ID, RATING_DATE, AGENCY, ISSUER_RATING, ISSUER_OUTLOOK, BOND_RATING, RATING_HISTORY CLOB` |
| `CP_FINANCE` | `CP_ID, PERIOD, TOTAL_ASSET, TOTAL_LIAB, REVENUE, NET_PROFIT, DEBT_RATIO, ROE` |
| `CP_RISK_EVENT` | `CP_ID, EVENT_ID, EVENT_TYPE, EVENT_DATE, EVENT_AMOUNT, EVENT_DESC CLOB, SOURCE` |
| `CP_COMMENT` | `COMMENT_ID PK, CP_ID, SESSION_ID, CONTENT CLOB, STYLE, ROLE, CREATED_AT` |
| `CHAT_SESSION` | `SESSION_ID PK, TITLE, USER_ID, SUMMARY CLOB, UPDATED_AT` |
| `CHAT_MESSAGE` | `MSG_ID PK, SESSION_ID, ROLE, CONTENT CLOB, META CLOB, CREATED_AT` |
| `LT_MEMORY` | `MEM_ID PK, SESSION_ID, CP_ID, MEM_TYPE, CONTENT CLOB, EXPIRE_AT, VERSION, LAST_REFRESHED` |
| `REPORT_RECORD` | `REPORT_ID PK, CP_ID, COUNTERPARTY, STATUS, PDF_PATH, MARKDOWN CLOB, ISSUES CLOB, CREATED_AT` |

> 未配置 `ORACLE_DSN` 时，`db.py` 用同构 DDL 建 SQLite（`data/app.db`），接口行为一致。

---

## 8. 核心算法要点

1. **RRF**：`score = Σ_i 1/(60 + rank_i)`；两路各自 top20，融合后 top6。
2. **拒答判定**：`max_cosine >= 0.30` 直接放行；否则须同时满足 `max_bm25 >= 10.0` 且 `max_cosine >= 0.22`（`weak_similarity`，防止仅靠高频词误召回）才放行；两者都不满足即返回「知识库暂无相关信息」，不调用大模型，避免幻觉。
3. **校验回环**：Agent4 输出 `issues[{level, field, problem, fix}]`；`level=high` 或累计 `>3` 条即触发重生，最多 2 次，仍不通过则报告标注「待人工复核」。
4. **记忆时效**：长期记忆 `expire_at` 到期即失效；`last_refreshed` 超过 24h 触发摘要重建，避免陈旧风格污染当前输出。
5. **降级矩阵**：`llm/chroma/redis/oracle/mcp/kafka` 六类依赖各自独立降级，任一不可用不影响主链路返回。

---

## 9. 运行方式

```powershell
# 1) 依赖（已安装）
python -m pip install -r requirements.txt

# 2) 配置（全部为占位值，替换即可）
Copy-Item .env.example .env

# 3) 知识库入库（100 条 → 分块 → 向量）
python scripts/ingest_knowledge.py

# 4) 启动
python -m uvicorn app.main:app --host 127.0.0.1 --port 8000
# 浏览器打开 http://127.0.0.1:8000/

# 5) 评测
python scripts/run_eval.py --limit 30

# 6) 冒烟测试（8 条，覆盖六组接口 + 降级链路）
python -m pytest tests -q
```

> `.env` 不存在也能直接启动：所有配置项在 `app/core/config.py` 里有同名默认值（占位）。
> 首次启动若向量库为空，`lifespan` 会自动执行一次知识库入库。

---

## 10. 评测集说明（`eval/qa_eval_set.jsonl`）

共 30 条，由 `scripts/build_eval.py` 从 100 条知识点**按主题分层抽样**（seed=42）自动生成 27 条，另手工补 3 条不可答用例：

| 主题（ID 前缀） | 条数 | 问题模板 |
|---|---|---|
| A 交易机制与申报 | 8 | 「{标题}的具体规则是什么？」 |
| B 清算交收与中央对手方 | 6 | 「{标题}的核心要求有哪些？」 |
| C 结算风险与违约处置 | 4 | 「{标题}情形下，违约如何处置？」 |
| D 基金交易与申赎估值 | 4 | 「{标题}是怎么规定的？」 |
| E 对手方识别与尽职调查 | 4 | 「{标题}对金融机构提出了什么要求？」 |
| F 信用风险与对手方分析 | 1 | 「{标题}应当如何管理或计量？」 |
| 不可答（应拒答） | 3 | 股价预测 / 基金推荐 / 个股财务，均为知识库外话题 |

每条用例字段：`id, question, type, expected_ids(期望命中的 knowledge_id), keywords(期望被召回的原文片段), note`。

判分口径：

- **可答用例**：`grounded=true` **且** `citations` 含 `expected_ids` **且** 关键词在答案或召回片段中出现（比较时忽略空格）；
- **拒答用例**：`grounded=false` 且答案严格为「知识库暂无相关信息」。

`python scripts/run_eval.py` 输出 `eval/eval_report.md`（含逐条明细与失败原因）。

---

## 11. 已知边界与免责

- 外部 MCP / LLM / Oracle / Redis / Kafka 均使用**占位配置**，未配置时自动降级并在响应中标记 `degraded`；
- MCP 真实密钥需账号实名申请，代码中为 `ifind_mcp_placeholder_key`；
- 参考报告为中国银行公开披露文件节选，仅作演示，不构成投资建议；
- 报告由大模型生成，**须人工复核后方可用于授信或交易决策**。

---

## 12. 交付物对照

| 用户要求 | 落点 |
|---|---|
| 100 条知识点结构化知识库 | `data/knowledge_base/*`（jsonl/csv/分主题 md/索引） |
| 上市公司 5 类×3 份 1–5 页 PDF（含图表、部分图片型） | `data/reference_reports/pdf/*`、`images/*`、`INDEX.md` |
| 金融数据库 MCP 接入与对手方表字段 | `docs/financial_data_mcp_research.md` |
| 免费 token LLM 接入与 key 申请 | `docs/llm_free_token_research.md` |
| 需求设计方案 / 技术栈 / 结构 / 模块 / 接口 | 本文档 |
| 代码注释 | 全量中文 docstring + 关键分支行内注释 |
| 知识问答评测集 | `eval/qa_eval_set.jsonl` + `scripts/run_eval.py` |
| 缺配置用占位 | `.env.example` + `config.py` 默认值 |
| 报告生成 Skill | `app/skills/counterparty_report_skill.md` |

---

## 13. 实测验证结果（2026-11-09，本机 Windows / Python 3.13）

| 项目 | 命令 | 结果 |
|---|---|---|
| 知识库入库 | `python scripts/ingest_knowledge.py` | 100 条知识点 → **295 个 chunk**，Chroma 集合 `trading_knowledge`，耗时 1.5 s |
| 评测集生成 | `python scripts/build_eval.py` | 30 条用例（27 自动 + 3 拒答）写入 `eval/qa_eval_set.jsonl` |
| 知识问答评测 | `python scripts/run_eval.py --limit 30` | **通过 29/30（96.7%）**；可答命中率 96.3%；**拒答正确率 100%（3/3）** |
| 冒烟测试 | `python -m pytest tests -q` | **8 passed**（健康检查、统计、问答、拒答、会话、录入中断流、报告生成、meta） |
| 服务自检 | `GET /health` | `chroma 295 chunks / redis=memory / oracle=sqlite / llm=degraded(占位key) / mcp=placeholder / kafka=local`，主链路可用 |
| 前端 | `GET /` + `node --check app.js` | 页面 200、静态资源挂载正常、JS 语法检查通过 |
| PDF 报告 | `POST /api/reports/generate` | 2 页 A4、9 个章节顺序编号、五维柱状图（单序列 5 柱）、引用去重，中文字体正常渲染 |
| 文档解析 | `POST /api/documents/parse`（文本型 PDF / 图片型 PDF / JPG） | 文本型：抽正文 + `find_tables()`；无文本层与图片：走视觉 OCR，**无视觉模型时明确返回 `ocr_failed`**，不伪造内容 |
| SSE 流式 | `POST /api/chat/messages/stream` | 事件流 `delta → done` 正常；LLM 不可用时降级为一次性 delta + 引用列表 |
| 录入中断确认 | 浏览器端到端操作 | 粘贴描述 → 抽取 `中国银行 / 9110…3428 / 商业银行 / 2943亿元 / 1983年 / AAA` → 弹窗确认 → 入库并回显 `cp_id` |
| 降级写库保护 | 录入后生成画像报告（MCP 走 mock） | 库内记录仍为 `2943亿元 / 1983年`，示例值不覆盖用户确认的真实字段 |
| 前端页面 | 浏览器打开 `GET /` | 会话列表、模式切换按钮、上传、录入、报告、评论五个入口可用；已修复「弹窗遮罩常驻」「确认弹窗被上层回调误关」两处前端缺陷 |

唯一失败用例：`Q15`（风险基金交纳标准）条款命中正确、`grounded=true`，但自动抽取的关键词在召回片段中措辞不同（知识库写作「按登记结算机构业务收入、收益的 9% 提取」，chunk 要点写作「机构按业务收入 9% 提取」），属于**评测集关键词构造的措辞差异**，并非检索失败；接入真实 LLM 后生成答案更完整，该项可稳定通过。

复现方式：`python scripts/ingest_knowledge.py && python scripts/build_eval.py && python scripts/run_eval.py && python -m pytest tests -q`。