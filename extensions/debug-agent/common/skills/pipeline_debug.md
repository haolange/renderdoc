# SOP-PIPELINE-01: 管线配置问题排查 - 完整执行卡

## 基本信息

```yaml
sop_id: SOP-PIPELINE-01
skill_name: 渲染管线配置问题排查
category: 管线问题
severity: HIGH
invariants:
  - I-SHADER-01: 所有Shader必须编译成功
  - I-SHADER-02: Shader绑定必须与管线状态一致
  - I-BLEND-01: 混合操作必须与颜色格式兼容
  - I-ALPHA-01: Alpha混合顺序必须正确
  - I-PERF-01: Resource Barrier必须正确放置
version: 2.0
standalone: true
```

---

## 第一部分：触发条件与诊断

### 1.1 症状标签

| 标签 | 特征 | 观察方式 | 根因倾向 |
|------|------|---------|---------|
| `pipeline_error` | 渲染无输出或崩溃 | 黑屏或错误消息 | Shader编译失败或状态错误 |
| `state_mismatch` | 颜色/效果与预期不符 | 视觉异常 | 管线状态与代码不一致 |
| `blend_error` | 混合效果错误 | 透明度或颜色混淆 | 混合模式配置错误 |
| `black_pixel` | 黑色输出区域 | 部分或全部黑色 | Shader无输出或值为0 |
| `alpha_error` | 透明度显示错误 | Alpha值不对或顺序错误 | 混合顺序或Alpha预乘错误 |
| `render_nothing` | 完全无渲染 | 屏幕全黑 | 管线无效或未绑定 |

### 1.2 触发标签

- Shader编译失败
- 渲染无输出(黑屏)
- 颜色混合异常
- 透明度处理错误
- Resource Barrier问题

### 1.3 违反的不变量

**I-SHADER-01**: Shader有效性
```
违反表现:
  - 编译错误
  - 未找到入口点
  - 资源绑定不匹配

检查:
  编译日志是否包含错误
  是否有警告信息
  版本是否兼容
```

**I-SHADER-02**: 绑定一致性
```
违反表现:
  - Shader使用register(t0)但未绑定
  - 常量缓冲大小不匹配
  - 资源类型不匹配

检查:
  Shader中的register声明
  C++中的绑定槽位
  资源大小和类型
```

**I-BLEND-01**: 混合兼容性
```
违反表现:
  - 混合操作与RT格式不兼容
  - 色彩空间混淆
  - 输出值超范围

检查:
  RT格式(UNORM/FLOAT/SINT等)
  混合操作(ADD/SUBTRACT等)
  色彩范围[0,1]或无限制
```

---

## 第二部分：决策树

```
┌─ 观察到管线问题
│
├─ 是否有渲染输出?
│  ├─ NO → 管线无效或未绑定 (路径A)
│  └─ YES → 输出异常 (路径B)
│
├─ Shader是否编译成功?
│  ├─ NO → 编译错误 (路径C)
│  └─ YES → 继续诊断 (路径D)
│
├─ 资源是否正确绑定?
│  ├─ NO → 绑定错误 (路径E)
│  └─ YES → 继续诊断 (路径F)
│
├─ 混合操作是否正确?
│  ├─ NO → 混合错误 (路径G)
│  └─ YES → 其他问题 (路径H)
│
└─ 是否有Resource Barrier问题?
   ├─ YES → 同步问题 (路径I)
   └─ NO → 其他问题 (路径J)
```

---

## 第三部分：详细执行阶段

### 阶段1: 快速诊断（10分钟）

#### 1.1 管线有效性检查
```
工具调用:
  rdx.api.get_log(
    session_id="debug_session_pipeline",
    from_event_id=1,
    to_event_id=50
  )

期望输出:
  {
    "events": [
      {
        "event_id": 10,
        "api_call": "CreateGraphicsPipelineState",
        "result": "SUCCESS"
      },
      {
        "event_id": 20,
        "api_call": "Draw",
        "vertex_count": 36,
        "result": "SUCCESS"
      },
      ...
    ],
    "errors": []
  }

检查标准:
  1. CreateGraphicsPipelineState是否成功
  2. Draw调用是否成功
  3. 是否有错误消息
  4. Shader编译是否成功

判断:
  - 无错误 → 管线有效,继续调查
  - 编译错误 → Shader问题(路径C)
  - 状态创建失败 → 配置错误(路径E)
  - Draw失败 → 资源问题(路径E)
```

