import test from 'node:test'
import assert from 'node:assert/strict'
import * as echarts from 'echarts'
import * as waveform from '../src/waveform.js'
const { createWaveformOption } = waveform

const now = 1790604000456
const sample = (offset, fields = {}) => ({
  received_at: (now + offset) / 1000,
  angle_deg: 1,
  angle_velocity_deg_s: 2,
  duty_actual_percent: 3,
  ...fields,
})

test('all three channels share the same exact ten-second epoch window', () => {
  const option = createWaveformOption([sample(-10000), sample(0)], now, 500)
  assert.equal(option.grid.length, 3)
  assert.equal(option.series.length, 3)
  for (const axis of option.xAxis) {
    assert.equal(axis.type, 'value')
    assert.equal(axis.min, now - 10000)
    assert.equal(axis.max, now)
  }
  assert.deepEqual(option.axisPointer.link, [{ xAxisIndex: 'all' }])
  assert.deepEqual(option.series.map(series => series.lineStyle.color), ['#DBAA64', '#C7CBBE', '#97A583'])
})

test('seconds convert to millisecond coordinates and preserve irregular sample spacing', () => {
  const option = createWaveformOption([sample(-8400), sample(-9650), sample(-2200)], now, 500)
  const times = option.series[0].data.map(point => point[0])
  assert.deepEqual(times, [now - 9650, now - 8400, now - 2200])
  assert.equal(times[1] - times[0], 1250)
  assert.equal(times[2] - times[1], 6200)
})

test('out-of-window and invalid timestamps are dropped without synthesizing timestamps', () => {
  const option = createWaveformOption([
    sample(-10001), sample(-10000), sample(0), sample(1),
    { angle_deg: 4 }, sample(-10, { received_at: null }),
    sample(-10, { received_at: '1790604000' }),
    sample(-10, { received_at: Infinity }),
  ], now, 500)
  assert.equal(option.series.length, 3)
  for (const series of option.series) {
    assert.deepEqual(series.data.map(point => point[0]), [now - 10000, now])
    assert.equal(series.smooth, false)
    assert.equal(series.connectNulls, false)
    assert.equal(series.areaStyle, undefined)
  }
})

test('missing and nonfinite channel values remain gaps without erasing other channels', () => {
  const option = createWaveformOption([
    sample(-9000, { angle_deg: undefined, duty_actual_percent: NaN }),
    sample(-8000, { angle_deg: null, angle_velocity_deg_s: Infinity }),
    sample(-7000, { angle_deg: '2', duty_actual_percent: 0 }),
  ], now, 500)
  assert.deepEqual(option.series[0].data.map(point => point[1]), [null, null, null])
  assert.deepEqual(option.series[1].data.map(point => point[1]), [2, null, 2])
  assert.deepEqual(option.series[2].data.map(point => point[1]), [null, 3, 0])
})

test('empty data yields no trace and finite meaningful y limits', () => {
  const option = createWaveformOption([], now, 500)
  assert.ok(option.series.every(series => series.data.length === 0))
  assert.ok(option.yAxis.every(axis => Number.isFinite(axis.min) && Number.isFinite(axis.max)))
  assert.ok(option.yAxis[0].min <= -2 && option.yAxis[0].max >= 2)
  assert.ok(option.yAxis[1].min <= -5 && option.yAxis[1].max >= 5)
  assert.ok(option.yAxis[2].min <= -60 && option.yAxis[2].max >= 60)
  assert.equal(option.title[0].text, '摆杆偏角 · °')
  assert.equal(option.title[1].text, '摆杆角速度 · °/s')
  assert.equal(option.title[2].text, 'PWM · %')
  assert.equal(option.yAxis[2].interval, 65)
})

test('dynamic angle and speed limits retain measured extrema', () => {
  const option = createWaveformOption([sample(-1000, { angle_deg: -181, angle_velocity_deg_s: 320 })], now, 500)
  assert.ok(option.yAxis[0].min < -181)
  assert.ok(option.yAxis[1].max > 320)
})

