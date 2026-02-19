# SOP-NAN-01: NaN/Inf问题排查

## 概述

本技能模块提供系统化的NaN/Inf问题排查流程。

## 触发条件

症状标签: `white_spot`, `nan_propagation`, `infinite_value`, `pixel_overflow`, `flickering`

违反不变量: I-NAN-01

## 排查流程

### 阶段1: 定位问题

**工具链**:
- `rdx.texture.get_data` - 获取问题像素原始数据
- `rdx.event.get_pixels` - 获取像素历史

**检查点**:
- 像素值是否包含 NaN/Inf
- RGB分量是否超出 [0, 1] 范围

### 阶段2: 追踪来源

使用 `rdx.event.get_pixels` 追踪NaN来源：
- 异常值首次出现的阶段
- 传播路径

### 阶段3: 根因分析

常见根因:
- `normalize(0)` → NaN
- `0/0` → NaN
- `sqrt(负数)` → NaN

### 阶段4: 修复建议

```hlsl
float3 SafeNormalize(float3 v) {
    float len = length(v);
    return len > 0.0001f ? v / len : float3(0, 1, 0);
}
```

### 阶段5: 验证

修复后重新检查问题区域

## 输出格式

```json
{
  "skill_id": "SOP-NAN-01",
  "status": "completed",
  "matched_invariant": "I-NAN-01",
  "root_cause": "normalize(0) -> NaN",
  "fix": "使用SafeNormalize"
}
```