#### 1.2 Shader编译检查
```
工具调用:
  检查编译日志(通常在Visual Studio输出窗口或调试器)
  或使用fxc.exe / dxc.exe 命令行编译

编译命令示例:
  fxc.exe /T ps_5_0 /O3 Shaders/PixelShader.hlsl /Fo Build/ps.o
  
预期结果:
  ✓ 编译成功,生成目标文件
  
编译失败示例:
  error X3000: syntax error : unexpected token 'float3'
  error X3004: undeclared identifier 'lightDir'

检查Shader源代码:
  rdx.shader.get_source(
    event_id=20,  // 使用该shader的DrawCall
    shader_stage="FragmentShader"
  )

常见Shader问题:
  1. 语法错误 (拼写, 括号不配对)
  2. 未声明的变量或函数
  3. 类型不匹配
  4. Register声明错误: register(t0) vs register(s0)
```

#### 1.3 管线状态检查
```
工具调用:
  rdx.pipeline.get_state(
    session_id="debug_session_pipeline",
    event_id=20
  )

期望输出:
  {
    "pipeline_state": {
      "vertex_shader": "VS_Main_0x123456",
      "pixel_shader": "PS_Main_0x234567",
      "geometry_shader": null,
      "render_targets": [
        {
          "format": "R8G8B8A8_UNORM",
          "texture": "Backbuffer"
        }
      ],
      "depth_stencil": {...},
      "blend_state": {
        "alpha_to_coverage": false,
        "targets": [
          {
            "blend_enable": true,
            "src_blend": "SRC_ALPHA",
            "dst_blend": "INV_SRC_ALPHA",
            "blend_op": "ADD"
          }
        ]
      }
    }
  }

验证关键配置:
  1. 是否有有效的vertex_shader和pixel_shader
  2. render_targets个数 > 0
  3. blend_state配置是否合理
  4. depth_stencil是否启用(若需要)
```

### 阶段2: Shader资源绑定分析（15分钟）

#### 2.1 资源绑定验证
```
工具调用:
  rdx.pipeline.get_state(...) → shader_resources
  
期望输出:
  {
    "shader_resources": [
      {
        "slot": 0,
        "resource_type": "Texture2D",
        "texture_id": "DiffuseTexture",
        "format": "R8G8B8A8_UNORM_SRGB"
      },
      {
        "slot": 1,
        "resource_type": "Buffer",
        "buffer_id": "ConstantBuffer_PerObject",
        "size": 256
      }
    ],
    "samplers": [
      {
        "slot": 0,
        "filter": "LINEAR"
      }
    ]
  }

验证步骤:
  1. 获取Shader源代码:
     rdx.shader.get_source(...)
  
  2. 查找所有register声明:
     Texture2D tex : register(t0);
     cbuffer perObject : register(b0);
     SamplerState sam : register(s0);
  
  3. 比对绑定:
     register(t0) → 应该在 shader_resources[0]
     register(b0) → 应该在 constant_buffers[0]
     register(s0) → 应该在 samplers[0]
  
  4. 检查资源有效性:
     是否为null
     类型是否匹配
     大小是否足够

常见绑定错误:
  1. Shader使用register(t5)但只绑定了t0-t3
  2. 常量缓冲大小为64字节但Shader期望256字节
  3. Texture期望FLOAT格式但绑定了UNORM
```

#### 2.2 常量缓冲验证
```
工具调用:
  rdx.buffer.get_data(
    session_id="debug_session_pipeline",
    buffer_id="ConstantBuffer_PerObject",
    event_id=20,
    offset=0,
    size=256
  )

期望输出:
  {
    "buffer_data": {
      "world_matrix": [[...], [...], [...], [...]],
      "color": [0.5, 0.5, 0.5, 1.0],
      "padding": [...]
    },
    "size": 256
  }

验证步骤:
  1. 数据大小是否与Shader定义匹配
  2. 数据值是否合理
  3. 对齐是否正确(HLSL有严格对齐要求)

常见错误:
  - 打包/对齐不正确: float3在HLSL中必须按float4对齐
  - 大小计算错误: float4x4 = 64字节, 不是 16*4
  - 数据未初始化: 全为0或随机值
```

