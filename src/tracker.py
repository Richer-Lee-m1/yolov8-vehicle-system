# -*- coding: utf-8 -*-
"""
Numpy ByteTrack core — no torch, no scipy.

Design reference: ByteTrack (ifzhang/ByteTrack, MIT license), see
THIRD_PARTY.md. The tracker below is a clean-room re-implementation of
its published algorithm in this file only (Kalman filter + two-stage
association), not a copy of its source:

- Track state: [cx, cy, aspect(w/h), h, vx, vy, va, vh] with the
  DeepSORT/ByteTrack Kalman filter (std weights 1/20 position, 1/160
  velocity, scaled by box height).
- Stage 1: high-confidence detections (conf >= track_high_thresh)
  matched to active tracks by IoU (greedy, best-IoU-first).
- Stage 2: remaining low-confidence detections matched to the tracks
  still unmatched, again by IoU.
- Unmatched high-confidence detections above new_track_thresh start
  new tracks; unmatched tracks go to a lost buffer (track_buffer
  frames) before deletion.
- Matching is optimal Hungarian assignment on IoU similarity (pure
  numpy, no scipy/lap), with detection-score fusion in stage 1
  (ultralytics ``fuse_score=True``): similarity = IoU * score.

R3 hardening (measured against the installed ultralytics tracker,
whose source is the ground truth for the thesis pipeline's counts):
- Two-frame confirmation (ultralytics-faithful): a new track starts
  *unconfirmed* — it is not output, and it does not enter the stage-1
  pool. In the next frame it is matched in a dedicated unconfirmed
  step against leftover high detections (fused similarity >= 0.3,
  cf. ultralytics ``linear_assignment(dists, thresh=0.7)``); a match
  confirms it, a miss deletes it immediately instead of parking it
  in the 30-frame lost buffer where its drifting prediction could
  steal later detections. R2's 473 tracks vs the torch baseline's
  296 came largely from single-frame false positives and duplicates
  surviving as permanent IDs.
- Duplicate-track suppression: (a) at creation, an unmatched
  detection whose box overlaps any already-active track with
  IoU > 0.7 is dropped instead of spawning a second track — the
  detector's per-class NMS cannot suppress cross-class duplicates
  (e.g. a ``car`` box and a ``van`` box for the same vehicle; 142
  such IoU > 0.7 pairs were measured on sampled test1 frames, ~0.9
  per frame), and those pairs otherwise become two tracks that both
  cross the line; (b) end-of-frame de-dup among active tracks and
  between active and lost tracks at IoU > 0.85 (the latter mirrors
  ultralytics ``remove_duplicate_stracks``), keeping the
  longer-lived (more hits) track.
- The creation guard is a deliberate product-side deviation from
  ultralytics (which has no such guard): two genuinely distinct
  vehicles sharing IoU > 0.7 in one frame are nearly the same box,
  and the suppressed one is re-created once the boxes separate.

Defaults mirror the *installed* ultralytics bytetrack.yaml (the exact
config the thesis pipeline runs with `model.track(..., conf=0.35)`):
track_high_thresh 0.25, track_low_thresh 0.1, new_track_thresh 0.25,
match_thresh 0.8, track_buffer 30. NOTE: these differ from the
ByteTrack paper's defaults (0.5 / 0.6) — R2 first used the paper values
and undercounted test1 (h 15 vs 18, v 32 vs 37) because vehicles first
seen at conf 0.35-0.6 never started a track before crossing the line.
With detector conf 0.35, stage 2 (low) is empty in practice, which is
exactly the ultralytics behaviour at this conf.
"""

import numpy as np

# ---------------------------------------------------------------- Kalman
# State: [x, y, a, h, vx, vy, va, vh]  (x,y = centre, a = w/h, h = height)

def _motion_matrix():
    F = np.eye(8)
    for i in range(4):
        F[i, 4 + i] = 1.0
    return F


