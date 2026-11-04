import test from 'node:test'
import assert from 'node:assert/strict'
import {createCarInput,keyAction} from '../src/carInput.js'
import * as controls from '../src/useCarControl.js'

test('F8 and pointer route holds are independent and route excludes manual input',()=>{
 const input=createCarInput(); assert.equal(keyAction('F8'),'route')
 input.press('key:F8','route'); input.press('pointer:2','route'); input.press('key:W','forward')
 assert.deepEqual(input.intent(),{direction:null,steering:'CENTER',route:1})
 input.release('key:F8'); assert.equal(input.intent().route,1)
 input.release('pointer:2'); assert.equal(input.intent().direction,'FWD')
})
test('completed route stays suppressed through held heartbeats until every source releases',()=>{
 const input=createCarInput(); input.press('key:F8','route'); input.press('pointer:2','route')
 input.completeRoute(); assert.equal(input.intent().route,0)
 input.release('key:F8'); assert.equal(input.press('key:F8','route'),false)
 input.release('key:F8'); input.release('pointer:2'); assert.equal(input.press('key:F8','route'),true)
})
const status=()=>({connected:true,identified:true,feedback_fresh:true,session:'s1',received_at:Date.now()/1000,profile:1,motion:null,telemetry:{active:0}})
function fixture(){
 const calls=[]; let pending=null; const state=status()
 const control=controls.createCarControl({request:async(path,body)=>{calls.push([path,body]); if(pending)return await pending; return {...state,session: path==='/stop'||path==='/brake'?'s2':state.session}},isActive:()=>true})
 Object.assign(control,{apiOnline:true,status:state});return {control,calls,defer(p){pending=p}}
}
test('live speed and route parameters use RC83 fields and boost is a single sequence',async()=>{
 assert.equal(typeof controls.createCarControl,'function');const {control,calls}=fixture();await control.enable()
 control.press('key:W','forward');await new Promise(r=>setImmediate(r))
 control.speed_q8=1280;control.slow_percent=75;control.steer_percent=60;await control.sendIntent()
 assert.deepEqual(calls.at(-1)[1],{session:'s2',sequence:3,direction:'FWD',steering:'CENTER',route:0,speed_q8:1280,slow_percent:75,steer_percent:60})
 control.status.motion='manual';control.status.telemetry.active=1;control.peak_q8=2048;await control.boost();assert.equal(calls.at(-1)[0],'/boost');assert.equal(calls.at(-1)[1].peak_q8,2048)
 await control.brake();assert.equal(calls.at(-1)[0],'/brake');assert.equal(control.enabled,false)
})
test('enable response cannot restore control after page cancellation',async()=>{
 assert.equal(typeof controls.createCarControl,'function');const f=fixture(); let resolve;f.defer(new Promise(r=>resolve=r)); const enabling=f.control.enable()
 f.control.deactivate('settings');resolve(status());await enabling;assert.equal(f.control.enabled,false)
})

test('Shift auto-repeat is ignored, space brakes and Esc stops independently',async()=>{
 const {control,calls}=fixture();await control.enable(); control.status.motion='manual';control.status.telemetry.active=1
 control.press('key:W','forward');await new Promise(r=>setImmediate(r));
 const event=(code,repeat=false)=>({code,repeat,preventDefault(){},target:{}})
 control.keydown(event('ShiftLeft'));control.keydown(event('ShiftLeft',true));control.keydown(event('ShiftLeft'))
 await new Promise(r=>setImmediate(r));assert.equal(calls.filter(([p])=>p==='/boost').length,1)
 control.keyup(event('ShiftLeft'));control.keydown(event('ShiftLeft'));await new Promise(r=>setImmediate(r));assert.equal(calls.filter(([p])=>p==='/boost').length,2)
 control.keydown(event('Space'));await new Promise(r=>setImmediate(r));assert.equal(calls.at(-1)[0],'/brake')
 await control.enable();control.keydown(event('Escape'));await new Promise(r=>setImmediate(r));assert.equal(calls.at(-1)[0],'/stop')
})
test('boost is forbidden with stale feedback or an unconfirmed manual run',async()=>{
 const {control,calls}=fixture();await control.enable();control.press('key:W','forward');await new Promise(r=>setImmediate(r))
 await control.boost();assert.equal(calls.filter(([p])=>p==='/boost').length,0)
 control.status.motion='manual';control.status.telemetry.active=1;control.status.feedback_fresh=false
 await control.boost();assert.equal(calls.filter(([p])=>p==='/boost').length,0)
})
test('inactive page does not submit a movement intent',async()=>{
 let active=true;const calls=[];const control=controls.createCarControl({request:async(p,b)=>{calls.push([p,b]);return status()},isActive:()=>active})
 control.status=status();control.apiOnline=true;await control.enable();active=false
 control.press('key:W','forward');await control.sendIntent();assert.equal(calls.filter(([p])=>p==='/intent').length,1)
})