### 阶段3: 混合状态分析（15分钟）

#### 3.1 混合配置检查
```
工具调用:
  rdx.pipeline.get_blend_state(
    session_id="debug_session_pipeline",
    event_id=20
  )

期望输出:
  {
    "blend_state": {
      "alpha_to_coverage": false,
      "independent_blend_enable": false,
      "targets": [
        {
          "blend_enable": true,
          "src_blend": "SRC_ALPHA",
          "dst_blend": "INV_SRC_ALPHA",
          "blend_op": "ADD",
          "src_blend_alpha": "ONE",
          "dst_blend_alpha": "ZERO",
          "blend_op_alpha": "ADD",
          "write_mask": "ALL"
        }
      ]
    }
  }

混合公式理解:

标准透明混合(SRC_ALPHA + INV_SRC_ALPHA):
  Final = Src * Src.a + Dst * (1 - Src.a)
  
  例: Src = (1, 0, 0, 0.5), Dst = (0, 1, 0, 1)
  Final = (1,0,0)*0.5 + (0,1,0)*0.5 = (0.5, 0.5, 0, 1)

加法混合(ONE + ONE):
  Final = Src + Dst (可能溢出!)
  
  例: Src = (0.8, 0.5, 0), Dst = (0.3, 0.6, 0)
  Final = (1.1, 1.1, 0) → 超出[0,1]! (若RT为UNORM则截断)

乘法混合(ZERO + SRC_COLOR):
  Final = 0 + Src * Dst = Src * Dst
  
验证步骤:
  1. 检查blend_enable是否启用
  2. 验证src_blend和dst_blend是否匹配使用场景
  3. 检查blend_op是否为ADD(或需要的操作)
  4. 验证write_mask是否为ALL(若需要写入)

常见错误:
  1. blend_enable = true但src_blend/dst_blend不合理
  2. 透明物体使用了OPAQUE混合
  3. 加法混合导致溢出(使用UNORM格式且值超1.0)
```

#### 3.2 透明度处理检查
```
对于透明纹理和Alpha混合:

验证步骤:
  1. 获取Shader输出:
     rdx.shader.get_debug(
       event_id=20,
       x=512, y=384,
       shader_stage="FragmentShader"
     )
  
  2. 检查输出的Alpha通道:
     float4 color = {...};
     color.a = ?  // 应该是 [0,1]
  
  3. 如果Alpha总是1.0:
     问题1: 纹理没有Alpha通道
     问题2: Shader忘记设置Alpha
     问题3: 混合禁用,Alpha被忽略
  
  4. 如果Alpha为0.0:
     物体完全透明(消失)
     检查是否预期

Alpha预乘问题:

预乘Alpha格式:
  RGB已乘以Alpha,格式为: (R*A, G*A, B*A, A)
  
  混合公式:
  Final = Src + Dst * (1 - Src.a)  // 无需乘以alpha

非预乘Alpha格式:
  RGB未乘,格式为: (R, G, B, A)
  
  混合公式:
  Final = Src * Src.a + Dst * (1 - Src.a)

常见错误:
  - 预乘纹理使用非预乘混合 → 颜色偏暗
  - 非预乘纹理使用预乘混合 → 颜色偏亮
```

### 阶段4: Resource Barrier分析（15分钟）

