// On phones, wide tables turn into stacked cards (the CSS lives in mobile.css). CSS can't read <th> text, so every
// body cell is labelled with its column header here. Only attributes are touched (never the React-owned markup),
// so React can keep re-rendering the rows freely; the observer re-labels them after each change.
function annotate(table: HTMLTableElement) {
  const heads = Array.from(table.tHead?.rows[0]?.cells ?? []).map((c) => (c.textContent || '').trim())
  if (heads.length < 3) return // small tables fit on a phone as they are
  if (!table.hasAttribute('data-stack')) table.setAttribute('data-stack', '')
  for (const body of Array.from(table.tBodies)) {
    for (const row of Array.from(body.rows)) {
      let col = 0
      for (const cell of Array.from(row.cells)) {
        const label = cell.colSpan > 1 ? '' : heads[col] ?? ''
        if (cell.getAttribute('data-label') !== label) cell.setAttribute('data-label', label)
        // a cell with nothing to show (no phone, no email...) would otherwise leave a lonely label in the card
        const empty = !(cell.textContent || '').trim() && !cell.querySelector('img, svg, input, select, textarea, button, a[href]')
        if (cell.hasAttribute('data-empty') !== empty) cell.toggleAttribute('data-empty', empty)
        // long text (an email, an address) needs the whole card width, short values sit two to a row
        const wide = (cell.textContent || '').trim().length > 20
        if (cell.hasAttribute('data-wide') !== wide) cell.toggleAttribute('data-wide', wide)
        col += cell.colSpan || 1
      }
    }
  }
}

export function watchTables() {
  let queued = false
  const run = () => {
    queued = false
    document.querySelectorAll<HTMLTableElement>('table').forEach(annotate)
  }
  new MutationObserver(() => {
    if (!queued) { queued = true; setTimeout(run, 16) } // a timer, not requestAnimationFrame: that pauses in background tabs
  }).observe(document.body, { childList: true, subtree: true })
  run()
}
