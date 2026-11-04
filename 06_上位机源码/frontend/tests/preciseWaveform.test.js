import test from 'node:test'
import assert from 'node:assert/strict'
import * as echarts from 'echarts'
import * as precise from '../src/preciseWaveform.js'
import { appendSamples } from '../src/hostPresentation.js'

const now = 1790604000456
const row = (offset, extra = {}) => ({
  sample_id: String(offset), received_at: now / 1000,
  plot_at: (now + offset) / 1000, measurement_at: (now + offset - 3) / 1000,
  sample_ms: 42, capture_ms: 45, sequence: 1, session_epoch: 0,
  angle_deg: .01, alpha_ref_deg: .02, arm_relative_deg: 1,
  target_relative_deg: 2, duty_actual_percent: 3, ...extra,
})
const optionFor = (rows, width = 600, layout = 'stacked') =>
  precise.createPreciseWaveformOption(rows, now, 500, [], width, 10000, layout)
const trace = (option, id) => option.series.find(series => series.id === id)
const values = series => series.data.filter(point => Number.isFinite(point[1]))

test('SP24 batches use board measurement time and capture time on three linked plots', () => {
  const option = optionFor([row(-30), row(-20), row(-10)])
  assert.equal(option.grid.length, 3)
  assert.equal(option.series.length, 5)
  for (const field of ['angle_deg', 'alpha_ref_deg', 'arm_relative_deg', 'target_relative_deg']) {
    assert.deepEqual(values(trace(option, field)).map(point => point[0]), [now - 33, now - 23, now - 13])
  }
  assert.deepEqual(values(trace(option, 'duty_actual_percent')).map(point => point[0]), [now - 30, now - 20, now - 10])
  assert.deepEqual(option.axisPointer.link, [{ xAxisIndex: 'all' }])
  assert.ok(option.xAxis.every(axis => axis.min === now - 10000 && axis.max === now))
  assert.match(option.title[1].text, /局部/)
})

test('tooltip preserves i96 strings and separates received, PID measurement and capture clocks', () => {
  const raw = { encoder_count: '-9223372036854775808', target_count_q32: '-39614081257132168796771975168', reference_rad_q20: '9007199254740993' }
  const sample = row(-10, { raw })
  const option = optionFor([sample])
  const data = trace(option, 'angle_deg').data[0]
  const html = option.tooltip.formatter([{ seriesName: '摆角', seriesId: 'angle_deg', data }])
  for (const value of Object.values(raw)) assert.ok(html.includes(value))
  assert.match(html, /sample_ms.*42/)
  assert.match(html, /capture_ms.*45/)
  assert.match(html, /received_at/)
  assert.match(html, /measurement_at/)
  assert.match(html, /plot_at/)
  const injected = row(-10, { raw: { unsafe: '<script>' } })
  const injectionOption = optionFor([injected])
  assert.doesNotMatch(injectionOption.tooltip.formatter([{ data: injectionOption.series[0].data[0] }]), /<script>/)
})

test('gap_before and epoch changes break every trace and mark capture gaps', () => {
  const option = optionFor([row(-30), row(-20, { gap_before: true }), row(-10, { session_epoch: 1 })])
  for (const series of option.series) {
    assert.equal(series.connectNulls, false)
    assert.deepEqual(series.data.map(point => point[1] === null), [false, true, false, true, false])
  }
  const gaps = option.graphic.find(group => group.id === 'waveform-precision-gaps')
  assert.equal(gaps.children.filter(item => item.type === 'line').length, 2)
})

test('duplicate measurement nulls stay gaps and never reuse capture or arrival timestamps', () => {
  const option = optionFor([row(-30), row(-20, { angle_deg: null, measurement_at: null }), row(-10)])
  const angle = trace(option, 'angle_deg')
  assert.equal(angle.data[1][1], null)
  assert.deepEqual(values(angle).map(point => point[0]), [now - 33, now - 13])
  assert.equal(values(trace(option, 'duty_actual_percent')).length, 3)
  assert.equal(values(trace(optionFor([row(-10, { measurement_at: null })]), 'angle_deg')).length, 0)
})

