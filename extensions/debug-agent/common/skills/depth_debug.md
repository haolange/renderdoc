# SOP-DEPTH-01: 深度问题排查 - 完整执行卡

## 基本信息

```yaml
sop_id: SOP-DEPTH-01
skill_name: 深度问题排查
category: 深度问题
severity: HIGH
invariants:
  - I-DEPTH-01: 深度缓冲必须正确初始化和更新
  - I-DEPTH-02: 深度测试配置必须与场景一致
version: 2.0
standalone: true
```

---

## 第一部分：触发条件与诊断

### 1.1 症状标签

| 标签 | 特征 | 观察方式 | 根因倾向 |
|------|------|---------|---------|
| `z_fighting` | 表面闪烁/条纹 | 同一表面显示混乱 | Near/Far比过大或Depth Bias不足 |
| `depth_error` | 遮挡关系错误 | 前景和背景互换 | Depth Func错误或深度值反向 |
| `occlusion` | 物体被错误遮挡 | 应该可见的物体不可见 | 深度测试禁用或值错误 |
| `far_plane` | 远处物体消失 | 增大Far平面时消失 | Far平面设置过小 |
| `near_plane` | 近处物体消失 | 靠近时物体被裁剪 | Near平面设置过大 |
| `clipping` | 物体被截断 | 边界处显示不完整 | Viewport或Scissor设置 |

### 1.2 触发标签

- 同一平面上的纹理闪烁或条纹
- 物体遮挡关系错误
- 某些物体完全消失
- 深度排序不对
- 深度缓冲显示异常

### 1.3 违反的不变量

**I-DEPTH-01**: 深度缓冲有效性
```
违反表现:
  - 深度缓冲未初始化(残留数据)
  - 深度值超出[0,1]范围
  - 深度写入被意外禁用

检查:
  深度格式: D24_UNORM_S8_UINT 或 D32_FLOAT
  初始值: 1.0(正常) 或 0.0(反向)
  范围: 所有值应在[0,1]内(UNORM)或[0,∞](FLOAT)
```

**I-DEPTH-02**: 深度函数一致性
```
违反表现:
  - DepthFunc = LESS 但期望 GREATER
  - DepthWriteMask = 0 (禁用写入)
  - Near > Far (矩阵错误)

检查:
  DepthFunc应该与近/远关系一致
  Near < Far (必须)
  Near > 0 (不能为0或负)
```

---

## 第二部分：决策树

```
┌─ 观察到深度问题
│
├─ 是什么类型的深度问题?
│  ├─ 闪烁/条纹 → Z-Fighting (路径A)
│  ├─ 遮挡错误 → Depth Func或值错误 (路径B)
│  ├─ 消失 → 视锥体外 (路径C)
│  └─ 显示错误 → Depth Bias不足 (路径D)
│
├─ 是否启用了深度测试?
│  ├─ NO → 禁用深度测试 (路径E)
│  └─ YES → 继续诊断 (路径F)
│
├─ Near/Far比是否过大?
│  ├─ YES → 精度不足 (路径G)
│  └─ NO → 其他问题 (路径H)
│
└─ 深度函数是否正确?
   ├─ LESS → 默认行为 (路径I)
   ├─ GREATER → 反向深度 (路径J)
   └─ 其他 → 检查特定函数 (路径K)
```

### 决策树详细说明

**路径A: Z-Fighting诊断**
- 症状: 表面闪烁
- 检查: 深度精度和Depth Bias
- 修复: 增加精度或Depth Bias

**路径B: 遮挡错误**
- 症状: 物体顺序反向
- 检查: Depth Func设置
- 修复: 改为LESS或GREATER

**路径G: 精度不足**
- 症状: 远处出现Z-Fighting
- 检查: Near/Far比 > 10000
- 修复: 调整Near/Far范围

---

## 第三部分：详细执行阶段

### 阶段1: 快速诊断（10分钟）

#### 1.1 深度缓冲可视化
```
工具调用:
  rdx.texture.get_data(
    session_id="debug_session_depth",
    texture_id="DepthStencil",
    x=0,
    y=0,
    region_size=[1920, 1080],
    format="DEPTH32F"
  )

期望输出:
  {
    "texture_data": [
      [1.0, 0.999, 0.99, ...],  // 远处(深度=1)
      [0.5, 0.5, 0.5, ...],     // 中距离
      [0.01, 0.01, 0.01, ...]   // 近处(深度=0)
    ],
    "depth_format": "D32_FLOAT",
    "min_depth": 0.01,
    "max_depth": 1.0,
    "has_content": true
  }

检查标准:
  1. min_depth 和 max_depth 应该在[0,1]范围内
  2. 深度值应该有梯度变化(不全相同)
  3. has_content = true
  4. 若有NaN/Inf → 数据损坏

判断:
  - 深度值正常分布 → 深度缓冲正确
  - 全为0或全为1 → 深度缓冲未初始化或测试禁用
  - 包含NaN/Inf → 着色器或矩阵计算错误
```

