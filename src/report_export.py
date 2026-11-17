# -*- coding: utf-8 -*-
"""
Report export (P3) — build Excel (.xlsx) and PDF (.pdf) survey reports
from one finished job directory.

Inputs (all optional, degraded gracefully when missing):
    summary.json / events.csv / movements.csv (or approach_movements.csv)
    survey_auto.json / merged_survey.json
    planning.json — if absent but a survey exists, planning is computed
    in-memory with the same defaults as the server's /api/planning
    (lanes=1, sat_flow=1650, cycle from survey or 90, greens [40, 40])
    and the report notes that it was computed on export.

Outputs: report.xlsx / report.pdf inside the job directory.

Honesty rule: every report carries the data note verbatim and a
"数据说明" section stating the counting convention (directional
crossing counts per approach are authoritative; movement tables give
structural shares only) and that demo/public videos are NOT field
survey data. Nothing here may be presented as a measured survey.

Deps: openpyxl (MIT), reportlab (BSD) — see THIRD_PARTY.md.
PDF uses reportlab's built-in CID font STSong-Light, so no font file
needs to be bundled with the exe.
"""

import csv
import json
import os
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

TITLE = "车辆检测与分型统计系统 · 调查报告"
COUNTING_NOTE = ("计数口径：进口流量以定向过线计数为准；"
                 "流向分类表只给结构比例，不作流量依据。")
DEMO_NOTE = ("演示数据声明：本报告基于公开测试视频或演示片段生成，"
             "流量为片段内原始计数（未折算为小时流量）；"
             "正式报告须用完整高峰小时实测数据并按实地几何参数复核，"
             "不得把演示数据当作实测调查数据使用。")
TYPE_LABELS = {"car": "小汽车", "bus": "客车", "truck": "货车",
               "motorcycle": "摩托车", "bicycle": "非机动车"}
MOVE_LABELS = {"left": "左转", "through": "直行", "right": "右转",
               "uturn": "掉头"}


def _read_json(path):
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return None


def _read_csv(path):
    try:
        with open(path, encoding="utf-8-sig", newline="") as f:
            return list(csv.DictReader(f))
    except OSError:
        return []


def _merged_total(survey):
    total = 0
    by_type = {}
    for ap in survey.get("approaches", []):
        for mv in ap.get("movements", {}).values():
            for t, n in mv.get("flow_by_type", {}).items():
                by_type[t] = by_type.get(t, 0) + n
                total += n
    return total, by_type


def _compute_planning(survey, merged):
    """Mirror server /api/planning defaults; returns result dict or None."""
    try:
        from planning import IntersectionAnalysis
    except Exception:
        return None
    lanes, sat, cycle = 1, 1650.0, float(survey.get("cycle_current", 90))
    greens = [40.0, 40.0]
    phases = [{"current_green": g} for g in greens]
    approaches = []
    for ap in survey.get("approaches", []):
        movements = {}
        for mv, spec in ap.get("movements", {}).items():
            if merged:
                movements[mv] = {
                    "flow_by_type": spec.get("flow_by_type", {}),
                    "sat_flow_per_lane": spec.get("sat_flow_per_lane", sat),
                    "lanes": spec.get("lanes", lanes),
                    "phase": spec.get("phase", 0)}
            else:
                movements[mv] = {
                    "flow_by_type": spec.get("flow_by_type", {}),
                    "sat_flow_per_lane": sat, "lanes": lanes,
                    "phase": 0 if ap.get("name") in ("东", "西", "东进口",
                                                     "西进口") else 1}
        approaches.append({"name": ap.get("name", ""), "movements": movements})
    if merged:
        data = {"intersection": survey.get("intersection", "演示路口"),
                "period": survey.get("period", ""),
                "data_note": survey.get("data_note", ""),
                "cycle_current": survey.get("cycle_current", cycle),
                "lost_time_per_phase": survey.get("lost_time_per_phase", 3.0),
                "phases": survey.get("phases") or phases,
                "approaches": approaches}
    else:
        data = {"intersection": "演示路口",
                "period": "演示片段（非小时流量）",
                "data_note": ("演示数据：流量为视频片段内的原始计数，"
                              "未折算为小时流量，仅演示分析流程；"
                              "正式报告须用完整高峰小时调查数据并按"
                              "实地几何参数复核。"),
                "cycle_current": cycle, "phases": phases,
                "approaches": approaches}
    try:
        return IntersectionAnalysis(data).run()
    except Exception:
        return None


