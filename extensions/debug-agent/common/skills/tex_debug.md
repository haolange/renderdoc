# SOP-TEX-01: 纹理采样问题排查 - 完整执行卡

## 基本信息

```yaml
sop_id: SOP-TEX-01
skill_name: 纹理采样问题排查
category: 纹理问题
severity: HIGH
invariants:
  - I-TEX-01: 纹理UV坐标必须在有效范围内
  - I-MIP-01: Mipmap链必须完整且正确
  - I-LOD-01: LOD选择必须与屏幕尺寸相适应
version: 2.0
standalone: true
```

---

## 第一部分：触发条件与诊断

### 1.1 症状标签

| 标签 | 特征 | 观察方式 | 根因倾向 |
|------|------|---------|---------|
| `texture_missing` | 整个纹理消失，显示单色 | 纹理为黑/白/默认颜色 | 采样器绑定或格式错误 |
| `uv_error` | 纹理部分显示或重复 | UV坐标异常映射 | UV计算错误或越界 |
| `mipmap` | 纹理突然切换 | Mip级别不平滑 | Mipmap链不完整 |
| `blurry` | 纹理过度模糊 | 小物体细节丢失 | LOD Bias过大或采样器设置 |
| `stretched` | 纹理拉伸变形 | 宽高比不对 | UV范围错误或投影 |
| `tiling` | 纹理重复或闪烁 | 网格状或噪点 | 寻址模式或采样频率 |
| `pixelated` | 纹理过度清晰 | 块状或马赛克 | LOD Bias过小或跳跃 |

### 1.2 触发标签

- 纹理采样异常（黑色/白色/丢失）
- 纹理显示不清晰（模糊或锐利）
- 纹理显示变形（拉伸、重复、旋转）
- Mipmap相关问题
- 纹理坐标问题

### 1.3 违反的不变量

**I-TEX-01**: UV坐标有效性
```
违反表现:
  - UV超出[0, 1]范围但寻址模式为CLAMP
  - UV计算产生NaN/Inf
  - UV未归一化

检查:
  0 ≤ u, v ≤ 1 (对于标准情况)
  若寻址模式为WRAP: 自动模运算
  若寻址模式为CLAMP: 超出范围导致边界取样错误
```

**I-MIP-01**: Mipmap完整性
```
违反表现:
  - Mipmap链缺少某个级别
  - Mipmap大小计算错误
  - 禁用Mipmap但采样器启用

检查:
  Mip[0] = 原始尺寸 (WxH)
  Mip[1] = W/2 x H/2
  Mip[k] = W/2^k x H/2^k
  最后 Mip = 1x1
  链中不能有间隙
```

**I-LOD-01**: LOD选择一致性
```
违反表现:
  - LOD Bias设置过大 (>2.0)
  - 采样频率与LOD不匹配
  - 动态改变LOD Bias导致闪烁

检查:
  LOD Bias ∈ [-2.0, 2.0] (推荐)
  采样导数应与屏幕频率匹配
  Anisotropy级别 ∈ [1, 16]
```

---

## 第二部分：决策树

```
┌─ 观察到纹理问题
│
├─ 纹理是否出现?
│  ├─ NO → 纹理未绑定或格式错误 (路径A)
│  └─ YES → 继续诊断 (路径B)
│
├─ UV坐标是否正确?
│  ├─ NO → UV计算错误 (路径C)
│  └─ YES → 继续诊断 (路径D)
│
├─ 纹理是否清晰或模糊?
│  ├─ 过度模糊 → LOD设置过大 (路径E)
│  ├─ 过度锐利 → LOD设置过小 (路径F)
│  └─ 正常 → 继续诊断 (路径G)
│
├─ Mipmap是否启用?
│  ├─ NO → 禁用Mipmap的特殊处理 (路径H)
│  └─ YES → Mipmap相关问题 (路径I)
│
└─ 采样器是否配置正确?
   ├─ NO → 采样器设置问题 (路径J)
   └─ YES → 其他问题 (路径K)
```

### 决策树详细说明

**路径A: 纹理未绑定**
- 检查: 纹理槽是否绑定
- 修复: PSSetShaderResources 或相当调用

**路径C: UV计算错误**
- 检查: Shader中的UV计算逻辑
- 修复: 验证UV范围和投影

