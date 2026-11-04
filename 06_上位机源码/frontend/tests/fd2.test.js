import test from 'node:test'
import assert from 'node:assert/strict'
import { fd2Actions, appendFd2Samples, periodicArm } from '../src/fd2Presentation.js'
import { createFd2WaveformOption } from '../src/fd2Waveform.js'
test('V5 has manual balance and trajectories, never swing or adaptive',()=>{assert(!fd2Actions('V5').includes('start-swing'));assert(fd2Actions('V5').includes('trajectory'));assert(fd2Actions('V3').includes('adapt-enable'));assert(!fd2Actions('V3').includes('start-swing'))})
test('120 second retention preserves decimal wide counts and deduplicates',()=>{const row={sample_id:'a',received_at:1,raw:{count:'1152921504606846976'}};assert.equal(appendFd2Samples([row],[row],120000).length,1);assert.equal(appendFd2Samples([row],[],122000).length,0);assert.equal(periodicArm(1085),5);assert.equal(periodicArm(null),null)})

test('diagnostic target uses independent board clock and gaps break lines',()=>{const rows=[{plot_at:1,measurement_at:1,channels:['arm_angle_deg'],arm_angle_deg:800,session_epoch:1},{plot_at:1.2,measurement_at:1.2,channels:['final_target_deg'],final_target_deg:900},{plot_at:1.5,measurement_at:1.5,channels:['arm_angle_deg'],arm_angle_deg:850,gap_before:true}];const o=createFd2WaveformOption(rows,2000,300,[],1500,120000,'horizontal');assert.equal(o.series.find(s=>s.id==='final_target_deg').data[0][0],1200);assert(o.series.find(s=>s.id==='arm_angle_deg').data.some(p=>p[1]===null))})
import { gestureLabels, gainPair, actualGainWindow, canFd2Command } from '../src/fd2Presentation.js'
test('all nine original gesture codes retain source meaning',()=>assert.deepEqual(gestureLabels,{0:'无',1:'右滑',2:'左滑',3:'上滑',4:'下滑',5:'靠近',6:'远离',7:'顺时针',8:'逆时针',9:'挥手STOP'}))
test('six gain fields use angle P and D at offsets zero and two, original averages only',()=>{assert.deepEqual(gainPair([1,99,3,4,5,6]),[1,3]);assert.deepEqual(actualGainWindow({valid:true,windows:[{window_ms:1000,kp:2,kd:4,covered_ms:500}]},1000),{kp:2,kd:4,coverage:.5});assert.equal(actualGainWindow({valid:false,windows:[]},1000).kp,null)})
test('replay forbids every hardware action, stale forbids motion, stop requires known host',()=>{for(const action of fd2Actions('V5'))assert.equal(canFd2Command({firmware:'V5',status_fresh:true,host_online:true,replay:{active:true}},action),false);assert.equal(canFd2Command({firmware:'V5',host_online:true},'trajectory'),false);assert.equal(canFd2Command({firmware:'V5',host_online:true},'stop'),true);assert.equal(canFd2Command({firmware:'V5',status_fresh:true},'start-swing'),false)})
import {fd2Freshness,canDispatchFd2,commandFailure,replayCacheNow,gainSegments,swingLabel} from '../src/fd2Presentation.js'
test('pending request never blocks stop or estop, but blocks motion',()=>{const s={firmware:'V5',host_online:true,status_fresh:true,observed_at:10,status_age_s:0,fd2:{control_fresh:true,control_age_s:0}};assert(canDispatchFd2(s,'stop',true,10000));assert(canDispatchFd2(s,'estop',true,10000));assert(!canDispatchFd2(s,'balance',true,10000));assert(!canDispatchFd2({...s,replay:{active:true}},'stop',true,10000))})
test('snapshot ages accumulate, independent channels expire without new polling',()=>{const s={status_fresh:true,display_fresh:true,observed_at:10,status_age_s:.1,fd2:{control_fresh:true,control_age_s:.4,diagnostic_fresh:true,diagnostic_age_s:.3}};assert.deepEqual(fd2Freshness(s,10200),{status:true,control:false,diagnostic:false});assert(!canFd2Command({...s,firmware:'V5'},'trajectory',10200));assert(fd2Freshness({...s,replay:{active:true}},99999).control)})
test('known server rejection remains rejected, transport failure unknown',()=>{assert.equal(commandFailure({httpStatus:409,result:{state:'rejected',message:'busy'}}).state,'rejected');assert.equal(commandFailure(new Error('timeout')).state,'indeterminate')})
test('replay cache retains last received clock after wall clock advances',()=>{const rows=[{received_at:10,plot_at:9}];assert.equal(replayCacheNow({replay:{active:true}},rows,900000),10000);assert.equal(appendFd2Samples(rows,[],replayCacheNow({replay:{active:true}},rows,900000)).length,1)})
test('gain trend splits board gaps, explicit gaps and epochs',()=>{const r=(t,extra={})=>({plot_at:t,angle_kp:1,session_epoch:1,...extra});assert.equal(gainSegments([r(1),r(1.005),r(1.02),r(1.025,{gap_before:true}),r(1.03,{session_epoch:2})],'angle_kp').length,4);assert.equal(swingLabel(0),'SWM4');assert.equal(swingLabel(1),'SA21');assert.equal(swingLabel(2),'144')})
test('120 second 200Hz plots reduce by pixels, preserve peaks and gaps without copying raw objects',()=>{const rows=Array.from({length:24001},(_,i)=>({plot_at:i*.005,measurement_at:i*.005,session_epoch:1,channels:['angle_deg'],angle_deg:i===12345?999:i===12346?-999:0,raw:{wide_encoder:'1152921504606846976'},gap_before:i===12347}));const o=createFd2WaveformOption(rows,120000,300,[],1500,120000,'horizontal');const points=o.series.find(s=>s.id==='angle_deg').data;assert(points.length<3000);assert(points.some(p=>p[1]===999));assert(points.some(p=>p[1]===-999));assert(points.some(p=>p[1]===null));assert(points.every(p=>p.length===2));assert.equal(rows.length,24001);assert.equal(rows[12345].raw.wide_encoder,'1152921504606846976')})
import {fd2RmsAngle,formatMotionValue} from '../src/fd2Presentation.js'
import * as fd2Presentation from '../src/fd2Presentation.js'
test('FD2 RMS uses board plot time despite delayed arrival and a frozen replay clock',()=>{const rows=[{plot_at:1,received_at:100,angle_deg:999,channels:['angle_deg']},{plot_at:9,received_at:100,angle_deg:3,channels:['angle_deg']},{plot_at:10,received_at:200,angle_deg:4,channels:['angle_deg']},{plot_at:10,received_at:200,angle_deg:800,channels:['final_target_deg']},{plot_at:10,received_at:200,angle_deg:null,channels:['angle_deg']}];assert.equal(fd2RmsAngle(rows,10000),Math.sqrt(12.5));assert.equal(fd2RmsAngle(rows,10000),Math.sqrt(12.5));assert.equal(formatMotionValue('actual_count','1152921504606846976'),'1152921504606846976');assert.equal(formatMotionValue('measured_velocity_deg_s',6.923076923),'6.923')})

