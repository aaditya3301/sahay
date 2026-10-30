// A real test call in the browser: the dashboard joins a LiveKit room as the caller and our worker answers with
// Sarvam's voice (Saaras STT, Bulbul TTS), on the same tools and rules as a phone call.
import { Mic, MicOff, Phone, PhoneOff } from 'lucide-react'
import { Room, RoomEvent, Track } from 'livekit-client'
import { useEffect, useRef, useState } from 'react'
import { Link } from 'react-router-dom'
import { api } from '../lib/api'
import { humanize } from '../lib/format'
import { Badge, Button, cx } from './ui'

type Line = { id: string; role: 'agent' | 'caller'; text: string }
const STATE: Record<string, [string, string]> = {
  initializing: ['Connecting…', 'slate'], listening: ['Listening', 'green'], thinking: ['Thinking…', 'amber'], speaking: ['Speaking', 'violet'],
}

export function LiveKitCall({ phone }: { phone: string }) {
  const [status, setStatus] = useState<'idle' | 'connecting' | 'live' | 'ending'>('idle')
  const [agentState, setAgentState] = useState('')
  const [lines, setLines] = useState<Line[]>([])
  const [muted, setMuted] = useState(false)
  const [summary, setSummary] = useState<any>(null)
  const [err, setErr] = useState('')
  const roomRef = useRef<Room | null>(null)
  const callRef = useRef<string | null>(null)
  const audioRef = useRef<HTMLDivElement>(null)

  useEffect(() => () => { roomRef.current?.disconnect() }, [])

  // One text stream per transcript segment: agent speech arrives as growing deltas, caller speech as interim
  // results that replace each other, so keep the latest full text per segment.
  const upsert = (id: string, role: Line['role'], text: string) => setLines((ls) => {
    const i = ls.findIndex((l) => l.id === id)
    if (i < 0) return [...ls, { id, role, text }]
    const next = ls.slice(); next[i] = { ...next[i], text }; return next
  })

  const fetchSummary = async (id: string) => {
    for (let i = 0; i < 15; i++) {  // the worker saves the transcript as it leaves; the summary follows a few seconds later
      await new Promise((r) => setTimeout(r, 2000))
      try {
        const c = await api(`/v1/calls/${id}`)
        if (c.status !== 'in_progress' && c.status !== 'queued') { setSummary(c); return }
      } catch { /* keep polling */ }
    }
  }

  const start = async () => {
    setErr(''); setSummary(null); setLines([]); setAgentState(''); setStatus('connecting')
    try {
      const r = await api('/v1/voice/livekit/web-call', { body: { phone: phone || null } })
      callRef.current = r.call_id
      const room = new Room({ adaptiveStream: true, dynacast: true })
      roomRef.current = room
      room.on(RoomEvent.TrackSubscribed, (track) => {
        if (track.kind !== Track.Kind.Audio) return
        const el = track.attach()
        audioRef.current?.appendChild(el)
      })
      room.on(RoomEvent.TrackUnsubscribed, (track) => { track.detach().forEach((el) => el.remove()) })
      room.on(RoomEvent.ParticipantConnected, (p) => { if (p.isAgent) setStatus('live') })
      room.on(RoomEvent.ParticipantAttributesChanged, (_changed, p) => {
        if (p.isAgent && p.attributes['lk.agent.state']) setAgentState(p.attributes['lk.agent.state'])
      })
      room.on(RoomEvent.Disconnected, () => {
        roomRef.current = null; setStatus('idle'); setAgentState('')
        if (callRef.current) fetchSummary(callRef.current)
      })
      room.registerTextStreamHandler('lk.transcription', async (reader, from) => {
        const id = reader.info.attributes?.['lk.segment_id'] || reader.info.id
        const role = from.identity === room.localParticipant.identity ? 'caller' : 'agent'
        let text = ''
        for await (const chunk of reader) { text += chunk; upsert(id, role, text) }
      })
      await room.connect(r.url, r.token)
      await room.localParticipant.setMicrophoneEnabled(true)
      await room.startAudio()
      if ([...room.remoteParticipants.values()].some((p) => p.isAgent)) setStatus('live')
    } catch (e: any) {
      setErr(e.message || String(e)); setStatus('idle'); roomRef.current?.disconnect()
    }
  }

  const hangup = async () => { setStatus('ending'); await roomRef.current?.disconnect() }
  const toggleMute = async () => {
    await roomRef.current?.localParticipant.setMicrophoneEnabled(muted)
    setMuted(!muted)
  }

  const [label, tone] = STATE[agentState] || (status === 'connecting' ? ['Calling the agent…', 'slate'] : ['', 'slate'])
  return (
    <div>
      <div ref={audioRef} className="hidden" />
      {status === 'idle' ? (
        <Button onClick={start}><Phone className="h-4 w-4" />Start call (Sarvam voice)</Button>
      ) : (
        <div className="flex flex-wrap items-center gap-2">
          <Badge tone="green">● {status === 'live' ? 'On call' : status === 'ending' ? 'Hanging up…' : 'Connecting'}</Badge>
          {label && <Badge tone={tone}>{label}</Badge>}
          <div className="flex-1" />
          <Button size="sm" variant="secondary" onClick={toggleMute} disabled={status !== 'live'}>
            {muted ? <><MicOff className="h-3.5 w-3.5" />Unmute</> : <><Mic className="h-3.5 w-3.5" />Mute</>}
          </Button>
          <Button size="sm" variant="danger" onClick={hangup}><PhoneOff className="h-3.5 w-3.5" />End call</Button>
        </div>
      )}
      {err && <div className="mt-2 text-xs text-red-600">{err}</div>}
      {lines.length > 0 && (
        <div className="mt-3 max-h-[420px] space-y-2 overflow-y-auto rounded-lg bg-slate-50 p-3 scroll-thin">
          {lines.map((l) => (
            <div key={l.id} className={cx('flex', l.role === 'caller' ? 'justify-end' : 'justify-start')}>
              <div className={cx('max-w-[80%] rounded-xl px-3 py-2 text-sm', l.role === 'caller' ? 'bg-brand-600 text-white' : 'border border-slate-200 bg-white text-slate-800')}>
                <div className="mb-0.5 text-[10px] opacity-60">{l.role === 'caller' ? 'You' : 'Voice agent'}</div>
                {l.text}
              </div>
            </div>
          ))}
        </div>
      )}
      {summary && (
        <div className="mt-3 rounded-lg border border-emerald-200 bg-emerald-50 p-3 text-sm">
          <div className="mb-1 flex items-center gap-2 font-medium text-emerald-900">Call summary {summary.outcome && <Badge tone="green">{humanize(summary.outcome)}</Badge>}</div>
          <div className="text-slate-700">{summary.summary || 'No summary yet.'}</div>
          {summary.next_step && <div className="mt-1 text-xs text-slate-600">Next step: {summary.next_step}</div>}
          <Link to="/calls" className="mt-2 inline-block text-xs font-medium text-brand-700 underline">See it in Calls →</Link>
        </div>
      )}
    </div>
  )
}
