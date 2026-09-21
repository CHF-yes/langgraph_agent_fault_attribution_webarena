#!/usr/bin/env python3
"""Generate standalone SVG diagrams for the agent architecture documentation."""

from pathlib import Path
from xml.sax.saxutils import escape


OUTPUT_DIR = Path(__file__).parent

COLORS = {
    "ink": "#152033",
    "muted": "#54657a",
    "line": "#2c415c",
    "panel": "#f7f9fc",
    "border": "#ccd6e2",
    "input": "#e8f1ff",
    "input_border": "#84aee8",
    "model": "#dff5ec",
    "model_border": "#48a880",
    "tool": "#fff0d9",
    "tool_border": "#e49a32",
    "decision": "#f2e9ff",
    "decision_border": "#9568cf",
    "start_end": "#eef2f6",
}


def svg_text(x, y, text, size=16, weight=400, fill=None, anchor="middle"):
    return (
        f'<text x="{x}" y="{y}" text-anchor="{anchor}" '
        f'font-family="Noto Sans CJK SC, Microsoft YaHei, Arial, sans-serif" font-size="{size}" '
        f'font-weight="{weight}" fill="{fill or COLORS["ink"]}">{escape(text)}</text>'
    )


def rounded_box(x, y, width, height, label, fill, stroke, subtitle=None):
    parts = [
        f'<rect x="{x}" y="{y}" width="{width}" height="{height}" rx="12" '
        f'fill="{fill}" stroke="{stroke}" stroke-width="2"/>',
        svg_text(x + width / 2, y + (height / 2 if not subtitle else height / 2 - 8), label, 18, 700),
    ]
    if subtitle:
        parts.append(svg_text(x + width / 2, y + height / 2 + 17, subtitle, 12, 400, COLORS["muted"]))
    return "\n".join(parts)


def pill(x, y, width, label):
    return "\n".join(
        [
            f'<rect x="{x}" y="{y}" width="{width}" height="34" rx="17" '
            f'fill="{COLORS["start_end"]}" stroke="{COLORS["border"]}" stroke-width="1.5"/>',
            svg_text(x + width / 2, y + 22, label, 13, 700),
        ]
    )


def arrow(x1, y1, x2, y2, label=None, label_x=None, label_y=None, dashed=False):
    dash = ' stroke-dasharray="7 5"' if dashed else ""
    parts = [
        f'<path d="M {x1} {y1} L {x2} {y2}" fill="none" stroke="{COLORS["line"]}" '
        f'stroke-width="2.3" marker-end="url(#arrowhead)"{dash}/>'
    ]
    if label:
        parts.append(svg_text(label_x, label_y, label, 13, 600, COLORS["muted"]))
    return "\n".join(parts)


def curved_arrow(path, label=None, label_x=None, label_y=None):
    parts = [
        f'<path d="{path}" fill="none" stroke="{COLORS["line"]}" stroke-width="2.3" '
        'marker-end="url(#arrowhead)"/>'
    ]
    if label:
        parts.append(svg_text(label_x, label_y, label, 13, 600, COLORS["muted"]))
    return "\n".join(parts)


def document(title, subtitle, width, height, body):
    heading = ""
    if title:
        heading += f'\n  <text x="48" y="54" font-family="Noto Sans CJK SC, Microsoft YaHei, Arial, sans-serif" font-size="28" font-weight="700" fill="{COLORS["ink"]}">{escape(title)}</text>'
    if subtitle:
        heading += f'\n  <text x="48" y="82" font-family="Noto Sans CJK SC, Microsoft YaHei, Arial, sans-serif" font-size="15" fill="{COLORS["muted"]}">{escape(subtitle)}</text>'
    return f'''<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" viewBox="0 0 {width} {height}" role="img" aria-labelledby="title description">
  <title id="title">{escape(title)}</title>
  <desc id="description">{escape(subtitle)}</desc>
  <defs>
    <marker id="arrowhead" markerWidth="10" markerHeight="8" refX="8" refY="4" orient="auto">
      <path d="M 0 0 L 9 4 L 0 8 z" fill="{COLORS["line"]}"/>
    </marker>
    <marker id="white-arrowhead" markerWidth="10" markerHeight="8" refX="8" refY="4" orient="auto">
      <path d="M 0 0 L 9 4 L 0 8 z" fill="white"/>
    </marker>
    <marker id="outlined-arrowhead" markerWidth="12" markerHeight="10" refX="10" refY="5" orient="auto">
      <path d="M 0 0 L 11 5 L 0 10 z" fill="white" stroke="#152033" stroke-width="1.8" stroke-linejoin="round"/>
    </marker>
  </defs>
  <rect width="100%" height="100%" fill="white"/>
  {heading}
  {body}
</svg>
'''


