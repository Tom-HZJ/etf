import { httpRequest, isMock } from "./http";
export interface ResearchRow {
  code: string;
  name: string;
  direction: string;
  phase: string;
  reason: string;
  eligible: boolean;
  prealert:boolean;
  history_bars:number;
  history_sufficient:boolean;
  price_event_date:string|null;
  last_signal_date:string|null;
  paths:{path:string;label:string;price_event_today:boolean;geometry_allowed:boolean;eligible:boolean}[];
  trend_confirmed: boolean;
  data_date: string | null;
  trigger_date: string | null;
  reference_close: number | null;
  invalidation_close: number | null;
  entry_distance_atr: number | null;
  risk_distance_pct: number | null;
  atr14?: number;
  exit_plan: string[];
  provenance?: {
    source: string | null;
    updated_at: string | null;
    adjustment: string | null;
  };
}
export interface ResearchResponse {
  model:'current'|'expanded';
  model_label:string;
  model_warning?:string|null;
  as_of: string | null;
  generated_at: string;
  rule_version: string;
  data_stale: boolean;
  candidate_count: number;
  prealert_count:number;
  rows: ResearchRow[];
  decision_scope: 'self_only';
  external_reference_required: false;
  sources: string[];
  latest_collected_at: string | null;
  stale_count: number;
}
export async function fetchResearch(
  signal?: AbortSignal,
  model:'current'|'expanded'='current',
): Promise<ResearchResponse> {
  if (isMock) throw new Error("研究页需连接真实后端，不提供模拟信号");
  return httpRequest<ResearchResponse>("/api/research?model="+model, { signal });
}
