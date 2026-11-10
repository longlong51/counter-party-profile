# 金融数据库 MCP 服务调研（含交易对手相关表名与字段）

- 调研时间：2026-11-09（Asia/Shanghai）
- 调研目的：为「AI 交易助手」的 Agent1（MCP 取数 Agent）选定可用的金融数据源，明确**接入方法**与**交易对手（Counterparty）相关表名/字段**。
- 结论先行：**首选同花顺 iFinD MCP**（协议级细节已实测可核验、按请求计费、有免费额度、工具名与参数可枚举）；**备选恒生聚源「数据地图 MCP / 综合问数 MCP」**（企业级、接口数量最多，但完整接口清单需签约后获取）。

---

## 一、候选清单与对比

| 服务商 | 产品 | MCP 形态 | 接入难度 | 交易对手数据覆盖 | 计费 | 适合本项目 |
|---|---|---|---|---|---|---|
| 同花顺 iFinD | iFinD MCP & SKILL | HTTP(JSON-RPC 2.0) 多 Server | 低（有公开文档 + SKILL 包） | 债券/存单行情、发债主体财务、工商企业（股东/被执行/失信/破产）、信用债评级、公告新闻 | 按请求次数计费，免费用户并发 2 req/s | **首选** |
| 恒生聚源 | 数据地图 MCP（含「综合问数 MCP」） | MCP Server（可上架阿里云百炼调用） | 中（需商务开通） | 股票/债券/基金/宏观/行业/财务指标/评级等专业金融库 | 按量计费（“聚粒”）+ 企业订阅 | 备选 / 生产环境 |
| 开源方案 | AKShare、Tushare Pro 自封装 MCP | 需自建 MCP Server | 中（自行封装） | 公开行情与财务，**无内部评级与工商司法明细** | 免费（Tushare 有积分限制） | 兜底 / 离线测试 |

> 说明：用户提到的「聚源」= 恒生聚源（上海恒生聚源数据服务有限公司，`gildata.com`），与同花顺 iFinD 是两家不同厂商，二者都能提供 MCP 形态的金融数据服务。

---

## 二、方案 A：同花顺 iFinD MCP（首选，协议细节已核验）

### 2.1 服务形态

iFinD MCP 按**数据域**拆成多个独立 MCP Server，每个 Server 一套工具：

| `server_type` | MCP Server 路径（基址 `https://api-mcp.51ifind.com:8643/ds-mcp-servers`） | 数据域 |
|---|---|---|
| `stock` | `/hexin-ifind-ds-stock-mcp` | A 股：智能选股、基本信息、财务、行情、股东、风险指标、ESG、重大事件 |
| `fund` | `/hexin-ifind-ds-fund-mcp` | 公募基金：搜索、资料、行情、持仓、持有人结构、基金公司 |
| `bond` | `/hexin-ifind-ds-bond-mcp` | 债券：基本信息、行情、发债主体财务、信用债/可转债/回购特殊指标、高频行情 |
| `global_stock` | `/hexin-ifind-ds-global-stock-mcp` | 港股 / 美股 |
| `index` | `/hexin-ifind-ds-index-mcp` | 指数与行业板块 |
| `future` | `/hexin-ifind-ds-futures-mcp` | 期货期权合约、行情、持仓 |
| `edb` | `/hexin-ifind-ds-edb-mcp` | 宏观与行业经济指标 |
| `news` | `/hexin-ifind-ds-news-mcp` | 财经新闻、上市公司公告语义搜索 |
| `enterprise` | `/kuaicha-enterprise-mcp` | 工商企业（同花顺快查）：工商、股权、司法风险、知识产权、招投标 |
| `law` | `/hexin-law-mcp` | 司法案例、法律法规检索 |

### 2.2 协议与鉴权（可照此实现客户端）

