import test from 'node:test'
import assert from 'node:assert/strict'
import {canDraw, targetFromY, createStrokeController, seriesSegments, createDrawingTimeline, drawingSegments} from '../src/ds13Draw.js'

const ready={firmware:'DS13',session:'s1',host_online:true,connected:true,synced:true,status_fresh:true,status_age_s:0,observed_at:10,display_fresh:true,fd2:{control_age_s:0},mode_name:'BALANCE',fault:0}
test('drawing requires a fresh live DS13 balance session',()=>{
 assert.equal(canDraw(ready,true,10000),true)
 for(const change of [{firmware:'V5'},{connected:false},{synced:false},{fault:1},{mode_name:'IDLE'},{session:''},{replay:{active:true}}]) assert.equal(canDraw({...ready,...change},true,10000),false)
 assert.equal(canDraw(ready,false,10000),false); assert.equal(canDraw(ready,true,10600),false)
 assert.equal(canDraw({...ready,draw:{stroke:{active:true,source:'native'}}},true,10000),false)
 assert.equal(canDraw({...ready,draw:{stroke:{active:true,source:'web'}}},true,10000),true)
})
test('vertical position clamps and rounds to signed count',()=>{
 assert.equal(targetFromY(0,0,100),192); assert.equal(targetFromY(50,0,100),0); assert.equal(targetFromY(200,0,100),-192)
})
test('release ends a stroke even when a begin response is delayed',async()=>{
 const sent=[]; let release; const transport=body=>{sent.push(body);return sent.length===1?new Promise(r=>release=r):Promise.resolve({state:'accepted'})}
 const c=createStrokeController({send:transport,id:(()=>{let n=0;return()=>`id${++n}`})()})
 c.begin('s1',2); await Promise.resolve(); c.move(5); c.move(6); c.hold(); c.end(); release({state:'accepted'}); await c.settled()
 assert.deepEqual(sent.map(x=>x.action),['stroke-begin','stroke-move','stroke-end']); assert.deepEqual(sent.map(x=>x.seq),[1,2,3]); assert.equal(sent[1].target,6)
 c.hold(); c.move(8); await c.settled(); assert.equal(sent.length,3)
})
test('failed command terminates stroke and never retries',async()=>{
 const sent=[]; const c=createStrokeController({send:async b=>{sent.push(b);if(b.action==='stroke-begin')throw Error('offline');return {state:'accepted'}},id:(()=>{let n=0;return()=>`id${++n}`})()})
 c.begin('s1',0); await c.settled(); c.hold(); await c.settled(); assert.deepEqual(sent.map(x=>x.action),['stroke-begin','stroke-end'])
})
test('an expired begin is reported immediately rather than waiting for missing acknowledgements',async()=>{
 const sent=[],changes=[]
 const c=createStrokeController({send:async b=>{sent.push(b);if(b.action==='stroke-begin')throw Object.assign(Error('手绘开始请求已过期'),{code:'stroke_input_expired'});return {state:'accepted'}},id:(()=>{let n=0;return()=>`id${++n}`})(),onChange:s=>changes.push(s)})
 c.begin('s',0);await c.settled()
 assert.equal(changes.at(-1).active,false)
 assert.match(changes.at(-1).error,/开始请求已过期/)
 assert.deepEqual(sent.map(x=>x.action),['stroke-begin','stroke-end'])
})
test('series split at missing values, gaps, and session boundaries',()=>{
 const rows=[{plot_at:1,session_epoch:'a',raw:{live_offset:1}},{plot_at:1.005,session_epoch:'a',raw:{live_offset:2}},{plot_at:1.01,session_epoch:'a',raw:{}},{plot_at:1.015,session_epoch:'a',raw:{live_offset:3}},{plot_at:1.02,session_epoch:'b',raw:{live_offset:4}}]
 assert.deepEqual(seriesSegments(rows,r=>r.raw.live_offset).map(s=>s.map(p=>p.value)),[[1,2],[3],[4]])
})

test('held input sends latest movement and subsequent lease in sequence',async()=>{
 let now=0;const sent=[];let release;const c=createStrokeController({now:()=>now,send:b=>{sent.push(b);return sent.length===1?new Promise(r=>release=r):Promise.resolve({state:'accepted'})},id:(()=>{let n=0;return()=>`id${++n}`})()})
 c.begin('s',0);await Promise.resolve();c.move(12);c.move(30);c.hold();release({state:'accepted'});await c.settled();now=50;c.hold();await c.settled();c.end();await c.settled()
 assert.deepEqual(sent.map(b=>b.action),['stroke-begin','stroke-move','stroke-move','stroke-end']);assert.deepEqual(sent.map(b=>b.seq),[1,2,3,4]);assert.equal(sent[1].target,30)
})

