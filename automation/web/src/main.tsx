import React, { useEffect, useRef, useState } from "react";
import { createRoot } from "react-dom/client";
import "./styles.css";
import { eventLabel, eventListLabel, resultName } from "./eventLabels";
import { configuredValue, copyEvents, templateTitle, validateEvents } from "./eventPlan";
import { EventEditor } from "./EventEditor";
import { Icon, type IconName } from "./icons";
import type { Device, Environment, Log, Session, Task, TaskRequest, Template } from "./types";
import { desktop, evidenceUrl } from "./desktop";
import { forceStopReady } from "./taskState";

const statusName: Record<string, string> = {
  queued: "排队中", preflight: "预检中", starting: "启动中", running: "运行中", stopping: "停止中",
  completed: "已完成", failed: "失败", cancelled: "已取消", interrupted: "已中断", idle: "空闲",
  device: "已连接", unauthorized: "待授权", offline: "离线", unknown: "状态未知", external: "外部任务",
};
const sourceName: Record<string, string> = { console: "控制台", cli: "命令行", campaign: "固定采集", archive: "归档" };
const modeName = { simulate: "模拟运行", device: "真机验证", formal: "正式采集" };
const runtimeOptions = [
  ["windows-dev", "Windows · 开发"], ["ubuntu-dev", "Ubuntu · 开发"],
  ["ubuntu-lab", "Ubuntu · 正式"], ["ubuntu-speaker-lab", "Ubuntu · 音箱正式"],
];

let controlToken = "";
let defaultRuntimeId = "windows-dev";
async function api<T>(url: string, init: RequestInit = {}): Promise<T> {
  if (desktop) return desktop.request({ url, method: init.method, body: typeof init.body === "string" ? init.body : undefined }) as Promise<T>;
  const headers = new Headers(init.headers);
  if (init.body) headers.set("content-type", "application/json");
  if (init.method && init.method !== "GET") headers.set("x-control-token", controlToken);
  const response = await fetch(url, { ...init, headers });
  if (!response.ok) {
    let message = await response.text();
    try {
      const data = JSON.parse(message);
      message = typeof data.detail === "string" ? data.detail : Array.isArray(data.detail)
        ? data.detail.map((item: { msg: string }) => item.msg).join("；") : message;
    } catch { /* The server may return a plain-text error. */ }
    throw new Error(message || `请求失败 ${response.status}`);
  }
  return response.json();
}
const errorText = (error: unknown) => error instanceof Error ? error.message : String(error);
const fmtTime = (ns: number) => ns ? new Date(ns / 1e6).toLocaleString("zh-CN", { hour12: false }) : "—";
const terminal = (status: string) => ["completed", "failed", "cancelled", "interrupted"].includes(status);
const successRate = (quality?: { app_success_rate?: number } | null) => quality?.app_success_rate != null
  ? `${(quality.app_success_rate * 100).toFixed(1)}%` : "—";
const formalRuntime = (template?: Template) => template?.runtime_defaults?.runtime_id
  ?? (template?.id === "xiaomi_touchscreen_speaker_music" ? "ubuntu-speaker-lab" : "ubuntu-lab");
const taskTitle = (task: Task, templates: Template[]) => task.title || task.display_name || task.metadata?.display_name
  || templates.find(template => template.id === task.request.template_id)?.display_name || task.request.template_id;

function Badge({ value }: { value: string }) {
  return <span className={`badge ${value}`}><span />{statusName[value] || value}</span>;
}
function Empty({ children, icon = "folder" }: { children: React.ReactNode; icon?: IconName }) {
  return <div className="empty"><Icon name={icon} size={28} /><div>{children}</div></div>;
}