**路径E/F: LOD问题**
- 检查: MipLODBias值
- 修复: 调整Bias值到[-1, 1]范围

**路径I: Mipmap问题**
- 检查: 纹理是否有完整Mipmap链
- 修复: 重新生成或重新加载纹理

---

## 第三部分：详细执行阶段

### 阶段1: 快速定位（10分钟）

#### 1.1 纹理存在性检查
```
工具调用:
  rdx.texture.get_info(
    session_id="debug_session_tex",
    texture_id="DiffuseTexture"
  )

期望输出:
  {
    "texture_id": "DiffuseTexture",
    "format": "R8G8B8A8_UNORM",
    "width": 1024,
    "height": 1024,
    "mip_levels": 10,
    "mip_count": 10,
    "color_space": "sRGB",
    "bind_flags": "SHADER_RESOURCE"
  }

检查标准:
  1. format 是否为预期格式
  2. width/height > 0
  3. mip_levels 应等于 mip_count
  4. mip_count = 1 + floor(log2(max(width, height)))
     例: 1024x1024 → 应该有11个mip (0-10)
  5. bind_flags 包含 SHADER_RESOURCE

判断:
  - 纹理信息正常 → 纹理已加载
  - 纹理不存在 → 加载失败
  - mip_count不对 → Mipmap链不完整
```

#### 1.2 纹理采样检查
```
工具调用:
  rdx.texture.get_data(
    session_id="debug_session_tex",
    texture_id="DiffuseTexture",
    x=0,
    y=0,
    region_size=[32, 32],
    mip_level=0,
    format="RGBA32F"
  )

期望输出:
  {
    "texture_data": [
      [1.0, 0.5, 0.25, 1.0],  // 第一个像素
      [0.9, 0.45, 0.2, 1.0],  // 第二个像素
      ...
    ],
    "mip_level": 0,
    "has_content": true,
    "min_value": 0.0,
    "max_value": 1.0
  }

检查标准:
  1. has_content = true (纹理非空)
  2. min_value / max_value 在合理范围
  3. 某些像素不全为0或全为1
  4. 若全为黑(0,0,0) → 纹理未初始化或默认值
  5. 若全为白(1,1,1) → 类似问题

判断:
  - 有内容且数值合理 → 纹理数据正确
  - 全黑/全白 → 纹理未正确加载
  - 全为NaN/Inf → 纹理格式错误
```

#### 1.3 着色器中的采样检查
```
工具调用:
  rdx.pipeline.get_state(
    session_id="debug_session_tex",
    event_id=1000  // 任意采样纹理的DrawCall
  )

期望输出:
  {
    "shader_resources": [
      {
        "slot": 0,
        "resource_type": "Texture2D",
        "texture_id": "DiffuseTexture",
        "format": "R8G8B8A8_UNORM_SRGB"
      }
    ],
    "samplers": [
      {
        "slot": 0,
        "filter": "ANISOTROPIC",
        "address_u": "WRAP",
        "address_v": "WRAP",
        "mip_lod_bias": 0.0,
        "max_anisotropy": 16
      }
    ]
  }

检查标准:
  1. 纹理是否绑定到正确的槽
  2. Sampler是否绑定
  3. Filter 是否合理 (LINEAR推荐)
  4. Address mode 是否与UV用法匹配
  5. mip_lod_bias 范围 (通常[-2, 2])

判断:
  - 纹理未绑定 → 使用了未设置的资源
  - Sampler无效 → 采样参数错误
```

### 阶段2: UV坐标分析（15分钟）

#### 2.1 UV坐标调试
```
工具调用:
  rdx.shader.get_debug(
    session_id="debug_session_tex",
    event_id=1000,
    x=512,
    y=384,
    shader_stage="FragmentShader",
    include_all_variables=true
  )

期望输出:
  {
    "execution_trace": [
      {
        "instruction": "float2 uv = input.texCoord;",
        "value": [0.5, 0.5],
        "note": "输入UV值"
      },
      {
        "instruction": "float3 color = diffuseTexture.Sample(sampler, uv);",
        "value": [0.8, 0.6, 0.4],
        "note": "采样结果"
      }
    ],
    "variables": {
      "texCoord": [0.5, 0.5],
      "sampleColor": [0.8, 0.6, 0.4],
      "isinfinite": false,
      "isnan": false
    }
  }

分析标准:
  1. UV值范围:
     标准情况: 0.0 ≤ u,v ≤ 1.0
     某些情况: 可以超出范围(取决于寻址模式)
  
  2. UV值异常:
     NaN → UV计算错误
     Inf → 除零
     负数 → 可能正确(取决于寻址)
  3. 采样结果:
     与纹理对应位置的值匹配
     若全黑 → 可能超出范围被CLAMP
     若全白 → 可能默认采样值

判断:
  - UV值合理 + 采样结果正确 → UV没问题
  - UV值异常 → UV计算有问题
  - UV值正确但采样结果错误 → 纹理或采样器问题
```

