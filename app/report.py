# -*- coding: utf-8 -*-
"""
report.py —— 变形监测研究报告（.docx）生成模块
================================================
严格仿照《报告样例.docx》的组织方式，为每条观测线依次输出：
    1. 观测线概况说明；
    2. 地表移动变形最大值统计表（对应样例“表6-3”）；
    3. 下沉 / 倾斜 / 曲率 / 水平移动 / 水平变形 五张统计表（对应表6-4 ~ 表6-8）；
    4. 与每张表配套的曲线图（matplotlib 绘制后以图片形式嵌入 Word）；
    5. 每张图后的“最大值 / 最小值位置与发生时间”文字结论；
    6. 全篇结尾给出总体评价与监测建议。
前端点击“生成 Word 报告”后，main.py 调用本模块返回 .docx 字节流供下载。
"""

import io
import datetime
from typing import List, Dict, Any

import matplotlib
matplotlib.use("Agg")               # 无界面服务器环境下也能出图
import matplotlib.pyplot as plt
from docx import Document
from docx.shared import Pt, Cm, RGBColor
from docx.enum.text import WD_ALIGN_PARAGRAPH
from docx.enum.table import WD_TABLE_ALIGNMENT
from docx.oxml.ns import qn

# ---------- 全局样式常量：统一字体、字号、颜色，保证报告排版一致 ----------
FONT_BODY = "宋体"          # 正文中文字体
FONT_HEAD = "黑体"          # 标题中文字体
FONT_EN = "Times New Roman"  # 数字与英文字体
COLOR_MAIN = "#1f4e79"      # 曲线主色（工程蓝）
# 多期曲线的配色盘，期数再多也能循环取色
PALETTE = ["#c0504d", "#1f4e79", "#4f81bd", "#9bbb59", "#f79646",
           "#8064a2", "#2c9faf", "#7f6084", "#bf9000", "#365f91"]


def _init_chinese_font():
    """让 matplotlib 正常显示中文与负号；优先使用微软雅黑/黑体。"""
    # Windows 上优先用微软雅黑/黑体；Linux（HF 部署）用 Noto Sans CJK SC
    matplotlib.rcParams["font.sans-serif"] = [
        "Noto Sans CJK SC", "Noto Sans CJK JP",
        "Microsoft YaHei", "SimHei", "SimSun", "DejaVu Sans",
    ]
    matplotlib.rcParams["axes.unicode_minus"] = False


def _set_run_font(run, size=12, bold=False, cn_font=FONT_BODY, color=None):
    """统一设置一个文字 run 的字号、加粗、中英文字体与颜色。"""
    run.font.size = Pt(size)
    run.bold = bold
    run.font.name = FONT_EN
    run._element.rPr.rFonts.set(qn("w:eastAsia"), cn_font)
    if color:
        run.font.color.rgb = RGBColor.from_string(color.lstrip("#"))


def _add_heading(doc, text, level=1):
    """添加自定义风格的章节标题（不依赖 Word 内置 Heading 样式，跨电脑显示更稳定）。"""
    p = doc.add_paragraph()
    p.paragraph_format.space_before = Pt(10)
    p.paragraph_format.space_after = Pt(6)
    size = {1: 16, 2: 14, 3: 12.5}.get(level, 12)
    run = p.add_run(text)
    _set_run_font(run, size=size, bold=True, cn_font=FONT_HEAD)
    return p


def _add_body(doc, text, indent=True, size=12, align=None):
    """添加正文段落；默认首行缩进 2 字符，符合中文报告习惯。"""
    p = doc.add_paragraph()
    p.paragraph_format.line_spacing = 1.5
    if indent:
        p.paragraph_format.first_line_indent = Pt(size * 2)
    if align is not None:
        p.alignment = align
    run = p.add_run(text)
    _set_run_font(run, size=size)
    return p


def _shade_cell(cell, fill="D9E2F3"):
    """给表格单元格加底纹色（用于表头）。"""
    shd = cell._tc.get_or_add_tcPr().makeelement(qn("w:shd"), {
        qn("w:val"): "clear", qn("w:fill"): fill})
    cell._tc.get_or_add_tcPr().append(shd)