test('releasing one route source preserves the other and cancellation wins a parking response',async()=>{
 const f=fixture();await f.control.enable();f.control.press('key:F8','route');await new Promise(r=>setImmediate(r));
 f.control.press('route:pointer:1','route');await new Promise(r=>setImmediate(r));
 const stopCount=f.calls.filter(([p])=>p==='/stop').length
 f.control.release('key:F8');await new Promise(r=>setImmediate(r));assert.equal(f.calls.filter(([p])=>p==='/stop').length,stopCount)
 assert.equal(f.calls.at(-1)[1].route,1)
 let resolve;f.defer(new Promise(r=>resolve=r));f.control.release('route:pointer:1');f.control.deactivate('scene changed')
 resolve(status());await new Promise(r=>setImmediate(r));assert.equal(f.control.enabled,false)
})

test('BRAKE upgrades a pending release STOP once and neither response restores enabled',async()=>{
 const calls=[];const pending={};let hold=false
 const control=controls.createCarControl({isActive:()=>true,request:async(path,body)=>{
  calls.push([path,body]);if(hold && (path==='/stop'||path==='/brake'))return await new Promise(resolve=>pending[path]=resolve)
  return {...status(),session:'s2'}
 }})
 control.apiOnline=true;control.status=status();await control.enable();hold=true
 const parked=control.stop('released',true);const emergency=control.brake();control.brake()
 assert.equal(calls.filter(([p])=>p==='/brake').length,1)
 assert.equal(calls.findLast(([p])=>p==='/brake')[1].session,'s2')
 pending['/brake']({...status(),session:'s3'});await emergency
 pending['/stop']({...status(),session:'s3'});await parked
 assert.equal(control.enabled,false);assert.equal(control.status.session,'s3')
 assert.equal(calls.filter(([p])=>p==='/intent').length,1)
})
test('Space remains available after release STOP disables driving, but respects page prohibition',async()=>{
 const calls=[];let allowed=true;let hold=false;const pending={}
 const control=controls.createCarControl({isActive:()=>true,isAllowed:()=>allowed,request:async(path,body)=>{
  calls.push([path,body]);if(hold && (path==='/stop'||path==='/brake'))return await new Promise(resolve=>pending[path]=resolve)
  return {...status(),session:'s2'}
 }})
 control.apiOnline=true;control.status=status();await control.enable();hold=true
 const parked=control.stop('released',true)
 const space=repeat=>({code:'Space',repeat,preventDefault(){},target:{}})
 control.keydown(space(false));control.keydown(space(true));control.keydown(space(false))
 assert.equal(calls.filter(([p])=>p==='/brake').length,1)
 pending['/stop']({...status(),session:'s3'});pending['/brake']({...status(),session:'s3'});await parked
 allowed=false;control.keyup(space(false));control.keydown(space(false));control.brake()
 assert.equal(calls.filter(([p])=>p==='/brake').length,1);assert.equal(control.enabled,false)
})

test('deactivate after confirmed cancellation clears local input without another hardware STOP',async()=>{
 const {control,calls}=fixture();await control.enable();await control.deactivate('replay entered')
 const count=calls.filter(([p])=>p==='/stop').length
 await control.deactivate('settings opened from Space native click');await control.deactivate('blur');await control.deactivate('scene changed')
 assert.equal(calls.filter(([p])=>p==='/stop').length,count);assert.equal(control.enabled,false)
})
test('deactivate still cancels a pending connect and parks its late connection result',async()=>{
 const calls=[];let resolve
 const control=controls.createCarControl({isActive:()=>true,request:async(path,body)=>{
  calls.push([path,body]);if(path==='/connect')return await new Promise(r=>resolve=r)
  return {...status(),session:'s2'}
 }})
 control.apiOnline=true;control.selected='fixture';const connection=control.connect();await control.deactivate('replay entered')
 resolve({...status(),session:'connected'});await connection;await new Promise(r=>setImmediate(r))
 assert.equal(calls.filter(([p])=>p==='/stop').length,1);assert.equal(control.enabled,false)
})
