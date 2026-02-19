# 图形不变量库

## 概述

不变量（Invariant）是图形渲染中必须始终成立的基本约束。当违反不变量时，会产生各种渲染异常。本库定义了 RenderDoc 调试框架中使用的核心不变量，为问题诊断提供理论依据。

## 搜索标签速查

| 标签 | 不变量 |
|------|--------|
| `nan`, `white_spot`, `flickering`, `flash` | I-NAN-01, I-NAN-02 |
| `object_missing`, `backface`, `culling`, `inverted` | I-GEO-01, I-GEO-02 |
| `color_too_dark`, `gamma`, `color_distortion`, `washed_out` | I-COLOR-01, I-COLOR-02 |
| `texture`, `uv`, `mipmap`, `blurry` | I-TEX-01, I-MIP-01, I-LOD-01 |
| `depth`, `z_fighting`, `precision`, `occlusion` | I-DEPTH-01, I-DEPTH-02 |
| `shader`, `compile_error`, `black_pixel` | I-SHADER-01, I-SHADER-02 |
| `precision`, `banding`, `quantization` | I-PREC-01, I-PREC-02 |
| `lighting`, `shadow`, `normal` | I-LIGHT-01, I-LIGHT-02, I-NORMAL-01 |
| `alpha`, `transparency`, `blend` | I-ALPHA-01, I-BLEND-01 |
| `performance`, `stall`, `timeout` | I-PERF-01 |

---

## 一、数值类不变量

### I-NAN-01: 禁止NaN/Inf传播

```yaml
invariant_id: I-NAN-01
category: 数值
severity: CRITICAL

definition: |
  任何渲染输出的像素值必须是有界有限值。
  最终颜色输出不允许包含 NaN 或 Inf。

detection:
  method: |
    1. 在 Pixel Shader 输出前添加 NaN/Inf 检测
    2. 使用 RenderDoc API 查询像素值
    3. 观察颜色是否显示为异常亮白色/闪烁
  tools:
    - Pixel History
    - Shader Debugging
    - Screenshot inspection

symptoms:
  - 像素显示为全白 (255,255,255)
  - 像素值超出 [0,1] 范围
  - 渲染结果闪烁
  - 部分区域随机过曝

root_causes:
  - normalize(零向量)
  - 0/0 除零操作
  - sqrt(负数)
  - log(负数)
  - pow(负数, 非整数)
  - 极端浮点溢出

fix_template:
  hlsl: |
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

### I-NAN-02: 数值范围约束

```yaml
invariant_id: I-NAN-02
category: 数值
severity: HIGH

definition: |
  所有中间计算值必须在合理范围内。
  超出范围的数值表示可能存在数值不稳定。

detection:
  method: |
    1. 检查 Shader 输出值范围
    2. 检查 Vertex Position 范围
    3. 检查纹理坐标范围
  tools:
    - Shader Debugging
    - Pipeline State Viewer

root_causes:
  - 矩阵变换错误
  - 单位错误 (度 vs 弧度)
  - 缩放系数过大/过小

---

## 二、几何类不变量

### I-GEO-01: 可见性约束

```yaml
invariant_id: I-GEO-01
category: 几何
severity: HIGH

definition: |
  可见物体的边界框必须与视锥体相交。
  物体在世界空间中必须有一个以上的顶点在视锥体内。

detection:
  method: |
    1. 检查 Object -> World -> View -> Projection 变换链
    2. 验证视锥体裁剪设置
    3. 检查 Cull Mode 设置
  tools:
    - Pipeline State > Rasterizer
    - Matrix Debugger

symptoms:
  - 物体完全消失
  - 物体部分消失
  - 背面剔除异常

root_causes:
  - 负缩放导致剔除反转
  - 视锥体设置错误 (near > far)
  - 世界矩阵过于远离原点
  - View Matrix 方向错误

fix_template: |
  ```hlsl
  // 动态调整剔除模式
  CullMode DetermineCullMode(float3x3 normalMatrix) {
      float det = determinant(normalMatrix);
      return det < 0 ? CullMode::Front : CullMode::Back;
  }
  ```

### I-GEO-02: 正面方向约束

```yaml
invariant_id: I-GEO-02
category: 几何
severity: MEDIUM

definition: |
  顶点的环绕顺序必须一致。
  逆时针或顺时针定义的面必须保持一致。

root_causes:
  - 顶点顺序错误
  - 索引顺序错误
  - 手动翻转背面
