/* Product workbench — vanilla JS, no framework. */
const $ = (id) => document.getElementById(id);
const state = { sourceKey: "test1", upload: null, job: null, summary: null,
                pollTimer: null, mergeSel: new Set(), paramPresets: {},
                lastLiveFrame: null, lastKpis: null, liveVideoFor: null };

const APPROACH_LABEL = { east: "东进口", south: "南进口",
                         west: "西进口", north: "北进口" };
// R21: display order for the per-approach blocks (E/S/W/N).
const APPROACH_ORDER = ["东进口", "南进口", "西进口", "北进口"];

const STATE_LABEL = { queued: "排队中", running: "运行中", done: "已完成",
                      error: "失败", stopped: "已停止", interrupted: "已中断" };

const CLS_LABEL = { car: "小汽车", bus: "客车", truck: "货车",
                    motorcycle: "摩托车", bicycle: "非机动车" };
const CLS_COLOR = { car: "", bus: "violet", truck: "cyan",
                    motorcycle: "cyan", bicycle: "" };

function fmt(n) { return (n === undefined || n === null) ? "—" : String(n); }

/* ---------------- tabs ---------------- */
document.querySelectorAll(".tab").forEach((b) => {
  b.addEventListener("click", () => {
    document.querySelectorAll(".tab").forEach((x) => x.classList.remove("active"));
    document.querySelectorAll(".tabpanel").forEach((x) => x.classList.remove("active"));
    b.classList.add("active");
    $("tab-" + b.dataset.tab).classList.add("active");
  });
});

/* ---------------- sources ---------------- */
async function loadSources() {
  const r = await fetch("/api/sources");
  const d = await r.json();
  const box = $("sourceList");
  box.innerHTML = "";
  d.presets.forEach((p) => {
    const el = document.createElement("div");
    el.className = "src-item" + (state.sourceKey === p.key ? " active" : "");
    el.innerHTML = `<div class="src-label">${p.label}</div>
      <div class="src-meta">${p.desc} · ${p.size_mb} MB · ${p.exists ? "就绪" : "缺失"}</div>`;
    el.onclick = () => {
      state.sourceKey = p.key; state.upload = null;
      document.querySelectorAll(".src-item").forEach((x) => x.classList.remove("active"));
      el.classList.add("active");
      applyPreset(p);
    };
    box.appendChild(el);
  });
  d.uploads.forEach((u) => {
    const el = document.createElement("div");
    el.className = "src-item";
    el.innerHTML = `<div class="src-label">上传 · ${u.file}</div>
      <div class="src-meta">用户上传视频 · ${u.size_mb} MB</div>`;
    el.onclick = () => {
      state.sourceKey = null; state.upload = u.file;
      document.querySelectorAll(".src-item").forEach((x) => x.classList.remove("active"));
      el.classList.add("active");
    };
    box.appendChild(el);
  });
}

function applyPreset(p) {
  $("conf").value = p.conf; $("confOut").textContent = p.conf.toFixed(2);
  $("line").value = p.line; $("lineOut").textContent = p.line.toFixed(2);
  if (p.line_x !== null && p.line_x !== undefined) {
    $("lineX").value = p.line_x; $("lineXOut").textContent = p.line_x.toFixed(2);
    $("lineXOn").checked = true;
  } else { $("lineXOn").checked = false; }
  $("classify").checked = !!p.classify;
  $("centerX").value = p.center_x; $("centerY").value = p.center_y;
  $("imgsz").value = String(p.imgsz);
  // Video presets are full-panorama shots: reset any single-approach pick.
  $("approach").value = "";
}

/* ---------------- parameter presets (P3) ---------------- */
function readFormParams() {
  return {
    conf: +$("conf").value,
    line: +$("line").value,
    line_x: $("lineXOn").checked ? +$("lineX").value : null,
    classify: $("classify").checked,
    center_x: +$("centerX").value,
    center_y: +$("centerY").value,
    imgsz: +$("imgsz").value,
    approach: $("approach").value || null,
  };
}

function applyParamPreset(p) {
  $("conf").value = p.conf; $("confOut").textContent = (+p.conf).toFixed(2);
  $("line").value = p.line; $("lineOut").textContent = (+p.line).toFixed(2);
  if (p.line_x !== null && p.line_x !== undefined) {
    $("lineX").value = p.line_x; $("lineXOut").textContent = (+p.line_x).toFixed(2);
    $("lineXOn").checked = true;
  } else { $("lineXOn").checked = false; }
  $("classify").checked = !!p.classify;
  $("centerX").value = p.center_x; $("centerY").value = p.center_y;
  $("imgsz").value = String(p.imgsz);
  $("approach").value = p.approach || "";
}

async function loadParamPresets() {
  const r = await fetch("/api/presets");
  const d = await r.json();
  const sel = $("presetSelect");
  const prev = sel.value;
  sel.innerHTML = "";
  state.paramPresets = {};
  (d.presets || []).forEach((p) => {
    state.paramPresets[p.name] = p;
    const opt = document.createElement("option");
    opt.value = p.name;
    opt.textContent = p.name + (p.builtin ? "（内置）" : "（自定义）");
    opt.title = p.desc || "";
    sel.appendChild(opt);
  });
  if (prev && state.paramPresets[prev]) sel.value = prev;
}

$("presetApplyBtn").addEventListener("click", () => {
  const p = state.paramPresets[$("presetSelect").value];
  if (!p) { $("presetStatus").textContent = "请先选择一个预设。"; return; }
  applyParamPreset(p.params);
  $("presetStatus").textContent = `已套用「${p.name}」：${p.desc || ""}`;
});

$("presetSaveBtn").addEventListener("click", async () => {
  const name = $("presetName").value.trim();
  if (!name) { $("presetStatus").textContent = "保存失败：请先在输入框填写预设名称。"; return; }
  const btn = $("presetSaveBtn");
  btn.disabled = true;
  const r = await fetch("/api/presets", {
    method: "POST", headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ name, params: readFormParams() }),
  });
  const d = await r.json();
  btn.disabled = false;
  if (!d.ok) { $("presetStatus").textContent = "保存失败：" + (d.error || r.status); return; }
  $("presetName").value = "";
  await loadParamPresets();
  $("presetSelect").value = d.name;
  $("presetStatus").textContent = `已保存自定义预设「${d.name}」，重启不丢。`;
});

$("presetDelBtn").addEventListener("click", async () => {
  const name = $("presetSelect").value;
  const p = state.paramPresets[name];
  if (!p) { $("presetStatus").textContent = "请先选择一个预设。"; return; }
  if (p.builtin) { $("presetStatus").textContent = `删除失败：内置预设「${name}」不可删除。`; return; }
  if (!confirm(`删除自定义预设「${name}」？`)) return;
  const r = await fetch("/api/presets/" + encodeURIComponent(name), { method: "DELETE" });
  const d = await r.json();
  if (!d.ok) { $("presetStatus").textContent = "删除失败：" + (d.error || r.status); return; }
  await loadParamPresets();
  $("presetStatus").textContent = `已删除自定义预设「${name}」。`;
});

$("conf").addEventListener("input", (e) => { $("confOut").textContent = (+e.target.value).toFixed(2); });
$("line").addEventListener("input", (e) => { $("lineOut").textContent = (+e.target.value).toFixed(2); });
$("lineX").addEventListener("input", (e) => { $("lineXOut").textContent = (+e.target.value).toFixed(2); });

$("uploadInput").addEventListener("change", async (e) => {
  const f = e.target.files[0];
  if (!f) return;
  $("uploadStatus").textContent = "上传中 — " + f.name + " …";
  const r = await fetch("/api/upload", {
    method: "POST",
    headers: { "X-Filename": encodeURIComponent(f.name) },
    body: f,
  });
  const d = await r.json();
  if (d.ok) {
    $("uploadStatus").textContent = "已上传 — " + d.file + " (" + d.size_mb + " MB)";
    await loadSources();
  } else {
    $("uploadStatus").textContent = "上传失败 — " + (d.error || r.status);
  }
});