test('tooltip displays local clock milliseconds across midnight and escapes event/sample content', () => {
  const midnight = new Date(2026, 8, 29, 0, 0, 0, 123).getTime()
  const option = createWaveformOption([], midnight, 500)
  const format = timestamp => option.tooltip.formatter([{ seriesName: '摆杆偏角', data: [timestamp, 1.25], color: '#38BDF8' }])
  assert.match(format(midnight), /00:00:00\.123/)
  assert.match(format(midnight - 500), /23:59:59\.623/)
  assert.match(format(midnight), /1\.250/)
  assert.doesNotMatch(option.tooltip.formatter([{ seriesName: '<script>', data: [midnight, null] }]), /<script>/)
})

test('each in-window event is a single continuous overlay line with a single label', () => {
  const option = createWaveformOption([], now, 500, [
    { id: 'start', at: now - 5000, label: '启动' },
    { id: 'old', at: now - 10001, label: '过期' },
    { id: 'future', at: now + 1, label: '未来' },
    { id: 'invalid', at: NaN, label: '无效' },
  ], 600)
  assert.ok(option.series.every(series => !series.markLine))
  const events = option.graphic.find(item => item.id === 'waveform-events')
  assert.equal(events.type, 'group')
  const lines = events.children.filter(item => item.type === 'line')
  const labels = events.children.filter(item => item.type === 'text')
  assert.equal(lines.length, 1)
  assert.equal(labels.length, 1)
  assert.equal(labels[0].style.text, '启动')
  assert.equal(lines[0].shape.x1, lines[0].shape.x2)
  assert.equal(lines[0].shape.x1, 54 + (600 - 54 - 18) / 2)
  assert.equal(lines[0].shape.y1, option.grid[0].top)
  assert.equal(lines[0].shape.y2, option.grid[2].top + option.grid[2].height)
})

test('relative labels appear once along the bottom and maintain a stable two-second spacing', () => {
  const option = createWaveformOption([], now, 500, [], 600)
  const labels = option.graphic.find(item => item.id === 'waveform-time-labels').children
  assert.deepEqual(labels.map(item => item.style.text), ['−10 s', '−8 s', '−6 s', '−4 s', '−2 s', '0 s'])
  assert.equal(labels.length, 6)
  assert.ok(option.xAxis.every(axis => axis.axisLabel.show === false))
  for (let index = 2; index < labels.length; index++) {
    assert.ok(Math.abs((labels[index].x - labels[index - 1].x) - (labels[1].x - labels[0].x)) < 1e-8)
  }
})

test('history view expands all channels to thirty seconds with matching events and ticks', () => {
  const option = createWaveformOption([
    sample(-30001), sample(-30000), sample(-20000), sample(-10000), sample(0),
  ], now, 500, [{ id:'older', at:now - 20000, label:'参数切换' }], 600, 30000)
  for (const axis of option.xAxis) {
    assert.equal(axis.min, now - 30000)
    assert.equal(axis.max, now)
    assert.equal(axis.interval, 5000)
  }
  for (const series of option.series) {
    assert.deepEqual(series.data.map(point => point[0]), [now - 30000, now - 20000, now - 10000, now])
  }
  const labels = option.graphic.find(item => item.id === 'waveform-time-labels').children
  assert.deepEqual(labels.map(label => label.style.text), ['−30 s', '−25 s', '−20 s', '−15 s', '−10 s', '−5 s', '0 s'])
  const eventLine = option.graphic.find(item => item.id === 'waveform-events').children.find(item => item.type === 'line')
  assert.equal(eventLine.shape.x1, 54 + (600 - 54 - 18) / 3)
  const chart = echarts.init(null, null, { renderer:'svg', ssr:true, width:600, height:500 })
  try {
    chart.setOption(option)
    for (let index = 0; index < labels.length; index++) {
      assert.ok(Math.abs(chart.convertToPixel({ xAxisIndex:0 }, now - 30000 + index * 5000) - labels[index].x) < 1e-8)
    }
  } finally { chart.dispose() }
})