#### 2.2 UV范围和寻址模式
```
获取Shader代码:
  rdx.shader.get_source(
    session_id="debug_session_tex",
    event_id=1000,
    shader_stage="FragmentShader"
  )

查找UV相关代码:

案例1 - 正常UV:
  float2 uv = input.texCoord;  // 来自顶点着色器
  // 应该已经归一化到[0,1]

案例2 - UV计算错误:
  float2 uv = input.position.xy / 1024.0;  // 错误!应该除以实际分辨率
  
案例3 - UV投影:
  float3 projected = input.worldPos / input.worldPos.w;
  float2 uv = projected.xy * 0.5 + 0.5;  // NDC到[0,1]

案例4 - UV缩放:
  float2 uv = input.texCoord * 2.0;  // 缩放UV (会重复纹理)

验证步骤:
  1. 检查UV来源
  2. 检查是否进行了缩放/平移
  3. 检查边界处理
  4. 对比着色器代码和渲染结果
```

#### 2.3 寻址模式验证
```
根据采样器配置验证行为:

情况1: AddressU = WRAP, AddressV = WRAP
  UV超出[0,1]时: 自动模运算
  例: uv=(1.5, 0.3) → (0.5, 0.3)
  预期: 纹理重复显示

情况2: AddressU = CLAMP, AddressV = CLAMP
  UV超出[0,1]时: 采用边界像素
  例: uv=(1.5, 0.3) → 采样(1.0, 0.3)的像素
  预期: 边界颜色重复或拉伸

情况3: AddressU = MIRROR
  UV超出[0,1]时: 镜像反射
  例: uv=(1.5, 0.3) → (0.5, 0.3) 但反向
  预期: 纹理镜像显示

验证方法:
  1. 编写一个显示UV坐标的Shader
  2. 返回 float4(uv, 0, 1)
  3. 观察渲染结果
     - 纯粉色(1,0,0) → u有问题
     - 纯绿色(0,1,0) → v有问题
     - 渐变 → UV正确
```

### 阶段3: Mipmap分析（15分钟）

#### 3.1 Mipmap链验证
```
工具调用:
  rdx.texture.get_info(
    session_id="debug_session_tex",
    texture_id="DiffuseTexture"
  )

获取Mipmap链信息:

期望输出包含:
  {
    "mip_levels": 11,
    "mip_count": 11,
    "mips": [
      {
        "level": 0,
        "width": 1024,
        "height": 1024,
        "size_bytes": 4194304
      },
      {
        "level": 1,
        "width": 512,
        "height": 512,
        "size_bytes": 1048576
      },
      ...
      {
        "level": 10,
        "width": 1,
        "height": 1,
        "size_bytes": 4
      }
    ]
  }

验证标准:
  1. Mip[k]大小 = 原始大小 / 4^k
  2. Mip链从 max(width,height) 的 log2 个mip
     计算: expected_mips = 1 + floor(log2(max(1024, 1024))) = 11
  3. 每个mip都应该存在（无间隙）
  4. 最后一个mip应该为1x1

检测异常:
  - mip_count != 期望值 → 链不完整
  - 某个mip大小错误 → 数据损坏
  - 存在间隙 → 链断裂
```

#### 3.2 Mipmap采样测试
```
工具调用:
  对每个mip level 调用:
  rdx.texture.get_data(
    texture_id="DiffuseTexture",
    mip_level=k,
    region_size=[4, 4]
  )

比较各级mipmap:

Mip 0 (1024x1024): [0.8, 0.6, 0.4]  // 原始纹理
Mip 1 (512x512):   [0.75, 0.55, 0.35]  // 模糊一次
Mip 2 (256x256):   [0.7, 0.5, 0.3]  // 继续模糊
...

验证标准:
  1. 值应该逐级平滑变化(高度模糊)
  2. 不应该有跳变或异常值
  3. Mip值应该接近原始值的平均

异常检测:
  - 某个mip全黑/全白 → 该级未初始化
  - Mip值与原始相差太大 → 生成算法错误
  - 值产生了NaN → 纹理损坏
```

