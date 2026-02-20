# SOP-COLOR-01: 颜色失真问题排查 - 完整执行卡

## 基本信息

```yaml
sop_id: SOP-COLOR-01
skill_name: 颜色失真问题排查
category: 颜色问题
severity: HIGH
invariants:
  - I-COLOR-01: 颜色空间管理必须一致
  - I-COLOR-02: Gamma链必须闭环且无冗余
version: 2.0
standalone: true
```

---

## 第一部分：触发条件与诊断

### 1.1 症状标签（Symptom Tags）

| 标签 | 特征 | RGB值范围 | 根因倾向 |
|------|------|---------|---------|
| `color_too_dark` | 整体偏暗 | RGB < 0.3 | 双重gamma或缺失 |
| `color_too_bright` | 整体过曝 | RGB > 0.8 | 缺失gamma |
| `gamma` | 渐变不平滑 | 各处不同 | Gamma值错误 |
| `washed_out` | 对比度低 | 0.3-0.7聚集 | HDR->LDR压缩错误 |
| `color_distortion` | 色偏或失真 | 色相改变 | 颜色空间混淆 |
| `flat` | 饱和度太低 | 所有通道接近 | 颜色融合或混合错误 |

### 1.2 触发标签

- 截图显示颜色异常
- 对比参考图像发现颜色失真
- Shader输出与预期不符
- 颜色转换操作存在

### 1.3 违反的不变量

**I-COLOR-01**: 颜色空间一致性
```
违反表现: 
  - sRGB RT + 手动gamma混合
  - Linear RT + 无gamma转换
  - 纹理空间与处理空间不匹配

检查:
  RT_Format = sRGB → Shader输出应为Linear
  RT_Format = Linear → Shader输出应为sRGB (或无转换)
```

**I-COLOR-02**: Gamma链完整性
```
违反表现:
  - 双重gamma: pow(color, 2.2) + sRGB RT转换
  - 缺失gamma: Linear RT但无转换
  - 破损链: 中间某步缺失

链条验证:
  Input → [Shader处理] → [Gamma调整?] → [RT转换?] → Output
```

---

## 第二部分：决策树

```
┌─ 观察到颜色异常
│
├─ 是整体偏暗还是偏亮?
│  ├─ 偏暗 → gamma过大或双重gamma (路径A)
│  ├─ 偏亮 → gamma不足或缺失 (路径B)
│  └─ 对比度低 → HDR处理错误 (路径C)
│
├─ RT格式是什么?
│  ├─ sRGB → 检查Shader是否手动转换 (路径D)
│  ├─ Linear → 检查Shader是否缺失转换 (路径E)
│  └─ HDR (Float16/32) → 检查Tone Mapping (路径F)
│
├─ 输入纹理格式是什么?
│  ├─ sRGB纹理 → 硬件自动转为Linear (路径G)
│  ├─ Linear纹理 → 不做转换 (路径H)
│  └─ 数值纹理 → 空间独立 (路径I)
│
└─ 问题是否影响所有像素?
   ├─ 全局影响 → gamma链问题 (路径J)
   └─ 局部影响 → 特定纹理或混合错误 (路径K)
```

### 决策树详细说明

**路径A: 双重Gamma诊断**
- 症状: 整体太暗，对比度低
- 检查: RT=sRGB 且 Shader包含 pow(color, 2.2)
- 修复: 移除Shader中的pow()，让硬件处理

**路径B: 缺失Gamma诊断**
- 症状: 整体偏亮，高光过曝
- 检查: RT=Linear 且 Shader无gamma转换
- 修复: 添加 pow(color, 1/2.2) 或改用sRGB RT

**路径C: HDR处理不当**
- 症状: 超1.0的值被截断为1.0
- 检查: 颜色值>1.0 但 RT非HDR格式
- 修复: 使用浮点RT + Tone Mapping

---

## 第三部分：详细执行阶段

### 阶段1: 快速诊断（10分钟）

