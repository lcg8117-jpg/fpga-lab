import test from 'node:test'
import assert from 'node:assert/strict'
import {isFd2, fd2Actions, gestureEventLabel, dualLinkLabel, v4LastPositionLabel} from '../src/fd2Presentation.js'
import {calibrationStorageKey} from '../src/pendulumCalibration.js'

test('new deliveries expose only their own capabilities', () => {
  for (const profile of ['ADB1_FASTP', 'V4_R28', 'V4_USB_BLE', 'G3_C1', 'DS13']) assert.equal(isFd2(profile), true)
  assert(fd2Actions('ADB1_FASTP').includes('start-swing'))
  assert(fd2Actions('ADB1_FASTP').includes('adapt-enable'))
  assert(fd2Actions('ADB1_FASTP').includes('adapt-disable'))
  assert(fd2Actions('V4_R28').includes('position'))
  assert(fd2Actions('V4_USB_BLE').includes('position'))
  for (const profile of ['ADB1_FASTP', 'V4_R28', 'V4_USB_BLE']) {
    assert(fd2Actions(profile).includes('read-gains'))
    assert(fd2Actions(profile).includes('set-gains'))
  }
  for (const profile of ['V4_R28', 'V4_USB_BLE', 'G3_C1', 'DS13']) assert(!fd2Actions(profile).includes('start-swing'))
  for (const profile of ['G3_C1', 'DS13']) assert(!fd2Actions(profile).includes('position'))
  assert(fd2Actions('G3_C1').includes('play-c1'))
  assert(!fd2Actions('DS13').includes('play-c1'))
  assert.equal(gestureEventLabel('ADB1_FASTP', 4), '下滑STOP')
})

test('new profile calibration keys are independent of legacy and one another', () => {
  const keys = ['ADB1_FASTP','V4_R28','V4_USB_BLE','G3_C1','DS13','V5'].map(calibrationStorageKey)
  assert.equal(new Set(keys).size, keys.length)
  for (const key of keys) assert.match(key, /:v3:/)
})

test('dual link labels expire if the Host observation stops updating', () => {
  const state = { host_online:true, observed_at:100, transport:{usb_connected:true,usb_ready:true,ble_connected:true,ble_ready:false} }
  assert.equal(dualLinkLabel(state,'usb',100500), '有回包')
  assert.equal(dualLinkLabel(state,'ble',100500), '等待回包')
  assert.equal(dualLinkLabel(state,'usb',101100), '状态未知')
  assert.equal(dualLinkLabel({...state,host_online:false},'usb',100500), '状态未知')
  assert.equal(dualLinkLabel({...state,transport:{}},'usb',100500), '未连接')
  assert.equal(dualLinkLabel({...state,transport:{ble_connected:true,ble_quarantined:true,usb_ready:true}},'ble',100500), '异常，位置改走USB')
})

test('V4 last position describes only an acknowledged, valid preset', () => {
  assert.equal(v4LastPositionLabel({value:3,route:'ble'}), '180° · 蓝牙')
  assert.equal(v4LastPositionLabel({value:6,route:'usb'}), '360°')
  assert.equal(v4LastPositionLabel({value:7,route:'ble'}), '尚无已确认的位置指令')
  assert.equal(v4LastPositionLabel(null), '尚无已确认的位置指令')
})
