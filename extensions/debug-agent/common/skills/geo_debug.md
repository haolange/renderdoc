# SOP-GEO-01: 物体消失问题排查 - 完整执行卡

## 基本信息

```yaml
sop_id: SOP-GEO-01
skill_name: 物体消失/隐形问题排查
category: 几何问题
severity: HIGH
invariants:
  - I-GEO-01: MVP矩阵必须保证对象在视锥体内
  - I-GEO-02: 剔除配置必须与网格缠绕一致
  - I-GEO-03: 索引缓冲必须有效
version: 2.0
standalone: true
```

---

## 第一部分：触发条件与诊断

### 1.1 症状标签

| 标签 | 特征 | 场景 | 根因倾向 |
|------|------|------|---------|
| `object_missing` | 物体不可见 | 整个物体消失 | MVP错误或剔除 |
| `object_invisible` | 应该可见但看不到 | 摄像机指向物体 | 剔除反向 |
| `backface` | 仅看到背面或仅看不到背面 | 剔除半边 | 剔除设置或法线反向 |
| `culling` | 物体被意外剔除 | 摄像机移动时消失 | 剔除模式错误 |
| `frustum` | 物体超出视锥体 | 移动摄像机后出现 | 视锥体计算错误 |
| `z_clipping` | 物体被近/远平面裁剪 | 靠近或远离摄像机消失 | Near/Far设置 |

### 1.2 触发标签

- 物体在编辑器中可见但游戏中不可见
- 某个特定物体的DrawCall无输出
- 改变摄像机位置后物体出现/消失
- 物体的背面/正面异常剔除

### 1.3 违反的不变量

**I-GEO-01**: MVP一致性
```
违反表现:
  - View或Projection矩阵计算错误
  - Model矩阵包含非均匀缩放 + 负缩放
  - 矩阵传递错误

检查:
  顶点变换: clipPos = viewProj * worldPos
  对所有顶点: -w ≤ x,y,z ≤ w
  若违反 → 在视锥体外
```

**I-GEO-02**: 剔除一致性
```
违反表现:
  - CullMode.Back 但法线指向相反方向
  - 负缩放导致法线反向但未调整剔除
  - 动态改变剔除需更新法线

检查:
  顶点缠绕: CCW(逆时针) → 正面
  剔除模式: CullMode.Back → 剔除背面(CW)
  若法线反向 → 需反转缠绕或改为CullMode.Front
```

**I-GEO-03**: 索引有效性
```
违反表现:
  - 索引越界访问
  - 索引顺序错误导致三角形反向
  - IndexBuffer未绑定

检查:
  0 ≤ 索引值 < 顶点数
  三角形卷绕: (v0→v1→v2) 逆时针为正面
```

---

## 第二部分：决策树

```
┌─ 物体消失
│
├─ 物体是否在列表中(DrawCall存在)?
│  ├─ NO → 物体未加载/未添加到场景 (路径A)
│  └─ YES → 继续诊断 (路径B)
│
├─ DrawCall的顶点计数是否为0?
│  ├─ YES → 没有几何体被提交 (路径C)
│  └─ NO → 继续诊断 (路径D)
│
├─ 视锥体裁剪是否移除了物体?
│  ├─ YES → 物体超出视锥体 (路径E)
│  └─ NO → 继续诊断 (路径F)
│
├─ 剔除是否移除了所有三角形?
│  ├─ YES → 背面剔除问题 (路径G)
│  └─ NO → 继续诊断 (路径H)
│
├─ 深度测试是否隐藏了物体?
│  ├─ YES → 深度问题 (路径I)
│  └─ NO → 其他原因 (路径J)
│
└─ 物体是否渲染到屏幕外?
   ├─ 部分渲染 → 变换错误 (路径K)
   └─ 完全在外 → 矩阵错误 (路径L)
```

### 决策树详细说明

**路径A: 物体未加载**
- 检查: 物体是否出现在场景树中
- 修复: 检查加载代码或手动添加

**路径E: 视锥体裁剪**
- 检查: MVP矩阵计算
- 修复: 调整矩阵或摄像机设置

**路径G: 背面剔除**
- 症状: 摄像机旋转后物体消失
- 修复: 调整CullMode或法线方向

---

## 第三部分：详细执行阶段

### 阶段1: 快速定位（10分钟）