#### 4.1 Barrier缺失检查
```
工具调用:
  rdx.resource.get_transitions(
    session_id="debug_session_pipeline"
  )

期望输出:
  {
    "transitions": [
      {
        "event_id": 10,
        "resource": "Backbuffer",
        "state_before": "PRESENT",
        "state_after": "RENDER_TARGET",
        "note": "正确的转换"
      },
      {
        "event_id": 20,
        "resource": "Texture_A",
        "state_before": "GENERIC_READ",
        "state_after": "RENDER_TARGET",
        "warning": "Texture_A既作为RT又作为SRV,可能导致同步问题"
      },
      ...
    ]
  }

Barrier常见问题:

问题1: 缺失Barrier导致WAR冒险
  操作1: Texture_A作为SRV(读取)
  操作2: 立即修改Texture_A的内容(作为RT写入)
  → 需要Barrier等待操作1完成

问题2: 状态转换不正确
  期望: COPY_DEST → 复制数据
  实际: GENERIC_READ → 读取旧数据
  
问题3: 同一资源的多个用途
  Texture既用作采样源又用作渲染目标
  需要正确的转换和Barrier

验证步骤:
  1. 找出所有资源转换
  2. 检查是否有WAR/RAW/WAW冒险
  3. 验证转换顺序
  4. 检查Barrier是否足够
```

#### 4.2 同步问题诊断
```
工具调用:
  rdx.api.get_log(...) → 查找所有ResourceBarrier调用

检查模式:

正确的模式:
  1. Draw(使用 TextureA 作为 SRV)
  2. ResourceBarrier(TextureA: SRV → RT)
  3. Draw(使用 TextureA 作为 RT)

错误的模式:
  1. Draw(使用 TextureA 作为 SRV)
  2. Draw(使用 TextureA 作为 RT)  // 缺少Barrier!
  结果: 后续draw可能读到旧数据或undefined

常见同步问题:
  1. 多个DrawCall之间缺少Barrier
  2. Copy操作后缺少Barrier
  3. Barrier等待的条件不足
```

### 阶段5: 根因分析（20分钟）

#### 5.1 根因检查清单

**检查项1: 编译错误诊断**
```
症状: Shader编译失败,无法创建管线

验证步骤:
  1. 获取编译日志
  2. 查找错误消息
  3. 查找相应的Shader代码行
  
常见编译错误:

错误1: Syntax Error
  Shader代码: float3 v = {1, 2, 3};  // 错误语法
  修复: float3 v = float3(1, 2, 3);

错误2: Undeclared Identifier
  Shader代码: return lightDir;  // lightDir未声明
  修复: 添加输入参数或声明变量

错误3: Type Mismatch
  Shader代码: float x = float3(1,2,3);  // 类型不匹配
  修复: float3 x = float3(1,2,3);

错误4: Invalid Register
  Shader代码: Texture2D tex : register(u0);  // u0不存在
  修复: register(t0) 对于纹理

修复方法:
  1. 根据错误消息修改Shader代码
  2. 重新编译
  3. 验证编译成功
```

**检查项2: 资源绑定错误诊断**
```
症状: Shader采样返回黑色或错误颜色

验证步骤:
  1. 比对Shader中的register声明和C++绑定
  2. 检查资源类型是否匹配
  3. 验证资源是否有效(非null)

常见绑定错误:

错误1: 忘记绑定资源
  Shader: Texture2D tex : register(t0);
  C++: // 缺少 PSSetShaderResources(0, 1, &texView)
  结果: 采样返回黑色

错误2: 槽位不匹配
  Shader: Texture2D tex : register(t5);
  C++: PSSetShaderResources(0, 1, &texView);  // 绑定到t0
  结果: Shader使用了空的t5,采样黑色

错误3: 资源类型错误
  Shader: Texture2D tex : register(t0);
  C++: PSSetConstantBuffers(0, 1, &cbView);  // 绑定错误的类型
  结果: 编译错误或黑屏

修复方法:
  1. 检查Shader源代码中的register声明
  2. 在C++中使用相同的槽位绑定
  3. 确保资源类型匹配
  4. 验证资源非null且已初始化
```

