# -*- coding: utf-8 -*-
"""
Movement classification: per-approach, per-movement traffic volumes.

Counting lines measure *section flows* (how many vehicles pass a line).
A traffic survey for signal design needs more: for every approach
(east / south / west / north entry), how many vehicles turn left, go
through, or turn right -- split by vehicle type. That is exactly the
input format of planning.py.

Method
------
For each finished track, look at where it started and where it ended
relative to the intersection centre:

- entry side = the side of the centre where the track first appeared
- exit  side = the side where the track last appeared

Sides are decided by the dominant axis of the offset from the centre
(|dx| vs |dy|). The (entry, exit) pair maps to a movement under
right-hand traffic:

    entry S (heading north): exit N=through, W=left,  E=right
    entry N (heading south): exit S=through, E=left,  W=right
    entry W (heading east):  exit E=through, N=left,  S=right
    entry E (heading west):  exit W=through, S=left,  N=right
    entry == exit: uturn

Quality gates: a track is classified only if it was observed for at
least `min_frames` frames and moved at least `min_disp_ratio` of the
frame diagonal -- this filters parked vehicles and jitter tracks.
Tracks that start and end on adjacent sides without passing near the
centre are still classified by the table (they turned at the corner);
the gates keep noise out, the table does the rest.

Accuracy depends on the camera view covering all four approaches and
on tracks surviving the crossing. Expect demo-grade numbers on skewed
or partial views; the formal survey uses one camera per approach pair
with the centre calibrated per site (see parameters).
"""

from collections import Counter, defaultdict

SIDES = ("N", "E", "S", "W")
APPROACH_NAMES = {"E": "东进口", "S": "南进口", "W": "西进口", "N": "北进口"}
EXIT_NAMES = {"N": "北", "E": "东", "S": "南", "W": "西"}

# (entry, exit) -> movement, right-hand traffic
MOVEMENT_TABLE = {
    ("S", "N"): "through", ("S", "W"): "left", ("S", "E"): "right",
    ("N", "S"): "through", ("N", "E"): "left", ("N", "W"): "right",
    ("W", "E"): "through", ("W", "N"): "left", ("W", "S"): "right",
    ("E", "W"): "through", ("E", "S"): "left", ("E", "N"): "right",
}
MOVEMENTS = ("left", "through", "right")


def side_of(cx, cy, center_x, center_y):
    """Which side of the centre a point lies on (dominant axis)."""
    dx = cx - center_x
    dy = cy - center_y
    if abs(dx) >= abs(dy):
        return "E" if dx > 0 else "W"
    return "S" if dy > 0 else "N"  # image y grows downwards: +dy = south


class _Track:
    __slots__ = ("first", "last", "frames", "votes")

    def __init__(self, cx, cy):
        self.first = (cx, cy)
        self.last = (cx, cy)
        self.frames = 0
        self.votes = Counter()


class MovementClassifier:
    def __init__(self, frame_width, frame_height, center_x_ratio=0.5,
                 center_y_ratio=0.5, min_frames=15, min_disp_ratio=0.08):
        self.w = frame_width
        self.h = frame_height
        self.cx = center_x_ratio * frame_width
        self.cy = center_y_ratio * frame_height
        self.min_frames = min_frames
        self.min_disp = min_disp_ratio * (frame_width ** 2
                                          + frame_height ** 2) ** 0.5
        self.tracks = {}

    def observe(self, track_id, cls_name, cx, cy):
        tr = self.tracks.get(track_id)
        if tr is None:
            tr = _Track(cx, cy)
            self.tracks[track_id] = tr
        tr.last = (cx, cy)
        tr.frames += 1
        tr.votes[cls_name] += 1

    def results(self):
        """Classified records for all tracks passing the quality gates."""
        records = []
        for tid, tr in self.tracks.items():
            if tr.frames < self.min_frames:
                continue
            dx = tr.last[0] - tr.first[0]
            dy = tr.last[1] - tr.first[1]
            if (dx * dx + dy * dy) ** 0.5 < self.min_disp:
                continue  # parked / jitter: not a through movement
            entry = side_of(tr.first[0], tr.first[1], self.cx, self.cy)
            exit_ = side_of(tr.last[0], tr.last[1], self.cx, self.cy)
            movement = MOVEMENT_TABLE.get((entry, exit_),
                                          "uturn" if entry == exit_
                                          else "unknown")
            records.append({
                "track_id": tid,
                "cls_name": tr.votes.most_common(1)[0][0] if tr.votes
                else "car",
                "entry": entry,
                "exit": exit_,
                "movement": movement,
                "frames_seen": tr.frames,
            })
        return records


