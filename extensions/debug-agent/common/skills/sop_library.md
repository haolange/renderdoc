# SOP Library 标准操作流程库

## 概述

本文档定义了 RenderDoc Debug Agent 框架中使用的标准操作流程（SOP）。每个 SOP 提供了完整的工具链、详细的检查点和执行步骤。

---

## SOP-NAN-01: NaN/Inf 问题排查

### 基本信息

```yaml
sop_id: SOP-NAN-01
name: NaN/Inf 问题排查
category: 数值问题
severity: CRITICAL
invariant: I-NAN-01

trigger_tags:
  - white_spot
  - nan_propagation
  - infinite_value
  - pixel_overflow
  - flickering
  - flash
```

### 完整工具链

| 阶段 | 工具 | 参数 | 输出 |
|------|------|------|------|
| 定位 | `rdx.texture.get_data` | texture_id, x, y | 原始像素值 |
| 定位 | `rdx.event.get_pixels` | event_id, x, y | 像素历史 |
| 定位 | `rdx.frame.take_screenshot` | - | 渲染结果截图 |
| 追踪 | `rdx.pipeline.get_state` | event_id | 管线状态 |
| 追踪 | `rdx.shader.get_debug` | event_id, x, y | Shader 调试 |
| 分析 | `rdx.api.get_log` | from, to | API 调用日志 |

### 详细执行流程

#### 阶段 1: 定位问题区域

```
1. 使用 Screenshot 识别异常像素位置
   - 观察: 全白像素、过曝区域
   
2. 使用 Pixel History 定位首次出现位置
   - 工具: rdx.event.get_pixels
   - 输出: 像素变化历史
   
3. 检查像素值是否包含 NaN/Inf
   - NaN 特征: 所有分量显示为白色
   - Inf 特征: 超亮或超暗
```

#### 阶段 2: 追踪 NaN 来源

```
1. 逆向追踪 Pixel History
   - 从最终输出向前追溯
   - 找到首次出现 NaN 的 DrawCall
   
2. 检查各 Shader 阶段的输出
   - Vertex Shader 输出
   - Fragment Shader 输出
   
3. 检查管线状态
   - Render Target 格式
   - Depth/Stencil 状态
```

#### 阶段 3: 根因分析

```
常见根因检查清单:
- [ ] normalize(零向量) - 需要检查 normalize() 输入
- [ ] 0/0 除零操作 - 需要检查除数
- [ ] sqrt(负数) - 需要检查被开方数
- [ ] log(负数) - 需要检查对数输入
- [ ] pow(负数, 非整数) - 需要检查指数
- [ ] 极端溢出 - 检查数值范围
```

#### 阶段 4: 修复建议

```hlsl
// 安全归一化
float3 SafeNormalize(float3 v) {
    float len = length(v);
    return len > 0.0001f ? v / len : float3(0, 1, 0);
}

// 安全除法
float SafeDivide(float a, float b) {
    return abs(b) > 0.0001f ? a / b : 0.0f;
}

// 安全平方根
float SafeSqrt(float x) {
    return sqrt(max(0.0f, x));
}
```

#### 阶段 5: 验证

```
1. 修复 Shader 代码
2. 重新捕获帧
3. 验证问题区域是否正常
4. 确认所有 DrawCall 无 NaN 传播
```

### 输出格式

```json
{
  "skill_id": "SOP-NAN-01",
  "status": "completed",
  "matched_invariant": "I-NAN-01",
  "root_cause": "normalize(0) -> NaN",
  "fix": "使用 SafeNormalize",
  "evidence": {
    "first_nan_event": 1247,
    "shader_stage": "Vertex Shader",
    "pixel_location": [512, 384]
  }
}
```

---

## SOP-COLOR-01: 颜色失真问题排查

### 基本信息

```yaml
sop_id: SOP-COLOR-01
name: 颜色失真问题排查
category: 颜色问题
severity: HIGH
invariant: I-COLOR-01

trigger_tags:
  - color_too_dark
  - color_too_bright
  - gamma
  - washed_out
  - color_distortion
```

### 完整工具链

| 阶段 | 工具 | 参数 | 输出 |
|------|------|------|------|
| 观察 | `rdx.frame.take_screenshot` | - | 渲染结果 |
| 检查 | `rdx.pipeline.get_state` | event_id | RT 格式 |
| 检查 | `rdx.texture.get_info` | texture_id | 纹理格式 |
| 分析 | `rdx.shader.get_source` | event_id | Shader 源码 |
| 验证 | `rdx.shader.get_debug` | event_id, x, y | Shader 输出 |