**检查项3: 混合错误诊断**
```
症状: 透明物体颜色异常或混合不正确

验证步骤:
  1. 检查Shader输出的Alpha值
  2. 验证混合模式设置
  3. 检查RT格式是否支持混合

常见混合错误:

错误1: 禁用混合但期望透明
  Shader输出: float4(color, 0.5);  // 50%透明
  混合设置: blend_enable = false;
  结果: Alpha被忽略,显示不透明

错误2: 混合顺序错误(OIT问题)
  问题: 先渲染远处物体后渲染近处
  结果: 混合顺序错误,图像不正确
  解决: 先渲染近处后渲染远处(需要sort)

错误3: Alpha预乘不匹配
  纹理格式: 预乘Alpha (RGB*A, A)
  混合模式: 非预乘 (SRC_ALPHA + INV_SRC_ALPHA)
  结果: 颜色偏暗
  修复: 改为预乘混合 (ONE + INV_SRC_ALPHA)

错误4: 高动态范围溢出
  Shader输出: float4(2.0, 2.0, 2.0, 1.0)  // 超亮
  RT格式: R8G8B8A8_UNORM (仅支持[0,1])
  混合: ONE + ONE (加法)
  结果: 溢出,被截断到1.0
  修复: 使用FLOAT RT或Tone Mapping

修复方法:
  1. 验证Shader输出的Alpha值
  2. 调整混合模式
  3. 确保RT格式支持输出范围
  4. 必要时改用高精度RT
```

**检查项4: Barrier问题诊断**
```
症状: 某些帧显示异常,可能间歇性崩溃

验证步骤:
  1. 查看ResourceBarrier调用序列
  2. 检查是否有WAR/RAW冒险
  3. 验证转换顺序

常见Barrier问题:

问题1: 缺失同步Barrier
  Timeline:
    Event 10: Draw(Texture_A SRV read)
    Event 11: Draw(Texture_A RT write)  // ← 缺少Barrier
  结果: Event 11可能读到旧数据

问题2: Barrier放置位置错误
  错误: Barrier在状态已使用后
  正确: Barrier在状态使用前

问题3: 状态转换不完整
  From: GENERIC_READ
  To: RENDER_TARGET
  但中间需要经过: COMMON → RENDER_TARGET

修复方法:
  1. 在资源用途改变时插入Barrier
  2. 使用正确的before/after状态
  3. 检查Barrier是否足够细粒度
  4. 必要时添加全局同步(Flush)
```

### 阶段6: 修复代码模板（20分钟）

#### 6.1 常见修复方案

**修复方案1: Shader编译问题**
```hlsl
// 问题: Shader编译失败
// float3 normal = GetNormal();  // GetNormal未定义

// 修复方案:
float3 GetNormal(float2 uv) {
    Texture2D normalTex : register(t2);
    return normalTex.Sample(samplerLinear, uv).rgb;
}

float4 PS_Main(PS_INPUT input) : SV_Target {
    float3 normal = GetNormal(input.uv);  // 现在可用
    return float4(normal, 1.0);
}
```

**修复方案2: 资源绑定错误**
```cpp
// 问题: 纹理未绑定
commandList->DrawIndexed(indexCount, 0, 0);
// Shader中: Texture2D tex : register(t0); 但t0未绑定

// 修复方案:
// C++中:
D3D12_GPU_DESCRIPTOR_HANDLE texHandle = descriptorHeap->GetGPUHandleAtIndex(0);
commandList->SetGraphicsRootDescriptorTable(TEXTURE_SLOT, texHandle);
commandList->DrawIndexed(indexCount, 0, 0);

// Shader中:
Texture2D diffuseTex : register(t0);
SamplerState samplerLinear : register(s0);

float4 PS_Main(PS_INPUT input) : SV_Target {
    return diffuseTex.Sample(samplerLinear, input.uv);
}
```

**修复方案3: 混合配置错误**
```cpp
// 问题: 透明物体显示不透明
D3D12_BLEND_DESC blendDesc = {...};
blendDesc.RenderTarget[0].BlendEnable = false;  // 错误!

// 修复方案:
D3D12_BLEND_DESC blendDesc = {};
blendDesc.AlphaToCoverageEnable = false;
blendDesc.IndependentBlendEnable = false;

D3D12_RENDER_TARGET_BLEND_DESC& rtBlend = blendDesc.RenderTarget[0];
rtBlend.BlendEnable = true;  // 启用混合
rtBlend.SrcBlend = D3D12_BLEND_SRC_ALPHA;
rtBlend.DestBlend = D3D12_BLEND_INV_SRC_ALPHA;
rtBlend.BlendOp = D3D12_BLEND_OP_ADD;
rtBlend.SrcBlendAlpha = D3D12_BLEND_ONE;
rtBlend.DestBlendAlpha = D3D12_BLEND_ZERO;
rtBlend.BlendOpAlpha = D3D12_BLEND_OP_ADD;
rtBlend.RenderTargetWriteMask = D3D12_COLOR_WRITE_ENABLE_ALL;

psoDesc.BlendState = blendDesc;
```