#### 1.1 视觉对比
```
工具调用:
  rdx.frame.take_screenshot(
    session_id="debug_session_color",
    capture_format="PNG",
    include_metadata=true
  )

观察:
  1. 整体亮度: 0~50%(太暗) / 50~100%(正常) / 100%+(过亮)
  2. 对比度: 高(>0.5) / 中(0.3-0.5) / 低(<0.3)
  3. 色相: 是否发生了不预期的色偏
  4. 对比参考图像（如有）

分类:
  整体偏暗 + 对比度正常 → 双重gamma
  整体偏亮 + 对比度偏高 → 缺失gamma
  整体正常但色偏 → 色彩空间混淆
  对比度很低 → HDR->LDR压缩错误
```

#### 1.2 Render Target格式检查
```
工具调用:
  rdx.pipeline.get_state(
    session_id="debug_session_color",
    event_id=1000  // 任意渲染事件
  )

期望输出:
  {
    "render_targets": [
      {
        "slot": 0,
        "texture": "Backbuffer",
        "format": "R8G8B8A8_UNORM_SRGB",
        "width": 1920,
        "height": 1080
      }
    ],
    "depth_stencil": {...}
  }

格式分析:
  - R8G8B8A8_UNORM_SRGB → 硬件自动应用gamma
    预期: Shader输出Linear颜色
  
  - R8G8B8A8_UNORM → 无gamma转换
    预期: Shader输出sRGB颜色或Linear颜色(自己处理)
  
  - R16G16B16A16_FLOAT → HDR容器
    预期: Shader输出任意范围，应用Tone Mapping
```

#### 1.3 纹理格式检查
```
工具调用:
  rdx.texture.get_info(
    session_id="debug_session_color",
    texture_id="DiffuseTexture"
  )

期望输出:
  {
    "format": "R8G8B8A8_UNORM_SRGB",
    "width": 512,
    "height": 512,
    "mip_levels": 9,
    "mip_count": 9,
    "color_space": "sRGB"
  }

格式分析:
  sRGB格式纹理：
    - 硬件在采样时自动转换为Linear
    - 需要在Shader中输出Linear或受rt控制的gamma
  
  Linear格式纹理：
    - 数据直接使用
    - 颜色处理需在Shader中完成
```

### 阶段2: Shader分析（15分钟）

#### 2.1 获取Shader源码
```
工具调用:
  rdx.shader.get_source(
    session_id="debug_session_color",
    event_id=1000,
    shader_stage="FragmentShader"
  )

查找危险信号:
  1. pow(color, 2.2) 或 pow(color, 1/2.2) → 手动gamma
  2. 缺少gamma处理 → 输出直接为输入
  3. 颜色空间转换注释 → 文档化的gamma处理
  4. 混合操作 → 可能破坏gamma链

代码示例分析:
  
  案例1 - 双重gamma问题:
  ```hlsl
  float3 color = DiffuseTex.Sample(sampler, uv).rgb;
  float3 gamma_corrected = pow(color, 2.2);  // 问题!
  return float4(gamma_corrected, 1.0);
  // 若RT是sRGB，会再做一次gamma → 太暗
  ```
  
  案例2 - 缺失gamma问题:
  ```hlsl
  float3 color = DiffuseTex.Sample(sampler, uv).rgb;
  return float4(color, 1.0);
  // 若RT是Linear，应该输出pow(color, 1/2.2)
  ```
  
  案例3 - 正确处理:
  ```hlsl
  float3 color = DiffuseTex.Sample(sampler, uv).rgb;
  // DiffuseTex为sRGB → 硬件已转为Linear
  float3 lit = color * lightIntensity;
  return float4(lit, 1.0);  // 直接输出，RT会处理gamma
  // 前提：RT为sRGB格式
  ```
```

#### 2.2 Shader调试输出
```
工具调用:
  rdx.shader.get_debug(
    session_id="debug_session_color",
    event_id=1000,
    x=960,
    y=540,
    shader_stage="FragmentShader",
    include_all_variables=true
  )

期望输出:
  {
    "execution_trace": [
      {
        "instruction": "color = DiffuseTex.Sample(...)",
        "value": [0.5, 0.5, 0.5],
        "note": "sRGB采样 → 硬件已转为Linear 0.5^2.2 ≈ 0.214"
      },
      {
        "instruction": "gamma_corrected = pow(color, 2.2)",
        "value": [0.022, 0.022, 0.022],
        "note": "再次应用gamma → 太暗"
      },
      {
        "instruction": "return color",
        "value": [0.022, 0.022, 0.022],
        "note": "若RT为sRGB，还会再gamma一次 → 更暗"
      }
    ]
  }

分析:
  - 如果发现pow()被应用了两次 → 双重gamma
  - 如果输出值偏小 → 可能多次gamma
  - 如果输出值偏大 → 可能缺失gamma
```

