# -*- coding: utf-8 -*-
"""
Product Web UI server — stdlib only (no Streamlit / no Flask).

Serves the DESIGNER OS workbench in ui/ and wraps the ONNX engine
(src/engine.py) as background jobs. Runs inside the packaged exe via
app.py; on this dev machine run:

    python src/server.py [--port 8931]

Endpoints
    GET  /                       -> ui/index.html
    GET  /ui/<file>              -> static UI assets
    GET  /api/sources            -> built-in test videos + uploads
    POST /api/upload             -> raw body + X-Filename header
    POST /api/run                -> enqueue one engine job (FIFO queue)
    POST /api/run/batch          -> enqueue several jobs at once
    POST /api/merge              -> merge approach surveys into one
                                    planning input (merged_survey.json)
    POST /api/job/<name>/delete  -> delete a finished/queued job + history
    POST /api/job/<name>/rerun   -> enqueue a copy of a past job
    POST /api/job/<name>/stop    -> stop a running/queued job (P3 live
                                    stream; writes a stop flag, engine
                                    exits gracefully and keeps its outputs)
    GET  /api/jobs               -> job history (persisted in jobs.json)
    GET  /api/job/<name>         -> summary.json
    GET  /api/job/<name>/progress -> progress.json (or {state})
    GET  /api/job/<name>/file/<fname> -> result file (Range-capable)
    POST /api/planning           -> planning analysis on survey_auto.json
                                    (or a merged_survey.json)
    POST /api/job/<name>/export   -> build report.xlsx / report.pdf (P3)
                                    from the job's outputs
"""

import collections
import json
import os
import re
import subprocess
import sys
import threading
import time
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import unquote, urlparse

from paths import is_frozen, product_dir  # noqa: E402 - src on sys.path (app.py inserts it; running src/server.py directly puts src first)

HERE = os.path.dirname(os.path.abspath(__file__))
# P4: frozen bundle -> product root is sys._MEIPASS (_internal, where
# PyInstaller 6 puts datas ui/models/data); dev -> parent of src/.
PRODUCT = product_dir()
UI_DIR = os.path.join(PRODUCT, "ui")
OUTPUTS = os.path.join(PRODUCT, "outputs")
UPLOADS = os.path.join(PRODUCT, "uploads")
def _resolve_test_videos():
    """Built-in test videos: prefer the copy bundled with the package
    (<PRODUCT>/test-videos, R14/P4: shipped inside the portable zip so
    the presets show READY on the user's machine), falling back to the
    dev-machine absolute path when the bundled copy is absent."""
    bundled = os.path.join(PRODUCT, "test-videos")
    _files = ("test1-intersection.mp4", "test2-highway.mp4",
              "test3-motorcycle-swarm.mp4")
    if all(os.path.exists(os.path.join(bundled, f)) for f in _files):
        return bundled
    return os.path.expanduser(
        "~/workspace/your_files/graduation-design/test-videos")


TEST_VIDEOS = _resolve_test_videos()

PRESETS = {
    "test1": {"file": "test1-intersection.mp4", "label": "测试1 · 路口俯拍",
              "desc": "1080p · 16 秒",
              "conf": 0.35, "line": 0.60, "line_x": 0.30, "classify": True,
              "center_x": 0.44, "center_y": 0.45, "imgsz": 640},
    "test2": {"file": "test2-highway.mp4", "label": "测试2 · 高速双向",
              "desc": "720p · 20 秒",
              "conf": 0.35, "line": 0.50, "line_x": None, "classify": False,
              "center_x": 0.5, "center_y": 0.5, "imgsz": 640},
    "test3": {"file": "test3-motorcycle-swarm.mp4", "label": "测试3 · 摩托洪流",
              "desc": "1080p · 8 秒",
              "conf": 0.30, "line": 0.55, "line_x": None, "classify": False,
              "center_x": 0.5, "center_y": 0.5, "imgsz": 640},
}

ALLOWED_FILES = {"annotated.mp4", "events.csv", "movements.csv",
                 "approach_movements.csv", "survey_auto.json",
                 "approach_survey.json", "merged_survey.json",
                 "summary.json", "planning.json", "planning.md",
                 "progress.json", "run.log", "report.xlsx", "report.pdf",
                 # P3.5 live feedback: paired frames of the same moment.
                 "live_raw.jpg", "live_annotated.jpg"}

APPROACHES = {"east": "东进口", "south": "南进口",
              "west": "西进口", "north": "北进口"}

# 1280 高清模式开关：只允许这两个档位；非法值由 _resolve_source
# 拦下（400），不让非法 imgsz 流到引擎命令行。
ALLOWED_IMGSZ = (640, 1280)

# -- 参数预设（P3）---------------------------------------------------
# 与 PRESETS（视频源预设）不同：参数预设只存检测参数组合，与视频源
# 无关，可套用到任意上传视频。内置 4 个；用户自定义的存入
# data/param_presets.json（文件即持久化，重启不丢）。
PARAM_PRESET_KEYS = ("conf", "line", "line_x", "classify", "center_x",
                     "center_y", "imgsz", "approach")