**修复方案4: Resource Barrier**
```cpp
// 问题: 缺少同步Barrier导致竞态条件
commandList->DrawIndexed(36, 0, 0);  // 使用 Texture_A 作为 RT
commandList->SetGraphicsRootDescriptorTable(0, texAHandle);
commandList->DrawIndexed(36, 0, 0);  // 使用 Texture_A 作为 SRV (错误!)

// 修复方案:
commandList->DrawIndexed(36, 0, 0);  // 使用 Texture_A 作为 RT

// 插入Barrier转换状态
D3D12_RESOURCE_BARRIER barrier = {};
barrier.Type = D3D12_RESOURCE_BARRIER_TYPE_TRANSITION;
barrier.Flags = D3D12_RESOURCE_BARRIER_FLAG_NONE;
barrier.Transition.pResource = textureA;
barrier.Transition.StateBefore = D3D12_RESOURCE_STATE_RENDER_TARGET;
barrier.Transition.StateAfter = D3D12_RESOURCE_STATE_PIXEL_SHADER_RESOURCE;
barrier.Transition.Subresource = D3D12_RESOURCE_BARRIER_ALL_SUBRESOURCES;
commandList->ResourceBarrier(1, &barrier);

// 现在可以安全使用 Texture_A 作为 SRV
commandList->SetGraphicsRootDescriptorTable(0, texAHandle);
commandList->DrawIndexed(36, 0, 0);
```

#### 6.2 完整修复示例

**原始问题代码**
```cpp
class Renderer {
    void Render() {
        // 问题1: Shader可能编译失败但未检查
        ID3DBlob* vertexShader = nullptr;
        D3DCompile(vsCode, strlen(vsCode), nullptr, nullptr, nullptr,
            "main", "vs_5_0", D3DCOMPILE_OPTIMIZATION_LEVEL3, 0,
            &vertexShader, nullptr);  // 错误被忽略!
        
        // 问题2: 创建PSO但未验证成功
        device->CreateGraphicsPipelineState(&psoDesc, IID_PPV_ARGS(&pso));
        
        // 问题3: 混合配置为禁用但有透明纹理
        psoDesc.BlendState.RenderTarget[0].BlendEnable = false;
        
        // 问题4: 缺少Resource Barrier
        commandList->DrawIndexed(36, 0, 0);  // 渲染到Texture_A
        commandList->SetGraphicsRootDescriptorTable(0, texAHandle);
        commandList->DrawIndexed(36, 0, 0);  // 使用Texture_A (竞态!)
    }
};
```

