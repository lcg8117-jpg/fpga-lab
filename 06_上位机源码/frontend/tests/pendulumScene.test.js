import test from 'node:test'
import assert from 'node:assert/strict'
import * as THREE from 'three'
let createBenchSupport, setSceneMode
try { ({createBenchSupport, setSceneMode} = await import('../src/pendulumScene.js')) }
catch (error) { if (error.code !== 'ERR_MODULE_NOT_FOUND') throw error }

test('固定台架沿用历史支撑尺寸并接触展示地面', () => {
  assert.equal(typeof createBenchSupport, 'function')
  const support = createBenchSupport()
  const bounds = new THREE.Box3().setFromObject(support)
  assert.equal(support.name, 'displaySupport')
  assert.ok(Math.abs(bounds.min.y + 1) < 1e-6)
  assert.ok(Math.abs(bounds.max.x - 1.08) < 1e-6)
  assert.equal(support.children.length, 3)
})

test('场景切换只影响底盘和台架，不改摆杆姿态或挂载', () => {
  assert.equal(typeof setSceneMode, 'function')
  const base = new THREE.Group(), chassis = new THREE.Group(), arm = new THREE.Group(), load = new THREE.Group()
  chassis.name = 'carChassis'; arm.name = 'armPivot'; load.name = 'pendulum-load'
  arm.rotation.y = .3; load.visible = true; arm.add(load)
  base.add(chassis, createBenchSupport(), arm)
  for (const mode of ['bench','car','bench']) {
    setSceneMode(base, mode)
    assert.equal(chassis.visible, mode === 'car')
    assert.equal(base.getObjectByName('displaySupport').visible, mode === 'bench')
    assert.equal(arm.rotation.y, .3)
    assert.equal(load.visible, true)
  }
})
