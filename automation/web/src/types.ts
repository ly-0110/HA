import type { TemplateEvent } from "./eventLabels";

export type NumericParameter = { range: [number, number]; unit: string; tolerance: number };
export type Parameters = {
  brightness?: NumericParameter | null;
  color_temperature?: NumericParameter | null;
  scene?: { scenes: string[] } | null;
};

export type Template = {
  id: string;
  experiment_id: string;
  adapter: string;
  adapter_available: boolean;
  device_id: string;
  display_name: string;
  app: { vendor: string; package: string; version: string };
  events: TemplateEvent[];
  parameters?: Parameters | null;
  phone_udid?: string | null;
  defaults: { repetitions_per_event: number; idle_range_seconds: [number, number]; cooldown_seconds: number };
  network: { target_device_ip?: string | null; forbidden_cidrs?: string[] };
  runtime_defaults?: {
    runtime_id: string;
    capture_interface?: string | null;
    capture_filter?: string | null;
    pre_roll_seconds?: number | null;
    post_roll_seconds?: number | null;
  };
};

export type Device = {
  udid: string;
  state: string;
  manufacturer?: string;
  model?: string;
  android_version?: string;
  sdk_level?: string;
  app_version?: string;
  busy_status: string;
  last_observation?: { state: string; observed_at_ns: number | null; source: string } | null;
};

export type TaskRequest = {
  template_id: string;
  runtime_id: string;
  mode: "simulate" | "device" | "formal";
  udid: string | null;
  repetitions: number;
  idle_min_seconds: number;
  idle_max_seconds: number;
  cooldown_seconds: number;
  seed: number | null;
  target_device_ip: string | null;
  capture_interface: string | null;
  capture_filter: string | null;
  pre_roll_seconds: number | null;
  post_roll_seconds: number | null;
  events?: TemplateEvent[] | null;
};

export type Quality = { app_success_rate?: number; result_counts?: Record<string, number> };
export type Task = {
  id: string;
  status: string;
  stage: string;
  queue_reason?: string | null;
  error?: string | null;
  session_id?: string | null;
  session_root?: string | null;
  completed_events: number;
  planned_events: number;
  created_at_ns: number;
  updated_at_ns: number;
  stop_requested_at_ns?: number | null;
  request: TaskRequest;
  quality?: Quality | null;
  source?: "console" | "cli" | "campaign" | "archive";
  controllable?: boolean;
  title?: string;
  display_name?: string;
  metadata?: { display_name?: string; device_id?: string; experiment_id?: string; is_campaign?: boolean };
};

export type Log = { id: number; created_at_ns: number; level: string; stage: string; message: string };
export type Session = {
  session_id: string;
  updated_at_ns: number;
  quality?: Quality | null;
  validation?: { ok?: boolean; errors?: string[] } | null;
  artifacts: string[];
  evidence: string[];
  experiment_id?: string;
  display_name?: string;
  device_id?: string;
  phone_udid?: string;
  runtime_mode?: string;
  status?: string;
  source?: string;
  session_root?: string;
};

export type Environment = {
  sdk_candidates?: { path: string; source: string; adb: string }[];
  capabilities?: { key: string; name: string; state: string; detail: string }[];
  discovery_roots?: { path: string; available: boolean; reason: string }[];
  runtime_id: string;
  checks: { name: string; ok: boolean; detail: unknown }[];
  capture_interfaces: string[];
};
