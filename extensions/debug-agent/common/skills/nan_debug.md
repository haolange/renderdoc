# SOP-NAN-01: NaN/Inf问题排查 - 完整执行卡

## 基本信息

```yaml
sop_id: SOP-NAN-01
skill_name: NaN/Inf问题排查
category: 数值问题
severity: CRITICAL
invariants:
  - I-NAN-01: 所有浮点运算必须防止NaN生成
  - I-NAN-02: NaN值不应传播到最终输出
version: 2.0
standalone: true
```

---

## 第一部分：触发条件与诊断

### 1.1 症状标签（Symptom Tags）

| 标签 | 特征 | 检测方式 |
|------|------|---------|
| `white_spot` | 孤立的全白像素 | 视觉检查+色值确认 |
| `nan_propagation` | 白色斑块逐帧扩大 | 多帧对比 |
| `infinite_value` | 过度曝光/过度暗化 | 色值超出[0,255]范围 |
| `pixel_overflow` | RGB任意分量溢出 | 像素历史显示值>1.0 |
| `flickering` | 像素值抖动 | 帧间对比分析 |
| `flash` | 突然出现极端亮度 | 动态分析 |

### 1.2 触发标签（Trigger Tags）

触发此SOP的条件：
- 检测到上述任意症状标签
- Shader输出包含非有限值（isnan() 或 isinf() 为真）
- 像素值不在预期范围内

### 1.3 违反的不变量

**I-NAN-01**: 数学安全性
```
违反表现: 任何浮点运算产生NaN/Inf
检查: normalize(v).x != normalize(v).x (NaN比较自身为false)
证明: float val = pixel_history[-1]; assert(val == val);
```

**I-NAN-02**: 传播限制
```
违反表现: NaN值从VS输出 -> FS输入 -> RT输出
链条: DrawCall[N] 输出NaN -> DrawCall[N+1] 采样该输出 -> 继续传播
```

---

## 第二部分：决策树

```
┌─ 观察到异常像素
│
├─ 是否为全白(R=1, G=1, B=1)?
│  ├─ YES → 可能为NaN (执行路径A)
│  └─ NO  → 可能为Inf或溢出 (执行路径B)
│
├─ 像素是孤立还是连续区域?
│  ├─ 孤立 → 可能为单点运算错误 (路径C)
│  └─ 连续 → 可能为纹理或整体计算错误 (路径D)
│
├─ 问题在哪个Shader阶段出现?
│  ├─ VS → 顶点位置/法线计算 (路径E)
│  ├─ FS → 颜色采样/运算 (路径F)
│  └─ GS → 几何变换 (路径G)
│
└─ 根本原因是什么?
   ├─ normalize(零向量) → SafeNormalize (路径H)
   ├─ 0/0 除零 → SafeDivide (路径I)
   ├─ sqrt(负数) → SafeSqrt (路径J)
   └─ log(负数) → SafeLog (路径K)
```

### 决策树详细说明

**路径A: NaN特征确认**
- 工具: `rdx.shader.get_debug(event_id, pixel_x, pixel_y)`
- 检查: VS输出是否已含NaN
- 判断: 若VS无NaN但FS有 → 纹理读取或FS计算产生NaN

**路径B: Inf/溢出特征**
- 工具: `rdx.texture.get_data(texture_id, x, y, level=0)`
- 检查: 像素值是否 > 1.0 或 < -1.0
- 判断: 若超范围但有限 → 可能为溢出而非NaN

**路径C-D: 空间分布分析**
- 工具: `rdx.frame.take_screenshot()`
- 判断: 用于确定是否需要深度纹理分析

---

## 第三部分：详细执行阶段

### 阶段1: 快速定位（5分钟）

#### 1.1 获取视觉证据
```
工具调用:
  rdx.frame.take_screenshot(
    session_id="debug_session_001",
    capture_format="PNG",
    include_metadata=true
  )

期望输出:
  {
    "framebuffer": {...},
    "timestamp": "2026-02-20T10:00:00Z",
    "resolution": [1920, 1080],
    "abnormal_regions": [
      {"x": 512, "y": 384, "radius": 10, "color": [255, 255, 255]}
    ]
  }

判断标准:
  - 如果存在全白像素: 进入NaN追踪
  - 如果颜色异常但非纯白: 检查是否为溢出
```