# v0.14: screen-direction labels per counting line (the vertical
# counter runs on swapped coordinates: down/up = screen right/left).
DIR_LABELS = {"horizontal": {"down": "向下过线", "up": "向上过线"},
              "vertical": {"down": "向右过线", "up": "向左过线"}}


def _direction_rows(line, spec):
    """(label, value) rows splitting one line's count by direction,
    with per-type rows under each direction — same numbers as the UI."""
    rows = []
    bd = (spec or {}).get("by_direction") or {}
    labels = DIR_LABELS.get(line, DIR_LABELS["horizontal"])
    for d in ("down", "up"):
        dd = bd.get(d) or {}
        rows.append((f"分断面 · {line} · {labels[d]}（辆）",
                     dd.get("total")))
        for t in dd.get("by_type") or []:
            if t.get("count"):
                rows.append((f"分断面 · {line} · {labels[d]} · "
                             f"{t.get('cls_label')}（辆）",
                             t.get("count")))
    return rows


def collect(job_dir):
    """Gather everything a report needs; never raises on missing files."""
    job_dir = os.path.abspath(job_dir)
    name = os.path.basename(job_dir.rstrip(os.sep))
    summary = _read_json(os.path.join(job_dir, "summary.json"))
    survey = _read_json(os.path.join(job_dir, "survey_auto.json"))
    merged = False
    if survey is None:
        survey = _read_json(os.path.join(job_dir, "merged_survey.json"))
        merged = survey is not None
    planning = _read_json(os.path.join(job_dir, "planning.json"))
    planning_computed = False
    if planning is None and survey is not None:
        planning = _compute_planning(survey, merged)
        planning_computed = planning is not None
    events = _read_csv(os.path.join(job_dir, "events.csv"))
    movements_rows = _read_csv(os.path.join(job_dir, "movements.csv"))
    if not movements_rows:
        movements_rows = _read_csv(
            os.path.join(job_dir, "approach_movements.csv"))
    data_note = ""
    if survey:
        data_note = survey.get("data_note", "")
    if planning:
        data_note = data_note or planning.get("data_note", "")
    if not data_note:
        data_note = DEMO_NOTE
    total, by_type = None, {}
    if summary is not None:
        total = summary.get("total")
        for row in summary.get("by_type", []):
            by_type[row.get("cls_name")] = row.get("count", 0)
    elif survey is not None:
        total, by_type = _merged_total(survey)
    return {
        "job_dir": job_dir, "name": name, "summary": summary,
        "survey": survey, "merged": merged, "planning": planning,
        "planning_computed": planning_computed,
        "events": events, "movements_rows": movements_rows,
        "data_note": data_note, "total": total, "by_type": by_type,
        "generated_at": time.strftime("%Y-%m-%d %H:%M:%S"),
    }


# ---------------------------------------------------------------- xlsx

