import { createWaveformOption } from './waveform.js'

const CHANNELS = [
  { field: 'angle_deg', name: '摆角', group: 0, color: '#DBAA64', unit: '°' },
  { field: 'alpha_ref_deg', name: '角度参考', group: 0, color: '#C7CBBE', unit: '°', reference: true },
  { field: 'arm_relative_deg', name: '旋臂局部角', group: 1, color: '#DBAA64', unit: '°' },
  { field: 'target_relative_deg', name: '局部目标', group: 1, color: '#C7CBBE', unit: '°', reference: true },
  { field: 'duty_actual_percent', name: '实际 PWM', group: 2, color: '#97A583', unit: '%' },
]
const TITLES = ['摆角 / 角度参考 · °', '旋臂局部角 / 目标 · °', '实际 PWM · %']

function fixSharedPlotGeometry(option, width, windowMs, layout) {
  const chartWidth = Number.isFinite(width) && width > 0 ? width : 600
  const horizontal = layout === 'horizontal'
  const columnWidth = horizontal ? (chartWidth - 48) / 3 : chartWidth
  // Fine-scale values near -180° need more room than the legacy integer ticks.
  // All plots use the same reserve; ECharts must not independently shrink them.
  const labelSpace = Math.min(128, Math.max(0, columnWidth - 19))
  const plotWidth = Math.max(1, columnWidth - labelSpace - 18)
  const previous = option.grid.map(grid => ({ ...grid }))
  const previousWidth = previous[0].width ?? chartWidth - 54 - 18
  option.grid.forEach((grid, index) => {
    grid.left = labelSpace + (horizontal ? index * (columnWidth + 24) : 0)
    grid.width = plotWidth
    grid.right = null
    grid.containLabel = false
    grid.outerBoundsMode = 'none'
    option.title[index].left = grid.left
  })
  const project = (x, index) => option.grid[index].left + (x - previous[index].left) / previousWidth * plotWidth
  for (const group of option.graphic) {
    if (group.id === 'waveform-events') {
      for (const child of group.children) {
        if (child.type === 'line') {
          const index = Number(child.id.split('-').at(-1))
          child.shape.x1 = project(child.shape.x1, index)
          child.shape.x2 = child.shape.x1
        } else if (child.type === 'text') {
          child.style.width = Math.min(child.style.width, plotWidth)
          child.x = Math.max(option.grid[0].left, Math.min(project(child.x, 0), option.grid[0].left + plotWidth - child.style.width))
        }
      }
    } else if (group.id === 'waveform-time-labels') {
      const ticksPerPlot = windowMs === 30000 ? 7 : 6
      group.children.forEach((child, index) => {
        child.x = project(child.x, horizontal ? Math.floor(index / ticksPerPlot) : 2)
      })
    }
  }
  return plotWidth
}

function escapeHtml(value) {
  return String(value).replace(/[&<>"']/g, character => ({
    '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;',
  })[character])
}