#### 1.2 深度状态检查
```
工具调用:
  rdx.pipeline.get_state(
    session_id="debug_session_depth",
    event_id=1000  // 任意DrawCall
  )

期望输出:
  {
    "depth_stencil_state": {
      "depth_enable": true,
      "depth_write_mask": "ALL",
      "depth_func": "LESS",
      "stencil_enable": false,
      "depth_bias": 0,
      "slope_scaled_depth_bias": 0.0,
      "depth_bias_clamp": 0.0
    },
    "depth_stencil_format": "D24_UNORM_S8_UINT"
  }

检查标准:
  1. depth_enable 应该是 true
  2. depth_write_mask 应该是 ALL
  3. depth_func 应该是 LESS(或GREATER,取决于设置)
  4. 若有Z-Fighting: depth_bias 应该 > 0

判断:
  - 若depth_enable = false → 深度测试被禁用
  - 若depth_write_mask = 0 → 深度写入被禁用
  - 若depth_func != LESS → 可能遮挡错误
```

#### 1.3 投影矩阵验证
```
工具调用:
  rdx.buffer.get_data(
    session_id="debug_session_depth",
    buffer_id="ConstantBuffer_PerFrame",
    offset=0,
    size=256
  )

期望输出:
  {
    "projection_matrix": [
      [2.413, 0, 0, 0],
      [0, 4.288, 0, 0],
      [0, 0, -1.0001, -1],
      [0, 0, -0.1, 0]
    ]
  }

计算Near/Far:
  // 从投影矩阵反推
  // M[2][2] = -(far + near) / (far - near)
  // M[3][2] = -2 * far * near / (far - near)
  
  near = 0.1
  far = 1000
  near/far = 1/10000  // 比率检查

检查标准:
  1. near > 0 (不能为0或负)
  2. near < far (near必须小于far)
  3. far / near < 10000 (精度要求)
  4. near不能太小(< 0.01通常导致精度问题)

判断:
  - 若 far/near > 10000 → 精度不足,易Z-Fighting
  - 若 near >= far → 矩阵错误,物体消失
  - 若 near接近0 → 近处精度丧失
```

### 阶段2: 深度测试分析（15分钟）

#### 2.1 深度函数验证
```
工具调用:
  获取多个物体的深度值:
  for each_object in scene {
    rdx.shader.get_debug(
      event_id=object.draw_call,
      x=screenX, y=screenY,
      shader_stage="VertexShader"
    )
  }

期望输出 (示例: 前景物体 + 背景物体):
  前景物体:
    {
      "clip_z": 0.1,
      "clip_w": 1.0,
      "normalized_depth": 0.1 / 1.0 = 0.1  // 近
    }
  
  背景物体:
    {
      "clip_z": 0.9,
      "clip_w": 1.0,
      "normalized_depth": 0.9 / 1.0 = 0.9  // 远
    }

深度比较规则:

DepthFunc = LESS (默认):
  前景物体深度: 0.1
  背景物体深度: 0.9
  0.1 < 0.9 → 前景物体通过深度测试 ✓
  
DepthFunc = GREATER (反向):
  前景物体深度: 0.9
  背景物体深度: 0.1
  0.9 > 0.1 → 前景物体通过深度测试 ✓

验证步骤:
  1. 收集所有可见物体的深度值
  2. 检查深度值是否与视觉深度一致
  3. 若视觉反向 → DepthFunc可能错误
```

#### 2.2 Z-Fighting诊断
```
症状: 同一平面显示闪烁/条纹

诊断步骤:
  1. 找出闪烁的物体:
     rdx.event.get_pixels(
       event_id=X,
       x=screen_x, y=screen_y
     )
  
  2. 查看像素历史:
     多个DrawCall修改同一像素 → 表面重合
     深度值非常接近 → Z-Fighting
  
  3. 计算深度精度:
     深度范围: [0.4, 0.6] (中间40%)
     UNORM精度: 24-bit → 16777216个值
     可分辨的深度差: 0.2 / 16777216 ≈ 1.2e-8
     
  4. 表面深度差:
     物体A深度: 0.500000
     物体B深度: 0.500001
     差值: 1e-6 (大于精度) → 可分辨
     
     物体A深度: 0.500000
     物体B深度: 0.500000001
     差值: 1e-9 (小于精度) → 无法分辨 → Z-Fighting

解决方案:
  1. 增加Near/Far比精度
  2. 使用Depth Bias
  3. 分离表面(稍微移动)
```