const displayRows = (angle = 1, arm = 900, target = 920) => [
  {plot_at:1, channels:['angle_deg','alpha_ref_deg','arm_angle_deg','arm_reference_deg'], angle_deg:angle, alpha_ref_deg:0, arm_angle_deg:arm, arm_reference_deg:arm},
  {plot_at:1, channels:['final_target_deg'], final_target_deg:target},
]
test('small balance motion uses a wide, symmetric degree scale without altering samples', () => {
  const rows=displayRows(.2), before=structuredClone(rows)
  const option=createFd2WaveformOption(rows,1000)
  assert.equal(option.yAxis[0].min,-60)
  assert.equal(option.yAxis[0].max,60)
  assert.equal(option.yAxis[0].interval,20)
  assert.equal(option.yAxis[2].min,-100)
  assert.equal(option.yAxis[2].max,100)
  assert.deepEqual(rows,before)
  assert.equal(option.series[0].data[0][1],.2)
})
test('display scales expand to include outliers and independent final targets, remain stable until reset', () => {
  const expanded=createFd2WaveformOption(displayRows(-85,900,2200),1000)
  assert(expanded.yAxis[0].min < -85)
  assert(expanded.yAxis[1].min <= 900)
  assert(expanded.yAxis[1].max > 2200)
  const stable=createFd2WaveformOption(displayRows(.1,1000,1100),1000,500,[],600,10000,'stacked',{previousAxes:expanded.yAxis})
  assert.deepEqual(stable.yAxis.map(a=>[a.min,a.max,a.interval]),expanded.yAxis.map(a=>[a.min,a.max,a.interval]))
  const reset=createFd2WaveformOption(displayRows(.1,1000,1100),1000)
  assert.equal(reset.yAxis[0].min,-60)
  assert.equal(reset.yAxis[0].max,60)
})
test('arm plot makes small changes visible while retaining cumulative degrees and explicit narrow angle preset', () => {
  const option=createFd2WaveformOption(displayRows(.1,99999,100001),1000,500,[],600,10000,'stacked',{angleExtent:30})
  assert.equal(option.yAxis[0].min,-30)
  assert.equal(option.yAxis[0].max,30)
  assert.equal(option.yAxis[0].interval,10)
  assert(option.yAxis[1].max-option.yAxis[1].min <= 120)
  assert(option.yAxis[1].min <= 99999 && option.yAxis[1].max >= 100001)
  assert.equal(option.series.find(s=>s.id==='arm_angle_deg').data[0][1],99999)
})
test('new gesture feedback follows event sequence, never a retained first event or a reconnect', () => {
  const state=(sequence,code=2,session='a',firmware='V2')=>({session,firmware,gesture_event:{sequence,code}})
  assert.equal(fd2Presentation.isNewFd2Gesture?.(state(10),state(11),true),true)
  assert.equal(fd2Presentation.isNewFd2Gesture(undefined,state(11),true),false)
  assert.equal(fd2Presentation.isNewFd2Gesture(state(10),state(10),true),false)
  assert.equal(fd2Presentation.isNewFd2Gesture(state(10),state(11,2,'b'),true),false)
  assert.equal(fd2Presentation.isNewFd2Gesture(state(10),state(11),false),false)
  assert.equal(fd2Presentation.isNewFd2Gesture(state(10),state(11,0),true),false)
  assert.equal(fd2Presentation.isNewFd2Gesture(state(10),state(0),true),false)
  assert.equal(fd2Presentation.isNewFd2Gesture(state(10),state(11,2,'a','V5'),true),false)
})
test('motion labels preserve all six firmware preset identities and never treat ACK as completion', () => {
  assert.equal(fd2Presentation.fd2MotionLabel?.(0x11),'相对定位 60°')
  assert.equal(fd2Presentation.fd2MotionLabel(0x16),'相对定位 360°')
  assert.equal(fd2Presentation.fd2MotionLabel(0x21),'正弦往返')
  assert.equal(fd2Presentation.fd2MotionLabel(0x26),'扫频往返')
  assert.equal(fd2Presentation.fd2MotionLabel(null),'—')
  assert.equal(fd2Presentation.fd2MotionLabel(99),'动作 99')
  assert.deepEqual(fd2Presentation.fd2PositionPresets.map(p=>p.value),[1,2,3,4,5,6])
  assert.deepEqual(fd2Presentation.fd2TrajectoryPresets.map(p=>p.value),[1,2,3,4,5,6])
})