function App() {
  const [tab, setTab] = useState("devices");
  const [templates, setTemplates] = useState<Template[]>([]);
  const [devices, setDevices] = useState<Device[]>([]);
  const [deviceError, setDeviceError] = useState("");
  const [tasks, setTasks] = useState<Task[]>([]);
  const [sessions, setSessions] = useState<Session[]>([]);
  const [environment, setEnvironment] = useState<Environment | null>(null);
  const [toast, setToast] = useState("");
  const [ready, setReady] = useState(false);
  const [bootstrapError, setBootstrapError] = useState("");
  const [preset, setPreset] = useState<TaskRequest | null>(null);
  const [createdTaskId, setCreatedTaskId] = useState("");

  const refreshDevices = async () => {
    try {
      const result = await api<{ devices: Device[]; error: string | null }>("/api/v1/devices");
      setDevices(result.devices); setDeviceError(result.error || "");
    } catch (error) { setDeviceError(errorText(error)); }
  };
  const refreshTasks = async () => {
    try { setTasks(await api<Task[]>("/api/v1/tasks")); } catch (error) { setToast(errorText(error)); }
  };
  const refreshSessions = async () => {
    try { setSessions(await api<Session[]>("/api/v1/sessions")); } catch (error) { setToast(errorText(error)); }
  };
  const refreshEnvironment = async (runtime = defaultRuntimeId) => {
    try { setEnvironment(await api<Environment>(`/api/v1/environment?runtime_id=${encodeURIComponent(runtime)}`)); }
    catch (error) { setToast(errorText(error)); }
  };
  useEffect(() => {
    (async () => {
      const boot = await api<{ control_token: string; default_runtime_id: string }>("/api/v1/bootstrap");
      controlToken = boot.control_token || ""; defaultRuntimeId = boot.default_runtime_id;
      setTemplates(await api<Template[]>("/api/v1/experiments"));
      await Promise.all([refreshDevices(), refreshTasks(), refreshSessions(), refreshEnvironment()]);
      setReady(true);
    })().catch(error => setBootstrapError(errorText(error)));
  }, []);
  useEffect(() => {
    if (!ready) return;
    const taskTimer = setInterval(refreshTasks, 1500);
    const deviceTimer = setInterval(refreshDevices, 5000);
    const sessionTimer = setInterval(refreshSessions, 10000);
    return () => { clearInterval(taskTimer); clearInterval(deviceTimer); clearInterval(sessionTimer); };
  }, [ready]);
  useEffect(() => {
    if (!toast) return;
    const timer = setTimeout(() => setToast(""), 6000);
    return () => clearTimeout(timer);
  }, [toast]);
  const running = tasks.filter(task => !terminal(task.status)).length;
  const online = devices.filter(device => device.state === "device").length;
  const nav: { id: string; label: string; icon: IconName }[] = [
    { id: "devices", label: "设备概览", icon: "devices" }, { id: "create", label: "新建实验", icon: "create" },
    { id: "runs", label: "运行中心", icon: "runs" }, { id: "history", label: "实验记录", icon: "history" },
    { id: "settings", label: "环境设置", icon: "settings" },
  ];
  return <div className="shell">
    <aside>
      <div className="brand"><div className="mark"><Icon name="runs" size={24} /></div><strong>IoT 实验工作台</strong></div>
      <nav aria-label="工作台导航">{nav.map(item => <button key={item.id} className={tab === item.id ? "active" : ""} aria-current={tab === item.id ? "page" : undefined} onClick={() => setTab(item.id)}>
        <Icon name={item.icon} /><span>{item.label}</span>{item.id === "runs" && running > 0 && <b className="nav-count">{running}</b>}
      </button>)}</nav>
      <div className="aside-foot"><span className={`pulse ${ready ? "" : "pending"}`} />{ready ? "本机服务已连接" : bootstrapError ? "服务连接失败" : "连接服务中…"}<code>127.0.0.1</code></div>
    </aside>
    <main>
      <header><h1>{nav.find(item => item.id === tab)?.label}</h1><div className="header-stats"><span><i className="status-dot" />{online} 台手机</span><span><Icon name="runs" size={15} />{running} 个运行任务</span></div></header>
      {bootstrapError ? <div className="panel"><div className="alert error">{bootstrapError}</div><button className="secondary" onClick={() => location.reload()}>重新连接</button></div> : !ready ? <Empty>正在加载工作台…</Empty> : <>
        {tab === "devices" && <Devices devices={devices} error={deviceError} templates={templates} onRefresh={refreshDevices} onCreate={id => { setPreset(blank(templates.find(template => template.id === id))); setTab("create"); }} />}
        {tab === "create" && <Create templates={templates} devices={devices} preset={preset} onCreated={created => {
          setTasks(current => [...created, ...current.filter(task => !created.some(item => item.id === task.id))]);
          setCreatedTaskId(created[0]?.id || ""); setTab("runs");
        }} notify={setToast} />}
        {tab === "runs" && <Runs tasks={tasks} templates={templates} initialTaskId={createdTaskId} notify={setToast} />}
        {tab === "history" && <History sessions={sessions} tasks={tasks} onRefresh={refreshSessions} notify={setToast} onReuse={value => { setPreset(value); setTab("create"); }} />}
        {tab === "settings" && <Settings data={environment} onRefresh={refreshEnvironment} />}
      </>}
    </main>
    {toast && <div className="toast" role="status"><span>{toast}</span><button aria-label="关闭提示" onClick={() => setToast("")}>×</button></div>}
  </div>;
}

function Devices({ devices, error, templates, onRefresh, onCreate }: {
  devices: Device[]; error: string; templates: Template[]; onRefresh: () => void; onCreate: (id: string) => void;
}) {
  return <section>
    <div className="toolbar"><div className="section-title"><h2>连接的手机</h2><span className="count">{devices.length}</span></div><button className="secondary" onClick={onRefresh}><Icon name="refresh" size={16} />刷新</button></div>
    {error && <div className="alert error" role="alert">{error}</div>}
    <div className="cards">{devices.length ? devices.map(device => <article className="device-card" key={device.udid}>
      <div className="card-top"><div className="phone-icon"><Icon name="devices" size={25} /></div><Badge value={device.state} /></div>
      <h3>{device.manufacturer || "Android"} {device.model || "未知型号"}</h3><code>{device.udid}</code>
      <dl><div><dt>Android</dt><dd>{device.android_version || "—"}<span className="muted"> / SDK {device.sdk_level || "—"}</span></dd></div>
        <div><dt>App 版本</dt><dd>{device.app_version || "未检测到"}</dd></div>
        <div><dt>任务占用</dt><dd>{statusName[device.busy_status] || device.busy_status}</dd></div>
        {device.last_observation && <div><dt>最近 App 状态</dt><dd>{device.last_observation.state}<small>{device.last_observation.observed_at_ns ? fmtTime(device.last_observation.observed_at_ns) : "时间未知"}</small></dd></div>}
      </dl>
    </article>) : <div className="panel wide"><Empty icon="devices">未发现手机。连接 USB 数据线后，请在手机上授权 USB 调试。</Empty></div>}</div>
    <div className="section-gap"><div className="toolbar"><div className="section-title"><h2>实验模板</h2><span className="count">{templates.length}</span></div></div>
      <div className="template-grid">{templates.map(template => <article className="template-card" key={template.id}>
        <div className="template-meta"><span className="tag">{template.events.some(event => event.target != null) ? "参数与模式" : "开关与状态"}</span><span className="muted">{template.app.vendor}</span></div>
        <h3>{template.display_name}</h3><div className="event-tags">{[...new Set(template.events.map(event => event.event_type))].map(type => <span key={type}>{eventLabel({ event_type: type, target: template.events.find(event => event.event_type === type)?.target }).split("→")[0]}</span>)}</div>
        <div className="template-foot"><code>{template.device_id}</code><button className="link" disabled={!template.adapter_available} onClick={() => onCreate(template.id)}>新建实验<Icon name="arrow" size={16} /></button></div>
      </article>)}</div>
    </div>
  </section>;
}