#### 2.3 Depth Bias设置
```
工具调用:
  rdx.pipeline.get_state(...) → depth_bias

理解Depth Bias:

实际深度 = 计算深度 + bias + slope_bias * 斜率

参数详解:
  - depth_bias: 固定偏移(单位: 1/2^23 对于FLOAT格式)
  - slope_scaled_depth_bias: 基于斜率的动态偏移
  - depth_bias_clamp: 偏移的最大值

例:
  depth_bias = 100
  slope_scaled_depth_bias = 1.0
  
  对于陡峭表面:
  实际深度 = 计算深度 + 100/8388608 + 1.0*large_slope
  
  偏移较大,避免Z-Fighting

推荐值:
  - 平面物体: depth_bias = 0, slope = 0
  - 稍微重叠: depth_bias = 10-100, slope = 0.5-1.0
  - 严重重叠: depth_bias = 100+, slope = 2.0+
```

### 阶段3: 根因分析（20分钟）

#### 3.1 根因检查清单

**检查项1: Z-Fighting诊断**
```
症状: 表面闪烁/条纹,两个物体深度非常接近

验证步骤:
  1. 获取两个物体的深度值:
     Object_A depth: 0.500123
     Object_B depth: 0.500124
  
  2. 计算精度需求:
     深度差: 1e-6
     深度范围: [near, far]
     
  3. 评估精度:
     24-bit深度 + near/far = 10000
     → 可分辨深度: ~1.2e-8
     1e-6 > 1.2e-8 → 理论上可分辨
     但在实际中可能失败 → 需Depth Bias
  
  4. 应用修复:
     depth_bias = 100
     slope_scaled_depth_bias = 1.0
     
  5. 验证:
     闪烁消失,物体显示清晰
```

**检查项2: 遮挡错误诊断**
```
症状: 前景和背景显示顺序反向

验证步骤:
  1. 检查DepthFunc:
     rdx.pipeline.get_state(...) → depth_func
  
  2. 检查物体深度值:
     前景物体: clip_z = 0.1 (近)
     背景物体: clip_z = 0.9 (远)
  
  3. 应用DepthFunc:
     若depth_func = LESS:
       0.1 < 0.9 → 前景通过 ✓
     若depth_func = GREATER:
       0.1 > 0.9 → 背景通过 ✗ (错误!)
  
  4. 修复:
     改为depth_func = LESS (或GREATER如果原意如此)
  
  5. 验证:
     物体顺序正确
```

**检查项3: Near/Far精度诊断**
```
症状: 远处物体出现Z-Fighting或显示错误

验证步骤:
  1. 计算当前比率:
     near = 0.1
     far = 1000
     ratio = 1000 / 0.1 = 10000
  
  2. 评估精度:
     24-bit深度: log2(10000) ≈ 13.3位有用
     剩余: 24 - 13.3 ≈ 10.7位
     可分辨深度差: far / 2^10.7 ≈ 0.96 (接近1%)
     在远处(深度=1000): 误差 ≈ 10单位!
  
  3. 改进方案:
     选项A: 增加near值
       near = 1.0 → ratio = 1000
       可分辨: far / 2^10.3 ≈ 0.097
     
     选项B: 减少far值
       far = 100 → ratio = 1000
       类似结果
     
     选项C: 使用32-bit浮点深度
       D32_FLOAT → 更高精度
  
  4. 选择修复:
     根据场景需求选择方案
  
  5. 验证:
     Z-Fighting减少或消失
```

**检查项4: 深度写入禁用诊断**
```
症状: 某些物体显示在最前面,遮挡被无视

验证步骤:
  1. 检查深度写入状态:
     rdx.pipeline.get_state(...) → depth_write_mask
  
  2. 若depth_write_mask = 0:
     物体深度未写入深度缓冲
     后续物体的深度测试无法对其正确判断
  
  3. 检查是否故意:
     某些特殊渲染(UI/透明) → 可能故意禁用
     普通物体 → 应该启用
  
  4. 修复:
     改为 depth_write_mask = ALL
  
  5. 验证:
     物体遮挡关系正确
```