def aggregate(records):
    """records -> {approach_name: {movement: Counter(cls_name)}}"""
    agg = {}
    for name in APPROACH_NAMES.values():
        agg[name] = {m: Counter() for m in MOVEMENTS + ("uturn",)}
    for r in records:
        approach = APPROACH_NAMES[r["entry"]]
        agg[approach][r["movement"]][r["cls_name"]] += 1
    return agg


def summary_table(agg):
    """Human-readable per-approach x movement totals (all types summed)."""
    rows = []
    for approach, by_move in agg.items():
        row = {"approach": approach}
        total = 0
        for m in MOVEMENTS + ("uturn",):
            n = sum(by_move[m].values())
            row[m] = n
            total += n
        row["total"] = total
        rows.append(row)
    return rows


# ---------------------------------------------------------------------------
# Single-approach mode
# ---------------------------------------------------------------------------
# Field reality (user, 2026-11-07): at a real intersection you rarely get
# one overhead shot of all four approaches. You stand at ONE approach and
# film it for ~15 minutes, then move to the next. The filmed approach is
# therefore always at the BOTTOM of the frame (camera behind the stop
# line, looking into the intersection), and a vehicle's movement is read
# off the frame edge its track leaves through:
#
#     top edge    -> through
#     left edge   -> left     (image left = driver's left, because the
#     right edge  -> right     camera looks the same way as the traffic)
#     bottom edge -> uturn
#
# Cross-street traffic crossing the TOP of the frame is kept out by an
# entry gate: a track only counts if it STARTS in the lower part of the
# frame (the filmed approach side). Tracks ending mid-frame (broken by
# occlusion) are reported as "unknown" and excluded from the split.
# Merge the per-approach results with src/merge_surveys.py.

EDGE_MOVEMENT = {"top": "through", "left": "left", "right": "right",
                 "bottom": "uturn"}


class SingleApproachClassifier:
    def __init__(self, frame_width, frame_height, approach,
                 min_frames=15, min_disp_ratio=0.08, edge_band=0.18,
                 entry_y_ratio=0.35):
        self.w = frame_width
        self.h = frame_height
        self.approach = approach
        self.min_frames = min_frames
        self.min_disp = min_disp_ratio * (frame_width ** 2
                                          + frame_height ** 2) ** 0.5
        self.edge_band = edge_band
        self.entry_y = entry_y_ratio * frame_height
        self.tracks = {}
        self.travel = {}
        self._prev = {}

    def observe(self, track_id, cls_name, cx, cy):
        tr = self.tracks.get(track_id)
        if tr is None:
            tr = _Track(cx, cy)
            self.tracks[track_id] = tr
            self.travel[track_id] = 0.0
        else:
            px, py = self._prev[track_id]
            self.travel[track_id] += ((cx - px) ** 2
                                      + (cy - py) ** 2) ** 0.5
        self._prev[track_id] = (cx, cy)
        tr.last = (cx, cy)
        tr.frames += 1
        tr.votes[cls_name] += 1

    def _exit_edge(self, x, y):
        ratios = {"top": y / self.h, "bottom": 1.0 - y / self.h,
                  "left": x / self.w, "right": 1.0 - x / self.w}
        edge = min(ratios, key=ratios.get)
        return edge if ratios[edge] <= self.edge_band else None

    def results(self):
        records = []
        for tid, tr in self.tracks.items():
            if tr.frames < self.min_frames:
                continue
            # Gate on TRAVEL distance, not net displacement: a U-turn
            # ends near where it started but travels a long way.
            if self.travel.get(tid, 0.0) < self.min_disp:
                continue  # parked / jitter
            if tr.first[1] < self.entry_y:
                continue  # did not enter from the filmed approach
            edge = self._exit_edge(tr.last[0], tr.last[1])
            records.append({
                "track_id": tid,
                "cls_name": tr.votes.most_common(1)[0][0] if tr.votes
                else "car",
                "entry": "approach",
                "exit": edge or "mid-frame",
                "movement": EDGE_MOVEMENT.get(edge, "unknown"),
                "frames_seen": tr.frames,
            })
        return records


def aggregate_single(records):
    """records -> {movement: Counter(cls_name)} for one approach."""
    agg = {m: Counter() for m in MOVEMENTS + ("uturn",)}
    for r in records:
        if r["movement"] in agg:
            agg[r["movement"]][r["cls_name"]] += 1
    return agg
