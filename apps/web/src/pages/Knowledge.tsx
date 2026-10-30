import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { BookOpen, Globe, Lightbulb, Plus, Search, Trash2, Upload } from 'lucide-react'
import { useState } from 'react'
import ReactMarkdown from 'react-markdown'
import { Badge, Button, Card, Empty, Field, inputCls, Modal, PageHeader, Spinner } from '../components/ui'
import { api } from '../lib/api'
import { ago, humanize } from '../lib/format'

export default function Knowledge() {
  const qc = useQueryClient()
  const { data: docs, isLoading } = useQuery({ queryKey: ['kb-docs'], queryFn: () => api('/v1/kb/documents') })
  const { data: gaps } = useQuery({ queryKey: ['kb-gaps'], queryFn: () => api('/v1/kb/gaps') })
  const [adding, setAdding] = useState(false)
  const [viewing, setViewing] = useState<string | null>(null)
  const [url, setUrl] = useState('')
  const [q, setQ] = useState('')
  const refresh = () => { qc.invalidateQueries({ queryKey: ['kb-docs'] }); qc.invalidateQueries({ queryKey: ['kb-gaps'] }) }
  const crawl = useMutation({ mutationFn: () => api('/v1/kb/crawl', { body: { url } }), onSuccess: () => { setUrl(''); refresh() } })
  const upload = useMutation({ mutationFn: (f: File) => { const fd = new FormData(); fd.append('file', f); return api('/v1/kb/upload', { form: fd }) }, onSuccess: refresh })
  const del = useMutation({ mutationFn: (id: string) => api(`/v1/kb/documents/${id}`, { method: 'DELETE' }), onSuccess: refresh })
  const search = useMutation({ mutationFn: () => api('/v1/kb/search', { body: { query: q } }) })
  const build = useMutation({ mutationFn: () => api('/v1/kb/gaps/build', { method: 'POST' }), onSuccess: refresh })

  return (
    <>
      <PageHeader title="Knowledge" subtitle="The answers your agent can use."
        actions={<>
          <label className="inline-flex cursor-pointer items-center gap-1.5 rounded-lg bg-white px-3.5 py-2 text-sm font-medium text-slate-700 ring-1 ring-slate-300 hover:bg-slate-50">
            <Upload className="h-4 w-4" />{upload.isPending ? 'Uploading…' : 'Upload PDF / text'}
            <input type="file" hidden accept=".pdf,.txt,.md" onChange={(e) => e.target.files?.[0] && upload.mutate(e.target.files[0])} />
          </label>
          <Button onClick={() => setAdding(true)}><Plus className="h-4 w-4" />Write article</Button>
        </>} />
      <div className="grid gap-4 lg:grid-cols-[1fr_400px]">
        <div className="space-y-4">
          <Card title={<span className="flex items-center gap-1.5"><Globe className="h-4 w-4 text-slate-400" />Import from your website</span>}>
            <div className="flex gap-2">
              <input className={inputCls} placeholder="https://yourbrand.in" value={url} onChange={(e) => setUrl(e.target.value)} />
              <Button onClick={() => crawl.mutate()} loading={crawl.isPending} disabled={!url}>Crawl</Button>
            </div>
            {crawl.data && <div className="mt-2 text-xs text-emerald-700">Imported {crawl.data.documents} pages.</div>}
            {crawl.error && <div className="mt-2 text-xs text-red-600">{(crawl.error as Error).message}</div>}
          </Card>
          <Card title="Articles" pad={false}>
            {isLoading ? <Spinner /> : !docs?.length ? <Empty icon={<BookOpen className="h-10 w-10" />} title="No knowledge yet">Crawl your website or upload your policies to get started.</Empty> : (
              <div className="divide-y divide-slate-100">
                {docs.map((d: any) => (
                  <div key={d.id} className="flex items-center justify-between gap-3 px-4 py-2.5">
                    <button onClick={() => setViewing(d.id)} className="min-w-0 text-left">
                      <div className="truncate text-sm font-medium text-slate-800 hover:text-brand-700">{d.title}</div>
                      <div className="text-xs text-slate-400">{humanize(d.source)} · {d.chunks} chunks · {ago(d.created_at)}</div>
                    </button>
                    <div className="flex items-center gap-2">
                      {d.source === 'gap_approved' && <Badge tone="green">From gap detector</Badge>}
                      <button onClick={() => del.mutate(d.id)} className="text-slate-300 hover:text-red-600"><Trash2 className="h-4 w-4" /></button>
                    </div>
                  </div>
                ))}
              </div>
            )}
          </Card>
        </div>
        <div className="space-y-4">
          <Card title={<span className="flex items-center gap-1.5"><Search className="h-4 w-4 text-slate-400" />Test retrieval</span>}>
            <div className="flex gap-2"><input className={inputCls} placeholder="e.g. jar toot gaya replacement?" value={q} onChange={(e) => setQ(e.target.value)} onKeyDown={(e) => e.key === 'Enter' && search.mutate()} />
              <Button onClick={() => search.mutate()} loading={search.isPending}>Go</Button></div>
            <div className="mt-2 space-y-1.5">{search.data?.map((h: any) => (
              <div key={h.id} className="rounded-md bg-slate-50 p-2 text-xs"><div className="flex justify-between font-medium"><span>{h.ref} · {h.heading}</span><span className="text-slate-400">sim {h.similarity}</span></div>
                <div className="line-clamp-3 text-slate-500">{h.content}</div></div>))}
              {search.data && !search.data.length && <div className="text-xs text-slate-500">Nothing relevant — this would be logged as a knowledge gap.</div>}</div>
          </Card>
          <Card title={<span className="flex items-center gap-1.5"><Lightbulb className="h-4 w-4 text-amber-500" />Knowledge gaps</span>}
            action={<Button size="sm" variant="secondary" onClick={() => build.mutate()} loading={build.isPending}>Find gaps</Button>}>
            <p className="mb-2 text-xs text-slate-500">Questions customers asked that your knowledge couldn't answer. Sahay clusters them and drafts an article for you to approve.</p>
            {gaps?.suggestions?.map((s: any) => <GapSuggestion key={s.id} s={s} onDone={refresh} />)}
            {!!gaps?.open_questions?.length && <div className="mt-2 space-y-1">{gaps.open_questions.slice(0, 8).map((g: any) => <div key={g.id} className="rounded bg-amber-50 px-2 py-1 text-xs text-amber-900">“{g.question}”</div>)}</div>}
            {!gaps?.suggestions?.length && !gaps?.open_questions?.length && <div className="text-xs text-slate-400">No gaps yet.</div>}
          </Card>
        </div>
      </div>
      <AddDoc open={adding} onClose={() => setAdding(false)} onDone={refresh} />
      <ViewDoc id={viewing} onClose={() => setViewing(null)} />
    </>
  )
}