function blank(template?: Template): TaskRequest {
  return {
    template_id: template?.id || "", runtime_id: defaultRuntimeId, mode: "simulate", udid: configuredValue(template?.phone_udid),
    repetitions: template?.defaults.repetitions_per_event ?? 1,
    idle_min_seconds: template?.defaults.idle_range_seconds[0] ?? 1, idle_max_seconds: template?.defaults.idle_range_seconds[1] ?? 3,
    cooldown_seconds: template?.defaults.cooldown_seconds ?? 1, seed: null,
    target_device_ip: configuredValue(template?.network.target_device_ip),
    capture_interface: configuredValue(template?.runtime_defaults?.capture_interface),
    capture_filter: configuredValue(template?.runtime_defaults?.capture_filter),
    pre_roll_seconds: null, post_roll_seconds: null, events: copyEvents(template?.events ?? []),
  };
}

function Create({ templates, devices, preset, onCreated, notify }: {
  templates: Template[]; devices: Device[]; preset: TaskRequest | null; onCreated: (created: Task[]) => void; notify: (message: string) => void;
}) {
  const [form, setForm] = useState<TaskRequest>(() => blank(templates[0]));
  const [queue, setQueue] = useState<TaskRequest[]>([]);
  const [checking, setChecking] = useState(false);
  const [submitting, setSubmitting] = useState(false);
  const [formError, setFormError] = useState("");
  const [preflightResult, setPreflightResult] = useState<{ ok: boolean; checks?: { name: string; ok: boolean; detail?: unknown }[] } | null>(null);
  const template = templates.find(item => item.id === form.template_id);
  const events = form.events ?? template?.events ?? [];
  const online = devices.filter(device => device.state === "device");
  useEffect(() => { if (preset) { setForm({ ...preset, events: preset.events ? copyEvents(preset.events) : null }); setFormError(""); setPreflightResult(null); } }, [preset]);
  const patch = (value: Partial<TaskRequest>) => { setForm(current => ({ ...current, ...value })); setFormError(""); setPreflightResult(null); };
  const changeTemplate = (id: string) => {
    const next = templates.find(item => item.id === id);
    const defaults = blank(next);
    patch({ ...defaults, mode: form.mode, udid: form.udid || defaults.udid, runtime_id: form.mode === "formal" ? formalRuntime(next) : defaultRuntimeId });
  };
  const changeMode = (mode: TaskRequest["mode"]) => patch({ mode, runtime_id: mode === "formal" ? formalRuntime(template) : defaultRuntimeId });
  const validate = (item: TaskRequest) => {
    const selected = templates.find(entry => entry.id === item.template_id);
    if (!selected) throw new Error("请选择实验模板");
    if (!selected.adapter_available) throw new Error("该模板的适配器尚未接入");
    if (item.mode !== "simulate" && !devices.some(device => device.state === "device" && device.udid === item.udid)) throw new Error("请选择已连接的手机");
    if (!Number.isInteger(item.repetitions) || item.repetitions < 1 || item.repetitions > 10000) throw new Error("每个目标的次数应为 1–10000 的整数");
    const plan = item.events ?? selected.events;
    const eventError = validateEvents(plan, selected.parameters);
    if (eventError) throw new Error(eventError);
    if (item.mode === "formal" && ![item.target_device_ip, item.capture_interface, item.capture_filter].every(value => configuredValue(value))) throw new Error("请填写目标设备 IP、抓包接口和过滤器");
    const timings = [item.idle_min_seconds, item.idle_max_seconds, item.cooldown_seconds, item.pre_roll_seconds ?? 0, item.post_roll_seconds ?? 0];
    if (timings.some(value => !Number.isFinite(value) || value < 0 || value > 3600)) throw new Error("等待与保护时间应为 0–3600 秒");
    if (item.idle_max_seconds < item.idle_min_seconds) throw new Error("最长等待应不小于最短等待");
    if (item.seed != null && (!Number.isInteger(item.seed) || item.seed < 1 || item.seed >= 2 ** 31)) throw new Error("随机种子应为 1–2147483647 的整数");
  };
  const preflight = async () => {
    try {
      validate(form); setChecking(true); setFormError("");
      const result = await api<{ ok: boolean; checks?: { name: string; ok: boolean; detail?: unknown }[] }>("/api/v1/preflights", { method: "POST", body: JSON.stringify(form) });
      setPreflightResult(result);
    } catch (error) { setFormError(errorText(error)); } finally { setChecking(false); }
  };
  const add = () => {
    try { validate(form); setQueue(current => [...current, { ...form, events: copyEvents(events) }]); notify("已加入批次"); }
    catch (error) { setFormError(errorText(error)); }
  };
  const submit = async () => {
    if (checking || submitting) return;
    const all = queue.length ? queue : [form];
    try {
      all.forEach(validate); setSubmitting(true); setFormError("");
      const created = await api<Task[]>("/api/v1/tasks", { method: "POST", body: JSON.stringify({ client_request_id: crypto.randomUUID(), tasks: all }) });
      setQueue([]); onCreated(created);
    } catch (error) { setFormError(errorText(error)); } finally { setSubmitting(false); }
  };
  const planned = events.length * form.repetitions;
  const hasScenes = events.some(event => event.event_type === "select_scene");
  const hasSliders = events.some(event => ["set_brightness", "set_color_temperature"].includes(event.event_type));
  const singleTargetTypes = [...new Set(events.filter(event => event.target != null).map(event => event.event_type))]
    .filter(type => events.filter(event => event.event_type === type).length === 1);
  const missingReturnTransition = events.some(event => event.expected_state && !events.some(next => next.required_state === event.expected_state && next.expected_state));
  return <section className="two-col">
    <div className="panel create-panel">
      <div className="form-section">
        <h2><span className="step-number">1</span>基本配置</h2>
        <div className="form-grid">
          <label className="wide">实验模板<select value={form.template_id} onChange={event => changeTemplate(event.target.value)}>{templates.map(item => <option key={item.id} value={item.id} disabled={!item.adapter_available}>{templateTitle(item)}</option>)}</select></label>
          <fieldset className="wide mode-field"><legend>运行方式</legend><div className="mode-options">{(["simulate", "device", "formal"] as const).map(mode => <button type="button" key={mode} className={form.mode === mode ? "selected" : ""} aria-pressed={form.mode === mode} onClick={() => changeMode(mode)}><Icon name={mode === "simulate" ? "file" : mode === "device" ? "devices" : "runs"} size={18} />{modeName[mode]}</button>)}</div></fieldset>
          {form.mode !== "simulate" && <label className="wide">运行手机<select value={form.udid || ""} onChange={event => patch({ udid: event.target.value || null })}><option value="">请选择手机</option>{online.map(device => <option key={device.udid} value={device.udid}>{device.manufacturer} {device.model} · {device.udid}</option>)}{form.udid && !online.some(device => device.udid === form.udid) && <option value={form.udid} disabled>{form.udid} · 未连接</option>}</select></label>}
          <label>每个事件目标的次数<input type="number" min="1" max="10000" step="1" value={form.repetitions} onChange={event => patch({ repetitions: Number(event.target.value) })} /></label>
          <div className="planned-count"><span>计划操作</span><strong>{Number.isFinite(planned) ? planned : "—"}<small> 次</small></strong></div>
        </div>
        {form.mode === "formal" && <div className="capture-config"><h3>抓包配置</h3><div className="form-grid">
          <label>目标设备 IP<input placeholder="192.168.1.20" value={form.target_device_ip || ""} onChange={event => patch({ target_device_ip: event.target.value || null })} /></label>
          <label>抓包接口<input placeholder="wlp2s0" value={form.capture_interface || ""} onChange={event => patch({ capture_interface: event.target.value || null })} /></label>
          <label className="wide">抓包过滤器<input placeholder="host 192.168.1.20" value={form.capture_filter || ""} onChange={event => patch({ capture_filter: event.target.value || null })} /></label>
        </div><p className="field-hint">手机需与 IoT 实验网络隔离。启动时自动执行正式预检。</p></div>}
      </div>
      <div className="form-section">
        <div className="toolbar"><h2><span className="step-number">2</span>事件与目标</h2><button className="link small" onClick={() => patch({ events: copyEvents(template?.events ?? []) })}>恢复模板</button></div>
        {template && <EventEditor template={template} events={events} onChange={value => patch({ events: value })} />}
        {form.repetitions > 1 && (singleTargetTypes.length > 0 || missingReturnTransition) && <p className="field-note">重复切换需要可往返的事件或至少两个不同目标；单向计划可能提前结束会话。</p>}
        {hasScenes && hasSliders && <p className="field-note">情景模式会接管亮度和色温。混合计划将先执行滑块目标，再执行情景切换。</p>}
      </div>
      <details className="advanced"><summary>高级配置<span>可选</span><Icon name="chevron" size={16} /></summary><div className="form-grid">
        <label>最短等待（秒）<input type="number" min="0" max="3600" step="0.1" value={form.idle_min_seconds} onChange={event => patch({ idle_min_seconds: Number(event.target.value) })} /></label>
        <label>最长等待（秒）<input type="number" min="0" max="3600" step="0.1" value={form.idle_max_seconds} onChange={event => patch({ idle_max_seconds: Number(event.target.value) })} /></label>
        <label>操作后等待（秒）<input type="number" min="0" max="3600" step="0.1" value={form.cooldown_seconds} onChange={event => patch({ cooldown_seconds: Number(event.target.value) })} /></label>
        <label>随机种子<input type="number" min="1" max="2147483647" placeholder="自动生成" value={form.seed ?? ""} onChange={event => patch({ seed: event.target.value ? Number(event.target.value) : null })} /></label>
        <label className="wide">运行环境<select value={form.runtime_id} onChange={event => patch({ runtime_id: event.target.value })}>{runtimeOptions.map(([id, name]) => <option key={id} value={id}>{name}</option>)}</select></label>
        {form.mode === "formal" && <><label>抓包前保护（秒）<input type="number" min="0" max="3600" step="0.1" placeholder={String(template?.runtime_defaults?.pre_roll_seconds ?? 5)} value={form.pre_roll_seconds ?? ""} onChange={event => patch({ pre_roll_seconds: event.target.value ? Number(event.target.value) : null })} /></label><label>抓包后保护（秒）<input type="number" min="0" max="3600" step="0.1" placeholder={String(template?.runtime_defaults?.post_roll_seconds ?? 5)} value={form.post_roll_seconds ?? ""} onChange={event => patch({ post_roll_seconds: event.target.value ? Number(event.target.value) : null })} /></label></>}
        {template?.network.forbidden_cidrs?.length ? <div className="wide field-hint">手机禁止网段：{template.network.forbidden_cidrs.join("、")}</div> : null}
      </div></details>
      {formError && <div className="alert error" role="alert">{formError}</div>}
      {preflightResult && <div className={`alert ${preflightResult.ok ? "success" : "error"}`} role="status"><b>{preflightResult.ok ? "预检通过" : "预检未通过"}</b>{!preflightResult.ok && <ul>{preflightResult.checks?.filter(check => !check.ok).map(check => <li key={check.name}>{check.name}{check.detail != null ? `：${typeof check.detail === "string" ? check.detail : JSON.stringify(check.detail)}` : ""}</li>)}</ul>}</div>}
      <div className="form-footer"><button className="secondary" disabled={checking || submitting} onClick={preflight}>{checking ? "检查中…" : "检查配置"}</button><div className="actions"><button className="secondary" disabled={checking || submitting || queue.length >= 16} onClick={add}>加入批次</button><button className="primary" disabled={checking || submitting} onClick={submit}><Icon name="runs" size={16} />{submitting ? "提交中…" : queue.length ? `启动批次 (${queue.length})` : "启动实验"}</button></div></div>
    </div>
    <div className="summary-stack">
      <div className="panel summary"><div className="summary-heading"><h2>运行预览</h2><span className="tag">{modeName[form.mode]}</span></div><h3 className="experiment-name">{template?.display_name || "请选择模板"}</h3><code>{template?.id}</code>
        <dl><div><dt>手机</dt><dd>{form.mode === "simulate" ? "模拟设备" : form.udid || "未选择"}</dd></div><div><dt>事件目标</dt><dd>{events.length} 个</dd></div><div><dt>每个目标</dt><dd>{form.repetitions} 次</dd></div><div><dt>计划操作</dt><dd>{planned} 次</dd></div>{form.mode === "formal" && <div><dt>抓包接口</dt><dd>{form.capture_interface || "未填写"}</dd></div>}</dl>
        <div className="plan-preview">{events.map((event, index) => <div key={index}><span>{eventLabel(event)}</span><small>× {form.repetitions}</small></div>)}</div>
        {form.mode === "simulate" && <p className="field-hint">模拟运行不连接手机。</p>}{form.mode === "device" && <p className="field-hint">将操作手机，不启动抓包。</p>}
      </div>
      {queue.length > 0 && <div className="panel batch"><div className="summary-heading"><h2>待启动批次</h2><span className="count">{queue.length}</span></div>{queue.map((item, index) => <div className="queue-item" key={index}><div><b>{templates.find(entry => entry.id === item.template_id)?.display_name}</b><small>{modeName[item.mode]} · {item.repetitions * (item.events?.length ?? templates.find(entry => entry.id === item.template_id)?.events.length ?? 0)} 次</small></div><button className="icon-button" aria-label={`移除批次第 ${index + 1} 项`} onClick={() => setQueue(current => current.filter((_, i) => i !== index))}>×</button></div>)}<p className="field-hint">启动批次只提交以上任务。</p></div>}
    </div>
  </section>;
}