#### 2.3 采样器状态检查
```
工具调用:
  rdx.pipeline.get_state(
    session_id="debug_session_color",
    event_id=1000
  )

查找采样器配置:
  {
    "samplers": [
      {
        "slot": 0,
        "filter": "ANISOTROPIC",
        "addressU": "WRAP",
        "mip_lod_bias": 0.0,
        "comparison_func": "NEVER"
      }
    ]
  }

验证:
  - LOD Bias = 0 (否则会影响mipmap选择，间接影响颜色)
  - 过滤模式是否会影响颜色插值
```

### 阶段3: 根因分析（20分钟）

#### 3.1 根因检查清单

**检查项1: 双重Gamma诊断**
```
症状: 图像过暗，对比度低

验证步骤:
  1. 检查RT格式:
     rdx.pipeline.get_state(...) → format = "sRGB"
  
  2. 检查Shader代码:
     rdx.shader.get_source(...) → 包含 pow(color, 2.2)
  
  3. 执行跟踪:
     rdx.shader.get_debug(...) → color值显著变小
  
  4. 颜色数学验证:
     输入sRGB值 0.5
     → 硬件转为Linear: 0.5^(1/2.2) ≈ 0.214  (第1次gamma)
     → Shader应用: pow(0.214, 2.2) ≈ 0.022  (第2次反向gamma被错用)
     → RT转换: 0.022^(1/2.2) ≈ 0.00088      (第3次gamma)
     最终: 约为输入的0.1% → 极暗

结论: 是否存在多次gamma应用
```

**检查项2: 缺失Gamma诊断**
```
症状: 图像偏亮，高光过曝

验证步骤:
  1. 检查RT格式:
     format = "LINEAR" (无gamma)
  
  2. 检查Shader代码:
     没有pow()或颜色空间转换
  
  3. 颜色跟踪:
     rdx.shader.get_debug(...) → RGB值 ≈ 输入值 (无转换)
  
  4. 视觉对比:
     在sRGB监视器上显示线性颜色 → 偏亮40-50%
     (因为监视器期望sRGB，但接收了Linear)

结论: Shader是否应该输出gamma校正后的值
```

**检查项3: HDR压缩错误**
```
症状: 动态范围被截断，对比度低

验证步骤:
  1. 检查RT格式:
     format = "R8G8B8A8_UNORM" (8-bit, 仅支持[0,1])
  
  2. 检查Shader输出值:
     rdx.shader.get_debug(...) → 包含 > 1.0 的值
  
  3. Tone Mapping缺失:
     Shader无tone curve / reinhard / filmic等
  
  4. 颜色空间:
     高对比度内容被压缩到[0,1] → 细节丢失

解决方案:
  - 使用float16/32 RT
  - 添加Tone Mapping
  - 降低光照强度
```

**检查项4: 色彩空间混淆**
```
症状: 色相不对，色偏明显

验证步骤:
  1. 纹理标记检查:
     rdx.texture.get_info(texture_id) → color_space = ?
  
  2. Shader采样:
     sRGB纹理在Linear空间使用 → 色会不对
     Linear纹理在sRGB空间使用 → 色会反向
  
  3. 输出验证:
     比对参考色值
     如RGB(255,128,64) 应该是什么 → 检查实际输出

修复:
  - 确保纹理标记正确
  - Shader中显式转换
```

#### 3.2 参考颜色数学

```
颜色空间转换参考表:

sRGB → Linear:
  if (C ≤ 0.04045)
    Linear = C / 12.92
  else
    Linear = pow((C + 0.055) / 1.055, 2.4)

Linear → sRGB:
  if (C ≤ 0.0031308)
    sRGB = C * 12.92
  else
    sRGB = 1.055 * pow(C, 1/2.4) - 0.055

近似版本 (经常在Shader中使用):
  sRGB → Linear: pow(C, 2.2)
  Linear → sRGB: pow(C, 1/2.2)

HDR Tone Mapping参考:
  Reinhard: result = color / (1 + color)
  Filmic: 见 Unreal/Godot 参考
  ACES: 见 Academy Color Encoding System
```