#### 1.1 确认物体存在于场景
```
工具调用:
  rdx.api.get_log(
    session_id="debug_session_geo",
    from_event_id=1,
    to_event_id=100
  )

期望输出:
  {
    "events": [
      {
        "event_id": 45,
        "api_call": "DrawIndexed",
        "vertex_count": 36,
        "start_index": 0,
        "base_vertex": 0
      }
    ]
  }

检查:
  1. 是否存在该物体的DrawCall
  2. vertex_count > 0
  3. 索引范围有效

判断:
  - DrawCall存在 + vertex_count > 0 → 物体存在，继续调查
  - DrawCall不存在 → 物体未添加，需检查加载逻辑
  - vertex_count = 0 → 传递了空网格
```

#### 1.2 获取物体的MVP矩阵
```
工具调用:
  rdx.buffer.get_data(
    session_id="debug_session_geo",
    buffer_id="ConstantBuffer_PerObject",
    event_id=45,  // 物体的DrawCall事件
    offset=0,
    size=256  // 足够存储多个矩阵
  )

期望输出:
  {
    "buffer_data": {
      "model_matrix": [
        [1.0, 0.0, 0.0, 0.0],
        [0.0, 1.0, 0.0, 5.0],
        [0.0, 0.0, 1.0, 0.0],
        [0.0, 0.0, 0.0, 1.0]
      ],
      "view_matrix": [...],
      "projection_matrix": [...]
    }
  }

初步检查:
  1. Model矩阵: 是否存在异常缩放 (行列式为0或负数)
  2. View矩阵: 是否指向物体
  3. Projection矩阵: Near/Far值合理吗
```

#### 1.3 视觉检查与管线状态
```
工具调用:
  rdx.pipeline.get_state(
    session_id="debug_session_geo",
    event_id=45
  )

期望输出:
  {
    "rasterizer_state": {
      "cull_mode": "BACK",
      "front_ccw": true,
      "depth_bias": 0,
      "slope_scaled_depth_bias": 0.0
    },
    "depth_stencil_state": {
      "depth_enable": true,
      "depth_func": "LESS",
      "depth_write": true
    },
    "blend_state": {
      "alpha_to_coverage": false,
      "independent_blend": false,
      "targets": [
        {
          "blend_enable": false
        }
      ]
    }
  }

检查:
  1. Cull Mode: 是否为None (禁用剔除)
  2. Front CCW: 顶点缠绕
  3. 深度测试: 是否启用
  4. 混合模式: 是否影响可见性
```

### 阶段2: 矩阵变换分析（15分钟）

#### 2.1 矩阵验证工具链
```
工具调用:
  rdx.shader.get_debug(
    session_id="debug_session_geo",
    event_id=45,
    x=960,
    y=540,
    shader_stage="VertexShader",
    include_all_variables=true
  )

期望输出:
  {
    "execution_trace": [
      {
        "instruction": "float4 worldPos = mul(float4(position, 1), worldMatrix);",
        "value": [10.0, 5.0, 0.0, 1.0],
        "note": "物体在世界坐标系中的位置"
      },
      {
        "instruction": "float4 viewPos = mul(worldPos, viewMatrix);",
        "value": [-2.0, 1.0, 10.0, 1.0],
        "note": "相对于摄像机的位置"
      },
      {
        "instruction": "float4 clipPos = mul(viewPos, projMatrix);",
        "value": [-0.2, 0.1, 0.99, 1.0],
        "note": "裁剪坐标"
      }
    ],
    "clip_analysis": {
      "clip_x": -0.2,
      "clip_y": 0.1,
      "clip_z": 0.99,
      "clip_w": 1.0,
      "in_frustum": true,
      "notes": "-w ≤ x,y,z ≤ w? -1 ≤ -0.2 ≤ 1? YES"
    }
  }

分析步骤:
  1. 检查clipPos是否满足: -w ≤ x,y,z ≤ w
  2. 若不满足 → 顶点超出视锥体
  3. 检查变换的中间步骤是否异常

判断标准:
  - clipPos完全在[-w,w]范围内 → 在视锥体内
  - clipPos多个分量在范围外 → 被裁剪
  - clipPos = [0,0,0,0] → 严重错误(通常为矩阵全0)
```

