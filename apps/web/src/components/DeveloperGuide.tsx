import { useEffect, useMemo, useRef, useState, type MouseEvent, type KeyboardEvent, type ReactNode } from 'react'
import documentation from '../../../docs/index.html?raw'
import './DeveloperGuide.css'

type GuideSection = { id: string; title: string; html: string }
type WorkspaceTools = { keys: ReactNode; events: ReactNode; webhooks: ReactNode }

// The public manual remains the source of truth. Only its article content is used;
// its site navigation, styles and scripts never enter the dashboard.
function readGuide(): GuideSection[] {
  const doc = new DOMParser().parseFromString(documentation, 'text/html')
  const sections: HTMLElement[] = []
  let current: HTMLElement | undefined
  for (const child of Array.from(doc.querySelector('main')?.children || [])) {
    if (child.tagName === 'HEADER' || child.tagName === 'FOOTER') continue
    if (child.tagName === 'H2') {
      current = doc.createElement('section')
      current.id = `developer-${child.id}`
      child.removeAttribute('id')
      sections.push(current)
    }
    current?.appendChild(child)
  }

  return sections.map((section) => {
    section.querySelectorAll('script, style, iframe').forEach((node) => node.remove())
    section.querySelectorAll('*').forEach((node) => {
      for (const attribute of Array.from(node.attributes)) {
        if (attribute.name.startsWith('on')) node.removeAttribute(attribute.name)
      }
    })
    section.querySelectorAll<HTMLAnchorElement>('a[href]').forEach((link) => {
      const href = link.getAttribute('href') || ''
      if (href.startsWith('#')) link.href = `#developer-${href.slice(1)}`
      if (href.startsWith('/docs#')) link.href = `#developer-${href.split('#')[1]}`
      if (href === '/api-docs') link.href = '#developer-rest'
      if (href === '/docs/commerce-contract.json') link.download = 'sahay-commerce-contract.json'
    })

    section.querySelectorAll<HTMLElement>('.tabs[data-group]').forEach((tabs) => {
      const group = tabs.dataset.group
      const panels = Array.from(section.querySelectorAll<HTMLElement>('.tab')).filter((panel) => panel.dataset.group === group)
      const block = doc.createElement('div')
      block.className = 'developer-code-block'
      tabs.before(block)
      tabs.className = 'developer-code-tabs'
      tabs.setAttribute('role', 'tablist')
      tabs.setAttribute('aria-label', 'Code example language or platform')
      block.appendChild(tabs)
      tabs.querySelectorAll('button').forEach((button, index) => {
        const active = index === 0
        button.type = 'button'
        button.id = `developer-example-${group}-${index}-tab`
        button.setAttribute('role', 'tab')
        button.setAttribute('aria-selected', String(active))
        button.setAttribute('aria-controls', `developer-example-${group}-${index}`)
        button.tabIndex = active ? 0 : -1
        button.className = ''
        const panel = panels[index]
        if (panel) {
          panel.id = `developer-example-${group}-${index}`
          panel.className = 'developer-code-panel'
          panel.hidden = !active
          panel.setAttribute('role', 'tabpanel')
          panel.setAttribute('aria-labelledby', button.id)
          panel.tabIndex = 0
          block.appendChild(panel)
        }
      })
    })

    section.querySelectorAll('pre').forEach((pre) => {
      pre.tabIndex = 0
      if (pre.closest('.developer-code-block')) return
      const block = doc.createElement('div')
      block.className = 'developer-code-block'
      pre.before(block)
      block.appendChild(pre)
    })
    section.querySelectorAll('.developer-code-block').forEach((block) => {
      const button = doc.createElement('button')
      button.type = 'button'
      button.className = 'developer-copy-code'
      button.dataset.copyCode = ''
      button.textContent = 'Copy code'
      button.setAttribute('aria-label', 'Copy the displayed code example')
      block.appendChild(button)
    })
    section.querySelectorAll('table').forEach((table) => {
      const headers = Array.from(table.querySelectorAll('th')).map((cell) => cell.textContent || '')
      table.querySelectorAll('tr').forEach((row) => row.querySelectorAll('td').forEach((cell, index) => { cell.dataset.label = headers[index] || '' }))
    })
    return { id: section.id, title: section.querySelector('h2')?.textContent || '', html: section.innerHTML }
  })
}

