import test from 'node:test'
import assert from 'node:assert/strict'
import {isFd2, fd2Actions, canFd2Command, gestureEventLabel} from '../src/fd2Presentation.js'

test('ADB1 DownStop labels its stop and wave while V1 and V2 retain their meanings', () => {
  assert.equal(gestureEventLabel('ADB1', 4), '下滑STOP')
  assert.equal(gestureEventLabel('ADB1', 9), '挥手（无停止动作）')
  for (const version of ['V1', 'V2']) {
    assert.equal(gestureEventLabel(version, 4), '下滑')
    assert.equal(gestureEventLabel(version, 9), '挥手STOP')
  }
  assert.equal(gestureEventLabel('ADB1', 3), '上滑')
  assert.equal(gestureEventLabel('ADB1', 99), '—')
})

test('ADB1 exposes swing, manual balance and stop but prevents demo control', () => {
  assert.equal(isFd2('ADB1'), true)
  for (const action of ['start-swing','balance','stop']) assert.ok(fd2Actions('ADB1').includes(action))
  assert.equal(canFd2Command({firmware:'ADB1', host_online:true, replay:{active:true}}, 'stop'), false)
  const live={firmware:'ADB1', host_online:true, status_fresh:true, status_age_s:0,
              observed_at:100, replay:{active:false}}
  for (const action of ['start-swing','balance','stop']) assert.equal(canFd2Command(live,action,100000),true)
})