def build_xlsx(job_dir, out_path=None):
    from openpyxl import Workbook
    from openpyxl.styles import Alignment, Border, Font, PatternFill, Side

    d = collect(job_dir)
    out_path = out_path or os.path.join(job_dir, "report.xlsx")
    wb = Workbook()
    dark = PatternFill("solid", fgColor="08090B")
    accent = PatternFill("solid", fgColor="D7FF35")
    hfont = Font(name="Calibri", size=11, bold=True, color="D7FF35")
    bfont = Font(name="Calibri", size=11)
    title_font = Font(name="Calibri", size=16, bold=True, color="08090B")
    thin = Side(style="thin", color="BFBFBF")
    border = Border(left=thin, right=thin, top=thin, bottom=thin)
    wrap = Alignment(wrap_text=True, vertical="top")

    def header_row(ws, row, values, widths=None):
        for i, v in enumerate(values, 1):
            c = ws.cell(row=row, column=i, value=v)
            c.fill, c.font, c.border = dark, hfont, border
        if widths:
            for i, w in enumerate(widths, 1):
                ws.column_dimensions[chr(64 + i)].width = w

    def body_row(ws, row, values):
        for i, v in enumerate(values, 1):
            c = ws.cell(row=row, column=i, value=v)
            c.font, c.border, c.alignment = bfont, border, wrap

    # -- 概览 ------------------------------------------------------
    ws = wb.active
    ws.title = "概览"
    ws["A1"] = TITLE
    ws["A1"].font = title_font
    ws["A2"] = f"任务：{d['name']}　生成时间：{d['generated_at']}"
    ws["A2"].font = bfont
    r = 4
    header_row(ws, r, ["项目", "数值"], [28, 60])
    rows = [("过线总数（辆）", d["total"] if d["total"] is not None else "—")]
    for t in ("car", "bus", "truck", "motorcycle", "bicycle"):
        rows.append((f"分车型 · {TYPE_LABELS[t]}（辆）",
                     d["by_type"].get(t, 0)))
    if d["summary"]:
        # Single-line jobs carry no by_line key — the top-level
        # summary IS the horizontal counter (same fallback as the UI).
        by_line = d["summary"].get("by_line") or {"horizontal": d["summary"]}
        for line, spec in by_line.items():
            rows.append((f"分断面 · {line}（辆）", spec.get("total")))
            rows.extend(_direction_rows(line, spec))
        rows.append(("轨迹总数（条，仅参考）",
                     d["summary"].get("unique_tracks_seen")))
        curve = d["summary"].get("flow_curve") or []
        for b in curve:
            rows.append((f"流量曲线 · 第 {b.get('bucket', 0)} 分钟（辆）",
                         b.get("total")))
    if d["merged"]:
        rows.append(("任务类型", "多进口合并任务（分进口分时录制、非同步调查）"))
    if d["planning"] and d["planning"].get("webster"):
        w = d["planning"]["webster"]
        rows.append(("Webster 关键流量比 Y", w.get("Y")))
        rows.append(("Webster 建议周期",
                     f"{w.get('cycle')}s" if w.get("feasible") else "不可行"))
    for k, v in rows:
        r += 1
        body_row(ws, r, [k, v])
    r += 2
    ws.cell(row=r, column=1, value=COUNTING_NOTE).font = bfont
    ws.merge_cells(start_row=r, start_column=1, end_row=r, end_column=2)
    r += 1
    ws.cell(row=r, column=1, value=d["data_note"]).font = bfont
    ws.merge_cells(start_row=r, start_column=1, end_row=r, end_column=2)
    ws.sheet_view.showGridLines = False

    # -- 过线记录 --------------------------------------------------
    ws2 = wb.create_sheet("过线记录")
    if d["events"]:
        cols = list(d["events"][0].keys())
        header_row(ws2, 1, cols, [16] * len(cols))
        for i, row in enumerate(d["events"], 2):
            body_row(ws2, i, [row.get(c, "") for c in cols])
        ws2.freeze_panes = "A2"
        ws2.auto_filter.ref = ws2.dimensions
    else:
        ws2["A1"] = "本任务无逐车过线记录（events.csv 缺失，多进口合并任务无此文件）。"
        ws2["A1"].font = bfont

    # -- 流向统计 --------------------------------------------------
    ws3 = wb.create_sheet("流向统计")
    if d["movements_rows"]:
        cols = list(d["movements_rows"][0].keys())
        header_row(ws3, 1, cols, [16] * len(cols))
        for i, row in enumerate(d["movements_rows"], 2):
            body_row(ws3, i, [row.get(c, "") for c in cols])
        ws3.freeze_panes = "A2"
        ws3.auto_filter.ref = ws3.dimensions
    else:
        ws3["A1"] = "本任务无流向轨迹表（未开启流向分类或单进口模式）。"
        ws3["A1"].font = bfont

    # -- 规划分析 --------------------------------------------------
    ws4 = wb.create_sheet("规划分析")
    p = d["planning"]
    if p:
        r = 1
        ws4.cell(row=r, column=1, value="现状通行能力与饱和度").font = Font(
            name="Calibri", size=13, bold=True)
        r += 1
        header_row(ws4, r,
                   ["进口道流向", "流量 pcu", "车道", "绿灯 s",
                    "通行能力", "饱和度 x", "服务水平"],
                   [22, 14, 10, 10, 14, 12, 18])
        for row in p.get("table_now", []):
            r += 1
            body_row(ws4, r, [row.get("label"), row.get("pcu"),
                              row.get("lanes"), row.get("green"),
                              row.get("capacity"), row.get("x"),
                              row.get("los")])
        w = p.get("webster") or {}
        r += 2
        ws4.cell(row=r, column=1,
                value="信号配时优化（Webster）").font = Font(
                    name="Calibri", size=13, bold=True)
        r += 1
        header_row(ws4, r, ["项目", "数值"], [28, 60])
        for k, v in [("关键流量比 Y", w.get("Y")),
                     ("总损失时间 L（s）", w.get("L")),
                     ("建议周期", f"{w.get('cycle')}s"
                      if w.get("feasible") else "不可行"),
                     ("各相位绿灯（s）", " / ".join(
                         str(g) for g in w.get("greens", []))
                      if w.get("feasible") else "—"),
                     ("不可行原因", w.get("reason", "") if not w.get(
                         "feasible") else "—")]:
            r += 1
            body_row(ws4, r, [k, v])
        sc = p.get("scenario")
        if sc:
            r += 2
            ws4.cell(row=r, column=1,
                    value="渠化扩容情景（+1 车道）").font = Font(
                        name="Calibri", size=13, bold=True)
            r += 1
            body_row(ws4, r, ["对象", sc.get("extra_lane_for")])
            r += 1
            body_row(ws4, r, ["关键流量比 Y", f"{sc.get('Y_before')} → "
                              f"{sc.get('Y_after')}"])
        findings = p.get("findings") or []
        if findings:
            r += 2
            ws4.cell(row=r, column=1,
                    value="问题诊断与改造措施").font = Font(
                        name="Calibri", size=13, bold=True)
            r += 1
            header_row(ws4, r, ["问题", "措施", "课本依据"], [46, 60, 18])
            for f in findings:
                r += 1
                body_row(ws4, r, [f.get("problem"), f.get("measure"),
                                  f.get("unit")])
        if d["planning_computed"]:
            r += 2
            ws4.cell(row=r, column=1, value="注：本页规划分析在导出时按默认参数"
                    "（车道 1、饱和流量 1650、绿灯 40/40）补算，未写入任务目录；"
                    "正式分析请在界面运行规划分析并按实地参数复核。").font = bfont
            ws4.merge_cells(start_row=r, start_column=1,
                            end_row=r, end_column=7)
    else:
        ws4["A1"] = "本任务无规划分析（无 survey_auto.json / merged_survey.json）。"
        ws4["A1"].font = bfont

    # -- 数据说明 --------------------------------------------------
    ws5 = wb.create_sheet("数据说明")
    notes = [COUNTING_NOTE, d["data_note"], DEMO_NOTE,
             "车型口径：小汽车 car / 客车 bus / 货车 truck / "
             "摩托车 motorcycle / 非机动车 bicycle；"
             "VisDrone 权重类别 van 并入小汽车、tricycle 并入摩托车。",
             "pcu 换算系数：小汽车 1.0 / 客车 2.0 / 货车 2.5 / "
             "摩托车 0.4 / 非机动车 0.2（GB 50220-95 表 A.0.2、"
             "CJJ 37-2012）。",
             "生成工具：车辆检测与分型统计系统 · 产品版（ONNX Runtime 引擎）。"]
    for i, n in enumerate(notes, 1):
        c = ws5.cell(row=i, column=1, value=f"{i}. {n}")
        c.font, c.alignment = bfont, wrap
        ws5.merge_cells(start_row=i, start_column=1, end_row=i, end_column=2)
    ws5.column_dimensions["A"].width = 110
    wb.save(out_path)
    return out_path


