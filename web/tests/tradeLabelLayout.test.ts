import test from 'node:test';
import assert from 'node:assert/strict';
import {layoutTradeLabels,overlap} from '../src/lib/tradeLabelLayout.ts';
const bar={left:80,right:90,top:40,bottom:100};
test('labels clear the complete neighbouring candle envelope; anchors never move',()=>{
 const anchors=[{id:'b',x:85,y:70,width:70,side:'buy' as const}];const before=structuredClone(anchors);
 const obstacles=[bar,{left:112,right:118,top:20,bottom:180}];
 const [label]=layoutTradeLabels(anchors,obstacles,320,300);
 assert.ok(label.rect.top>=192);assert.ok(obstacles.every(o=>!overlap(label.rect,o,10)));
 assert.equal(label.x,85);assert.equal(label.y,70);assert.deepEqual(anchors,before);
});
test('near a plot edge labels can change side without clipping or covering volume',()=>{
 const [buy]=layoutTradeLabels([{id:'b',x:9,y:245,width:80,side:'buy'}],[{left:5,right:12,top:55,bottom:260}],300,280);
 assert.equal(buy.above,true);assert.ok(buy.rect.top>=6&&buy.rect.bottom<=274);assert.ok(buy.rect.left>=6);
 const [sell]=layoutTradeLabels([{id:'s',x:295,y:15,width:80,side:'sell'}],[{left:290,right:299,top:10,bottom:60}],300,280);
 assert.equal(sell.above,false);assert.ok(sell.rect.right<=294);assert.ok(sell.rect.top>=72);
});
test('adjacent labels use separate lanes and rescale from current pixels',()=>{
 const input=[0,1,2].map(i=>({id:String(i),x:100+i*15,y:100,width:65,side:'buy' as const}));
 const labels=layoutTradeLabels(input,[{left:90,right:140,top:70,bottom:110}],340,330);
 assert.equal(labels.length,3);for(let a=0;a<labels.length;a++)for(let b=a+1;b<labels.length;b++)assert.ok(!overlap(labels[a].rect,labels[b].rect,4));
 const shifted=layoutTradeLabels(input.map(p=>({...p,x:p.x*1.5,y:p.y+60})),[{left:135,right:210,top:130,bottom:170}],510,400);
 assert.equal(shifted[0].x,labels[0].x*1.5);assert.equal(shifted[0].y,160);assert.notEqual(shifted[0].rect.top,labels[0].rect.top);
});
test('dense views retain every in-range point, and hidden dates stay offscreen',()=>{
 const input=Array.from({length:30},(_,i)=>({id:String(i),x:20+i*5,y:95,width:65,side:i%2?'buy' as const:'sell' as const}));
 const labels=layoutTradeLabels(input,[{left:10,right:190,top:75,bottom:110}],220,190);
 assert.equal(labels.length,30);assert.equal(new Set(labels.map(p=>p.id)).size,30);
 assert.ok(labels.every(p=>p.rect.top>=6&&p.rect.bottom<=184&&p.rect.left>=6&&p.rect.right<=214));
 assert.deepEqual(layoutTradeLabels([{id:'off',x:-10,y:100,width:50,side:'buy'}],[],220,190),[]);
});
