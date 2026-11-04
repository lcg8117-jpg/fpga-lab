const WINDOW_MS = 10000
const PLOT_LEFT = 54
const PLOT_RIGHT = 18
const NUMBER_FONT = 'Consolas, "Courier New", monospace'
const CHANNELS = [
  { field: 'angle_deg', name: '摆杆偏角', unit: '°', color: '#DBAA64', minimum: 2, digits: 3 },
  { field: 'angle_velocity_deg_s', name: '摆杆角速度', unit: '°/s', color: '#C7CBBE', minimum: 5, digits: 1 },
  { field: 'duty_actual_percent', name: 'PWM', unit: '%', color: '#97A583', minimum: 65, digits: 1 },
]

function localTime(timestamp) {
  const date = new Date(timestamp)
  const pad = (value, length = 2) => String(value).padStart(length, '0')
  return `${pad(date.getHours())}:${pad(date.getMinutes())}:${pad(date.getSeconds())}.${pad(date.getMilliseconds(), 3)}`
}

function escapeHtml(value) {
  return String(value).replace(/[&<>"']/g, character => ({
    '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;',
  })[character])
}

function yExtent(data, channel) {
  if (channel.field === 'duty_actual_percent') return channel.minimum
  const magnitude = data.reduce((maximum, point) => Math.max(maximum, Math.abs(point[1] ?? 0)), 0)
  return Math.max(channel.minimum, Math.ceil(magnitude * 1.12 * 10) / 10)
}

/** Keep tooltip/cursor models alive; graphic components are snapshots so expired overlays disappear. */
export function updateWaveformChart(chart, option) {
  chart.setOption(option, { replaceMerge: ['graphic'], silent: true })
}