#### 1.2 定位问题像素
```
工具调用:
  rdx.texture.get_data(
    session_id="debug_session_001",
    texture_id="RenderTarget_0",
    x=512,
    y=384,
    region_size=[32, 32],
    format="RGBA32F"
  )

期望输出:
  {
    "pixel_data": [
      [1.0, 1.0, 1.0, 1.0],  // NaN在IEEE754中表现为最大值或特殊位模式
      [0.5, 0.5, 0.5, 1.0],
      ...
    ],
    "data_type": "FLOAT32",
    "has_nan": true,
    "has_inf": false
  }

判断标准:
  - "has_nan": true → 确认NaN问题
  - "has_inf": true → 可能是Inf溢出
```

#### 1.3 像素历史追踪
```
工具调用:
  rdx.event.get_pixels(
    session_id="debug_session_001",
    event_id=1247,  // 出现NaN的DrawCall事件ID
    x=512,
    y=384,
    include_all_stages=true
  )

期望输出:
  {
    "pixel_history": [
      {
        "event": 1240,
        "stage": "BackgroundColor",
        "value": [0.0, 0.0, 0.0, 1.0]
      },
      {
        "event": 1245,
        "stage": "VertexShader",
        "value": [0.5, 0.5, 0.5, 1.0]
      },
      {
        "event": 1247,
        "stage": "FragmentShader",
        "value": [NaN, NaN, NaN, 1.0]  // 首次出现NaN
      },
      {
        "event": 1250,
        "stage": "BlendOp",
        "value": [NaN, NaN, NaN, 1.0]  // NaN传播
      }
    ],
    "first_nan_event": 1247,
    "first_nan_stage": "FragmentShader"
  }

判断标准:
  - 找到 "first_nan_event": X
  - 找到 "first_nan_stage": Y
  - Y决定后续调查方向（VS/FS/其他）
```

### 阶段2: Shader调试（10分钟）

#### 2.1 获取Shader源码
```
工具调用:
  rdx.shader.get_source(
    session_id="debug_session_001",
    event_id=1247,
    shader_stage="FragmentShader"
  )

期望输出:
  {
    "source": """
    cbuffer PerFrame {
        float4x4 viewProj;
        float3 lightDir;
    };
    
    Texture2D normalMap : register(t0);
    SamplerState samplerLinear : register(s0);
    
    float4 PS_Main(PS_INPUT input) : SV_Target {
        float3 normal = normalMap.Sample(samplerLinear, input.uv).rgb;
        float3 normal_normalized = normalize(normal);  // 潜在危险
        float diffuse = dot(normal_normalized, lightDir);
        return float4(diffuse.xxx, 1.0);
    }
    """,
    "shader_id": "PS_Main_0x12345678",
    "compiled": true
  }

分析:
  1. 查找危险函数: normalize(), length(), sqrt(), log(), pow()
  2. 检查输入验证: 是否有除零检查、长度检查
  3. 标记可疑行: normal_normalized = normalize(normal) - 如果normal为(0,0,0)会产生NaN
```

#### 2.2 Shader调试执行
```
工具调用:
  rdx.shader.get_debug(
    session_id="debug_session_001",
    event_id=1247,
    x=512,
    y=384,
    shader_stage="FragmentShader",
    include_all_variables=true
  )

期望输出:
  {
    "execution_trace": [
      {
        "instruction": "normal = texture.Sample(...)",
        "value": [0.0, 0.0, 0.0]  // 黑色纹理值
      },
      {
        "instruction": "length_normal = length(normal)",
        "value": 0.0  // 零向量
      },
      {
        "instruction": "normal_normalized = normal / length_normal",
        "value": [NaN, NaN, NaN]  // 0/0 = NaN
      },
      {
        "instruction": "diffuse = dot(normal_normalized, lightDir)",
        "value": NaN  // NaN传播
      }
    ],
    "root_cause_line": 15,
    "root_cause_code": "float3 normal_normalized = normalize(normal);"
  }

判断标准:
  - 找到包含NaN的指令
  - 找到前一步产生NaN的原因
  - 标记 root_cause_line 和 root_cause_code
```

