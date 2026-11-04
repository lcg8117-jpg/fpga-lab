import test from 'node:test'
import assert from 'node:assert/strict'
let createCarInput, keyAction
try { ({ createCarInput, keyAction } = await import('../src/carInput.js')) }
catch (error) { if (error.code !== 'ERR_MODULE_NOT_FOUND') throw error }

test('键盘和屏幕输入共用方向，冲突输入归零，不锁定转向', () => {
  assert.equal(typeof createCarInput, 'function')
  const input = createCarInput()
  input.press('p:1', 'left')
  input.press('k:KeyW', 'forward')
  assert.deepEqual(input.intent(), {direction:'FWD', steering:'LEFT'})
  input.press('k:KeyS', 'reverse')
  input.press('p:2', 'right')
  assert.deepEqual(input.intent(), {direction:null, steering:'CENTER'})
  input.release('p:1')
  input.release('k:KeyS')
  assert.deepEqual(input.intent(), {direction:'FWD', steering:'RIGHT'})
  input.release('p:2')
  assert.equal(input.intent().steering, 'CENTER')
})

test('STOP 抑制仍按住的按键，全部释放后才接受同一按键的新动作', () => {
  assert.equal(typeof createCarInput, 'function')
  const input = createCarInput()
  input.press('k:KeyW', 'forward')
  input.cancel()
  assert.equal(input.press('k:KeyW', 'forward'), false)
  assert.deepEqual(input.intent(), {direction:null, steering:'CENTER'})
  input.release('k:KeyW')
  assert.equal(input.press('k:KeyW', 'forward'), true)
  assert.equal(input.intent().direction, 'FWD')
})

test('键盘支持 WASD、方向键，忽略其他按键', () => {
  assert.equal(typeof keyAction, 'function')
  for (const code of ['KeyW','ArrowUp']) assert.equal(keyAction(code), 'forward')
  for (const code of ['KeyS','ArrowDown']) assert.equal(keyAction(code), 'reverse')
  assert.equal(keyAction('KeyA'), 'left')
  assert.equal(keyAction('ArrowRight'), 'right')
  assert.equal(keyAction('Enter'), null)
})
