# Renderdoc Debug Agent Framework

一套面向渲染调试的AI Agent资源体系，基于 **skill + docs + search tool + MCP** 模式。

## 9个Agent定义

| # | Agent | 角色 |
|---|-------|------|
| 1 | Team Lead | 协调者，任务分解与最终裁决 |
| 2 | Triage & Taxonomy | 症状分类专家 |
| 3 | Capture & Repro | 捕获与复现专家 |
| 4 | Pass Graph / Pipeline | 渲染管线分析专家 |
| 5 | Pixel/Value Forensics | 像素/数值取证专家 |
| 6 | Shader & IR | 着色器与IR分析专家 |
| 7 | Driver/Device Specialist | 驱动/设备专家 |
| 8 | Skeptic | 对抗式审查者 |
| 9 | Report & Knowledge Curator | 报告与知识策展者 |

## 目录结构

```
debug-agent/
├── common/                          # 公共资源
│   ├── invariants/                 # 不变量库
│   ├── skills/                     # SOP技能库
│   ├── cases/
│   │   ├── bugcards/               # BugCard模板和示例
│   │   └── bugfulls/              # BugFull模板和示例
│   ├── taxonomy/                   # 分类学
│   │   ├── symptom_taxonomy.md     # 症状分类
│   │   └── trigger_taxonomy.md    # 触发条件分类
│   └── project_plugin/            # 项目私有知识插件
│
├── platforms/                       # 平台适配
│   ├── claude-code/                # 9个Agent
│   ├── claude-work/                # 9个Agent + plugin
│   ├── minimax/                    # 9个Expert
│   ├── code-buddy/                 # 9个Subagents
│   └── manus/                      # Workflow
│
├── docs/                            # 规范文档
│   ├── agent_collaboration.md
│   └── case_specification.md
│
├── config/                          # 配置文件
│   └── mcp_tools.json
│
└── design/                          # 原始设计文档
```

## 资源类型

| 类型 | 位置 | 用途 |
|------|------|------|
| Invariants | common/invariants/ | 搜索不变量 |
| Skills | common/skills/ | SOP技能 |
| BugCard | common/cases/bugcards/ | 轻量检索 |
| BugFull | common/cases/bugfulls/ | 完整报告 |
| Taxonomy | common/taxonomy/ | 分类体系 |
| Project Plugin | common/project_plugin/ | 项目私有知识 |

## 平台适配

| 平台 | Agents | Skills | 位置 |
|------|--------|--------|------|
| Claude Code | 9 | common引用 | platforms/claude-code/ |
| Claude Work | 9 | common引用 | platforms/claude-work/ |
| MiniMax | 9 | common引用 | platforms/minimax/ |
| Code Buddy | 9 | - | platforms/code-buddy/ |
| Manus | 1 | - | platforms/manus/ |