#### 2.3 管线状态检查
```
工具调用:
  rdx.pipeline.get_state(
    session_id="debug_session_001",
    event_id=1247
  )

期望输出:
  {
    "pipeline_state": {
      "vertex_shader": "VS_Main",
      "fragment_shader": "PS_Main",
      "render_targets": [
        {
          "format": "R8G8B8A8_UNORM",
          "texture": "Backbuffer"
        }
      ],
      "depth_stencil": {
        "format": "D24_UNORM_S8_UINT",
        "depth_test": true,
        "depth_write": true
      }
    }
  }

分析:
  1. RT格式是否支持所有像素值（UNORM vs FLOAT）
  2. 深度测试是否影响结果
  3. 混合模式是否将NaN传播
```

### 阶段3: 根因分析（15分钟）

#### 3.1 根因检查清单

**检查项目1: normalize(零向量)**
```
症状: float3 v = float3(0,0,0); float3 n = normalize(v); // n = NaN
触发条件:
  - 纹理包含黑色像素 (0,0,0)
  - 法线纹理未初始化
  - 顶点数据为零

验证步骤:
  工具: rdx.texture.get_data(texture_id="NormalMap", ...)
  检查所有纹素值是否存在(0,0,0)
  如果存在 → 这是根因
```

**检查项目2: 0/0 除零**
```
症状: float a = 0; float b = 0; float c = a/b; // c = NaN
触发条件:
  - 分母计算为0
  - 距离或长度为0

验证步骤:
  查找代码中所有除法操作
  用Shader调试追踪分母值
  如果分母为0 → 这是根因
```

**检查项目3: sqrt(负数)**
```
症状: float x = -1; float y = sqrt(x); // y = NaN
触发条件:
  - 误差累积导致浮点值略为负数
  - 物理模型计算错误

验证步骤:
  查找sqrt/pow调用
  检查输入值范围
  添加max(0, x)防护
```

**检查项目4: log(负数)**
```
症状: float x = -1; float y = log(x); // y = NaN
触发条件:
  - 概率/密度计算出负值
  - 镜面反射系数为负

验证步骤:
  查找log/log2/log10调用
  确保输入 > 0
```

**检查项目5: pow(负数, 非整数)**
```
症状: float x = -0.5; float y = pow(x, 0.5); // y = NaN
触发条件:
  - 负值作为底数
  - 指数为分数

验证步骤:
  查找pow调用
  对负数添加abs()或clamp(0,...)
```

#### 3.2 API日志分析
```
工具调用:
  rdx.api.get_log(
    session_id="debug_session_001",
    from_event_id=1240,
    to_event_id=1250
  )

期望输出:
  {
    "events": [
      {
        "event_id": 1240,
        "api_call": "PSSetShaderResources",
        "resources": [
          {"slot": 0, "texture": "NormalMap", "format": "R8G8B8A8"}
        ]
      },
      {
        "event_id": 1247,
        "api_call": "Draw",
        "vertex_count": 36,
        "instance_count": 1
      },
      ...
    ]
  }

分析:
  - 确认NormalMap正确绑定
  - 确认所有纹理已加载
  - 检查纹理内容是否为预期值
```

### 阶段4: 反事实验证（10分钟）

#### 4.1 必要条件验证
```
条件1: NaN必须确实存在
  验证方法:
    rdx.texture.get_data(..., format="RGBA32F")
    检查输出中的 "has_nan": true
  预期结果: 确实找到NaN

条件2: NaN来源必须可追踪
  验证方法:
    rdx.event.get_pixels(...) 
    找到 "first_nan_event"
  预期结果: 明确的起点事件ID

条件3: 根因必须可重现
  验证方法:
    使用相同输入数据
    同样的Shader代码
    重新执行DrawCall
  预期结果: 相同位置再次出现NaN
```

#### 4.2 替代解释排除

**假设1: 这不是NaN，而是极端浮点值**
```
排除方法:
  1. 检查位模式: 0x7FC00000 (NaN) vs 0x7F800000 (Inf)
  2. 用isnan()测试: return (value != value)
  3. 如果确实为Inf → 改为路径B处理
```