#### 3.2 根因排查决策表

| 症状 | 第一检查 | 第二检查 | 最可能根因 |
|------|---------|---------|---------|
| 闪烁 | 深度差 | Near/Far比 | Z-Fighting精度不足 |
| 遮挡反向 | DepthFunc | 物体顺序 | Depth Func = GREATER |
| 消失 | 视锥体 | Near/Far | Far平面太近 |
| 无遮挡 | depth_enable | depth_write | 深度测试/写入禁用 |
| 近处消失 | Near值 | clipPos.z | Near平面设置太大 |

### 阶段4: 反事实验证（15分钟）

#### 4.1 必要条件验证

**条件1: 深度问题确实存在**
```
验证方法:
  1. 禁用深度测试: depth_enable = false
  2. 重新渲染
  3. 若问题消失 → 确实是深度问题
```

**条件2: 问题可重现**
```
重现测试:
  1. 相同的视角
  2. 相同的物体配置
  3. 相同的投影矩阵
  
结果: 问题应该重现
```

**条件3: 修复有效**
```
验证:
  1. 应用临时修复
  2. 重新渲染
  3. 问题是否消失
```

#### 4.2 替代解释排除

**假设1: 这不是深度问题,而是物体在视锥体外**
```
排除方法:
  1. 禁用视锥体剔除
  2. 增大Far平面到10000
  3. 重新渲染
  4. 若问题保留 → 是深度问题
```

**假设2: 这是顶点着色器错误,不是深度配置**
```
排除方法:
  1. 使用调试着色器显示深度值
  2. 检查深度值范围
  3. 若深度值正确 → 不是VS问题
```

**假设3: 这是渲染顺序问题,不是深度测试**
```
排除方法:
  1. 启用深度测试: depth_enable = true
  2. 重新渲染
  3. 若问题保留 → 是其他问题
```

### 阶段5: 修复代码模板（20分钟）

#### 5.1 常见修复方案

**修复方案1: 启用深度测试**
```cpp
// 问题: 深度测试被禁用
D3D12_GRAPHICS_PIPELINE_STATE_DESC psoDesc = {...};
psoDesc.DepthStencilState.DepthEnable = false;  // 错误!

// 修复: 启用深度测试
D3D12_DEPTH_STENCIL_DESC dsDesc = {};
dsDesc.DepthEnable = true;  // 启用
dsDesc.DepthWriteMask = D3D12_DEPTH_WRITE_MASK_ALL;  // 允许写入
dsDesc.DepthFunc = D3D12_COMPARISON_FUNC_LESS;  // 标准比较函数

psoDesc.DepthStencilState = dsDesc;
```

**修复方案2: 调整Near/Far范围**
```cpp
// 问题: 比率过大导致精度不足
float near = 0.01f;   // 太小
float far = 10000.0f; // 太大
// 比率 = 10000 / 0.01 = 1000000 (太大!)

// 修复: 选择合理范围
// 方案A: 增加near
float near = 1.0f;    // 合理的最小值
float far = 10000.0f;
// 比率 = 10000 / 1.0 = 10000 (可接受)

// 方案B: 减少far
float near = 0.1f;
float far = 1000.0f;
// 比率 = 1000 / 0.1 = 10000 (可接受)

// 方案C: 使用32-bit深度
// D3D12_RESOURCE_DESC depthDesc = {...};
// depthDesc.Format = DXGI_FORMAT_D32_FLOAT;  // 而不是D24_UNORM

D3DXMATRIX projection;
D3DXMatrixPerspectiveFovLH(&projection, 
    D3DX_PI / 4,      // FOV
    16.0f / 9.0f,     // Aspect
    near,             // Near plane
    far               // Far plane
);
```

**修复方案3: 应用Depth Bias解决Z-Fighting**
```cpp
// 问题: 相同深度的两个表面闪烁
D3D12_RASTERIZER_DESC rastDesc = {...};
rastDesc.DepthBias = 0;                    // 无偏移
rastDesc.SlopeScaledDepthBias = 0.0f;     // 无斜率偏移

// 修复: 添加Depth Bias
rastDesc.DepthBias = 100;                  // 固定偏移
rastDesc.SlopeScaledDepthBias = 1.0f;     // 斜率偏移
rastDesc.DepthBiasClamp = 0.0f;           // 无上限

// 对于阴影贴图(常见用途):
rastDesc.DepthBias = 1000;                 // 更大的偏移
rastDesc.SlopeScaledDepthBias = 2.0f;     // 对斜表面更大的偏移

ID3D12PipelineState* pso = nullptr;
device->CreateGraphicsPipelineState(&psoDesc, IID_PPV_ARGS(&pso));
```

