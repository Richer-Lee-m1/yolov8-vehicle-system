# -*- coding: utf-8 -*-
"""
Product engine: ONNX detector + numpy ByteTrack + counting/movements.

Same counting semantics and output files as the thesis package's
detect_system.py (annotated.mp4 / events.csv / summary.json /
movements.csv / survey_auto.json), but the inference path uses no
PyTorch: onnx_detector.py + tracker.py only.

Usage:
    python src/engine.py --source test1.mp4 --model models/yolov8s-visdrone.onnx \
        --line 0.6 --line-x 0.3 --conf 0.35 --out outputs/test1 \
        --classify --center-x 0.44 --center-y 0.45
    # Single-approach clip (camera behind one approach's stop line):
    python src/engine.py --source ap_east.mp4 --out outputs/ap_east \
        --approach east
"""

import argparse
import csv
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from counting import VehicleCounter, VEHICLE_CLASSES  # noqa: E402
from movements import (MovementClassifier, aggregate,  # noqa: E402
                       SingleApproachClassifier, aggregate_single)
from onnx_detector import OnnxDetector, VISDRONE_NAMES  # noqa: E402
from tracker import ByteTracker  # noqa: E402

# --approach argument -> display name of the filmed approach.
APPROACH_ARG = {"east": "东进口", "south": "南进口",
                "west": "西进口", "north": "北进口"}

# Model class name -> counting class name (same table as the thesis
# package's detect_system.py; covers VisDrone aliases and COCO names).
NAME_MAP = {
    "car": "car",
    "motorcycle": "motorcycle",
    "bus": "bus",
    "truck": "truck",
    "bicycle": "bicycle",
    "motor": "motorcycle",
    "van": "car",
    "tricycle": "motorcycle",
    "awning-tricycle": "motorcycle",
}


def _transcode_h264(path):
    """Transcode annotated.mp4 to H.264 (browser-playable) in place.

    Returns True on success. OpenCV writes FMP4/mp4v, which Chromium
    cannot decode in <video>; ffmpeg/libx264 is the portable fix.
    On machines without ffmpeg (future exe packaging, P4) this is a
    no-op and the mp4v file is kept.
    """
    import shutil
    import subprocess
    import tempfile

    ffmpeg = shutil.which("ffmpeg")
    if not ffmpeg:
        print("[engine] ffmpeg not found: annotated.mp4 stays mp4v "
              "(may not play in browser preview)", flush=True)
        return False
    tmp = tempfile.mktemp(suffix=".mp4",
                          dir=os.path.dirname(path) or ".")
    try:
        proc = subprocess.run(
            [ffmpeg, "-y", "-v", "error", "-i", path,
             "-c:v", "libx264", "-pix_fmt", "yuv420p",
             "-movflags", "+faststart", "-crf", "23", tmp],
            timeout=1800)
        if proc.returncode != 0 or not os.path.exists(tmp):
            return False
        os.replace(tmp, path)
        return True
    except Exception:  # noqa: BLE001 - keep original file on any failure
        try:
            if os.path.exists(tmp):
                os.remove(tmp)
        except OSError:
            pass
        return False


