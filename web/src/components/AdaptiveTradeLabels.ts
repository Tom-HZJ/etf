import type {ISeriesPrimitive,IPrimitivePaneRenderer,SeriesAttachedParameter,Time,Logical} from 'lightweight-charts';
import {layoutTradeLabels} from '../lib/tradeLabelLayout';
import type {PlacedLabel,LabelRect} from '../lib/tradeLabelLayout';
export interface TradeLabel {id:string;time:Time;price:number;side:'buy'|'sell';text:string}
export interface PriceObstacle {time:Time;high:number;low:number}
/** Chart primitive redraws with the library's own pan/zoom/resize cycle. */
export class AdaptiveTradeLabels implements ISeriesPrimitive<Time> {
 private attachedTo:SeriesAttachedParameter<Time>|null=null;
 private request=()=>{};
 private placed:PlacedLabel[]=[];
 private points:readonly TradeLabel[]=[];
 private obstacles:readonly PriceObstacle[]=[];
 constructor(private colors:{buy:string;sell:string;background:string},private plotFraction=.8){}
 setData(points:readonly TradeLabel[],obstacles:readonly PriceObstacle[]){this.points=points;this.obstacles=obstacles;this.placed=[];this.request();}
 attached(p:SeriesAttachedParameter<Time>){this.attachedTo=p;this.request=p.requestUpdate;this.request();}
 detached(){this.attachedTo=null;this.request=()=>{};this.placed=[];}
 autoscaleInfo(start:Logical,end:Logical){
  const p=this.attachedTo;if(!p)return null;
  const prices=this.points.filter(point=>{const index=p.chart.timeScale().timeToIndex(point.time);return index!==null&&index>=start&&index<=end;}).map(point=>point.price).filter(Number.isFinite);
  return prices.length?{priceRange:{minValue:Math.min(...prices),maxValue:Math.max(...prices)},margins:{above:36,below:36}}:null;
 }
 hitTest(x:number,y:number){
  const label=this.placed.find(p=>(x>=p.rect.left&&x<=p.rect.right&&y>=p.rect.top&&y<=p.rect.bottom)||Math.hypot(x-p.x,y-p.y)<9);
  return label?{externalId:label.id,zOrder:'top' as const,cursorStyle:'pointer'}:null;
 }
 private renderer:IPrimitivePaneRenderer={draw:target=>{
  const p=this.attachedTo;if(!p)return;
  target.useMediaCoordinateSpace(({context:ctx,mediaSize})=>{
   ctx.save();ctx.font='11px "SFMono-Regular", Consolas, monospace';
   const width=mediaSize.width,height=mediaSize.height*this.plotFraction;
   const anchors=this.points.flatMap(point=>{const x=p.chart.timeScale().timeToCoordinate(point.time),y=p.series.priceToCoordinate(point.price);return x!==null&&y!==null?[{id:point.id,x,y,width:ctx.measureText(point.text).width+14,side:point.side}]:[];});
   const xs=[...new Set(this.obstacles.map(o=>p.chart.timeScale().timeToCoordinate(o.time)).filter((x):x is NonNullable<typeof x>=>x!==null))].sort((a,b)=>a-b);
   const barHalf=xs.length>1?Math.max(1,Math.abs(xs[1]-xs[0])*.45):3;
   const obstacles:LabelRect[]=this.obstacles.flatMap(o=>{const x=p.chart.timeScale().timeToCoordinate(o.time),a=p.series.priceToCoordinate(o.high),b=p.series.priceToCoordinate(o.low);return x!==null&&a!==null&&b!==null?[{left:x-barHalf,right:x+barHalf,top:Math.min(a,b),bottom:Math.max(a,b)}]:[];});
   this.placed=layoutTradeLabels(anchors,obstacles,width,height);
   const byId=new Map(this.points.map(point=>[point.id,point]));
   ctx.lineWidth=1;ctx.textBaseline='middle';ctx.textAlign='center';
   for(const label of this.placed){
    const point=byId.get(label.id)!;const color=point.side==='buy'?this.colors.buy:this.colors.sell;
    const r=label.rect,edge=label.above?r.bottom:r.top,center=(r.left+r.right)/2;
    ctx.strokeStyle=color;ctx.globalAlpha=.65;ctx.beginPath();ctx.moveTo(label.x,label.y);ctx.lineTo(center,edge);ctx.stroke();ctx.globalAlpha=1;
    ctx.fillStyle=color;ctx.beginPath();ctx.arc(label.x,label.y,2.5,0,2*Math.PI);ctx.fill();
    ctx.fillStyle=this.colors.background;ctx.beginPath();ctx.roundRect(r.left,r.top,r.right-r.left,r.bottom-r.top,4);ctx.fill();
    ctx.strokeStyle=color;ctx.stroke();ctx.fillStyle=color;ctx.fillText(point.text,center,(r.top+r.bottom)/2);
   }
   ctx.restore();
  });
 }};
 private views=[{zOrder:()=> 'top' as const,renderer:()=>this.renderer}];
 paneViews(){return this.views;}
}