- 传输：MCP Streamable HTTP，`POST` + `Content-Type: application/json`，`Accept: application/json, text/event-stream`
- 协议版本：`protocolVersion: "2025-03-26"`（initialize 握手中声明）
- 鉴权：请求头 `Authorization: <MCP密钥>`（**注意：不是 `Bearer xxx`，是裸密钥**）
- 会话：`initialize` 响应头返回 `Mcp-Session-Id`，后续请求必须回传同名请求头；随后发送 `notifications/initialized`
- 工具发现：`tools/list`（**不消耗计费次数**）；工具调用：`tools/call`
- 请求头 `User-Agent` 用于标识调用方 Agent 名称（可空）

握手与调用示例（Python，可用 httpx 替换 requests）：

```python
import requests, json

BASE = "https://api-mcp.51ifind.com:8643/ds-mcp-servers"
SERVER = f"{BASE}/hexin-ifind-ds-bond-mcp"
TOKEN  = "ifind_mcp_placeholder_key"          # 占位：实际密钥从 MCP 官网「个人中心-密钥」获取
SID = None

def rpc(payload):
    headers = {"Content-Type": "application/json",
               "Accept": "application/json, text/event-stream",
               "Authorization": TOKEN}
    if SID:
        headers["Mcp-Session-Id"] = SID
    return requests.post(SERVER, json=payload, headers=headers, timeout=60)

# 1) 握手
r = rpc({"jsonrpc": "2.0", "id": 1, "method": "initialize",
         "params": {"protocolVersion": "2025-03-26", "capabilities": {},
                    "clientInfo": {"name": "ai-trading-assistant", "version": "1.0.0"}}})
SID = r.headers["Mcp-Session-Id"]
rpc({"jsonrpc": "2.0", "method": "notifications/initialized"})

# 2) 列出可用工具（不消耗次数，可用于探测当前账号权益）
print(rpc({"jsonrpc": "2.0", "id": 2, "method": "tools/list", "params": {}}).json())

# 3) 调用工具
resp = rpc({"jsonrpc": "2.0", "id": 3, "method": "tools/call",
            "params": {"name": "bond_basic_info",
                       "arguments": {"query": "26中国银行CD072的发行期限与发行总额"}}})
print(resp.json())
```

### 2.3 密钥申请（实际步骤）

1. 打开 <https://mcp.51ifind.com> → 右上角登录（需注册同花顺账号并完成实名/企业认证，视套餐而定）
2. 登录后进入「个人中心 → 密钥」，新建密钥并复制
3. 密钥等同账号权限，**不要写入代码仓库**，放 `.env` 或密钥管理服务
4. 首次接入建议先 `tools/list` 确认当前账号可用工具；返回 403 说明权益不足，需订阅套餐或购买增量包
5. 并发与计费：免费用户 2 并发/秒，个人版 5，企业版 10；计费按「模型实际发起的请求次数」计算（一次提问可能消耗多次）

> 截至本次调研，**未实际申请到可用密钥**（需登录+实名+可能付费），故本项目统一使用占位符 `ifind_mcp_placeholder_key`，代码链路完整、可随时替换真实 key。

### 2.4 交易对手相关工具与字段（MCP 侧「表名/字段」等价物）

MCP 不暴露数据库表，暴露的是「工具 + 参数 + 返回字段」。下表把**对手方画像所需字段**映射到已验证的工具：