def run_video(source, model_path, line_ratio, conf, out_dir,
              bucket_seconds=60, line_x_ratio=None, classify=False,
              center_x=0.5, center_y=0.5, imgsz=640, progress_path=None,
              approach=None, stream=False, duration_s=60.0,
              stop_file=None):
    """Run detection+tracking+counting on a file or a live stream.

    stream=True: source is an RTSP/RTMP/HTTP URL (or "cam:N" for a
    local camera). Frames are processed as fast as the CPU allows
    until duration_s wall seconds pass, the stream drops, or
    stop_file appears (server-side stop). Outputs are the same files
    as file mode; summary["params"] records source_type="stream".
    """
    import cv2
    import time

    os.makedirs(out_dir, exist_ok=True)
    detector = OnnxDetector(model_path, names=VISDRONE_NAMES,
                            imgsz=imgsz, conf=conf)
    tracker = ByteTracker()

    if isinstance(source, str) and source.startswith("cam:"):
        try:
            source = int(source[4:])
        except ValueError:
            raise RuntimeError(f"invalid camera source: {source}")
    cap = cv2.VideoCapture(source)
    if not cap.isOpened():
        raise RuntimeError(f"cannot open source: {source}")
    fps = cap.get(cv2.CAP_PROP_FPS) or 25.0
    total_frames = int(cap.get(cv2.CAP_PROP_FRAME_COUNT) or 0)
    t0 = time.time()

    def _progress(frame_idx, done=False, live=None):
        if not progress_path:
            return
        try:
            payload = {"frame": frame_idx,
                       "total_frames": total_frames if not stream
                       else None,
                       "pct": round(100.0 * frame_idx / total_frames, 1)
                       if total_frames and not stream else None,
                       "elapsed_s": round(time.time() - t0, 1),
                       "stream": stream,
                       "done": done}
            if live is not None:
                payload["live"] = live
            with open(progress_path, "w", encoding="utf-8") as f:
                json.dump(payload, f)
        except OSError:
            pass
    width = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
    height = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))

    writer = cv2.VideoWriter(
        os.path.join(out_dir, "annotated.mp4"),
        cv2.VideoWriter_fourcc(*"mp4v"), fps, (width, height))

    counter = VehicleCounter(line_y_ratio=line_ratio, direction="both")
    counter_v = None
    if line_x_ratio is not None:
        counter_v = VehicleCounter(line_y_ratio=line_x_ratio,
                                   direction="both")
    classifier = None
    if classify:
        classifier = MovementClassifier(width, height,
                                        center_x_ratio=center_x,
                                        center_y_ratio=center_y)
    # Single-approach mode: the video covers ONE approach only (camera
    # behind its stop line). Movements are read off the frame edge each
    # track leaves through; see movements.SingleApproachClassifier.
    single = None
    if approach is not None:
        single = SingleApproachClassifier(width, height,
                                          APPROACH_ARG[approach])
    bucket_frames = max(1, int(bucket_seconds * fps))

    def _live_snapshot(raw_frame, annotated_frame, frame_idx):
        """P3.5 live feedback: write the paired frames of this moment
        (raw + annotated JPEGs) and return the live counts snapshot
        (per-type crossings so far, tracks seen, partial flow curve).

        Called every 5 frames and immediately when a crossing happens
        (R19: was every 15 frames), in file mode and stream mode
        alike; cheap (two small JPEG encodes) next to detection.
        """
        for fname, img in (("live_raw.jpg", raw_frame),
                           ("live_annotated.jpg", annotated_frame)):
            try:
                # The frame can be None/empty when the loop ended on a
                # failed read (end of file / dropped stream): skip the
                # JPEG then, the counts snapshot below still goes out.
                if img is None or getattr(img, "size", 0) == 0:
                    continue
                # R19: preview at 640px wide (was full 1080p) — a fifth
                # of the pixels, so the 5-frame cadence stays cheap.
                h, w = img.shape[:2]
                if w > 640:
                    img = cv2.resize(img, (640, int(h * 640 / w)))
                ok, buf = cv2.imencode(
                    ".jpg", img, [cv2.IMWRITE_JPEG_QUALITY, 78])
                if ok:
                    with open(os.path.join(out_dir, fname), "wb") as f:
                        f.write(buf.tobytes())
            except OSError:
                pass

        def _by_type(counter_obj):
            return {r["cls_name"]: r["count"]
                    for r in counter_obj.summary()["by_type"]}

        snap = {"frame": frame_idx,
                "total": counter.total,
                "by_type": _by_type(counter),
                "by_direction": counter.summary()["by_direction"],
                "tracks": len(counter.tracks),
                "flow_curve": counter.summary()["flow_curve"]}
        if counter_v is not None:
            snap["total_v"] = counter_v.total
            snap["by_type_v"] = _by_type(counter_v)
            snap["by_direction_v"] = counter_v.summary()["by_direction"]
        if classifier is not None:
            # R21: per-approach live counts (E/S/W/N) from the same
            # aggregate the final summary uses, so the panel can show
            # each approach's total + movement split + type split
            # ticking live instead of only per-line totals. Values are
            # "so far": a still-active track's exit side is provisional
            # and its movement may be re-bucketed by the next snapshot,
            # but every gated track contributes exactly one record.
            approaches = {}
            for name, by_move in aggregate(classifier.results()).items():
                by_type_a, by_movement_a, total_a = {}, {}, 0
                for m, cnt in by_move.items():
                    n = sum(cnt.values())
                    by_movement_a[m] = n
                    total_a += n
                    for cls_name, c in cnt.items():
                        by_type_a[cls_name] = by_type_a.get(cls_name, 0) + c
                approaches[name] = {"total": total_a,
                                    "by_movement": by_movement_a,
                                    "by_type": by_type_a}
            snap["approaches"] = approaches
        if single is not None:
            by_type_s, by_movement_s, total_s = {}, {}, 0
            for m, cnt in aggregate_single(single.results()).items():
                n = sum(cnt.values())
                by_movement_s[m] = n
                total_s += n
                for cls_name, c in cnt.items():
                    by_type_s[cls_name] = by_type_s.get(cls_name, 0) + c
            snap["approach_single"] = {"name": single.approach,
                                       "total": total_s,
                                       "by_movement": by_movement_s,
                                       "by_type": by_type_s}
        return snap

    frame_idx = 0
    # R19: last totals seen by the live snapshot, so a crossing can
    # trigger an immediate snapshot instead of waiting for cadence.
    prev_total = 0
    prev_total_v = 0
    read_failures = 0
    stop_reason = "source-ended"
    # Last good frames, kept for the final live snapshot: after the
    # loop ends, `frame` itself may be None (failed read at end of
    # file / dropped stream) and must not be encoded. R13 fix.
    last_raw_frame = None
    last_annotated_frame = None
    frame_queue = None
    grab_state = None
    if stream:
        # A slow detector must not stall the RTSP socket: a grabber
        # thread drains the stream continuously into a bounded queue
        # (R10 root cause: without this, mediamtx kills a slow reader
        # with "write queue is full" and the run ends stream-dropped).
        # The main loop consumes frames in order, so tracking still
        # sees consecutive frames; it just trails live by at most the
        # queue depth (~4 s of video).
        import collections
        import threading
        frame_queue = collections.deque(maxlen=120)
        grab_state = {"failures": 0, "dead": False, "stop": False}
        q_lock = threading.Lock()
        # Read timeout lets the grabber notice the stop flag instead of
        # blocking forever inside cap.read() (ffmpeg backend).
        cap.set(cv2.CAP_PROP_READ_TIMEOUT_MSEC, 2000)

        def _grabber():
            fails = 0
            while not grab_state["stop"]:
                ok, fr = cap.read()
                if not ok:
                    fails += 1
                    grab_state["failures"] = fails
                    if fails >= 30 or grab_state["stop"]:
                        grab_state["dead"] = True
                        return
                    time.sleep(0.2)
                    continue
                fails = 0
                grab_state["failures"] = 0
                with q_lock:
                    frame_queue.append(fr)
            grab_state["dead"] = True

        grabber_thread = threading.Thread(target=_grabber, daemon=True)
        grabber_thread.start()
    while True:
        # Live stream end conditions (file mode keeps the old
        # read-fails-once behaviour via the same counter).
        if stream and time.time() - t0 >= duration_s:
            stop_reason = "duration-reached"
            break
        # R14: stop flag is honoured in FILE mode too, not just stream
        # mode (before this, a file job's stop button wrote a flag the
        # engine never polled, so the job ran to completion and the
        # server's 20 s terminate fallback landed it as ERROR).
        if stop_file and frame_idx % 5 == 0 \
                and os.path.exists(stop_file):
            stop_reason = "stopped"
            break
        if stream:
            frame = None
            wait_t0 = time.time()
            while frame is None:
                with q_lock:
                    if frame_queue:
                        frame = frame_queue.popleft()
                if frame is None:
                    if grab_state["dead"] or time.time() - wait_t0 > 10:
                        stop_reason = "stream-dropped"
                        frame = False
                        break
                    if time.time() - t0 >= duration_s:
                        stop_reason = "duration-reached"
                        frame = False
                        break
                    time.sleep(0.05)
            if frame is False:
                break
        else:
            ok, frame = cap.read()
            if not ok:
                read_failures += 1
                if read_failures >= 30:
                    stop_reason = "stream-dropped"
                    break
                time.sleep(0.2)
                continue
            read_failures = 0
        # Raw frame of this moment, before any annotation is drawn on
        # it: the live pair is (raw, annotated) of the same frame. P3.5.
        raw_frame = frame.copy()
        dets = detector.detect(frame)
        tracks = tracker.update(dets)
        detections = []
        for t in tracks:
            cls_name = NAME_MAP.get(t["cls_name"])
            if cls_name is None:
                continue
            detections.append({
                "track_id": t["track_id"],
                "cls_name": cls_name,
                "cx": (t["x1"] + t["x2"]) / 2.0,
                "cy": (t["y1"] + t["y2"]) / 2.0,
            })
            label = f"#{t['track_id']} {VEHICLE_CLASSES[cls_name]}"
            cv2.rectangle(frame, (int(t["x1"]), int(t["y1"])),
                          (int(t["x2"]), int(t["y2"])), (0, 200, 0), 2)
            cv2.putText(frame, label, (int(t["x1"]), max(0, int(t["y1"]) - 6)),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.6, (0, 200, 0), 2)
        bucket = frame_idx // bucket_frames
        counter.update(frame_idx, height, detections, time_bucket=bucket)
        if classifier is not None:
            for d in detections:
                classifier.observe(d["track_id"], d["cls_name"],
                                   d["cx"], d["cy"])
        if single is not None:
            for d in detections:
                single.observe(d["track_id"], d["cls_name"],
                               d["cx"], d["cy"])
        if counter_v is not None:
            swapped = [{"track_id": d["track_id"], "cls_name": d["cls_name"],
                        "cx": d["cy"], "cy": d["cx"]} for d in detections]
            counter_v.update(frame_idx, width, swapped, time_bucket=bucket)

        line_y = int(line_ratio * height)
        cv2.line(frame, (0, line_y), (width, line_y), (0, 0, 255), 2)
        cv2.putText(frame, f"total: {counter.total}", (16, 36),
                    cv2.FONT_HERSHEY_SIMPLEX, 1.0, (0, 0, 255), 2)
        if counter_v is not None:
            line_x = int(line_x_ratio * width)
            cv2.line(frame, (line_x, 0), (line_x, height), (255, 128, 0), 2)
            cv2.putText(frame, f"cross: {counter_v.total}", (16, 72),
                        cv2.FONT_HERSHEY_SIMPLEX, 1.0, (255, 128, 0), 2)
        if single is not None:
            # Entry gate: tracks must start below this line to count as
            # coming from the filmed approach (keeps cross-street
            # traffic at the top of the frame out).
            gate_y = int(0.35 * height)
            cv2.line(frame, (0, gate_y), (width, gate_y), (0, 255, 255), 1)
            cv2.putText(frame, "entry gate", (16, gate_y - 8),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.6, (0, 255, 255), 2)
            cv2.putText(frame, f"approach: {APPROACH_ARG[approach]}",
                        (16, 108),
                        cv2.FONT_HERSHEY_SIMPLEX, 1.0, (0, 255, 255), 2)
        writer.write(frame)
        last_raw_frame = raw_frame
        last_annotated_frame = frame
        frame_idx += 1
        # R19: paired live frames + live counts snapshot every 5 frames
        # (was 15) and immediately when a crossing happens, so the UI
        # numbers jump the moment a vehicle crosses instead of waiting
        # for the next cadence tick (file and stream alike).
        crossed = (counter.total != prev_total
                   or (counter_v is not None
                       and counter_v.total != prev_total_v))
        if frame_idx % 5 == 0 or crossed:
            prev_total = counter.total
            if counter_v is not None:
                prev_total_v = counter_v.total
            _progress(frame_idx,
                      live=_live_snapshot(raw_frame, frame, frame_idx))
    if stream:
        # Stop the grabber before releasing the capture: releasing
        # while another thread is inside cap.read() aborts the process
        # at shutdown ("FATAL: exception not rethrown", R10).
        grab_state["stop"] = True
        grabber_thread.join(timeout=5)
    cap.release()
    writer.release()
    # Final live snapshot: the last frame pair + final counts, so the
    # UI's live panel ends on the finished frame, not a stale one. P3.5.
    # Uses the kept last-good frames: `frame` itself is None after the
    # loop ends on a failed read, and encoding it crashed the engine
    # (R13: job errored at frame 465/473, before transcode/summary).
    final_live = None
    if frame_idx > 0:
        final_live = _live_snapshot(last_raw_frame,
                                    last_annotated_frame, frame_idx)
    _progress(frame_idx, done=True, live=final_live)
    # Wall time of the detection loop itself (excludes the H.264
    # transcode below, so duration_s means what the user asked for).
    loop_end = time.time()

    # P5: the summary is written BEFORE the H.264 transcode below.
    # The old order (transcode first) meant a stopped job whose
    # transcode outlasted the server's stop watcher was killed with no
    # summary.json at all -> the job landed as "error" and its counts
    # were lost (r15-livefix: stopped at frame 105, killed
    # mid-transcode, no summary). Now the summary (codec "mp4v") is on
    # disk first; a successful transcode then patches
    # params.annotated_codec to "h264" and rewrites it.
    annotated_path = os.path.join(out_dir, "annotated.mp4")
    annotated_codec = "mp4v"

    all_events = [dict(e, line="horizontal") for e in counter.counted_events]
    if counter_v is not None:
        all_events += [dict(e, line="vertical")
                       for e in counter_v.counted_events]
    all_events.sort(key=lambda e: e["frame"])
    with open(os.path.join(out_dir, "events.csv"), "w", newline="",
              encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=["track_id", "cls_name", "cls_label",
                                          "frame", "direction", "time_bucket",
                                          "line"])
        w.writeheader()
        w.writerows(all_events)
    summary = counter.summary()
    if counter_v is not None:
        summary["by_line"] = {"horizontal": counter.summary(),
                              "vertical": counter_v.summary()}
    if classifier is not None:
        records = classifier.results()
        with open(os.path.join(out_dir, "movements.csv"), "w", newline="",
                  encoding="utf-8") as f:
            w = csv.DictWriter(f, fieldnames=["track_id", "cls_name",
                                              "entry", "exit", "movement",
                                              "frames_seen"])
            w.writeheader()
            w.writerows(records)
        agg = aggregate(records)
        movements_out = {}
        for ap_name, by_move in agg.items():
            movements_out[ap_name] = {
                m: dict(by_move[m]) for m in by_move}
        summary["movements"] = movements_out
        survey = {
            "data_note": "系统自动组装的分进口分流向计数（原始计数，未折算"
                         "小时流量）；车道数/饱和流量/信号配时等几何参数需"
                         "按实地调查补齐后才能作为 planning.py 正式输入。",
            "approaches": [
                {"name": ap_name,
                 "movements": {
                     m: {"flow_by_type": movements_out[ap_name][m]}
                     for m in ("left", "through", "right")}}
                for ap_name in movements_out],
        }
        with open(os.path.join(out_dir, "survey_auto.json"), "w",
                  encoding="utf-8") as f:
            json.dump(survey, f, ensure_ascii=False, indent=2)
    if single is not None:
        records = single.results()
        # Kept separate from movements.csv (the 4-approach classifier's
        # output) so the two modes never overwrite each other -- the
        # thesis package reuses movements.csv here; the product keeps
        # both files when both modes run together.
        with open(os.path.join(out_dir, "approach_movements.csv"), "w",
                  newline="", encoding="utf-8") as f:
            w = csv.DictWriter(f, fieldnames=["track_id", "cls_name",
                                              "entry", "exit", "movement",
                                              "frames_seen"])
            w.writeheader()
            w.writerows(records)
        agg1 = aggregate_single(records)
        if stream:
            duration_min = round((loop_end - t0) / 60.0, 2)
        else:
            duration_min = round(frame_idx / fps / 60.0, 2) if fps else 0.0
        line_totals = {r["cls_name"]: r["count"]
                       for r in counter.summary()["by_type"]}
        frag = {
            "approach": APPROACH_ARG[approach],
            "approach_key": approach,
            "mode": "single-approach",
            "duration_min": duration_min,
            "counting_line_total_by_type": line_totals,
            "movements": {m: dict(agg1[m])
                          for m in ("left", "through", "right", "uturn")},
            "data_note": "单进口模式：本段视频只覆盖该进口（机位在停止线"
                         "后方），流向按轨迹出口画面边判定（上=直行、左="
                         "左转、右=右转、下=掉头）；进口总量以计数线过线"
                         "数为准，本表为流向结构。用合并功能（/api/merge）"
                         "合并各进口片段后才是规划分析的正式输入。",
        }
        with open(os.path.join(out_dir, "approach_survey.json"), "w",
                  encoding="utf-8") as f:
            json.dump(frag, f, ensure_ascii=False, indent=2)
        summary["approach_mode"] = frag
    summary["params"] = {"weights": model_path, "conf": conf,
                         "imgsz": imgsz,
                         "line_y_ratio": line_ratio,
                         "line_x_ratio": line_x_ratio, "fps": fps,
                         "bucket_seconds": bucket_seconds,
                         "annotated_codec": annotated_codec,
                         "approach": approach,
                         "engine": "onnx+bytetrack-numpy",
                         "source_type": "stream" if stream else "file",
                         "frames_processed": frame_idx,
                         "duration_s": round(loop_end - t0, 1),
                         "stop_reason": stop_reason}
    with open(os.path.join(out_dir, "summary.json"), "w",
              encoding="utf-8") as f:
        json.dump(summary, f, ensure_ascii=False, indent=2)

    # Browser preview needs H.264: OpenCV's mp4v (FMP4) does not play in
    # Chromium <video>. Transcode with ffmpeg when available (P2 fix);
    # without ffmpeg the mp4v file is kept and the UI notes the limitation.
    # Runs AFTER the summary write (see above): if this step is slow or
    # the process is killed here, the summary already exists and the job
    # still lands as "stopped" instead of "error".
    if _transcode_h264(annotated_path):
        summary["params"]["annotated_codec"] = "h264"
        with open(os.path.join(out_dir, "summary.json"), "w",
                  encoding="utf-8") as f:
            json.dump(summary, f, ensure_ascii=False, indent=2)
    return summary


