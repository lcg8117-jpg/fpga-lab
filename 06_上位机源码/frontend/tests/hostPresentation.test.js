import test from 'node:test'
import assert from 'node:assert/strict'
import { appendSamples, requestLabel, canConfigure, rmsAngle, reconcileRequest } from '../src/hostPresentation.js'
import { createWaveformOption } from '../src/waveform.js'

test('分页样本按 ID 去重，源切换清空，RMS 不重复计入 PWM 页',()=>{
  const rows=[{sample_id:'a',received_at:10,angle_deg:3},{sample_id:'b',received_at:10.1,duty_actual_percent:5}]
  assert.equal(appendSamples(rows,rows,11000).length,2)
  assert.equal(rmsAngle(rows,11000),3)
  assert.equal(rmsAngle(rows,14000),null)
  assert.deepEqual(appendSamples(rows,[],11000,true),[])
})

test('ACK 与实际验证完成分开呈现；故障或过期状态不开放配置',()=>{
  assert.match(requestLabel({state:'accepted'}),/等待/)
  assert.match(requestLabel({state:'verified'}),/读回/)
  assert.match(requestLabel({state:'indeterminate'}),/未知/)
  const data={status_fresh:true,status:{pwm:0,pwm_zero:true,stopped_config_allowed:true}}
  assert.equal(canConfigure(data),true)
  assert.equal(canConfigure({...data,status_fresh:false}),false)
  assert.equal(canConfigure({...data,status:{...data.status,run_session:true}}),false)
})

test('分页观测不会在另一个通道插入伪造缺口或复制缓存读数',()=>{
  const rows=[
    {received_at:10,channels:['angle_deg','angle_velocity_deg_s'],angle_deg:1,angle_velocity_deg_s:2},
    {received_at:10.1,channels:['duty_actual_percent'],duty_actual_percent:5},
    {received_at:10.2,channels:['angle_deg','angle_velocity_deg_s'],angle_deg:2,angle_velocity_deg_s:3},
  ]
  const option=createWaveformOption(rows,11000)
  assert.deepEqual(option.series[0].data,[[10000,1],[10200,2]])
  assert.deepEqual(option.series[2].data,[[10100,5]])
})

test('断流或 Host 会话变化将失联的待确认操作标为未知，保留 ID 供查询',()=>{
  const record={request_id:'same-id',state:'accepted',host_session:'old',host_firmware:'SG21'}
  const recovered=reconcileRequest(record,{status_fresh:true,session:'new',firmware:'SG21',requests:{}})
  assert.equal(recovered.state,'indeterminate')
  assert.equal(recovered.request_id,'same-id')
  const disconnected=reconcileRequest(record,{status_fresh:false,session:'old',firmware:'SG21',requests:{}})
  assert.equal(disconnected.state,'indeterminate')
  assert.equal(reconcileRequest(record,{status_fresh:true,session:'old',firmware:'SG21',requests:{'same-id':{state:'verified'}}}).state,'verified')
  assert.equal(reconcileRequest(record,{status_fresh:true,session:'new',firmware:'SG21',requests:{'same-id':{state:'verified'}}}).state,'indeterminate')
})
