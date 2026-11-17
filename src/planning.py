# -*- coding: utf-8 -*-
"""
Planning & intersection-improvement module.

Pipeline position (mapped to the textbook
《交通工程技术》 Xiang Huaikun, People's Transport Press, 2021):
    Unit 3 survey/counting  ->  THIS MODULE  ->  thesis chapters
    (counting.py output)       Unit 4 traffic planning basics:
                                   demand analysis & forecast
                               Unit 5 road traffic design:
                                   intersection channelisation design
                               Unit 6 traffic impact analysis:
                                   organisation & improvement measures

Methods (all parameters are explicit and adjustable for the advisor):
- PCU conversion factors (design values, urban signalised intersection):
    car 1.0, bus 2.0, truck 2.5, motorcycle 0.4, bicycle 0.2
- Lane-group capacity:  c = S * (g / C),  S = sat_flow_per_lane * lanes
- Flow ratio:           y = v / S
- Saturation degree:    x = v / c
- Webster optimum cycle: C0 = (1.5L + 5) / (1 - Y),  Y = sum of critical y
- Green split proportional to critical y, with minimum-green enforcement.
- Demand forecast: compound growth, V_t = V_0 * (1 + r) ** t

Input JSON schema: see data/sample_intersection.json.
Output: an analysis dict + a Markdown improvement report.
"""

import json
import math
import os

PCU = {"car": 1.0, "bus": 2.0, "truck": 2.5, "motorcycle": 0.4,
       "bicycle": 0.2}
LABELS = {"car": "小汽车", "bus": "客车", "truck": "货车",
          "motorcycle": "摩托车", "bicycle": "非机动车"}
MOVEMENTS = {"left": "左转", "through": "直行", "right": "右转"}


def to_pcu(flow_by_type):
    """veh/h by class -> pcu/h."""
    return sum(n * PCU.get(k, 1.0) for k, n in flow_by_type.items())


def los_of(x):
    """Service level by saturation degree (design grading used here)."""
    if x <= 0.60:
        return "一级（畅通）"
    if x <= 0.80:
        return "二级（稳定）"
    if x <= 0.90:
        return "三级（临界）"
    if x <= 1.00:
        return "四级（拥挤）"
    return "五级（过饱和）"


def phf(counts_15min):
    """Peak hour factor from four 15-min counts."""
    if not counts_15min or max(counts_15min) == 0:
        return None
    return round(sum(counts_15min) / (4.0 * max(counts_15min)), 3)


def allocate_greens(y_list, total_green, min_green):
    """Split effective green proportionally to y (Webster), enforcing minima.

    Pure proportional split first; only phases that fall below min_green
    are clamped to it, and the remainder is redistributed proportionally
    among the unclamped phases. (Giving every phase min_green *plus* a
    proportional share would over-allocate small phases and starve the
    critical phase - that bug made optimised saturation worse than the
    status quo in the first test run, and was caught by verification.)
    """
    n = len(y_list)
    if total_green <= min_green * n:
        return [total_green / n] * n
    greens = [0.0] * n
    free = list(range(n))
    remaining = float(total_green)
    while free:
        y_sum = sum(y_list[i] for i in free) or 1.0
        provisional = {i: remaining * (y_list[i] / y_sum) for i in free}
        clamped = [i for i in free if provisional[i] < min_green]
        if not clamped:
            for i in free:
                greens[i] = provisional[i]
            break
        for i in clamped:
            greens[i] = min_green
            remaining -= min_green
            free.remove(i)
    return greens