def react_diagram():
    body = []
    body.append(f'<rect x="38" y="108" width="1124" height="466" rx="16" fill="{COLORS["panel"]}" stroke="{COLORS["border"]}"/>')
    body.append(svg_text(62, 142, "控制流程", 12, 700, COLORS["muted"], "start"))
    body.append(pill(74, 234, 96, "开始"))
    body.append(rounded_box(254, 194, 198, 114, "智能体", COLORS["model"], COLORS["model_border"], "单次模型调用"))
    body.append(rounded_box(548, 194, 184, 114, "工具", COLORS["tool"], COLORS["tool_border"], "执行一个动作"))
    body.append(pill(1008, 234, 96, "结束"))
    body.append(arrow(170, 251, 254, 251))
    body.append(arrow(452, 251, 548, 251, "调用工具", 500, 232))
    body.append(curved_arrow("M 732 251 C 806 251, 808 160, 720 160 L 366 160 C 322 160, 320 184, 322 194", "新的页面观测", 566, 143))
    body.append(curved_arrow("M 452 286 C 600 382, 864 382, 1008 251", "无工具调用，输出最终答案", 735, 399))
    body.append(svg_text(62, 448, "每轮模型输入", 12, 700, COLORS["muted"], "start"))
    body.append(f'<rect x="74" y="470" width="802" height="72" rx="12" fill="white" stroke="{COLORS["border"]}" stroke-width="1.5"/>')
    inputs = [
        (90, 482, 156, "任务"),
        (262, 482, 116, "URL"),
        (394, 482, 220, "无障碍树"),
        (630, 482, 230, "历史消息"),
    ]
    for x, y, width, label in inputs:
        body.append(rounded_box(x, y, width, 48, label, COLORS["input"], COLORS["input_border"]))
    body.append(arrow(353, 470, 353, 308, "读取当前输入", 416, 392))
    body.append(svg_text(930, 504, "每轮至多一次工具调用", 14, 700, COLORS["muted"], "start"))
    return document(
        "标准 ReAct 架构",
        "模型读取任务与浏览器状态，随后调用至多一个工具，或直接结束运行。",
        1200,
        620,
        "\n  ".join(body),
    )


def plan_execute_diagram():
    body = []
    body.append(f'<rect x="38" y="108" width="1124" height="438" rx="16" fill="{COLORS["panel"]}" stroke="{COLORS["border"]}"/>')
    body.append(svg_text(62, 142, "单模型多阶段工作流", 12, 700, COLORS["muted"], "start"))
    body.append(pill(68, 268, 96, "开始"))
    body.append(rounded_box(220, 224, 176, 104, "规划器", COLORS["decision"], COLORS["decision_border"], "结构化子目标"))
    body.append(rounded_box(452, 224, 178, 104, "执行器", COLORS["model"], COLORS["model_border"], "每轮一个动作"))
    body.append(rounded_box(686, 224, 152, 104, "工具", COLORS["tool"], COLORS["tool_border"], "浏览器动作"))
    body.append(rounded_box(894, 224, 184, 104, "重规划器", COLORS["decision"], COLORS["decision_border"], "推进 / 结束 / 重规划"))
    body.append(arrow(164, 285, 220, 285))
    body.append(arrow(396, 276, 452, 276))
    body.append(arrow(630, 276, 686, 276))
    body.append(arrow(838, 276, 894, 276))
    body.append(pill(968, 420, 96, "结束"))
    body.append(arrow(986, 328, 1016, 420, "结束", 1048, 384))
    body.append(curved_arrow("M 986 224 C 966 166, 818 162, 748 165 L 304 165 C 256 165, 250 196, 273 224", "重规划", 612, 149))
    body.append(curved_arrow("M 894 309 C 837 387, 641 397, 559 328", "推进至下一子目标", 730, 390))
    body.append(svg_text(62, 380, "职责边界", 12, 700, COLORS["muted"], "start"))
    role_rows = [
        (74, "规划器", "根据任务和观测生成、修订结构化子目标。"),
        (74, "执行器", "每个执行轮次只为当前子目标选择一个动作。"),
        (74, "重规划器", "依据工具反馈决定推进、结束或返回规划。"),
    ]
    for y, role, description in [(405, *role_rows[0][1:]), (445, *role_rows[1][1:]), (485, *role_rows[2][1:])]:
        body.append(svg_text(74, y, role, 14, 700, COLORS["ink"], "start"))
        body.append(svg_text(188, y, description, 14, 400, COLORS["muted"], "start"))
    body.append(svg_text(1112, 514, "不是多模型、多智能体系统", 13, 700, COLORS["muted"], "end"))
    return document(
        "计划与执行架构",
        "同一个模型在规划、执行和重规划阶段被调用，并共享同一组浏览器工具。",
        1200,
        580,
        "\n  ".join(body),
    )