### 阶段4: 反事实验证（15分钟）

#### 4.1 必要条件验证

**条件1: 颜色空间配置必须可验证**
```
验证方法:
  1. rdx.pipeline.get_state() → RT.format
  2. rdx.texture.get_info() → texture.color_space
  3. rdx.shader.get_source() → gamma相关代码
  
预期: 三者应该互相一致

一致情况:
  RT=sRGB + Tex=sRGB + Shader=无手动gamma → 正确
  RT=Linear + Tex=Linear + Shader=有pow(1/2.2) → 正确
```

**条件2: 问题必须可重现**
```
重现测试:
  1. 相同输入(渲染同一帧)
  2. 相同Shader配置
  3. 相同RT格式
  
结果: 应该看到相同的颜色失真
```

**条件3: 根因必须可隔离**
```
隔离测试:
  1. 临时禁用Shader中的gamma操作
  2. 重新渲染
  3. 观察颜色是否接近预期
```

#### 4.2 替代解释排除

**假设1: 这是监视器设置问题，不是代码问题**
```
排除方法:
  1. 使用RenderDoc自带的色值读取工具
  2. rdx.texture.get_data() 获取RT的原始数据
  3. 验证存储在RT中的实际值
  
  如果存储值本身错误 → 是代码问题
  如果存储值正确但显示错误 → 是监视器问题
```

**假设2: 这是纹理格式问题，不是gamma问题**
```
排除方法:
  1. 尝试临时改用不同格式纹理
  2. 如果问题消失 → 确实是纹理
  3. 如果问题保留 → 是gamma处理
```

**假设3: 这是混合模式问题**
```
排除方法:
  1. 关闭混合: BlendOp=DISABLE
  2. 重新渲染
  3. 如果问题消失 → 是混合模式
  4. 如果问题保留 → 是gamma或其他
```

#### 4.3 对比验证

**与正常帧的对比**
```
工具调用:
  rdx.frame.compare(
    session_id="debug_session_color",
    normal_frame_file="reference.tga",
    abnormal_frame_file="current_capture.tga",
    diff_threshold=0.05
  )

输出分析:
  {
    "total_different_pixels": 100000,
    "difference_magnitude": 0.35,
    "problem_type": "uniform_color_shift",
    "shift_direction": "darker",
    "shift_magnitude": 0.35  // 35%偏暗
  }

判断:
  - uniform_color_shift + darker → 双重gamma
  - uniform_color_shift + brighter → 缺失gamma
  - 局部shift → 纹理或混合问题
```

### 阶段5: 修复代码模板（20分钟）

#### 5.1 修复方案选择

**方案A: 使用sRGB Render Target (推荐)**
```hlsl
// C++设置:
D3D12_RESOURCE_DESC rtDesc = {...};
rtDesc.Format = DXGI_FORMAT_R8G8B8A8_UNORM_SRGB;  // 关键
// 或 DXGI_FORMAT_R10G10B10A2_UNORM_SRGB 获得更好精度

// Shader (简化版):
Texture2D DiffuseTex : register(t0);  // sRGB纹理
SamplerState sampler : register(s0);

float4 PS_Main(PS_INPUT input) : SV_Target {
    float3 color = DiffuseTex.Sample(sampler, input.uv).rgb;
    // 此时color已被硬件转为Linear (sRGB → Linear)
    
    float3 lit = color * GetLightInfluence(input.normal, lightDir);
    
    // 直接返回Linear颜色
    // 硬件会自动转为sRGB供监视器显示
    return float4(lit, 1.0);
}

优点:
  - Shader简单
  - 自动处理gamma
  - 色彩准确
  - 支持HDR扩展(使用float16 RT)

缺点:
  - 需要RT支持sRGB格式
  - 纹理也需标记为sRGB
```