**修复方案4: 修复反向深度问题**
```cpp
// 问题: 物体显示顺序反向
D3D12_DEPTH_STENCIL_DESC dsDesc = {...};
dsDesc.DepthFunc = D3D12_COMPARISON_FUNC_GREATER;  // 反向!

// 修复: 改为LESS(标准)
dsDesc.DepthFunc = D3D12_COMPARISON_FUNC_LESS;

// 或者调整顶点输出的深度值(高级方案):
// Shader中:
float4 clipPos = mul(position, mvp);
// 反向深度: clipPos.z = clipPos.w - clipPos.z;

ID3D12PipelineState* pso = nullptr;
device->CreateGraphicsPipelineState(&psoDesc, IID_PPV_ARGS(&pso));
```

#### 5.2 完整修复示例

**原始问题代码**
```cpp
class Renderer {
    D3D12_GRAPHICS_PIPELINE_STATE_DESC CreatePSO() {
        D3D12_GRAPHICS_PIPELINE_STATE_DESC psoDesc = {...};
        
        // 问题1: 深度测试被禁用
        psoDesc.DepthStencilState.DepthEnable = false;
        
        // 问题2: Near/Far比过大
        float near = 0.001f;   // 太小
        float far = 10000.0f;  // 太大
        D3DXMatrixPerspectiveFovLH(&proj, fov, aspect, near, far);
        
        // 问题3: Depth Bias未设置(会导致Z-Fighting)
        psoDesc.RasterizerState.DepthBias = 0;
        psoDesc.RasterizerState.SlopeScaledDepthBias = 0.0f;
        
        return psoDesc;
    }
};
```

**修复后代码**
```cpp
class Renderer {
    D3D12_GRAPHICS_PIPELINE_STATE_DESC CreatePSO() {
        D3D12_GRAPHICS_PIPELINE_STATE_DESC psoDesc = {...};
        
        // 修复1: 启用深度测试
        D3D12_DEPTH_STENCIL_DESC dsDesc = {};
        dsDesc.DepthEnable = true;                           // 启用深度测试
        dsDesc.DepthWriteMask = D3D12_DEPTH_WRITE_MASK_ALL;  // 启用深度写入
        dsDesc.DepthFunc = D3D12_COMPARISON_FUNC_LESS;       // 标准比较
        dsDesc.StencilEnable = false;
        
        psoDesc.DepthStencilState = dsDesc;
        
        // 修复2: 调整Near/Far范围
        float near = 0.1f;     // 增加near
        float far = 1000.0f;   // 或减少far
        // 比率 = 1000 / 0.1 = 10000 (可接受)
        
        D3DXMATRIX proj;
        D3DXMatrixPerspectiveFovLH(&proj, 
            D3DX_PI / 4,
            1920.0f / 1080.0f,
            near, far
        );
        
        // 修复3: 应用Depth Bias避免Z-Fighting
        D3D12_RASTERIZER_DESC rastDesc = {};
        rastDesc.DepthBias = 100;              // 固定偏移
        rastDesc.SlopeScaledDepthBias = 1.0f;  // 斜率偏移
        rastDesc.DepthBiasClamp = 0.0f;
        
        psoDesc.RasterizerState = rastDesc;
        
        return psoDesc;
    }
};
```

### 阶段6: 完整验证流程（15分钟）

#### 6.1 修复前后对比
```
步骤1: 保存原始帧
  rdx.texture.get_data("DepthStencil", ..., save="depth_before.bin")
  rdx.frame.take_screenshot("before.png")

步骤2: 应用修复
  - 修改PSO配置
  - 调整投影矩阵
  - 重新编译

步骤3: 重新渲染
  rdx.texture.get_data("DepthStencil", ..., save="depth_after.bin")
  rdx.frame.take_screenshot("after.png")

步骤4: 对比
  - Z-Fighting是否减少
  - 遮挡关系是否正确
  - 深度梯度是否正常
```