class IntersectionAnalysis:
    def __init__(self, data):
        self.d = data
        self.cycle_now = float(data["cycle_current"])
        self.lost_pp = float(data.get("lost_time_per_phase", 3.0))
        self.phases = data["phases"]
        self.movements = []          # enriched movement records
        for ap in data["approaches"]:
            for mv, spec in ap["movements"].items():
                pcu = to_pcu(spec["flow_by_type"])
                S = float(spec["sat_flow_per_lane"]) * int(spec["lanes"])
                self.movements.append({
                    "approach": ap["name"], "movement": mv,
                    "label": ap["name"] + MOVEMENTS[mv],
                    "flow_by_type": spec["flow_by_type"],
                    "veh": sum(spec["flow_by_type"].values()),
                    "pcu": round(pcu, 1), "lanes": int(spec["lanes"]),
                    "sat_flow": S, "y": pcu / S,
                    "phase": int(spec["phase"]),
                    "has_dedicated_lane": bool(spec.get("dedicated_lane",
                                                         int(spec["lanes"]) > 0)),
                })
        self.phases_now_green = [float(p["current_green"])
                                 for p in self.phases]

    # ------------------------------------------------------------ capacity
    def _capacity_table(self, cycle, greens):
        rows = []
        for m in self.movements:
            g = greens[m["phase"]]
            c = m["sat_flow"] * (g / cycle)
            x = m["pcu"] / c if c else float("inf")
            rows.append({**m, "green": round(g, 1), "capacity": round(c),
                         "x": round(x, 3), "los": los_of(x)})
        return rows

    def critical_y(self, extra_lane_for=None):
        """Critical flow ratio per phase; optionally with +1 lane scenario."""
        ycrit = []
        for pi, _ in enumerate(self.phases):
            cand = []
            for m in self.movements:
                if m["phase"] != pi:
                    continue
                S = m["sat_flow"]
                if extra_lane_for and \
                        (m["approach"], m["movement"]) == extra_lane_for:
                    S = m["sat_flow"] / m["lanes"] * (m["lanes"] + 1)
                cand.append(m["pcu"] / S)
            ycrit.append(max(cand) if cand else 0.0)
        return ycrit

    def webster(self, ycrit, min_green=15.0, cycle_bounds=(50, 150)):
        Y = sum(ycrit)
        L = self.lost_pp * len(self.phases)
        result = {"Y": round(Y, 3), "L": round(L, 1)}
        if Y >= 1.0:
            result.update({"feasible": False,
                           "reason": "Y>=1.0，配时已无法满足需求，必须渠化扩容或交通组织分流"})
            return result
        c0 = (1.5 * L + 5) / (1 - Y)
        cycle = min(max(c0, cycle_bounds[0]), cycle_bounds[1])
        cycle = int(round(cycle / 5.0)) * 5
        greens = allocate_greens(ycrit, cycle - L, min_green)
        result.update({"feasible": True, "C0_raw": round(c0, 1),
                       "cycle": cycle,
                       "greens": [round(g, 1) for g in greens]})
        return result

    # ----------------------------------------------------------- diagnosis
    def diagnose(self, table_now, web):
        findings = []
        worst = max(table_now, key=lambda r: r["x"])
        if worst["x"] > 0.90:
            findings.append({
                "problem": f"{worst['label']}饱和度达 {worst['x']:.2f}（{worst['los']}）",
                "measure": "配时优化之外，为该方向增加一条进口车道或重新分配车道功能"
                           "（单元五：平面交叉口渠化设计），并做加道情景测算",
                "unit": "单元五 模块二"})
        lefts = [m for m in self.movements if m["movement"] == "left"]
        for m in lefts:
            ap_total = sum(x["pcu"] for x in self.movements
                           if x["approach"] == m["approach"])
            share = m["pcu"] / ap_total if ap_total else 0
            if share > 0.15 and m["pcu"] > 120:
                findings.append({
                    "problem": f"{m['approach']}左转占该进口 {share*100:.0f}%"
                               f"（{m['pcu']:.0f} pcu/h），左转需求突出",
                    "measure": "设置左转专用车道并采用左转保护相位，避免左转与对向直行冲突"
                               "（单元五 模块二）",
                    "unit": "单元五 模块二"})
        for ap in self.d["approaches"]:
            f = phf(ap.get("counts_15min_peak", []))
            if f is not None and f < 0.85:
                findings.append({
                    "problem": f"{ap['name']}高峰小时系数 PHF={f}，高峰内流量波动大",
                    "measure": "信号配时按分时段方案设计（高峰/平峰分开），"
                               "规划层面评估错峰与需求管理（单元四 模块四）",
                    "unit": "单元四 模块四"})
        bus_share = {}
        for m in self.movements:
            bus_share[m["approach"]] = bus_share.get(m["approach"], 0) + \
                m["flow_by_type"].get("bus", 0)
        for ap_name, nb in bus_share.items():
            tot = sum(x["veh"] for x in self.movements
                      if x["approach"] == ap_name)
            if tot and nb / tot > 0.04:
                findings.append({
                    "problem": f"{ap_name}公交车辆占比 {nb/tot*100:.0f}%，公交运行受社会车辆干扰",
                    "measure": "评估公交专用道与港湾式停靠站、站点远离交叉口布设"
                               "（单元五 模块五：地面公交站与线路设计）",
                    "unit": "单元五 模块五"})
        moto = sum(m["flow_by_type"].get("motorcycle", 0) +
                   m["flow_by_type"].get("bicycle", 0)
                   for m in self.movements)
        tot_all = sum(m["veh"] for m in self.movements)
        if tot_all and moto / tot_all > 0.15:
            findings.append({
                "problem": f"非机动车/摩托车占总流量 {moto/tot_all*100:.0f}%，机非冲突风险高",
                "measure": "设置机非隔离设施与非机动车过街通道，行人过街按最小绿灯"
                           "（行人速度 1.2 m/s）校核（单元二、单元五 模块三）",
                "unit": "单元五 模块三"})
        if not web["feasible"] or web["Y"] > 0.90:
            findings.append({
                "problem": f"关键流量比总和 Y={web['Y']}，单靠信号配时接近或超过极限",
                "measure": "按近/中/远期分层：近期配时优化，中期渠化扩容，"
                           "远期结合片区规划评估禁左、单行等交通组织方案"
                           "（单元四 模块五、单元六 模块五）",
                "unit": "单元六 模块五"})
        return findings

    # ------------------------------------------------------------- forecast
    def forecast(self, years=5, rate=0.03):
        factor = (1 + rate) ** years
        return {"years": years, "rate": rate, "factor": round(factor, 3),
                "note": f"按年均增长 {rate*100:.0f}% 弹性预测，{years} 年后"
                        f"需求约为现状的 {factor:.2f} 倍（单元四 模块四：需求分析与预测）"}

    # ---------------------------------------------------------------- run
    def run(self):
        table_now = self._capacity_table(self.cycle_now,
                                         self.phases_now_green)
        ycrit = self.critical_y()
        web = self.webster(ycrit)
        table_new, scenario = None, None
        if web["feasible"]:
            table_new = self._capacity_table(web["cycle"], web["greens"])
        worst = max(table_now, key=lambda r: r["x"])
        if worst["x"] > 0.85:
            key = (worst["approach"], worst["movement"])
            y2 = self.critical_y(extra_lane_for=key)
            web2 = self.webster(y2)
            scenario = {"extra_lane_for": worst["label"],
                        "Y_before": web["Y"],
                        "Y_after": web2["Y"],
                        "webster_after": web2}
            if web2["feasible"]:
                greens2 = web2["greens"]
                S2 = worst["sat_flow"] / worst["lanes"] * (worst["lanes"] + 1)
                c2 = S2 * (greens2[worst["phase"]] / web2["cycle"])
                scenario["x_after"] = round(worst["pcu"] / c2, 3)
                scenario["x_before"] = worst["x"]
        findings = self.diagnose(table_now, web)
        return {"intersection": self.d["intersection"],
                "period": self.d.get("period", ""),
                "data_note": self.d.get("data_note", ""),
                "cycle_current": self.cycle_now,
                "table_now": table_now, "webster": web,
                "table_new": table_new, "scenario": scenario,
                "findings": findings, "forecast": self.forecast(),
                "phf": {ap["name"]: phf(ap.get("counts_15min_peak", []))
                        for ap in self.d["approaches"]}}