BUILTIN_PARAM_PRESETS = {
    "路口俯拍 640": {
        "desc": "标准路口俯拍：双线 + 流向分类",
        "params": {"conf": 0.35, "line": 0.60, "line_x": 0.30,
                   "classify": True, "center_x": 0.44, "center_y": 0.45,
                   "imgsz": 640, "approach": None}},
    "摩托增强 1280": {
        "desc": "小目标增强：1280 输入，远处摩托/小车检出提升，耗时约 3–4 倍",
        "params": {"conf": 0.30, "line": 0.55, "line_x": None,
                   "classify": False, "center_x": 0.5, "center_y": 0.5,
                   "imgsz": 1280, "approach": None}},
    "高速断面": {
        "desc": "高速双向断面：单横线计数，不开流向分类",
        "params": {"conf": 0.35, "line": 0.50, "line_x": None,
                   "classify": False, "center_x": 0.5, "center_y": 0.5,
                   "imgsz": 640, "approach": None}},
    "单进口 15 分钟": {
        "desc": "单进口机位示例（东进口）：套用后请按实际进口改 approach",
        "params": {"conf": 0.35, "line": 0.60, "line_x": None,
                   "classify": False, "center_x": 0.5, "center_y": 0.5,
                   "imgsz": 640, "approach": "east"}},
}

PARAM_PRESETS_PATH = os.path.join(PRODUCT, "data", "param_presets.json")
PRESET_NAME_RE = re.compile(r"^[\w\u4e00-\u9fa5][\w\u4e00-\u9fa5 \-.]{0,23}$")
PRESETS_LOCK = threading.Lock()


def _load_user_presets():
    """User presets from data/param_presets.json: {name: {params, desc}}."""
    data = _read_json(PARAM_PRESETS_PATH)
    if not isinstance(data, dict):
        return {}
    out = {}
    for name, spec in data.items():
        if isinstance(spec, dict) and isinstance(spec.get("params"), dict):
            out[name] = spec
    return out


def _save_user_presets(presets):
    tmp = PARAM_PRESETS_PATH + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(presets, f, ensure_ascii=False, indent=2)
    os.replace(tmp, PARAM_PRESETS_PATH)


def _all_param_presets():
    """Built-ins + user presets: {name: {desc, params, builtin}}."""
    out = {}
    for name, spec in BUILTIN_PARAM_PRESETS.items():
        out[name] = {"desc": spec["desc"], "params": dict(spec["params"]),
                     "builtin": True}
    for name, spec in _load_user_presets().items():
        if name in out:  # user file must never shadow a built-in
            continue
        out[name] = {"desc": spec.get("desc", "自定义预设"),
                     "params": dict(spec["params"]), "builtin": False}
    return out


def _get_param_preset(name):
    """Params dict for a preset name, or None."""
    spec = _all_param_presets().get(name)
    return dict(spec["params"]) if spec else None


def _validate_preset_params(p):
    """Validate + normalise a preset params dict.
    Returns (normalised_dict, error_str)."""
    if not isinstance(p, dict):
        return None, "params 须为对象"
    unknown = sorted(set(p) - set(PARAM_PRESET_KEYS))
    if unknown:
        return None, "未知参数: " + "、".join(unknown)
    for req in ("conf", "line", "imgsz"):
        if req not in p or p[req] is None:
            return None, f"缺少必填参数: {req}"
    out = {}
    try:
        out["conf"] = float(p["conf"])
        out["line"] = float(p["line"])
        out["line_x"] = (None if p.get("line_x") is None
                         else float(p["line_x"]))
        out["classify"] = bool(p.get("classify", False))
        out["center_x"] = float(p.get("center_x", 0.5))
        out["center_y"] = float(p.get("center_y", 0.5))
        out["imgsz"] = int(p["imgsz"])
    except (TypeError, ValueError):
        return None, ("参数类型错误：conf/line/line_x/center 须为数字，"
                      "imgsz 须为整数")
    if not 0.05 <= out["conf"] <= 0.95:
        return None, "conf 须在 0.05–0.95 之间"
    if not 0.05 <= out["line"] <= 0.95:
        return None, "line 须在 0.05–0.95 之间"
    if out["line_x"] is not None and not 0.05 <= out["line_x"] <= 0.95:
        return None, "line_x 须在 0.05–0.95 之间（或留空）"
    if not 0.0 <= out["center_x"] <= 1.0:
        return None, "center_x 须在 0–1 之间"
    if not 0.0 <= out["center_y"] <= 1.0:
        return None, "center_y 须在 0–1 之间"
    if out["imgsz"] not in ALLOWED_IMGSZ:
        return None, "imgsz 须为 640 或 1280"
    ap = p.get("approach")
    if ap is not None and ap not in APPROACHES:
        return None, "approach 须为 east/south/west/north 之一（或留空）"
    out["approach"] = ap
    return out, None

JOBS = {}          # name -> registry entry (persisted to jobs.json)
JOBS_LOCK = threading.Lock()
REGISTRY_PATH = os.path.join(OUTPUTS, "jobs.json")
QUEUE = collections.deque()       # names waiting for the single worker
QUEUE_COND = threading.Condition()
WORKER_STARTED = False
# Live engine processes, for stop: name -> subprocess.Popen.
RUNNING_PROCS = {}
RUNNING_PROCS_LOCK = threading.Lock()


def _load_registry():
    """Load persisted job history; import legacy output dirs (R4 and
    earlier, which predate the registry) as done jobs."""
    global JOBS
    reg = _read_json(REGISTRY_PATH) or {}
    if not isinstance(reg, dict):
        reg = {}
    JOBS = {k: v for k, v in reg.items() if isinstance(v, dict)}
    if os.path.isdir(OUTPUTS):
        for name in sorted(os.listdir(OUTPUTS)):
            d = os.path.join(OUTPUTS, name)
            if not os.path.isdir(d) or name in JOBS:
                continue
            s = _read_json(os.path.join(d, "summary.json"))
            if s is None:
                continue
            mtime = os.path.getmtime(os.path.join(d, "summary.json"))
            JOBS[name] = {
                "name": name, "state": "done",
                "source_label": "旧任务", "source_file": "",
                "params": (s.get("params") or {}),
                "queued_at": mtime, "started_at": mtime,
                "finished_at": mtime, "total": s.get("total"),
                "by_type": s.get("by_type"), "duration_s": None,
                "legacy": True}
    # A restart mid-run must not leave a phantom "running" job.
    for entry in JOBS.values():
        if entry.get("state") in ("running", "queued"):
            entry["state"] = "interrupted"
            entry["finished_at"] = entry.get("finished_at") or time.time()
    _save_registry()