def project_overview_diagram():
    body = []
    body.append(f'<rect x="38" y="38" width="1124" height="490" rx="16" fill="{COLORS["panel"]}" stroke="{COLORS["border"]}"/>')
    body.append(svg_text(62, 76, "主要核心链路", 15, 700, COLORS["muted"], "start"))
    body.append(svg_text(62, 368, "故障注入分支链路", 15, 700, COLORS["muted"], "start"))
    body.append(rounded_box(62, 214, 150, 100, "LLM 架构", "#d8e7f8", "#84aee8", "模型与工作流"))
    body.append(rounded_box(256, 214, 150, 100, "Agent", "#ccebdc", "#48a880", "实验执行主体"))
    body.append(rounded_box(450, 214, 150, 100, "WebArena 测试", "#fae4bd", "#e49a32", "任务与交互环境"))
    body.append(rounded_box(644, 214, 150, 100, "分析", "#e4d6f5", "#9568cf", "轨迹与评估数据"))
    body.append(rounded_box(838, 214, 150, 100, "责任占比", "#f7d9de", "#d86a7a", "故障归因结果"))
    body.append(rounded_box(1032, 214, 112, 100, "针对性修复", "#d5ebda", "#5eaf72", "优化迭代"))
    body.append(rounded_box(210, 400, 220, 82, "故障注入", "#fae4bd", "#e49a32", "构造可控故障"))
    body.append(rounded_box(590, 400, 250, 82, "受控故障 WebArena", "#d8e7f8", "#84aee8", "故障测试环境"))
    for x1, x2, label, label_x in [
        (212, 256, "构成", 234),
        (406, 450, "驱动", 428),
        (600, 644, "记录", 622),
        (794, 838, "量化", 816),
        (988, 1032, "修复", 1010),
    ]:
        body.append(f'<path d="M {x1} 264 L {x2} 264" fill="none" stroke="#152033" stroke-width="2.5" marker-end="url(#arrowhead)"/>')
        body.append(svg_text(label_x, 246, label, 13, 700, COLORS["ink"]))
    body.append(arrow(430, 441, 590, 441, "注入", 510, 423))
    body.append('<path d="M 715 400 L 715 350 L 525 350 L 525 314" fill="none" stroke="#152033" stroke-width="2.5" stroke-linejoin="round" marker-end="url(#arrowhead)"/>')
    body.append(svg_text(626, 344, "进入 WebArena 测试", 13, 700, COLORS["ink"]))
    return document(
        "",
        "",
        1200,
        570,
        "\n  ".join(body),
    )


def main():
    diagrams = {
        "react_architecture.svg": react_diagram(),
        "plan_and_execute_architecture.svg": plan_execute_diagram(),
        "project_overview.svg": project_overview_diagram(),
    }
    for filename, content in diagrams.items():
        (OUTPUT_DIR / filename).write_text(content, encoding="utf-8")
    print(f"Wrote {len(diagrams)} SVG diagrams to {OUTPUT_DIR}")


if __name__ == "__main__":
    main()