def render_report(a):
    L = []
    L.append(f"# {a['intersection']}交通分析与路口改造方案")
    L.append(f"\n调查时段：{a['period']}  \n{a['data_note']}\n")
    L.append("## 一、现状通行能力与饱和度（单元三调查 → 单元五评价口径）\n")
    L.append("| 进口道流向 | 流量(pcu/h) | 车道数 | 现行绿灯(s) | 通行能力(pcu/h) | 饱和度 x | 服务水平 |")
    L.append("|---|---|---|---|---|---|---|")
    for r in a["table_now"]:
        L.append(f"| {r['label']} | {r['pcu']:.0f} | {r['lanes']} | "
                 f"{r['green']:.0f} | {r['capacity']} | {r['x']:.2f} | {r['los']} |")
    L.append(f"\n现行周期 {a['cycle_current']:.0f}s。高峰小时系数 PHF："
             + "，".join(f"{k}={v}" for k, v in a["phf"].items() if v) + "。")
    w = a["webster"]
    L.append("\n## 二、信号配时优化（Webster 法）\n")
    L.append(f"关键流量比总和 Y={w['Y']}，总损失时间 L={w['L']}s。")
    if w["feasible"]:
        L.append(f"最佳周期 C0=(1.5L+5)/(1-Y)={w['C0_raw']}s，取整采用 "
                 f"**{w['cycle']}s**；各相位有效绿灯：{w['greens']}s。\n")
        L.append("| 进口道流向 | 优化前 x | 优化后 x |")
        L.append("|---|---|---|")
        newmap = {r["label"]: r["x"] for r in a["table_new"]}
        for r in a["table_now"]:
            L.append(f"| {r['label']} | {r['x']:.2f} | {newmap[r['label']]:.2f} |")
    else:
        L.append(f"**配时不可行**：{w['reason']}。")
    if a["scenario"]:
        s = a["scenario"]
        L.append("\n## 三、渠化扩容情景测算（单元五：平面交叉口设计）\n")
        L.append(f"对最饱和的「{s['extra_lane_for']}」增加 1 条进口车道："
                 f"Y 由 {s['Y_before']} 降至 {s['Y_after']}"
                 + (f"，该流向饱和度由 {s['x_before']:.2f} 降至 "
                    f"{s['x_after']:.2f}" if "x_after" in s else "") + "。")
    L.append("\n## 四、问题诊断与改造措施（单元六：交通组织与改善对策）\n")
    for i, f in enumerate(a["findings"], 1):
        L.append(f"{i}. **问题**：{f['problem']}\n   **措施**：{f['measure']}（依据：{f['unit']}）")
    fc = a["forecast"]
    L.append("\n## 五、规划层面的需求预测与分期实施（单元四）\n")
    L.append(fc["note"] + "。实施分期：近期（配时优化+标志标线）→ "
             "中期（渠化扩容，按情景测算执行）→ 远期（结合片区规划复核交通组织）。")
    L.append("\n## 六、依据\n《交通工程技术》（向怀坤，人民交通出版社，2021）"
             "单元三 交通参数调查与分析、单元四 道路交通规划基础、"
             "单元五 道路交通设计基础、单元六 交通影响分析与评价；"
             "配时方法为 Webster 法；pcu 换算与饱和流量为本设计参数，"
             "论文中须注明取值来源并按导师口径复核。")
    return "\n".join(L)


def main():
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("--input", required=True)
    ap.add_argument("--out", default=None)
    args = ap.parse_args()
    with open(args.input, encoding="utf-8") as f:
        data = json.load(f)
    analysis = IntersectionAnalysis(data).run()
    report = render_report(analysis)
    out = args.out or os.path.join("outputs", "improvement_report.md")
    os.makedirs(os.path.dirname(out) or ".", exist_ok=True)
    with open(out, "w", encoding="utf-8") as f:
        f.write(report)
    with open(out.replace(".md", ".json"), "w", encoding="utf-8") as f:
        json.dump(analysis, f, ensure_ascii=False, indent=2, default=str)
    print(report)


if __name__ == "__main__":
    main()