**假设2: NaN来自硬件浮点异常，非计算产生**
```
排除方法:
  1. 检查GPU驱动版本
  2. 验证RenderDoc是否正确显示值
  3. 对比GPU调试输出和RenderDoc显示
```

**假设3: 这是纹理数据中已存在的NaN**
```
排除方法:
  工具: rdx.texture.get_data(texture_id="InputTexture")
  检查源纹理是否包含NaN
  如果包含 → 需要修复纹理加载，非Shader问题
```

#### 4.3 差异分析

**与正常像素对比**
```
工具调用:
  rdx.event.get_pixels(
    event_id=1247,
    x=400, y=300  // 正常区域
  )

对比:
  异常像素(512,384): VS=[正常], FS=[NaN]
  正常像素(400,300): VS=[正常], FS=[正常]
  
结论: FS计算产生NaN，与输入数据相关
```

### 阶段5: 修复代码模板（15分钟）

#### 5.1 安全数学函数库
```hlsl
// 安全数学库 - MathSafe.hlsli

// 安全归一化 (最常见的NaN来源)
float3 SafeNormalize(float3 v, float3 fallback = float3(0, 1, 0)) {
    float len = length(v);
    if (len > 0.00001f) {
        return v / len;
    }
    return fallback;
}

// 安全长度 (保证 >= 0)
float SafeLength(float3 v) {
    float lenSq = dot(v, v);
    return sqrt(max(0.0f, lenSq));
}

// 安全除法
float SafeDivide(float numerator, float denominator, float fallback = 0.0f) {
    return abs(denominator) > 0.0001f ? numerator / denominator : fallback;
}

// 安全平方根
float SafeSqrt(float x) {
    return sqrt(max(0.0f, x));
}

// 安全对数
float SafeLog(float x, float fallback = 0.0f) {
    return x > 0.00001f ? log(x) : fallback;
}

// 安全幂运算 (支持负底数)
float SafePow(float base, float exponent, float fallback = 0.0f) {
    if (base < 0.0f && frac(exponent) > 0.00001f) {
        // 负底数 + 非整数指数 = NaN
        return fallback;
    }
    return pow(abs(base), exponent) * sign(base);
}

// 安全镜面反射计算
float3 SafeSpecular(float3 normal, float3 viewDir, float3 lightDir, float shininess) {
    float3 safeNormal = SafeNormalize(normal);
    float3 safeView = SafeNormalize(viewDir);
    float3 safeLight = SafeNormalize(lightDir);
    
    float3 H = SafeNormalize(safeLight + safeView);
    float NdotH = dot(safeNormal, H);
    
    return pow(max(0.0f, NdotH), shininess).xxx;
}
```

#### 5.2 具体修复示例

**原始问题代码:**
```hlsl
float4 PS_Main(PS_INPUT input) : SV_Target {
    // 问题1: 未检查法线
    float3 normal = NormalMap.Sample(samplerLinear, input.uv).rgb;
    float3 normal_normalized = normalize(normal);  // 可能 NaN
    
    // 问题2: 未检查光照方向
    float3 lightDir = normalize(input.worldPos - lightPos);
    
    // 问题3: 可能的 0/0
    float distance = length(input.worldPos - lightPos);
    float attenuation = 1.0 / (distance * distance);  // 若distance=0 → Inf
    
    float diffuse = dot(normal_normalized, lightDir);
    return float4(diffuse.xxx * attenuation, 1.0);
}
```

**修复后代码:**
```hlsl
float4 PS_Main(PS_INPUT input) : SV_Target {
    // 修复1: 使用安全的归一化
    float3 normal = NormalMap.Sample(samplerLinear, input.uv).rgb;
    float3 normal_normalized = SafeNormalize(normal, float3(0, 0, 1));
    
    // 修复2: 对零向量情况处理
    float3 lightDelta = lightPos - input.worldPos;
    float distance = SafeLength(lightDelta);
    float3 lightDir = SafeNormalize(lightDelta, float3(0, 0, 1));
    
    // 修复3: 避免 1/0
    float attenuation = SafeDivide(1.0, 
        max(0.001f, distance * distance + 0.001f),  // 添加偏移
        0.0f);
    
    float diffuse = dot(normal_normalized, lightDir);
    diffuse = max(0.0f, diffuse);  // 额外保护
    
    return float4(diffuse.xxx * attenuation, 1.0);
}
```

