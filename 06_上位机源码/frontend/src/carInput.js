const actions = new Set(['forward','reverse','left','right','route'])
export function keyAction(code) {
  return {KeyW:'forward',ArrowUp:'forward',KeyS:'reverse',ArrowDown:'reverse',
    KeyA:'left',ArrowLeft:'left',KeyD:'right',ArrowRight:'right',F8:'route'}[code] ?? null
}
export function createCarInput() {
  const held = new Map(), suppressed = new Set()
  let routeBlocked = false, route = 1
  return {
    press(token, action) {
      if (!actions.has(action) || suppressed.has(token) || held.has(token) || (action === 'route' && routeBlocked)) return false
      held.set(token, action); return true
    },
    release(token) {
      const action = held.get(token); held.delete(token); suppressed.delete(token)
      if (![...held.values()].includes('route')) routeBlocked = false
      return action
    },
    cancel() { for (const token of held.keys()) suppressed.add(token); held.clear(); routeBlocked = false },
    completeRoute() { routeBlocked = true },
    setRoute(value) { route = Number(value) },
    held() { return [...held.values()] },
    intent() {
      const values = new Set(held.values())
      if (values.has('route')) return {direction:null,steering:'CENTER',route:routeBlocked ? 0 : route}
      return {
        direction:values.has('forward') === values.has('reverse') ? null : values.has('forward') ? 'FWD' : 'REV',
        steering:values.has('left') === values.has('right') ? 'CENTER' : values.has('left') ? 'LEFT' : 'RIGHT',
      }
    },
  }
}
