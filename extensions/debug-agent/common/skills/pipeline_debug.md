# SOP-PIPELINE-01: 管线状态问题排查

## 概述

本技能模块处理渲染管线配置相关问题。

## 触发条件

症状标签: `pipeline_error`, `state_mismatch`, `blend_error`, `raster_error`

## 排查流程

### 阶段1: 管线状态获取

**工具链**:
- `rdx.pipeline.get_state` - 获取完整管线状态
- `rdx.pipeline.get_blend_state` - 获取混合状态
- `rdx.pipeline.get_raster_state` - 获取光栅状态