_F = _motion_matrix()
_H = np.zeros((4, 8))
_H[0, 0] = _H[1, 1] = _H[2, 2] = _H[3, 3] = 1.0
_STD_W_POS = 1.0 / 20
_STD_W_VEL = 1.0 / 160


class KalmanFilter:
    def __init__(self, mean, covariance):
        self.mean = mean
        self.covariance = covariance

    @staticmethod
    def initiate(measurement):
        """measurement = [x, y, a, h]."""
        mean = np.zeros(8)
        mean[:4] = measurement
        std = np.array([
            2 * _STD_W_POS * measurement[3],
            2 * _STD_W_POS * measurement[3],
            1e-2,
            2 * _STD_W_POS * measurement[3],
            10 * _STD_W_VEL * measurement[3],
            10 * _STD_W_VEL * measurement[3],
            1e-5,
            10 * _STD_W_VEL * measurement[3],
        ])
        return KalmanFilter(mean, np.diag(std ** 2))

    def predict(self):
        h = self.mean[3]
        std_pos = np.array([
            _STD_W_POS * h, _STD_W_POS * h, 1e-2, _STD_W_POS * h])
        std_vel = np.array([
            _STD_W_VEL * h, _STD_W_VEL * h, 1e-5, _STD_W_VEL * h])
        motion_cov = np.diag(np.concatenate([std_pos, std_vel]) ** 2)
        self.mean = _F @ self.mean
        self.covariance = _F @ self.covariance @ _F.T + motion_cov

    def update(self, measurement):
        h = self.mean[3]
        std = np.array([_STD_W_POS * h, _STD_W_POS * h, 1e-1, _STD_W_POS * h])
        R = np.diag(std ** 2)
        S = _H @ self.covariance @ _H.T + R
        K = self.covariance @ _H.T @ np.linalg.inv(S)
        y = measurement - _H @ self.mean
        self.mean = self.mean + K @ y
        I_KH = np.eye(8) - K @ _H
        self.covariance = I_KH @ self.covariance @ I_KH.T + K @ R @ K.T


def _xyxy_to_state(box):
    x1, y1, x2, y2 = box
    w = max(x2 - x1, 1.0)
    h = max(y2 - y1, 1.0)
    return np.array([(x1 + x2) / 2, (y1 + y2) / 2, w / h, h])


def _state_to_xyxy(mean):
    x, y, a, h = mean[:4]
    w = a * h
    return np.array([x - w / 2, y - h / 2, x + w / 2, y + h / 2])


def _iou_matrix(boxes_a, boxes_b):
    """boxes: (N, 4) xyxy -> (N, M) IoU."""
    if len(boxes_a) == 0 or len(boxes_b) == 0:
        return np.zeros((len(boxes_a), len(boxes_b)))
    a = boxes_a[:, None, :]
    b = boxes_b[None, :, :]
    x1 = np.maximum(a[..., 0], b[..., 0])
    y1 = np.maximum(a[..., 1], b[..., 1])
    x2 = np.minimum(a[..., 2], b[..., 2])
    y2 = np.minimum(a[..., 3], b[..., 3])
    inter = np.maximum(x2 - x1, 0) * np.maximum(y2 - y1, 0)
    area_a = (a[..., 2] - a[..., 0]) * (a[..., 3] - a[..., 1])
    area_b = (b[..., 2] - b[..., 0]) * (b[..., 3] - b[..., 1])
    union = area_a + area_b - inter
    return np.where(union > 0, inter / np.maximum(union, 1e-9), 0.0)