#### 3.3 LOD选择分析
```
验证采样器的LOD设置:
  rdx.pipeline.get_state(...) → samplers[0]

检查参数:
  {
    "filter": "ANISOTROPIC",
    "mip_filter": "LINEAR",
    "mip_lod_bias": 0.5,  // 调整LOD选择
    "min_lod": 0.0,       // 最小可用mip
    "max_lod": 11.0,      // 最大可用mip
    "max_anisotropy": 8
  }

理解LOD选择:

自动LOD计算:
  LOD = log2(采样频率) + bias
  
  其中采样频率 ≈ 屏幕像素到纹理像素的映射
  
  例: 64x64纹理显示在1024x1024屏幕
  采样频率 ≈ 1024/64 = 16
  LOD ≈ log2(16) = 4
  
  最终选中 Mip[4 + bias]

bias的影响:
  bias = 0:   选择自动计算的mip
  bias > 0:   选择更高级的mip(更模糊)
  bias < 0:   选择更低级的mip(更清晰)

常见问题:
  bias = 2:   始终选择mip 2以上 → 过度模糊
  bias = -2:  始终选择mip 0-2 → 可能失真
```

### 阶段4: 根因分析（20分钟）

#### 4.1 根因检查清单

**检查项1: 纹理未绑定诊断**
```
症状: 渲染结果全为黑色或默认颜色

验证步骤:
  1. 检查纹理是否存在:
     rdx.texture.get_info(...) → 失败
  
  2. 检查是否绑定到着色器:
     rdx.pipeline.get_state(...) → 检查shader_resources
     若texture_id为null或不存在 → 未绑定
  
  3. 检查绑定槽:
     C++代码: PSSetShaderResources(0, 1, &textureView)
     Shader代码: Texture2D tex : register(t0);
     应该匹配(都是0)
  
  4. 验证采样:
     若绑定了错误的纹理 → 会采样错误内容

修复验证:
  - 重新绑定纹理
  - 确保资源有效
  - 查看结果是否恢复
```

**检查项2: UV越界诊断**
```
症状: 纹理边界显示异常(拉伸、重复、黑边)

验证步骤:
  1. 采样UV坐标范围:
     rdx.shader.get_debug(...) → 检查所有采样点的UV
     
  2. 比对寻址模式:
     WRAP → UV应该自动模运算
     CLAMP → UV超出范围 → 采边界像素
  
  3. 检查预期行为:
     UV = (1.5, 0.3) + WRAP → 显示(0.5, 0.3)
     UV = (1.5, 0.3) + CLAMP → 显示(1.0, 0.3)
  
  4. 视觉验证:
     显示UV坐标: return float4(uv, 0, 1)
     应该显示平滑的渐变
     若显示重复或黑边 → 寻址有问题

修复:
  - 确保UV在[0,1]范围内
  - 选择正确的寻址模式
  - 调整UV计算逻辑
```

**检查项3: Mipmap不完整诊断**
```
症状: 纹理显示不平滑,有明显的级别切换

验证步骤:
  1. 获取Mipmap链信息:
     rdx.texture.get_info(...) → 检查mip_count
  
  2. 检查mip数量是否正确:
     expected = 1 + floor(log2(max(w, h)))
     若 mip_count != expected → 不完整
  
  3. 逐mip检查数据:
     for level in range(mip_count):
       rdx.texture.get_data(..., mip_level=level)
       检查数据是否有效
  
  4. 重新生成Mipmap:
     D3D12: GenerateMips()
     或在加载时: D3D12_RESOURCE_FLAG_ALLOW_UNORDERED_ACCESS
  
  5. 修复后验证:
     重新采样 → 确认Mipmap正常

修复:
  - 重新加载纹理(确保生成Mipmap)
  - 重新生成Mipmap链
  - 禁用Mipmap采样(临时方案)
```

