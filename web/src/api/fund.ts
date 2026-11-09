import { httpRequest } from './http';
export interface CoverageRow {
  code:string;name:string;year:number;status:string;first_available:string|null;last_available:string|null;
  expected_sessions:number;available_sessions:number;missing_dates:string[];invalid_dates:string[];
  history_bars:number;research_history_sufficient:boolean;listing_date:string|null;updated_at:string|null;
}
export interface CoverageResponse {
  year:number;calendar_as_of:string|null;calendar_status?:string;complete:number;total:number;rows:CoverageRow[];
  fund_share_history:{complete_basket_days:number;first:string|null;message:string};
  share_counts:Record<string,{days:number;first:string;last:string;source:string}>;
  exchange_shares:{stored_days?:number;expected_days?:number;last_stored_date?:string;note?:string;error?:string};
  holdings:{total:number;available:number};
  maintenance:{running?:boolean;phase?:string;done?:number;total_funds?:number;finished_at?:string;schedule?:string;errors?:{code:string;error:string}[]};
}
export interface HoldingRow {
  rank:number;symbol:string;name:string;weight:number;previous_weight:number|null;
  weight_change:number|null;new_in_top10:boolean;shares_wan:number|null;market_value_wan:number|null;
}
export interface HoldingsResponse {
  code:string;name:string;periods:string[];report_period_end:string|null;previous_period_end:string|null;
  published_at:string|null;first_observed_at:string|null;fetched_at:string|null;source_page:string|null;
  rows:HoldingRow[];total_weight:number|null;
  status:{state:string;last_checked_at?:string;last_success_at?:string;message?:string|null};notes:string[];
}
export function fetchHoldings(code:string,period:string,signal?:AbortSignal):Promise<HoldingsResponse>{
  const query=new URLSearchParams({code});if(period)query.set('period',period);
  return httpRequest<HoldingsResponse>('/api/holdings?'+query,{signal});
}
export function fetchCoverage(signal?:AbortSignal):Promise<CoverageResponse>{return httpRequest<CoverageResponse>('/api/data/coverage',{signal});}
