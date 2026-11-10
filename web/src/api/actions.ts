import {httpRequest,isMock} from './http';
export interface ActionRow {
 version?:string;
 code:string;name:string;direction:string;kind:'buy'|'sell'|'risk'|'holding'|'waiting'|'stale';label:string;reason:string;
 actionable:boolean;price:number|null;reference_price?:number;quote_time:string|null;quote_source:string|null;
 trigger_time:string|null;observed_at?:string;event_id?:string;confirmation:string;
 entry_low:number|null;entry_high:number|null;stop_price:number|null;sell_price:number|null;
 simulated_position:boolean;ready_for_buy?:boolean;data_date:string|null;daily_confirmation_date?:string;
 simulated_entry_date?:string|null;simulated_entry_price?:number|null;
 paper_trade_id?:number|null;paper_buy_event_id?:string|null;
}
export interface ActionDesk {
 model:'current'|'expanded';protection:'none'|'trailing7';version:string;date:string;generated_at:string|null;
 server_time:string;session:string;data_stale:boolean;poll_seconds:number;position_basis:'simulated_only'|'simulated_ledger';
 rows:ActionRow[];history:(ActionRow&{active:boolean;live_recorded:boolean})[];message:string;
 buy_point_policy?:string;buy_points?:BuyPoint[];
 exit_point_policy?:string;exit_points?:ExitPoint[];
 paper_ledger?:{policy:string;start_date:string;simulated:true;execution_basis:string;open_count:number;trades:PaperTrade[]}|null;
 trade_scope?:{policy:string;excluded_codes:string[];team_exceptions:string[];duplicate_codes?:string[];
 user_excluded_codes?:string[];user_excluded_sectors?:string[];
 representatives?:{policy?:string;date?:string;groups?:Record<string,{selected_code:string|null;status:string}>}};
}
export interface BuyPoint extends ActionRow {
 model:ActionDesk['model'];protection:ActionDesk['protection'];
 event_id:string;observed_at:string;trigger_time:string;reference_price:number;
 live_recorded:true;active:boolean;
}
export interface ExitPoint extends BuyPoint {kind:'sell'|'risk';simulated_position:true}
export interface PaperTrade {
 id:number;code:string;name:string;buy_event_id:string;entry_time:string;entry_price:number;
 initial_stop:number;target_price:number;exit_event_id:string|null;exit_time:string|null;exit_price:number|null;exit_reason:string|null;
}
export function fetchActions(signal?:AbortSignal,model:'current'|'expanded'='current',protection:'none'|'trailing7'='none'):Promise<ActionDesk>{
 if(isMock)throw new Error('盘中提醒需要真实行情后端');
 return httpRequest<ActionDesk>('/api/research/actions?'+new URLSearchParams({model,protection}),{signal});
}