def _add_table(doc, header: List[str], rows: List[List[Any]], font_size=8):
    """
    生成一张带边框、带表头底纹的统计表。
    header 为表头行，rows 为数据行；数值统一格式化成字符串写入。
    """
    table = doc.add_table(rows=1 + len(rows), cols=len(header))
    table.style = "Table Grid"
    table.alignment = WD_TABLE_ALIGNMENT.CENTER
    # 写表头
    for j, h in enumerate(header):
        cell = table.rows[0].cells[j]
        cell.text = ""
        p = cell.paragraphs[0]
        p.alignment = WD_ALIGN_PARAGRAPH.CENTER
        run = p.add_run(str(h))
        _set_run_font(run, size=font_size, bold=True)
        _shade_cell(cell)
    # 写数据行
    for i, row in enumerate(rows, start=1):
        for j, val in enumerate(row):
            cell = table.rows[i].cells[j]
            cell.text = ""
            p = cell.paragraphs[0]
            p.alignment = WD_ALIGN_PARAGRAPH.CENTER
            run = p.add_run(str(val))
            _set_run_font(run, size=font_size)
    return table


def _fmt(v, nd=2):
    """数值格式化：None 显示为 '--'，其余保留 nd 位小数。"""
    if v is None:
        return "--"
    return f"{v:.{nd}f}"


def _fig_to_png(fig):
    """把 matplotlib 图对象转成 PNG 字节流，供 python-docx 嵌入。"""
    buf = io.BytesIO()
    fig.savefig(buf, format="png", dpi=150, bbox_inches="tight")
    plt.close(fig)
    buf.seek(0)
    return buf


def _curve_figure(x, matrix, dates, title, ylabel, mark=None):
    """
    绘制“沿观测线方向”的多期变形曲线图。
    x 为各位置里程，matrix[t] 为该期序列，mark 为要重点标注的最值点。
    """
    _init_chinese_font()
    fig, ax = plt.subplots(figsize=(8.6, 4.2))
    for t in range(len(matrix)):
        ax.plot(x, matrix[t],
                color=PALETTE[t % len(PALETTE)], linewidth=1.3,
                marker="o", markersize=2.6, label=dates[t] if t < len(dates) else f"第{t + 1}期")
    if mark:
        # 用红色五角星标注变形最剧烈的位置
        ax.scatter([mark["distance"]], [mark["value"]], marker="*",
                   s=180, color="red", zorder=5,
                   label=f"极值点 {mark['label']}")
        ax.annotate(f"{mark['label']}\n{mark['value']:.2f}",
                    (mark["distance"], mark["value"]),
                    textcoords="offset points", xytext=(8, -14),
                    fontsize=9, color="red")
    ax.set_title(title, fontsize=13, fontweight="bold")
    ax.set_xlabel("距观测线起点距离 (m)", fontsize=10)
    ax.set_ylabel(ylabel, fontsize=10)
    ax.grid(True, linestyle="--", alpha=0.4)
    ax.legend(fontsize=7.5, ncol=2, loc="best")
    fig.tight_layout()
    return _fig_to_png(fig)