```

### I-GEO-03: 顶点索引约束

```yaml
invariant_id: I-GEO-03
category: 几何
severity: MEDIUM

definition: |
  索引值必须在顶点缓冲的有效范围内。
  越界索引会导致未定义行为。

root_causes:
  - 索引缓冲越界
  - 顶点数量与索引不匹配
  - 索引类型错误 (16bit vs 32bit)
```

---

## 三、颜色类不变量

### I-COLOR-01: 颜色空间一致性

```yaml
invariant_id: I-COLOR-01
category: 颜色
severity: HIGH

definition: |
  颜色空间转换必须恰好执行一次。
  多次转换或缺失转换都会导致颜色失真。

detection:
  method: |
    1. 检查 Render Target 格式 (sRGB vs LINEAR)
    2. 检查 Shader 中的颜色处理
    3. 对比预期颜色与实际颜色
  tools:
    - Pipeline State > Render Target
    - Shader Editor
    - Color Picker

symptoms:
  - 颜色过暗
  - 颜色过亮 (过曝)
  - Gamma 失真
  - 颜色平淡/对比度不足

root_causes:
  - 双重 Gamma 校正 (手动 + 硬件)
  - 缺失 Gamma 校正
  - HDR 颜色写入 LDR Render Target
  - 错误的颜色空间假设

fix_template: |
  ```hlsl
  // 正确的 sRGB 处理流程
  
  // 方案1: 使用 sRGB Render Target (推荐)
  // RT 格式设为 D3D11_FORMAT_R8G8B8A8_UNORM_SRGB
  // 硬件自动处理 sRGB->Linear 转换
  
  // 方案2: 手动处理 (当使用 LINEAR RT 时)
  float3 LinearToSRGB(float3 linear) {
      return pow(linear, 1.0/2.2);
  }
  
  float3 SRGBToLinear(float3 srgb) {
      return pow(srgb, 2.2);
  }
  
  // 输出时确保只做一次转换
  float4 output;
  output.rgb = LinearToSRGB(calculatedColor); // 只做一次
  output.a = calculatedAlpha;
  ```

### I-COLOR-02: 颜色范围约束

```yaml
invariant_id: I-COLOR-02
category: 颜色
severity: MEDIUM

definition: |
  最终输出的颜色值必须在目标格式的合法范围内。
  LDR 格式: [0, 1], HDR 格式: [0, +∞)

root_causes:
  - HDR 值写入 LDR 格式
  - Tone Mapping 缺失
  - 曝光过度
```

---

## 四、纹理类不变量

### I-TEX-01: 纹理坐标有效性

```yaml
invariant_id: I-TEX-01
category: 纹理
severity: HIGH

definition: |
  纹理坐标必须在纹理的有效采样范围内。
  超范围坐标的行为由纹理寻址模式决定。

detection:
  method: |
    1. 检查 UV 坐标值范围
    2. 检查纹理寻址模式
    3. 验证纹理采样结果
  tools:
    - Shader Debugging
    - Texture Viewer

symptoms:
  - 纹理拉伸
  - 边缘重复
  - 纹理边缘采样错误

root_causes:
  - UV 计算错误
  - 纹理坐标溢出
  - 寻址模式设置错误 (Clamp vs Repeat)
```

### I-MIP-01: Mipmap 一致性

```yaml
invariant_id: I-MIP-01
category: 纹理
severity: MEDIUM

definition: |
  纹理 Mipmap 链必须完整且一致。
  不同 Mipmap 级别之间应该平滑过渡。

root_causes:
  - Mipmap 生成错误
  - Mipmap 链不完整
  - 手动设置 Mipmap 级别导致不连续
```

### I-LOD-01: 纹理 LOD 约束

```yaml
invariant_id: I-LOD-01
category: 纹理
severity: MEDIUM

definition: |
  纹理 LOD 计算必须在有效范围内。
  过度放大的纹理会导致模糊或闪烁。

root_causes:
  - Mipmap 被禁用
  - LOD Bias 过大
  - 各向异性过滤异常
```

---

## 五、深度类不变量

### I-DEPTH-01: 深度范围约束