/** Pure rendering option: telemetry received_at is seconds; now and event.at are epoch milliseconds. */
export function createWaveformOption(samples, now, height = 500, events = [], width = 600, windowMs = WINDOW_MS, layout = 'stacked') {
  if (!Number.isFinite(now)) throw new TypeError('now must be a finite epoch millisecond timestamp')
  if (![10000, 30000].includes(windowMs)) throw new RangeError('waveform window must be 10000 or 30000 ms')
  if (!['stacked', 'horizontal'].includes(layout)) throw new RangeError('Unknown waveform layout')
  const horizontal = layout === 'horizontal'
  const chartHeight = Number.isFinite(height) && height > 0 ? height : 500
  const chartWidth = Number.isFinite(width) && width > 0 ? width : 600
  const start = now - windowMs
  const tickInterval = windowMs === 30000 ? 5000 : 2000
  const tickCount = windowMs / tickInterval
  const columnWidth = (chartWidth - 48) / 3
  const plotWidth = Math.max(1, (horizontal ? columnWidth : chartWidth) - PLOT_LEFT - PLOT_RIGHT)
  const gridHeight = Math.max(1, horizontal ? chartHeight - 56 : (chartHeight - 28 - 28 - 2 * 38) / 3)
  const grid = CHANNELS.map((_, index) => ({
    left: PLOT_LEFT + (horizontal ? index * (columnWidth + 24) : 0),
    ...(horizontal ? { width:plotWidth } : { right:PLOT_RIGHT }),
    top: horizontal ? 28 : 28 + index * (gridHeight + 38), height: gridHeight,
  }))
  const inWindow = (Array.isArray(samples) ? samples : [])
    .filter(sample => sample && Number.isFinite(sample.received_at))
    .map(sample => ({ sample, at: sample.received_at * 1000 }))
    .filter(({ at }) => Number.isFinite(at) && at >= start && at <= now)
    .sort((a, b) => a.at - b.at)
  const series = CHANNELS.map((channel, index) => ({
    id: channel.field,
    name: channel.name,
    type: 'line', xAxisIndex: index, yAxisIndex: index,
    data: inWindow.filter(({ sample }) => !Array.isArray(sample.channels) || sample.channels.includes(channel.field))
      .map(({ sample, at }) => [at, Number.isFinite(sample[channel.field]) ? sample[channel.field] : null]),
    showSymbol: false, smooth: false, connectNulls: false, animation: false,
    lineStyle: { color: channel.color, width: 1.5 },
    itemStyle: { color: channel.color },
    emphasis: { disabled: true },
  }))
  const visibleEvents = (Array.isArray(events) ? events : [])
    .filter(event => event && Number.isFinite(event.at) && event.at >= start && event.at <= now)
  const eventChildren = visibleEvents.flatMap((event, index) => {
    const x = PLOT_LEFT + ((event.at - start) / windowMs) * plotWidth
    const label = String(event.label ?? '')
    const labelWidth = Math.min(plotWidth, Math.max(32, Array.from(label).length * 11 + 12))
    const lines = (horizontal ? grid : [grid[0]]).map((plot, plotIndex) => ({
      id: `event-line-${index}-${plotIndex}`, type: 'line', silent: true, z: 8,
      shape: { x1:plot.left + x - PLOT_LEFT, y1:plot.top, x2:plot.left + x - PLOT_LEFT, y2:horizontal ? plot.top + gridHeight : grid[2].top + gridHeight },
      style: { stroke:'#DBAA64', lineWidth:1, opacity:.7, lineDash:[4,4] },
    }))
    return [
      ...lines,
      {
        id: `event-label-${index}`, type: 'text', silent: true, z: 9,
        x: Math.max(PLOT_LEFT, Math.min(x + 4, PLOT_LEFT + plotWidth - labelWidth)),
        y: grid[0].top + 5,
        style: {
          text: label, fill: '#DBAA64', font: '11px sans-serif',
          width: labelWidth, overflow: 'truncate', padding: [2, 4], backgroundColor: '#30332D',
        },
      },
    ]
  })

  return {
    animation: false,
    backgroundColor: 'transparent',
    grid,
    title: CHANNELS.map((channel, index) => ({
      text: `${channel.name} · ${channel.unit}`, left: grid[index].left, top: grid[index].top - 24,
      padding: 0, textStyle: { color: channel.color, fontSize: horizontal ? 14 : 12, fontWeight: 500 },
    })),
    axisPointer: { link: [{ xAxisIndex: 'all' }], lineStyle: { color: '#B0B5A6', width: 1 } },
    tooltip: {
      trigger: 'axis', confine: true, transitionDuration: 0,
      backgroundColor: '#30332D', borderColor: '#656B5A', textStyle: { color: '#F1F2E9', fontSize: 12, fontFamily: NUMBER_FONT },
      extraCssText: 'box-shadow:0 3px 12px rgba(0,0,0,.2);border-radius:6px;',
      axisPointer: { type: 'line' },
      formatter(params) {
        const points = (Array.isArray(params) ? params : [params]).filter(point => Array.isArray(point?.data))
        if (!points.length) return ''
        return [localTime(points[0].data[0]), ...points.map(point => {
          const channel = CHANNELS.find(item => item.name === point.seriesName)
          const value = Number.isFinite(point.data[1]) ? point.data[1].toFixed(channel?.digits ?? 3) : '—'
          return `${escapeHtml(point.seriesName ?? '')}：${value}${escapeHtml(channel?.unit ?? '')}`
        })].join('<br/>')
      },
    },
    xAxis: CHANNELS.map((_, index) => ({
      type: 'value', gridIndex: index, min: start, max: now, interval: tickInterval,
      axisLabel: { show: false }, axisTick: { show: false },
      axisLine: { show: horizontal || index === 2, lineStyle: { color: '#525849' } },
      splitLine: { show: true, lineStyle: { color: 'rgba(199, 203, 190, 0.09)' } },
      axisPointer: { label: { show: horizontal || index === 2, color:'#F1F2E9', backgroundColor:'#656B5A', fontFamily: NUMBER_FONT, formatter: params => localTime(params.value) } },
    })),
    yAxis: CHANNELS.map((channel, index) => {
      const extent = yExtent(series[index].data, channel)
      return {
        type: 'value', gridIndex: index, min: -extent, max: extent, splitNumber: 2,
        ...(channel.field === 'duty_actual_percent' ? { interval: 65 } : {}),
        axisLabel: { color: '#A6AA9B', fontFamily: NUMBER_FONT, fontSize: 11, margin: 8, hideOverlap: true },
        axisTick: { show: false }, axisLine: { show: false },
        splitLine: { show: true, lineStyle: { color: 'rgba(199, 203, 190, 0.11)' } },
        axisPointer: { show: false },
      }
    }),
    series,
    graphic: [
      { id: 'waveform-events', type: 'group', silent: true, children: eventChildren },
      {
        id: 'waveform-time-labels', type: 'group', silent: true,
        children: (horizontal ? grid : [grid[2]]).flatMap(plot => Array.from({ length: tickCount + 1 }, (_, index) => ({
          type: 'text', x: plot.left + index / tickCount * plotWidth, y: plot.top + gridHeight + 10,
          style: {
            text: index === tickCount ? '0 s' : `−${(windowMs - index * tickInterval) / 1000} s`,
            fill: '#A6AA9B', font: `11px ${NUMBER_FONT}`,
            align: index === 0 ? 'left' : index === tickCount ? 'right' : 'center',
          },
        }))),
      },
    ],
  }
}