#### 2.2 矩阵数值检查
```
工具调用:
  rdx.buffer.get_data(
    session_id="debug_session_geo",
    buffer_id="ConstantBuffer_PerFrame",
    event_id=45,
    offset=0,
    size=512
  )

手工计算验证:

// 检查1: Model矩阵行列式(是否为0或负数)
float det = determinant(modelMatrix);
期望: det > 0.0001f
异常: det < 0 → 包含负缩放 (需调整剔除)
异常: det ≈ 0 → 扁平化 (可能是导出错误)

// 检查2: View矩阵是否指向物体
Vector3 cameraPos = inverse(viewMatrix).row3;
Vector3 cameraDir = -inverse(viewMatrix).row2;  // 通常为-Z
Vector3 toObject = worldPos - cameraPos;
float dotProduct = dot(cameraDir, normalize(toObject));
期望: dotProduct > 0  (物体在摄像机前面)
异常: dotProduct < 0  (物体在摄像机后面)

// 检查3: 视锥体边界
float fov = 45.0;  // 度数
float aspect = 1920.0 / 1080.0;
float near = 0.1;
float far = 1000.0;
期望: near 和 far 值合理
异常: near > far
异常: near 接近0 (会导致精度问题)
```

#### 2.3 网格数据检查
```
工具调用:
  rdx.mesh.get_data(
    session_id="debug_session_geo",
    object_id=45,  // DrawCall对应的物体ID
    include_indices=true,
    include_normals=true
  )

期望输出:
  {
    "vertex_count": 36,
    "index_count": 36,
    "vertices": [
      {
        "position": [0.0, 1.0, 0.0],
        "normal": [0.0, 1.0, 0.0],
        "uv": [0.5, 0.5]
      },
      ...
    ],
    "indices": [0, 1, 2, 3, 4, 5, ...],
    "topology": "TRIANGLE_LIST",
    "winding": "CCW"
  }

验证步骤:
  1. vertex_count > 0 且 index_count > 0
  2. 索引值范围: 0 ≤ 所有索引 < vertex_count
  3. 法线方向: 预期为外法线(指向观察者)
  4. 网格中心在原点附近（通常）

示例异常:
  - 法线全为(0,0,0) → 网格损坏
  - 索引超范围 → 访问越界
  - 网格在(1000,1000,1000) → 太远可能超出视锥体
```

### 阶段3: 根因分析（20分钟）

#### 3.1 根因检查清单

**检查项1: 视锥体外物体诊断**
```
症状: 整个物体消失，不随摄像机旋转

验证步骤:
  1. 获取顶点最终位置:
     rdx.shader.get_debug(...) → 获取多个顶点的clipPos
  
  2. 检查所有顶点是否均超出视锥体:
     如果所有顶点的X坐标 > w 
     → 物体在右侧超出视锥体
  
  3. 计算物体边界球:
     center = (min + max) / 2
     radius = length(max - min) / 2
     
  4. 检查摄像机到物体距离:
     distance > 1000.0? → 可能超出Far平面
     distance < 0.1? → 可能在Near平面内
  
  5. 矩阵验证:
     检查ViewMatrix是否指向物体
     检查ProjectionMatrix的near/far值

判断:
  - 距离超出near/far范围 → 调整摄像机或near/far值
  - ViewMatrix指向相反方向 → 摄像机矩阵错误
  - clipPos全在范围外 → 物体确实超出视锥体
```

**检查项2: 背面剔除诊断**
```
症状: 物体消失或仅显示一个面的错误方向

验证步骤:
  1. 获取网格缠绕信息:
     rdx.mesh.get_data(...) → winding = CCW or CW
  
  2. 获取剔除模式:
     rdx.pipeline.get_raster_state(...) → cull_mode
  
  3. 法线方向验证:
     rdx.shader.get_debug(..., 多个顶点)
     → 检查法线是否一致指向外侧
  
  4. 矩阵行列式检查:
     det(modelMatrix) = ?
     - det > 0 → 缠绕保留
     - det < 0 → 缠绕反向(需调整剔除或法线)
  
  5. 可视化验证:
     临时禁用剔除: cull_mode = NONE
     重新渲染
     - 若物体出现 → 剔除问题确认
     - 若仍消失 → 其他原因

判断:
  - det < 0 且 cull_mode = BACK → 改为FRONT
  - det > 0 但法线反向 → 调整法线计算
  - 缠绕为CW但期望CCW → 更改缠绕或剔除设置
```