### 详细执行流程

#### 阶段 1: 颜色空间检查

```
1. 检查 Render Target 格式
   - 工具: rdx.pipeline.get_state
   - 目标: R8G8B8A8_UNORM vs R8G8B8A8_UNORM_SRGB
   
2. 如果是 sRGB 格式
   - 硬件会自动执行 sRGB -> Linear
   - Shader 应该输出 Linear 空间颜色
   
3. 如果是 LINEAR 格式
   - 硬件不做转换
   - Shader 需要手动执行 gamma 校正
```

#### 阶段 2: Gamma 链检查

```
1. 检查 Shader 中的颜色处理
   - 查找 pow(color, 2.2) 或 pow(color, 1/2.2)
   - 确认是否与 RT 格式匹配
   
2. 常见错误:
   - 双重 gamma: RT=sRGB + 手动 pow(color, 2.2)
   - 缺失 gamma: RT=LINEAR + 无转换
   
3. 正确做法:
   - 方案 A: RT = sRGB, Shader = Linear 输出
   - 方案 B: RT = LINEAR, Shader = sRGB 输出
```

#### 阶段 3: HDR 检查

```
1. 检查是否为 HDR 内容
   - 颜色值 > 1.0
   - RT 格式支持 HDR
   
2. HDR 处理:
   - 需要 Tone Mapping
   - 需要 OETF/EOTF 转换
```

### 修复模板

```hlsl
// 方案 1: 使用 sRGB Render Target (推荐)
Texture2D<float4> colorTex : register(t0);
SamplerState linearSampler : register(s0);

// Shader 直接输出 Linear 颜色
float4 main(PS_INPUT input) : SV_Target {
    float3 color = colorTex.Sample(linearSampler, input.uv);
    return float4(color, 1.0);  // sRGB RT 会自动转换
}

// 方案 2: 手动处理
float4 main(PS_INPUT input) : SV_Target {
    float3 linear = colorTex.Sample(linearSampler, input.uv);
    float3 srgb = pow(linear, 1.0/2.2);  // 手动转换
    return float4(srgb, 1.0);  // LINEAR RT
}
```

---

## SOP-GEO-01: 物体消失问题排查

### 基本信息

```yaml
sop_id: SOP-GEO-01
name: 物体消失问题排查
category: 几何问题
severity: HIGH
invariant: I-GEO-01

trigger_tags:
  - object_missing
  - object_invisible
  - backface
  - culling
  - frustum
```

### 完整工具链

| 阶段 | 工具 | 参数 | 输出 |
|------|------|------|------|
| 检查 | `rdx.pipeline.get_state` | event_id | 管线状态 |
| 检查 | `rdx.mesh.get_data` | object_id | 顶点数据 |
| 检查 | `rdx.buffer.get_data` | buffer_id | 矩阵缓冲 |
| 分析 | `rdx.api.get_log` | from, to | API 调用 |
| 调试 | `rdx.shader.get_debug` | event_id | VS 输出 |

### 详细执行流程

#### 阶段 1: 视锥体检查

```
1. 获取顶点着色器输入
   - 检查 Vertex Buffer 数据
   - 验证顶点位置
   
2. 检查 MVP 矩阵
   - Model Matrix
   - View Matrix
   - Projection Matrix
   
3. 计算视锥体裁剪
   - 顶点变换: clipPos = MVP * worldPos
   - 检查: -w <= x,y,z <= w
```

#### 阶段 2: 剔除设置检查

```
1. 检查 Rasterizer State
   - Cull Mode: Front/Back/None
   - Front Counter Clockwise
   
2. 检查负缩放问题
   - World Matrix 行列式 < 0
   - 导致剔除方向反转
   
3. 检查深度
   - Near/Far 平面设置
   - Depth Bias
```

#### 阶段 3: 特殊问题排查

```
1. 背面剔除异常
   - 关闭背面剔除测试
   - 观察物体是否出现
   
2. 深度问题
   - 深度写入/测试设置
   - 深度缓冲格式
   
3. 索引问题
   - 索引越界
   - 索引顺序错误
```

### 修复模板

```hlsl
// 动态调整剔除模式
CullMode DetermineCullMode(float4x4 worldMatrix) {
    float det = determinant(worldMatrix);
    return det < 0 ? CullMode::Front : CullMode::Back;
}

// 确保正确的视锥体设置
void ValidateFrustum(float4x4 viewProj) {
    // near 必须小于 far
    // 建议: near = 0.1, far = 1000
}
```

