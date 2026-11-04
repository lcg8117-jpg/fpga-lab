import { createWaveformOption } from './waveform.js'

const CHANNELS = [
  ['angle_deg', '摆角', 0, '#DBAA64'],
  ['alpha_ref_deg', '摆角参考', 0, '#C7CBBE'],
  ['arm_angle_deg', '累计旋臂', 1, '#DBAA64'],
  ['arm_reference_deg', '运动参考', 1, '#C7CBBE'],
  ['final_target_deg', '最终目标（独立诊断）', 1, '#97A583'],
  ['duty_actual_percent', '实际 PWM', 2, '#97A583'],
]

/** Preserve chronological first/min/max/last per pixel, including hidden breaks. */
export function reduceFd2Pixels(points, start, windowMs, width) {
  const buckets = new Map()
  const pixels = Math.max(1, Math.floor(width))
  for (const point of points) {
    const pixel = Math.min(pixels - 1, Math.max(0, Math.floor((point[0] - start) / windowMs * pixels)))
    if (!buckets.has(pixel)) buckets.set(pixel, [])
    buckets.get(pixel).push(point)
  }
  return [...buckets.values()].flatMap(bucket => {
    if (bucket.length <= 4) return bucket
    let min = -1, max = -1
    bucket.forEach((point, index) => {
      if (!Number.isFinite(point[1])) return
      if (min < 0 || point[1] < bucket[min][1]) min = index
      if (max < 0 || point[1] > bucket[max][1]) max = index
    })
    const indices = [...new Set([0, min, max, bucket.length - 1])].filter(i => i >= 0).sort((a,b) => a-b)
    const retained = []
    let previous = -1
    for (const index of indices) {
      if (previous >= 0 && bucket[previous][1] !== null && bucket[index][1] !== null) {
        const gap = bucket.slice(previous + 1, index).find(point => point[1] === null)
        if (gap) retained.push(gap)
      }
      retained.push(bucket[index])
      previous = index
    }
    return retained
  })
}

function niceStep(value) {
  const power = 10 ** Math.floor(Math.log10(value))
  return [1, 2, 5, 10].find(step => step * power >= value) * power
}

/** Only widen a retained axis. Each chart/window owns its previous axes. */
function displayAxis(series, group, previous, angleExtent) {
  if (group === 2) return { min:-100, max:100, interval:100 }
  const values = series.filter(line => line.yAxisIndex === group)
    .flatMap(line => line.data.map(point => point[1])).filter(Number.isFinite)
  const low = values.length ? Math.min(...values) : 0
  const high = values.length ? Math.max(...values) : 0
  if (group === 0) {
    const magnitude = Math.max(Math.abs(low), Math.abs(high))
    const extent = Math.max(angleExtent, previous?.max ?? 0, magnitude > angleExtent ? magnitude * 1.08 : 0)
    const interval = niceStep(extent / 3)
    const max = Math.ceil(extent / interval) * interval
    return { min:-max, max, interval }
  }
  if (previous && low >= previous.min && high <= previous.max) return previous
  const padding = Math.max(20, (high - low) * .08)
  const min = Math.min(low - padding, previous?.min ?? Infinity)
  const max = Math.max(high + padding, previous?.max ?? -Infinity)
  const interval = 30 * 2 ** Math.max(0, Math.ceil(Math.log2((max - min) / 120)))
  const center = Math.round((min + max) / 2 / interval) * interval
  return {
    min:Math.min(center - 2 * interval, Math.floor(min / interval) * interval),
    max:Math.max(center + 2 * interval, Math.ceil(max / interval) * interval), interval,
  }
}

export function createFd2WaveformOption(samples, now, height = 500, events = [], width = 600, windowMs = 10000, layout = 'stacked', {previousAxes = [], angleExtent = 60} = {}) {
  const option = createWaveformOption([], now, height, [], width, 10000, layout)
  const start = now - windowMs
  option.xAxis.forEach(axis => { axis.min = start; axis.max = now; axis.interval = windowMs / 5 })
  option.graphic = option.graphic.filter(group => group.id === 'waveform-time-labels')
  option.graphic.forEach(group => group.children.forEach((child, i) => {
    const index = i % 6
    child.style.text = index === 5 ? '0 s' : `−${(windowMs - index * windowMs / 5) / 1000} s`
  }))
  const plotWidth = option.grid[0].width ?? width - 72
  option.series = CHANNELS.map(([field, name, group, color]) => {
    let epoch, last
    const points = []
    for (const row of samples) {
      if (Array.isArray(row.channels) && !row.channels.includes(field)) continue
      const time = (field === 'final_target_deg' ? row.plot_at : row.measurement_at ?? row.plot_at) * 1000
      if (!Number.isFinite(time) || time < start || time > now) continue
      if (row.gap_before || (epoch !== undefined && epoch !== row.session_epoch) ||
          (last !== undefined && time - last > (field === 'final_target_deg' ? 1500 : 100))) {
        points.push([time, null])
      }
      points.push([time, Number.isFinite(row[field]) ? row[field] : null])
      epoch = row.session_epoch
      last = time
    }
    return {
      id: field, name, type: 'line', xAxisIndex: group, yAxisIndex: group,
      data: reduceFd2Pixels(points, start, windowMs, plotWidth),
      showSymbol: false, connectNulls: false, animation: false,
      lineStyle: { color, width: 1.5, type: field.includes('ref') || field === 'final_target_deg' ? 'dashed' : 'solid' },
    }
  })
  const titles = ['摆角 / 参考 · °', '累计旋臂 / 参考 / 最终目标 · °', '实际 PWM · %']
  option.title.forEach((title, i) => { title.text = titles[i] })
  option.yAxis.forEach((axis, i) => {
    Object.assign(axis, displayAxis(option.series, i, previousAxes[i], angleExtent === 60 ? 60 : 30))
    axis.scale = true
  })
  // Series contain only numeric pairs. Original raw strings remain in the independent cache/export.
  option.tooltip.formatter = items => {
    const time = items[0]?.data?.[0]
    const clock = Number.isFinite(time) ? `板时时轴：距窗口末端 ${((time - now) / 1000).toFixed(3)} s<br/>` : ''
    return clock + items.map(point =>
      `${point.seriesName}：${Number.isFinite(point.data[1]) ? point.data[1].toFixed(3) : '—'}`
    ).join('<br/>')
  }
  return option
}
