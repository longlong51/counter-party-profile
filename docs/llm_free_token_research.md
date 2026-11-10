# 免费 Token 大模型调研与接入方案

- 调研时间：2026-11-09（Asia/Shanghai）
- 目标：为「AI 交易助手」找到**零成本或低成本可长期跑通**的 LLM 服务，且必须兼容 OpenAI SDK，便于在 LangGraph 中统一封装。
- 结论：主用 **智谱 GLM-4.7-Flash（永久免费，200K 上下文）**，视觉/OCR 场景用 **GLM-4.6V-Flash（免费）**；备用 **阿里云百炼 Qwen 系列（新人每模型约 100 万 tokens，90 天）** 与 **硅基流动（新用户赠金 + 部分限免模型）**。

---

## 一、候选对比

| 服务商 | 免费模型/额度 | 上下文 | 多模态 | 有效期 | 并发/限流 | OpenAI 兼容 |
|---|---|---|---|---|---|---|
| 智谱 BigModel | **GLM-4.7-Flash**：输入/输出/缓存全免费；**GLM-4.6V-Flash** 视觉模型免费 | 200K（4.7-Flash） | 文本 + 视觉（4.6V） | **长期有效**（免费模型政策） | 免费档有 QPS 限制，实测够单机开发 | 是 |
| 阿里云百炼 | 新人开通后各模型独立约 **100 万 tokens**，合计千万级 | Qwen 系列 32K–128K+ | qwen-vl 多模态 | 90 天 | 按模型档位限流 | 是（compatible-mode） |
| 硅基流动 SiliconFlow | 新用户 **16 元 / 1 美元赠金**；多款模型**限时免费**（如 Nex-N2-Pro、DeepSeek-V4-Flash 系列部分档） | 依模型 | 部分支持 | 赠金长期、限免随官方调整 | 免费档并发低 | 是 |
| 火山方舟（豆包） | 新用户赠 token（额度随活动调整，需登录确认） | 依模型 | 是 | 活动期 | 按套餐 | 是 |

> 说明：**DeepSeek 官方 API 无免费额度**（需充值），故未列入主选；Gemini / Groq 等海外服务存在网络可达性问题，本项目不作为默认链路，仅在 `LLM_PROVIDER=gemini` 时可选。

---

## 二、各平台接入方法与 API Key 申请

### 2.1 智谱 BigModel（推荐主力）

1. 访问 <https://bigmodel.cn> → 注册/登录 → 完成**实名认证**（个人身份证或企业认证）
2. 控制台「API Keys」→ 新建 API Key，复制保存（`xxxxxxxx.xxxxxxxx` 形式）
3. 计费：GLM-4.7-Flash、GLM-4.6V-Flash 为免费模型，无需充值；付费模型需先充值
4. Endpoint（OpenAI 兼容）：`https://open.bigmodel.cn/api/paas/v4`
5. 模型名：`glm-4.7-flash`（文本）、`glm-4.6v-flash`（视觉）

### 2.2 阿里云百炼（备用/长文本）

1. 访问 <https://bailian.console.aliyun.com> → 用阿里云账号登录 → **开通百炼** → 完成实名认证
   - 注意：**仅华北2（北京）地域模型享有新人免费额度**，其他地域无
2. 控制台「API-KEY 管理」→ 创建 API Key
3. 免费额度：开通即自动发放，**每模型约 100 万 tokens、有效期 90 天**；建议在控制台开启「**免费额度用完即停**」，避免超额扣费（超额后报 `AllocationQuota.FreeTierOnly`）
4. Endpoint（OpenAI 兼容）：`https://dashscope.aliyuncs.com/compatible-mode/v1`
5. 模型名：`qwen-plus`、`qwen-max`、`qwen-vl-max`（视觉/OCR）

### 2.3 硅基流动 SiliconFlow（备用/限免模型）

1. 访问 <https://cloud.siliconflow.cn> → 注册（手机号）→ 实名认证
2. 「API 密钥」→ 新建密钥
3. 新用户赠金 16 元，可用于全部模型；「模型广场」中标注「免费」的模型可直接调用
4. Endpoint：`https://api.siliconflow.cn/v1`；模型名形如 `Qwen/Qwen3-32B`、`deepseek-ai/DeepSeek-V3.2`

### 2.4 关于「实际申请 API Key」的说明

- 申请动作需要**登录 + 实名认证 + 手机验证**，属于只能由账号持有人完成的步骤；自动化流程无法代替用户注册。
- 因此本项目按用户约定采用**占位字符串**，代码链路完整可运行，替换真实 key 即可：

