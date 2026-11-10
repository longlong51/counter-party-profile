# iFinD Finance Data Skill

面向 AI Agent 的同花顺 iFinD 数据查询 Skill，通过按需加载的业务文档与内置请求模块，连接远程 MCP 服务，获取金融、企业及法律数据。

## 项目结构

```text
.
├── SKILL.md                       # Agent 入口：能力说明、文档路由与执行约定
├── references/                    # 按业务拆分的工具说明、参数与调用示例
│   └── version.md                 # 版本检查与更新流程
├── scripts/
│   ├── call-node.js               # Node.js 请求模块
│   └── call.py                    # Python 请求模块
└── config/
    ├── mcp_config.json            # MCP 鉴权配置（auth_token）
    └── skill_env_config.json      # Agent 环境配置（agent_name）
```

## 调用流程

```text
用户需求 → SKILL.md 路由 → references/ 业务文档
         → Agent 调用请求模块 → 远程 MCP 服务 → 返回数据
```

Agent 根据业务文档选择服务、工具及参数，通过模块导出的 `call()` 发起请求。请求模块负责配置读取、鉴权、MCP 会话管理、参数校验与响应封装；两种语言实现共用配置。

## 运行条件

- Node.js（推荐，仅使用内置模块），或 Python + `requests`。
- 在 `config/mcp_config.json` 中配置有效的 `auth_token`，并确保可访问远程 MCP 服务。

业务能力、环境配置约定及调用示例见 [SKILL.md](SKILL.md)；各业务接口详见 [references/](references/)。