function Runs({ tasks, templates, initialTaskId, notify }: { tasks: Task[]; templates: Template[]; initialTaskId: string; notify: (message: string) => void }) {
  const [selected, setSelected] = useState(initialTaskId);
  const [logs, setLogs] = useState<Log[]>([]);
  const [paused, setPaused] = useState(false);
  const [level, setLevel] = useState("all");
  const [query, setQuery] = useState("");
  const [filter, setFilter] = useState("all");
  const cursor = useRef(0);
  const consoleEnd = useRef<HTMLDivElement>(null);
  const visibleTasks = tasks.filter(task => filter === "all" || (filter === "active" ? !terminal(task.status) : terminal(task.status)));
  const task = visibleTasks.find(item => item.id === selected) || visibleTasks[0];
  useEffect(() => { setSelected(task?.id || ""); }, [task?.id]);
  useEffect(() => { cursor.current = 0; setLogs([]); }, [selected]);
  useEffect(() => {
    if (!selected || paused) return;
    let cancelled = false;
    let loading = false;
    const load = async () => {
      if (loading) return;
      loading = true;
      try {
        const rows = await api<Log[]>(`/api/v1/tasks/${encodeURIComponent(selected)}/logs?after=${cursor.current}`);
        if (!cancelled && rows.length) { cursor.current = rows.at(-1)!.id; setLogs(current => [...current, ...rows].slice(-1000)); }
      } catch (error) { if (!cancelled) notify(errorText(error)); }
      finally { loading = false; }
    };
    load(); const timer = setInterval(load, 1500);
    return () => { cancelled = true; clearInterval(timer); };
  }, [selected, paused]);
  useEffect(() => { if (!paused) consoleEnd.current?.scrollIntoView({ block: "nearest" }); }, [logs, paused]);
  const mutate = async (action: string) => {
    if (!task) return;
    try { await api(`/api/v1/tasks/${encodeURIComponent(task.id)}/${action}`, { method: "POST" }); notify(action === "stop" ? "已请求安全停止" : "已强制终止任务"); }
    catch (error) { notify(errorText(error)); }
  };
  const downloadLogs = () => {
    if (!task) return;
    const blob = new Blob([logs.map(log => `${fmtTime(log.created_at_ns)} [${log.stage}] ${log.message}`).join("\n")], { type: "text/plain" });
    const link = document.createElement("a"); const url = URL.createObjectURL(blob);
    link.href = url; link.download = `${task.id}.log`; link.click(); setTimeout(() => URL.revokeObjectURL(url), 1000);
  };
  const shown = logs.filter(log => (level === "all" || log.level === level) && (!query || `${log.stage} ${log.message}`.toLowerCase().includes(query.toLowerCase())));
  const forceReady = forceStopReady(task);
  const taskEvents = task?.metadata?.is_campaign ? [] : task?.request.events ?? templates.find(template => template.id === task?.request.template_id)?.events ?? [];
  return <section className="runs-layout"><div className="task-list">
    <div className="task-list-head"><h2>实验任务</h2><select aria-label="任务筛选" value={filter} onChange={event => setFilter(event.target.value)}><option value="all">全部</option><option value="active">进行中</option><option value="finished">已结束</option></select></div>
    {visibleTasks.length ? visibleTasks.map(item => <button key={item.id} className={item.id === task?.id ? "selected" : ""} onClick={() => setSelected(item.id)}><div className="task-row-head"><span className="task-source">{sourceName[item.source || "console"] || item.source}</span><Badge value={item.status} /></div><b>{taskTitle(item, templates)}</b><small>{item.session_id || item.id.slice(0, 12)}</small><progress max={Math.max(item.planned_events, 1)} value={item.completed_events} aria-label="任务进度" /><span className="task-progress">{item.completed_events} / {item.planned_events}</span></button>) : <Empty>没有实验任务。</Empty>}
  </div>{task ? <div className="run-detail panel">
    <div className="run-head"><div><div className="actions"><Badge value={task.status} /><span className="muted small">{sourceName[task.source || "console"] || task.source}</span></div><h2>{taskTitle(task, templates)}</h2><code>{task.session_id || task.id}</code></div>{!terminal(task.status) && task.controllable !== false && <div className="actions"><button className="danger-soft" onClick={() => mutate("stop")}>安全停止</button>{forceReady && <button className="danger" onClick={() => mutate("force-stop")}>强制终止</button>}</div>}</div>
    {task.queue_reason && <div className="alert">{task.queue_reason}</div>}{task.controllable === false && !terminal(task.status) && <p className="field-note">此任务在工作台外启动，请通过原启动终端停止。</p>}
    {task.status === "stopping" && !forceReady && <div className="alert">正在清理资源。60 秒后可强制终止。</div>}{task.error && <div className="alert error">{task.error}</div>}
    <div className="metrics"><div><strong>{task.completed_events}<small> / {task.planned_events}</small></strong><span>已完成操作</span></div><div><strong>{successRate(task.quality)}</strong><span>App 回执成功率</span></div><div><strong>{modeName[task.request.mode] || "外部采集"}</strong><span>运行方式</span></div></div>
    {taskEvents.length > 0 && <details className="plan-details"><summary>事件计划<span>{taskEvents.length} 个目标 · 每个 {task.request.repetitions} 次</span></summary><p>{eventListLabel(taskEvents)}</p></details>}
    {task.quality?.result_counts && <div className="results">{Object.entries(task.quality.result_counts).map(([name, count]) => <span key={name}>{resultName[name] || name}<b>{count}</b></span>)}</div>}
    <div className="log-head"><h3>运行日志</h3><div className="actions"><input className="log-search" aria-label="筛选日志" placeholder="搜索日志" value={query} onChange={event => setQuery(event.target.value)} /><select aria-label="日志级别" value={level} onChange={event => setLevel(event.target.value)}><option value="all">全部级别</option><option value="info">信息</option><option value="warning">警告</option><option value="error">错误</option></select><button className="secondary small" onClick={() => setPaused(!paused)}>{paused ? "继续更新" : "暂停更新"}</button><button className="secondary small" onClick={downloadLogs}>导出日志</button></div></div>
    <div className="console">{shown.length ? shown.map(log => <div key={log.id} className={log.level}><time>{fmtTime(log.created_at_ns)}</time><span>{log.stage}</span><p>{log.message}</p></div>) : <p className="muted console-empty">{paused ? "日志更新已暂停" : "暂无日志"}</p>}<div ref={consoleEnd} /></div>
  </div> : <div className="panel"><Empty icon="runs">启动实验后，在此查看进度和日志。</Empty></div>}</section>;
}