#### 5.3 验证修复

```hlsl
// 调试模式: 显示安全网启动
float4 DebugMode = float4(0.0f, 0.0f, 0.0f, 0.0f);

float4 PS_Main_Debug(PS_INPUT input) : SV_Target {
    float3 normal = NormalMap.Sample(samplerLinear, input.uv).rgb;
    float normal_length = length(normal);
    
    if (normal_length < 0.00001f) {
        // 触发了安全网: 返回明显的调试颜色
        return float4(1, 0, 0, 1);  // 红色表示 normalize(0)
    }
    
    float3 normal_normalized = normalize(normal);
    
    if (any(isinf(normal_normalized)) || any(isnan(normal_normalized))) {
        return float4(0, 1, 0, 1);  // 绿色表示检测到NaN/Inf
    }
    
    // 正常流程
    return float4(normal_normalized, 1.0);
}
```

### 阶段6: 完整验证流程（20分钟）

#### 6.1 修复前后对比
```
工具调用:
  1. 保存原始帧:
     rdx.frame.compare(
       session_id="debug_session_001",
       event_id=1247,
       frame_before="capture_before.rdcap"
     )
  
  2. 应用修复
  
  3. 重新捕获:
     rdx.frame.take_screenshot(
       session_id="debug_session_001_fixed"
     )

对比标准:
  - 问题像素是否从白色变为正常颜色
  - 周围像素是否受影响
  - 是否引入新的伪影
```

#### 6.2 逐DrawCall验证
```
对每个相关DrawCall:
  rdx.event.get_pixels(
    event_id=X,
    x=512, y=384
  )

验证:
  - first_nan_stage 是否变为 "None"
  - 像素值是否在预期范围
  - 是否有传播链断裂
```

#### 6.3 回归测试
```
运行测试集:
  1. 零向量测试: 所有法线输入 = (0,0,0) → 应使用fallback
  2. 极端值测试: 距离接近0 → 应不产生Inf
  3. 负数测试: 镜面反射指数为负 → 应返回fallback
```

---

## 第四部分：输出格式