def _save_registry():
    tmp = REGISTRY_PATH + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(JOBS, f, ensure_ascii=False, indent=2)
    os.replace(tmp, REGISTRY_PATH)


def _update_job(name, **fields):
    with JOBS_LOCK:
        entry = JOBS.setdefault(name, {"name": name})
        entry.update(fields)
        _save_registry()


def _enqueue(name, params, source_label, source_file):
    """Register + queue a job; returns its queue position (1-based)."""
    now = time.time()
    _update_job(name, state="queued", params=params,
                source_label=source_label, source_file=source_file,
                queued_at=now, started_at=None, finished_at=None,
                total=None, by_type=None, duration_s=None)
    with QUEUE_COND:
        QUEUE.append(name)
        pos = len(QUEUE)
        QUEUE_COND.notify()
    return pos


def _queue_worker():
    """Single worker: runs queued jobs strictly FIFO so two engine
    processes never compete for the CPU (R3/R4 saw 4x slowdowns)."""
    while True:
        with QUEUE_COND:
            while not QUEUE:
                QUEUE_COND.wait()
            name = QUEUE.popleft()
        entry = JOBS.get(name)
        if entry is None:
            continue
        _run_engine(name, entry.get("params") or {})


def _ensure_worker():
    global WORKER_STARTED
    if not WORKER_STARTED:
        WORKER_STARTED = True
        t = threading.Thread(target=_queue_worker, daemon=True)
        t.start()


def _safe_name(name):
    name = re.sub(r"[^A-Za-z0-9_\-\.]", "_", str(name or "")).strip("._")
    return name or "run"


def _job_dir(name):
    return os.path.join(OUTPUTS, _safe_name(name))


def _read_json(path):
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return None


def _run_engine(name, params):
    out_dir = _job_dir(name)
    os.makedirs(out_dir, exist_ok=True)
    _update_job(name, state="running", started_at=time.time())
    progress_path = os.path.join(out_dir, "progress.json")
    engine_args = ["--source", params["source"],
                   "--model", os.path.join(PRODUCT, "models",
                                           "yolov8s-visdrone.onnx"),
                   "--line", str(params.get("line", 0.6)),
                   "--conf", str(params.get("conf", 0.35)),
                   "--imgsz", str(params.get("imgsz", 640)),
                   "--out", out_dir, "--progress", progress_path]
    if is_frozen():
        # Frozen exe: no engine.py file exists on disk in the bundle,
        # so the engine runs as a subcommand of the same binary
        # (app.py --engine ...). P4.
        cmd = [sys.executable, "--engine"] + engine_args
    else:
        cmd = [sys.executable, os.path.join(HERE, "engine.py")] + engine_args
    if params.get("line_x") is not None:
        cmd += ["--line-x", str(params["line_x"])]
    if params.get("classify"):
        cmd += ["--classify", "--center-x", str(params.get("center_x", 0.5)),
                "--center-y", str(params.get("center_y", 0.5))]
    if params.get("approach") in APPROACHES:
        cmd += ["--approach", params["approach"]]
    # R14: --stop-file is passed for file jobs too, not just streams:
    # the engine now honours it in both modes, so a stopped file job
    # lands as "stopped" with a partial summary instead of running to
    # completion (or being terminated into "error" by the 180 s watcher).
    stop_flag = os.path.join(out_dir, "stop.flag")
    if params.get("source_type") == "stream":
        cmd += ["--stream",
                "--duration", str(params.get("duration", 60))]
    cmd += ["--stop-file", stop_flag]
    log_path = os.path.join(out_dir, "run.log")
    started = time.time()
    try:
        with open(log_path, "w", encoding="utf-8") as log:
            proc = subprocess.Popen(cmd, stdout=log,
                                    stderr=subprocess.STDOUT, cwd=PRODUCT)
            with RUNNING_PROCS_LOCK:
                RUNNING_PROCS[name] = proc
            try:
                rc = proc.wait(timeout=3600)
            except subprocess.TimeoutExpired:
                proc.kill()
                rc = proc.wait()
            finally:
                with RUNNING_PROCS_LOCK:
                    RUNNING_PROCS.pop(name, None)
        # A stop flag + a written summary means the engine exited
        # gracefully on request: state "stopped", not "error".
        s_pre = _read_json(os.path.join(out_dir, "summary.json"))
        if stop_flag and os.path.exists(stop_flag) and s_pre is not None:
            state = "stopped"
        else:
            state = "done" if rc == 0 else "error"
    except Exception as exc:  # noqa: BLE001 - surfaced in job state
        with open(log_path, "a", encoding="utf-8") as log:
            log.write(f"\n[server] engine failed to start: {exc}\n")
        state = "error"
    s = _read_json(os.path.join(out_dir, "summary.json"))
    _update_job(name, state=state, finished_at=time.time(),
                duration_s=round(time.time() - started, 1),
                total=(s.get("total") if s else None),
                by_type=(s.get("by_type") if s else None))