test('DS13 drawing timeline compares the board target and measured encoder relative to the same stroke origin',()=>{
 const timeline=createDrawingTimeline()
 const row=(seq,changes={})=>({session_epoch:'s:1',gap_before:false,channels:['arm_angle_deg'],raw:{
   coherent:true,mode:3,fault:0,job_id:42,play_state:6,play_time_ms:seq*5,board_ms:1000+seq*5,
   origin:'90071992547409920',wide_encoder:'90071992547409931',wide_target:'90071992547409940',live_offset:20,...changes}})
 assert.equal(timeline.ingest(row(0,{point_sequence:33})),true)
 assert.equal(timeline.rows[0].pointSequence,33)
 assert.equal(timeline.ingest(row(1,{wide_encoder:'90071992547409934'})),true)
 assert.deepEqual(timeline.rows.map(r=>[r.target,r.reference,r.actual]),[[20,20,11],[20,20,14]])
 assert.deepEqual(timeline.rows.map(r=>r.gap),[false,false])
 assert.equal(timeline.ingest(row(3,{play_time_ms:15})),true)
 assert.equal(timeline.rows.at(-1).gap,true)
 assert.equal(timeline.ingest(row(4,{mode:4,fault:1})),false)
 assert.equal(timeline.rows.length,3,'fault must keep already observed data, without inventing a point')
})

test('DS13 drawing timeline resets on a new stroke or connection and never substitutes target for missing encoder',()=>{
 const timeline=createDrawingTimeline()
 const row=(job,epoch,changes={})=>({session_epoch:'s:1',gap_before:false,channels:['arm_angle_deg'],raw:{
   coherent:true,mode:3,fault:0,job_id:job,play_state:6,play_time_ms:epoch*5,board_ms:1000+epoch*5,
   origin:'100',wide_encoder:'120',wide_target:'110',live_offset:10,...changes}})
 timeline.ingest(row(1,1));timeline.ingest(row(1,2,{wide_encoder:null}))
 timeline.ingest(row(1,3))
 assert.deepEqual(timeline.rows.map(r=>r.actual),[20,20])
 assert.equal(timeline.rows.at(-1).gap,true,'a missing measurement must break the actual trace')
 timeline.ingest(row(2,1,{origin:'200',wide_encoder:'205',wide_target:'210'}))
 assert.deepEqual(timeline.rows.map(r=>r.actual),[5])
 timeline.ingest({...row(3,1),session_epoch:'new:1'})
 assert.equal(timeline.rows.length,1)
 assert.equal(timeline.rows[0].job,3)
})

test('DS13 plots never bridge a telemetry gap or a missing real encoder value',()=>{
 const rows=[{time:0,target:2,actual:1,gap:false},{time:.005,target:3,actual:2,gap:false},
   {time:.015,target:5,actual:4,gap:true},{time:.02,target:6,actual:null,gap:false},
   {time:.025,target:7,actual:6,gap:false}]
 assert.deepEqual(drawingSegments(rows,'actual').map(s=>s.map(p=>p.value)),[[1,2],[4],[6]])
 assert.deepEqual(drawingSegments(rows,'target').map(s=>s.map(p=>p.value)),[[2,3],[5,6,7]])
})

test('target curve follows Host vertices while preserving holds and gaps',()=>{
 const rows=[
  {time:0,target:0,actual:0,pointSequence:1}, {time:.005,target:0,actual:0,pointSequence:1},
  {time:.01,target:10,actual:1,pointSequence:2}, {time:.015,target:10,actual:2,pointSequence:2},
  {time:.02,target:20,actual:3,pointSequence:3}, {time:.025,target:20,actual:3,pointSequence:3},
  {time:.04,target:30,actual:4,pointSequence:4,gap:true}, {time:.045,target:30,actual:4,pointSequence:4}]
 assert.deepEqual(drawingSegments(rows,'target').map(segment=>segment.map(p=>p.time)),[[0,.01,.02,.025],[.04,.045]])
 assert.equal(drawingSegments(rows,'actual')[0].length,6,'encoder samples remain untouched')
})

test('moving pointer sends a fresh target without waiting for the periodic heartbeat',async()=>{
 let now=0;const sent=[]
 const c=createStrokeController({now:()=>now,send:async body=>{sent.push(body);return {state:'accepted'}},id:(()=>{let n=0;return()=>`id${++n}`})()})
 c.begin('s',0);await c.settled();now=31;c.move(42);await c.settled()
 assert.deepEqual(sent.map(x=>x.action),['stroke-begin','stroke-move']);assert.equal(sent[1].target,42)
 c.end();await c.settled()
})