**检查项4: LOD偏差诊断**
```
症状: 纹理过度模糊或过度清晰

验证步骤:
  1. 获取当前LOD bias:
     rdx.pipeline.get_state(...) → mip_lod_bias
  
  2. 计算自动LOD:
     自动LOD = log2(采样频率)
     最终LOD = 自动LOD + bias
  
  3. 检查选中的mipmap等级:
     若最终LOD过高 → 选了太高的mip → 模糊
     若最终LOD过低 → 选了太低的mip → 清晰/失真
  
  4. 调整bias:
     bias -= 1.0 → 更清晰
     bias += 1.0 → 更模糊
  
  5. 各向异性检查:
     MaxAnisotropy = 1 → 各向同性(可能模糊)
     MaxAnisotropy = 16 → 各向异性(清晰)

修复:
  - 调整mip_lod_bias 到 [-1, 0] 范围
  - 增加MaxAnisotropy
  - 检查采样频率计算
```

#### 4.2 根因排查决策表

| 症状 | 第一检查 | 第二检查 | 最可能根因 |
|------|---------|---------|---------|
| 全黑 | 纹理存在? | 绑定到着色器? | 未绑定或格式错误 |
| 全白 | 纹理内容 | 采样结果 | 纹理未初始化 |
| 重复 | UV值范围 | 寻址模式 | WRAP模式下UV超范围 |
| 拉伸 | 宽高比 | 寻址模式 | CLAMP或UV计算错误 |
| 模糊 | Mip_count | LOD_bias | Mipmap不完整或bias过大 |
| 清晰过度 | LOD_bias | 采样频率 | bias过小或频率计算错误 |

### 阶段5: 反事实验证（15分钟）

#### 5.1 必要条件验证

**条件1: 纹理问题确实存在**
```
验证方法:
  1. 比对参考渲染(如编辑器)
  2. 对比预期纹理显示
  3. 确认不是颜色空间或其他问题
  
预期结果: 确实存在纹理采样问题
```

**条件2: 问题可重现**
```
重现测试:
  1. 相同的模型和纹理
  2. 相同的摄像机视角
  3. 相同的Shader
  
结果: 问题应该在相同条件下重现
```

**条件3: 根因修复有效**
```
验证:
  1. 应用临时修复(如禁用Mipmap)
  2. 重新渲染
  3. 问题是否消失
```

#### 5.2 替代解释排除

**假设1: 这不是纹理问题,而是颜色空间问题**
```
排除方法:
  1. 使用调试着色器显示UV坐标
     return float4(uv, 0, 1)
  2. 若显示正确的渐变 → 不是纹理问题
  3. 若显示错误 → 确实是纹理问题
```

**假设2: 这是着色器计算问题,不是采样问题**
```
排除方法:
  1. 临时使用固定色值:
     return float4(0.5, 0.5, 0.5, 1.0)
  2. 若问题消失 → 是着色器问题
  3. 若问题保留 → 是纹理采样问题
```

**假设3: 这是格式或精度问题**
```
排除方法:
  1. 检查纹理格式: rdx.texture.get_info()
  2. 比对预期格式
  3. 若格式不对 → 重新加载
  4. 若格式对 → 不是格式问题
```

### 阶段6: 修复代码模板（20分钟）

#### 6.1 常见修复方案

**修复方案1: 纹理绑定问题**
```cpp
// 问题: 纹理未绑定或绑定到错误的槽
ID3D12Resource* textureResource = nullptr;
D3D12_GPU_DESCRIPTOR_HANDLE textureHandle = {0};

// 错误的做法:
commandList->SetGraphicsRootDescriptorTable(0, {0});  // 无效handle

// 正确的做法:
textureResource = LoadTexture("Assets/diffuse.dds");  // 加载纹理
textureHandle = descriptorHeap->GetGPUHandleAtIndex(0);  // 获取有效handle
commandList->SetGraphicsRootDescriptorTable(0, textureHandle);

// Shader端:
Texture2D diffuseTex : register(t0);  // 必须匹配C++中的槽
SamplerState samplerLinear : register(s0);

float4 PS_Main(PS_INPUT input) : SV_Target {
    return diffuseTex.Sample(samplerLinear, input.uv);
}
```

