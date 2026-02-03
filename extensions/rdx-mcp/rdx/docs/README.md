# RDX-MCP 文档

RDX-MCP（`rdx-mcp`）是一个基于 Model Context Protocol（MCP）的 RenderDoc 自动化 GPU 调试服务：它把“打开 capture → 定位异常 → 二分定位首个坏事件 → 抽取 pipeline/shader → 生成与验证修复假设 → 生成报告”的链路封装为一组可组合的 MCP tools，并提供一个端到端的一键 pipeline 工具。

## 这套文档写给谁

- **使用者（集成到 Agent / 桌面客户端 / Web 客户端）**：想快速把 RenderDoc 调试能力接入 MCP，并用工具完成分析与产出报告。
- **开发者（扩展与维护）**：想新增 verifier / patch 逻辑 / KB 索引来源，或补全 pipeline 步骤。

## 使用前提

- Python `>= 3.10`。
- 需要本仓库的 RenderDoc 源码并完成本地编译，生成 `renderdoc.pyd` 与 `renderdoc.dll`。
- 启动时需能 `import renderdoc`：在默认构建布局下 `run.bat` 会自动探测；否则设置 `RDX_RENDERDOC_PATH`。

## 目录

- 快速开始：`quickstart.md`（见 [quickstart.md](quickstart.md)）
- 配置与目录结构：`configuration.md`（见 [configuration.md](configuration.md)）
- MCP 工具手册（21 个 tools）：`tools.md`（见 [tools.md](tools.md)）
- 工作流（S0–S7）与端到端 Pipeline：`workflows.md`（见 [workflows.md](workflows.md)）
- 常见问题排查：`troubleshooting.md`（见 [troubleshooting.md](troubleshooting.md)）

## 能做什么（按能力块理解）

- **会话与 capture**
  - 创建/关闭 replay session：`rd.session.create`、`rd.session.close`
  - 打开 capture：`rd.capture.open`、读取事件树：`rd.capture.get_event_tree`
- **定位与验证**
  - 设置当前事件：`rd.event.set`
  - 找首个坏事件（bisect）：`rd.event.bisect_first_bad`
  - 渲染输出与读回：`rd.output.render`、`rd.output.readback`
  - Verifiers：`rd.verify.naninf`、`rd.verify.image_diff`
- **检查 pipeline / shader / pixel**
  - pipeline 快照：`rd.pipeline.snapshot`
  - 导出 shader 相关 artifacts：`rd.shader.export_artifacts`
  - 像素级 shader 调试：`rd.debug.pixel`
- **热补丁与实验**
  - 应用/回滚 shader patch：`rd.patch.apply`、`rd.patch.revert`
  - 运行单次实验并对比：`rd.experiment.run`
- **性能与报告**
  - 采样性能计数器：`rd.perf.sample_counters`
  - 生成自包含报告包：`rd.report.build_bundle`
- **知识库与指纹复用**
  - KB 检索：`rd.kb.search`
  - fingerprint 匹配：`rd.fingerprint.match`
- **一键端到端**
  - 全流程 pipeline：`rd.pipeline.run_full_debug`
