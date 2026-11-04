// 只处理 3D 显示，不修改遥测、波形、硬件零点或控制参数。
const wrap360 = degrees => ((degrees % 360) + 360) % 360
const finite = (value, fallback) => Number.isFinite(value) ? value : fallback
const fd2Profiles = /^(?:V[1-5]|ADB1|ADB1_FASTP|ADB1_07|V4_R28|V4_USB_BLE|G3_C1|DS13)$/

export function calibrationStorageKey(firmware) {
  // FD2 now uses unclipped Q4 pose angles; retain old profiles without applying their old input domain.
  const version = fd2Profiles.test(firmware || '') ? 'v3' : 'v2'
  return `j280:twin3d:calibration:${version}:${fd2Profiles.test(firmware || '') || ['SG21','SA21','SP24'].includes(firmware) ? firmware : 'generic'}`
}

export function calibrationDefaults(firmware) {
  // 用户 2026-10-15 确认 SG21 的 -182.138° 是实物自然下垂。
  // 仅建立该机台的线性显示偏移，不把旧固件 -169.937° 的两点拉伸套进新协议。
  const down = firmware === 'SG21' ? -182.138 : -180
  return {thetaDirection:1,thetaOffsetDeg:0,alphaDirection:-1,alphaOffsetDeg:180+down,
    alphaTwoPointEnabled:false,alphaUprightDeg:0,alphaDownDeg:down,smoothing:.02}
}

export function normalizeCalibration(value, firmware) {
  const defaults = calibrationDefaults(firmware)
  const source = value && typeof value === 'object' ? value : {}
  return {
    thetaDirection:[1,-1].includes(source.thetaDirection) ? source.thetaDirection : defaults.thetaDirection,
    thetaOffsetDeg:finite(source.thetaOffsetDeg,defaults.thetaOffsetDeg),
    alphaDirection:[1,-1].includes(source.alphaDirection) ? source.alphaDirection : defaults.alphaDirection,
    alphaOffsetDeg:finite(source.alphaOffsetDeg,defaults.alphaOffsetDeg),
    alphaTwoPointEnabled:typeof source.alphaTwoPointEnabled === 'boolean' ? source.alphaTwoPointEnabled : defaults.alphaTwoPointEnabled,
    alphaUprightDeg:finite(source.alphaUprightDeg,defaults.alphaUprightDeg),
    alphaDownDeg:finite(source.alphaDownDeg,defaults.alphaDownDeg),
    smoothing:Math.min(.05,Math.max(0,finite(source.smoothing,defaults.smoothing))),
  }
}

export function alphaReferencesValid(config) {
  if (!Number.isFinite(config.alphaUprightDeg) || !Number.isFinite(config.alphaDownDeg)) return false
  const span = wrap360(config.alphaDownDeg-config.alphaUprightDeg)
  return span >= 90 && span <= 270
}

export function displayAlphaDeg(raw, config) {
  let alpha = raw
  if (config.alphaTwoPointEnabled && alphaReferencesValid(config)) {
    const down = wrap360(config.alphaDownDeg-config.alphaUprightDeg)
    const phase = wrap360(raw-config.alphaUprightDeg)
    const corrected = phase <= down ? 180*phase/down : 180+180*(phase-down)/(360-down)
    alpha = corrected > 180 ? corrected-360 : corrected
  }
  return config.alphaDirection*alpha+config.alphaOffsetDeg
}

export function calibrateDownPose(config, raw) {
  if (!Number.isFinite(raw)) throw new Error('需要有效的当前摆角读数')
  return {...config,alphaTwoPointEnabled:false,alphaDownDeg:raw,alphaOffsetDeg:180-config.alphaDirection*raw}
}