test('pixel reduction keeps first, min, max and last while preserving gaps and original cache', () => {
  const rows = Array.from({ length: 3000 }, (_, index) => row(-9990 + index * 3, {
    angle_deg: index === 1234 ? 11 : index === 1235 ? -9 : .01,
    gap_before: index === 1235,
  }))
  const before = structuredClone(rows)
  const option = optionFor(rows, 100)
  const angle = trace(option, 'angle_deg')
  const finite = values(angle)
  assert.equal(finite[0][0], rows[0].measurement_at * 1000)
  assert.equal(finite.at(-1)[0], rows.at(-1).measurement_at * 1000)
  assert.ok(finite.some(point => point[1] === 11))
  assert.ok(finite.some(point => point[1] === -9))
  const peak = angle.data.findIndex(point => point[1] === 11)
  const trough = angle.data.findIndex(point => point[1] === -9)
  assert.ok(angle.data.slice(peak + 1, trough).some(point => point[1] === null))
  assert.ok(angle.data.length <= 8 * option.grid[0].width)
  assert.deepEqual(rows, before)
})

test('small angle variation remains visible with shared reference extents and honest PWM limits', () => {
  const option = optionFor([row(-20), row(-10, { angle_deg: -.01, alpha_ref_deg: .03, duty_actual_percent: 80 })])
  assert.ok(option.yAxis[0].max < .1)
  assert.ok(option.yAxis[0].min < -.01)
  assert.ok(option.yAxis[0].max > .03)
  assert.ok(option.yAxis[2].max >= 80)
  assert.ok(optionFor([]).yAxis.every(axis => Number.isFinite(axis.min) && axis.max > axis.min))
})

test('small downward-rest variation gets a local range with quantization padding and distinct decimal ticks', () => {
  const option = optionFor([
    row(-30, { angle_deg: -180.03, alpha_ref_deg: null }),
    row(-20, { angle_deg: -180, alpha_ref_deg: null }),
    row(-10, { angle_deg: -179.97, alpha_ref_deg: null }),
  ])
  const axis = option.yAxis[0]
  assert.ok(axis.min < -180.03 && axis.max > -179.97)
  assert.ok(axis.max - axis.min < .1, 'small downward-rest signals must not force zero into the angular range')
  assert.equal(typeof axis.axisLabel.formatter, 'function')
  assert.notEqual(axis.axisLabel.formatter(-180.01), axis.axisLabel.formatter(-180.02))
  const flat = optionFor([row(-10, { angle_deg: -180, alpha_ref_deg: null })]).yAxis[0]
  const q20Quantum = 180 / Math.PI / 2 ** 20
  assert.ok(flat.max - flat.min >= q20Quantum * 4 - 1e-12)
  assert.ok(flat.max - flat.min < .01)
  const withReference = optionFor([row(-10, { angle_deg: -180.03, alpha_ref_deg: 0 })])
  assert.ok(withReference.yAxis[0].min < -180.03 && withReference.yAxis[0].max > 0)
  assert.ok(withReference.yAxis[2].min <= 0 && withReference.yAxis[2].max >= 0)
  const chart = echarts.init(null, null, { renderer: 'svg', ssr: true, width: 600, height: 500 })
  try {
    chart.setOption(option)
    const labels = chart.getModel().getComponent('yAxis', 0).axis.getViewLabels().map(label => label.formattedLabel)
    assert.equal(new Set(labels).size, labels.length)
    assert.ok(labels.length >= 2 && labels.every(label => /-\d+\.\d+/.test(label)))
    assert.match(chart.renderToSVGString(), /-180\.\d+/)
  } finally { chart.dispose() }
})

