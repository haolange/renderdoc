# SOP-DEPTH-01: 深度问题排查

## 概述

本技能模块处理深度缓冲相关问题。

## 触发条件

症状标签: `depth_artifacts`, `z-fighting`, `depth_precision`, `occlusion`

违反不变量: I-DEPTH-01

## 排查流程

### 阶段1: 深度状态检查

**工具链**:
- `rdx.pipeline.get_depth_state` - 获取深度状态
- `rdx.texture.get_data` - 获取深度缓冲数据

### 阶段2: 精度分析

检查近平面/远平面设置是否合理