class Handler(SimpleHTTPRequestHandler):
    protocol_version = "HTTP/1.1"  # keep-alive; <video> needs it for Range

    def log_message(self, fmt, *args):  # keep console quiet
        sys.stderr.write("[ui] %s - %s\n" % (self.address_string(),
                                              fmt % args))

    # -- helpers ------------------------------------------------------
    def _send_json(self, obj, status=200):
        body = json.dumps(obj, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _read_json_body(self):
        n = int(self.headers.get("Content-Length") or 0)
        raw = self.rfile.read(n) if n else b"{}"
        try:
            return json.loads(raw.decode("utf-8"))
        except ValueError:
            return {}

    # -- routing ------------------------------------------------------
    def do_GET(self):
        parsed = urlparse(self.path)
        path = unquote(parsed.path)
        if path == "/":
            # Serve the workbench. NOTE: must set self.directory here
            # too — SimpleHTTPRequestHandler defaults to os.getcwd(),
            # which is the product dir only when the dev server happens
            # to be started from there; the frozen exe starts from an
            # arbitrary cwd and served 404 here (found by R11).
            self.directory = PRODUCT
            self.path = "/ui/index.html"
            return super().do_GET()
        if path == "/api/jobs":
            return self._api_jobs()
        if path == "/api/sources":
            return self._api_sources()
        if path == "/api/presets":
            return self._api_presets_list()
        m = re.match(r"^/api/job/([^/]+)$", path)
        if m:
            return self._api_job(m.group(1))
        m = re.match(r"^/api/job/([^/]+)/progress$", path)
        if m:
            return self._api_progress(m.group(1))
        m = re.match(r"^/api/job/([^/]+)/original$", path)
        if m:
            return self._api_original(m.group(1))
        m = re.match(r"^/api/job/([^/]+)/file/([^/]+)$", path)
        if m:
            return self._api_file(m.group(1), m.group(2))
        if path.startswith("/ui/"):
            self.directory = PRODUCT
            return super().do_GET()
        self.send_error(404, "not found")

    def do_POST(self):
        parsed = urlparse(self.path)
        path = unquote(parsed.path)
        if path == "/api/upload":
            return self._api_upload()
        if path == "/api/run":
            return self._api_run()
        if path == "/api/run/batch":
            return self._api_run_batch()
        if path == "/api/planning":
            return self._api_planning()
        if path == "/api/merge":
            return self._api_merge()
        if path == "/api/presets":
            return self._api_presets_save()
        m = re.match(r"^/api/job/([^/]+)/(delete|rerun|export|stop)$", path)
        if m:
            if m.group(2) == "export":
                return self._api_export(m.group(1))
            if m.group(2) == "stop":
                return self._api_stop(m.group(1))
            return (self._api_delete(m.group(1)) if m.group(2) == "delete"
                    else self._api_rerun(m.group(1)))
        self.send_error(404, "not found")

    def do_DELETE(self):
        parsed = urlparse(self.path)
        path = unquote(parsed.path)
        m = re.match(r"^/api/presets/(.+)$", path)
        if m:
            return self._api_presets_delete(m.group(1))
        self.send_error(404, "not found")

    def _api_presets_list(self):
        """GET /api/presets -> built-ins first, then user presets."""
        presets = [{"name": n, **spec}
                   for n, spec in _all_param_presets().items()]
        presets.sort(key=lambda p: (not p["builtin"], p["name"]))
        self._send_json({"presets": presets})

    def _api_presets_save(self):
        """POST /api/presets {"name": ..., "params": {...}}.
        Built-in names and existing user names cannot be overwritten;
        delete the user preset first."""
        body = self._read_json_body()
        name = str(body.get("name") or "").strip()
        if not PRESET_NAME_RE.match(name):
            return self._send_json(
                {"error": "预设名称须为 1–24 字：中文/字母/数字/空格/-/_/."},
                400)
        if name in BUILTIN_PARAM_PRESETS:
            return self._send_json({"error": f"内置预设「{name}」不可覆盖"},
                                   409)
        params, err = _validate_preset_params(body.get("params"))
        if err:
            return self._send_json({"error": err}, 400)
        with PRESETS_LOCK:
            user = _load_user_presets()
            if name in user:
                return self._send_json(
                    {"error": f"预设「{name}」已存在：请先删除再保存"}, 409)
            user[name] = {"params": params,
                          "desc": str(body.get("desc") or "自定义预设")[:80]}
            _save_user_presets(user)
        self._send_json({"ok": True, "name": name, "params": params,
                         "builtin": False})

    def _api_presets_delete(self, name):
        """DELETE /api/presets/<name>: user presets only."""
        name = unquote(name)
        if name in BUILTIN_PARAM_PRESETS:
            return self._send_json({"error": f"内置预设「{name}」不可删除"},
                                   400)
        with PRESETS_LOCK:
            user = _load_user_presets()
            if name not in user:
                return self._send_json({"error": f"预设「{name}」不存在"},
                                       404)
            del user[name]
            _save_user_presets(user)
        self._send_json({"ok": True, "name": name})

    def _api_export(self, name):
        """Build report.xlsx / report.pdf for a finished job (P3).
        Body: {"format": "xlsx" | "pdf"}. Rebuilds on every call so the
        report always reflects the current outputs; cheap (<2s).
        """
        body = self._read_json_body()
        fmt = body.get("format", "xlsx")
        if fmt not in ("xlsx", "pdf"):
            return self._send_json({"error": "format 须为 xlsx 或 pdf"}, 400)
        job_dir = _job_dir(_safe_name(name))
        if not os.path.isdir(job_dir):
            return self._send_json({"error": f"任务 {name} 不存在"}, 404)
        sys.path.insert(0, HERE)
        try:
            from report_export import export_report  # noqa: E402
            path = export_report(job_dir, fmt)
        except Exception as exc:  # noqa: BLE001
            return self._send_json({"error": f"报告生成失败：{exc}"}, 500)
        fname = os.path.basename(path)
        self._send_json({"ok": True, "file": fname,
                         "url": f"/api/job/{_safe_name(name)}/file/{fname}",
                         "size": os.path.getsize(path)})

    # -- api ----------------------------------------------------------
    def _api_sources(self):
        items = []
        for key, p in PRESETS.items():
            fp = os.path.join(TEST_VIDEOS, p["file"])
            items.append({**p, "key": key, "exists": os.path.exists(fp),
                          "size_mb": round(os.path.getsize(fp) / 1e6, 1)
                          if os.path.exists(fp) else 0})
        ups = []
        if os.path.isdir(UPLOADS):
            for fn in sorted(os.listdir(UPLOADS)):
                fp = os.path.join(UPLOADS, fn)
                if os.path.isfile(fp):
                    ups.append({"file": fn, "size_mb":
                                round(os.path.getsize(fp) / 1e6, 1)})
        self._send_json({"presets": items, "uploads": ups})

    def _api_jobs(self):
        with JOBS_LOCK:
            queued = list(QUEUE)
            entries = [dict(v) for v in JOBS.values()]
        for e in entries:
            if e.get("state") == "queued" and e["name"] in queued:
                e["queue_position"] = queued.index(e["name"]) + 1
            if not e.get("has_summary"):
                s = _read_json(os.path.join(_job_dir(e["name"]),
                                            "summary.json"))
                e["has_summary"] = bool(s)
                e["has_video"] = os.path.exists(
                    os.path.join(_job_dir(e["name"]), "annotated.mp4"))
                e["has_approach"] = os.path.exists(
                    os.path.join(_job_dir(e["name"]),
                                 "approach_survey.json"))
                if s and e.get("total") is None:
                    e["total"] = s.get("total")
        entries.sort(key=lambda e: e.get("queued_at") or 0, reverse=True)
        self._send_json({"jobs": entries,
                         "queue": queued,
                         "running": next((e["name"] for e in entries
                                          if e.get("state") == "running"),
                                         None)})

    def _api_job(self, name):
        s = _read_json(os.path.join(_job_dir(name), "summary.json"))
        if s is None:
            return self._send_json({"error": "no summary yet"}, 404)
        self._send_json(s)

    def _api_progress(self, name):
        p = _read_json(os.path.join(_job_dir(name), "progress.json"))
        with JOBS_LOCK:
            live = dict(JOBS.get(name) or {})
            queued = list(QUEUE)
        state = live.get("state", "unknown")
        if p is None:
            return self._send_json(
                {"state": state,
                 "queue_position": (queued.index(name) + 1
                                    if name in queued else None),
                 "frame": 0, "total_frames": 0, "pct": 0, "done": False})
        p["state"] = state
        if name in queued:
            p["queue_position"] = queued.index(name) + 1
        self._send_json(p)

    def _send_file(self, fp):
        """Serve one file with Range support (206/Content-Range).

        Shared by result files (_api_file) and the job's original
        video (_api_original, P3.5): <video> needs Range to seek and
        to play at all in Chromium (P2 acceptance caught this).
        """
        import mimetypes
        size = os.path.getsize(fp)
        ctype, _ = mimetypes.guess_type(fp)
        ctype = ctype or "application/octet-stream"
        start, end = 0, size - 1
        status = 200
        range_hdr = self.headers.get("Range")
        if range_hdr:
            m = re.match(r"bytes=(\d*)-(\d*)$", range_hdr.strip())
            if m:
                s, e = m.group(1), m.group(2)
                if s:
                    start = int(s)
                if e:
                    end = min(int(e), size - 1)
                if 0 <= start <= end < size:
                    status = 206
                else:
                    self.send_response(416)
                    self.send_header("Content-Range", f"bytes */{size}")
                    self.send_header("Content-Length", "0")
                    self.end_headers()
                    return
        length = end - start + 1
        self.send_response(status)
        self.send_header("Content-Type", ctype)
        self.send_header("Accept-Ranges", "bytes")
        self.send_header("Content-Length", str(length))
        if status == 206:
            self.send_header("Content-Range",
                             f"bytes {start}-{end}/{size}")
        self.end_headers()
        if self.command == "HEAD":
            return
        with open(fp, "rb") as f:
            f.seek(start)
            remaining = length
            while remaining > 0:
                chunk = f.read(min(1 << 20, remaining))
                if not chunk:
                    break
                self.wfile.write(chunk)
                remaining -= len(chunk)

    def _api_file(self, name, fname):
        if fname not in ALLOWED_FILES:
            return self.send_error(403, "file not allowed")
        fp = os.path.join(_job_dir(name), fname)
        if not os.path.isfile(fp):
            return self.send_error(404, "file not found")
        return self._send_file(fp)

    def _api_original(self, name):
        """GET /api/job/<name>/original — the job's source video, so
        the result page can play original + annotated side by side in
        sync (P3.5). Stream jobs and merged jobs have no source file.
        """
        name = _safe_name(name)
        with JOBS_LOCK:
            entry = dict(JOBS.get(name) or {})
        params = entry.get("params") or {}
        if params.get("source_type") == "stream":
            return self._send_json({"error": "实时流任务无原视频文件"}, 404)
        src = params.get("source")
        if not src or not os.path.isfile(src):
            return self._send_json({"error": "原视频文件不存在"}, 404)
        return self._send_file(src)

    def _api_upload(self):
        fn = _safe_name(self.headers.get("X-Filename") or "upload.mp4")
        if not fn.lower().endswith((".mp4", ".avi", ".mov", ".mkv")):
            return self._send_json({"error": "仅支持 mp4/avi/mov/mkv 视频"},
                                   400)
        n = int(self.headers.get("Content-Length") or 0)
        os.makedirs(UPLOADS, exist_ok=True)
        fp = os.path.join(UPLOADS, fn)
        with open(fp, "wb") as f:
            remaining = n
            while remaining > 0:
                chunk = self.rfile.read(min(1 << 20, remaining))
                if not chunk:
                    break
                f.write(chunk)
                remaining -= len(chunk)
        self._send_json({"ok": True, "file": fn,
                         "size_mb": round(os.path.getsize(fp) / 1e6, 1)})

    def _resolve_source(self, body):
        """Shared by /api/run and /api/run/batch: returns
        (params, source_label, error). params includes 'source'."""
        src_key = body.get("source_key")
        # 参数预设（P3）：先套预设，再让 body 里的显式参数覆盖；
        # 优先级：视频源默认 < 参数预设 < 本次显式参数。
        preset_params = None
        preset_name = body.get("preset")
        if preset_name:
            preset_params = _get_param_preset(preset_name)
            if preset_params is None:
                return None, "", f"参数预设不存在: {preset_name}"
        override = {k: v for k, v in body.items()
                    if k in ("conf", "line", "line_x", "classify",
                             "center_x", "center_y", "imgsz", "approach")}
        if src_key in PRESETS:
            base = dict(PRESETS[src_key])
            if preset_params:
                base.update(preset_params)
            base.update(override)
            source = os.path.join(TEST_VIDEOS, base["file"])
            label = base["label"]
        elif body.get("upload"):
            source = os.path.join(UPLOADS,
                                  _safe_name(body["upload"]))
            label = "UPLOAD · " + _safe_name(body["upload"])
            base = dict(preset_params) if preset_params else {}
            base.setdefault("conf", body.get("conf", 0.35))
            base.setdefault("line", body.get("line", 0.6))
            base.setdefault("line_x", body.get("line_x"))
            base.setdefault("classify", bool(body.get("classify")))
            base.setdefault("center_x", body.get("center_x", 0.5))
            base.setdefault("center_y", body.get("center_y", 0.5))
            base.setdefault("imgsz", body.get("imgsz", 640))
            base.setdefault("approach", body.get("approach"))
            base.update(override)
        elif body.get("stream"):
            # Live stream (P3): RTSP/RTMP/HTTP(S) URL, or cam:N for a
            # local camera. No file to check; the engine fails the job
            # if the URL cannot be opened.
            raw = body["stream"]
            if isinstance(raw, dict):
                url = str(raw.get("url") or "")
                dur = raw.get("duration", body.get("duration", 60))
            else:
                url = str(raw or "")
                dur = body.get("duration", 60)
            url = url.strip()
            parsed = urlparse(url)
            if parsed.scheme in ("rtsp", "rtmp", "http", "https") \
                    and parsed.netloc:
                host = parsed.hostname or parsed.netloc
                label = parsed.scheme.upper() + " · " + host
            elif re.fullmatch(r"cam:\d+", url):
                label = "CAMERA · " + url
            else:
                return None, "", ("流地址须为 rtsp://、rtmp://、http(s):// "
                                  "地址或 cam:N 本地摄像头")
            try:
                dur = float(dur)
            except (TypeError, ValueError):
                return None, "", "录制时长须为数字（秒）"
            if not 5 <= dur <= 3600:
                return None, "", "录制时长须为 5–3600 秒"
            source = url
            base = dict(preset_params) if preset_params else {}
            base.setdefault("conf", body.get("conf", 0.35))
            base.setdefault("line", body.get("line", 0.6))
            base.setdefault("line_x", body.get("line_x"))
            base.setdefault("classify", bool(body.get("classify")))
            base.setdefault("center_x", body.get("center_x", 0.5))
            base.setdefault("center_y", body.get("center_y", 0.5))
            base.setdefault("imgsz", body.get("imgsz", 640))
            base.setdefault("approach", body.get("approach"))
            base.update(override)
            base["source_type"] = "stream"
            base["duration"] = dur
        else:
            return None, "", "未指定视频源"
        if base.get("approach") not in (None, *APPROACHES):
            return None, "", "approach 须为 east/south/west/north 之一"
        try:
            imgsz = int(base.get("imgsz", 640))
        except (TypeError, ValueError):
            return None, "", "imgsz 须为 640 或 1280"
        if imgsz not in ALLOWED_IMGSZ:
            return None, "", "imgsz 须为 640 或 1280"
        base["imgsz"] = imgsz
        if preset_name:
            base["preset"] = preset_name
        if base.get("source_type") != "stream" \
                and not os.path.isfile(source):
            return None, "", "视频文件不存在: %s" % source
        base["source"] = source
        return base, label, None

    def _unique_name(self, wanted):
        name = _safe_name(wanted or
                          ("run-" + time.strftime("%m%d-%H%M%S")))
        if name in JOBS or os.path.exists(_job_dir(name)):
            name = name + "-" + time.strftime("%H%M%S")
        n = 1
        while name in JOBS or os.path.exists(_job_dir(name)):
            n += 1
            name = "%s-%d" % (_safe_name(wanted or "run"), n)
        return name

    def _api_run(self):
        body = self._read_json_body()
        params, label, err = self._resolve_source(body)
        if err:
            return self._send_json({"error": err}, 400)
        _ensure_worker()
        name = self._unique_name(body.get("name"))
        pos = _enqueue(name, params, label,
                       os.path.basename(params["source"]))
        self._send_json({"ok": True, "name": name, "state": "queued",
                         "queue_position": pos})

    def _api_run_batch(self):
        """Enqueue several jobs at once (P3 batch queue). Body:
        {"jobs": [ {source_key|upload, ...params, name?}, ... ]}."""
        body = self._read_json_body()
        items = body.get("jobs")
        if not isinstance(items, list) or not items:
            return self._send_json({"error": "jobs 须为非空列表"}, 400)
        if len(items) > 10:
            return self._send_json({"error": "单次批量最多 10 个任务"}, 400)
        resolved = []
        for it in items:
            if not isinstance(it, dict):
                return self._send_json({"error": "任务须为对象"}, 400)
            params, label, err = self._resolve_source(it)
            if err:
                return self._send_json({"error": err}, 400)
            resolved.append((it, params, label))
        _ensure_worker()
        out = []
        for it, params, label in resolved:
            name = self._unique_name(it.get("name"))
            pos = _enqueue(name, params, label,
                           os.path.basename(params["source"]))
            out.append({"name": name, "state": "queued",
                        "queue_position": pos})
        self._send_json({"ok": True, "jobs": out})

    def _api_stop(self, name):
        """POST /api/job/<name>/stop — stop a queued or running job.

        Queued: drop from the queue, mark stopped. Running: touch the
        stop flag the engine polls; the engine finishes its current
        outputs and exits 0, so the job lands as "stopped" with a
        summary instead of "error". A watcher terminates the engine
        if it has not exited 180s after the flag (e.g. stuck on a dead
        stream read). 180s, not 20s: the engine now writes its summary
        before the H.264 transcode (P5), but the transcode of a
        near-full clip still takes a while, and 20s was short enough
        to kill a healthy engine mid-transcode (r15-livefix). This
        closes the R5–R9 legacy item "running jobs cannot be
        cancelled".
        """
        name = _safe_name(name)
        with JOBS_LOCK:
            entry = dict(JOBS.get(name) or {})
        if not entry:
            return self._send_json({"error": "任务不存在"}, 404)
        state = entry.get("state")
        if state == "queued":
            with QUEUE_COND:
                if name in QUEUE:
                    QUEUE.remove(name)
            _update_job(name, state="stopped", finished_at=time.time())
            return self._send_json({"ok": True, "name": name,
                                    "state": "stopped"})
        if state != "running":
            return self._send_json(
                {"error": f"任务已是 {state}，无需停止"}, 409)
        flag = os.path.join(_job_dir(name), "stop.flag")
        try:
            with open(flag, "w", encoding="utf-8") as f:
                f.write("stop requested at %s\n" % time.time())
        except OSError as exc:
            return self._send_json({"error": f"写停止标志失败：{exc}"}, 500)
        _update_job(name, stop_requested=True)
        with RUNNING_PROCS_LOCK:
            proc = RUNNING_PROCS.get(name)

        def _watcher():
            time.sleep(180)
            if proc is not None and proc.poll() is None:
                try:
                    proc.terminate()
                    proc.wait(timeout=5)
                except Exception:  # noqa: BLE001 - last-resort kill
                    try:
                        proc.kill()
                    except Exception:  # noqa: BLE001
                        pass
        threading.Thread(target=_watcher, daemon=True).start()
        self._send_json({"ok": True, "name": name, "state": "stopping"})

    def _api_delete(self, name):
        name = _safe_name(name)
        with JOBS_LOCK:
            entry = JOBS.get(name)
        if entry is None:
            return self._send_json({"error": "任务不存在"}, 404)
        if entry.get("state") in ("running",):
            return self._send_json(
                {"error": "运行中的任务不能删除，请先点停止"}, 409)
        with QUEUE_COND:
            if name in QUEUE:
                QUEUE.remove(name)
        import shutil
        d = _job_dir(name)
        if os.path.isdir(d):
            shutil.rmtree(d)
        with JOBS_LOCK:
            JOBS.pop(name, None)
            _save_registry()
        self._send_json({"ok": True, "name": name})

    def _api_rerun(self, name):
        name = _safe_name(name)
        with JOBS_LOCK:
            entry = dict(JOBS.get(name) or {})
        if not entry:
            return self._send_json({"error": "任务不存在"}, 404)
        params = entry.get("params") or {}
        if params.get("source_type") != "stream" \
                and (not params.get("source")
                     or not os.path.isfile(params["source"])):
            return self._send_json({"error": "原视频源已不存在"}, 400)
        _ensure_worker()
        new_name = self._unique_name(name + "-rerun")
        pos = _enqueue(new_name, params, entry.get("source_label", ""),
                       entry.get("source_file", ""))
        self._send_json({"ok": True, "name": new_name, "state": "queued",
                         "queue_position": pos})

    def _api_merge(self):
        """Merge approach fragments into one planning input (P3).
        Body: {"jobs": [names with approach_survey.json], "name": opt}.
        Writes outputs/merged-<name>/merged_survey.json and registers
        it as a done pseudo-job so planning + file download work on it.
        Geometry comes from data/geometry_template.json (demo values,
        must be replaced with field-measured geometry for formal use).
        """
        body = self._read_json_body()
        names = body.get("jobs")
        if not isinstance(names, list) or not names:
            return self._send_json({"error": "jobs 须为非空列表"}, 400)
        frags = {}
        for raw in names:
            name = _safe_name(raw)
            frag = _read_json(os.path.join(_job_dir(name),
                                           "approach_survey.json"))
            if frag is None:
                return self._send_json(
                    {"error": f"任务 {name} 无 approach_survey.json："
                              "请先以单进口模式运行"}, 400)
            if frag["approach"] in frags:
                return self._send_json(
                    {"error": f"重复进口：{frag['approach']} 出现了两次"},
                    400)
            frags[frag["approach"]] = frag
        tpl = _read_json(os.path.join(PRODUCT, "data",
                                      "geometry_template.json"))
        if tpl is None:
            return self._send_json({"error": "几何模板缺失"}, 500)
        sys.path.insert(0, HERE)
        from merge_surveys import merge_fragments  # noqa: E402
        merged, notes = merge_fragments(frags, tpl)
        mname = self._unique_name(body.get("name") or
                                  ("merged-" + time.strftime("%m%d-%H%M")))
        out_dir = _job_dir(mname)
        os.makedirs(out_dir, exist_ok=True)
        with open(os.path.join(out_dir, "merged_survey.json"), "w",
                  encoding="utf-8") as f:
            json.dump(merged, f, ensure_ascii=False, indent=2)
        total = sum(sum(sum(mv.get("flow_by_type", {}).values())
                        for mv in ap["movements"].values())
                    for ap in merged["approaches"])
        _update_job(mname, state="done", params={},
                    source_label="合并 · " + "、".join(sorted(frags)),
                    source_file="", queued_at=time.time(),
                    started_at=time.time(), finished_at=time.time(),
                    total=round(total, 1), by_type=None, duration_s=None,
                    merged_from=sorted(frags), merged=True)
        self._send_json({"ok": True, "name": mname, "merged": merged,
                         "notes": notes})

    def _api_planning(self):
        body = self._read_json_body()
        name = _safe_name(body.get("job") or "")
        job_dir = _job_dir(name)
        survey = _read_json(os.path.join(job_dir, "survey_auto.json"))
        merged = survey is None
        if merged:
            survey = _read_json(os.path.join(job_dir,
                                             "merged_survey.json"))
        if survey is None:
            return self._send_json(
                {"error": "该任务无 survey_auto.json / merged_survey.json："
                          "请先以流向分类或单进口模式运行"}, 400)
        sys.path.insert(0, HERE)
        from planning import IntersectionAnalysis, render_report  # noqa: E402
        lanes = int(body.get("lanes", 1))
        sat = float(body.get("sat_flow_per_lane", 1650))
        cycle = float(body.get("cycle_current",
                               survey.get("cycle_current", 90)))
        greens = [float(g) for g in body.get("greens", [40, 40])]
        phases = [{"current_green": g} for g in greens]
        approaches = []
        for ap in survey["approaches"]:
            movements = {}
            for mv, spec in ap["movements"].items():
                if merged:
                    # Merged survey carries template geometry per
                    # movement (lanes / sat flow / phase); honour it.
                    movements[mv] = {
                        "flow_by_type": spec["flow_by_type"],
                        "sat_flow_per_lane": spec.get("sat_flow_per_lane",
                                                      sat),
                        "lanes": spec.get("lanes", lanes),
                        "phase": spec.get("phase", 0)}
                else:
                    movements[mv] = {
                        "flow_by_type": spec["flow_by_type"],
                        "sat_flow_per_lane": sat, "lanes": lanes,
                        "phase": 0 if ap["name"] in ("东", "西", "东进口",
                                                     "西进口")
                        else 1}
            approaches.append({"name": ap["name"], "movements": movements})
        if merged:
            data = {"intersection": survey.get("intersection", "演示路口"),
                    "period": survey.get("period", ""),
                    "data_note": survey.get("data_note", ""),
                    "cycle_current": survey.get("cycle_current", cycle),
                    "lost_time_per_phase": survey.get("lost_time_per_phase",
                                                      3.0),
                    "phases": survey.get("phases") or phases,
                    "approaches": approaches}
        else:
            data = {"intersection": body.get("intersection", "演示路口"),
                    "period": "演示片段（非小时流量）",
                    "data_note": "演示数据：流量为视频片段内的原始计数，未折算为"
                                 "小时流量，仅演示分析流程；正式报告须用完整高峰"
                                 "小时调查数据并按实地几何参数复核。",
                    "cycle_current": cycle, "phases": phases,
                    "approaches": approaches}
        result = IntersectionAnalysis(data).run()
        report_md = render_report(result)
        with open(os.path.join(_job_dir(name), "planning.json"), "w",
                  encoding="utf-8") as f:
            json.dump(result, f, ensure_ascii=False, indent=2)
        with open(os.path.join(_job_dir(name), "planning.md"), "w",
                  encoding="utf-8") as f:
            f.write(report_md)
        self._send_json({"ok": True, "result": result,
                         "report_md": report_md})


def start_server(port=8931, host="127.0.0.1"):
    """Start the product HTTP server and return it (R18, desktop shell).

    The caller owns the serving thread: call ``srv.serve_forever()`` on
    any thread, ``srv.shutdown()`` to stop. Port 0 lets the OS pick a
    free port; read it back from ``srv.server_address[1]``.
    """
    os.makedirs(OUTPUTS, exist_ok=True)
    os.makedirs(UPLOADS, exist_ok=True)
    _load_registry()
    _ensure_worker()
    srv = ThreadingHTTPServer((host, port), Handler)
    print(f"[ui] serving product UI at http://{host}:{srv.server_address[1]}/",
          flush=True)
    return srv


def main():
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=8931)
    ap.add_argument("--host", default="127.0.0.1")
    args = ap.parse_args()
    srv = start_server(port=args.port, host=args.host)
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