/* ---------------- jobs ---------------- */
function fmtDur(s) {
  if (s === undefined || s === null) return "";
  return Math.round(s) + " 秒";
}

function fmtTime(ts) {
  if (!ts) return "";
  const d = new Date(ts * 1000);
  const p = (n) => String(n).padStart(2, "0");
  return `${p(d.getMonth() + 1)}-${p(d.getDate())} ${p(d.getHours())}:${p(d.getMinutes())}`;
}

async function loadJobs() {
  const r = await fetch("/api/jobs");
  const d = await r.json();
  const box = $("jobList");
  box.innerHTML = "";
  if (!d.jobs.length) {
    box.innerHTML = `<div class="progress-text">暂无任务</div>`;
    return;
  }
  d.jobs.forEach((j) => {
    const el = document.createElement("div");
    el.className = "job-item" + (state.job === j.name ? " active" : "");
    const p = j.params || {};
    const stat = j.state === "queued" && j.queue_position
      ? `排队第 ${j.queue_position} 位`
      : (STATE_LABEL[j.state] || j.state) + (j.total !== null && j.total !== undefined ? " · " + j.total : "");
    const meta = [j.source_label || "",
      p.source_type === "stream" ? "实时流 " + (p.duration || "") + " 秒" : "",
      p.preset ? "预设 " + p.preset : "",
      p.approach ? "单进口 " + (APPROACH_LABEL[p.approach] || p.approach) : "",
      p.conf !== undefined ? "置信度 " + p.conf : "",
      p.imgsz ? "分辨率 " + p.imgsz : "",
      j.duration_s !== null && j.duration_s !== undefined ? fmtDur(j.duration_s) : "",
      fmtTime(j.queued_at)].filter(Boolean).join(" · ");
    el.innerHTML = `<div class="job-main">
        <span class="jname">${j.name}</span>
        <span class="jstat ${j.state}">${stat}</span>
      </div>
      <div class="job-meta">${meta}</div>
      <div class="job-actions">
        ${(j.state === "running" || j.state === "queued")
          ? `<button class="mini danger" data-act="stop">停止</button>` : ""}
        <button class="mini" data-act="rerun">重跑</button>
        <button class="mini danger" data-act="delete">删除</button>
      </div>`;
    el.querySelector(".job-main").onclick = () => loadJob(j.name);
    const stopBtn = el.querySelector('[data-act="stop"]');
    if (stopBtn) {
      stopBtn.onclick = async (ev) => {
        ev.stopPropagation();
        const rr = await fetch("/api/job/" + encodeURIComponent(j.name) + "/stop", { method: "POST" });
        const dd = await rr.json();
        if (!dd.ok) { alert("停止失败：" + (dd.error || rr.status)); return; }
        $("progressText").textContent = `正在停止 ${j.name} · 正在收尾并写出已处理结果`;
        await loadJobs();
      };
    }
    el.querySelector('[data-act="rerun"]').onclick = async (ev) => {
      ev.stopPropagation();
      const rr = await fetch("/api/job/" + encodeURIComponent(j.name) + "/rerun", { method: "POST" });
      const dd = await rr.json();
      if (!dd.ok) { alert("重跑失败：" + (dd.error || rr.status)); return; }
      state.job = dd.name;
      pollProgress(dd.name);
      await loadJobs();
    };
    el.querySelector('[data-act="delete"]').onclick = async (ev) => {
      ev.stopPropagation();
      if (!confirm("删除任务 " + j.name + "（含结果文件与历史记录）？")) return;
      const rr = await fetch("/api/job/" + encodeURIComponent(j.name) + "/delete", { method: "POST" });
      const dd = await rr.json();
      if (!dd.ok) { alert("删除失败：" + (dd.error || rr.status)); return; }
      if (state.job === j.name) { state.job = null; state.summary = null; }
      await loadJobs();
    };
    box.appendChild(el);
  });
  // Merge list: finished jobs that carry an approach survey.
  const mbox = $("mergeList");
  mbox.innerHTML = "";
  const mergeable = d.jobs.filter((j) => j.has_approach && j.state === "done");
  if (!mergeable.length) {
    mbox.innerHTML = `<div class="progress-text">暂无单进口调查 — 先以单进口模式运行各进口视频</div>`;
  } else {
    mergeable.forEach((j) => {
      const p = j.params || {};
      const ap = APPROACH_LABEL[p.approach] || p.approach || "";
      const row = document.createElement("label");
      row.className = "check-row";
      row.innerHTML = `<input type="checkbox" data-job="${j.name}"
        ${state.mergeSel.has(j.name) ? "checked" : ""}>
        <span>${j.name} · ${ap} · ${fmt(j.total)} 辆</span>`;
      row.querySelector("input").addEventListener("change", (ev) => {
        if (ev.target.checked) state.mergeSel.add(j.name);
        else state.mergeSel.delete(j.name);
      });
      mbox.appendChild(row);
    });
  }
  // Auto-refresh while anything is queued or running.
  clearInterval(loadJobs._timer);
  if (d.jobs.some((j) => j.state === "queued" || j.state === "running")) {
    loadJobs._timer = setInterval(loadJobs, 5000);
  }
}

/* ---------------- merge ---------------- */
$("mergeBtn").addEventListener("click", async () => {
  const jobs = [...state.mergeSel];
  if (!jobs.length) { alert("请先在上方勾选至少一个单进口任务"); return; }
  const btn = $("mergeBtn");
  btn.disabled = true;
  btn.textContent = "合并中…";
  $("mergeStatus").textContent = "合并中 — " + jobs.join(" + ");
  const r = await fetch("/api/merge", {
    method: "POST", headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ jobs }),
  });
  const d = await r.json();
  btn.disabled = false;
  btn.textContent = "合并所选进口";
  if (!d.ok) {
    $("mergeStatus").textContent = "合并失败 — " + (d.error || r.status);
    return;
  }
  $("mergeStatus").textContent = `已合并 ${d.name} · 共 ${jobs.length} 个进口`;
  let mergedTotal = 0;
  const rows = (d.merged.approaches || []).map((ap) => {
    const per = {};
    let sub = 0;
    for (const [mv, spec] of Object.entries(ap.movements || {})) {
      const s = Object.values(spec.flow_by_type || {}).reduce((a, b) => a + b, 0);
      per[mv] = s; sub += s;
    }
    mergedTotal += sub;
    return `<tr><td>${ap.name}</td><td class="num">${(per.left || 0).toFixed(0)}</td>
      <td class="num">${(per.through || 0).toFixed(0)}</td>
      <td class="num">${(per.right || 0).toFixed(0)}</td>
      <td class="num"><b>${sub.toFixed(0)}</b></td></tr>`;
  }).join("");
  const notes = (d.notes || []).map((n) => `<div class="f-unit">${n}</div>`).join("");
  $("mergeResult").innerHTML = `<div class="finding">
      <div class="f-problem">已合并：${d.name} · 小时流率合计 ${mergedTotal.toFixed(0)} 辆/小时</div>
      <table style="margin-top:8px;"><tr><th>进口</th><th class="num">左转</th>
      <th class="num">直行</th><th class="num">右转</th><th class="num">合计</th></tr>${rows}</table>
      ${notes}
      <div style="margin-top:8px;display:flex;gap:6px;">
        <button class="mini" id="mergePlanBtn">对合并结果运行规划分析</button>
        <a class="mini" href="/api/job/${encodeURIComponent(d.name)}/file/merged_survey.json" download style="text-decoration:none;line-height:26px;">下载 merged_survey.json</a>
      </div></div>`;
  $("mergePlanBtn").onclick = async () => {
    document.querySelector('[data-tab="planning"]').click();
    $("planEmpty").style.display = "none";
    $("planBody").style.display = "block";
    await runPlanningFor(d.name);
  };
  await loadJobs();
});