**修复方案2: UV计算错误**
```hlsl
// 问题: UV计算错误导致采样范围不对

// 错误做法1: 未归一化
float2 uv = input.position.xy;  // 绝对屏幕坐标
Texture2D tex : register(t0);
return tex.Sample(sampler, uv);  // 超出[0,1]范围!

// 错误做法2: 宽高比不对
float2 uv = float2(input.position.x / 1024, input.position.y / 1024);
// 问题: 忽略了宽高不同的情况

// 正确做法:
cbuffer PerFrame {
    uint screenWidth;
    uint screenHeight;
};

float2 VS_Main(VS_INPUT input) : SV_Position {
    // ... MVP变换 ...
    // 返回 clipPos
    return clipPos;
}

float4 PS_Main(PS_INPUT input) : SV_Target {
    // 正确: 从顶点着色器获取预计算的UV
    float2 uv = input.texCoord;  // 已在[0,1]范围
    
    // 或者在FS中重新计算:
    float2 ndc = input.position.xy / float2(screenWidth, screenHeight);
    float2 uv_screen = ndc * 2.0 - 1.0;  // 转到[-1,1]
    // ... 然后映射到[0,1] ...
    
    return diffuseTex.Sample(sampler, uv);
}
```

**修复方案3: Mipmap问题**
```cpp
// 问题: Mipmap链不完整或未生成

// 加载纹理并生成Mipmap:
D3D12_RESOURCE_DESC desc = {};
desc.Dimension = D3D12_RESOURCE_DIMENSION_TEXTURE2D;
desc.Width = 1024;
desc.Height = 1024;
desc.DepthOrArraySize = 1;
desc.MipLevels = 0;  // 0表示自动计算完整链
desc.Format = DXGI_FORMAT_R8G8B8A8_UNORM;
desc.SampleDesc.Count = 1;
desc.Layout = D3D12_TEXTURE_LAYOUT_UNKNOWN;
desc.Flags = D3D12_RESOURCE_FLAG_ALLOW_UNORDERED_ACCESS;  // 允许生成mipmap

ID3D12Resource* texture = nullptr;
device->CreateCommittedResource(
    &uploadHeapProps, D3D12_HEAP_FLAG_NONE,
    &desc, D3D12_RESOURCE_STATE_COPY_DEST,
    nullptr, IID_PPV_ARGS(&texture)
);

// 加载数据后,生成mipmap:
// (需要使用ComputeShader或DirectCompute)
GenerateMipmaps(commandList, texture);

// Shader中正确使用Mipmap:
Texture2D tex : register(t0);
SamplerState sam : register(s0);

float4 PS_Main(PS_INPUT input) : SV_Target {
    // 自动LOD选择:
    return tex.Sample(sam, input.uv);
    
    // 或显式LOD:
    float lod = CalcLOD(input.uv);
    return tex.SampleLevel(sam, input.uv, lod);
}
```

**修复方案4: LOD Bias调整**
```cpp
// 问题: LOD Bias设置不当导致过度模糊或清晰

// C++中创建采样器:
D3D12_SAMPLER_DESC samplerDesc = {};
samplerDesc.Filter = D3D12_FILTER_ANISOTROPIC;
samplerDesc.AddressU = D3D12_TEXTURE_ADDRESS_MODE_WRAP;
samplerDesc.AddressV = D3D12_TEXTURE_ADDRESS_MODE_WRAP;
samplerDesc.AddressW = D3D12_TEXTURE_ADDRESS_MODE_WRAP;
samplerDesc.MipLODBias = 0.0f;  // 关键参数
samplerDesc.MaxAnisotropy = 16;
samplerDesc.MinLOD = 0.0f;
samplerDesc.MaxLOD = D3D12_FLOAT32_MAX;

device->CreateSampler(&samplerDesc, samplerHandle);

// 调整bias的指南:
// bias = -1.0 → 更清晰(选择Mip 0-1)
// bias = 0.0  → 自动(推荐)
// bias = 1.0  → 更模糊(选择Mip 1-2)

// Shader中也可以指定LOD:
float4 PS_Main(PS_INPUT input) : SV_Target {
    // 计算导数以确定LOD
    float lodX = length(ddx(input.uv));
    float lodY = length(ddy(input.uv));
    float lod = log2(max(lodX, lodY)) + lodBias;
    
    return tex.SampleLevel(sam, input.uv, lod);
}
```

#### 6.2 完整修复示例

