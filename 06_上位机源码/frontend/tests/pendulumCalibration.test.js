import test from 'node:test'
import assert from 'node:assert/strict'
import * as THREE from 'three'
let calibrationDefaults, normalizeCalibration, calibrationStorageKey, displayAlphaDeg, calibrateDownPose
try { ({calibrationDefaults, normalizeCalibration, calibrationStorageKey, displayAlphaDeg, calibrateDownPose} = await import('../src/pendulumCalibration.js')) }
catch(error) { if(error.code!=='ERR_MODULE_NOT_FOUND')throw error }

test('当前 SG21 自然下垂读数显示为世界竖直向下，原始值保持不变',()=>{
  assert.equal(typeof calibrationDefaults,'function')
  const raw=-182.138, config=calibrationDefaults('SG21')
  const degrees=displayAlphaDeg(raw,config)
  assert.ok(Math.abs(degrees-180)<1e-9)
  const direction=new THREE.Vector3(0,1,0).applyAxisAngle(new THREE.Vector3(1,0,0),degrees*Math.PI/180)
  assert.ok(direction.distanceTo(new THREE.Vector3(0,-1,0))<1e-9)
  assert.equal(raw,-182.138)
  assert.equal(config.alphaTwoPointEnabled,false,'不用旧固件的两点角度拉伸当前读数')
})

test('当前显示偏移保持角度尺度，不把附近运动吸附为下垂',()=>{
  assert.equal(typeof displayAlphaDeg,'function')
  const config=calibrationDefaults('SG21')
  assert.ok(Math.abs((displayAlphaDeg(-181.138,config)-displayAlphaDeg(-182.138,config))+1)<1e-9)
})

test('显示标定按固件隔离，SA21 不自动采用 SG21 的实物下垂参考',()=>{
  assert.equal(typeof calibrationStorageKey,'function')
  assert.notEqual(calibrationStorageKey('SG21'),calibrationStorageKey('SA21'))
  assert.notEqual(calibrationStorageKey('SG21'),'j280:twin3d:calibration:v1')
  assert.equal(calibrationDefaults('SA21').alphaOffsetDeg,0)
  assert.equal(normalizeCalibration(null,'SG21').alphaDownDeg,-182.138)
})

test('SP24 显示标定与旧固件及通用配置隔离，不沿用旧版实测偏移',()=>{
  assert.notEqual(calibrationStorageKey('SP24'),calibrationStorageKey('generic'))
  assert.notEqual(calibrationStorageKey('SP24'),calibrationStorageKey('SG21'))
  assert.notEqual(calibrationStorageKey('SP24'),calibrationStorageKey('SA21'))
  assert.equal(calibrationDefaults('SP24').alphaOffsetDeg,0)
  assert.equal(displayAlphaDeg(-180,calibrationDefaults('SP24')),180)
})

test('FD2 未裁切姿态使用独立新版标定，不继承旧控制显示角的标定',()=>{
  for(const firmware of ['V1','V2','V3','V4','V5']) {
    assert.equal(calibrationStorageKey(firmware),`j280:twin3d:calibration:v3:${firmware}`)
    assert.equal(displayAlphaDeg(0,calibrationDefaults(firmware)),0)
    assert.equal(displayAlphaDeg(180,calibrationDefaults(firmware)),-180)
  }
  assert.equal(calibrationStorageKey('SP24'),'j280:twin3d:calibration:v2:SP24')
})

test('手动下垂对齐只产生显示配置，并保留旋臂配置和角度方向',()=>{
  assert.equal(typeof calibrateDownPose,'function')
  const original={...calibrationDefaults('SA21'),thetaOffsetDeg:17,alphaDirection:1,alphaTwoPointEnabled:true}
  const before={...original}
  const result=calibrateDownPose(original,173.25)
  assert.deepEqual(original,before)
  assert.equal(result.thetaOffsetDeg,17)
  assert.equal(result.alphaDirection,1)
  assert.equal(result.alphaTwoPointEnabled,false)
  assert.equal(displayAlphaDeg(173.25,result),180)
  assert.throws(()=>calibrateDownPose(original,null),/有效/)
})

test('显式两点显示标定仍保持两个参考点和跨周连续性',()=>{
  assert.equal(typeof displayAlphaDeg,'function')
  const config={...calibrationDefaults('SA21'),alphaTwoPointEnabled:true,alphaUprightDeg:-.396,alphaDownDeg:-169.937,alphaOffsetDeg:0}
  assert.ok(Math.abs(displayAlphaDeg(-.396,config))<1e-9)
  assert.ok(Math.abs(Math.abs(displayAlphaDeg(-169.937,config))-180)<1e-9)
  assert.ok(Math.abs(displayAlphaDeg(-179.999,config)-displayAlphaDeg(180.001,config))<1e-9)
})