async function loadJob(name) {
  // P5 fix: check progress FIRST. A running/queued job has no
  // summary.json yet — the old code fetched the summary first, got a
  // 404, and bailed out, so the live panel (P3.5) never appeared for
  // it (e.g. after a page reload mid-run, or a job started via
  // API/batch). Attach to its live progress instead: pollProgress
  // renders the dual-window live panel on every tick and auto-loads
  // the result page when the job finishes.
  try {
    const pr = await fetch("/api/job/" + encodeURIComponent(name) + "/progress");
    if (pr.ok) {
      const p = await pr.json();
      if (p.state === "running" || p.state === "queued") {
        state.job = name;
        $("overviewBody").style.display = "none";
        $("livePanel").style.display = "none";
        $("overviewEmpty").style.display = "block";
        pollProgress(name);
        await loadJobs();
        return;
      }
    }
  } catch (e) { /* fall through to the summary fetch below */ }
  const r = await fetch("/api/job/" + encodeURIComponent(name));
  if (!r.ok) {
    // Non-blocking: a native alert() here would freeze the page's JS
    // (R13: alert on a not-yet-written summary blocked all polling).
    $("progressText").textContent = `结果尚未生成 — ${name} · 请稍后再试`;
    return;
  }
  state.job = name;
  state.summary = await r.json();
  state.lastLiveFrame = null;
  $("livePanel").style.display = "none";
  renderAll();
  await loadJobs();
}