**方案B: 手动Gamma管理**
```hlsl
// C++设置:
D3D12_RESOURCE_DESC rtDesc = {...};
rtDesc.Format = DXGI_FORMAT_R8G8B8A8_UNORM;  // 不含sRGB标记

// Shader:
Texture2D DiffuseTex : register(t0);  // Linear纹理
SamplerState sampler : register(s0);

// Gamma转换函数
float3 LinearToSRGB(float3 color) {
    return pow(color, 1.0 / 2.2);
}

float4 PS_Main(PS_INPUT input) : SV_Target {
    float3 color = DiffuseTex.Sample(sampler, input.uv).rgb;
    // color已是Linear空间
    
    float3 lit = color * GetLightInfluence(input.normal, lightDir);
    
    // 手动转为sRGB
    float3 final = LinearToSRGB(lit);
    return float4(final, 1.0);
}

优点:
  - 更灵活
  - 可视化中间值
  - 支持自定义Tone Mapping

缺点:
  - Shader复杂性增加
  - 手动计算开销
  - 易出错(忘记转换或转两次)
```

**方案C: HDR工作流程**
```hlsl
// C++设置:
D3D12_RESOURCE_DESC rtDesc = {...};
rtDesc.Format = DXGI_FORMAT_R16G16B16A16_FLOAT;  // HDR

// Shader:
float3 ACESToneMapping(float3 x) {
    // ACES Tone Curve (参考电影色彩处理)
    float3 a = 2.51f;
    float3 b = 0.03f;
    float3 c = 2.43f;
    float3 d = 0.59f;
    float3 e = 0.14f;
    return clamp((x*(a*x+b))/(x*(c*x+d)+e), 0.0, 1.0);
}

float4 PS_Main(PS_INPUT input) : SV_Target {
    float3 color = DiffuseTex.Sample(sampler, input.uv).rgb;
    float3 lit = color * GetLightInfluence(...);
    
    // 可能包含 > 1.0 的值
    // 应用Tone Mapping
    float3 toneMapped = ACESToneMapping(lit);
    
    // 转为sRGB
    float3 final = LinearToSRGB(toneMapped);
    return float4(final, 1.0);
}

优点:
  - 支持高动态范围
  - 更真实的效果
  - 可处理过曝场景

缺点:
  - 内存/带宽增加
  - Shader复杂度高
```

#### 5.2 具体修复示例

**原始问题代码 (双重Gamma)**
```hlsl
// 问题: RT为sRGB但Shader也做了gamma
Texture2D DiffuseTex : register(t0);
SamplerState sampler : register(s0);

float4 PS_Main(PS_INPUT input) : SV_Target {
    float3 color = DiffuseTex.Sample(sampler, input.uv).rgb;
    float3 gamma_corrected = pow(color, 2.2);  // 错误1
    return float4(gamma_corrected, 1.0);
}

/*
执行:
  1. 硬件读取sRGB纹理 → 自动转为Linear: 0.5^(1/2.2)
  2. Shader: pow(值, 2.2) → 反向操作 → 回到sRGB值
  3. RT写入: sRGB RT再转为sRGB → 多余
  4. 结果: 颜色出错
*/
```

**修复后代码 (使用sRGB RT)**
```hlsl
Texture2D DiffuseTex : register(t0);  // 声明为sRGB纹理
SamplerState sampler : register(s0);

float4 PS_Main(PS_INPUT input) : SV_Target {
    // 方案1: 直接使用硬件自动转换
    float3 color = DiffuseTex.Sample(sampler, input.uv).rgb;
    // 此处color已是Linear值 (硬件转换)
    
    // 方案2: 手动光照计算 (在Linear空间)
    float3 normal = normalize(input.normal);
    float3 lightDir = normalize(input.lightDir);
    float diffuse = max(0.0, dot(normal, lightDir));
    
    // 方案3: 应用光照
    float3 lit = color * diffuse;
    
    // 方案4: 直接输出Linear
    // (RT会自动转为sRGB供显示)
    return float4(lit, 1.0);
}

// C++: 确保RT格式正确
D3D12_RESOURCE_DESC rtDesc = {...};
rtDesc.Format = DXGI_FORMAT_R8G8B8A8_UNORM_SRGB;  // 关键!
```

