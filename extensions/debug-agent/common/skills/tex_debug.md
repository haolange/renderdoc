# SOP-TEX-01: 纹理问题排查

## 概述

本技能模块处理纹理相关问题。

## 触发条件

症状标签: `texture_missing`, `texture_distortion`, `mipmap_issue`, `uv_error`

违反不变量: I-TEX-01, I-MIP-01

## 排查流程

### 阶段1: 纹理诊断

**工具链**:
- `rdx.resource.get_details` - 获取纹理详情
- `rdx.pipeline.get_shader_resource` - 获取SRV绑定

### 阶段2: UV分析

检查UV坐标计算是否正确

### 阶段3: Mipmap检查

检查Mipmap层级是否正确

## 输出格式

```json
{
  "skill_id": "SOP-TEX-01",
  "status": "completed"
}
```
