# SOP-COLOR-01: 颜色问题排查

## 概述

本技能模块处理颜色相关的渲染问题。

## 触发条件

症状标签: `color_too_dark`, `color_too_bright`, `gamma_error`, `hdr_issue`, `color_distortion`

违反不变量: I-COLOR-01

## 排查流程

### 阶段1: 颜色空间诊断

**工具链**:
- `rdx.texture.get_data` - 获取纹理数据
- `rdx.pipeline.get_blend_state` - 检查混合状态

### 阶段2: 根因分析

- 双重Gamma校正（画面过暗）
- 缺失Gamma校正（画面过亮）
- 混合空间不匹配

### 阶段3: 修复建议

```hlsl
// 正确流程
float3 color = texColor; // 已经是Linear
color *= exposure;
// 输出时
output = pow(linearColor, 1.0/2.2);
```

### 阶段4: 验证

验证颜色值在预期范围