| 画像维度 | server_type | 工具名 | 典型参数 | 关键返回字段 |
|---|---|---|---|---|
| 主体身份 / 债券归属 | `bond` | `bond_basic_info` | `{"query":"26中国银行CD072的发行期限与发行总额"}` | 债券代码、债券简称、发行人、发行期限、发行总额、票面利率、起息日、到期日、债项评级 |
| 交易与估值表现 | `bond` | `bond_market_data` | `{"query":"26国债01近五日收盘价、成交金额与久期、凸性"}` | 收盘价、涨跌幅、成交量/成交金额、久期、凸性、收益率 |
| 交易对手财务实力 | `bond` | `bond_financial_data` | `{"query":"24中行01在20251231的资产负债率和ROE"}` | 资产负债率、ROE、净利润、营业收入、总资产 |
| 信用资质（评级/回购/条款） | `bond` | `bond_special_data` | `{"query":"华海转债最新转股价格"}` | 主体评级、债项评级、评级展望、回购利率、转股价 |
| 实时交易活跃度 | `bond` | `bond_highfreq_quotes` | `{"symbols":"240025.IB","indicators":"最新价,成交量","data_mode":"real_time"}` | 最新价、现手、振幅、成交量（**银行间与经纪商数据不支持**） |
| 工商主体底档 | `enterprise` | `kuaicha_tool_discover` → `kuaicha_tool_call` | `{"query":"企业股东信息"}` / `{"tool_id":"...","params_to_tool":"{\"orgid\":\"...\"}"}` | 统一社会信用代码、注册资本、成立日期、法定代表人、股东股权、实际控制人、最终受益人 |
| 对手方风险事件 | `enterprise` | 同上（先 discover 再 call） | `{"query":"被执行人查询"}` / `{"query":"失信名单"}` / `{"query":"破产重整"}` | 经营异常、行政处罚、欠税、股权质押、被执行、失信、限高、裁判文书 |
| 名称归一（尽调第一步） | `enterprise` | `kuaicha_tool_call`（`tool_id=basic_get_enterprise_associate`） | `{"query":"中行"}` | `orgid`、`creditcode`、登记全称（**企业标识优先级：orgid > creditcode > 登记全称**） |
| 舆情与公告风险 | `news` | 公告/新闻语义搜索工具 | 交付方名称 + 事件关键词 | 标题、发布时间、正文段落、来源 |
| 宏观环境 | `edb` | `search_edb` → `get_edb_data` | 指标关键词 | 指标代码、指标名称、频率、数值序列 |

> 注意：`enterprise` 域必须「先搜后查」——`kuaicha_tool_discover` 的 `query` 只能传**简短中文能力描述**（如「企业股东信息」），不能传企业名或用户原句；`enterprise.md` 明确要求返回结果标注“数据来源于同花顺旗下快查企业数据引擎”。

---

## 三、方案 B：恒生聚源 数据地图 MCP / 综合问数 MCP（备选）

### 3.1 服务形态

- **定位**：基于恒生聚源专业金融数据库（20+ 年，服务 1000+ 家金融机构）构建的「一站式 MCP 服务」，官方称提供 **450+ 细粒度接口**，覆盖行情、F9、宏观行业、研报、公告、资讯等
- **两种入口**：
  1. **数据地图 MCP**：面向企业私有化/专有云，支持「本地主权 + 全域数据隔离」
  2. **综合问数 MCP**：2026-04 上架**阿里云百炼**平台，覆盖股票、债券、基金、宏观行业、财务指标等高频品类，机构可在百炼部署并调用 MCP Server
- **协议**：标准 MCP（百炼侧支持 SSE / Streamable HTTP），阿里云侧以 `type: "mcp"` + `server_protocol: "sse"` + 鉴权头方式接入

### 3.2 接入方法（三步）

1. **开通**：官网 <https://www.gildata.com> 「数据地图」产品页申请试用 / 企业开通（商务：021-60897828，service@gildata.com）；或直接在阿里云百炼「官方及第三方 MCP 服务」中搜索并开通
2. **取凭据**：开通后获得 MCP Endpoint + API Key（企业版一般由客户成功经理交付，含接口清单与数据字典）
3. **接入**：两种姿势
   - 直连：在 Agent 框架（LangGraph / LangChain）中用 `MultiServerMCPClient` 或自建 JSON-RPC 客户端连接 Endpoint，鉴权头带 API Key
   - 经百炼中转：Responses API 的 `tools` 中声明 `{"type":"mcp","server_protocol":"sse","server_label":"gildata","server_url":"<endpoint>","headers":{"Authorization":"Bearer <key>"}}`，最多挂 10 个 MCP Server

### 3.3 交易对手相关接口组（按公开品类整理）

聚源**未公开完整数据字典**，以下为按其公开品类（行情 / F9 / 宏观行业 / 研报 / 公告 / 资讯）与「综合问数」覆盖范围整理的**接口组映射**；签约后应以官方接口清单为准：