function GapSuggestion({ s, onDone }: { s: any; onDone: () => void }) {
  const [open, setOpen] = useState(false)
  const [title, setTitle] = useState(s.draft_title)
  const [md, setMd] = useState(s.draft_article_md)
  const decide = useMutation({ mutationFn: (decision: string) => api(`/v1/kb/gaps/${s.id}/decide`, { body: { decision, title, article_md: md } }), onSuccess: () => { setOpen(false); onDone() } })
  return (
    <div className="mb-2 rounded-lg border border-amber-200 bg-amber-50/50 p-2.5">
      <div className="text-sm font-medium text-slate-800">{s.label}</div>
      <div className="text-xs text-slate-500">{s.questions.length} customers asked, e.g. “{s.questions[0]}”</div>
      <div className="mt-2 flex gap-1.5"><Button size="sm" onClick={() => setOpen(true)}>Review article</Button><Button size="sm" variant="ghost" onClick={() => decide.mutate('reject')}>Dismiss</Button></div>
      <Modal open={open} onClose={() => setOpen(false)} title="Suggested FAQ article" wide>
        <div className="space-y-3">
          <div className="text-xs text-slate-500">Questions: {s.questions.map((q: string) => `“${q}”`).join(' · ')}</div>
          <Field label="Title"><input className={inputCls} value={title} onChange={(e) => setTitle(e.target.value)} /></Field>
          <Field label="Article (fill any [OWNER: …] blanks)"><textarea className={inputCls + ' font-mono text-xs'} rows={12} value={md} onChange={(e) => setMd(e.target.value)} /></Field>
          <div className="flex gap-2"><Button onClick={() => decide.mutate('approve')} loading={decide.isPending}>Approve & add to knowledge</Button></div>
        </div>
      </Modal>
    </div>
  )
}

function AddDoc({ open, onClose, onDone }: { open: boolean; onClose: () => void; onDone: () => void }) {
  const [title, setTitle] = useState('')
  const [content, setContent] = useState('')
  const m = useMutation({ mutationFn: () => api('/v1/kb/documents', { body: { title, content } }), onSuccess: () => { setTitle(''); setContent(''); onClose(); onDone() } })
  return (
    <Modal open={open} onClose={onClose} title="New article" wide>
      <div className="space-y-3">
        <Field label="Title"><input className={inputCls} value={title} onChange={(e) => setTitle(e.target.value)} /></Field>
        <Field label="Content (markdown)"><textarea className={inputCls} rows={12} value={content} onChange={(e) => setContent(e.target.value)} /></Field>
        <Button onClick={() => m.mutate()} loading={m.isPending} disabled={!title || !content}>Save & index</Button>
      </div>
    </Modal>
  )
}

function ViewDoc({ id, onClose }: { id: string | null; onClose: () => void }) {
  const { data } = useQuery({ queryKey: ['kb-doc', id], queryFn: () => api(`/v1/kb/documents/${id}`), enabled: !!id })
  return (
    <Modal open={!!id} onClose={onClose} title={data?.title || '…'} wide>
      <div className="prose-sm text-sm text-slate-700"><ReactMarkdown>{data?.content_md || ''}</ReactMarkdown></div>
    </Modal>
  )
}