**检查项3: 深度测试遮挡诊断**
```
症状: 物体应该可见但被隐藏

验证步骤:
  1. 检查深度测试状态:
     rdx.pipeline.get_state(...) → depth_enable
  
  2. 深度函数检查:
     depth_func = LESS (默认) → 物体必须最近
     depth_func = GREATER → 物体必须最远
  
  3. 获取深度缓冲:
     rdx.texture.get_data(texture_id="Depth", ...)
     → 检查物体位置的深度值
  
  4. 顶点深度值追踪:
     rdx.shader.get_debug(...) → clip_z / clip_w
     → 标准化深度值应在[0,1]
  
  5. 排除其他物体遮挡:
     暂时删除其他物体
     重新渲染此物体
     - 若出现 → 被其他物体遮挡
     - 若仍消失 → 深度问题

判断:
  - 深度值超出[0,1] → Near/Far计算错误
  - 物体深度值 > 前景物体 → 显示顺序错误
  - depth_func错误 → 改为LESS或合适的函数
```

**检查项4: 索引越界诊断**
```
症状: 物体显示部分或显示错误的几何体

验证步骤:
  1. 获取索引缓冲:
     rdx.buffer.get_data(..., buffer_id="IndexBuffer")
  
  2. 检查索引范围:
     max_index = max(所有索引值)
     vertex_count = ?
     
     若 max_index >= vertex_count
     → 索引越界，行为未定义
  
  3. 索引值分布:
     应该均匀覆盖[0, vertex_count)
     若存在孤立的大值 → 数据错误
  
  4. 三角形卷绕检查:
     对每个三角形(i0, i1, i2):
     - 计算法线: cross(v[i1]-v[i0], v[i2]-v[i0])
     - 应该指向物体外侧
  
  5. 修复验证:
     修复索引后重新渲染
     物体应该正常显示

判断:
  - 存在越界索引 → 数据损坏，需重新导出网格
  - 三角形缠绕错误 → 调整索引顺序或翻转面
```

#### 3.2 根因排查决策表

| 症状 | 第一检查 | 第二检查 | 最可能根因 |
|------|---------|---------|---------|
| 完全消失 | clipPos范围 | 视锥体距离 | Far平面外 |
| 完全消失 | clipPos范围 | 矩阵行列式 | 视锥体外 |
| 一半消失 | 法线方向 | 剔除模式 | 背面剔除反向 |
| 显示混乱 | 索引值 | 顶点数 | 索引越界 |
| 显示小点 | 矩阵行列式 | 缩放值 | 过度缩放 |

### 阶段4: 反事实验证（15分钟）

#### 4.1 必要条件验证

**条件1: 物体必须真的消失**
```
验证方法:
  1. 临时禁用所有其他物体的渲染
  2. 仅渲染目标物体
  3. 改变摄像机位置（靠近、远离、旋转）
  
预期结果: 确认物体确实不可见
```

**条件2: 根因必须可重现**
```
重现测试:
  1. 使用相同的摄像机位置
  2. 相同的物体配置
  3. 相同的Shader
  
结果: 物体应该在相同的位置消失/出现
```

**条件3: 修复必须有效**
```
修复验证:
  1. 应用临时修复（如禁用剔除）
  2. 重新渲染
  3. 观察物体是否出现
```

#### 4.2 替代解释排除

**假设1: 这不是几何问题，而是着色问题（太暗）**
```
排除方法:
  1. 使用调试着色器渲染物体:
     return float4(1, 1, 1, 1);  // 纯白
  2. 若物体仍然不可见 → 确实是几何问题
  3. 若物体可见 → 是着色/颜色问题
```

**假设2: 这是摄像机配置错误**
```
排除方法:
  1. 使用默认摄像机:
     eye = (0, 0, 10), 
     target = (0, 0, 0), 
     up = (0, 1, 0)
  2. 重新渲染
  3. 若物体出现 → 摄像机设置错误
  4. 若物体仍消失 → 是物体配置问题
```

**假设3: 这是其他物体遮挡**
```
排除方法:
  1. 禁用深度测试: depth_enable = false
  2. 重新渲染
  3. 若物体出现 → 被其他物体遮挡
  4. 若物体仍消失 → 确实是几何问题
```

#### 4.3 对比验证