test('3D prefers a real unclipped pose, and never turns a clipped control angle into an inferred down pose',()=>{
  assert.equal(fd2Presentation.fd2TwinAngle?.({pendulum_pose_deg:179.99,angle_deg:85.0708,angle_display_limited:true}),179.99)
  assert.equal(fd2Presentation.fd2TwinAngle({pendulum_pose_deg:null,angle_deg:85.0708,angle_display_limited:true}),null)
  assert.equal(fd2Presentation.fd2TwinAngle({pendulum_pose_deg:null,angle_deg:-.084}),null)
  assert.equal(fd2Presentation.fd2TwinAngle({pendulum_pose_deg:NaN,angle_deg:0}),null)
  assert.equal(fd2Presentation.fd2TwinAngle({angle_deg:-.084}),-.084)
  assert.equal(fd2Presentation.fd2TwinAngle({angle_deg:null}),null)
})

test('fault 1 is angle protection and is distinct from the ADC OTR fault',()=>{
  assert.equal(fd2Presentation.fd2FaultLabel?.(1),'角度越界保护（1）')
  assert.equal(fd2Presentation.fd2FaultLabel(4),'ADC 越量程保护（4）')
  assert.equal(fd2Presentation.fd2FaultLabel(99),'未知故障（99）')
})