test('a heartbeat immediately after a movement does not duplicate the request',async()=>{
 let now=0;const sent=[]
 const c=createStrokeController({now:()=>now,send:async body=>{sent.push(body);return {state:'accepted'}},id:(()=>{let n=0;return()=>`id${++n}`})()})
 c.begin('s',0);await c.settled();now=31;c.move(42);c.hold();await c.settled()
 assert.deepEqual(sent.map(x=>x.action),['stroke-begin','stroke-move'])
 now=62;c.hold();await c.settled();assert.equal(sent.length,3)
 c.end();await c.settled()
})

test('lease renewals and release are sent while an earlier response is delayed',async()=>{
 const sent=[],replies=[]; const changes=[]
 const c=createStrokeController({send:body=>{sent.push(body);return new Promise((resolve,reject)=>replies.push({resolve,reject}))},id:(()=>{let n=0;return()=>`id${++n}`})(),onChange:s=>changes.push(s)})
 c.begin('s',0);await Promise.resolve()
 c.move(30);c.hold();await Promise.resolve()
 assert.deepEqual(sent.map(x=>x.action),['stroke-begin','stroke-move'])
 assert.equal(sent[1].target,30)
 assert.ok(Number.isSafeInteger(sent[0].issued_ms))
 c.end();await Promise.resolve()
 assert.deepEqual(sent.map(x=>x.action),['stroke-begin','stroke-move','stroke-end'])
 replies[2].resolve({state:'accepted'});replies[1].resolve({state:'accepted'});replies[0].resolve({state:'accepted'})
 await c.settled();c.hold();await Promise.resolve()
 assert.equal(sent.length,3);assert.equal(changes.at(-1).active,false)
})

test('an obsolete rejected renewal cannot interrupt a newer accepted renewal',async()=>{
 let now=0;const sent=[],replies=[],changes=[]
 const c=createStrokeController({now:()=>now,send:body=>{sent.push(body);return new Promise((resolve,reject)=>replies.push({resolve,reject}))},id:(()=>{let n=0;return()=>`id${++n}`})(),onChange:s=>changes.push(s)})
 c.begin('s',0);await Promise.resolve();replies[0].resolve({state:'accepted'});await c.settled()
 now=50;c.hold();await Promise.resolve();now=81;c.move(20);c.hold();await Promise.resolve()
 replies[2].resolve({state:'accepted'});await Promise.resolve();replies[1].reject(Error('旧笔画序号'));await Promise.resolve();await Promise.resolve()
 assert.equal(changes.at(-1).active,true)
 c.end();await Promise.resolve();replies[3].resolve({state:'accepted'});await c.settled()
})

test('a definitive Host stop ends drawing even if newer requests are still in flight',async()=>{
 let now=0;const sent=[],replies=[],changes=[]
 const c=createStrokeController({now:()=>now,send:body=>{sent.push(body);return new Promise((resolve,reject)=>replies.push({resolve,reject}))},id:(()=>{let n=0;return()=>`id${++n}`})(),onChange:s=>changes.push(s)})
 c.begin('s',0);await Promise.resolve();replies[0].resolve({state:'accepted'});await c.settled()
 now=50;c.hold();await Promise.resolve();now=100;c.hold();await Promise.resolve()
 replies[1].reject(Object.assign(Error('笔画已结束'),{code:'stroke_ended'}))
 await new Promise(resolve=>setImmediate(resolve))
 assert.equal(changes.at(-1).active,false);assert.match(changes.at(-1).error,/笔画已结束/)
 assert.equal(sent.at(-1).action,'stroke-end')
 replies[2].resolve({state:'accepted'});replies[3].resolve({state:'accepted'});await c.settled()
})

test('a stream of unacknowledged input cannot remain visually active forever',async()=>{
 let now=0;const changes=[]
 const c=createStrokeController({now:()=>now,send:body=>body.action==='stroke-begin'||body.action==='stroke-end'?Promise.resolve({state:'accepted'}):new Promise(()=>{}),id:(()=>{let n=0;return()=>`id${++n}`})(),onChange:s=>changes.push(s)})
 c.begin('s',0);await c.settled()
 for(let i=1;i<=12;i++){now=i*50;c.hold();await Promise.resolve()}
 assert.equal(changes.at(-1).active,false)
 assert.match(changes.at(-1).error,/未确认|响应/)
})