**与参考帧的对比**
```
工具调用:
  1. 在编辑器中查看物体位置
  2. 在RenderDoc中对比
  3. 检查物体变换是否一致

差异分析:
  - 编辑器可见但RenderDoc不可见 → 导出问题
  - 两者都不可见 → 代码逻辑问题
  - 显示位置不同 → 矩阵错误
```

### 阶段5: 修复代码模板（20分钟）

#### 5.1 常见修复方案

**修复方案1: 视锥体外物体（调整摄像机或对象）**
```hlsl
// C++代码: 修复摄像机参数

// 问题: 摄像机Far平面太近
D3DXMATRIX projection;
D3DXMatrixPerspectiveFovLH(&projection, 
    D3DX_PI / 4,      // FOV
    1920.0f / 1080.0f,  // Aspect
    0.1f,   // Near - 不要太小!
    1000.0f // Far - 应该足够大
);

// 修复: 调整Far值或物体位置
// 方案A: 增加Far平面
float far = 5000.0f;  // 增加覆盖范围
D3DXMatrixPerspectiveFovLH(&projection, ..., 0.1f, far);

// 方案B: 调整物体位置
Vector3 objectPos = Vector3(10, 5, 0);  // 确保Z在[near, far]范围内

// 方案C: 验证View矩阵
D3DXMATRIX view;
D3DXVECTOR3 eye(0, 5, 10);
D3DXVECTOR3 target(0, 0, 0);
D3DXVECTOR3 up(0, 1, 0);
D3DXMatrixLookAtLH(&view, &eye, &target, &up);

// 验证: 物体在eye指向的方向
Vector3 toObject = objectPos - eye;
Vector3 viewDir = target - eye;
assert(dot(viewDir, toObject) > 0);  // 物体应在前方
```

**修复方案2: 背面剔除问题**
```hlsl
// C++代码: 修复剔除设置

// 问题: 负缩放导致法线反向但未调整剔除
Matrix modelMatrix = Matrix::CreateScale(-1, 1, 1);  // 负缩放!
float det = determinant(modelMatrix);  // det < 0

// 修复方案1: 禁用剔除(调试用)
D3D12_RASTERIZER_DESC rastDesc = {...};
rastDesc.CullMode = D3D12_CULL_MODE_NONE;  // 禁用剔除

// 修复方案2: 反向剔除模式
if (det < 0) {
    rastDesc.CullMode = D3D12_CULL_MODE_FRONT;  // 改为Front
} else {
    rastDesc.CullMode = D3D12_CULL_MODE_BACK;   // 保持Back
}

// 修复方案3: 调整FrontCCW (顶点缠绕)
rastDesc.FrontCounterClockwise = true;  // 假设顶点为CCW顺序

// 验证: 检查法线方向
// Shader中:
float3 N = normalize(mul(float3(normal), (float3x3)worldMatrix));
// 应该指向观察方向: dot(N, normalize(viewDir)) > 0
```

**修复方案3: 索引越界修复**
```hlsl
// C++代码: 数据验证和修复

// 问题: 索引缓冲包含超范围的值
std::vector<uint32_t> indices = LoadIndicesFromFile();
uint32_t vertexCount = 1024;

// 修复: 验证和修复索引
std::vector<uint32_t> validIndices;
for (uint32_t idx : indices) {
    if (idx < vertexCount) {
        validIndices.push_back(idx);
    } else {
        // 选择: 忽略 / 替换为有效值 / 报错
        validIndices.push_back(idx % vertexCount);  // 模运算修复
        LOG_WARNING("Index out of bounds: %u >= %u", idx, vertexCount);
    }
}

// 或者: 检查并拒绝加载
bool ValidateIndices(const std::vector<uint32_t>& indices, uint32_t vertexCount) {
    for (uint32_t idx : indices) {
        if (idx >= vertexCount) {
            LOG_ERROR("Invalid index: %u >= %u", idx, vertexCount);
            return false;  // 加载失败
        }
    }
    return true;
}
```

