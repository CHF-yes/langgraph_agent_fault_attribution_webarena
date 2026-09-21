# 架构图

此目录包含项目文档所用两张智能体架构图的独立绘图代码及 SVG 输出文件。

仅使用 Python 标准库即可重新生成两张图：

```bash
python3 docs/diagrams/generate_architecture_diagrams.py
```

使用项目已有的 Playwright 环境生成可直接查看的 PNG 预览图：

```bash
python3 docs/diagrams/render_png_previews.py
```

生成文件：

- `react_architecture.svg`：标准 ReAct 控制循环与每轮模型输入。
- `plan_and_execute_architecture.svg`：单模型的规划、执行、工具调用与重规划工作流。
- `project_overview.svg`：项目核心模块、运行流程和实验支持的简洁总览。
- 对应的 `.png` 文件：可直接预览和下载的 PNG 图片。
