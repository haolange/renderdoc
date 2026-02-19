# SOP-GEO-01: 几何问题排查

## 概述

本技能模块处理渲染中的几何问题。

## 触发条件

症状标签: `object_missing`, `backface_culling`, `frustum_culling`, `negative_scale`, `clipping`

违反不变量: I-GEO-01

## 排查流程

### 阶段1: 识别问题类型

**工具链**:
- `rdx.event.get_actions` - 获取Draw Call列表
- `rdx.pipeline.get_raster_state` - 获取光栅化状态

### 阶段2: 分析剔除状态

- 背面剔除模式
- 负缩放检测
- 视锥体裁剪

### 阶段3: 修复建议

```hlsl
float det = determinant(worldMatrix);
CullMode cull = (det < 0) ? CullMode::Front : CullMode::Back;
```

### 阶段4: 验证

修复后重新渲染确认物体正常显示