**修复方案4: 深度问题修复**
```hlsl
// Shader代码: 深度范围修复

// 问题: 深度值超出[0,1]范围
// 在VS中:
float4 VS_Main(VS_INPUT input) : SV_Position {
    float4 worldPos = mul(float4(input.position, 1), worldMatrix);
    float4 viewPos = mul(worldPos, viewMatrix);
    float4 clipPos = mul(viewPos, projMatrix);
    
    // clipPos.z / clipPos.w 应该在 [0, 1]
    // 若不在 → near/far计算错误
    
    return clipPos;
}

// C++修复: 调整投影矩阵
float near = 0.1f;
float far = 1000.0f;

// 验证near和far的比率
float ratio = far / near;
if (ratio > 10000.0f) {
    LOG_WARNING("Near/Far ratio too high: %f", ratio);
    // 可能导致深度精度问题
    // 解决: 增加near或减少far
    near = 1.0f;   // 增加near
    far = 1000.0f; // 保持far
}

D3DXMATRIX proj;
D3DXMatrixPerspectiveFovLH(&proj, 
    D3DX_PI / 4, 1920.0f/1080.0f, 
    near, far);
```

#### 5.2 完整修复示例

**原始问题代码**
```cpp
// 问题: 多个错误组合
class GameObject {
    Matrix modelMatrix;
    
    void Render() {
        // 问题1: 矩阵应用错误的顺序
        Matrix mvp = model * view * projection;  // 错误!应该是projection*view*model
        
        // 问题2: 摄像机参数不合理
        float near = 0.001f;  // 太小,精度问题
        float far = 100.0f;   // 太小,远处物体裁剪
        
        // 问题3: 负缩放但未调整剔除
        modelMatrix = Matrix::CreateScale(-1, 1, 1);
        
        // 问题4: 剔除设置固定
        rastDesc.CullMode = D3D12_CULL_MODE_BACK;
    }
};
```

**修复后代码**
```cpp
class GameObject {
    Matrix modelMatrix;
    Matrix worldViewProj;  // 缓存MVP矩阵
    
    void Render(const Camera& camera) {
        // 修复1: 正确的矩阵乘法顺序
        // clipPos = projMatrix * viewMatrix * worldMatrix * position
        worldViewProj = modelMatrix * camera.GetViewMatrix() * camera.GetProjectionMatrix();
        
        // 修复2: 合理的摄像机参数
        float near = 0.1f;    // 足够大以避免精度问题
        float far = 5000.0f;  // 足够大以覆盖所有场景
        assert(far / near <= 10000.0f);  // 验证比率
        
        // 修复3: 检查矩阵行列式
        float det = modelMatrix.Determinant();
        if (det < 0) {
            // 负缩放: 调整剔除模式
            rastDesc.CullMode = D3D12_CULL_MODE_FRONT;
        } else {
            rastDesc.CullMode = D3D12_CULL_MODE_BACK;
        }
        
        // 修复4: 验证物体在视锥体内
        Vector3 objectPos = modelMatrix.GetTranslation();
        Vector3 cameraPos = camera.GetPosition();
        float distance = Length(objectPos - cameraPos);
        if (distance < near || distance > far) {
            LOG_WARNING("Object at distance %f outside frustum [%f, %f]", 
                distance, near, far);
        }
    }
};
```

### 阶段6: 完整验证流程（15分钟）

#### 6.1 修复前后对比
```
步骤1: 捕获问题帧
  rdx.frame.take_screenshot("before.png")
  rdx.api.get_log(...) → 记录DrawCall列表

步骤2: 应用修复
  - 修改C++代码
  - 重新编译
  - 确保新的二进制已加载

步骤3: 重新捕获
  rdx.frame.take_screenshot("after.png")
  rdx.api.get_log(...) → 验证DrawCall数量

步骤4: 对比
  - 物体是否出现
  - 位置是否正确
  - 法线方向是否正确（检查高光）
```

#### 6.2 逐个验证修复项
```
验证1: 矩阵变换
  - 检查MVP矩阵值是否合理
  - 验证顶点最终位置在[-w, w]范围内

验证2: 剔除模式
  - 禁用剔除渲染 → 检查是否双倍物体
  - 启用剔除 → 检查剔除方向

验证3: 深度测试
  - 比对深度缓冲可视化
  - 检查物体深度值范围

验证4: 索引有效性
  - rdx.mesh.get_data() → 验证索引范围
  - 检查三角形缠绕方向
```

