# -*- coding: utf-8 -*-
"""
Merge per-approach survey fragments into one planning.py input.

Product port of the thesis package's src/merge_surveys.py (v13):
same merge semantics, plus an importable merge_fragments() so the
product server can merge jobs via POST /api/merge without shelling
out to a subprocess.

Field workflow (single-approach mode): film each approach separately
(~15 min each, camera behind the stop line), run the engine with
--approach <east|south|west|north> on each clip, then merge.

Counts from each clip are scaled to an hourly flow rate
(count x 60 / duration_min) -- the standard design flow-rate practice
of expanding the peak-period count. Approaches filmed at different
times are NOT simultaneous; the merged data_note records this, and
any report built from it must state it as a survey-method limitation.

Geometry (lanes, saturation flow, phases, cycle) comes from the
template: data/geometry_template.json (demo geometry, clearly marked
as a template -- replace with field-measured geometry before any
formal report).
"""

import argparse
import json
import os

MOVEMENTS = ("left", "through", "right")


def merge_fragments(frags, template):
    """frags: {approach_name: fragment dict} (approach_survey.json
    contents); template: geometry-template dict.
    Returns (merged_dict, notes list). Raises ValueError on duplicate
    approach (callers should check before passing)."""
    if len(frags) != len(set(frags)):
        raise ValueError("duplicate approach in fragments")
    approaches, notes = [], []
    for tap in template["approaches"]:
        name = tap["name"]
        frag = frags.get(name)
        entry = {"name": name,
                 "counts_15min_peak": tap.get("counts_15min_peak", []),
                 "movements": {}}
        for mv, spec in tap["movements"].items():
            flow = {}
            if frag is not None and mv in MOVEMENTS:
                scale = 60.0 / frag["duration_min"]
                flow = {cls: round(n * scale, 1)
                        for cls, n in
                        frag["movements"].get(mv, {}).items()}
            entry["movements"][mv] = {
                "flow_by_type": flow,
                "lanes": spec["lanes"],
                "sat_flow_per_lane": spec["sat_flow_per_lane"],
                "phase": spec["phase"],
                "dedicated_lane": spec.get("dedicated_lane", False),
            }
        if frag is None:
            notes.append(f"{name}：未提供片段，流量置 0（待补录）")
        else:
            notes.append(f"{name}：录制 {frag['duration_min']} 分钟，"
                         f"计数按 ×{60.0 / frag['duration_min']:.2f} "
                         f"折算小时流率")
        approaches.append(entry)

    merged = {
        "intersection": template["intersection"],
        "period": template.get("period", ""),
        "cycle_current": template["cycle_current"],
        "lost_time_per_phase": template.get("lost_time_per_phase", 3.0),
        "phases": template["phases"],
        "approaches": approaches,
        "data_note": ("分进口分时录制合并（非同步调查）：各进口在同一"
                      "高峰时段内分别录制，论文须注明此时序局限。"
                      + "；".join(notes)
                      + "。几何与配时参数来自模板文件 "
                      "geometry_template.json，须按实地渠化与信号调查"
                      "核对后使用。"),
    }
    return merged, notes


def load_fragment(path):
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def main():
    ap = argparse.ArgumentParser(description="Merge approach surveys")
    ap.add_argument("--runs", nargs="+", required=True,
                    help="run dirs, each containing approach_survey.json")
    ap.add_argument("--template", required=True,
                    help="geometry template (planning-schema JSON)")
    ap.add_argument("--out", required=True)
    args = ap.parse_args()

    with open(args.template, encoding="utf-8") as f:
        tpl = json.load(f)

    frags = {}
    for d in args.runs:
        frag = load_fragment(os.path.join(d, "approach_survey.json"))
        if frag["approach"] in frags:
            raise SystemExit(f"duplicate approach: {frag['approach']}")
        frags[frag["approach"]] = frag

    merged, notes = merge_fragments(frags, tpl)
    os.makedirs(os.path.dirname(args.out) or ".", exist_ok=True)
    with open(args.out, "w", encoding="utf-8") as f:
        json.dump(merged, f, ensure_ascii=False, indent=2)
    print(f"merged {len(frags)} approach fragment(s) -> {args.out}")
    for n in notes:
        print(" ", n)


if __name__ == "__main__":
    main()