const artifactLabels: Record<string, string> = {
  "session.yaml": "会话配置", "quality_report.json": "质量报告", "actions.jsonl": "动作记录", "run_journal.jsonl": "运行记录",
  "appium.log": "Appium 日志", "traffic.pcapng": "网络抓包", "ha_reconciliation.json": "HA 对账", "pcap_review.json": "抓包检查",
  "ha_history.json": "HA 历史", "ha_clock_probe.json": "时钟测量", "capture_plan.json": "采集计划", "acquisition_report.json": "采集报告",
  "campaign.json": "采集进度", "network_isolation_check.json": "网络预检", "clock_sync.json": "时钟信息",
};
function artifactUrl(sessionId: string, file: string, kind: "artifacts" | "evidence") {
  return `/api/v1/sessions/${encodeURIComponent(sessionId)}/${kind}/${file.split("/").map(encodeURIComponent).join("/")}`;
}

function TextPreview({ url }: { url: string }) {
  const [content, setContent] = useState<{ text: string; truncated: boolean; size_bytes: number } | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  useEffect(() => {
    const controller = new AbortController();
    setContent(null); setError(""); setLoading(true);
    api<{ text: string; truncated: boolean; size_bytes: number }>(`${url}?preview=true`, { signal: controller.signal })
      .then(value => { if (!controller.signal.aborted) setContent(value); })
      .catch(reason => { if (!controller.signal.aborted) setError(errorText(reason)); })
      .finally(() => { if (!controller.signal.aborted) setLoading(false); });
    return () => controller.abort();
  }, [url]);
  if (loading) return <Empty icon="file">正在加载文件…</Empty>;
  if (error) return <div className="text-preview-error alert error" role="alert">{error}</div>;
  return <>
    {content?.truncated && <p className="preview-limit" role="status">当前显示前 2 MiB，文件共 {(content.size_bytes / 1024 / 1024).toFixed(1)} MiB。可用本机应用打开完整内容。</p>}
    <pre className="text-preview">{content?.text || "文件为空。"}</pre>
  </>;
}

