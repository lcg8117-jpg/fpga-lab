import {fd2Freshness} from './fd2Presentation.js'

// DS13's two traces use the same board-reported stroke origin. Subtract the
// int64 values before converting to Number so a large accumulated encoder does
// not silently lose the small relative movement.
function integer64(value) {
  if (typeof value==='number') return Number.isSafeInteger(value) ? BigInt(value) : null
  if (typeof value!=='string' || !/^-?\d{1,19}$/.test(value)) return null
  try {return BigInt(value)} catch {return null}
}
function relativeCount(value,origin) {
  const raw=integer64(value)
  if(raw===null || origin===null)return null
  const difference=raw-origin,limit=BigInt(Number.MAX_SAFE_INTEGER)
  return difference>=-limit && difference<=limit ? Number(difference) : null
}

export function createDrawingTimeline() {
  let rows=[],session=null,job=null,origin=null,tracking=false,lastBoard=null,pendingGap=false
  function reset(){rows=[];session=null;job=null;origin=null;tracking=false;lastBoard=null;pendingGap=false}
  function ingest(row){
    if (!row || !Array.isArray(row.channels) || !row.channels.includes('arm_angle_deg') ||
        typeof row.session_epoch!=='string') return false
    if (session!==row.session_epoch){reset();session=row.session_epoch}
    const raw=row.raw || {},newOrigin=integer64(raw.origin)
    const validJob=Number.isInteger(raw.job_id) && raw.job_id>0
    if (raw.coherent!==true)return false
    if (raw.mode!==3 || raw.fault!==0){tracking=false;return false}
    if (validJob && [3,4,6].includes(raw.play_state) && raw.job_id!==job){
      rows=[];job=raw.job_id;origin=newOrigin;tracking=true;lastBoard=null;pendingGap=false
    }
    if (!tracking || raw.job_id!==job || newOrigin===null || newOrigin!==origin)return false
    const target=raw.live_offset, actual=relativeCount(raw.wide_encoder,origin), reference=relativeCount(raw.wide_target,origin)
    const time=raw.play_time_ms,board=raw.board_ms
    if (!Number.isInteger(target) || Math.abs(target)>192 || actual===null || reference===null ||
        !Number.isInteger(time) || time<0 || time>165000 || !Number.isInteger(board) || board<0 || board>0xffffffff){
      pendingGap=true;return false
    }
    const delta=lastBoard===null ? null : (board-lastBoard)>>>0
    if (delta!==null && (delta===0 || delta>=0x80000000))return false
    if (rows.length && time<rows[rows.length-1].time*1000)return false
    const pointSequence=Number.isInteger(raw.point_sequence) && raw.point_sequence>=0 && raw.point_sequence<=0xffffffff ? raw.point_sequence : null
    rows.push({time:time/1000,target,reference,actual,job,pointSequence,gap:Boolean(row.gap_before || pendingGap || (delta!==null && delta>7))})
    if(rows.length>40000)rows.shift()
    lastBoard=board;pendingGap=false
    if(raw.play_state===0)tracking=false
    return true
  }
  return {ingest,reset,get rows(){return rows}}
}

export function drawingSegments(rows,field) {
  const segments=[];let current=null,previous=null,tail=null
  function finish(){
    if(current && tail && tail!==previous)current.push(tail)
    current=null;previous=null;tail=null
  }
  for(const row of rows || []){
    const value=row?.[field]
    if(!Number.isFinite(value) || !Number.isFinite(row?.time)){finish();continue}
    if(!current || row.gap){finish();current=[];segments.push(current)}
    const point={time:row.time,value,pointSequence:row.pointSequence}
    // Match the supplied Host: connect target updates, retain the final hold,
    // and never bridge missing telemetry. Actual encoder samples stay intact.
    if(field!=='target' || !previous || !Number.isInteger(row.pointSequence) ||
       row.pointSequence!==previous.pointSequence || value!==previous.value){
      current.push(point);previous=point
    }
    tail=point
  }
  finish()
  return segments
}