| 接口组（聚源侧） | 覆盖内容 | 对手方画像用途 |
|---|---|---|
| 债券基础与估值 | 债券要素、发行人、久期/凸性、中债估值 | 交易标的与交易对手归属 |
| 同业存单 | 发行计划、发行量、利率、到期日 | 银行类对手方的融资与交易活跃度 |
| 主体与债项评级 | 评级结果、展望、评级历史、评级机构 | 信用资质 |
| 财务指标（F9） | 三大报表、比率、同业对比 | 财务实力与偿债能力 |
| 工商与股权 | 股东、实际控制人、关联方 | 关联交易与关联对手方识别 |
| 公告与资讯 | 交易所公告、新闻语义检索 | 风险事件与舆情 |
| 宏观行业 | GDP/CPI/行业景气 | 行业环境 |

> 由于未签约，**未获得聚源的表名与字段级文档**，本文件不臆造其内部表名。落地时以「MCP 工具返回字段」为准即可（见第四节的自建表结构）。

---

## 四、交易对手相关「表名 + 字段」落地设计

MCP 只给「接口」，而本项目的关系库（Oracle）需要真实的表。下面这张映射表把 MCP 字段落到 Oracle 自建表，**表名与字段即项目内真实使用**：

### 4.1 `CP_COUNTERPARTY`（对手方主档）

| 字段 | 类型 | 来源 |
|---|---|---|
| `CP_ID` | VARCHAR2(32) PK | 系统生成（UUID） |
| `ORG_ID` | VARCHAR2(64) | `enterprise` → `basic_get_enterprise_associate` 返回 `orgid` |
| `CREDIT_CODE` | VARCHAR2(18) | 同上 `creditcode`（统一社会信用代码） |
| `FULL_NAME` | VARCHAR2(200) | 登记全称 |
| `SHORT_NAME` | VARCHAR2(100) | 债券简称 / 常用简称 |
| `INST_TYPE` | VARCHAR2(32) | 商业银行 / 证券公司 / 基金公司 / 保险 / 企业 |
| `REG_CAPITAL` | NUMBER(20,2) | 注册资本 |
| `FOUND_DATE` | DATE | 成立日期 |
| `LEGAL_PERSON` | VARCHAR2(100) | 法定代表人 |
| `LISTED_FLAG` | CHAR(1) | 是否上市（`stock` 域命中即 Y） |
| `UPDATE_TIME` | TIMESTAMP | 同步时间 |

### 4.2 `CP_RATING`（评级与信用资质）

| 字段 | 类型 | 来源 |
|---|---|---|
| `CP_ID` / `RATING_DATE` | — / DATE | 联合主键 |
| `AGENCY` | VARCHAR2(64) | `bond_special_data` 评级信息 |
| `ISSUER_RATING` | VARCHAR2(8) | 主体评级（AAA/AA+…） |
| `ISSUER_OUTLOOK` | VARCHAR2(16) | 评级展望 |
| `BOND_RATING` | VARCHAR2(8) | 债项评级 |
| `RATING_HISTORY` | CLOB | 评级变动记录 |

### 4.3 `CP_FINANCE`（财务与偿债能力）

| 字段 | 类型 | 来源 |
|---|---|---|
| `CP_ID` / `PERIOD` | — / VARCHAR2(8) | 报告期 `YYYYMMDD` |
| `TOTAL_ASSET` / `TOTAL_LIAB` | NUMBER(20,2) | `bond_financial_data` |
| `REVENUE` / `NET_PROFIT` | NUMBER(20,2) | 同上 |
| `DEBT_RATIO` / `ROE` | NUMBER(8,4) | 同上（资产负债率 / ROE） |
| `NPL_RATIO` / `CAP_ADEQUACY` | NUMBER(8,4) | 银行类对手方（公告/年报解析补充） |

### 4.4 `CP_TRADE_STAT`（交易行为统计）