function History({ sessions, tasks, onRefresh, onReuse, notify }: {
  sessions: Session[]; tasks: Task[]; onRefresh: () => void; onReuse: (request: TaskRequest) => void; notify: (message: string) => void;
}) {
  const [query, setQuery] = useState("");
  const [quality, setQuality] = useState("all");
  const [selected, setSelected] = useState("");
  const [preview, setPreview] = useState<{ file: string; kind: "artifacts" | "evidence" } | null>(null);
  const filtered = sessions.filter(session => (!query || `${session.session_id} ${session.display_name || ""} ${session.phone_udid || ""}`.toLowerCase().includes(query.toLowerCase()))
    && (quality === "all" || (quality === "ok" ? session.validation?.ok : session.validation?.ok === false)));
  const session = filtered.find(item => item.session_id === selected);
  useEffect(() => { setPreview(null); }, [selected]);
  const openLocal = async (file: string, kind: "artifacts" | "evidence") => {
    if (!session) return;
    try { if (desktop) await desktop.openArtifact({ sessionId: session.session_id, category: kind, relativePath: file }); else await api(`${artifactUrl(session.session_id, file, kind)}/open`, { method: "POST" }); notify("已用本机应用打开"); }
    catch (error) { notify(errorText(error)); }
  };
  const openDirectory = async () => {
    if (!session) return;
    try { if (desktop) await desktop.openDirectory(session.session_id); else await api(`/api/v1/sessions/${encodeURIComponent(session.session_id)}/open`, { method: "POST" }); notify("已打开会话目录"); }
    catch (error) { notify(errorText(error)); }
  };
  const source = session && tasks.find(task => task.session_id === session.session_id);
  const previewUrl = session && preview ? artifactUrl(session.session_id, preview.file, preview.kind) : null;
  const isText = preview && /\.(jsonl?|ya?ml|xml|log|txt|md|csv|tsv)$/i.test(preview.file);
  const canPreview = preview && (/\.(png|jpe?g|webp|gif|svg|pdf)$/i.test(preview.file) || isText);
  return <section>
    <div className="toolbar history-toolbar"><div className="section-title"><h2>实验会话</h2><span className="count">{filtered.length}</span></div><div className="actions"><input aria-label="搜索实验会话" placeholder="搜索会话、设备或手机" value={query} onChange={event => setQuery(event.target.value)} /><select aria-label="会话校验筛选" value={quality} onChange={event => setQuality(event.target.value)}><option value="all">全部结果</option><option value="ok">校验通过</option><option value="bad">校验失败</option></select><button className="secondary" onClick={onRefresh}><Icon name="refresh" size={16} />刷新</button></div></div>
    <div className="table sessions"><div className="tr head"><span>会话 / 实验</span><span>更新时间</span><span>App 回执</span><span>校验</span><span /></div>{filtered.length ? filtered.map(item => <button className={`tr session-row ${item.session_id === selected ? "selected" : ""}`} key={item.session_id} onClick={() => setSelected(item.session_id === selected ? "" : item.session_id)}><span><b>{item.display_name || item.experiment_id || item.session_id}</b><small>{item.session_id}</small>{item.phone_udid && <code>{item.phone_udid}</code>}</span><span>{fmtTime(item.updated_at_ns)}</span><span>{successRate(item.quality)}</span><span className={item.validation?.ok ? "validation-ok" : "muted"}>{item.validation ? item.validation.ok ? "通过" : "未通过" : "待校验"}</span><span className="session-open">查看文件<Icon name="chevron" size={16} /></span></button>) : <Empty>没有匹配的实验会话。</Empty>}</div>
    {session && <div className="panel session-detail section-gap"><div className="toolbar"><div><h2>{session.session_id}</h2>{session.status && <Badge value={session.status} />}</div><div className="actions"><button className="secondary" onClick={openDirectory}><Icon name="folder" size={16} />打开目录</button>{source && source.controllable !== false && source.source !== "campaign" && !source.metadata?.is_campaign && <button className="secondary" onClick={() => onReuse(source.request)}>使用此配置新建</button>}<button className="icon-button" aria-label="关闭会话文件" onClick={() => setSelected("")}>×</button></div></div><div className="evidence-layout"><div className="file-list">
      <h3>会话文件</h3>{session.artifacts.map(file => <button key={file} className={preview?.file === file && preview.kind === "artifacts" ? "selected" : ""} onClick={() => setPreview({ file, kind: "artifacts" })}><Icon name="file" size={16} /><span>{artifactLabels[file] || file}<small>{artifactLabels[file] ? file : ""}</small></span></button>)}
      {session.evidence.length > 0 && <><h3>截图与页面证据</h3>{session.evidence.map(file => <button key={file} className={preview?.file === file && preview.kind === "evidence" ? "selected" : ""} onClick={() => setPreview({ file, kind: "evidence" })}><Icon name="file" size={16} /><span>{file}</span></button>)}</>}
    </div><div className="file-preview">{preview && previewUrl ? <><div className="preview-toolbar"><code>{preview.file}</code><div className="actions"><button className="secondary small" onClick={() => openLocal(preview.file, preview.kind)}>本机打开</button>{canPreview && (desktop ? <button className="secondary small" onClick={() => desktop!.previewArtifact({ sessionId: session.session_id, category: preview.kind, relativePath: preview.file }).catch(error => notify(errorText(error)))}>新窗口</button> : <a className="secondary small" href={previewUrl} target="_blank" rel="noreferrer">新窗口</a>)}<a className="secondary small" href={`${evidenceUrl(previewUrl)}?download=true`}>下载</a></div></div>{canPreview ? isText ? <TextPreview key={previewUrl} url={previewUrl} /> : /\.(png|jpe?g|webp|gif|svg)$/i.test(preview.file) ? <div className="image-preview"><img src={evidenceUrl(previewUrl)} alt={preview.file} /></div> : <iframe key={previewUrl} title={`预览 ${preview.file}`} src={evidenceUrl(previewUrl)} sandbox="allow-same-origin" /> : <Empty icon="file">此文件可用本机应用打开。</Empty>}</> : <Empty icon="file">选择文件查看。</Empty>}</div></div></div>}
  </section>;
}