def _hungarian(cost):
    """Shortest-augmenting-path Hungarian assignment (minimise total cost).

    Pure-numpy implementation of the O(n^3) algorithm (e-maxx style);
    no scipy needed. ``cost`` is an (n, m) array with n <= m; returns a
    dict {row: col} covering every row. Callers transpose when needed.
    """
    n, m = cost.shape
    u = np.zeros(n + 1)
    v = np.zeros(m + 1)
    p = np.zeros(m + 1, dtype=int)   # p[j] = row (1-based) assigned to col j
    way = np.zeros(m + 1, dtype=int)
    for i in range(1, n + 1):
        p[0] = i
        j0 = 0
        minv = np.full(m + 1, np.inf)
        used = np.zeros(m + 1, dtype=bool)
        while True:
            used[j0] = True
            i0 = p[j0]
            delta = np.inf
            j1 = 0
            for j in range(1, m + 1):
                if not used[j]:
                    cur = cost[i0 - 1, j - 1] - u[i0] - v[j]
                    if cur < minv[j]:
                        minv[j] = cur
                        way[j] = j0
                    if minv[j] < delta:
                        delta = minv[j]
                        j1 = j
            for j in range(m + 1):
                if used[j]:
                    u[p[j]] += delta
                    v[j] -= delta
                else:
                    minv[j] -= delta
            j0 = j1
            if p[j0] == 0:
                break
        while j0:
            j1 = way[j0]
            p[j0] = p[j1]
            j0 = j1
    return {int(p[j]) - 1: j - 1 for j in range(1, m + 1) if p[j] != 0}


def _greedy_match(iou, iou_thresh):
    """Greedy best-IoU-first matching. Returns (pairs, unmatched_a,
    unmatched_b); pairs are (a_idx, b_idx) with IoU >= iou_thresh."""
    if iou.size == 0:
        return [], list(range(iou.shape[0])), list(range(iou.shape[1]))
    flat = np.argsort(-iou.ravel())
    used_a, used_b, pairs = set(), set(), []
    for flat_idx in flat:
        i, j = divmod(int(flat_idx), iou.shape[1])
        if iou[i, j] < iou_thresh:
            break
        if i in used_a or j in used_b:
            continue
        used_a.add(i)
        used_b.add(j)
        pairs.append((i, j))
    unmatched_a = [i for i in range(iou.shape[0]) if i not in used_a]
    unmatched_b = [j for j in range(iou.shape[1]) if j not in used_b]
    return pairs, unmatched_a, unmatched_b


# ---------------------------------------------------------------- Tracker
class Track:
    __slots__ = ("track_id", "kf", "cls_name", "conf", "hits",
                 "time_since_update", "state", "confirmed", "start_frame")

    def __init__(self, track_id, det, frame_id=0):
        self.track_id = track_id
        self.kf = KalmanFilter.initiate(
            _xyxy_to_state([det["x1"], det["y1"], det["x2"], det["y2"]]))
        self.cls_name = det["cls_name"]
        self.conf = det["conf"]
        self.hits = 1
        self.time_since_update = 0
        self.state = "active"  # active | lost
        # Unconfirmed until matched again in a later frame (ultralytics
        # ``is_activated``): not output, not in the stage-1 pool.
        self.confirmed = False
        self.start_frame = frame_id

    def predict(self):
        self.kf.predict()
        self.time_since_update += 1

    def update(self, det):
        self.kf.update(_xyxy_to_state(
            [det["x1"], det["y1"], det["x2"], det["y2"]]))
        self.cls_name = det["cls_name"]
        self.conf = det["conf"]
        self.hits += 1
        self.time_since_update = 0
        self.state = "active"

    @property
    def box(self):
        return _state_to_xyxy(self.kf.mean)