| 字段 | 类型 | 来源 |
|---|---|---|
| `CP_ID` / `TRADE_DATE` | — / DATE | 联合主键 |
| `BOND_CODE` | VARCHAR2(32) | `bond_basic_info` |
| `DEAL_AMOUNT` | NUMBER(20,2) | `bond_market_data`（成交金额） |
| `AVG_YIELD` / `DURATION` | NUMBER(10,6) | 同上（收益率 / 久期） |
| `CD_ISSUE_AMT` | NUMBER(20,2) | 同业存单发行量（公告解析） |
| `TRADE_CNT` | NUMBER(10) | 成交笔数 |

### 4.5 `CP_RISK_EVENT`（风险事件）

| 字段 | 类型 | 来源 |
|---|---|---|
| `CP_ID` / `EVENT_ID` | — / VARCHAR2(64) | 联合主键 |
| `EVENT_TYPE` | VARCHAR2(32) | 被执行 / 失信 / 破产重整 / 行政处罚 / 经营异常 / 欠税 / 股权质押 |
| `EVENT_DATE` | DATE | `enterprise` 域返回 |
| `EVENT_AMOUNT` | NUMBER(20,2) | 执行标的 / 罚款金额 |
| `EVENT_DESC` | CLOB | 事件摘要 |
| `SOURCE` | VARCHAR2(64) | 数据来源标识（合规留痕） |

### 4.6 `CP_DOC_CACHE`（Redis 缓存键约定）

| Key | 结构 | 说明 |
|---|---|---|
| `cp:profile:{cp_id}` | Hash | 画像主档，TTL 24h |
| `cp:mcp:raw:{cp_id}:{tool}` | String(JSON) | MCP 原始返回，TTL 6h（供 Agent4 校验回溯） |
| `cp:doc:{file_hash}` | String(JSON) | 已解析文档内容（PDF/图片），TTL 12h |

---

## 五、对接注意事项（踩坑记录）

1. **鉴权头格式**：iFinD 用裸密钥而非 `Bearer`；聚源/百炼侧用 `Authorization: Bearer <key>`。两套不要混用。
2. **必须回传 `Mcp-Session-Id`**：漏传会导致后续 `tools/call` 返回 400/会话失效，需重新 initialize。
3. **`tools/list` 免费**，可用于健康检查与权益探测；**不要在循环里反复 initialize**（每次握手也算一次会话开销）。
4. **并发限制**：免费账号 2 req/s，四 Agent 协作时要串行化取数或加令牌桶。
5. **自然语言取数**：`query` 里主体与指标各不超过 5 个，超长会被截断；多指标失败时拆成单指标重试。
6. **银行间债券高频行情不可用**（文档明确：不支持银行间及经纪商数据），对手方若为银行，交易活跃度需改用公告/存单发行数据。
7. **合规留痕**：所有 MCP 返回必须带 `SOURCE` 字段落库，报告中注明“数据来源于同花顺 iFinD / 恒生聚源”。
8. **降级策略**：MCP 不可用时，Agent1 应读取本地 `data/reference_reports` 与 `cp:mcp:raw:*` 缓存，而不是直接失败。

---

## 六、参考来源

- 同花顺 iFinD MCP 官网与文档：<https://mcp.51ifind.com>、<https://mcp.51ifind.com/#/documents/guide-one-click>
- iFinD 官方 SKILL 安装说明（含端点与协议要点）：<https://mcp.51ifind.com/gwstatic/static/ds_web/ifind-mcp-web/skills/SKILL_INSTALL_GUIDE.md>
- iFinD 金融数据 SKILL 包（工具清单与参数，本次已下载核验）：<https://s.thsi.cn/cd/ifind-java-ds-bff-web-container/ifind-mcp-web/skills/ifind-finance-data-1.5.1.zip>
- 恒生聚源官网（数据地图 / MCP）：<https://www.gildata.com/>
- 上海证券报：恒生电子数据地图 MCP 开放（450+ 接口）：<http://www.cnstock.com/commonDetail/805496>
- 证券日报：恒生聚源金融综合问数 MCP 上架阿里云百炼：<http://m.zqrb.cn/gscy/gongsi/2026-04-07/A1775556978130.html>
- 阿里云百炼 MCP 接入文档：<https://docs.bailian.console.aliyun.com/zh/model-studio/official-and-third-party-mcp>
