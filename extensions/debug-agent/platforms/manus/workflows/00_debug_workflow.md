# RenderDoc Debug Agent 工作流

## Agent团队

1. Team Lead - 协调者：接收问题，分解任务，汇总裁决
2. Triage - 症状分类：将现象量化，归入症状分类学
3. Capture - 捕获复现：设计并执行重现与捕获策略
4. Pipeline - 管线分析：在渲染管线中定位异常的Pass或Event
5. Forensics - 像素取证：追溯像素历史，检查NaN/Inf等问题
6. Shader - Shader分析：关联HLSL到IR/SPIR-V，定位精度问题
7. Driver - 驱动专家：进行GPU/驱动维度的归因
8. Skeptic - 审查者：寻找反例，挑战假设，质疑因果
9. Curator - 知识策展：生成BugFull/BugCard，更新知识库

## 工作流

### 入口

用户输入问题描述或上传捕获文件

### 流程

```
用户 → Team Lead → 分派任务
                ↓
        ┌───────┴───────┐
        ↓               ↓
    6个行动Agent    Skeptic审查
        ↓               ↓
        └───────┬───────┘
                ↓
           Curator报告
```

### 出口

生成调试报告，更新知识库