**原始问题代码**
```cpp
// 问题代码: 多个纹理问题的组合

class TexturedMesh {
    ID3D12Resource* diffuseTexture;
    D3D12_GPU_DESCRIPTOR_HANDLE textureHandle;
    
    void Render() {
        // 问题1: 纹理可能未初始化
        if (!diffuseTexture) {
            // 空指针使用 → 崩溃或黑屏
        }
        
        // 问题2: 纹理未绑定(忘记SetGraphicsRootDescriptorTable)
        // commandList->SetGraphicsRootDescriptorTable(...) 缺失
        
        // 问题3: Shader中有UV计算错误
        // float2 uv = input.position.xy;  // 错误!
    }
};

// Shader代码:
float4 PS_Main(PS_INPUT input) : SV_Target {
    // 问题4: UV未归一化
    float2 uv = input.position.xy;  // 屏幕坐标,不是纹理坐标
    
    // 问题5: 采样器LOD设置过大
    SamplerState samplerBad {
        Filter = LINEAR;
        MipLODBias = 2.0;  // 太大,导致过度模糊
    };
    
    return diffuseTex.Sample(samplerBad, uv);
}
```

**修复后代码**
```cpp
class TexturedMesh {
    ID3D12Resource* diffuseTexture;
    D3D12_GPU_DESCRIPTOR_HANDLE textureHandle;
    D3D12_SAMPLER_DESC samplerDesc;
    
    bool LoadTexture(const char* path) {
        // 修复1: 正确加载纹理
        diffuseTexture = TextureLoader::Load(path);
        if (!diffuseTexture) {
            LOG_ERROR("Failed to load texture: %s", path);
            return false;
        }
        
        // 修复2: 获取正确的descriptor handle
        textureHandle = descriptorHeap->GetGPUHandleAtIndex(TEXTURE_SLOT);
        return true;
    }
    
    void SetupSampler() {
        // 修复5: 设置合理的采样器参数
        samplerDesc.Filter = D3D12_FILTER_ANISOTROPIC;
        samplerDesc.AddressU = D3D12_TEXTURE_ADDRESS_MODE_WRAP;
        samplerDesc.AddressV = D3D12_TEXTURE_ADDRESS_MODE_WRAP;
        samplerDesc.MipLODBias = 0.0f;  // 自动LOD,不偏差
        samplerDesc.MaxAnisotropy = 16;
        samplerDesc.MinLOD = 0.0f;
        samplerDesc.MaxLOD = D3D12_FLOAT32_MAX;
    }
    
    void Render(ID3D12GraphicsCommandList* cmdList) {
        // 修复2: 绑定纹理到着色器
        cmdList->SetGraphicsRootDescriptorTable(TEXTURE_SLOT, textureHandle);
        
        // 顶点着色器会正确传递UV坐标
        // 像素着色器使用预计算的UV
    }
};

// Shader代码:
Texture2D diffuseTex : register(t0);
SamplerState samplerLinear : register(s0);

struct PS_INPUT {
    float2 texCoord : TEXCOORD0;  // 正确的UV来源
};

float4 PS_Main(PS_INPUT input) : SV_Target {
    // 修复3和4: 使用正确的UV坐标
    float2 uv = input.texCoord;  // [0,1]范围
    
    // 修复5: 使用合理的采样器
    float4 color = diffuseTex.Sample(samplerLinear, uv);
    
    return color;
}
```

### 阶段7: 完整验证流程（15分钟）

#### 7.1 修复前后对比
```
步骤1: 保存原始帧
  rdx.frame.take_screenshot("before.png")
  rdx.texture.get_data(...) 记录原始采样值

步骤2: 应用修复
  - 修改Shader代码
  - 调整C++绑定代码
  - 重新编译

步骤3: 重新渲染
  rdx.frame.take_screenshot("after.png")

步骤4: 对比
  - 纹理是否出现
  - 清晰度是否改善
  - 色彩是否正确
```

#### 7.2 逐项验证
```
验证1: 纹理绑定
  - 纹理是否可见
  - 颜色是否与纹理相符

验证2: UV坐标
  - UV渐变Shader的结果
  - 是否显示平滑渐变

验证3: Mipmap
  - 远距离物体是否清晰
  - 近距离物体是否清晰

验证4: LOD Bias
  - 清晰度是否适宜
  - 是否存在闪烁
```