#### 6.2 深度可视化验证
```
创建调试Shader显示深度:

float4 PS_DebugDepth(PS_INPUT input) : SV_Target {
    float depth = input.position.z / input.position.w;
    // 归一化到[0,1]显示
    float normalized = (depth - near) / (far - near);
    return float4(normalized, normalized, normalized, 1.0);
}

预期:
  - 近处(黑色) → 深度值接近0
  - 远处(白色) → 深度值接近1
  - 平滑渐变 → 深度正确
  - 闪烁/条纹 → Z-Fighting
```

#### 6.3 回归测试
```
测试1: 不同距离的物体
  - 近距离(near平面附近)
  - 中距离
  - 远距离(far平面附近)
  验证: 都显示正确,无闪烁

测试2: 倾斜表面
  - 陡峭表面
  - 浅角表面
  验证: Depth Bias有效,无Z-Fighting

测试3: 透明物体
  - 确保深度测试启用
  - 验证排序正确

测试4: 性能
  - 深度写入不应显著降低性能
  - Depth Bias不应导致质量下降
```

---

## 第四部分：输出格式

```json
{
  "execution_card": "SOP-DEPTH-01",
  "session_id": "debug_session_depth",
  "timestamp": "2026-02-20T12:30:00Z",
  
  "diagnosis": {
    "matched_invariants": ["I-DEPTH-01", "I-DEPTH-02"],
    "violation_type": "Z-Fighting due to depth precision and Depth Bias not set",
    "severity": "HIGH",
    "confidence": 0.89
  },
  
  "root_cause": {
    "primary": "Near/Far ratio too large (50000) causing depth precision loss",
    "secondary": "Depth Bias set to 0, insufficient to resolve Z-Fighting",
    "evidence": {
      "near": 0.01,
      "far": 500.0,
      "ratio": 50000,
      "recommended_ratio": 10000,
      "depth_bias": 0,
      "recommended_bias": 100
    }
  },
  
  "evidence": {
    "depth_state": {
      "depth_enable": true,
      "depth_write": true,
      "depth_func": "LESS",
      "depth_bias": 0,
      "status": "Partially correct"
    },
    "depth_analysis": {
      "near": 0.01,
      "far": 500.0,
      "ratio": 50000,
      "precision_bits": 7,
      "resolvable_depth_diff_far": 4.0,
      "actual_surface_diff": 0.001,
      "z_fighting_predicted": true
    },
    "visual_artifacts": {
      "flicker": true,
      "affected_region": "All surfaces",
      "pattern": "Alternating pixel patterns"
    }
  },
  
  "fix": {
    "strategy": "Adjust Near/Far range and apply Depth Bias",
    "priority": "IMMEDIATE",
    "changes": [
      {
        "type": "PROJECTION_MATRIX",
        "file": "Source/Camera.cpp",
        "before": "near = 0.01f; far = 500.0f;",
        "after": "near = 0.1f; far = 1000.0f;  // ratio = 10000"
      },
      {
        "type": "DEPTH_BIAS",
        "file": "Source/Renderer.cpp",
        "before": "rastDesc.DepthBias = 0;",
        "after": "rastDesc.DepthBias = 100;  // Apply bias to prevent Z-Fighting"
      }
    ]
  },
  
  "verification": {
    "before": {
      "z_fighting": true,
      "flicker_intensity": 0.8,
      "depth_precision_bits": 7
    },
    "after": {
      "z_fighting": false,
      "flicker_intensity": 0.0,
      "depth_precision_bits": 12,
      "improvement_percent": 99.0
    }
  },
  
  "quick_reference": {
    "symptoms": [
      "Flickering surface patterns",
      "Wrong occlusion order",
      "Objects disappearing near/far"
    ],
    "root_causes": [
      "Near/Far ratio too large (>10000)",
      "Depth Bias = 0 with overlapping surfaces",
      "Depth test disabled",
      "Depth write disabled"
    ],
    "quick_fixes": [
      "Adjust near to 0.1f and far to 1000f",
      "Set DepthBias = 100",
      "Enable depth write mask",
      "Verify DepthFunc = LESS"
    ]
  },
  
  "execution_time": {
    "phase1_diagnosis": 600,
    "phase2_depth_analysis": 900,
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

| 症状 | 第一检查 | 根因 | 修复 |
|------|---------|------|------|
| 闪烁 | Near/Far比 | 精度不足 + 无Bias | 调整范围+设置Bias |
| 遮挡反向 | DepthFunc | Func设为GREATER | 改为LESS |
| 消失 | Far值 | Far太近 | 增加Far或near |
| 无遮挡 | depth_enable | 深度测试禁用 | 启用 |
| 近处消失 | Near值 | Near太大 | 减小Near值 |

---