def _layout_figure(line):
    """绘制观测线平面布设图：点位按 X、Y 展开，颜色深浅表示末期下沉量。"""
    _init_chinese_font()
    xs = [p["x"] for p in line["point_table"]]
    ys = [p["y"] for p in line["point_table"]]
    last_w = [p["subsidence"][-1] for p in line["point_table"]]
    fig, ax = plt.subplots(figsize=(8.6, 3.6))
    sc = ax.scatter(ys, xs, c=last_w, cmap="RdYlBu_r", s=28)
    # 每隔若干点标注一次点号，避免文字挤成一团
    step = max(1, len(xs) // 15)
    for i, p in enumerate(line["point_table"]):
        if i % step == 0 or i == len(xs) - 1:
            ax.annotate(p["name"], (p["y"], p["x"]), fontsize=7,
                        textcoords="offset points", xytext=(3, 3))
    ax.set_title(f"{line['line_name']} 观测点平面布设与末期下沉分布", fontsize=12, fontweight="bold")
    ax.set_xlabel("Y 坐标 (m)", fontsize=10)
    ax.set_ylabel("X 坐标 (m)", fontsize=10)
    ax.grid(True, linestyle="--", alpha=0.4)
    cb = fig.colorbar(sc, ax=ax)
    cb.set_label("末期下沉 (mm)", fontsize=9)
    fig.tight_layout()
    return _fig_to_png(fig)


def _extremum_sentence(cn_name, unit, ex, location_text="距离观测线起点"):
    """
    按样例口吻生成最值结论句，例如：
    最大下沉量为456.00，位于Q38，距离观测线起点1077.0m，出现时间为2019-12-20。
    """
    sentences = []
    if ex.get("max"):
        m = ex["max"]
        sentences.append(
            f"最大{cn_name}为 {m['value']:.2f} {unit}，位于 {m['label']}，"
            f"{location_text} {m['distance']:.1f} m，出现时间为 {m['date']}。")
    if ex.get("min") and ex["min"] is not ex.get("max"):
        m = ex["min"]
        sentences.append(
            f"最小{cn_name}为 {m['value']:.2f} {unit}，位于 {m['label']}，"
            f"{location_text} {m['distance']:.1f} m，出现时间为 {m['date']}。")
    return sentences


def _build_max_stat_table(doc, line):
    """生成“地表移动变形最大值统计表”（对应样例表6-3，七列）。"""
    ex = line["extrema"]

    def row_of(metric, cn, unit, have):
        if not have or metric not in ex:
            return [f"{cn}（{unit}）", "--", "--", "--", "--", "--", "--"]
        mx, mn = ex[metric]["max"], ex[metric]["min"]
        return [
            f"{cn}（{unit}）",
            _fmt(mx["value"]) if mx else "--",
            mx["label"] if mx else "--",
            _fmt(mx["distance"], 1) if mx else "--",
            _fmt(mn["value"]) if mn else "--",
            mn["label"] if mn else "--",
            _fmt(mn["distance"], 1) if mn else "--",
        ]

    is_ts = line["source_type"] == "total_station"
    header = ["变形值", "最大值", "最大值点号", "距起点位置(m)",
              "最小值", "最小值点号", "距起点位置(m)"]
    rows = [
        row_of("subsidence", "下沉", "mm", True),
        row_of("tilt", "倾斜", "mm/m", True),
        row_of("curvature", "曲率", "mm/m²", True),
        row_of("horizontal", "水平移动", "mm", is_ts),
        row_of("hstrain", "水平变形", "mm/m", is_ts),
    ]
    _add_table(doc, header, rows, font_size=9)


def _metric_section(doc, line, table_no, cn_name, unit, kind, nd=2):
    """
    生成一个完整指标小节：标题 → 统计表 → 曲线图 → 最值文字结论。
    kind 取值：subsidence / tilt / curvature / horizontal / hstrain
    """
    dates = line["dates"]
    ex = line["extrema"]

    # --- 1) 统计表 ---
    if kind in ("subsidence", "horizontal"):
        # 点级指标：每一行是一个监测点
        seq = line["point_table"]
        key = "subsidence" if kind == "subsidence" else "horizontal"
        header = ["观测点"] + dates
        rows = [[p["name"]] + [_fmt(v, nd) for v in p[key]] for p in seq]
        x = [p["distance"] for p in seq]
        label_col = "name"
    elif kind in ("tilt", "hstrain"):
        # 点对级指标：每一行是一对相邻点
        seq = line["seg_table"]
        key = "tilt" if kind == "tilt" else "hstrain"
        header = ["观测点段"] + dates
        rows = [[s["label"]] + [_fmt(v, nd) for v in s[key]] for s in seq]
        x = [s["distance"] for s in seq]
        label_col = "label"
    else:
        # 曲率：每一行是相邻三点组
        seq = line["tri_table"]
        header = ["观测点组"] + dates
        rows = [[t["label"]] + [_fmt(v, nd) for v in t["curvature"]] for t in seq]
        x = [t["distance"] for t in seq]
        label_col = "label"

    cap = doc.add_paragraph()
    cap.alignment = WD_ALIGN_PARAGRAPH.CENTER
    r = cap.add_run(f"表{table_no} {line['line_name']}地表{cn_name}统计表")
    _set_run_font(r, size=10.5, bold=True)
    unit_p = doc.add_paragraph()
    unit_p.alignment = WD_ALIGN_PARAGRAPH.RIGHT
    r = unit_p.add_run(f"单位：{unit}")
    _set_run_font(r, size=9)
    _add_table(doc, header, rows, font_size=7.5)

    # --- 2) 曲线图（把“期×位置”转成 ECharts 同款的逐期序列） ---
    matrix = []
    for t in range(len(dates)):
        if kind in ("subsidence", "horizontal"):
            key = "subsidence" if kind == "subsidence" else "horizontal"
            matrix.append([p[key][t] for p in seq])
        elif kind in ("tilt", "hstrain"):
            key = "tilt" if kind == "tilt" else "hstrain"
            matrix.append([s[key][t] for s in seq])
        else:
            matrix.append([t_row["curvature"][t] for t_row in seq])
    png = _curve_figure(x, matrix, dates,
                        f"{line['line_name']} 地表{cn_name}曲线",
                        f"{cn_name}（{unit}）",
                        mark=ex[kind]["absmax"] if kind in ex else None)
    pic_p = doc.add_paragraph()
    pic_p.alignment = WD_ALIGN_PARAGRAPH.CENTER
    pic_p.add_run().add_picture(png, width=Cm(15.5))
    cap2 = doc.add_paragraph()
    cap2.alignment = WD_ALIGN_PARAGRAPH.CENTER
    r = cap2.add_run(f"图{table_no} 地表{cn_name}曲线图")
    _set_run_font(r, size=10.5, bold=True)

    # --- 3) 最值结论 ---
    for sent in _extremum_sentence(cn_name, unit, ex[kind]):
        _add_body(doc, sent, indent=False, size=10.5)


def build_report(results: List[Dict[str, Any]],
                 title: str = "地表移动观测站变形监测分析报告",
                 author: str = "") -> bytes:
    """
    根据一条或多条观测线的计算结果，生成完整 .docx 报告，返回文件字节。
    """
    _init_chinese_font()
    doc = Document()

    # 页面与默认字体设置（A4、常规页边距、正文宋体小四）
    sec = doc.sections[0]
    sec.page_width, sec.page_height = Cm(21.0), Cm(29.7)
    sec.top_margin = sec.bottom_margin = Cm(2.2)
    sec.left_margin = sec.right_margin = Cm(2.4)
    normal = doc.styles["Normal"]
    normal.font.name = FONT_EN
    normal.font.size = Pt(12)
    normal._element.rPr.rFonts.set(qn("w:eastAsia"), FONT_BODY)

    # ---------- 封面标题区 ----------
    t = doc.add_paragraph()
    t.alignment = WD_ALIGN_PARAGRAPH.CENTER
    t.paragraph_format.space_before = Pt(30)
    r = t.add_run(title)
    _set_run_font(r, size=22, bold=True, cn_font=FONT_HEAD)
    sub = doc.add_paragraph()
    sub.alignment = WD_ALIGN_PARAGRAPH.CENTER
    r = sub.add_run("（基于水准与全站仪观测数据）")
    _set_run_font(r, size=13)
    info = doc.add_paragraph()
    info.alignment = WD_ALIGN_PARAGRAPH.CENTER
    today = datetime.date.today().strftime("%Y 年 %m 月 %d 日")
    r = info.add_run((f"编制人：{author}　　" if author else "") + f"编制日期：{today}")
    _set_run_font(r, size=11)

    # ---------- 一、观测站概况 ----------
    _add_heading(doc, "一、观测站概况与数据说明", 1)
    total_points = sum(r0["point_count"] for r0 in results)
    intro = (f"本次变形监测共布设 {len(results)} 条地表移动观测线、"
             f"合计 {total_points} 个监测点。各观测线的观测期次、点数与长度统计如下：")
    _add_body(doc, intro)
    head = ["观测线", "观测手段", "监测点数", "点间段数", "观测期数", "线长(m)", "观测时段"]
    rows = [[r0["line_name"], r0["source_type_text"], r0["point_count"],
             r0["segment_count"], len(r0["dates"]), _fmt(r0["line_length"], 1),
             f"{r0['dates'][0]} 至 {r0['dates'][-1]}"] for r0 in results]
    _add_table(doc, head, rows, font_size=9)
    _add_body(doc,
              "数据处理以首期观测为基准：下沉 W=(H₀−Hₜ)×1000（mm，下沉为正）；"
              "相邻点倾斜 i=(Wₙ−Wₙ₊₁)/l（mm/m）；曲率 K=(iₙ−iₙ₊₁)/l̄（mm/m²）；"
              "全站仪观测线进一步计算水平移动 U（位移沿观测线方向投影，mm）"
              "与水平变形 ε=(Uₙ−Uₙ₊₁)/l（mm/m）。下面分线给出统计结果。")

    # ---------- 分线输出 ----------
    for li, line in enumerate(results, start=1):
        _add_heading(doc, f"二.{li}　{line['line_name']}（{line['source_type_text']}）分析", 1)

        # 平面布设图
        png = _layout_figure(line)
        p = doc.add_paragraph()
        p.alignment = WD_ALIGN_PARAGRAPH.CENTER
        p.add_run().add_picture(png, width=Cm(15.5))

        _add_heading(doc, f"（1）地表移动变形最大值统计", 2)
        _build_max_stat_table(doc, line)

        base = li * 10
        _add_heading(doc, f"（2）地表下沉分析", 2)
        _metric_section(doc, line, base + 4, "下沉", "mm", "subsidence", 2)
        _add_heading(doc, f"（3）地表倾斜分析", 2)
        _metric_section(doc, line, base + 5, "倾斜", "mm/m", "tilt", 3)
        _add_heading(doc, f"（4）地表曲率分析", 2)
        _metric_section(doc, line, base + 6, "曲率", "mm/m²", "curvature", 4)
        if line["source_type"] == "total_station":
            _add_heading(doc, f"（5）地表水平移动分析", 2)
            _metric_section(doc, line, base + 7, "水平移动", "mm", "horizontal", 2)
            _add_heading(doc, f"（6）地表水平变形分析", 2)
            _metric_section(doc, line, base + 8, "水平变形", "mm/m", "hstrain", 3)
        else:
            _add_body(doc,
                      "说明：水准观测仅获取各期高程，未进行逐期平面坐标测量，"
                      "故本线不计算水平移动与水平变形；该两项指标由全站仪观测线给出。",
                      indent=False, size=10.5)

    # ---------- 结尾：总体结论 ----------
    _add_heading(doc, "三、主要结论与建议", 1)
    for line in results:
        ex = line["extrema"]
        _add_body(doc, f"【{line['line_name']}】", indent=False)
        w = ex["subsidence"]["absmax"]
        _add_body(doc, f"下沉最剧烈位置为 {w['label']}（距起点 {w['distance']:.1f} m），"
                       f"{w['date']} 下沉量达 {w['value']:.2f} mm。", size=11)
        i = ex["tilt"]["absmax"]
        _add_body(doc, f"倾斜最剧烈段为 {i['label']}（距起点 {i['distance']:.1f} m），"
                       f"{i['date']} 倾斜值为 {i['value']:.3f} mm/m。", size=11)
        k = ex["curvature"]["absmax"]
        _add_body(doc, f"曲率变形最剧烈段为 {k['label']}（距起点 {k['distance']:.1f} m），"
                       f"{k['date']} 曲率值为 {k['value']:.4f} mm/m²。", size=11)
        if "horizontal" in ex:
            u = ex["horizontal"]["absmax"]
            _add_body(doc, f"水平移动最剧烈位置为 {u['label']}（距起点 {u['distance']:.1f} m），"
                           f"{u['date']} 水平移动量达 {u['value']:.2f} mm。", size=11)
    _add_body(doc,
              "建议对上述下沉、倾斜、曲率及水平移动极值区域加密观测频次，"
              "结合工作面开采进度分析地表移动处于初始期、活跃期还是衰退期；"
              "当变形速率持续增大时应及时预警，并对影响范围内的建（构）筑物采取保护措施。")

    buf = io.BytesIO()
    doc.save(buf)
    buf.seek(0)
    return buf.getvalue()