#### 7.3 回归测试
```
测试1: 不同分辨率纹理
  - 512x512
  - 1024x1024  
  - 2048x2048
  验证: 所有尺寸都正确显示

测试2: 不同采样器设置
  - WRAP vs CLAMP
  - LINEAR vs POINT
  - 各向异性等级
  验证: 各设置都有效

测试3: 不同UV范围
  - 标准[0,1]
  - 缩放[0,2]
  - 负数
  验证: 行为符合寻址模式

测试4: 边界情况
  - 极小物体(远处)
  - 极大物体(近处)
  - 倾斜表面
  验证: 采样正确
```

---

## 第四部分：输出格式

```json
{
  "execution_card": "SOP-TEX-01",
  "session_id": "debug_session_tex",
  "timestamp": "2026-02-20T12:00:00Z",
  
  "diagnosis": {
    "matched_invariants": ["I-TEX-01", "I-MIP-01", "I-LOD-01"],
    "violation_type": "Mipmap chain incomplete and LOD Bias excessive",
    "severity": "HIGH",
    "confidence": 0.87
  },
  
  "root_cause": {
    "primary": "Mipmap chain not fully generated during texture load",
    "secondary": "LOD Bias set to 2.0, causing excessive blur",
    "evidence": {
      "mip_count": 5,
      "expected_mip_count": 11,
      "mipmap_complete": false,
      "lod_bias": 2.0,
      "recommended_lod_bias": 0.0
    }
  },
  
  "evidence": {
    "texture_info": {
      "texture_id": "DiffuseTexture",
      "format": "R8G8B8A8_UNORM",
      "dimensions": [1024, 1024],
      "mip_count": 5,
      "expected_mips": 11,
      "issue": "Mipmap chain incomplete"
    },
    "sampling_analysis": {
      "sampler_lod_bias": 2.0,
      "auto_lod": 4.0,
      "final_lod": 6.0,
      "selected_mip": "Capped at 5 (max available)",
      "effect": "Blurry rendering"
    },
    "uv_analysis": {
      "uv_range": [0.0, 1.0],
      "uv_valid": true,
      "address_mode": "WRAP"
    }
  },
  
  "fix": {
    "strategy": "Regenerate complete mipmap chain and adjust LOD bias",
    "priority": "IMMEDIATE",
    "changes": [
      {
        "type": "TEXTURE_LOADING",
        "file": "Source/TextureLoader.cpp",
        "change": "Set D3D12_RESOURCE_DESC.MipLevels = 0 to auto-generate full chain"
      },
      {
        "type": "SAMPLER_ADJUSTMENT",
        "file": "Source/Renderer.cpp",
        "before": "samplerDesc.MipLODBias = 2.0f;",
        "after": "samplerDesc.MipLODBias = 0.0f;  // Use auto LOD"
      }
    ]
  },
  
  "verification": {
    "before": {
      "mip_count": 5,
      "lod_bias": 2.0,
      "visual_quality": "Blurry",
      "clarity_score": 0.3
    },
    "after": {
      "mip_count": 11,
      "lod_bias": 0.0,
      "visual_quality": "Clear",
      "clarity_score": 0.9,
      "improvement_percent": 200.0
    }
  },
  
  "quick_reference": {
    "symptoms": [
      "Texture appears blurry or pixelated",
      "Texture missing or black",
      "Texture edges show artifacts"
    ],
    "root_causes": [
      "Texture not bound to shader",
      "UV coordinates out of range",
      "Mipmap chain incomplete",
      "LOD Bias set incorrectly"
    ],
    "quick_fixes": [
      "Verify texture binding",
      "Check UV coordinate generation",
      "Regenerate mipmap chain",
      "Adjust LOD Bias to 0.0"
    ]
  },
  
  "execution_time": {
    "phase1_localization": 600,
    "phase2_uv_analysis": 900,
    "phase3_mipmap_analysis": 900,
    "phase4_root_cause": 1200,
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

| 症状 | 第一检查 | 根因 | 修复 |
|------|---------|------|------|
| 全黑 | 纹理绑定 | 未绑定或格式错误 | 重新绑定 |
| 全白 | 纹理内容 | 纹理未初始化 | 重新加载 |
| 模糊 | Mip_count | Mipmap不完整或bias过大 | 生成Mipmap,调整bias |
| 清晰过头 | LOD_bias | bias过小 | 增加bias值 |
| 重复 | UV值 | WRAP模式下超范围 | 归一化UV |
| 拉伸 | 宽高比 | CLAMP或比例错误 | 调整UV范围 |

---