test('thirty seconds of 100Hz data remain in received-time cache independently of board-time drawing', () => {
  const rows = Array.from({ length: 3001 }, (_, index) => row(-30000 + index * 10, {
    sample_id: `epoch:0:${index}`, received_at: (now - 30000 + index * 10) / 1000,
  }))
  const cache = appendSamples([], rows, now)
  assert.equal(cache.length, 3001)
  assert.equal(appendSamples(cache, rows, now).length, 3001)
  optionFor(cache, 100)
  assert.equal(cache.length, 3001)
  assert.equal(appendSamples([], [row(-40000)], now).length, 1, 'arrival-time retention is independent of board plot time')
})

test('ECharts renders both layouts and replaces all precise points with empty data', () => {
  for (const layout of ['stacked', 'horizontal']) {
    const option = optionFor([row(-30), row(-20), row(-10)], 1000, layout)
    const chart = echarts.init(null, null, { renderer: 'svg', ssr: true, width: 1000, height: 500 })
    try {
      chart.setOption(option)
      assert.match(chart.renderToSVGString(), /局部/)
      chart.setOption(optionFor([], 1000, layout))
      for (let index = 0; index < 5; index++) assert.equal(chart.getModel().getSeriesByIndex(index).getData().count(), 0)
    } finally { chart.dispose() }
  }
})

test('actual ECharts grids keep long fine-scale labels aligned with capture gaps, events and common time labels', () => {
  const layouts = ['stacked', 'horizontal'].flatMap(layout =>
    [10000, 30000].map(windowMs => ({ layout, windowMs })))
  for (const { layout, windowMs } of layouts) {
    for (const angles of [[-180.03], [-180.03, -179.97]]) {
      const capture = now - 5000
      const rows = angles.map((angle, index) => row(-5000 + index * 10, {
        angle_deg: angle, alpha_ref_deg: null, arm_relative_deg: 0, target_relative_deg: 1, gap_before: index === 0,
      }))
      const option = precise.createPreciseWaveformOption(rows, now, 500, [{ at: capture, label: '缺帧检查' }], 1000, windowMs, layout)
      const chart = echarts.init(null, null, { renderer: 'svg', ssr: true, width: 1000, height: 500 })
      try {
        chart.setOption(option)
        const ticks = [now - windowMs, now - windowMs / 2, now]
        const offsets = option.grid.map((grid, index) => ticks.map(at => chart.convertToPixel({ xAxisIndex: index }, at) - grid.left))
        for (const actual of offsets) {
          for (let index = 0; index < ticks.length; index++) assert.ok(Math.abs(actual[index] - offsets[0][index]) < 1e-8, `${layout}: all board axes share the same pixel scale`)
        }
        for (let index = 0; index < 3; index++) {
          const rect = chart.getModel().getComponent('grid', index).coordinateSystem.getRect()
          assert.equal(rect.x, option.grid[index].left, `${layout}: labels must not independently shrink grid ${index}`)
          assert.equal(rect.width, option.grid[index].width)
          assert.ok(rect.x >= 100, 'reserve room for long precise y labels')
        }
        for (const id of ['waveform-precision-gaps', 'waveform-events']) {
          const lines = option.graphic.find(group => group.id === id).children.filter(item => item.type === 'line')
          lines.forEach((line, index) => {
            const axisIndex = layout === 'horizontal' ? index : 0
            assert.ok(Math.abs(line.shape.x1 - chart.convertToPixel({ xAxisIndex: axisIndex }, capture)) < 1e-8, `${layout}: ${id} tracks capture pixels`)
          })
        }
        const labels = option.graphic.find(group => group.id === 'waveform-time-labels').children
        const tickCount = windowMs === 30000 ? 7 : 6
        const interval = windowMs === 30000 ? 5000 : 2000
        labels.forEach((label, index) => {
          const axisIndex = layout === 'horizontal' ? Math.floor(index / tickCount) : 2
          const at = now - windowMs + index % tickCount * interval
          assert.ok(Math.abs(label.x - chart.convertToPixel({ xAxisIndex: axisIndex }, at)) < 1e-8, `${layout}: shared time label tracks actual axis`)
        })
      } finally { chart.dispose() }
    }
  }
})
