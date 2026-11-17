# -*- coding: utf-8 -*-
"""
Vehicle counting and classification statistics (pure logic, no ML deps).

Core idea
---------
Counting by "detection boxes per frame" double-counts the same vehicle in
every frame. This module counts by *track ID + counting-line crossing*:

- ByteTrack (via ultralytics `model.track`) assigns each vehicle a stable ID.
- A virtual counting line is placed across the lane(s).
- When the centre of a tracked box crosses the line, that ID is counted
  exactly once, under the vehicle class reported for that ID.
- The class of an ID is decided by majority vote over its observations,
  which makes the final type robust against single-frame misclassification.

This module is deliberately dependency-free so the counting logic can be
unit-tested with synthetic tracks before any real video is processed.
"""

from collections import Counter, defaultdict

# COCO vehicle classes used by YOLOv8 -> Chinese labels for the thesis/UI.
VEHICLE_CLASSES = {
    "car": "小汽车",
    "bus": "客车",
    "truck": "货车",
    "motorcycle": "摩托车",
    "bicycle": "非机动车",
}


class TrackState:
    __slots__ = ("last_side", "counted", "class_votes", "first_seen", "last_seen")

    def __init__(self, frame_idx):
        self.last_side = None        # -1 = above line, +1 = below line
        self.counted = False
        self.class_votes = Counter()
        self.first_seen = frame_idx
        self.last_seen = frame_idx

    @property
    def final_class(self):
        if not self.class_votes:
            return "car"
        return self.class_votes.most_common(1)[0][0]


class VehicleCounter:
    """Count vehicles crossing a horizontal counting line, by type.

    Parameters
    ----------
    line_y_ratio : float
        Vertical position of the counting line, as a ratio of frame height
        (0.0 = top, 1.0 = bottom). 0.55-0.65 works well for typical
        roadside / gantry camera angles.
    direction : str
        "down" counts vehicles moving downwards across the line,
        "up" counts upwards, "both" counts either direction.
    """

    def __init__(self, line_y_ratio=0.6, direction="both"):
        if direction not in ("down", "up", "both"):
            raise ValueError("direction must be 'down', 'up' or 'both'")
        self.line_y_ratio = float(line_y_ratio)
        self.direction = direction
        self.tracks = {}
        self.counted_events = []          # list of dicts, one per counted vehicle
        self._by_type = Counter()
        self._by_time_bucket = defaultdict(Counter)  # bucket -> Counter(type)

    # ------------------------------------------------------------------ API
    def update(self, frame_idx, frame_height, detections, time_bucket=None):
        """Feed one frame of tracked detections.

        detections: iterable of dicts with keys
            track_id (int), cls_name (str), cx (float), cy (float)
            where cx/cy are box-centre coordinates in pixels.
        Returns the list of counting events triggered by this frame.
        """
        line_y = self.line_y_ratio * frame_height
        new_events = []
        for det in detections:
            tid = det["track_id"]
            if tid is None or tid < 0:
                continue  # untracked detection: cannot dedupe, skip
            state = self.tracks.get(tid)
            if state is None:
                state = TrackState(frame_idx)
                self.tracks[tid] = state
            state.last_seen = frame_idx
            if det["cls_name"] in VEHICLE_CLASSES:
                state.class_votes[det["cls_name"]] += 1

            side = -1 if det["cy"] < line_y else 1
            if state.last_side is not None and side != state.last_side \
                    and not state.counted:
                moving_down = state.last_side == -1 and side == 1
                if self.direction == "both" \
                        or (self.direction == "down" and moving_down) \
                        or (self.direction == "up" and not moving_down):
                    state.counted = True
                    cls_name = state.final_class
                    event = {
                        "track_id": tid,
                        "cls_name": cls_name,
                        "cls_label": VEHICLE_CLASSES.get(cls_name, cls_name),
                        "frame": frame_idx,
                        "direction": "down" if moving_down else "up",
                        "time_bucket": time_bucket,
                    }
                    self.counted_events.append(event)
                    self._by_type[cls_name] += 1
                    if time_bucket is not None:
                        self._by_time_bucket[time_bucket][cls_name] += 1
                    new_events.append(event)
            state.last_side = side
        return new_events

    # -------------------------------------------------------------- results
    @property
    def total(self):
        return sum(self._by_type.values())

    def summary(self):
        total = self.total
        by_type = []
        for cls_name, label in VEHICLE_CLASSES.items():
            n = self._by_type.get(cls_name, 0)
            by_type.append({
                "cls_name": cls_name,
                "cls_label": label,
                "count": n,
                "share": round(n / total, 4) if total else 0.0,
            })
        flow_curve = []
        for bucket in sorted(self._by_time_bucket):
            c = self._by_time_bucket[bucket]
            flow_curve.append({"bucket": bucket, "total": sum(c.values()),
                               "by_type": dict(c)})
        # R21/v0.14: per-direction aggregation, derived from the
        # counted events (each carries direction + time_bucket).
        # A section total that mixes both travel directions is not a
        # usable traffic figure, so every summary now splits down/up:
        # totals, per-type shares of the whole line count, and a
        # per-bucket direction split for the flow curve. For the
        # vertical counter (fed swapped coordinates) "down"/"up" mean
        # screen-right / screen-left; the UI labels them accordingly.
        dir_counts = {"down": Counter(), "up": Counter()}
        dir_bucket = defaultdict(lambda: {"down": Counter(),
                                          "up": Counter()})
        for e in self.counted_events:
            d = e.get("direction")
            if d not in dir_counts:
                continue
            dir_counts[d][e["cls_name"]] += 1
            if e.get("time_bucket") is not None:
                dir_bucket[e["time_bucket"]][d][e["cls_name"]] += 1
        by_direction = {}
        for d in ("down", "up"):
            c = dir_counts[d]
            by_direction[d] = {
                "total": sum(c.values()),
                "by_type": [{
                    "cls_name": cls_name,
                    "cls_label": label,
                    "count": c.get(cls_name, 0),
                    "share": round(c.get(cls_name, 0) / total, 4)
                    if total else 0.0,
                } for cls_name, label in VEHICLE_CLASSES.items()],
            }
        for entry in flow_curve:
            bd = dir_bucket.get(entry["bucket"])
            entry["by_direction"] = {
                d: {"total": sum(bd[d].values()), "by_type": dict(bd[d])}
                if bd else {"total": 0, "by_type": {}}
                for d in ("down", "up")}
        return {
            "total": total,
            "by_type": by_type,
            "by_direction": by_direction,
            "flow_curve": flow_curve,
            "unique_tracks_seen": len(self.tracks),
        }