/** Each pixel keeps chronological first/min/max/last and a break between retained segments. */
function reducePixels(points, start, windowMs, width) {
  const buckets = new Map()
  const pixels = Math.max(1, Math.floor(width))
  for (const point of points) {
    const column = Math.max(0, Math.min(pixels - 1, Math.floor((point[0] - start) / windowMs * pixels)))
    if (!buckets.has(column)) buckets.set(column, [])
    buckets.get(column).push(point)
  }
  return [...buckets.values()].flatMap(bucket => {
    if (bucket.length <= 4) return bucket
    let minimum = -1
    let maximum = -1
    for (let index = 0; index < bucket.length; index++) {
      if (!Number.isFinite(bucket[index][1])) continue
      if (minimum < 0 || bucket[index][1] < bucket[minimum][1]) minimum = index
      if (maximum < 0 || bucket[index][1] > bucket[maximum][1]) maximum = index
    }
    const indices = [...new Set([0, minimum, maximum, bucket.length - 1])].filter(index => index >= 0).sort((a, b) => a - b)
    const retained = []
    let previous = -1
    for (const index of indices) {
      // A hidden null may be a lost frame, invalid PID, or duplicate measurement.
      // Keep the break even if none of its neighbours is a pixel extremum.
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

function tooltip(params) {
  const points = (Array.isArray(params) ? params : [params]).filter(point => Array.isArray(point?.data))
  const lines = []
  const seen = new Set()
  for (const point of points) {
    const channel = CHANNELS.find(item => item.field === point.seriesId || item.name === point.seriesName)
    const value = Number.isFinite(point.data[1]) ? point.data[1].toFixed(4) : '—'
    lines.push(`${escapeHtml(point.seriesName ?? channel?.name ?? '')}：${value}${escapeHtml(channel?.unit ?? '')}`)
    const sample = point.data[2]
    if (!sample || seen.has(sample)) continue
    seen.add(sample)
    lines.push('旋臂局部原点：当前精确流首份编码器，非机械绝对零点')
    for (const field of ['sample_id', 'session_epoch', 'sequence', 'sample_ms', 'capture_ms', 'received_at', 'measurement_at', 'plot_at']) {
      if (sample[field] !== undefined) lines.push(`${field}：${escapeHtml(sample[field] ?? '—')}`)
    }
    if (sample.gap_before) lines.push('此前缺帧／断流，曲线已断开')
    for (const [field, raw] of Object.entries(sample.raw ?? {})) {
      // Never coerce integer strings to Number: i64/i96 must survive unchanged.
      lines.push(`${escapeHtml(field)}：${escapeHtml(raw ?? '—')}`)
    }
  }
  return lines.join('<br/>')
}

/** received_at retains samples; measurement_at plots PID channels; plot_at plots capture PWM. */
export function createPreciseWaveformOption(samples, now, height = 500, events = [], width = 600, windowMs = 10000, layout = 'stacked') {
  // Reuse layout, linked axes, events and clock labels without altering legacy firmware rendering.
  const option = createWaveformOption([], now, height, events, width, windowMs, layout)
  const start = now - windowMs
  const rows = (Array.isArray(samples) ? samples : [])
    .filter(sample => sample && Number.isFinite(sample.plot_at))
    .map(sample => ({ sample, at: sample.plot_at * 1000 }))
    .filter(row => Number.isFinite(row.at) && row.at <= now)
    .sort((a, b) => a.at - b.at)
  let previousEpoch
  for (const row of rows) {
    row.gap = Boolean(row.sample.gap_before || (previousEpoch !== undefined && row.sample.session_epoch !== previousEpoch))
    previousEpoch = row.sample.session_epoch
  }
  const plotWidth = fixSharedPlotGeometry(option, width, windowMs, layout)
  option.series = CHANNELS.map(channel => {
    const points = []
    for (const { sample, at, gap } of rows) {
      if (Array.isArray(sample.channels) && !sample.channels.includes(channel.field)) continue
      const measurement = channel.group !== 2
      const time = measurement && Number.isFinite(sample.measurement_at) ? sample.measurement_at * 1000 : at
      if (!Number.isFinite(time) || time < start || time > now) continue
      const valid = (!measurement || Number.isFinite(sample.measurement_at)) && Number.isFinite(sample[channel.field])
      if (gap && valid) points.push([time, null, sample])
      points.push([time, valid ? sample[channel.field] : null, sample])
    }
    return {
      id: channel.field, name: channel.name, type: 'line',
      xAxisIndex: channel.group, yAxisIndex: channel.group,
      dimensions: ['time', 'value', 'sample'], encode: { x: 0, y: 1 },
      data: reducePixels(points, start, windowMs, plotWidth),
      showSymbol: false, smooth: false, connectNulls: false, animation: false,
      lineStyle: { color: channel.color, width: 1.5, type: channel.reference ? 'dashed' : 'solid' },
      itemStyle: { color: channel.color }, emphasis: { disabled: true },
    }
  })
  option.title.forEach((title, index) => { title.text = TITLES[index] })
  option.yAxis.forEach((axis, index) => {
    const values = option.series.filter(series => series.yAxisIndex === index)
      .flatMap(series => series.data.map(point => point[1]).filter(Number.isFinite))
    if (index === 2) {
      const magnitude = values.reduce((maximum, value) => Math.max(maximum, Math.abs(value)), 0)
      const extent = Math.max(65, Math.ceil(magnitude * 1.08))
      axis.min = -extent
      axis.max = extent
      axis.interval = extent
      return
    }
    // Q20 radians resolve the pendulum; the SP24 encoder has three counts/degree.
    // Pad around the observed range so a -180° rest signal remains legible too.
    const quantum = index === 0 ? 180 / Math.PI / 2 ** 20 : 1 / 3
    const minimum = values.length ? Math.min(...values) : 0
    const maximum = values.length ? Math.max(...values) : 0
    const padding = Math.max(quantum * 2, (maximum - minimum) * .12)
    axis.min = minimum - padding
    axis.max = maximum + padding
    axis.scale = true
    const digits = Math.max(0, Math.min(8, 1 - Math.floor(Math.log10((axis.max - axis.min) / 2))))
    axis.axisLabel.formatter = value => Number(value).toFixed(digits)
  })
  option.tooltip.formatter = tooltip
  const gapLines = rows.filter(row => row.gap && row.at >= start)
  option.graphic.push({
    id: 'waveform-precision-gaps', type: 'group', silent: true,
    children: gapLines.flatMap((row, index) => {
      const plots = layout === 'horizontal' ? option.grid : [option.grid[0]]
      return plots.map((plot, plotIndex) => {
        const x = plot.left + (row.at - start) / windowMs * plotWidth
        return {
          id: `precision-gap-${index}-${plotIndex}`, type: 'line', silent: true, z: 7,
          shape: { x1: x, x2: x, y1: plot.top, y2: layout === 'horizontal' ? plot.top + plot.height : option.grid[2].top + option.grid[2].height },
          style: { stroke: '#D18B78', lineWidth: 1, opacity: .8, lineDash: [2, 3] },
        }
      })
    }),
  })
  return option
}