def main():
    ap = argparse.ArgumentParser(description="Product engine: ONNX + "
                                 "numpy ByteTrack vehicle counting")
    ap.add_argument("--source", required=True)
    ap.add_argument("--model", default="models/yolov8s-visdrone.onnx")
    ap.add_argument("--line", type=float, default=0.6)
    ap.add_argument("--line-x", type=float, default=None)
    ap.add_argument("--conf", type=float, default=0.35)
    ap.add_argument("--imgsz", type=int, default=640,
                    choices=[640, 1280],
                    help="input resolution: 640 standard, 1280 small-object boost")
    ap.add_argument("--out", default="outputs/run1")
    ap.add_argument("--classify", action="store_true")
    ap.add_argument("--center-x", type=float, default=0.5)
    ap.add_argument("--center-y", type=float, default=0.5)
    ap.add_argument("--approach", choices=sorted(APPROACH_ARG),
                    default=None,
                    help="single-approach mode: the video covers only "
                         "this approach (camera behind its stop line); "
                         "writes approach_survey.json for merging")
    ap.add_argument("--stream", action="store_true",
                    help="live-stream mode: --source is an RTSP/RTMP/HTTP "
                         "URL or cam:N; run for --duration wall seconds")
    ap.add_argument("--duration", type=float, default=60.0,
                    help="stream mode: wall seconds to run (5-3600)")
    ap.add_argument("--stop-file", default=None,
                    help="stop gracefully when this file appears "
                         "(server-side stop; file and stream alike)")
    ap.add_argument("--progress", default=None)
    args = ap.parse_args()
    if args.model == "models/yolov8s-visdrone.onnx" \
            and not os.path.exists(args.model):
        # Frozen bundle (or any other cwd): resolve the default model
        # against the product root instead of the working directory. P4.
        from paths import product_dir  # noqa: E402
        bundled = os.path.join(product_dir(), "models",
                               "yolov8s-visdrone.onnx")
        if os.path.exists(bundled):
            args.model = bundled
    summary = run_video(args.source, args.model, args.line, args.conf,
                        args.out, line_x_ratio=args.line_x,
                        classify=args.classify, center_x=args.center_x,
                        center_y=args.center_y, imgsz=args.imgsz,
                        progress_path=args.progress,
                        approach=args.approach, stream=args.stream,
                        duration_s=args.duration,
                        stop_file=args.stop_file)
    print(json.dumps(summary, ensure_ascii=False, indent=2), flush=True)


if __name__ == "__main__":
    main()
