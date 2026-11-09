export type PriceBar={date:string;close:number};
export function chartRange<T extends PriceBar>(all:T[],range:string,months:number|null,ytd?:{year:number|null;baseline:number|null}){
  if(!all.length)return null;
  const last=all[all.length-1];
  let cutoff='';
  if(range==='ytd')cutoff=`${ytd?.year??last.date.slice(0,4)}-01-01`;
  else if(months!==null){
    const date=new Date(last.date+'T00:00:00Z'),day=date.getUTCDate();date.setUTCDate(1);date.setUTCMonth(date.getUTCMonth()-months);
    const lastDay=new Date(Date.UTC(date.getUTCFullYear(),date.getUTCMonth()+1,0)).getUTCDate();date.setUTCDate(Math.min(day,lastDay));cutoff=date.toISOString().slice(0,10);
  }
  const filtered=cutoff?all.filter(bar=>bar.date>=cutoff):all;
  const effective=filtered.length?filtered:all;
  const prior=cutoff?[...all].reverse().find(bar=>bar.date<cutoff):undefined;
  const baseline=range==='ytd'?(ytd?.baseline??prior?.close??effective[0].close):effective[0].close;
  return {bars:effective,baseline,change:last.close/baseline-1,
          truncated:Boolean(cutoff&&all[0].date>cutoff),cutoff,
          baseline_kind:range==='ytd'&&(ytd?.baseline??prior?.close)!=null?'previous_year_close':'first_available_close'};
}