$("runBtn").addEventListener("click", async () => {
  const body = { ...readFormParams(),
    approach: $("approach").value || undefined,
    name: $("jobName").value.trim() || undefined,
  };
  if (state.sourceKey) body.source_key = state.sourceKey;
  else if (state.upload) body.upload = state.upload;
  else { alert("请先选择视频源"); return; }
  $("runBtn").disabled = true;
  $("runBtn").textContent = "运行中…";
  const r = await fetch("/api/run", {
    method: "POST", headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
  const d = await r.json();
  if (!d.ok) {
    alert("启动失败：" + (d.error || r.status));
    $("runBtn").disabled = false;
    $("runBtn").textContent = "开始检测";
    return;
  }
  state.job = d.name;
  pollProgress(d.name);
  await loadJobs();
});

/* ---------------- live stream (P3) ---------------- */
$("streamBtn").addEventListener("click", async () => {
  const url = $("streamUrl").value.trim();
  const dur = +$("streamDuration").value;
  if (!url) { alert("请先填写流地址（rtsp://… 或 cam:0）"); return; }
  if (!(dur >= 5 && dur <= 3600)) {
    alert("录制时长须为 5–3600 秒"); return;
  }
  const body = { ...readFormParams(),
    stream: url, duration: dur,
    name: $("streamJobName").value.trim() || undefined,
  };
  const btn = $("streamBtn");
  btn.disabled = true;
  btn.textContent = "启动中…";
  const r = await fetch("/api/run", {
    method: "POST", headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
  const d = await r.json();
  btn.disabled = false;
  btn.textContent = "开始实时检测";
  if (!d.ok) {
    $("streamStatus").textContent = "启动失败 — " + (d.error || r.status);
    return;
  }
  $("streamStatus").textContent = `实时流 ${d.name} · ${url} · ${dur} 秒`;
  state.job = d.name;
  pollProgress(d.name);
  await loadJobs();
});

function pollProgress(name) {
  clearInterval(state.pollTimer);
  state.pollTimer = setInterval(async () => {
    const r = await fetch("/api/job/" + encodeURIComponent(name) + "/progress");
    const p = await r.json();
    $("progressBar").style.width = (p.pct || 0) + "%";
    if (p.state === "queued") {
      $("progressText").textContent =
        `排队中 — ${name} · 第 ${p.queue_position || "?"} 位 · 等待执行`;
      return;
    }
    // P3.5 live feedback: paired frames + counts that grow as
    // vehicles cross the line. Refresh the live panel on every tick
    // that carries a newer snapshot.
    if (p.live) {
      renderLive(p.live, name, p.stream);
      $("progressText").textContent = p.stream
        ? `运行中 — ${name} · 实时流 · 已处理 ${p.frame || 0} 帧 · ${p.elapsed_s || 0} 秒 · 已过线 ${p.live.total || 0} 辆`
        : `运行中 — ${name} · ${p.frame || 0}/${p.total_frames || "?"} 帧 · ${p.pct || 0}% · ${p.elapsed_s || 0} 秒 · 已过线 ${p.live.total || 0} 辆`;
    } else {
      $("progressText").textContent = p.stream
        ? `运行中 — ${name} · 实时流 · 已处理 ${p.frame || 0} 帧 · ${p.elapsed_s || 0} 秒`
        : `运行中 — ${name} · ${p.frame || 0}/${p.total_frames || "?"} 帧 · ${p.pct || 0}% · ${p.elapsed_s || 0} 秒`;
    }
    if (p.done && p.state === "running") {
      // Detection loop finished, but the engine is still transcoding
      // the annotated video and writing summary.json (up to ~2 min
      // for a 1080p clip): the job is not done until the registry
      // says so. Keep polling. (R13: treating progress "done" as
      // finished stopped the poll early and the result page never
      // loaded; loadJob then alerted on the missing summary and
      // blocked the page's JS entirely.)
      $("progressText").textContent =
        `收尾中 — ${name} · 检测完成 · 正在转码并写出结果文件…`;
      return;
    }
    if (p.state === "done" || p.state === "error" || p.state === "stopped") {
      clearInterval(state.pollTimer);
      $("progressBar").style.width = p.state === "error" ? "0%" : "100%";
      $("progressText").textContent =
        p.state === "error" ? `失败 — ${name} 运行失败，见运行日志`
        : p.state === "stopped" ? `已停止 — ${name} · 结果已写出`
        : `已完成 — ${name} · ${p.elapsed_s || 0} 秒`;
      $("runBtn").disabled = false;
      $("runBtn").textContent = "开始检测";
      if (p.state === "done" || p.state === "stopped") await loadJob(name);
      await loadJobs();
    }
  }, 1000);
}

/* ---------------- live feedback (P3.5 + R19) ---------------- */
// Dual live windows + KPIs that climb as vehicles cross the line + a
// live flow curve. R19: the left window is the source video itself,
// played at its native frame rate (smooth, looping) — no longer a
// snapshot frame pretending to be video. The right window is the
// engine's annotated preview, refreshed every ~5 frames (plus an
// immediate refresh on a crossing). KPIs jump with a bump animation
// the moment a number changes. Driven by the engine's progress.json
// "live" snapshot.
function renderLive(live, jobName, isStream) {
  const panel = $("livePanel");
  if (panel.style.display === "none") {
    panel.style.display = "block";
    $("overviewEmpty").style.display = "none";
    // A previous result must not stay visible under the live panel
    // while a new run is in progress (R17: it stacked below the live
    // dual windows in the centre column).
    $("overviewBody").style.display = "none";
    $("liveState").textContent = "运行中";
    $("kpiEmpty").style.display = "none";
    $("kpiLiveWrap").style.display = "block";
    $("kpiResultWrap").style.display = "none";
    $("flowEmpty").style.display = "none";
    $("liveFlowWrap").style.display = "block";
    $("flowWrap").style.display = "none";
    state.lastKpis = null;
  }
  // R19: wire the native-rate source video when the job (or its kind)
  // changes — not only on first panel show, so a second run from the
  // same page re-points the video. File jobs play /original; stream
  // jobs have no source file and keep the raw snapshot image.
  const vidKey = jobName + (isStream ? ":s" : ":f");
  if (state.liveVideoFor !== vidKey) {
    state.liveVideoFor = vidKey;
    const vid = $("liveRawVideo"), img = $("liveRawImg");
    if (isStream) {
      vid.removeAttribute("src");
      vid.load();
      vid.style.display = "none";
      img.style.display = "block";
    } else {
      img.style.display = "none";
      vid.style.display = "block";
      vid.src = "/api/job/" + encodeURIComponent(jobName) + "/original";
      vid.play().catch(() => {});
    }
  }
  if (live.frame !== state.lastLiveFrame) {
    state.lastLiveFrame = live.frame;
    const bust = "?t=" + live.frame;
    // The raw snapshot is only shown for stream jobs; file jobs play
    // the real source video in the left window instead (R19).
    if (isStream) {
      $("liveRawImg").src = "/api/job/" + encodeURIComponent(jobName) + "/file/live_raw.jpg" + bust;
    }
    $("liveAnnImg").src = "/api/job/" + encodeURIComponent(jobName) + "/file/live_annotated.jpg" + bust;
    $("liveRawCap").textContent = isStream ? "第 " + live.frame + " 帧" : "循环播放";
    $("liveAnnCap").textContent = "第 " + live.frame + " 帧";
  }
  const bt = live.by_type || {};
  const car = bt.car || 0, moto = bt.motorcycle || 0,
        truck = bt.truck || 0, bus = bt.bus || 0;
  const total = live.total || 0;
  const hasV = live.total_v !== undefined;
  const btv = live.by_type_v || {};
  const carV = btv.car || 0, motoV = btv.motorcycle || 0,
        truckV = btv.truck || 0, busV = btv.bus || 0;
  const totalV = live.total_v || 0;
  // R19: a KPI whose number changed since the last tick gets the bump
  // animation, so the jump is visible the moment a vehicle crosses.
  const prev = state.lastKpis || {};
  const bump = (key, val) => prev[key] !== undefined && prev[key] !== val;
  const pct = (n, t) => n && t ? ((100 * n / t).toFixed(0) + "%") : "";
  // R21: with two counting lines, each line gets its own headed block
  // (total + per-type, all live) — before, the grid was hard-wired to
  // the horizontal line and the vertical line only got one appended
  // total tile with no per-type split. Single-line jobs keep the
  // original flat layout.
  const lineBlock = (head, t, c, m, tr, b, cls, keys) =>
    lineHead(head) +
    kpi("过线总数", t, "实时递增", cls, bump(keys[0], t)) +
    kpi("小汽车", c, pct(c, t), "", bump(keys[1], c)) +
    kpi("摩托车", m, pct(m, t), "cyan", bump(keys[2], m)) +
    kpi("货车", tr, "", "", bump(keys[3], tr)) +
    kpi("客车", b, "", "violet", bump(keys[4], b));
  // R21: per-approach blocks lead the panel when movement
  // classification is on — each approach's total ticks live with its
  // movement and type splits; the per-line blocks follow below as the
  // crossing-count view.
  const apTotals = {};
  let apHtml = "";
  if (live.approaches) {
    apHtml = lineHead("分进口计数 · 流向分类口径") + APPROACH_ORDER.map((name) => {
      const d = live.approaches[name] ||
                { total: 0, by_movement: {}, by_type: {} };
      apTotals["ap_" + name] = d.total || 0;
      return approachTile(name, d, bump("ap_" + name, d.total || 0));
    }).join("");
  } else if (live.approach_single) {
    const d = live.approach_single;
    apTotals.ap_single = d.total || 0;
    apHtml = lineHead("单进口计数 · 流向分类口径") +
      approachTile(d.name || "本进口", d, bump("ap_single", d.total || 0));
  }
  // v0.14: section tasks (no movement classification) lead with the
  // per-direction tiles — a two-way mixed total is not a usable
  // section figure. Intersection tasks keep the approach blocks in
  // front and the per-line blocks below them.
  const dirTotals = {};
  let dirHtml = "";
  if (!live.approaches && !live.approach_single && live.by_direction) {
    const bd = live.by_direction;
    const empty = { total: 0, by_type: [] };
    dirTotals.dir_down = (bd.down || empty).total || 0;
    dirTotals.dir_up = (bd.up || empty).total || 0;
    dirHtml = lineHead("分方向计数 · 过线口径") +
      directionTile(DIR_LABEL.h.down, bd.down || empty, total, "lime",
                    bump("dir_down", dirTotals.dir_down)) +
      directionTile(DIR_LABEL.h.up, bd.up || empty, total, "cyan",
                    bump("dir_up", dirTotals.dir_up));
    if (live.by_direction_v) {
      const bdv = live.by_direction_v;
      dirTotals.dir_v_down = (bdv.down || empty).total || 0;
      dirTotals.dir_v_up = (bdv.up || empty).total || 0;
      dirHtml += directionTile(DIR_LABEL.v.down, bdv.down || empty,
                               totalV, "lime",
                               bump("dir_v_down", dirTotals.dir_v_down)) +
                 directionTile(DIR_LABEL.v.up, bdv.up || empty,
                               totalV, "cyan",
                               bump("dir_v_up", dirTotals.dir_v_up));
    }
  }
  $("liveKpis").innerHTML =
    apHtml +
    dirHtml +
    (dirHtml
      ? ""
      : (hasV
        ? lineBlock("横向计数线", total, car, moto, truck, bus, "lime",
                    ["total", "car", "moto", "truck", "bus"]) +
          lineBlock("纵向计数线", totalV, carV, motoV, truckV, busV, "cyan",
                    ["total_v", "car_v", "moto_v", "truck_v", "bus_v"])
        : kpi("已过线总数", total, "实时递增 · 横向计数线", "lime", bump("total", total)) +
          kpi("小汽车", car, pct(car, total), "", bump("car", car)) +
          kpi("摩托车", moto, pct(moto, total), "cyan", bump("moto", moto)) +
          kpi("货车", truck, "", "", bump("truck", truck)) +
          kpi("客车", bus, "", "violet", bump("bus", bus)))) +
    kpi("确认轨迹", live.tracks || 0, "已确认输出轨迹", "", bump("tracks", live.tracks || 0));
  state.lastKpis = { total, car, moto, truck, bus,
                     tracks: live.tracks || 0, total_v: live.total_v,
                     car_v: carV, moto_v: motoV,
                     truck_v: truckV, bus_v: busV,
                     ...apTotals, ...dirTotals };
  const curve = live.flow_curve || [];
  const hasDir = curve.some((b) => b.by_direction);
  const max = hasDir
    ? Math.max(1, ...curve.flatMap((b) => [
        (b.by_direction.down || {}).total || 0,
        (b.by_direction.up || {}).total || 0]))
    : Math.max(1, ...curve.map((b) => b.total));
  $("liveFlowBars").innerHTML = curve.map((b) => {
    if (hasDir) {
      const dn = (b.by_direction.down || {}).total || 0;
      const up = (b.by_direction.up || {}).total || 0;
      return `<div class="bar" style="height:${Math.max(2, Math.round(100 * dn / max))}%" title="时段 ${b.bucket} · 向下 ${dn} 辆"></div>` +
             `<div class="bar up" style="height:${Math.max(2, Math.round(100 * up / max))}%" title="时段 ${b.bucket} · 向上 ${up} 辆"></div>`;
    }
    const h = Math.max(2, Math.round(100 * b.total / max));
    return `<div class="bar" style="height:${h}%" title="${b.bucket}: ${b.total} 辆"></div>`;
  }).join("");
  $("liveFlowLabelLeft").textContent = curve.length ? `第 1 格 — ${curve[0].total} 辆` : "";
  $("liveFlowLabelRight").textContent = !curve.length ? ""
    : hasDir
      ? `共 ${curve.length} 格 · 向下 ${curve.reduce((a, b) => a + ((b.by_direction.down || {}).total || 0), 0)} · 向上 ${curve.reduce((a, b) => a + ((b.by_direction.up || {}).total || 0), 0)}（横向线）`
      : `共 ${curve.length} 格 · 合计 ${total} 辆`;
}

/* ---------------- dual-video sync (P3.5) ---------------- */
// Result page: original + annotated play in lockstep. Either side can
// lead (play / pause / seek); a guard flag stops the echo.
let videoSyncWired = false;
function wireVideoSync() {
  if (videoSyncWired) return;
  videoSyncWired = true;
  const a = $("originalVideo"), b = $("annotatedVideo");
  // No global "syncing" blackout window: it dropped one-shot events
  // (pause / seeked) that landed inside it, with no retry (R13).
  // The echo is harmless instead: every mirror is idempotent — play
  // only when paused, pause only when playing, seek only when the
  // gap exceeds 0.15s — so an echo is a no-op and cannot loop.
  function mirror(src, dst, kind) {
    if (kind === "play") {
      if (dst.paused) { dst.play().catch(() => {}); }
    } else if (kind === "pause") {
      if (!dst.paused) { dst.pause(); }
    } else if (kind === "seek") {
      if (Math.abs(dst.currentTime - src.currentTime) > 0.15) {
        dst.currentTime = src.currentTime;
      }
    }
  }
  [["play", a, b], ["play", b, a], ["pause", a, b], ["pause", b, a],
   ["seeked", a, b], ["seeked", b, a]].forEach(([ev, src, dst]) => {
    src.addEventListener(ev, () => mirror(src, dst, ev === "seeked" ? "seek" : ev));
  });
  // Keep drift bounded while playing (different decoders drift apart).
  a.addEventListener("timeupdate", () => mirror(a, b, "seek"));
  // Stream / merged jobs have no original file: hide that pane.
  a.addEventListener("error", () => { $("origWrap").style.display = "none"; });
}

$("batchBtn").addEventListener("click", async () => {
  const btn = $("batchBtn");
  btn.disabled = true;
  btn.textContent = "入队中…";
  const r = await fetch("/api/run/batch", {
    method: "POST", headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ jobs: [
      { source_key: "test1" }, { source_key: "test2" }, { source_key: "test3" },
    ] }),
  });
  const d = await r.json();
  btn.disabled = false;
  btn.textContent = "批量运行三个预设";
  if (!d.ok) { alert("批量入队失败：" + (d.error || r.status)); return; }
  $("progressText").textContent =
    `已入队 ${d.jobs.length} 个任务：${d.jobs.map((j) => j.name).join(" / ")}`;
  state.job = d.jobs[0].name;
  pollProgress(d.jobs[0].name);
  await loadJobs();
});

/* ---------------- render ---------------- */
function byType(s, cls) {
  const row = (s.by_type || []).find((x) => x.cls_name === cls);
  return row ? row.count : 0;
}

function kpi(label, value, sub, cls, bump) {
  return `<div class="kpi ${cls || ""}${bump ? " bump" : ""}"><div class="ghost-num">${fmt(value)}</div>
    <div class="k-label">${label}</div><div class="k-value">${fmt(value)}</div>
    <div class="k-sub">${sub || ""}</div></div>`;
}

// R21: full-width section head inside a KPI grid, used to give each
// counting line its own labelled block (horizontal / vertical).
function lineHead(text) {
  return `<div class="kpi-head">${text}</div>`;
}

// R21/v0.14: screen-direction labels per counting line. The vertical
// counter is fed swapped coordinates, so its down/up mean screen
// right/left. Labels stay screen-relative on purpose — never dressed
// up as real-world bearings.
const DIR_LABEL = { h: { down: "向下过线", up: "向上过线" },
                    v: { down: "向右过线", up: "向左过线" } };

// R21/v0.14: one wide tile per travel direction of a counting line.
// d = a by_direction entry {total, by_type:[rows]}; lineTotal is the
// whole line count the share refers to.
function directionTile(name, d, lineTotal, cls, bumped) {
  const bt = {};
  for (const r of (d.by_type || [])) bt[r.cls_name] = r.count;
  const btTxt = `汽车 ${bt.car || 0} · 摩托 ${bt.motorcycle || 0} · ` +
                `货车 ${bt.truck || 0} · 客车 ${bt.bus || 0}`;
  const share = lineTotal
    ? (100 * (d.total || 0) / lineTotal).toFixed(0) : "0";
  return `<div class="kpi span2 ${cls || ""}${bumped ? " bump" : ""}">` +
    `<div class="ghost-num">${fmt(d.total || 0)}</div>` +
    `<div class="k-label">${name}</div>` +
    `<div class="k-value">${fmt(d.total || 0)}</div>` +
    `<div class="k-sub">占本断面 ${share}%</div>` +
    `<div class="k-sub">${btTxt}</div></div>`;
}

// v0.14: {movement: {cls: n}} -> {total, by_movement, by_type}.
function distFromMoves(byMove) {
  const d = { total: 0, by_movement: {}, by_type: {} };
  for (const [m, byCls] of Object.entries(byMove || {})) {
    let n = 0;
    for (const [cls, c] of Object.entries(byCls || {})) {
      n += c;
      d.by_type[cls] = (d.by_type[cls] || 0) + c;
    }
    d.by_movement[m] = n;
    d.total += n;
  }
  return d;
}

const MOVE_NAME = { left: "左转", through: "直行", right: "右转",
                    uturn: "掉头" };
const MOVE_COLOR = { left: "#62F6FF", through: "#D7FF35",
                     right: "#8C6CFF", uturn: "#8a8f98" };

// v0.14: per-approach share-bar cards — the movement structure of
// each approach as horizontal bars (count + share), table stays below
// for exact figures.
function movementCards(mv) {
  return `<div class="mov-cards">` + APPROACH_ORDER.map((ap) => {
    const d = distFromMoves(mv[ap]);
    const rows = ["left", "through", "right", "uturn"].map((m) => {
      const n = d.by_movement[m] || 0;
      const p = d.total ? Math.round(100 * n / d.total) : 0;
      return `<div class="mov-row"><span class="mov-name">${MOVE_NAME[m]}</span>` +
        `<div class="mov-bar"><i style="width:${p}%;background:${MOVE_COLOR[m]}"></i></div>` +
        `<span class="mov-num">${n} 辆 · ${p}%</span></div>`;
    }).join("");
    return `<div class="mov-card"><div class="mov-card-head">` +
      `<span>${ap}</span><span class="mov-total">${d.total} 辆</span></div>` +
      `${rows}</div>`;
  }).join("") + `</div>`;
}

// v0.14: schematic intersection diagram — 12 movement arrows on a
// stylised cross, stroke width proportional to the movement count,
// coloured by movement (through lime / left cyan / right violet).
// Schematic only: shared exit lanes are slightly offset so flows
// stay separable. U-turns get no arrow (noted in the detail line).
const MOVE_PATHS = {
  "南进口": {
    through: "M185 336 L185 4",
    left: "M185 336 L185 182 Q185 150 152 150 L4 150",
    right: "M185 336 L185 224 Q185 192 218 192 L336 192" },
  "北进口": {
    through: "M155 4 L155 336",
    left: "M155 4 L155 148 Q155 178 188 178 L336 178",
    right: "M155 4 L155 130 Q155 162 128 162 L4 162" },
  "西进口": {
    through: "M4 185 L336 185",
    left: "M4 185 L148 185 Q192 185 192 148 L192 4",
    right: "M4 185 L125 185 Q155 185 155 215 L155 336" },
  "东进口": {
    through: "M336 155 L4 155",
    left: "M336 155 L188 155 Q148 155 148 188 L148 336",
    right: "M336 155 L215 155 Q185 155 185 125 L185 4" },
};
const MOVE_LABEL_POS = {
  "南进口": { through: [197, 255], left: [75, 138], right: [262, 206] },
  "北进口": { through: [142, 95], left: [262, 164], right: [75, 176] },
  "西进口": { through: [100, 200], left: [206, 90], right: [168, 262] },
  "东进口": { through: [240, 142], left: [134, 262], right: [172, 88] },
};

function movementDiagram(mv) {
  const dists = {};
  let maxN = 0;
  for (const ap of APPROACH_ORDER) {
    dists[ap] = distFromMoves(mv[ap]);
    for (const m of ["left", "through", "right"]) {
      maxN = Math.max(maxN, dists[ap].by_movement[m] || 0);
    }
  }
  maxN = Math.max(1, maxN);
  const markers = ["through", "left", "right"].map((m) =>
    `<marker id="ah-${m}" viewBox="0 0 10 10" refX="8" refY="5" ` +
    `markerWidth="5.5" markerHeight="5.5" orient="auto-start-reverse">` +
    `<path d="M0 0 L10 5 L0 10 z" fill="${MOVE_COLOR[m]}"></path></marker>`
  ).join("");
  let arrows = "";
  for (const ap of APPROACH_ORDER) {
    for (const m of ["through", "left", "right"]) {
      const n = dists[ap].by_movement[m] || 0;
      const w = n <= 0 ? 1 : 2.5 + 9 * n / maxN;
      const bt = {};
      const byMove = (mv[ap] || {})[m] || {};
      for (const [cls, c] of Object.entries(byMove)) bt[cls] = c;
      const btTxt = `汽车 ${bt.car || 0} · 摩托 ${bt.motorcycle || 0} · ` +
                    `货车 ${bt.truck || 0} · 客车 ${bt.bus || 0}`;
      const [lx, ly] = MOVE_LABEL_POS[ap][m];
      arrows +=
        `<path class="mv-arrow" d="${MOVE_PATHS[ap][m]}" fill="none" ` +
        `stroke="${MOVE_COLOR[m]}" stroke-width="${w.toFixed(1)}" ` +
        `opacity="${n <= 0 ? 0.15 : 0.9}" marker-end="url(#ah-${m})" ` +
        `data-ap="${ap}" data-mv="${m}" data-n="${n}" ` +
        `data-bt="${btTxt}" style="cursor:pointer"></path>` +
        `<text x="${lx}" y="${ly}" fill="#e8e9ea" font-size="11" ` +
        `font-family="var(--mono)" text-anchor="middle" ` +
        `style="paint-order:stroke;stroke:#08090B;stroke-width:3">${n}</text>`;
    }
  }
  const uturnTxt = APPROACH_ORDER.map((ap) =>
    `${ap.replace("进口", "")} ${dists[ap].by_movement.uturn || 0}`).join(" · ");
  const label = (x, y, t) =>
    `<text x="${x}" y="${y}" fill="#9a9ea6" font-size="11.5" ` +
    `font-family="var(--mono)" text-anchor="middle" ` +
    `style="paint-order:stroke;stroke:#08090B;stroke-width:3">${t}</text>`;
  return `<div class="mov-diagram"><svg viewBox="0 0 340 340">` +
    `<defs>${markers}</defs>` +
    `<rect x="140" y="0" width="60" height="340" fill="#101216" stroke="#292C32"></rect>` +
    `<rect x="0" y="140" width="340" height="60" fill="#101216" stroke="#292C32"></rect>` +
    `<rect x="140" y="140" width="60" height="60" fill="#14161a"></rect>` +
    arrows +
    label(170, 30, "北进口") + label(170, 318, "南进口") +
    label(36, 100, "西进口") + label(304, 100, "东进口") +
    `</svg>` +
    `<div class="mov-detail" id="movDetail">点按任意箭头查看该流向的分车型构成 · 掉头不画箭头（${uturnTxt}）</div></div>`;
}

function bindDiagram() {
  document.querySelectorAll("#movDiagram path.mv-arrow").forEach((p) => {
    p.addEventListener("click", () => {
      const el = $("movDetail");
      if (el) {
        el.textContent = `${p.dataset.ap} · ${MOVE_NAME[p.dataset.mv]} ` +
          `${p.dataset.n} 辆 — ${p.dataset.bt}`;
      }
    });
  });
}

// R21: one wide tile per approach — big total, then the movement split
// and the type split as sub lines. d = {total, by_movement, by_type}.
function approachTile(name, d, bumped) {
  const mv = d.by_movement || {}, bt = d.by_type || {};
  const mvTxt = `左转 ${mv.left || 0} · 直行 ${mv.through || 0} · ` +
                `右转 ${mv.right || 0} · 掉头 ${mv.uturn || 0}`;
  const btTxt = `汽车 ${bt.car || 0} · 摩托 ${bt.motorcycle || 0} · ` +
                `货车 ${bt.truck || 0} · 客车 ${bt.bus || 0}`;
  return `<div class="kpi span2${bumped ? " bump" : ""}">` +
    `<div class="ghost-num">${fmt(d.total || 0)}</div>` +
    `<div class="k-label">${name}</div>` +
    `<div class="k-value">${fmt(d.total || 0)}</div>` +
    `<div class="k-sub">${mvTxt}</div>` +
    `<div class="k-sub">${btTxt}</div></div>`;
}

function renderAll() {
  const s = state.summary;
  if (!s) return;
  $("overviewEmpty").style.display = "none";
  $("overviewBody").style.display = "block";
  $("kpiEmpty").style.display = "none";
  $("kpiLiveWrap").style.display = "none";
  $("kpiResultWrap").style.display = "block";
  $("flowEmpty").style.display = "none";
  $("liveFlowWrap").style.display = "none";
  $("flowWrap").style.display = "block";
  $("expEmpty").style.display = "none";
  $("expBody").style.display = "block";

  const total = s.total;
  const car = byType(s, "car"), moto = byType(s, "motorcycle"),
        truck = byType(s, "truck"), bus = byType(s, "bus");
  // R21: when a vertical line was counted too, mirror the KPI block
  // for it under its own head — the headline grid used to show the
  // horizontal line only, with the vertical line relegated to the
  // breakdown tables further down. Totals stay per-line (never
  // merged), matching the counting doctrine.
  const vSub = (s.by_line || {}).vertical || null;
  const pctR = (n, t) => n && t ? ((100 * n / t).toFixed(0) + "%") : "";
  const lineTiles = (sub, cls) =>
    kpi("过线总数", sub.total, "定向过线计数", cls) +
    kpi("小汽车", byType(sub, "car"), pctR(byType(sub, "car"), sub.total), "") +
    kpi("摩托车", byType(sub, "motorcycle"), pctR(byType(sub, "motorcycle"), sub.total), "cyan") +
    kpi("货车", byType(sub, "truck"), "", "") +
    kpi("客车", byType(sub, "bus"), "", "violet");
  // R21: per-approach tiles lead the result KPIs too, built from the
  // final movements aggregate (same {total, by_movement, by_type}
  // shape as the live snapshot's per-approach data).
  const apFromMoves = (byMove) => {
    const d = { total: 0, by_movement: {}, by_type: {} };
    for (const [m, byCls] of Object.entries(byMove || {})) {
      let n = 0;
      for (const [cls, c] of Object.entries(byCls || {})) {
        n += c;
        d.by_type[cls] = (d.by_type[cls] || 0) + c;
      }
      d.by_movement[m] = n;
      d.total += n;
    }
    return d;
  };
  let apResHtml = "";
  if (s.movements) {
    apResHtml = lineHead("分进口计数 · 流向分类口径") +
      APPROACH_ORDER.map((name) =>
        approachTile(name, apFromMoves(s.movements[name]), false)).join("");
  } else if (s.approach_mode && s.approach_mode.movements) {
    apResHtml = lineHead("单进口计数 · 流向分类口径") +
      approachTile(s.approach_mode.approach || "本进口",
                   apFromMoves(s.approach_mode.movements), false);
  }
  // v0.14: section tasks lead the result KPIs with per-direction
  // tiles, and the mixed two-way line blocks are suppressed (the
  // mixed totals survive only as reconciliation notes in the
  // breakdown tables). With movements present, direction splits
  // live in those tables instead.
  let dirResHtml = "";
  if (!s.movements && !s.approach_mode && s.by_direction) {
    const empty = { total: 0, by_type: [] };
    dirResHtml = lineHead("分方向计数 · 过线口径") +
      directionTile(DIR_LABEL.h.down, s.by_direction.down || empty,
                    s.total, "lime", false) +
      directionTile(DIR_LABEL.h.up, s.by_direction.up || empty,
                    s.total, "cyan", false);
    if (vSub && vSub.by_direction) {
      dirResHtml +=
        directionTile(DIR_LABEL.v.down, vSub.by_direction.down || empty,
                      vSub.total, "lime", false) +
        directionTile(DIR_LABEL.v.up, vSub.by_direction.up || empty,
                      vSub.total, "cyan", false);
    }
  }
  const mixedHtml = dirResHtml
    ? ""
    : (vSub ? lineHead("横向计数线") : "") +
      lineTiles(s, "lime") +
      (vSub ? lineHead("纵向计数线") + lineTiles(vSub, "cyan") : "");
  $("kpis").innerHTML =
    apResHtml +
    dirResHtml +
    mixedHtml +
    kpi("确认轨迹", s.unique_tracks_seen, "已确认输出轨迹", "");

  const v = $("annotatedVideo");
  v.src = "/api/job/" + encodeURIComponent(state.job) + "/file/annotated.mp4";
  $("videoCapLeft").textContent = "标注视频 — " + state.job;
  $("videoCapRight").textContent = "引擎 " + ((s.params && s.params.engine) || "");
  // P3.5: original video beside the annotated one, played in sync.
  $("origWrap").style.display = "";
  const ov = $("originalVideo");
  ov.src = "/api/job/" + encodeURIComponent(state.job) + "/original";
  $("origCapLeft").textContent = "原视频 — " + state.job;
  $("origCapRight").textContent = "视频源";
  wireVideoSync();

  // flow curve — v0.14: paired per-direction bars when the data
  // carries by_direction (lime = down, cyan = up, horizontal line).
  const curve = s.flow_curve || [];
  const hasDir = curve.some((b) => b.by_direction);
  const max = hasDir
    ? Math.max(1, ...curve.flatMap((b) => [
        (b.by_direction.down || {}).total || 0,
        (b.by_direction.up || {}).total || 0]))
    : Math.max(1, ...curve.map((b) => b.total));
  $("flowBars").innerHTML = curve.map((b) => {
    if (hasDir) {
      const dn = (b.by_direction.down || {}).total || 0;
      const up = (b.by_direction.up || {}).total || 0;
      return `<div class="bar" style="height:${Math.max(2, Math.round(100 * dn / max))}%" title="时段 ${b.bucket} · 向下 ${dn} 辆"></div>` +
             `<div class="bar up" style="height:${Math.max(2, Math.round(100 * up / max))}%" title="时段 ${b.bucket} · 向上 ${up} 辆"></div>`;
    }
    const h = Math.max(2, Math.round(100 * b.total / max));
    return `<div class="bar" style="height:${h}%" title="${b.bucket}: ${b.total} 辆"></div>`;
  }).join("");
  $("flowLabelLeft").textContent = curve.length ? `第 1 格 — ${curve[0].total} 辆` : "";
  $("flowLabelRight").textContent = !curve.length ? ""
    : hasDir
      ? `共 ${curve.length} 格 · 向下 ${curve.reduce((a, b) => a + ((b.by_direction.down || {}).total || 0), 0)} · 向上 ${curve.reduce((a, b) => a + ((b.by_direction.up || {}).total || 0), 0)}（横向线）`
      : `共 ${curve.length} 格 · 合计 ${total} 辆`;

  // by-line tables — v0.14: grouped per direction; the two-way
  // total is demoted to a reconciliation note, never a headline.
  // Old outputs without by_direction keep the legacy table.
  let html = "";
  const lines = s.by_line || { horizontal: s };
  for (const [key, sub] of Object.entries(lines)) {
    const label = key === "horizontal" ? "横向计数线" : "纵向计数线";
    const dl = key === "horizontal" ? DIR_LABEL.h : DIR_LABEL.v;
    if (sub.by_direction) {
      for (const d of ["down", "up"]) {
        const dd = sub.by_direction[d] || { total: 0, by_type: [] };
        html += `<h3 class="subhead">${label} · ${dl[d]} — ${dd.total} 辆</h3><table>
          <tr><th>车型</th><th class="num">数量</th><th class="num">占本断面</th></tr>` +
          (dd.by_type || []).map((row) =>
            `<tr><td>${row.cls_label}</td><td class="num">${row.count}</td>
             <td class="num">${(100 * row.share).toFixed(1)}%</td></tr>`).join("") +
          `</table>`;
      }
      html += `<div class="note">${label}双向合计 ${sub.total} 辆（仅对账用；断面流量以分方向数字为准）。</div>`;
    } else {
      html += `<h3 class="subhead">${label} — 合计 ${sub.total} 辆</h3><table>
        <tr><th>车型</th><th class="num">数量</th><th class="num">占比</th></tr>` +
        (sub.by_type || []).map((row) =>
          `<tr><td>${row.cls_label}</td><td class="num">${row.count}</td>
           <td class="num">${(100 * row.share).toFixed(1)}%</td></tr>`).join("") +
        `</table>`;
    }
  }
  $("byLineTables").innerHTML = html;

  // single-approach mode: movement split read off the exit edge.
  const am = s.approach_mode;
  if (am) {
    const mv1 = am.movements || {};
    const moveName = { left: "左转", through: "直行", right: "右转", uturn: "掉头" };
    let ahtml = `<h3 class="subhead">单进口流向 — ${APPROACH_LABEL[am.approach] || am.approach} · 录制 ${am.duration_min} 分钟</h3>
      <table><tr><th>流向</th><th class="num">小汽车</th><th class="num">摩托车</th>
      <th class="num">货车</th><th class="num">客车</th><th class="num">合计</th></tr>`;
    for (const [m, byType] of Object.entries(mv1)) {
      const c = byType.car || 0, mo = byType.motorcycle || 0,
            t = byType.truck || 0, b = byType.bus || 0;
      ahtml += `<tr><td>${moveName[m] || m}</td><td class="num">${c}</td>
        <td class="num">${mo}</td><td class="num">${t}</td>
        <td class="num">${b}</td><td class="num"><b>${c + mo + t + b}</b></td></tr>`;
    }
    ahtml += `</table><div class="note">机位在停止线后方，流向按轨迹出口画面边判定（上=直行 / 左=左转 / 右=右转 / 下=掉头）；进口总量以计数线过线数为准，本表为流向结构。合并各进口片段后才是规划分析的正式输入。</div>`;
    $("byLineTables").innerHTML += ahtml;
  }

  // movements
  const mv = s.movements;
  if (mv && Object.keys(mv).length) {
    $("movEmpty").style.display = "none";
    $("movBody").style.display = "block";
    $("planEmpty").style.display = "none";
    $("planBody").style.display = "block";
    // v0.14: schematic diagram + share-bar cards lead the tab, the
    // exact tables stay below them.
    $("movDiagram").innerHTML = movementDiagram(mv);
    bindDiagram();
    $("movCards").innerHTML = movementCards(mv);
    let mhtml = "";
    const moveName = { left: "左转", through: "直行", right: "右转" };
    for (const [ap, byMove] of Object.entries(mv)) {
      mhtml += `<h3 class="subhead">${ap}</h3><table>
        <tr><th>流向</th><th class="num">小汽车</th><th class="num">摩托车</th>
        <th class="num">货车</th><th class="num">客车</th><th class="num">合计</th></tr>`;
      for (const [m, byType] of Object.entries(byMove)) {
        const c = byType.car || 0, mo = byType.motorcycle || 0,
              t = byType.truck || 0, b = byType.bus || 0;
        mhtml += `<tr><td>${moveName[m] || m}</td><td class="num">${c}</td>
          <td class="num">${mo}</td><td class="num">${t}</td>
          <td class="num">${b}</td><td class="num"><b>${c + mo + t + b}</b></td></tr>`;
      }
      mhtml += `</table>`;
    }
    $("movTables").innerHTML = mhtml;
  } else {
    $("movEmpty").style.display = "block";
    $("movBody").style.display = "none";
    $("planEmpty").style.display = "block";
    $("planBody").style.display = "none";
  }

  // export files
  const files = [
    ["annotated.mp4", "带检测框 / 轨迹 ID / 计数线的标注视频"],
    ["summary.json", "过线总数 · 分车型 · 分断面 · 时段流量曲线"],
    ["events.csv", "逐车过线记录（轨迹号 / 车型 / 时刻 / 断面）"],
    ["movements.csv", "四进口 × 流向 × 车型轨迹表（开流向分类时）"],
    ["survey_auto.json", "分进口分流向自动调查表（planning 输入）"],
    ["approach_survey.json", "单进口调查片段（单进口模式时）"],
    ["approach_movements.csv", "单进口流向轨迹表（单进口模式时）"],
    ["merged_survey.json", "多进口合并调查表（合并任务时）"],
    ["planning.json", "规划分析结构化结果（运行规划分析后）"],
    ["planning.md", "规划分析报告 Markdown（运行规划分析后）"],
    ["report.xlsx", "调查报告 Excel（概览 / 过线记录 / 流向统计 / 规划分析 / 数据说明）"],
    ["report.pdf", "调查报告 PDF（同上，A4 打印版）"],
  ];
  $("fileList").innerHTML = files.map(([f, desc]) =>
    `<div class="file-row"><span><span class="fname">${f}</span>
     <span class="fmeta"> — ${desc}</span></span>
     <a href="/api/job/${encodeURIComponent(state.job)}/file/${f}" download>下载</a></div>`
  ).join("");
}

/* ---------------- planning ---------------- */
async function runPlanningFor(jobName) {
  const btn = $("planRunBtn");
  btn.disabled = true;
  btn.textContent = "分析中…";
  const r = await fetch("/api/planning", {
    method: "POST", headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      job: jobName,
      intersection: $("planName").value.trim() || "演示路口",
      cycle_current: +$("planCycle").value,
      greens: [+$("planGreen1").value, +$("planGreen2").value],
      lanes: +$("planLanes").value,
      sat_flow_per_lane: +$("planSat").value,
    }),
  });
  const d = await r.json();
  btn.disabled = false;
  btn.textContent = "运行规划分析";
  if (!d.ok) { alert("规划分析失败：" + (d.error || r.status)); return; }
  renderPlanning(d.result, d.report_md);
  await loadJobs();
}

