import test from 'node:test'
import assert from 'node:assert/strict'
import { createCarSimulation, createDemoSequence } from '../src/carSimulation.js'

test('idle simulation exposes the delivery CAR2 fields without claiming a hardware connection', () => {
  const car = createCarSimulation(0)
  const row = car.sample(0)
  assert.equal(row.source, 'car-simulation')
  assert.equal(row.active, 0)
  assert.equal(row.servo_compare, 750)
  assert.equal(row.left_count, 0)
  assert.equal(row.left_pwm8400, 0)
  assert.equal(car.command('STATUS', 0), 'OK STATUS BLUETOOTH_HOLD_V2')
  assert.throws(() => car.command('FWDLEFT', 0), /NOT_ARMED/)
})

test('one V2 lease cannot move the model past 500 ms even after a large clock gap', () => {
  const car = createCarSimulation(0)
  car.command('ARM', 0)
  car.command('FWDCENTER', 0)
  const row = car.sample(5000)
  assert.equal(row.left_count, 300)
  assert.equal(row.right_count, 300)
  assert.equal(row.active, 0)
  assert.equal(row.left_pwm8400, 0)
  assert.equal(row.stop_reason, 1)
  assert.equal(row.servo_compare, 750)
})

test('KEEP renews drive and requested direction while STOP resets both without restarting', () => {
  const car = createCarSimulation(0)
  car.command('ARM', 0)
  car.command('FWDLEFT', 0)
  car.command('KEEPLEFT', 400)
  assert.equal(car.sample(700).active, 1)
  assert.equal(car.sample(700).servo_compare, 700)
  car.command('STOP', 700)
  const parked = car.sample(800)
  assert.equal(parked.left_count, 420)
  assert.equal(parked.left_pwm8400, 0)
  assert.equal(parked.servo_compare, 750)
  assert.throws(() => car.command('KEEPLEFT', 800), /NOT_ACTIVE/)
})

test('standalone steering never drives wheels and expires to center', () => {
  const car = createCarSimulation(0)
  car.command('RIGHT', 0)
  assert.equal(car.sample(100).servo_compare, 800)
  assert.equal(car.sample(100).active, 0)
  assert.equal(car.sample(501).servo_compare, 750)
  assert.equal(car.sample(501).left_count, 0)
})

test('a delayed presentation tick cancels the sequence and requires explicit restart', () => {
  const demo = createDemoSequence(0)
  demo.start(0)
  demo.tick(100)
  assert.equal(demo.sample(100).active, 1)
  demo.tick(500)
  assert.equal(demo.running, false)
  assert.equal(demo.sample(500).active, 0)
  assert.equal(demo.sample(500).servo_compare, 750)
  demo.tick(600)
  assert.equal(demo.running, false)
})

test('the sequence changes drive direction through a stopped interval and obeys cancellation', () => {
  const demo = createDemoSequence(0)
  demo.start(0)
  let sawForward = false, sawReverse = false, parkedBeforeReverse = false
  for (let now = 100; now <= 12000; now += 100) {
    demo.tick(now)
    const row = demo.sample(now)
    if (row.left_pwm8400 > 0) sawForward = true
    if (sawForward && !row.active) parkedBeforeReverse = true
    if (row.left_pwm8400 < 0) {
      assert.equal(parkedBeforeReverse, true)
      sawReverse = true
    }
  }
  assert.equal(sawForward, true)
  assert.equal(sawReverse, true)
  demo.stop(12000)
  assert.equal(demo.running, false)
  assert.equal(demo.sample(12100).active, 0)
})