const checkLabels: Record<string, string> = { adb: "Android 调试工具", java: "Java", node: "Node.js", appium: "Appium", dumpcap: "抓包工具", android_sdk_root: "Android SDK" };
const capabilityStates: Record<string, string> = { ready: "就绪", prepared: "工具已准备", missing: "待准备", unavailable: "不可用", permission_denied: "权限不足", checksum_failed: "校验失败" };
function Settings({ data, onRefresh }: { data: Environment | null; onRefresh: (runtime?: string) => void }) {
  const [runtime, setRuntime] = useState(defaultRuntimeId);
  const [desktopStatus, setDesktopStatus] = useState<{ workspace: string; bundle: string } | null>(null);
  const [setupMessage, setSetupMessage] = useState("");
  useEffect(() => { desktop?.status().then(setDesktopStatus).catch(error => setSetupMessage(errorText(error))); }, []);
  const setup = async (kind: "sdk" | "dumpcap" | "roots" | "workspace" | "import", detectedSdk?: string) => {
    if (!desktop) return;
    try {
      if (kind === "roots") await desktop.registerRoot();
      else if (kind === "workspace") await desktop.chooseWorkspace();
      else if (kind === "import") await desktop.importWorkspace();
      else { const outcome = await desktop.configureTool(kind, detectedSdk) as { cancelled?: boolean }; if (outcome?.cancelled) return; }
      setSetupMessage("配置已更新"); onRefresh(runtime);
    } catch (error) { setSetupMessage(errorText(error)); }
  };
  const preparation = <div className="panel section-gap"><h2>环境准备</h2><div className="check-grid">{data?.capabilities?.map(item => <article key={item.key} className={["ready", "prepared"].includes(item.state) ? "ok" : "fail"}><div><b>{item.name} · {capabilityStates[item.state] || item.state}</b><small>{item.detail}</small></div></article>)}</div>{data?.discovery_roots?.length ? <div><h3>已登记实验目录</h3>{data.discovery_roots.map(item => <p key={item.path}><code>{item.path}</code><br /><span className={item.available ? "muted" : "alert"}>{item.available ? "可读取" : item.reason}</span></p>)}</div> : null}<details><summary>Android SDK 与手机</summary><p>使用官方 Android Studio 的 SDK Manager 准备 Platform Tools 和 Build Tools。当前 Windows 验证使用 Platform Tools 36.0.0、Build Tools 36.1.0；Linux SDK 仍待实机验证。UiAutomator2 6.9.3 要求 Android 8 / API 26 及以上。</p><p>记录 SDK 目录，完成官方许可和手机 USB 调试授权后，点击“选择 Android SDK”。手机应通过移动数据或公共 Wi-Fi 与 IoT 实验网隔离，关闭 USB 网络共享。</p>{desktop && <div className="actions"><button className="secondary" onClick={() => desktop!.openPreparation("sdk").catch(error => setSetupMessage(errorText(error)))}>官方 SDK 准备</button><button className="secondary" onClick={() => desktop!.openPreparation("usb").catch(error => setSetupMessage(errorText(error)))}>USB 与驱动指南</button></div>}</details><details><summary>抓包工具与权限</summary><p>Windows 使用官方 Wireshark / Npcap，由管理员完成驱动安装。Ubuntu 使用系统 wireshark-common，管理员配置抓包权限后重新登录。桌面应用按普通用户运行。</p><p>选择 Dumpcap 后检查接口。本次正式启动仍会检查目标 IP、接口、过滤器、网络隔离和可用空间。工具缺失时可以查看记录和模拟。</p>{desktop && <button className="secondary" onClick={() => desktop!.openPreparation("capture").catch(error => setSetupMessage(errorText(error)))}>官方抓包工具</button>}</details></div>;
  return <section>{preparation}{desktop && <div className="panel section-gap"><h2>桌面运行环境</h2>{data?.sdk_candidates?.map(candidate => <div className="field-hint" key={candidate.path}><b>已检测到 Android SDK</b><br /><code>{candidate.path}</code><div className="actions"><button className="secondary" onClick={() => setup("sdk", candidate.path)}>使用此 SDK</button></div></div>)}{desktopStatus && <p className="field-hint">工作区：{desktopStatus.workspace}<br />运行时：{desktopStatus.bundle}</p>}<div className="actions"><button className="secondary" onClick={() => setup("sdk")}>选择 Android SDK</button><button className="secondary" onClick={() => setup("dumpcap")}>选择 Dumpcap</button><button className="secondary" onClick={() => setup("roots")}>登记历史实验目录</button><button className="secondary" onClick={() => setup("workspace")}>切换工作区</button><button className="secondary" onClick={() => setup("import")}>导入旧工作区</button></div><p className="field-hint">Python、Node、Java 和 Appium 已内置。Android SDK / ADB 使用本机 SDK：可复用上方检测到的目录，也可手动选择。手机需完成 USB 授权；正式抓包需配置抓包工具。</p>{setupMessage && <div className="alert" role="status">{setupMessage}</div>}</div>}<div className="toolbar"><div className="section-title"><h2>依赖检查</h2>{data && <span className={`tag ${data.checks.every(check => check.ok) ? "good" : ""}`}>{data.checks.filter(check => check.ok).length} / {data.checks.length} 通过</span>}</div><div className="actions"><select aria-label="检查运行环境" value={runtime} onChange={event => { setRuntime(event.target.value); onRefresh(event.target.value); }}>{runtimeOptions.map(([id, name]) => <option key={id} value={id}>{name}</option>)}</select><button className="secondary" onClick={() => onRefresh(runtime)}><Icon name="refresh" size={16} />重新检查</button></div></div>{data ? <><div className="check-grid">{data.checks.map(check => <article className={check.ok ? "ok" : "fail"} key={check.name}><span>{check.ok ? <Icon name="check" size={17} /> : "!"}</span><div><b>{checkLabels[check.name] || check.name}</b><small>{typeof check.detail === "string" ? check.detail : JSON.stringify(check.detail)}</small></div></article>)}</div><div className="panel section-gap"><h2>抓包接口</h2>{data.capture_interfaces.length ? <ul className="interfaces">{data.capture_interfaces.map(item => <li key={item}>{item}</li>)}</ul> : <p className="muted">未检测到可用抓包接口。</p>}</div></> : <Empty>正在检查运行环境…</Empty>}</section>;
}

createRoot(document.getElementById("root")!).render(<React.StrictMode><App /></React.StrictMode>);