export function canDraw(state, active, now=Date.now()) {
  const fresh=fd2Freshness(state || {},now)
  return Boolean(active && state?.firmware==='DS13' && state.session && state.host_online && state.connected && state.synced && !state.replay?.active && !state.replay?.finished && !(state.draw?.stroke?.active && state.draw.stroke.source==='native') && fresh.status && fresh.control && state.mode_name==='BALANCE' && state.fault===0)
}
export function targetFromY(y,top,height) {
  if (!Number.isFinite(y) || !Number.isFinite(top) || !(height>0)) return 0
  return Math.max(-192,Math.min(192,Math.round(192-384*(y-top)/height)))
}
export function seriesSegments(rows, valueOf) {
  const segments=[]; let prev=null, segment=null
  for (const row of rows || []) {
    // Rows from other channels are not samples of these signals.
    if (Array.isArray(row.channels) && !row.channels.includes('arm_angle_deg')) continue
    const value=valueOf(row), t=row.plot_at
    if (!Number.isFinite(value) || !Number.isFinite(t)) {prev=null; continue}
    if (!prev || row.gap_before || row.session_epoch!==prev.session_epoch || Math.abs(t-prev.plot_at-.005)>.0001) {segment=[]; segments.push(segment)}
    segment.push({time:t,value}); prev=row
  }
  return segments
}
export function createStrokeController({send,id,onChange=()=>{},now=()=>globalThis.performance?.now?.() ?? Date.now()}) {
  const pointerInterval=30
  let current=null, error=''
  const inFlight=new Set()
  const publish=()=>onChange({active:!!current,error})
  function dispatch(stroke,action,target) {
    const body={action,request_id:id(),session:stroke.session,stroke_id:stroke.id,seq:++stroke.seq,issued_ms:Date.now()}
    if (action==='stroke-begin'||action==='stroke-move') body.target=target
    if(action==='stroke-begin'||action==='stroke-move'){stroke.lastDispatchAt=now();stroke.lastAction=action}
    if(action!=='stroke-end')stroke.latestSeq=body.seq
    let promise
    promise=Promise.resolve().then(()=>send(body)).then(result=>{
      if (result?.state!=='accepted') throw Error(result?.message || '命令未获接受')
      if(action==='stroke-begin')stroke.beginPending=false
      if(action!=='stroke-end' && current===stroke)stroke.lastAckAt=now()
    }).catch(e=>{
      if(action==='stroke-begin')stroke.beginPending=false
      if(current!==stroke || action==='stroke-end')return
      if(action!=='stroke-begin' && ['stroke_not_started','stroke_outdated','stroke_input_expired'].includes(e.code))return
      if(e.code!=='stroke_ended' && action!=='stroke-begin' && (stroke.beginPending || body.seq<stroke.latestSeq))return
      error=`${e.name==='AbortError' ? '手绘连接响应超时' : (e.message || '连接失败')}；本笔已结束，请重新按住绘制`
      current=null;dispatch(stroke,'stroke-end');publish()
    }).finally(()=>{
      inFlight.delete(promise)
    })
    inFlight.add(promise)
  }
  function renew() {
    if(!current)return
    if(now()-current.lastAckAt>=600){const stroke=current;current=null;error='手绘连接长时间未确认，本笔已结束，请重新按住绘制';dispatch(stroke,'stroke-end');publish();return}
    if(inFlight.size>=32){const stroke=current;current=null;error='手绘连接响应过慢，本笔已结束，请重新按住绘制';dispatch(stroke,'stroke-end');publish();return}
    dispatch(current,'stroke-move',current.target)
  }
  return {
    begin(session,target) {if(current)return false; error='';current={session,id:id(),seq:0,target,beginPending:true,lastAckAt:now()};dispatch(current,'stroke-begin',target);publish();return true},
    move(target) {if(!current)return;current.target=target;if(now()-current.lastDispatchAt>=pointerInterval)renew()},
    hold() {if(current && (current.lastAction!=='stroke-move' || now()-current.lastDispatchAt>=pointerInterval))renew()},
    end() {if(!current)return;const stroke=current;current=null;dispatch(stroke,'stroke-end');publish()},
    async settled() {while(inFlight.size)await Promise.allSettled([...inFlight])},
  }
}