**修复后代码**
```cpp
class Renderer {
    bool CompileShader(const char* source, const char* target, ID3DBlob*& blob) {
        // 修复1: 检查编译错误
        ID3DBlob* errorBlob = nullptr;
        HRESULT hr = D3DCompile(source, strlen(source), nullptr, 
            nullptr, nullptr, "main", target, 
            D3DCOMPILE_OPTIMIZATION_LEVEL3, 0, &blob, &errorBlob);
        
        if (FAILED(hr)) {
            if (errorBlob) {
                LOG_ERROR("Shader compile error: %s", (char*)errorBlob->GetBufferPointer());
                errorBlob->Release();
            }
            return false;
        }
        return true;
    }
    
    bool CreatePSO(const D3D12_GRAPHICS_PIPELINE_STATE_DESC& desc, ID3D12PipelineState*& pso) {
        // 修复2: 验证PSO创建
        HRESULT hr = device->CreateGraphicsPipelineState(&desc, IID_PPV_ARGS(&pso));
        if (FAILED(hr)) {
            LOG_ERROR("PSO creation failed: 0x%08X", hr);
            return false;
        }
        return true;
    }
    
    void SetupBlendState(D3D12_GRAPHICS_PIPELINE_STATE_DESC& psoDesc) {
        // 修复3: 启用混合
        D3D12_BLEND_DESC& blendDesc = psoDesc.BlendState;
        blendDesc.AlphaToCoverageEnable = false;
        blendDesc.IndependentBlendEnable = false;
        
        D3D12_RENDER_TARGET_BLEND_DESC& rtBlend = blendDesc.RenderTarget[0];
        rtBlend.BlendEnable = true;  // 启用混合
        rtBlend.SrcBlend = D3D12_BLEND_SRC_ALPHA;
        rtBlend.DestBlend = D3D12_BLEND_INV_SRC_ALPHA;
        rtBlend.BlendOp = D3D12_BLEND_OP_ADD;
        rtBlend.SrcBlendAlpha = D3D12_BLEND_ONE;
        rtBlend.DestBlendAlpha = D3D12_BLEND_ZERO;
        rtBlend.BlendOpAlpha = D3D12_BLEND_OP_ADD;
        rtBlend.RenderTargetWriteMask = D3D12_COLOR_WRITE_ENABLE_ALL;
    }
    
    void Render() {
        // 编译和创建Shader
        ID3DBlob* vsBlob = nullptr;
        if (!CompileShader(vsCode, "vs_5_0", vsBlob)) return;
        
        ID3D12PipelineState* pso = nullptr;
        if (!CreatePSO(psoDesc, pso)) return;
        
        // 设置混合状态
        SetupBlendState(psoDesc);
        
        // 第一个DrawCall: 渲染到Texture_A
        commandList->OMSetRenderTargets(1, &texARTV, false, nullptr);
        commandList->DrawIndexed(36, 0, 0);
        
        // 修复4: 插入Barrier等待RT操作完成
        D3D12_RESOURCE_BARRIER barrier = {};
        barrier.Type = D3D12_RESOURCE_BARRIER_TYPE_TRANSITION;
        barrier.Transition.pResource = textureA;
        barrier.Transition.StateBefore = D3D12_RESOURCE_STATE_RENDER_TARGET;
        barrier.Transition.StateAfter = D3D12_RESOURCE_STATE_PIXEL_SHADER_RESOURCE;
        barrier.Transition.Subresource = D3D12_RESOURCE_BARRIER_ALL_SUBRESOURCES;
        commandList->ResourceBarrier(1, &barrier);
        
        // 第二个DrawCall: 使用Texture_A作为SRV (安全)
        commandList->OMSetRenderTargets(1, &backbufferRTV, false, nullptr);
        commandList->SetGraphicsRootDescriptorTable(0, texAHandle);
        commandList->DrawIndexed(36, 0, 0);
    }
};
```

### 阶段7: 完整验证流程（15分钟）

#### 7.1 修复验证
```
步骤1: 重新编译Shader
  检查编译日志 → 无错误

步骤2: 验证资源绑定
  rdx.pipeline.get_state(...) → 检查绑定正确

步骤3: 验证混合状态
  rdx.pipeline.get_blend_state(...) → 确认配置

步骤4: 验证Barrier
  rdx.resource.get_transitions(...) → 检查转换正确

步骤5: 渲染验证
  rdx.frame.take_screenshot(...) → 确认输出正确
```

#### 7.2 回归测试
```
测试1: Shader变量
  - 常量缓冲数据
  - 纹理采样
  - 顶点属性

测试2: 混合操作
  - 透明物体
  - 加法混合
  - 乘法混合

测试3: 多Pass渲染
  - 检查状态转换
  - 验证Barrier正确性
  - 检查输出一致性
```

---

## 第四部分：输出格式