```yaml
invariant_id: I-DEPTH-01
category: 深度
severity: HIGH

definition: |
  深度值必须在深度缓冲格式的有效范围内。
  常见的 24-bit 格式: [0.0, 1.0]

detection:
  method: |
    1. 检查 DepthStencil 格式
    2. 检查深度范围设置
    3. 可视化深度缓冲
  tools:
    - Pipeline State > DepthStencil
    - Texture Viewer (Depth)

symptoms:
  - Z-fighting
  - 深度精度丢失
  - 远处物体闪烁

root_causes:
  - 深度范围设置错误 (near=0, far=0)
  - 深度缓冲格式精度不足
  - 远平面过远导致精度分散
```

### I-DEPTH-02: 深度写入约束

```yaml
invariant_id: I-DEPTH-02
category: 深度
severity: MEDIUM

definition: |
  深度测试和深度写入必须正确配置。
  透明物体不应写入深度缓冲。

root_causes:
  - 深度写入在透明渲染时未禁用
  - 深度测试在应该启用时未启用
  - 深度函数设置错误 (Less vs Greater)
```

---

## 六、Shader 类不变量

### I-SHADER-01: Shader 合法性

```yaml
invariant_id: I-SHADER-01
category: Shader
severity: CRITICAL

definition: |
  所有 Shader 必须能够成功编译。
  编译错误必须被捕获并修复。

detection:
  method: |
    1. 检查 Pipeline State 中的 Shader 状态
    2. 查看编译错误信息
    3. 验证 Shader blob 存在
  tools:
    - Pipeline State > Shader
    - Shader Editor (Compile Errors)

symptoms:
  - 渲染结果全黑
  - 渲染结果全白
  - 部分 DrawCall 失败

root_causes:
  - Shader 编译错误
  - Shader 类型不匹配
  - 符号未定义
```

### I-SHADER-02: Shader 输出完整性

```yaml
invariant_id: I-SHADER-02
category: Shader
severity: HIGH

definition: |
  Pixel Shader 必须输出有效的颜色值。
  未初始化的输出会导致未定义行为。

root_causes:
  - 早期返回导致输出未设置
  - 条件分支未覆盖所有情况
  - 错误的 return 语句
```

### I-SHADING-NONNEG-01: 着色值非负

```yaml
invariant_id: I-SHADING-NONNEG-01
category: Shader
severity: MEDIUM

definition: |
  光照计算结果（尤其是漫反射分量）必须为非负值。
  负值光照会导致异常暗斑或颜色失真。

detection:
  method: |
    1. 检查 Diffuse = max(0, NdotL)
    2. 检查 Shader 中的光照模型
  tools:
    - Shader Debugging
    - Pixel History

fix_template: |
  ```hlsl
  // 错误的写法
  float diffuse = NdotL;
  
  // 正确的写法
  float diffuse = max(0.0f, NdotL);
  // 或使用 saturate
  float diffuse = saturate(NdotL);
  ```

---

## 七、精度类不变量

### I-PREC-01: 插值精度约束

```yaml
invariant_id: I-PREC-01
category: 精度
severity: MEDIUM

definition: |
  顶点到像素的颜色插值必须保持精度。
  过低的精度会导致颜色条带(Banding)。

detection:
  method: |
    1. 观察平滑渐变区域
    2. 检查 Render Target 格式
    3. 检查顶点着色器输出精度
  tools:
    - Screenshot inspection
    - Pipeline State

symptoms:
  - 颜色条带
  - 渐变不平滑
  - 量化噪点

root_causes:
  - 使用低精度数据类型 (half vs float)
  - Render Target 精度不足
  - 抖动(Dithering)被禁用
```

### I-PREC-02: 矩阵运算精度

```yaml
invariant_id: I-PREC-02
category: 精度
severity: MEDIUM

definition: |
  矩阵运算必须保持足够的精度。
  累积误差可能导致变换错误。

root_causes:
  - 多次矩阵乘法累积误差
  - 矩阵未正交化
  - 逆矩阵计算错误
```

---

## 八、光照类不变量

### I-LIGHT-01: 光照方向归一化

```yaml
invariant_id: I-LIGHT-01
category: 光照
severity: HIGH

definition: |
  光照计算中使用的方向向量必须归一化。
  未归一化的方向会导致光照强度错误。

detection:
  method: |
    1. 检查光照 Shader 中的向量
    2. 验证 normalize() 调用
  tools:
    - Shader Editor
    - Shader Debugging

fix_template: |
  ```hlsl
  // 错误: 未归一化
  float NdotL = dot(normal, lightDir);
  
  // 正确: 归一化
  float3 L = normalize(lightDir);
  float NdotL = saturate(dot(normal, L));
  ```

