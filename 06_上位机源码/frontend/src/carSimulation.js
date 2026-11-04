// Offline presentation model of the teammate delivery's BLUETOOTH_HOLD_V2 / CAR2
// contract. It never opens a transport and does not model pendulum dynamics.
const STEERING = { LEFT: 700, CENTER: 750, RIGHT: 800 }
const LEASE_MS = 500

export function createCarSimulation(origin = 0) {
  let last = origin, count = 0, direction = 0, servo = 750, active = false
  let leaseUntil = 0, servoUntil = 0, armedUntil = 0, coolUntil = origin, reason = 0
  const stop = (now, stopReason = 0) => {
    active = false
    direction = 0
    servo = 750
    armedUntil = 0
    coolUntil = now + 500
    reason = stopReason
  }
  function advance(now) {
    if (!Number.isFinite(now) || now < last) throw new RangeError('Invalid simulation clock')
    if (active) {
      count += Math.max(0, Math.min(now, leaseUntil) - last) * direction * .6
      if (now >= leaseUntil) stop(leaseUntil, 1)
    }
    if (now >= servoUntil) servo = 750
    last = now
  }
  return {
    command(command, now) {
      advance(now)
      if (command === 'STATUS') return 'OK STATUS BLUETOOTH_HOLD_V2'
      if (command === 'STOP') { stop(now); return 'OK STOP' }
      if (command in STEERING) {
        servo = STEERING[command]
        servoUntil = now + LEASE_MS
        return 'OK STEER'
      }
      if (command === 'ARM') {
        if (active) throw new Error('ERR BUSY')
        if (now < coolUntil) throw new Error('ERR COOLDOWN')
        armedUntil = now + 2000
        return 'OK ARM'
      }
      const match = /^(FWD|REV|KEEP)(LEFT|CENTER|RIGHT)$/.exec(command)
      if (!match) throw new Error('ERR COMMAND')
      const [, drive, steer] = match
      if (drive === 'KEEP') {
        if (!active) throw new Error('ERR NOT_ACTIVE')
      } else {
        if (active) throw new Error('ERR BUSY')
        if (now < coolUntil) throw new Error('ERR COOLDOWN')
        if (now >= armedUntil) throw new Error('ERR NOT_ARMED')
        active = true
        direction = drive === 'FWD' ? 1 : -1
        armedUntil = 0
        reason = 0
      }
      servo = STEERING[steer]
      leaseUntil = servoUntil = now + LEASE_MS
      return drive === 'KEEP' ? 'OK KEEP' : 'OK ACTION'
    },
    sample(now) {
      advance(now)
      return {
        source: 'car-simulation', ms: Math.round(now - origin),
        left_count: Math.trunc(count), right_count: Math.trunc(count),
        left_delta5: active ? direction * 3 : 0, right_delta5: active ? direction * 3 : 0,
        left_target5: active ? direction * 3 : 0, right_target5: active ? direction * 3 : 0,
        left_pwm8400: active ? direction * 1680 : 0, right_pwm8400: active ? direction * 1680 : 0,
        servo_compare: servo, active: Number(active), stop_reason: reason,
      }
    },
  }
}

const PHASES = [
  { duration: 2800, drive: 'FWD', steer: 'CENTER', label: '前进演示' },
  { duration: 700, label: '停止并回正' },
  { duration: 600, steer: 'LEFT', label: '前轮向左' },
  { duration: 2800, drive: 'FWD', steer: 'LEFT', label: '左转请求演示' },
  { duration: 700, label: '停止并回正' },
  { duration: 2400, drive: 'REV', steer: 'CENTER', label: '后退演示' },
  { duration: 700, label: '停止并回正' },
  { duration: 600, steer: 'RIGHT', label: '前轮向右' },
  { duration: 2800, drive: 'FWD', steer: 'RIGHT', label: '右转请求演示' },
  { duration: 800, label: '停止并回正' },
]

export function createDemoSequence(origin = 0) {
  let car = createCarSimulation(origin), running = false, index = 0
  let phaseAt = origin, lastTick = origin, renewedAt = origin, label = '待机 / 回正'
  function enter(now) {
    const phase = PHASES[index]
    label = phase.label
    if (phase.drive) {
      car.command('ARM', now)
      car.command(phase.drive + phase.steer, now)
    } else if (phase.steer) car.command(phase.steer, now)
    else car.command('STOP', now)
    renewedAt = now
  }
  const sequence = {
    get running() { return running },
    get label() { return label },
    start(now) {
      car = createCarSimulation(now)
      index = 0
      phaseAt = lastTick = now
      running = true
      enter(now)
    },
    stop(now, message = '待机 / 回正') {
      car.command('STOP', now)
      running = false
      label = message
    },
    tick(now) {
      if (!running) return
      if (now - lastTick > 200) {
        sequence.stop(now, '更新中断，已停止')
        return
      }
      lastTick = now
      if (now - phaseAt >= PHASES[index].duration) {
        index += 1
        if (index >= PHASES.length) {
          sequence.stop(now, '演示结束 / 回正')
          return
        }
        phaseAt = now
        enter(now)
      }
      const phase = PHASES[index]
      if (now - renewedAt >= 100) {
        if (phase.drive) car.command('KEEP' + phase.steer, now)
        else if (phase.steer) car.command(phase.steer, now)
        renewedAt = now
      }
    },
    sample: now => car.sample(now),
  }
  return sequence
}