# ----------------------------------------------------------------- pdf

def build_pdf(job_dir, out_path=None):
    from reportlab.lib import colors
    from reportlab.lib.pagesizes import A4
    from reportlab.lib.styles import ParagraphStyle
    from reportlab.lib.units import mm
    from reportlab.pdfbase import pdfmetrics
    from reportlab.pdfbase.cidfonts import UnicodeCIDFont
    from reportlab.platypus import (PageBreak, Paragraph, SimpleDocTemplate,
                                    Spacer, Table, TableStyle)

    pdfmetrics.registerFont(UnicodeCIDFont("STSong-Light"))
    F = "STSong-Light"
    d = collect(job_dir)
    out_path = out_path or os.path.join(job_dir, "report.pdf")
    BLACK = colors.HexColor("#08090B")
    GREEN = colors.HexColor("#D7FF35")
    GREY = colors.HexColor("#666666")
    LINE = colors.HexColor("#CCCCCC")

    st_title = ParagraphStyle("title", fontName=F, fontSize=20, leading=26)
    st_h1 = ParagraphStyle("h1", fontName=F, fontSize=14, leading=20,
                           spaceBefore=14, spaceAfter=6)
    st_h2 = ParagraphStyle("h2", fontName=F, fontSize=12, leading=17,
                           spaceBefore=10, spaceAfter=4)
    st_body = ParagraphStyle("body", fontName=F, fontSize=10.5, leading=15)
    st_small = ParagraphStyle("small", fontName=F, fontSize=9, leading=13,
                              textColor=GREY)
    st_cell = ParagraphStyle("cell", fontName=F, fontSize=10, leading=14)
    st_cell_h = ParagraphStyle("cellh", fontName=F, fontSize=10, leading=14,
                               textColor=GREEN)

    def P(text, style=st_body):
        return Paragraph(str(text).replace("·", "—"), style)

    def table(data, widths, header=True):
        t = Table(data, colWidths=widths, repeatRows=1 if header else 0)
        style = [("GRID", (0, 0), (-1, -1), 0.5, LINE),
                 ("VALIGN", (0, 0), (-1, -1), "TOP"),
                 ("LEFTPADDING", (0, 0), (-1, -1), 6),
                 ("RIGHTPADDING", (0, 0), (-1, -1), 6),
                 ("TOPPADDING", (0, 0), (-1, -1), 4),
                 ("BOTTOMPADDING", (0, 0), (-1, -1), 4)]
        if header:
            style += [("BACKGROUND", (0, 0), (-1, 0), BLACK),
                      ("TEXTCOLOR", (0, 0), (-1, 0), GREEN)]
        t.setStyle(TableStyle(style))
        return t

    def hrow(values):
        return [P(v, st_cell_h) for v in values]

    def row(values):
        return [P(v, st_cell) for v in values]

    story = []
    story.append(P(TITLE, st_title))
    story.append(P(f"任务：{d['name']}　生成时间：{d['generated_at']}",
                   st_small))
    story.append(Spacer(1, 4 * mm))
    story.append(P(d["data_note"], st_small))
    story.append(Spacer(1, 2 * mm))

    # -- 关键指标 --------------------------------------------------
    story.append(P("一、关键指标", st_h1))
    kpi = [hrow(["项目", "数值"])]
    kpi.append(row(["过线总数（辆）",
                    d["total"] if d["total"] is not None else "—"]))
    for t in ("car", "bus", "truck", "motorcycle", "bicycle"):
        kpi.append(row([f"分车型 · {TYPE_LABELS[t]}（辆）",
                        d["by_type"].get(t, 0)]))
    if d["summary"]:
        by_line = d["summary"].get("by_line") or {"horizontal": d["summary"]}
        for line, spec in by_line.items():
            kpi.append(row([f"分断面 · {line}（辆）", spec.get("total")]))
            for k, v in _direction_rows(line, spec):
                kpi.append(row([k, v]))
        curve = d["summary"].get("flow_curve") or []
        for b in curve:
            kpi.append(row([f"流量曲线 · 第 {b.get('bucket', 0)} 分钟（辆）",
                            b.get("total")]))
    if d["merged"]:
        kpi.append(row(["任务类型",
                        "多进口合并任务（分进口分时录制、非同步调查）"]))
    story.append(table(kpi, [70 * mm, 90 * mm]))

    # -- 流向结构 --------------------------------------------------
    story.append(P("二、流向结构", st_h1))
    mv = (d["summary"] or {}).get("movements") if d["summary"] else None
    if mv:
        t = [hrow(["进口", "流向", "小汽车", "摩托车", "货车", "客车"])]
        for ap, by_move in mv.items():
            for m, by_type in by_move.items():
                t.append(row([ap, MOVE_LABELS.get(m, m),
                              by_type.get("car", 0),
                              by_type.get("motorcycle", 0),
                              by_type.get("truck", 0),
                              by_type.get("bus", 0)]))
        story.append(table(t, [26 * mm, 20 * mm, 26 * mm, 26 * mm,
                               26 * mm, 26 * mm]))
        story.append(P("流向表只给结构比例；进口总量以定向过线计数为准。",
                       st_small))
    elif d["movements_rows"]:
        t = [hrow(list(d["movements_rows"][0].keys()))]
        for r in d["movements_rows"][:200]:
            t.append(row([r.get(c, "") for c in d["movements_rows"][0]]))
        story.append(table(t, [160 * mm / len(t[0])] * len(t[0])))
    else:
        story.append(P("本任务无流向数据（未开启流向分类或单进口模式）。"))

    # -- 规划分析 --------------------------------------------------
    p = d["planning"]
    story.append(P("三、规划分析", st_h1))
    if p:
        t = [hrow(["进口道流向", "流量 pcu", "车道", "绿灯 s",
                    "通行能力", "饱和度 x", "服务水平"])]
        for r in p.get("table_now", []):
            t.append(row([r.get("label"), r.get("pcu"), r.get("lanes"),
                          r.get("green"), r.get("capacity"), r.get("x"),
                          r.get("los")]))
        story.append(table(t, [34 * mm, 22 * mm, 14 * mm, 18 * mm,
                               24 * mm, 22 * mm, 26 * mm]))
        w = p.get("webster") or {}
        story.append(P("信号配时优化（Webster）", st_h2))
        wt = [hrow(["项目", "数值"]),
              row(["关键流量比 Y", w.get("Y")]),
              row(["总损失时间 L（s）", w.get("L")]),
              row(["建议周期", f"{w.get('cycle')}s" if w.get("feasible")
                   else "不可行"]),
              row(["各相位绿灯（s）",
                   " / ".join(str(g) for g in w.get("greens", []))
                   if w.get("feasible") else "—"])]
        if not w.get("feasible") and w.get("reason"):
            wt.append(row(["不可行原因", w.get("reason")]))
        story.append(table(wt, [50 * mm, 110 * mm]))
        sc = p.get("scenario")
        if sc:
            story.append(P("渠化扩容情景（+1 车道）", st_h2))
            story.append(P(f"对最饱和的「{sc.get('extra_lane_for')}」增加 "
                           f"1 条进口车道：关键流量比 Y 由 "
                           f"{sc.get('Y_before')} 降至 {sc.get('Y_after')}。"))
        for f in p.get("findings") or []:
            story.append(P(f"· {f.get('problem')}——{f.get('measure')}"
                           f"（{f.get('unit')}）"))
        if d["planning_computed"]:
            story.append(P("注：本节规划分析在导出时按默认参数（车道 1、"
                           "饱和流量 1650、绿灯 40/40）补算；正式分析请在界面"
                           "运行规划分析并按实地参数复核。", st_small))
    else:
        story.append(P("本任务无规划分析（无 survey_auto.json / "
                       "merged_survey.json）。"))

    # -- 数据说明 --------------------------------------------------
    story.append(P("四、数据说明", st_h1))
    story.append(P(COUNTING_NOTE))
    story.append(P(DEMO_NOTE))
    story.append(P("车型口径：小汽车 car / 客车 bus / 货车 truck / "
                   "摩托车 motorcycle / 非机动车 bicycle；VisDrone 权重类别 "
                   "van 并入小汽车、tricycle 并入摩托车。"))
    story.append(P("pcu 换算系数：小汽车 1.0 / 客车 2.0 / 货车 2.5 / "
                   "摩托车 0.4 / 非机动车 0.2（GB 50220-95 表 A.0.2、"
                   "CJJ 37-2012）。"))
    story.append(P("生成工具：车辆检测与分型统计系统 · 产品版"
                   "（ONNX Runtime 引擎）。"))

    def footer(canvas, doc):
        canvas.saveState()
        canvas.setFont("STSong-Light", 8)
        canvas.setFillColor(GREY)
        canvas.drawString(15 * mm, 12 * mm,
                          "演示数据 — 非实测调查 — 正式报告须用完整高峰小时实测数据")
        canvas.drawRightString(A4[0] - 15 * mm, 12 * mm,
                               f"第 {doc.page} 页")
        canvas.restoreState()

    doc = SimpleDocTemplate(out_path, pagesize=A4,
                            leftMargin=15 * mm, rightMargin=15 * mm,
                            topMargin=15 * mm, bottomMargin=18 * mm,
                            title=TITLE, author="车辆检测与分型统计系统 · 产品版")
    doc.build(story, onFirstPage=footer, onLaterPages=footer)
    return out_path


def export_report(job_dir, fmt):
    if fmt == "xlsx":
        return build_xlsx(job_dir)
    if fmt == "pdf":
        return build_pdf(job_dir)
    raise ValueError("fmt 须为 xlsx 或 pdf")


if __name__ == "__main__":
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("job_dir")
    ap.add_argument("--format", choices=["xlsx", "pdf", "both"],
                    default="both")
    ap.add_argument("--out", default=None)
    args = ap.parse_args()
    fmts = ["xlsx", "pdf"] if args.format == "both" else [args.format]
    for fmt in fmts:
        out = (os.path.join(args.out, f"report.{fmt}") if args.out
               else None)
        path = export_report(args.job_dir, fmt) if out is None else \
            (build_xlsx(args.job_dir, out) if fmt == "xlsx"
             else build_pdf(args.job_dir, out))
        print(f"{fmt}: {path} ({os.path.getsize(path)} bytes)")