### I-LIGHT-02: 光照强度约束

```yaml
invariant_id: I-LIGHT-02
category: 光照
severity: MEDIUM

definition: |
  光照强度必须在合理范围内。
  超出范围的值需要 Tone Mapping 处理。

root_causes:
  - 光源强度设置过大
  - 缺少衰减计算
  - HDR 颜色未映射
```

### I-LIGHT-UNPACK-01: 光照解包约束

```yaml
invariant_id: I-LIGHT-UNPACK-01
category: 光照
severity: MEDIUM

definition: |
  从纹理/缓冲解包的光照数据必须正确解码。
  错误的解码会导致光照完全错误。

detection:
  method: |
    1. 检查光照数据的编码格式
    2. 验证解码 Shader
    3. 对比解包前后的值
  tools:
    - Shader Debugging
    - Buffer Viewer

root_causes:
  - 编码/解码格式不匹配
  - 字节序错误
  - 缩放/偏移错误

fix_template: |
  ```hlsl
  // 常见解包函数
  float3 UnpackLightmap(float4 encoded) {
      // 典型: RGB = color, A = intensity
      return encoded.rgb * encoded.a;
  }
  
  float3 UnpackNormalDXT5(float4 encoded) {
      // DXT5 法线解包
      float3 n;
      n.xy = encoded.ag * 2.0 - 1.0;
      n.z = sqrt(saturate(1.0 - dot(n.xy, n.xy)));
      return n;
  }
  ```

---

## 九、透明/混合类不变量

### I-ALPHA-01: Alpha 范围约束

```yaml
invariant_id: I-ALPHA-01
category: 混合
severity: HIGH

definition: |
  Alpha 值必须在 [0, 1] 范围内。
  超出范围的 Alpha 导致未定义混合行为。

root_causes:
  - 预乘 alpha 计算错误
  - HDR alpha 值未映射
```

### I-BLEND-01: 混合方程一致性

```yaml
invariant_id: I-BLEND-01
category: 混合
severity: MEDIUM

definition: |
  混合操作必须与渲染目标格式兼容。
  不兼容的混合会导致颜色错误。

root_causes:
  - Blend Op 与 Render Target 不兼容
  - 透明物体顺序错误
  - Alpha Test vs Alpha Blend 混淆
```

---

## 十、性能类不变量

### I-PERF-01: 资源状态有效性

```yaml
invariant_id: I-PERF-01
category: 性能
severity: MEDIUM

definition: |
  资源必须在使用前正确转换到所需状态。
  状态转换错误会导致 Pipeline Stall。

detection:
  method: |
    1. 检查 API 日志中的状态转换
    2. 查找 Pipeline Stall
  tools:
    - API Inspector
    - Resource Transitions

root_causes:
  - 缺少 Resource Barrier
  - 状态转换过于频繁
  - 不必要的同步
```

---

## 不变量检查流程

```
┌─────────────────────────────────────────────────────────────────┐
│                    INVARIANT CHECKING FLOW                        │
├─────────────────────────────────────────────────────────────────┤
│                                                                  │
│  症状观察                                                        │
│      │                                                          │
│      ▼                                                          │
│  ┌──────────────────┐                                           │
│  │ 标签匹配         │ ──▶ 候选不变量列表                         │
│  └──────────────────┘                                           │
│      │                                                          │
│      ▼                                                          │
│  ┌──────────────────┐                                           │
│  │ 逐一验证不变量   │                                             │
│  │ - 检测方法       │                                             │
│  │ - 症状匹配       │                                             │
│  │ - 根因匹配       │                                             │
│  └──────────────────┘                                           │
│      │                                                          │
│      ▼                                                          │
│  ┌──────────────────┐                                           │
│  │ 确认违反的不变量 │ ──▶ Hypothesis Board                       │
│  └──────────────────┘                                           │
│      │                                                          │
│      ▼                                                          │
│  ┌──────────────────┐                                           │
│  │ 应用修复模板     │ ──▶ 验证修复                               │
│  └──────────────────┘                                           │
│                                                                  │
└─────────────────────────────────────────────────────────────────┘
```

---

**相关文档**:
- [Hypothesis Board](./docs/hypothesis_board.md)
- [Quality Hooks](./docs/quality_hooks.md)
- [BugCard Template](./cases/bugcards/bugcard_template.md)
- [Expert Constraints](./docs/expert_constraints.md)