**修复后代码 (手动Gamma)**
```hlsl
Texture2D DiffuseTex : register(t0);  // Linear纹理
SamplerState sampler : register(s0);

float3 LinearToSRGB(float3 color) {
    return pow(color, 1.0/2.2);
}

float4 PS_Main(PS_INPUT input) : SV_Target {
    float3 color = DiffuseTex.Sample(sampler, input.uv).rgb;
    // color已是Linear (纹理存储为Linear)
    
    float3 normal = normalize(input.normal);
    float3 lightDir = normalize(input.lightDir);
    float diffuse = max(0.0, dot(normal, lightDir));
    
    float3 lit = color * diffuse;
    
    // 显式转为sRGB (因为RT是LINEAR格式)
    float3 final = LinearToSRGB(lit);
    return float4(final, 1.0);
}

// C++: RT为普通格式
D3D12_RESOURCE_DESC rtDesc = {...};
rtDesc.Format = DXGI_FORMAT_R8G8B8A8_UNORM;  // 无sRGB标记
```

### 阶段6: 完整验证流程（15分钟）

#### 6.1 修复前后对比
```
步骤1: 保存原始帧
  rdx.frame.take_screenshot("before.png")
  rdx.texture.get_data(rt_id, format="RGBA32F", save_to="before_data.bin")

步骤2: 应用修复
  - 修改Shader代码
  - 重新编译
  - 确保RT格式正确

步骤3: 重新渲染
  rdx.frame.take_screenshot("after.png")
  rdx.texture.get_data(rt_id, format="RGBA32F", save_to="after_data.bin")

步骤4: 对比验证
  差异应该包括:
  - 整体亮度更正 (如果是双重gamma → 变亮)
  - 对比度恢复 (如果是缺失gamma → 增加)
  - 色彩准确 (与参考比较)
```

#### 6.2 定量验证
```
验证方法:
  rdx.frame.compare(
    before="before_data.bin",
    after="after_data.bin",
    reference="reference.bin"
  )

期望输出:
  {
    "before_vs_reference": {
      "mae": 0.45,  // 平均误差45%
      "rmse": 0.52,
      "verdict": "FAIL"
    },
    "after_vs_reference": {
      "mae": 0.02,  // 平均误差2%
      "rmse": 0.03,
      "verdict": "PASS"
    },
    "improvement": "98%"
  }

验证标准:
  - MAE < 0.05 (颜色误差 < 5%)
  - RMSE < 0.08
  - 肉眼无法区分
```

#### 6.3 逐像素验证
```
采样几个关键像素:

测试像素1: 高光区域
  期望: 接近白色 (0.9, 0.9, 0.9)
  修复前: 可能(0.6, 0.6, 0.6) 太暗
  修复后: 应回到(0.85, 0.85, 0.85)

测试像素2: 阴影区域
  期望: 灰色 (0.2, 0.2, 0.2)
  修复前: 可能(0.05, 0.05, 0.05) 太暗
  修复后: 应回到(0.18, 0.18, 0.18)

测试像素3: 彩色区域 (如红色物体)
  期望: (0.8, 0.2, 0.2)
  修复前: 可能(0.5, 0.1, 0.1) 色相正确但饱和度低
  修复后: 应回到(0.75, 0.18, 0.18)

验证工具:
  rdx.event.get_pixels(event_id, x, y) 逐像素检查
```

#### 6.4 回归测试
```
测试套件:

测试1: 不同亮度材质
  - 非常暗的材质 (0.1)
  - 中等材质 (0.5)
  - 非常亮的材质 (0.9)
  验证: 所有情况下颜色正确

测试2: 不同纹理类型
  - sRGB纹理
  - Linear纹理
  - 普通数据 (法线/参数)
  验证: 采样和处理正确

测试3: 混合操作
  - 透明对象
  - 加法混合
  - 乘法混合
  验证: 混合后颜色空间一致

测试4: 多种视点
  - 近距离(高光明显)
  - 远距离(整体较暗)
  - 极端角度
  验证: 各视点颜色一致
```

---

## 第四部分：输出格式