function selectCodeTab(button: HTMLButtonElement) {
  const block = button.closest('.developer-code-block')
  if (!block) return
  block.querySelectorAll<HTMLButtonElement>('[role="tab"]').forEach((tab) => {
    const active = tab === button
    tab.setAttribute('aria-selected', String(active))
    tab.tabIndex = active ? 0 : -1
  })
  block.querySelectorAll<HTMLElement>('[role="tabpanel"]').forEach((panel) => { panel.hidden = panel.id !== button.getAttribute('aria-controls') })
}

export default function DeveloperGuide({ tools }: { tools: WorkspaceTools }) {
  const sections = useMemo(readGuide, [])
  const [copyStatus, setCopyStatus] = useState('')
  const timers = useRef<ReturnType<typeof setTimeout>[]>([])
  useEffect(() => () => timers.current.forEach(clearTimeout), [])

  const jump = (id: string) => {
    const section = document.getElementById(id)
    if (!section) return
    section.scrollIntoView({ block: 'start', behavior: matchMedia('(prefers-reduced-motion: reduce)').matches ? 'instant' : 'smooth' })
    section.focus({ preventScroll: true })
  }
  const handleClick = async (event: MouseEvent<HTMLDivElement>) => {
    if (!(event.target instanceof Element)) return
    const button = event.target.closest<HTMLButtonElement>('button')
    if (button?.getAttribute('role') === 'tab') selectCodeTab(button)
    if (button?.hasAttribute('data-copy-code')) {
      const block = button.closest('.developer-code-block')
      const code = block?.querySelector<HTMLElement>('.developer-code-panel:not([hidden]) pre') || block?.querySelector<HTMLElement>('pre')
      try {
        await navigator.clipboard.writeText(code?.textContent || '')
        button.textContent = 'Copied'
        setCopyStatus('Code copied to clipboard.')
        timers.current.push(setTimeout(() => { if (button.isConnected) button.textContent = 'Copy code' }, 1800))
      } catch { setCopyStatus('Copy was unavailable. Select the code to copy it manually.') }
    }
    const link = event.target.closest<HTMLAnchorElement>('a[href^="#developer-"]')
    if (link && !event.metaKey && !event.ctrlKey && !event.shiftKey && !event.altKey) {
      event.preventDefault()
      jump(link.hash.slice(1))
    }
  }
  const handleKeyDown = (event: KeyboardEvent<HTMLDivElement>) => {
    if (!(event.target instanceof HTMLButtonElement) || event.target.getAttribute('role') !== 'tab') return
    const tabs = Array.from(event.target.parentElement?.querySelectorAll<HTMLButtonElement>('[role="tab"]') || [])
    const current = tabs.indexOf(event.target)
    const index = event.key === 'ArrowRight' ? (current + 1) % tabs.length : event.key === 'ArrowLeft' ? (current + tabs.length - 1) % tabs.length : event.key === 'Home' ? 0 : event.key === 'End' ? tabs.length - 1 : -1
    if (index < 0) return
    event.preventDefault()
    selectCodeTab(tabs[index])
    tabs[index].focus()
  }
  const toolTitles = { keys: 'Keys for your developer', events: 'View received events', webhooks: 'Manage webhook endpoints' }

  return (
    <div className="developer-guide" onClick={handleClick} onKeyDown={handleKeyDown}>
      <nav className="developer-guide-navigation" aria-label="Documentation sections">
        <label><span>Jump to</span><select defaultValue="" onChange={(event) => { jump(event.target.value); event.target.value = '' }}>
          <option value="" disabled>Choose a section</option>
          {sections.map((section) => <option key={section.id} value={section.id}>{section.title}</option>)}
        </select></label>
        <div><a href="#developer-widget">Install chat</a><a href="#developer-keys">Your keys</a><a href="#developer-rest">API reference</a></div>
      </nav>
      <p role="status" aria-live="polite" className="sr-only">{copyStatus}</p>
      <div className="developer-guide-content">
        {sections.map((section) => {
          const tool = section.id.replace('developer-', '') as keyof WorkspaceTools
          return <section key={section.id} id={section.id} className="developer-guide-section" tabIndex={-1} aria-labelledby={`${section.id}-title`}>
            <div dangerouslySetInnerHTML={{ __html: section.html.replace('<h2>', `<h2 id="${section.id}-title">`) }} />
            {tools[tool] && <details className="developer-workspace-tool"><summary>{toolTitles[tool]}</summary>{tools[tool]}</details>}
          </section>
        })}
      </div>
    </div>
  )
}