```json
{
  "execution_card": "SOP-PIPELINE-01",
  "session_id": "debug_session_pipeline",
  "timestamp": "2026-02-20T13:00:00Z",
  
  "diagnosis": {
    "matched_invariants": ["I-SHADER-01", "I-BLEND-01"],
    "violation_type": "Blend state disabled but Alpha texture expected, Resource Barrier missing",
    "severity": "HIGH",
    "confidence": 0.91
  },
  
  "root_cause": {
    "primary": "Blend state BlendEnable = false while shader outputs Alpha value",
    "secondary": "Missing Resource Barrier between Texture_A RT write and SRV read",
    "evidence": {
      "blend_enable": false,
      "shader_alpha_output": 0.5,
      "expected_blend": true,
      "barrier_count": 0,
      "expected_barrier_count": 1
    }
  },
  
  "evidence": {
    "shader_analysis": {
      "compilation": "SUCCESS",
      "vertex_shader": "VS_Main",
      "pixel_shader": "PS_Main",
      "shader_output": "float4(color, alphaValue)",
      "alpha_value": 0.5
    },
    "blend_state": {
      "blend_enable": false,
      "src_blend": "N/A",
      "dst_blend": "N/A",
      "expected_blend_enable": true,
      "expected_config": "SRC_ALPHA + INV_SRC_ALPHA"
    },
    "resource_barriers": {
      "total_barriers": 0,
      "missing_barriers": [
        {
          "resource": "Texture_A",
          "after_event": 10,
          "before_event": 11,
          "transition": "RENDER_TARGET → PIXEL_SHADER_RESOURCE"
        }
      ]
    }
  },
  
  "fix": {
    "strategy": "Enable blend state and add Resource Barriers",
    "priority": "IMMEDIATE",
    "changes": [
      {
        "type": "BLEND_STATE_FIX",
        "file": "Source/Renderer.cpp",
        "before": "blendDesc.RenderTarget[0].BlendEnable = false;",
        "after": "blendDesc.RenderTarget[0].BlendEnable = true; blendDesc.RenderTarget[0].SrcBlend = SRC_ALPHA;"
      },
      {
        "type": "BARRIER_ADD",
        "file": "Source/Renderer.cpp",
        "location": "Between line 150 and 151",
        "code": "D3D12_RESOURCE_BARRIER barrier; barrier.Transition.pResource = textureA; barrier.Transition.StateBefore = RENDER_TARGET; barrier.Transition.StateAfter = PIXEL_SHADER_RESOURCE; commandList->ResourceBarrier(1, &barrier);"
      }
    ]
  },
  
  "verification": {
    "before": {
      "alpha_blending": false,
      "visual_result": "Opaque appearance despite Alpha texture",
      "correctness": "FAIL"
    },
    "after": {
      "alpha_blending": true,
      "visual_result": "Correct transparency",
      "barrier_correctness": "PASS",
      "correctness": "PASS"
    }
  },
  
  "quick_reference": {
    "symptoms": [
      "Shader compilation fails",
      "No rendering output",
      "Alpha blending not working",
      "Textures show wrong values"
    ],
    "root_causes": [
      "Shader syntax/type errors",
      "Missing resource binding",
      "Blend state misconfiguration",
      "Missing Resource Barriers"
    ],
    "quick_fixes": [
      "Check shader compilation errors",
      "Verify register declarations match bindings",
      "Enable blend state if transparency needed",
      "Add Barriers between state changes"
    ]
  },
  
  "execution_time": {
    "phase1_diagnosis": 600,
    "phase2_shader_analysis": 900,
    "phase3_blend_analysis": 900,
    "phase4_barrier_analysis": 900,
    "phase5_fix": 1200,
    "phase6_verification": 900,
    "total_minutes": 90
  },
  
  "status": "COMPLETED",
  "resolution": "FIXED"
}
```

---

## 快速参考速查表

| 问题 | 第一检查 | 根因 | 修复 |
|------|---------|------|------|
| 黑屏 | 编译成功? | Shader错误或绑定失败 | 检查编译,验证绑定 |
| 无混合 | blend_enable | 混合禁用 | 改为true + 设置参数 |
| 竞态条件 | Barrier数量 | 缺少同步 | 添加ResourceBarrier |
| 采样黑色 | 资源绑定 | 未绑定或null | 检查register,绑定资源 |
| 颜色错误 | 混合参数 | 混合模式错 | 调整SrcBlend/DstBlend |

---