$("planRunBtn").addEventListener("click", async () => {
  if (!state.job) return;
  await runPlanningFor(state.job);
});

/* ---------------- report export (P3) ---------------- */
async function exportReport(fmt) {
  if (!state.job) return;
  const st = $("expStatus");
  const btn = fmt === "xlsx" ? $("expXlsxBtn") : $("expPdfBtn");
  btn.disabled = true;
  st.textContent = "生成中…";
  try {
    const r = await fetch(`/api/job/${encodeURIComponent(state.job)}/export`, {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ format: fmt }),
    });
    const d = await r.json();
    if (!d.ok) { st.textContent = "失败：" + (d.error || r.status); return; }
    st.textContent = `已生成 ${d.file}（${(d.size / 1024).toFixed(0)} KB），见下方文件列表。`;
    await loadJob(state.job);
  } catch (e) {
    st.textContent = "失败：" + e.message;
  } finally {
    btn.disabled = false;
  }
}

$("expXlsxBtn").addEventListener("click", () => exportReport("xlsx"));
$("expPdfBtn").addEventListener("click", () => exportReport("pdf"));

function renderPlanning(a, md) {
  const el = $("planResult");
  const w = a.webster;
  let html = `<h3 class="subhead">现状通行能力与饱和度 · 周期 ${a.cycle_current} 秒</h3>
  <table><tr><th>进口道流向</th><th class="num">流量（pcu）</th><th class="num">车道</th>
  <th class="num">绿灯（秒）</th><th class="num">通行能力</th><th class="num">饱和度</th><th>服务水平</th></tr>`;
  a.table_now.forEach((r) => {
    const cls = r.x > 0.9 ? "bad" : (r.x > 0.7 ? "warn" : "");
    html += `<tr><td>${r.label}</td><td class="num">${r.pcu.toFixed(0)}</td>
      <td class="num">${r.lanes}</td><td class="num">${r.green}</td>
      <td class="num">${r.capacity}</td><td class="num"><span class="${cls}">${r.x.toFixed(2)}</span></td>
      <td>${r.los}</td></tr>`;
  });
  html += `</table>`;
  html += `<h3 class="subhead">信号配时优化</h3><div class="webster-box">
    <div class="wb"><div class="k-label">关键流量比 Y</div><div class="k-value">${w.Y}</div></div>
    <div class="wb"><div class="k-label">总损失时间</div><div class="k-value">${w.L} 秒</div></div>
    <div class="wb"><div class="k-label">建议周期</div><div class="k-value">${w.feasible ? w.cycle + " 秒" : "不可行"}</div></div>
    <div class="wb"><div class="k-label">各相位绿灯</div><div class="k-value">${w.feasible ? w.greens.join(" / ") + " 秒" : "—"}</div></div>
  </div>`;
  if (!w.feasible) html += `<div class="note red"><b>配时不可行：</b>${w.reason}</div>`;
  if (a.scenario) {
    const sc = a.scenario;
    html += `<h3 class="subhead">渠化扩容情景（+1 车道）</h3>
    <div class="note">对最饱和的「${sc.extra_lane_for}」增加 1 条进口车道：
    关键流量比 Y 由 ${sc.Y_before} 降至 ${sc.Y_after}` +
    (sc.x_after !== undefined ? `；该流向饱和度由 ${sc.x_before.toFixed(2)} 降至 ${sc.x_after.toFixed(2)}` : "") +
    `。</div>`;
  }
  html += `<h3 class="subhead">问题诊断与改造措施</h3>`;
  if (a.findings.length) {
    a.findings.forEach((f) => {
      html += `<div class="finding"><div class="f-problem">${f.problem}</div>
        <div class="f-measure">${f.measure}</div><div class="f-unit">依据：${f.unit}</div></div>`;
    });
  } else {
    html += `<div class="note">未发现超标问题：各流向饱和度均在合理区间。</div>`;
  }
  html += `<h3 class="subhead">需求预测</h3>
    <div class="note">${a.forecast.note}。实施分期：近期（配时优化 + 标志标线）→ 中期（渠化扩容，按情景测算执行）→ 远期（结合片区规划复核交通组织）。</div>`;
  html += `<h3 class="subhead">完整报告</h3><pre class="report">${md.replace(/</g, "&lt;")}</pre>`;
  el.innerHTML = html;
}

/* ---------------- init ---------------- */
loadSources();
loadParamPresets();
loadJobs();