test('relative grid lines align with labels at an arbitrary millisecond epoch origin', () => {
  const epochNow = 1790604000123
  const option = createWaveformOption([], epochNow, 500, [], 600)
  const labels = option.graphic.find(item => item.id === 'waveform-time-labels').children
  const chart = echarts.init(null, null, { renderer: 'svg', ssr: true, width: 600, height: 500 })
  try {
    chart.setOption(option)
    assert.deepEqual(chart.getModel().getComponent('yAxis', 2).axis.scale.getTicks().map(tick => tick.value), [-65, 0, 65])
    for (let gridIndex = 0; gridIndex < 3; gridIndex++) {
      const axis = chart.getModel().getComponent('xAxis', gridIndex).axis
      const ticks = axis.scale.getTicks()
      assert.deepEqual(ticks.map(tick => (tick.value - epochNow) / 1000), [-10, -8, -6, -4, -2, 0])
      for (let tickIndex = 0; tickIndex < 6; tickIndex++) {
        const pixel = chart.convertToPixel({ xAxisIndex: gridIndex }, ticks[tickIndex].value)
        assert.ok(Math.abs(pixel - labels[tickIndex].x) < 1e-8)
      }
    }
  } finally {
    chart.dispose()
  }
})

test('chart numbers use a monospaced font and PWM tick marks remain minus65 zero plus65', () => {
  const option = createWaveformOption([], now, 500)
  assert.match(option.tooltip.textStyle.fontFamily ?? '', /Consolas/)
  assert.ok(option.yAxis.every(axis => /Consolas/.test(axis.axisLabel.fontFamily)))
  const labels = option.graphic.find(item => item.id === 'waveform-time-labels').children
  assert.ok(labels.every(label => /Consolas/.test(label.style.font)))
  assert.deepEqual([option.yAxis[2].min, option.yAxis[2].interval, option.yAxis[2].max], [-65, 65, 65])
})

test('live updates preserve interaction models while removing expired events and emptied telemetry', () => {
  assert.equal(typeof waveform.updateWaveformChart, 'function')
  const chart = echarts.init(null, null, { renderer: 'svg', ssr: true, width: 600, height: 500 })
  try {
    waveform.updateWaveformChart(chart, createWaveformOption([sample(-1000)], now, 500, [{ id: 'start', at: now - 1000, label: '启动' }], 600))
    const tooltipModel = chart.getModel().getComponent('tooltip')
    const pointerModel = chart.getModel().getComponent('axisPointer')
    const seriesModel = chart.getModel().getSeriesByIndex(0)
    assert.match(chart.renderToSVGString(), /启动/)
    waveform.updateWaveformChart(chart, createWaveformOption([], now + 11000, 500, [{ id: 'start', at: now - 1000, label: '启动' }], 600))
    assert.equal(chart.getModel().getComponent('tooltip'), tooltipModel, 'stationary-hover tooltip model survives polling')
    assert.equal(chart.getModel().getComponent('axisPointer'), pointerModel, 'linked cursor model survives polling')
    assert.equal(chart.getModel().getSeriesByIndex(0), seriesModel)
    assert.doesNotMatch(chart.renderToSVGString(), /启动/, 'expired event must be removed')
    for (let index = 0; index < 3; index++) {
      assert.equal(chart.getModel().getSeriesByIndex(index).getData().count(), 0, 'empty telemetry replaces old points')
    }
  } finally {
    chart.dispose()
  }
})

test('showcase horizontal channels preserve one time domain in three separate nonoverlapping plots', () => {
  const option = createWaveformOption([sample(-5000), sample(0)], now, 210, [], 1500, 10000, 'horizontal')
  assert.ok(option.grid[0].left + option.grid[0].width < option.grid[1].left)
  assert.ok(option.grid[1].left + option.grid[1].width < option.grid[2].left)
  assert.ok(option.grid.every(grid => grid.top === option.grid[0].top && grid.height > 0))
  assert.ok(option.xAxis.every(axis => axis.min === now - 10000 && axis.max === now))
  const chart = echarts.init(null, null, { renderer:'svg', ssr:true, width:1500, height:210 })
  try {
    chart.setOption(option)
    const offsets = option.grid.map((grid, index) => chart.convertToPixel({ xAxisIndex:index }, now - 5000) - grid.left)
    assert.ok(offsets.every(offset => Math.abs(offset - offsets[0]) < 1e-8))
  } finally { chart.dispose() }
})

test('showcase event markers appear in every plot with a single event label', () => {
  const option = createWaveformOption([], now, 210, [{ id:'probe', at:now - 5000, label:'扰动' }], 1500, 10000, 'horizontal')
  const children = option.graphic.find(group => group.id === 'waveform-events').children
  assert.equal(children.filter(item => item.type === 'line').length, 3)
  assert.equal(children.filter(item => item.type === 'text').length, 1)
})
