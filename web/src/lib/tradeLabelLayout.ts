/** Pixel-only annotation layout. Anchor coordinates and market data stay immutable. */
export interface LabelRect {left:number;top:number;right:number;bottom:number}
export interface LabelAnchor {id:string;x:number;y:number;width:number;side:'buy'|'sell'}
export interface PlacedLabel extends LabelAnchor {rect:LabelRect;above:boolean;crowded:boolean}
export function overlap(a:LabelRect,b:LabelRect,gap=0):boolean {
 return a.left<b.right+gap&&a.right>b.left-gap&&a.top<b.bottom+gap&&a.bottom>b.top-gap;
}
const clamp=(n:number,lo:number,hi:number)=>Math.max(lo,Math.min(hi,n));
export function layoutTradeLabels(anchors:readonly LabelAnchor[],obstacles:readonly LabelRect[],width:number,height:number):PlacedLabel[]{
 if(width<30||height<30)return [];
 const pad=6,h=21,out:PlacedLabel[]=[];
 for(const p of [...anchors].filter(p=>[p.x,p.y,p.width].every(Number.isFinite)&&p.x>=0&&p.x<=width).sort((a,b)=>a.x-b.x||a.id.localeCompare(b.id))){
  const w=Math.min(Math.max(20,p.width),width-2*pad),left=clamp(p.x-w/2,pad,width-pad-w);
  const near=obstacles.filter(o=>o.left<left+w+4&&o.right>left-4);
  const top=Math.min(p.y,...near.map(o=>o.top)),bottom=Math.max(p.y,...near.map(o=>o.bottom));
  const gap=clamp((bottom-top)*.05,12,22),candidates:{rect:LabelRect;above:boolean;score:number}[]=[];
  for(const above of [p.side==='sell',p.side!=='sell']){
   const preferred=above===(p.side==='sell');
   for(let lane=0;lane<Math.ceil(height/(h+5));lane++){
    const y=above?top-gap-h-lane*(h+5):bottom+gap+lane*(h+5);
    if(y<pad||y+h>height-pad)continue;
    const rect={left,top:y,right:left+w,bottom:y+h};
    const blocked=near.some(o=>overlap(rect,o,5));
    const conflicts=out.filter(o=>overlap(rect,o.rect,5)).length;
    candidates.push({rect,above,score:(blocked?100000:0)+conflicts*10000+(preferred?0:28)+Math.abs(y+h/2-p.y)});
   }
  }
  // At extreme zoom/density there may be no free lane. Retain the point and
  // its text, constrained to the pane; zooming out must never delete signals.
  candidates.sort((a,b)=>a.score-b.score||a.rect.top-b.rect.top);
  const best=candidates[0]??{rect:{left,top:clamp(p.side==='buy'?bottom+gap:top-gap-h,pad,height-pad-h),right:left+w,bottom:0},above:p.side==='sell',score:100000};
  best.rect.bottom=best.rect.top+h;
  out.push({...p,rect:best.rect,above:best.above,crowded:best.score>=10000});
 }
 return out;
}