---

## SOP-TEX-01: 纹理采样问题排查

### 基本信息

```yaml
sop_id: SOP-TEX-01
name: 纹理采样问题排查
category: 纹理问题
severity: HIGH
invariant: I-TEX-01, I-MIP-01

trigger_tags:
  - texture_missing
  - uv_error
  - mipmap
  - blurry
  - stretched
  - tiling
```

### 完整工具链

| 阶段 | 工具 | 参数 | 输出 |
|------|------|------|------|
| 检查 | `rdx.texture.get_info` | texture_id | 纹理信息 |
| 检查 | `rdx.texture.get_data` | texture_id, mip, slice | 纹理数据 |
| 检查 | `rdx.pipeline.get_state` | event_id | Sampler 状态 |
| 调试 | `rdx.shader.get_debug` | event_id, x, y | UV 值 |
| 对比 | `rdx.frame.compare` | event1, event2 | 帧对比 |

### 详细执行流程

#### 阶段 1: 纹理基础检查

```
1. 检查纹理是否存在
   - 工具: rdx.texture.get_info
   - 验证: 宽度、高度、Mip 级别
   
2. 检查纹理格式
   - 格式: R8G8B8A8, BC1, BC7 等
   - 通道顺序: RGBA vs BGRA
   
3. 检查纹理数据
   - 是否有内容
   - 是否为黑/白/错误颜色
```

#### 阶段 2: UV 坐标检查

```
1. 获取 UV 坐标
   - 工具: rdx.shader.get_debug
   - 检查: UV 范围是否合理
   
2. 常见 UV 问题:
   - UV 超出 [0,1] 范围
   - UV 计算错误
   - UV 坐标未归一化
   
3. 寻址模式检查
   - Clamp vs Repeat
   - Mirror
```

#### 阶段 3: Mipmap 检查

```
1. 检查 Mipmap 状态
   - 是否启用
   - Mip 级别是否正确
   
2. 常见 Mip 问题:
   - 禁用 Mip 导致模糊
   - LOD Bias 过大
   - Mipmap 链不完整
   
3. 各向异性检查
   - Anisotropy 级别
   - 是否被正确应用
```

### 修复模板

```hlsl
// 正确的纹理采样
Texture2D<float4> tex : register(t0);
SamplerState sam : register(s0);

// 使用显式 LOD
float4 main(PS_INPUT input) : SV_Target {
    float lod = 0;  // 或动态计算
    return tex.SampleLevel(sam, input.uv, lod);
}

// Mipmap 偏差调整
SamplerState samAniso {
    Filter = ANISOTROPIC;
    MaxAnisotropy = 16;
    MipLODBias = 0;  // 调整此值
};
```

---

## SOP-DEPTH-01: 深度问题排查

### 基本信息

```yaml
sop_id: SOP-DEPTH-01
name: 深度问题排查
category: 深度问题
severity: HIGH
invariant: I-DEPTH-01, I-DEPTH-02

trigger_tags:
  - z_fighting
  - depth_error
  - occlusion
  - far_plane
  - near_plane
```

### 完整工具链

| 阶段 | 工具 | 参数 | 输出 |
|------|------|------|------|
| 可视化 | `rdx.texture.get_data` | texture_id=Depth | 深度缓冲|
| 检查 | `rdx.pipeline.get_state` | event_id | Depth Stencil |
| 调试 | `rdx.shader.get_debug` | event_id | Depth 输出 |
| 分析 | `rdx.event.get_pixels` | event_id | 深度历史 |

### 详细执行流程

#### 阶段 1: 深度缓冲检查

```
1. 可视化深度缓冲
   - 工具: Texture Viewer
   - 观察: 深度值分布
   
2. 检查深度格式
   - D24_UNORM_S8_UINT
   - D32_FLOAT
   - 精度是否足够
   
3. 检查深度范围
   - Near Plane 距离
   - Far Plane 距离
   - 建议: far/near < 10000
```

#### 阶段 2: 深度状态检查

```
1. 检查 Depth Stencil State
   - Depth Enable
   - Depth Write Mask
   - Depth Func
   
2. 常见问题:
   - 深度测试被禁用
   - 深度写入被禁用
   - 深度函数错误 (Less vs Greater)
```

#### 阶段 3: Z-Fighting 排查

```
1. 识别 Z-Fighting
   - 表面闪烁
   - 深度值非常接近
   
2. 解决方案:
   - 增加深度精度
   - 调整物体位置
   - 使用 Polygonal Offset
```

