import {useState} from 'react';import {createRoot} from 'react-dom/client';
import {KlineChart} from './src/components/Charts';import {MinuteTrendChart} from './src/components/MinuteTrendChart';
import {TradePointExplanation} from './src/components/TradePointExplanation';
import {replayPointKey} from './src/lib/tradeReasons';import './src/styles.css';
import raw from './verify-labels-data.json';import type {ReplayResult} from './src/api/replay';import type {Bar,MinuteBar} from './src/api/types';
const data=raw.replay as ReplayResult,bars=raw.bars.bars as Bar[],visible=bars.filter(b=>b.date.startsWith('2026'));
const markers=data.executions.map(p=>({...p,label:p.side==='buy'?'B开':'S开'}));
const mean=(n:number)=>bars.flatMap((b,i)=>i>=n-1?[{date:b.date,value:bars.slice(i-n+1,i+1).reduce((s,r)=>s+r.close,0)/n}]:[]).filter(b=>b.date.startsWith('2026'));
function Verify(){const [selected,setSelected]=useState<string|null>(null),[mode,setMode]=useState<'dark'|'light'>('dark');
return <main style={{maxWidth:1200,margin:'12px auto',padding:12}}><h1 style={{fontSize:18}}>真实历史行情 · 标签与原因验收</h1><button onClick={()=>{setMode(mode==='dark'?'light':'dark');document.documentElement.dataset.theme=mode==='dark'?'light':'dark';}}>切换主题</button><KlineChart bars={visible} ma20={[]} ma60={[]} movingAverages={[{label:'MA5',tone:'gold',points:mean(5)},{label:'MA10',tone:'cyan',points:mean(10)},{label:'MA30',tone:'violet',points:mean(30)}]} strategyMarkers={markers} onSelectStrategyMarker={p=>setSelected(replayPointKey(p))} mode={mode} height={410} ariaLabel="传媒ETF真实日K标签避让"/><TradePointExplanation points={markers} selected={selected} onSelect={setSelected} data={data} bars={bars} kind="executions"/><h2>科创50实际记录分钟买点</h2><MinuteTrendChart bars={raw.minute.bars as MinuteBar[]} interval={1} basis={raw.minute.quote?.pre_close??null} buyPoints={[{id:raw.point.event_id,time:raw.point.observed_at,price:raw.point.reference_price}]} mode={mode} height={360} ariaLabel="科创50分钟买点"/></main>}
createRoot(document.getElementById('verify')!).render(<Verify/>);