#### 6.3 回归测试
```
测试套件:

测试1: 摄像机移动
  - 摄像机靠近物体 → 物体应放大
  - 摄像机远离物体 → 物体应缩小
  - 摄像机旋转 → 物体应跟随

测试2: 物体变换
  - 平移物体 → 位置变化
  - 旋转物体 → 方向变化
  - 缩放物体 → 大小变化（含负缩放）

测试3: 多个物体
  - 多个物体同时渲染
  - 遮挡关系正确
  - 深度排序正确

测试4: 极端情况
  - 物体在摄像机后面 → 不显示
  - 物体在near平面内 → 不显示
  - 物体在far平面外 → 不显示
```

---

## 第四部分：输出格式

```json
{
  "execution_card": "SOP-GEO-01",
  "session_id": "debug_session_geo",
  "timestamp": "2026-02-20T11:30:00Z",
  
  "diagnosis": {
    "matched_invariants": ["I-GEO-01", "I-GEO-02"],
    "violation_type": "Negative scale causes backface culling reversal",
    "severity": "HIGH",
    "confidence": 0.88
  },
  
  "root_cause": {
    "category": "Backface Culling Mismatch",
    "description": "Model matrix contains -1.0 scale, reversing face winding. Cull mode still set to BACK, so front faces are culled.",
    "location": {
      "file": "Source/GameObject.cpp",
      "function": "GameObject::SetWorldMatrix",
      "problematic_value": {
        "model_matrix_determinant": -1.0,
        "cull_mode": "BACK",
        "expected_cull_mode": "FRONT"
      }
    }
  },
  
  "evidence": {
    "object_missing": {
      "object_id": "Box_001",
      "draw_call_event": 45,
      "vertex_count": 36,
      "visible": false
    },
    "matrix_analysis": {
      "model_matrix_determinant": -1.0,
      "view_matrix_valid": true,
      "projection_matrix_valid": true,
      "mvp_result": "All vertices have CCW winding reversed (now CW)"
    },
    "culling_mismatch": {
      "rasterizer_cull_mode": "BACK",
      "actual_winding": "CW (clockwise)",
      "expected_winding": "CCW (counter-clockwise)",
      "result": "All faces culled"
    },
    "verification": {
      "with_culling_disabled": {
        "cull_mode": "NONE",
        "object_visible": true,
        "note": "Object appears when culling disabled, confirming culling issue"
      }
    }
  },
  
  "fix": {
    "strategy": "Adjust cull mode based on matrix determinant",
    "priority": "IMMEDIATE",
    "changes": [
      {
        "type": "CODE_LOGIC_FIX",
        "file": "Source/GameObject.cpp",
        "function": "SetWorldMatrix",
        "before": "rastDesc.CullMode = D3D12_CULL_MODE_BACK;",
        "after": "float det = modelMatrix.Determinant(); rastDesc.CullMode = (det < 0) ? D3D12_CULL_MODE_FRONT : D3D12_CULL_MODE_BACK;"
      }
    ]
  },
  
  "verification": {
    "before": {
      "object_visible": false,
      "reason": "Culling removed all faces"
    },
    "after": {
      "object_visible": true,
      "position": [0.0, 0.0, -5.0],
      "orientation": "correct",
      "lighting": "correct"
    }
  },
  
  "recommendations": {
    "immediate": [
      "Apply determinant check to cull mode selection",
      "Re-compile and test",
      "Verify object position and orientation"
    ],
    "medium_term": [
      "Add matrix validation in GameObject constructor",
      "Implement debug visualization for normals",
      "Create unit tests for matrix transformations"
    ],
    "long_term": [
      "Develop transform validation framework",
      "Implement automatic cull mode adjustment",
      "Add shader-based normal visualization tools"
    ]
  },
  
  "related_invariants": {
    "I-GEO-01": "MVP matrix must ensure object is in frustum",
    "I-GEO-02": "Cull configuration must match mesh winding",
    "I-GEO-03": "Index buffer must be valid and in range"
  },
  
  "execution_time": {
    "phase1_localization": 600,
    "phase2_matrix_analysis": 900,
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
| 完全消失 | clipPos范围 | 视锥体外 | 调整摄像机/Far |
| 完全消失 | 法线方向 | 背面剔除 | 改CullMode或法线 |
| 一半显示 | 索引值 | 索引越界 | 修复网格数据 |
| 显示错误 | 矩阵det | 负缩放 | 动态调整CullMode |
| 闪烁消失 | near/far | 深度裁剪 | 扩大深度范围 |

---