### 修复模板

```hlsl
// Polygonal Offset 避免 Z-Fighting
RasterizerState {
    DepthBias = 100;  // 根据需要调整
    DepthBiasClamp = 0.0f;
    SlopeScaledDepthBias = 1.0f;
};

// 推荐深度范围设置
Matrix projection = PerspectiveFov(
    fov, aspect, 
    0.1f,   // near: 不要太小
    1000.0f // far: far/near < 10000
);
```

---

## SOP-PREC-01: 精度问题排查

### 基本信息

```yaml
sop_id: SOP-PREC-01
name: 精度问题排查
category: 精度问题
severity: MEDIUM
invariant: I-PREC-01, I-PREC-02

trigger_tags:
  - banding
  - quantization
  - precision
  - artifact
  - gradient
```

### 完整工具链

| 阶段 | 工具 | 参数 | 输出 |
|------|------|------|------|
| 观察 | `rdx.frame.take_screenshot` | - | 渲染结果 |
| 检查 | `rdx.pipeline.get_state` | event_id | RT 格式 |
| 调试 | `rdx.shader.get_debug` | event_id, x, y | 输出值 |
| 分析 | `rdx.texture.get_data` | texture_id | 纹理内容 |

### 详细执行流程

#### 阶段 1: 精度损失识别

```
1. 观察颜色渐变区域
   - 是否存在条带(Banding)
   - 是否存在量化噪点
   
2. 检查 Render Target 格式
   - LDR: 8-bit 容易出现条带
   - HDR: 16-bit/32-bit 更好
   
3. 检查顶点/像素 Shader 精度
   - float vs half vs fixed
```

#### 阶段 2: 解决方案

```
1. 提升精度:
   - 使用更高精度 RT
   - 使用 float 类型
   
2. 添加抖动:
   - 启用 Dithering
   - 添加随机噪声
   
3. 后处理:
   - Film Grain
   - 颜色量化
```

---

## SOP-PERF-01: 性能问题排查

### 基本信息

```yaml
sop_id: SOP-PERF-01
name: 性能问题排查
category: 性能问题
severity: MEDIUM
invariant: I-PERF-01

trigger_tags:
  - slow
  - stall
  - timeout
  - fps_drop
  - hitch
```

### 完整工具链

| 阶段 | 工具 | 参数 | 输出 |
|------|------|------|------|
| 分析 | `rdx.api.get_log` | from, to | API 日志 |
| 检查 | `rdx.pipeline.get_state` | event_id | 状态 |
| 分析 | `rdx.resource.get_transitions` | - | 资源转换 |
| 计时 | `rdx.timing.get_data` | - | 计时数据 |

### 详细执行流程

#### 阶段 1: 瓶颈识别

```
1. 分析 API 调用时间
   - 工具: API Inspector
   - 查找: 耗时长的调用
   
2. 检查 Pipeline Stall
   - 资源状态转换
   - 同步操作
   
3. 常见瓶颈:
   - 纹理绑定
   - 状态转换
   - 内存拷贝
```

#### 阶段 2: 优化建议

```
1. 减少状态转换:
   - 批量相似 DrawCall
   - 使用固定管线状态
   
2. 优化资源:
   - 使用纹理数组
   - 预加载资源
   
3. 减少同步:
   - 延迟删除
   - 多重缓冲
```

---

## SOP 快速参考表

| SOP ID | 名称 | 主要工具 | 典型症状 |
|--------|------|----------|----------|
| SOP-NAN-01 | NaN/Inf 排查 | Pixel History, Shader Debug | 全白像素 |
| SOP-COLOR-01 | 颜色失真 | RT Format, Shader Source | 颜色异常 |
| SOP-GEO-01 | 物体消失 | Pipeline State, Matrix | 物体不显示 |
| SOP-TEX-01 | 纹理采样 | Texture Viewer, UV Debug | 纹理错误 |
| SOP-DEPTH-01 | 深度问题 | Depth Viewer, Pipeline | Z-Fighting |
| SOP-PREC-01 | 精度问题 | Screenshot, RT Format | 条带 |
| SOP-PERF-01 | 性能问题 | API Log, Timing | 卡顿 |

---

**相关文档**:
- [Invariant Library](../invariants/invariant_library.md)
- [Hypothesis Board](../docs/hypothesis_board.md)
- [Quality Hooks](../docs/quality_hooks.md)
- [BugCard Template](../cases/bugcards/bugcard_template.md)