class ByteTracker:
    """Two-stage ByteTrack association over numpy Kalman tracks."""

    # IoU above which an unmatched detection is treated as a duplicate
    # of an already-tracked object instead of a new track.
    DUP_CREATE_IOU = 0.7
    # IoU above which two live tracks (or a live and a lost track) are
    # treated as the same object at end of frame.
    DUP_TRACK_IOU = 0.85
    # Fused-similarity floor for confirming an unconfirmed track
    # (ultralytics: cost < 0.7 on 1 - IoU*score).
    UNCONFIRMED_SIM = 0.3

    def __init__(self, track_high_thresh=0.25, track_low_thresh=0.1,
                 new_track_thresh=0.25, match_thresh=0.8,
                 match_thresh_low=0.5, track_buffer=30):
        self.track_high_thresh = track_high_thresh
        self.track_low_thresh = track_low_thresh
        self.new_track_thresh = new_track_thresh
        self.match_thresh = match_thresh
        self.match_thresh_low = match_thresh_low
        self.track_buffer = track_buffer
        self.tracks = []
        self._next_id = 1
        self._frame_id = 0

    def update(self, detections):
        """detections: list of detector dicts (with conf/cls_name).
        Returns list of *confirmed* active tracks as dicts:
        track_id, cls_name, x1, y1, x2, y2."""
        self._frame_id += 1
        for tr in self.tracks:
            tr.predict()

        high = [d for d in detections if d["conf"] >= self.track_high_thresh]
        low = [d for d in detections
               if self.track_low_thresh <= d["conf"] < self.track_high_thresh]
        # Unconfirmed tracks stay out of the stage-1 pool (ultralytics
        # _split_tracked): they get their own association step below.
        confirmed = [t for t in self.tracks
                     if t.state == "active" and t.confirmed]
        lost = [t for t in self.tracks if t.state == "lost"]
        unconfirmed = [t for t in self.tracks
                       if t.state == "active" and not t.confirmed]

        # Stage 1: high-confidence detections vs confirmed + lost tracks.
        cand = confirmed + lost
        pairs, un_tr, un_det = self._associate(cand, high,
                                               1 - self.match_thresh,
                                               fuse=True)
        matched_ids = set()
        for ti, di in pairs:
            cand[ti].update(high[di])
            matched_ids.add(cand[ti].track_id)

        # Stage 2: low-confidence detections vs still-unmatched
        # confirmed tracks only (lost tracks are not re-matched here,
        # same as ultralytics _second_association).
        remaining = [cand[i] for i in un_tr if cand[i].state == "active"]
        pairs2, un_tr2, un_det2 = self._associate(
            remaining, low, 1 - self.match_thresh_low, fuse=False)
        for ti, di in pairs2:
            remaining[ti].update(low[di])
            matched_ids.add(remaining[ti].track_id)

        # Unconfirmed association: unconfirmed tracks vs leftover high
        # detections. A match confirms the track; a miss deletes it
        # immediately (it never enters the lost buffer).
        leftover = [high[i] for i in un_det]
        doomed = set()
        if unconfirmed and leftover:
            pairs3, un_unconf, un_det3 = self._associate(
                unconfirmed, leftover, self.UNCONFIRMED_SIM, fuse=True)
            for ti, di in pairs3:
                unconfirmed[ti].update(leftover[di])
                unconfirmed[ti].confirmed = True
                matched_ids.add(unconfirmed[ti].track_id)
            doomed = {unconfirmed[i].track_id for i in un_unconf}
            leftover = [leftover[i] for i in un_det3]
        elif unconfirmed:
            doomed = {t.track_id for t in unconfirmed}

        # New tracks from surviving high-confidence detections, with a
        # duplicate guard: a detection overlapping an already-active
        # track (confirmed or just created this frame) is the same
        # object seen under another class label, not a new vehicle.
        guard = [t for t in self.tracks if t.state == "active"]
        for det in leftover:
            if det["conf"] < self.new_track_thresh:
                continue
            if guard:
                boxes = np.array([t.box for t in guard])
                dbox = np.array([[det["x1"], det["y1"],
                                  det["x2"], det["y2"]]])
                if _iou_matrix(boxes, dbox).max() > self.DUP_CREATE_IOU:
                    continue
            tr = Track(self._next_id, det, frame_id=self._frame_id)
            self.tracks.append(tr)
            guard.append(tr)
            matched_ids.add(tr.track_id)
            self._next_id += 1

        # Housekeeping: unmatched confirmed active -> lost; drop lost
        # past buffer; drop unconfirmed tracks that failed to confirm.
        kept = []
        for tr in self.tracks:
            if tr.track_id in doomed:
                continue
            if tr.track_id not in matched_ids and tr.state == "active" \
                    and tr.confirmed:
                tr.state = "lost"
            if tr.state == "lost" and tr.time_since_update > self.track_buffer:
                continue
            kept.append(tr)
        self.tracks = kept
        self._dedup()

        out = []
        for tr in self.tracks:
            if tr.state != "active" or not tr.confirmed \
                    or tr.time_since_update > 0:
                continue
            x1, y1, x2, y2 = tr.box
            out.append({
                "track_id": tr.track_id,
                "cls_name": tr.cls_name,
                "x1": float(x1), "y1": float(y1),
                "x2": float(x2), "y2": float(y2),
            })
        return out

    def _dedup(self):
        """Drop duplicate tracks: active-vs-active and active-vs-lost
        pairs with IoU > DUP_TRACK_IOU keep the longer-lived track
        (more hits; ties keep the lower id). Mirrors ultralytics
        remove_duplicate_stracks (tracked vs lost) and extends it to
        two live tracks, which cross-class duplicate detections can
        otherwise create in the same frame."""
        active = [t for t in self.tracks if t.state == "active"]
        lost = [t for t in self.tracks if t.state == "lost"]
        drop = set()
        for group_a, group_b, cross in ((active, active, False),
                                        (active, lost, True)):
            if not group_a or not group_b:
                continue
            boxes_a = np.array([t.box for t in group_a])
            boxes_b = np.array([t.box for t in group_b])
            iou = _iou_matrix(boxes_a, boxes_b)
            for i in range(len(group_a)):
                for j in range(len(group_b)):
                    if not cross and j <= i:
                        continue
                    if iou[i, j] <= self.DUP_TRACK_IOU:
                        continue
                    a, b = group_a[i], group_b[j]
                    if (a.hits, -a.track_id) >= (b.hits, -b.track_id):
                        drop.add(b.track_id)
                    else:
                        drop.add(a.track_id)
        if drop:
            self.tracks = [t for t in self.tracks
                           if t.track_id not in drop]

    @staticmethod
    def _associate(tracks, detections, sim_thresh, fuse=False):
        """Optimal (Hungarian) assignment on IoU similarity.

        sim_thresh: minimum similarity for a match. With fuse=True the
        similarity is IoU * detection-score (ultralytics ``fuse_score``,
        on by default in bytetrack.yaml): a weak detection needs a much
        higher IoU to steal a track, which stabilises dense small-target
        scenes. Stage 1 (fused): sim >= 1 - match_thresh = 0.2;
        stage 2 (plain IoU): sim >= 0.5, per ultralytics.
        Returns (pairs, unmatched_track_idx, unmatched_det_idx)."""
        if not tracks or not detections:
            return [], list(range(len(tracks))), list(range(len(detections)))
        boxes_t = np.array([t.box for t in tracks])
        boxes_d = np.array([[d["x1"], d["y1"], d["x2"], d["y2"]]
                            for d in detections])
        iou = _iou_matrix(boxes_t, boxes_d)
        if fuse:
            scores = np.array([d["conf"] for d in detections])
            sim = iou * scores[None, :]
        else:
            sim = iou
        cost = 1.0 - sim
        if len(tracks) <= len(detections):
            assign = _hungarian(cost)
            pairs = [(r, c) for r, c in assign.items()
                     if sim[r, c] >= sim_thresh]
        else:
            assign = _hungarian(cost.T)
            pairs = [(c, r) for r, c in assign.items()
                     if sim[c, r] >= sim_thresh]
        used_t = {r for r, _ in pairs}
        used_d = {c for _, c in pairs}
        return (pairs,
                [i for i in range(len(tracks)) if i not in used_t],
                [j for j in range(len(detections)) if j not in used_d])
