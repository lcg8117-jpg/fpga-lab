export function appendMappedPage(previous, page) {
  if (!page || typeof page.stream !== 'string' || !page.stream || !Number.isSafeInteger(page.cursor) ||
      page.cursor < 0 || !Array.isArray(page.samples)) throw Error('映射数据格式无效')
  const reset = !previous || previous.stream !== page.stream || page.overrun === true
  let points = reset ? [] : previous.points.slice()
  let cursor = reset ? 0 : previous.cursor
  for (const row of page.samples) {
    const p = row?.point
    if (!Number.isSafeInteger(row?.cursor) || row.cursor <= cursor || !p ||
        !Number.isFinite(p.x_display) || !Number.isFinite(p.y_display) ||
        typeof p.break_before !== 'boolean') throw Error('映射点或游标无效')
    if (points.length && Number.isInteger(p.source_index) && Number.isInteger(points.at(-1).source_index) &&
        p.source_index <= points.at(-1).source_index) points = []
    points.push({...p,break_before: points.length === 0 || p.break_before})
    cursor = row.cursor
  }
  if (page.cursor < cursor) throw Error('映射游标倒退')
  return {stream:page.stream,cursor:page.cursor,points:points.slice(-12000)}
}

export function mappedSegments(points) {
  const result=[]
  for (const point of points || []) {
    if (!result.length || point.break_before) result.push([])
    result.at(-1).push(point)
  }
  return result
}