```
LLM_PROVIDER=zhipu
LLM_API_KEY=sk-placeholder-zhipu-key
LLM_BASE_URL=https://open.bigmodel.cn/api/paas/v4
LLM_MODEL=glm-4.7-flash
LLM_VISION_MODEL=glm-4.6v-flash
LLM_FALLBACK_API_KEY=sk-placeholder-dashscope-key
LLM_FALLBACK_BASE_URL=https://dashscope.aliyuncs.com/compatible-mode/v1
LLM_FALLBACK_MODEL=qwen-plus
```

- 上述 key 一经替换即生效，无需改代码（配置集中在 `app/core/config.py` + `.env`）。

---

## 三、统一接入代码（OpenAI SDK，三家通用）

```python
from openai import OpenAI

# 换 base_url + model 即可在智谱 / 百炼 / 硅基流动之间切换
client = OpenAI(api_key="sk-placeholder-zhipu-key",
                base_url="https://open.bigmodel.cn/api/paas/v4")

resp = client.chat.completions.create(
    model="glm-4.7-flash",
    messages=[{"role": "system", "content": "你是交易对手方风险分析助手"},
              {"role": "user", "content": "解释一下 DVP 结算的含义"}],
    temperature=0.3,
    max_tokens=1024,
)
print(resp.choices[0].message.content)
```

流式（前端聊天页使用）：

```python
stream = client.chat.completions.create(model="glm-4.7-flash",
                                        messages=[{"role": "user", "content": "你好"}],
                                        stream=True)
for chunk in stream:
    delta = chunk.choices[0].delta.content
    if delta:
        yield delta
```

视觉（图片 OCR / 图表理解，供 PDF 图片解析兜底）：

```python
resp = client.chat.completions.create(
    model="glm-4.6v-flash",
    messages=[{
        "role": "user",
        "content": [
            {"type": "text", "text": "提取这张财报截图里的表格，输出 Markdown 表格"},
            {"type": "image_url", "image_url": {"url": "data:image/jpeg;base64,<BASE64>"}},
        ],
    }],
)
```

---

## 四、工程化要点

1. **主备切换**：`LLM_PROVIDER` 主链路失败（429/5xx/超时）时，自动切到 fallback provider，并在响应里标注 `degraded=true`。
2. **限流与重试**：免费档 QPS 低，需指数退避重试（最多 3 次，1s/2s/4s），并对同一会话做请求合并。
3. **Token 预算**：问答链路控制 `retrieved_chunks ≤ 6`、每 chunk ≤ 400 字；报告生成分段落多次调用，避免单次超长。
4. **缓存**：相同问题的最终回答写入 Redis（`qa:answer:{hash}`，TTL 1h），省钱也提速。
5. **可观测**：记录 provider、model、prompt_tokens、completion_tokens、latency，落到 `logs/llm_calls.jsonl`，便于核算免费额度消耗。
6. **合规**：调用日志不得记录用户上传的原始证件号、账号密码；报告输出需标注“由大模型生成，仅供参考”。

---

## 五、为什么选 GLM-4.7-Flash 作主力

- **永久免费**、无需按量充值，适合长期演示与本地跑测；
- 200K 上下文，可一次塞入多份检索 chunk 与对手方档案；
- 提供**免费视觉模型**，正好覆盖本项目「图片型 PDF → OCR」的兜底路径，无需额外买 OCR 服务；
- OpenAI 兼容，LangGraph / LangChain 的 `ChatOpenAI` 可直接用，不引入额外适配层。

---

## 六、参考来源

- 智谱定价与免费模型说明：<https://bigmodel.cn/pricing>
- GLM-4.7-Flash 上线公告（替代 4.5-Flash 并免费）：<https://www.zhipuai.cn/zh/news/148>
- GLM-4.5-Flash 免费模型文档（历史参考）：<https://zhipu-ef7018ed.mintlify.app/cn/guide/models/free/glm-4.5-flash>
- 阿里云百炼新人免费额度规则：<https://help.aliyun.com/zh/model-studio/new-free-quota>
- 阿里云百炼 API Key 与调用指南（社区实测）：<https://developer.aliyun.com/article/1772653>
- 硅基流动新用户赠金与免费模型：<https://www.siliconflow.cn/news/zaw07rwkzh2ddssh2ib8rjzj>
- 硅基流动 OpenAI 兼容接入：<https://docs.siliconflow.cn/docs/userguide/capabilities/text-generation>
