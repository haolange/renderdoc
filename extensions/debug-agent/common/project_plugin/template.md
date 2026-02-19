# Project Plugin Docs 模板

## 项目私有知识插件

项目特定的渲染管线知识封装。

### 材质模块定义

```yaml
material_modules:
  - module_name: CharacterSkinBlock
    description: 角色皮肤渲染模块
    blocks:
      - block_name: skin_diffuse
        hlsl_function: CalcSkinDiffuse
        inputs: [worldPos, normal, viewDir]
        outputs: [diffuseColor]

      - block_name: skin_specular
        hlsl_function: CalcSkinSpecular
        inputs: [worldPos, normal, viewDir, roughness]
        outputs: [specularColor]

# Block计算指纹
block_fingerprints:
  - block_name: skin_diffuse
    hlsl_expression: "dot(normal, lightDir)"
    ir_pattern: "FMul.F32 * FDiv.F32"

# Block与引擎资源映射
resource_mappings:
  - block_name: skin_diffuse
    engine_resource: CharacterSkinMaterial
    material_instance: SkinMaterialInstance
    parameters:
      albedo: texture2D
      normal_map: texture2D
      roughness: float

# 项目特定不变量
project_invariants:
  - id: I-PROJECT-SKIN-01
    definition: 角色皮肤渲染的法线必须有效非零
    context: 仅适用于CharacterSkinBlock模块
```

---

# Project Plugin 示例

```yaml
project_name: MyGameEngine
project_version: 1.0.0

material_modules:
  - module_name: PBRStandard
    description: 标准PBR材质
    blocks:
      - block_name: albedo
        hlsl_function: CalcAlbedo
        inputs: [baseColor, metallic, roughness]
        outputs: [albedo]

      - block_name: lighting
        hlsl_function: CalcPBRLighting
        inputs: [albedo, NdotL, NdotV, roughness]
        outputs: [finalColor]

block_fingerprints:
  - block_name: albedo
    hlsl_expression: "baseColor * (1.0 - metallic)"
    ir_pattern: "FMul.F32 * FSub.F32"

resource_mappings:
  - block_name: albedo
    engine_resource: PBRMaterial
    material_instance: PBRMaterialInstance
    parameters:
      albedoMap: texture2D
      normalMap: texture2D
      metallicMap: texture2D
      roughnessMap: texture2D

project_invariants:
  - id: I-PROJECT-PBR-01
    definition: PBR材质的metallic值必须在[0,1]范围内
    context: 全局PBR渲染
```