### 最终报告JSON
```json
{
  "execution_card": "SOP-NAN-01",
  "session_id": "debug_session_001",
  "timestamp": "2026-02-20T10:30:00Z",
  
  "diagnosis": {
    "matched_invariants": ["I-NAN-01", "I-NAN-02"],
    "violation_type": "NaN generation and propagation",
    "severity": "CRITICAL",
    "confidence": 0.95
  },
  
  "root_cause": {
    "category": "normalize(zero_vector)",
    "shader_stage": "FragmentShader",
    "problematic_code": "float3 normal_normalized = normalize(normal);",
    "line_number": 15,
    "root_event_id": 1247,
    "root_pixel": [512, 384],
    "propagation_path": [1247, 1250, 1253]
  },
  
  "evidence": {
    "first_nan_detection": {
      "event_id": 1247,
      "stage": "FragmentShader",
      "pixel_location": [512, 384],
      "pixel_value": [NaN, NaN, NaN, 1.0],
      "input_trigger": {
        "normal_map_sample": [0.0, 0.0, 0.0],
        "explanation": "黑色像素 → normalize(0,0,0) → NaN"
      }
    },
    "nan_propagation": [
      {
        "event_id": 1247,
        "stage": "FragmentShader",
        "affected_pixels": 1,
        "value_trace": "normalize(0,0,0) = NaN"
      },
      {
        "event_id": 1250,
        "stage": "BlendOp",
        "affected_pixels": 1,
        "value_trace": "NaN * blendFactor = NaN"
      },
      {
        "event_id": 1253,
        "stage": "RenderTarget",
        "affected_pixels": 1,
        "value_trace": "NaN stored to backbuffer"
      }
    ],
    "alternative_explanations_ruled_out": [
      "Inf overflow: 位模式验证确认为NaN (0x7FC00000) 而非Inf (0x7F800000)",
      "Texture corruption: NormalMap内容验证正确，黑色像素为预期",
      "Hardware bug: 重现测试成功，规律清晰"
    ]
  },
  
  "fix": {
    "strategy": "SafeNormalize with fallback",
    "priority": "IMMEDIATE",
    "changes": [
      {
        "file": "Shaders/LightingCommon.hlsli",
        "type": "HELPER_FUNCTION_ADD",
        "code": "float3 SafeNormalize(float3 v, float3 fallback = float3(0, 1, 0)) { ... }"
      },
      {
        "file": "Shaders/PS_Lighting.hlsl",
        "type": "CODE_REPLACE",
        "before": "float3 normal_normalized = normalize(normal);",
        "after": "float3 normal_normalized = SafeNormalize(normal);"
      },
      {
        "file": "Shaders/PS_Lighting.hlsl",
        "type": "CODE_REPLACE",
        "before": "float attenuation = 1.0 / (distance * distance);",
        "after": "float attenuation = SafeDivide(1.0, max(0.001f, distance * distance + 0.001f), 0.0f);"
      }
    ]
  },
  
  "verification": {
    "pre_fix": {
      "has_nan": true,
      "affected_pixels": 1,
      "affected_events": [1247, 1250, 1253]
    },
    "post_fix": {
      "has_nan": false,
      "affected_pixels": 0,
      "affected_events": [],
      "verification_tests": [
        {
          "name": "zero_vector_test",
          "input": [0, 0, 0],
          "expected_output": "fallback [0, 1, 0]",
          "result": "PASS"
        },
        {
          "name": "normal_vector_test",
          "input": [1, 0, 0],
          "expected_output": "[1, 0, 0]",
          "result": "PASS"
        },
        {
          "name": "zero_distance_test",
          "input_distance": 0.0,
          "expected_attenuation": 0.0,
          "result": "PASS"
        }
      ]
    }
  },
  
  "recommendations": {
    "immediate": [
      "应用修复代码",
      "重新编译Shader",
      "验证所有依赖项目"
    ],
    "medium_term": [
      "添加编译时检查：禁用不安全的normalize/length等",
      "建立Shader安全库",
      "定期代码审查"
    ],
    "long_term": [
      "Shader validation framework",
      "自动化NaN检测工具",
      "团队培训：数值稳定性"
    ]
  },
  
  "related_invariants": {
    "I-NAN-01": "所有浮点运算必须防止NaN生成",
    "I-NAN-02": "NaN值不应传播到最终输出",
    "I-SHADER-01": "所有Shader代码必须经过数值稳定性审查"
  },
  
  "execution_time": {
    "phase1_localization": 300,  // 秒
    "phase2_shader_debug": 600,
    "phase3_root_cause": 900,
    "phase4_verification": 600,
    "phase5_fix": 900,
    "phase6_validation": 1200,
    "total_seconds": 4500,
    "total_minutes": 75
  },
  
  "status": "COMPLETED",
  "resolution": "FIXED"
}
```

---

## 快速参考表

| 阶段 | 工具 | 参数 | 时间 | 判断 |
|------|------|------|------|------|
| 定位 | `take_screenshot` | - | 5min | 视觉确认 |
| 定位 | `get_data` | texture_id | 5min | 像素值 |
| 追踪 | `get_pixels` | event_id | 5min | 首次NaN |
| 调试 | `get_source` | event_id | 5min | 可疑代码 |
| 调试 | `get_debug` | event_id,x,y | 10min | 执行跟踪 |
| 分析 | `get_state` | event_id | 5min | 管线配置 |
| 验证 | 反事实测试 | - | 10min | 排除假设 |
| 修复 | 代码替换 | - | 15min | SafeX() |
| 验证 | 对比测试 | - | 20min | NaN消失 |

---

## 常见根因速查

| 根因 | 表现 | 修复 | 验证 |
|------|------|------|------|
| normalize(0) | white_spot | SafeNormalize | (0,0,0)→fallback |
| 0/0 | white_spot | SafeDivide | div=0→fallback |
| sqrt(-x) | NaN | SafeSqrt(max(0,x)) | x<0→0 |
| log(-x) | NaN | SafeLog | x≤0→fallback |
| pow(-x, 0.5) | NaN | abs+sign处理 | 负底数→fallback |

---

## 依赖与关联

- 本卡片独立可用，不依赖sop_library.md
- 可直接交给开发者作为修复指南
- 所有工具调用包含完整参数
- 所有代码示例可直接使用