```json
{
  "execution_card": "SOP-COLOR-01",
  "session_id": "debug_session_color",
  "timestamp": "2026-02-20T11:00:00Z",
  
  "diagnosis": {
    "matched_invariants": ["I-COLOR-01", "I-COLOR-02"],
    "violation_type": "Gamma chain broken - double gamma applied",
    "severity": "HIGH",
    "confidence": 0.92
  },
  
  "root_cause": {
    "category": "Double Gamma",
    "description": "Shader applies pow(color, 2.2) AND sRGB RT applies gamma conversion",
    "location": {
      "file": "Shaders/PS_Lighting.hlsl",
      "line": 47,
      "code": "float3 gamma_corrected = pow(color, 2.2);"
    }
  },
  
  "evidence": {
    "rt_format": "R8G8B8A8_UNORM_SRGB",
    "texture_format": "R8G8B8A8_UNORM_SRGB",
    "shader_analysis": {
      "has_manual_gamma": true,
      "gamma_value": 2.2,
      "operation": "pow(color, 2.2)"
    },
    "shader_debug": {
      "before_gamma": [0.214, 0.214, 0.214],
      "after_gamma": [0.022, 0.022, 0.022],
      "expected": [0.5, 0.5, 0.5],
      "error": "96% too dark"
    },
    "color_comparison": {
      "reference": [0.8, 0.8, 0.8],
      "observed": [0.2, 0.2, 0.2],
      "difference_magnitude": 0.6
    }
  },
  
  "fix": {
    "strategy": "Use sRGB Render Target with hardware gamma (recommended)",
    "changes": [
      {
        "type": "SHADER_SIMPLIFICATION",
        "file": "Shaders/PS_Lighting.hlsl",
        "before": "float3 gamma_corrected = pow(color, 2.2); return float4(gamma_corrected, 1.0);",
        "after": "return float4(color, 1.0);  // RT handles gamma conversion"
      },
      {
        "type": "RENDER_TARGET_FORMAT",
        "file": "Source/Graphics/Renderer.cpp",
        "change": "rtDesc.Format = DXGI_FORMAT_R8G8B8A8_UNORM_SRGB;",
        "reason": "Enable hardware gamma conversion"
      }
    ]
  },
  
  "verification": {
    "before": {
      "color_sample_47_47": [0.2, 0.2, 0.2],
      "vs_reference": [0.8, 0.8, 0.8],
      "error_magnitude": 0.6,
      "verdict": "FAIL"
    },
    "after": {
      "color_sample_47_47": [0.79, 0.79, 0.79],
      "vs_reference": [0.8, 0.8, 0.8],
      "error_magnitude": 0.01,
      "verdict": "PASS"
    },
    "mae": 0.01,
    "rmse": 0.012,
    "improvement_percent": 98.3
  },
  
  "recommendations": {
    "immediate": [
      "Remove all manual gamma operations from Shaders",
      "Set RT format to sRGB variant",
      "Recompile and test"
    ],
    "medium_term": [
      "Establish color space documentation",
      "Code review for gamma handling",
      "Create shader template library"
    ],
    "long_term": [
      "Implement automated color space validation",
      "Develop color grading tools",
      "Team training on gamma and color spaces"
    ]
  },
  
  "quick_reference": {
    "symptom": "Image too dark or washed out",
    "common_root_causes": [
      "RT=sRGB + Shader=pow(c, 2.2) → 双重gamma",
      "RT=Linear + Shader=无转换 → 缺失gamma",
      "RT=8bit + 颜色值>1.0 → HDR截断"
    ],
    "quick_fixes": [
      "移除Shader中的pow()，使用sRGB RT",
      "添加pow(c, 1/2.2)，使用Linear RT",
      "使用float RT + Tone Mapping"
    ]
  },
  
  "execution_time": {
    "phase1_diagnosis": 600,
    "phase2_shader_analysis": 900,
    "phase3_root_cause": 1200,
    "phase4_verification": 900,
    "phase5_fix": 1200,
    "phase6_validation": 900,
    "total_minutes": 90
  },
  
  "status": "COMPLETED",
  "resolution": "FIXED"
}
```

---

## 快速参考速查表

| 症状 | 根因 | 修复方案 | 验证方法 |
|------|------|---------|---------|
| 过暗 | 双重gamma | 移除pow() | 亮度+30% |
| 过亮 | 缺失gamma | 添加pow(1/2.2) | 对比参考 |
| 对比低 | HDR->LDR | float RT+Tone Map | MAE<0.05 |
| 色偏 | 空间混淆 | 标记纹理格式 | 色值匹配 |
| 饱和低 | 颜色融合 | 分离处理通道 | 色相正确 |

---

## 依赖与关联

- 本卡片完全独立，可直接使用
- 所有代码可复制粘贴使用
- 所有参数完整，无外部依赖

