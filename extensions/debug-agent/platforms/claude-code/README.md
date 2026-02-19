# RenderDoc Debug Agent Plugin

## 概述

RenderDoc调试专家套件，提供完整的渲染问题排查能力。

## 安装方式

将本目录复制到Claude Code的plugins目录：
- macOS: `~/Library/Application Support/Claude/plugins/`
- Linux: `~/.config/Claude/plugins/`
- Windows: `%APPDATA%/Claude/plugins/`

## 包含内容

### Subagents（子代理）

| Agent | 功能 | 工具 |
|-------|------|------|
| triage_agent | 分诊专家 | rdx-mcp全部工具 |
| pixel_forensics_agent | 像素取证 | rdx texture/event工具 |
| shader_analysis_agent | Shader分析 | rdx shader工具 |
| capture_repro_agent | 捕获复现 | rdx capture工具 |

### Skills（技能）

| Skill | 功能 | 触发词 |
|-------|------|--------|
| nan_debug | NaN问题排查 | white_spot, nan, flickering |
| geo_debug | 几何问题排查 | object_missing, backface |
| color_debug | 颜色问题排查 | color_too_dark, gamma |

### MCP服务器

- **rdx-mcp**: RenderDoc API封装

## 使用方式

```
# 触发分诊
> 帮我排查画面中的白色斑点

# 手动指定Agent
/agent Triage Agent

# 手动触发Skill
@skill nan-debug
```

## 依赖

- rdx-mcp: `pip install rdx-mcp`
- RenderDoc
