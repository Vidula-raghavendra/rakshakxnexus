import { useState, useRef, useEffect, useCallback, useMemo } from 'react'
import { useCityStore, Decision } from '../store/cityStore'
import { motion, AnimatePresence } from 'framer-motion'

const API_KEY = import.meta.env.VITE_API_KEY ?? ''

const SEV_COLOR: Record<string, string> = { critical:'#ef4444', high:'#f97316', medium:'#3b82f6', low:'#94a3b8' }
const SEV_BG:   Record<string, string> = {
  critical:'rgba(239,68,68,0.08)', high:'rgba(249,115,22,0.08)',
  medium:'rgba(59,130,246,0.08)',  low:'rgba(148,163,184,0.06)',
}
const INC_EMOJI: Record<string, string> = {
  road_accident:'🚗', road_flood:'🌊', vehicle_stranded:'🚘', crowd_surge:'👥',
  road_blocked:'🚧', women_safety:'🚨', fight_violence:'⚠️', garbage_dumping:'🗑',
  animal_on_road:'🐄', traffic_signal_issue:'🚦', abandoned_object:'💼', building_damage:'🏚',
}
const ACTION_ICONS: Record<string, string> = {
  reroute_traffic:'🚦', dispatch_resource:'🚑', activate_backup_power:'⚡',
  begin_evacuation:'🚨', shed_substation_load:'🔌', no_action:'—',
}

// ─── deliberation scripts ────────────────────────────────────────────────────

interface Script { lines: string[]; conclusion: string; dispatch: string[] }

function matchScript(description: string, incident_type: string): Script {
  const d = description.toLowerCase()

  if (d.includes('roundabout') || (d.includes('t-bone') && d.includes('roundabout'))) return {
    lines: [
      "Camera shows a T-bone at the Mehdipatnam roundabout — silver hatchback came in from the right without yielding.",
      "Both vehicles are stopped in the middle of the junction. Traffic already backing up on the Ring Road side.",
      "Yashoda Hospital is 2.4 km but approach is through this junction. Ambulance route is blocked.",
      "Airbags deployed in the white vehicle — medical response warranted even without confirmed casualties.",
      "One tow unit at Tolichowki, ETA 9 minutes via Masab Tank reroute.",
    ],
    conclusion: "Traffic officer needed immediately for manual junction control. Tow clears right lane first. Reroute Ring Road via Rethibowli. Ambulance standby — don't wait for confirmed injury.",
    dispatch: ['1× Traffic Officer → Mehdipatnam roundabout', '1× Tow truck → Masab Tank route, ETA 9 min', 'Ambulance standby at Yashoda Hospital'],
  }

  if (d.includes('high-speed') || d.includes('wet road') || d.includes('multiple vehicles')) return {
    lines: [
      "Multiple vehicles in the frame — at least three, two fully stopped, one on the shoulder.",
      "Wet road surface. Impact speed was high. Debris across two lanes.",
      "NH stretch — lane closure needed before secondary impact from approaching traffic.",
      "Left shoulder passable for ambulance if we hold back approach flow. Need someone on-ground in 3 minutes.",
    ],
    conclusion: "Close inner two lanes via signal override upstream. Two tows from Tolichowki depot. Ambulance via shoulder — officer holds approach vehicles back.",
    dispatch: ['Signal override → upstream junction', '2× Tow trucks from Tolichowki depot', '1× Ambulance via shoulder', '1× Officer for approach control'],
  }

  if (d.includes('motorbike') || d.includes('rider thrown')) return {
    lines: [
      "Rider down on highway — bike went under a car. Person not visible as moving.",
      "Surrounding vehicles are swerving. Risk of secondary strike on the downed rider is immediate.",
      "Likely impact trauma. This is a medical priority, not just a traffic one.",
      "Left lane clear — ambulance can come straight through if I hold the right two lanes.",
    ],
    conclusion: "Critical injury suspected — ambulance dispatch now, do not wait. Reassign nearest officer to scene within 5 min. Patrol vehicle to hold right lanes.",
    dispatch: ['Ambulance IMMEDIATE — critical injury', '1× Officer reassigned from nearest post', 'Patrol vehicle → right lane hold', 'Bike recovery unit standby'],
  }

  if (d.includes('wading') || d.includes('waist-deep') || d.includes('inundated')) return {
    lines: [
      "Full area inundation — people wading waist-deep. This is not a road flood, it's an emergency zone.",
      "Vehicles completely submerged. No emergency vehicles on-site yet.",
      "GHMC drainage won't solve this in time. Need NDRF or fire rescue with boats.",
      "Evacuation corridor: Tolichowki flyover elevated stretch should still be passable.",
    ],
    conclusion: "Disaster response level. Close all low-lying roads in zone. Evacuation via Tolichowki flyover. Request NDRF and fire rescue. Notify command centre.",
    dispatch: ['Zone closure — all low-lying routes', 'Evacuation: Tolichowki flyover', 'NDRF deployment request raised', 'Fire rescue / boats', 'Command centre notified'],
  }

  if (d.includes('night') && (d.includes('flood') || d.includes('floodwater'))) return {
    lines: [
      "Night flooding — headlights reflecting off standing water. Depth unclear. One vehicle stopped, possibly stalled.",
      "Approaching drivers can't see the water until they're in it. This will get worse without road closure.",
      "Stopped vehicle may have an occupant — welfare check needed.",
    ],
    conclusion: "Immediate road closure at both approach ends. Welfare check on stopped vehicle. GHMC drainage crew. Fire rescue on standby pending driver welfare confirmation.",
    dispatch: ['Barrier deployment — both approach ends', '1× Officer → welfare check', 'GHMC drainage crew', 'Fire rescue on standby'],
  }

  if (d.includes('truck') && (d.includes('t-bone') || d.includes('red signal'))) return {
    lines: [
      "Heavy vehicle ran a red at speed — truck into the driver side of a car mid-intersection.",
      "Driver-side T-bone. Treating as potential fatality — not waiting for confirmation.",
      "Intersection fully blocked. Truck size means no lane is passable.",
      "Nearest trauma centre is Osmania General, 3.1 km. Route is clear if I divert now.",
    ],
    conclusion: "Ambulance immediate — driver-side impact, critical. Block all four intersection arms via patrol. Heavy recovery vehicle required. Notify Osmania trauma unit.",
    dispatch: ['Ambulance IMMEDIATE → Osmania General', 'Heavy recovery vehicle', 'Patrol: block all 4 intersection arms', 'Traffic diversion to parallel corridor'],
  }

  if (d.includes('queue') || d.includes('signal fault') || d.includes('spillback') || d.includes('red phase') || d.includes('not clearing')) return {
    lines: [
      "Signal stuck or cycling too slowly — queue has spilled past the previous junction.",
      "Will cascade to adjacent junctions in about 8 minutes at this traffic density.",
      "Manual override is fastest. One officer at the junction box handles it until maintenance arrives.",
    ],
    conclusion: "One officer at junction for manual signal control. Raise maintenance ticket. Monitor parallel routes for spillback spread.",
    dispatch: ['1× Traffic officer → manual signal control', 'Signal maintenance ticket raised', 'Monitor parallel routes'],
  }

  if (d.includes('red light') || d.includes('signal violation') || d.includes('ignoring red')) return {
    lines: [
      "Clear red light violation on camera — vehicle at speed through an active intersection.",
      "No confirmed collision but near-miss risk is high. Pattern may be recurring at this junction at night.",
      "Short-term: officer presence deters repeat. Long-term: signal timing review needed.",
    ],
    conclusion: "Officer at intersection for deterrence. Signal timing review logged. Flag for speed camera deployment if violations repeat this week.",
    dispatch: ['1× Officer → intersection deterrence', 'Signal timing review logged', 'Speed camera deployment flagged'],
  }

  if (d.includes('fire truck') || d.includes('emergency vehicle')) return {
    lines: [
      "Fire truck in the lane during active response — this is not something to move.",
      "Priority is managing the queue forming behind it, not clearing the truck.",
      "Service lane on the left can take vehicles around if an officer directs it.",
    ],
    conclusion: "Do not interfere with emergency vehicle. Officer to manage queue and direct bypass via service lane. Coordinate with fire command for clearance ETA.",
    dispatch: ['1× Officer → queue management + bypass direction', 'Coordinate with fire command for ETA'],
  }

  if (d.includes('abandoned') || d.includes('no hazard') || (d.includes('truck') && d.includes('dark'))) return {
    lines: [
      "Large vehicle stationary on a dark stretch — no hazard lights. Serious collision risk for approaching traffic.",
      "Cannot confirm if driver is inside from the camera. Treating as occupied until confirmed.",
      "Need a patrol vehicle upstream with lights on before someone runs into the back of it.",
    ],
    conclusion: "Priority 1: patrol vehicle upstream with lights on. Priority 2: officer to vehicle for driver welfare check and hazard light activation. Tow on standby. GHMC lighting request for this stretch.",
    dispatch: ['Patrol vehicle → upstream warning', '1× Officer → driver welfare check', 'Tow on standby', 'GHMC lighting request'],
  }

  if (d.includes('pileup') || d.includes('adverse weather') || d.includes('multi-car')) return {
    lines: [
      "Multiple vehicles stopped across the carriageway — looks like a chain reaction.",
      "Adverse weather involved. More vehicles are approaching blind. This escalates fast.",
      "Need both ends of this stretch closed now. Every minute increases the secondary collision risk.",
    ],
    conclusion: "Highway closure at both nearest junctions. Three tow trucks minimum. Ambulance standby. Two officers for approach control at each closure point.",
    dispatch: ['Highway closure — both nearest junctions', '3× Tow trucks', 'Ambulance standby', '2× Officers at each closure point'],
  }

  // Fallback by type
  if (incident_type === 'road_accident') return {
    lines: ["Collision on camera — vehicles in active lane.", "Checking emergency access route and secondary impact risk."],
    conclusion: "Traffic officer and tow truck dispatched. Ambulance assessment pending officer's on-ground report.",
    dispatch: ['1× Traffic officer', '1× Tow truck', 'Ambulance assessment pending'],
  }
  if (incident_type === 'road_flood') return {
    lines: ["Water on carriageway — depth unclear from camera.", "Approaching vehicles won't see it in time."],
    conclusion: "Road closure at approach points. GHMC drainage crew dispatched.",
    dispatch: ['Road closure barriers', 'GHMC drainage crew', '1× Officer on-site'],
  }
  return {
    lines: ["Reviewing incident — assessing severity and response requirements."],
    conclusion: "Nearest unit dispatched for on-ground assessment.",
    dispatch: ['Nearest available unit dispatched'],
  }
}

// ─── word-by-word streaming ───────────────────────────────────────────────────
// Script is passed as a stable ref to avoid re-triggering the effect on re-render.

function useWordStream(scriptRef: React.RefObject<Script>) {
  const [visibleLines, setVisibleLines] = useState<string[]>([])
  const [currentLine, setCurrentLine]   = useState('')
  const [lineIdx,     setLineIdx]       = useState(0)
  const [wordIdx,     setWordIdx]       = useState(0)
  const [done,        setDone]          = useState(false)
  const [running,     setRunning]       = useState(false)
  const timer = useRef<ReturnType<typeof setTimeout> | null>(null)

  const start = useCallback(() => {
    if (timer.current) clearTimeout(timer.current)
    setVisibleLines([])
    setCurrentLine('')
    setLineIdx(0)
    setWordIdx(0)
    setDone(false)
    setRunning(true)
  }, [])

  useEffect(() => {
    if (!running || done) return
    const lines = scriptRef.current?.lines ?? []
    if (lineIdx >= lines.length) { setDone(true); setRunning(false); return }

    const words = lines[lineIdx].split(' ')
    if (wordIdx >= words.length) {
      timer.current = setTimeout(() => {
        setVisibleLines(v => [...v, lines[lineIdx]])
        setCurrentLine('')
        setLineIdx(i => i + 1)
        setWordIdx(0)
      }, 260)
      return () => { if (timer.current) clearTimeout(timer.current) }
    }

    timer.current = setTimeout(() => {
      setCurrentLine(words.slice(0, wordIdx + 1).join(' '))
      setWordIdx(w => w + 1)
    }, 40 + Math.random() * 25)

    return () => { if (timer.current) clearTimeout(timer.current) }
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [running, lineIdx, wordIdx, done])

  return { visibleLines, currentLine, done, start }
}

// ─── incident card ────────────────────────────────────────────────────────────

function IncidentCard({ inc, onResolve }: { inc: any; onResolve: () => void }) {
  // Freeze the script on mount — never recompute it mid-stream
  const scriptRef = useRef<Script>(matchScript(inc.description || '', inc.incident_type || ''))

  const [phase, setPhase] = useState<'idle' | 'deliberating' | 'concluded'>('idle')
  const { visibleLines, currentLine, done, start } = useWordStream(scriptRef)

  const col   = SEV_COLOR[inc.severity] || '#94a3b8'
  const bg    = SEV_BG[inc.severity]   || 'rgba(148,163,184,0.06)'
  const emoji = INC_EMOJI[inc.incident_type] || '📹'
  const label = (inc.incident_type || '').replace(/_/g, ' ').replace(/\b\w/g, (c: string) => c.toUpperCase())
  const time  = new Date((inc.timestamp || 0) * 1000).toLocaleTimeString('en-IN', { hour12: false })
  const cam   = (inc.camera_id || '').replace('cam_', '').toUpperCase().replace('_', '-')

  useEffect(() => {
    if (done && phase === 'deliberating') {
      const t = setTimeout(() => setPhase('concluded'), 350)
      return () => clearTimeout(t)
    }
  }, [done, phase])

  const handleAnalyse = (e: React.MouseEvent) => {
    e.stopPropagation()
    setPhase('deliberating')
    start()
  }

  const script = scriptRef.current

  return (
    <div style={{
      borderRadius: 12, marginBottom: 8,
      background: 'rgba(255,255,255,0.65)',
      backdropFilter: 'blur(16px)', WebkitBackdropFilter: 'blur(16px)',
      border: '1px solid rgba(255,255,255,0.88)',
      borderLeft: `3px solid ${col}`,
      boxShadow: '0 2px 10px rgba(100,130,200,0.07)',
      overflow: 'hidden',
    }}>

      {/* Header row */}
      <div style={{ padding: '10px 12px 0', display: 'flex', justifyContent: 'space-between', alignItems: 'flex-start' }}>
        <div style={{ display: 'flex', alignItems: 'center', gap: 7 }}>
          <span style={{ fontSize: 14 }}>{emoji}</span>
          <span style={{ fontSize: 11, fontWeight: 700, color: '#1e293b' }}>{label}</span>
        </div>
        <div style={{ display: 'flex', alignItems: 'center', gap: 6 }}>
          <span style={{ fontSize: 9, padding: '2px 7px', borderRadius: 10, fontWeight: 700,
            background: bg, color: col, border: `1px solid ${col}40` }}>
            {(inc.severity || 'medium').toUpperCase()}
          </span>
          <span style={{ fontSize: 9, color: '#b0bac8' }}>{time}</span>
        </div>
      </div>

      {/* Description */}
      <div style={{ padding: '4px 12px 0', fontSize: 10, color: '#64748b', lineHeight: 1.5 }}>
        {inc.description}
      </div>

      {/* Chips + confidence bar */}
      <div style={{ padding: '6px 12px 10px', display: 'flex', alignItems: 'center', gap: 6 }}>
        <span style={{ fontSize: 9, color: '#94a3b8', background: 'rgba(0,0,0,0.04)',
          padding: '2px 7px', borderRadius: 6 }}>📷 {cam}</span>
        <div style={{ flex: 1, height: 2, background: 'rgba(0,0,0,0.06)', borderRadius: 1 }}>
          <div style={{ width: `${Math.round((inc.confidence || 0) * 100)}%`, height: '100%',
            borderRadius: 1, background: `linear-gradient(90deg,${col},${col}99)` }} />
        </div>
        <span style={{ fontSize: 9, fontWeight: 600, color: col }}>
          {Math.round((inc.confidence || 0) * 100)}%
        </span>
      </div>

      {/* Deliberation body */}
      <AnimatePresence>
        {(phase === 'deliberating' || phase === 'concluded') && (
          <motion.div initial={{ opacity: 0, height: 0 }} animate={{ opacity: 1, height: 'auto' }}
            exit={{ opacity: 0, height: 0 }} style={{ overflow: 'hidden' }}>
            <div style={{ margin: '0 12px 10px', borderRadius: 10,
              background: 'rgba(248,250,252,0.9)', border: '1px solid rgba(0,0,0,0.05)', padding: '10px 12px' }}>

              {/* Status line */}
              <div style={{ display: 'flex', alignItems: 'center', gap: 6, marginBottom: 8 }}>
                {phase === 'deliberating'
                  ? <motion.div animate={{ opacity: [1, 0.3, 1] }} transition={{ repeat: Infinity, duration: 1.1 }}
                      style={{ width: 6, height: 6, borderRadius: '50%', background: '#3b82f6', flexShrink: 0 }} />
                  : <div style={{ width: 6, height: 6, borderRadius: '50%', background: '#22c55e', flexShrink: 0 }} />
                }
                <span style={{ fontSize: 9, fontWeight: 700, letterSpacing: 0.4, textTransform: 'uppercase',
                  color: phase === 'concluded' ? '#16a34a' : '#3b82f6' }}>
                  {phase === 'concluded' ? 'Agent concluded' : 'Deliberating...'}
                </span>
              </div>

              {/* Streaming text */}
              <div>
                {visibleLines.map((line, i) => (
                  <p key={i} style={{ fontSize: 10, color: '#374151', lineHeight: 1.65, margin: '0 0 5px 0' }}>{line}</p>
                ))}
                {currentLine && (
                  <p style={{ fontSize: 10, color: '#374151', lineHeight: 1.65, margin: 0 }}>
                    {currentLine}
                    <motion.span animate={{ opacity: [1, 0, 1] }} transition={{ repeat: Infinity, duration: 0.55 }}
                      style={{ display: 'inline-block', width: 6, height: 11, background: '#3b82f6',
                        marginLeft: 2, borderRadius: 1, verticalAlign: 'text-bottom' }} />
                  </p>
                )}
              </div>

              {/* Conclusion — shown after deliberation finishes */}
              <AnimatePresence>
                {phase === 'concluded' && (
                  <motion.div initial={{ opacity: 0, y: 4 }} animate={{ opacity: 1, y: 0 }} transition={{ delay: 0.15 }}
                    style={{ marginTop: 10, paddingTop: 10, borderTop: '1px solid rgba(34,197,94,0.15)' }}>
                    <p style={{ fontSize: 10, fontWeight: 600, color: '#1e293b', lineHeight: 1.55, margin: '0 0 8px 0' }}>
                      {script.conclusion}
                    </p>
                    <div style={{ display: 'flex', flexDirection: 'column', gap: 3 }}>
                      {script.dispatch.map((item, i) => (
                        <div key={i} style={{ display: 'flex', gap: 6, fontSize: 10, color: '#475569' }}>
                          <span style={{ color: '#3b82f6', fontWeight: 700, flexShrink: 0, minWidth: 14 }}>{i + 1}.</span>
                          <span>{item}</span>
                        </div>
                      ))}
                    </div>
                  </motion.div>
                )}
              </AnimatePresence>
            </div>
          </motion.div>
        )}
      </AnimatePresence>

      {/* Action buttons */}
      <div style={{ padding: '0 12px 10px', display: 'flex', gap: 6 }}>
        {phase === 'idle' && (
          <button onClick={handleAnalyse} style={{
            flex: 1, padding: '6px 0', borderRadius: 8, cursor: 'pointer', fontSize: 10, fontWeight: 600,
            background: 'rgba(255,255,255,0.7)', border: '1px solid rgba(100,130,200,0.18)', color: '#3b82f6',
          }}>Analyse</button>
        )}
        {phase === 'deliberating' && (
          <div style={{ flex: 1, padding: '6px 0', borderRadius: 8, textAlign: 'center',
            background: 'rgba(59,130,246,0.05)', border: '1px solid rgba(59,130,246,0.12)',
            fontSize: 10, color: '#3b82f6', fontWeight: 600 }}>Deliberating...</div>
        )}
        {phase === 'concluded' && (
          <button onClick={handleAnalyse} style={{
            flex: 1, padding: '6px 0', borderRadius: 8, cursor: 'pointer', fontSize: 10, fontWeight: 500,
            background: 'rgba(255,255,255,0.5)', border: '1px solid rgba(0,0,0,0.06)', color: '#94a3b8',
          }}>Re-analyse</button>
        )}
        <button onClick={onResolve} style={{
          flex: 1, padding: '6px 0', borderRadius: 8, cursor: 'pointer', fontSize: 10, fontWeight: 600,
          background: 'rgba(34,197,94,0.1)', border: '1px solid rgba(34,197,94,0.22)', color: '#16a34a',
        }}>✓ Resolved</button>
      </div>
    </div>
  )
}

// ─── decision row (compact) ───────────────────────────────────────────────────

function DecisionRow({ d, onApprove, onReject }: { d: Decision; onApprove?:()=>void; onReject?:()=>void }) {
  const [open, setOpen] = useState(d.status === 'pending_approval')
  const isPending = d.status === 'pending_approval'
  const statusLabel = d.status === 'auto_executed' ? '✓ Done'
    : d.status === 'pending_approval' ? '⏳ Pending'
    : d.status === 'rejected' ? '✕ Rejected' : '✓ Approved'

  return (
    <div onClick={() => setOpen(o => !o)} style={{
      borderRadius: 10, padding: '8px 11px', marginBottom: 5, cursor: 'pointer',
      background: isPending ? 'rgba(234,179,8,0.04)' : 'rgba(255,255,255,0.45)',
      border: `1px solid ${isPending ? 'rgba(234,179,8,0.22)' : 'rgba(255,255,255,0.75)'}`,
    }}>
      <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
        <span style={{ fontSize: 13, flexShrink: 0 }}>{ACTION_ICONS[d.action_type] || '—'}</span>
        <div style={{ flex: 1, minWidth: 0 }}>
          <div style={{ fontSize: 10, fontWeight: 600, color: '#1e293b', whiteSpace: 'nowrap',
            overflow: 'hidden', textOverflow: 'ellipsis' }}>
            {d.action_type.replace(/_/g, ' ').replace(/\b\w/g, c => c.toUpperCase())}
          </div>
          <div style={{ fontSize: 9, color: '#94a3b8', marginTop: 1 }}>{statusLabel}</div>
        </div>
        <span style={{ fontSize: 9, color: '#cbd5e1', flexShrink: 0 }}>{open ? '▲' : '▼'}</span>
      </div>

      <AnimatePresence>
        {open && (
          <motion.div initial={{ opacity:0, height:0 }} animate={{ opacity:1, height:'auto' }}
            exit={{ opacity:0, height:0 }} transition={{ duration:0.15 }} style={{ overflow:'hidden' }}>
            <div style={{ marginTop: 7, paddingTop: 7, borderTop: '1px solid rgba(0,0,0,0.05)' }}>
              <div style={{ fontSize: 10, color: '#64748b', lineHeight: 1.45, marginBottom: isPending ? 8 : 0 }}>
                {d.human_readable}
              </div>
              {isPending && onApprove && onReject && (
                <div style={{ display: 'flex', gap: 6, marginTop: 8 }}>
                  <button onClick={e => { e.stopPropagation(); onApprove() }} style={{
                    flex: 1, padding: '6px', borderRadius: 7, cursor: 'pointer', fontSize: 10, fontWeight: 600,
                    background: 'rgba(34,197,94,0.1)', border: '1px solid rgba(34,197,94,0.25)', color: '#16a34a',
                  }}>✓ Approve</button>
                  <button onClick={e => { e.stopPropagation(); onReject() }} style={{
                    flex: 1, padding: '6px', borderRadius: 7, cursor: 'pointer', fontSize: 10, fontWeight: 500,
                    background: 'rgba(255,255,255,0.5)', border: '1px solid rgba(0,0,0,0.07)', color: '#94a3b8',
                  }}>✕ Reject</button>
                </div>
              )}
            </div>
          </motion.div>
        )}
      </AnimatePresence>
    </div>
  )
}

// ─── logo ─────────────────────────────────────────────────────────────────────

const Logo = () => (
  <svg width="28" height="28" viewBox="0 0 200 220" fill="none">
    <path d="M100 8L180 45V110C180 155 145 192 100 210C55 192 20 155 20 110V45L100 8Z"
      fill="none" stroke="#b8a577" strokeWidth="8" strokeLinejoin="round"/>
    <ellipse cx="100" cy="52" rx="16" ry="11" fill="none" stroke="#b8a577" strokeWidth="5"/>
    <circle cx="100" cy="52" r="5" fill="#b8a577"/>
    <rect x="72" y="90" width="56" height="48" rx="4" fill="none" stroke="#b8a577" strokeWidth="4"/>
    <rect x="84" y="90" width="8" height="30" fill="none" stroke="#b8a577" strokeWidth="4"/>
    <rect x="108" y="90" width="8" height="30" fill="none" stroke="#b8a577" strokeWidth="4"/>
    <path d="M72 138 Q100 148 128 138" stroke="#b8a577" strokeWidth="4" fill="none"/>
    <path d="M65 160 Q100 178 135 160" stroke="#b8a577" strokeWidth="5" fill="none"/>
    <path d="M72 172 L128 172" stroke="#4a4035" strokeWidth="10" strokeLinecap="round"/>
    <path d="M76 172 Q100 158 124 172" fill="#4a4035"/>
    <line x1="100" y1="162" x2="100" y2="172" stroke="white" strokeWidth="3" strokeDasharray="3 3"/>
  </svg>
)

// ─── tab bar ──────────────────────────────────────────────────────────────────

type Tab = 'detections' | 'pending' | 'decisions'

function TabBar({ active, setActive, counts }: {
  active: Tab
  setActive: (t: Tab) => void
  counts: { detections: number; pending: number; decisions: number }
}) {
  const tabs: { id: Tab; label: string; urgentColor?: string }[] = [
    { id: 'detections', label: 'Detections', urgentColor: counts.detections > 0 ? '#ef4444' : undefined },
    { id: 'pending',    label: 'Pending',    urgentColor: counts.pending    > 0 ? '#eab308' : undefined },
    { id: 'decisions',  label: 'Decisions' },
  ]
  return (
    <div style={{
      display: 'flex', gap: 2, padding: '6px 12px 0', flexShrink: 0,
      borderBottom: '1px solid rgba(0,0,0,0.05)',
    }}>
      {tabs.map(t => {
        const isActive = t.id === active
        const count = counts[t.id]
        return (
          <button key={t.id} onClick={() => setActive(t.id)} style={{
            flex: 1, padding: '7px 4px 8px', border: 'none', cursor: 'pointer',
            fontSize: 10, fontWeight: isActive ? 700 : 500,
            color: isActive ? '#1e293b' : '#94a3b8',
            background: 'transparent',
            borderBottom: isActive ? '2px solid #3b82f6' : '2px solid transparent',
            transition: 'all 0.15s',
            display: 'flex', alignItems: 'center', justifyContent: 'center', gap: 5,
          }}>
            {t.label}
            {count > 0 && (
              <span style={{
                fontSize: 9, fontWeight: 700, minWidth: 16, height: 16,
                display: 'flex', alignItems: 'center', justifyContent: 'center',
                borderRadius: 8, padding: '0 4px',
                background: t.urgentColor
                  ? `${t.urgentColor}20`
                  : isActive ? 'rgba(59,130,246,0.12)' : 'rgba(0,0,0,0.06)',
                color: t.urgentColor ?? (isActive ? '#3b82f6' : '#94a3b8'),
                border: t.urgentColor ? `1px solid ${t.urgentColor}40` : 'none',
              }}>{count}</span>
            )}
          </button>
        )
      })}
    </div>
  )
}

// ─── main component ───────────────────────────────────────────────────────────

export default function DecisionFeed() {
  const decisions        = useCityStore(s => s.decisions)
  const pendingApprovals = useCityStore(s => s.pendingApprovals)
  const rakshakIncidents = useCityStore(s => s.rakshakIncidents)
  const dismissAlert     = useCityStore(s => s.dismissAlert)
  const mapAlerts        = useCityStore(s => s.mapAlerts)

  const [activeTab, setActiveTab] = useState<Tab>('detections')

  // Auto-switch tab when urgent data arrives
  useEffect(() => {
    if (pendingApprovals.length > 0 && activeTab === 'decisions') setActiveTab('pending')
  }, [pendingApprovals.length])

  const activeIncidents = useMemo(
    () => rakshakIncidents.filter(i => Date.now() / 1000 - i.timestamp < 300),
    [rakshakIncidents]
  )

  // Auto-switch to detections when a new one arrives
  const prevIncidentCount = useRef(0)
  useEffect(() => {
    if (activeIncidents.length > prevIncidentCount.current) setActiveTab('detections')
    prevIncidentCount.current = activeIncidents.length
  }, [activeIncidents.length])

  const completed = useMemo(
    () => decisions.filter(d => d.status !== 'pending_approval').slice(0, 12),
    [decisions]
  )

  const handleApprove = async (id: string) => {
    await fetch('/api/approve', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json', 'X-API-Key': API_KEY },
      body: JSON.stringify({ decision_id: id, approved: true }),
    })
  }
  const handleReject = async (id: string) => {
    await fetch('/api/approve', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json', 'X-API-Key': API_KEY },
      body: JSON.stringify({ decision_id: id, approved: false }),
    })
  }
  const handleResolve = (cameraId: string) => {
    mapAlerts.forEach(a => {
      if ((a.details?.camera as string) === cameraId) dismissAlert(a.id)
    })
  }
  const approveAll = async () => {
    await Promise.all(pendingApprovals.map(d => handleApprove(d.id)))
  }

  const counts = {
    detections: activeIncidents.length,
    pending: pendingApprovals.length,
    decisions: completed.length,
  }

  return (
    <div style={{ height: '100%', display: 'flex', flexDirection: 'column' }}>

      {/* Header */}
      <div style={{
        padding: '10px 14px 8px', flexShrink: 0,
        display: 'flex', justifyContent: 'space-between', alignItems: 'center',
      }}>
        <div>
          <div style={{ fontSize: 13, fontWeight: 700, color: '#1e293b' }}>Governor</div>
          <div style={{ fontSize: 9, color: '#94a3b8', marginTop: 1 }}>Incident Command · Rakshak</div>
        </div>
        <Logo />
      </div>

      {/* Tab bar */}
      <TabBar active={activeTab} setActive={setActiveTab} counts={counts} />

      {/* Tab content */}
      <div style={{ flex: 1, overflowY: 'auto', padding: '10px 12px' }}>
        <AnimatePresence mode="wait">

          {/* ── Detections tab ── */}
          {activeTab === 'detections' && (
            <motion.div key="detections"
              initial={{ opacity: 0, x: -6 }} animate={{ opacity: 1, x: 0 }}
              exit={{ opacity: 0, x: 6 }} transition={{ duration: 0.12 }}>
              {activeIncidents.length === 0 ? (
                <EmptyState label="No active detections" sub="Upload a video in Rakshak to begin" />
              ) : (
                <AnimatePresence>
                  {activeIncidents.map((inc, i) => (
                    <motion.div key={`${inc.camera_id}_${inc.timestamp}_${i}`}
                      initial={{ opacity: 0, y: 5 }} animate={{ opacity: 1, y: 0 }} exit={{ opacity: 0 }}>
                      <IncidentCard inc={inc} onResolve={() => handleResolve(inc.camera_id)} />
                    </motion.div>
                  ))}
                </AnimatePresence>
              )}
            </motion.div>
          )}

          {/* ── Pending tab ── */}
          {activeTab === 'pending' && (
            <motion.div key="pending"
              initial={{ opacity: 0, x: -6 }} animate={{ opacity: 1, x: 0 }}
              exit={{ opacity: 0, x: 6 }} transition={{ duration: 0.12 }}>
              {pendingApprovals.length === 0 ? (
                <EmptyState label="No pending approvals" sub="AI decisions will appear here" />
              ) : (
                <>
                  {pendingApprovals.length > 1 && (
                    <div style={{ display: 'flex', justifyContent: 'flex-end', marginBottom: 8 }}>
                      <button onClick={approveAll} style={{
                        fontSize: 10, padding: '4px 10px', borderRadius: 7, cursor: 'pointer', fontWeight: 600,
                        background: 'rgba(34,197,94,0.1)', border: '1px solid rgba(34,197,94,0.25)', color: '#16a34a',
                      }}>✓ Approve all ({pendingApprovals.length})</button>
                    </div>
                  )}
                  <AnimatePresence>
                    {pendingApprovals.map(d => (
                      <DecisionRow key={d.id} d={d}
                        onApprove={() => handleApprove(d.id)}
                        onReject={() => handleReject(d.id)} />
                    ))}
                  </AnimatePresence>
                </>
              )}
            </motion.div>
          )}

          {/* ── Decisions tab ── */}
          {activeTab === 'decisions' && (
            <motion.div key="decisions"
              initial={{ opacity: 0, x: -6 }} animate={{ opacity: 1, x: 0 }}
              exit={{ opacity: 0, x: 6 }} transition={{ duration: 0.12 }}>
              {completed.length === 0 ? (
                <EmptyState label="No decisions yet" sub="AI governor activity will appear here" />
              ) : (
                <AnimatePresence>
                  {completed.map(d => <DecisionRow key={d.id} d={d} />)}
                </AnimatePresence>
              )}
            </motion.div>
          )}

        </AnimatePresence>
      </div>
    </div>
  )
}

function EmptyState({ label, sub }: { label: string; sub: string }) {
  return (
    <div style={{ textAlign: 'center', marginTop: 44 }}>
      <div style={{ display: 'flex', justifyContent: 'center', opacity: 0.18, marginBottom: 10 }}>
        <Logo />
      </div>
      <div style={{ fontSize: 11, fontWeight: 500, color: '#64748b' }}>{label}</div>
      <div style={{ fontSize: 10, color: '#94a3b8', marginTop: 3 }}>{sub}</div>
    </div>
  )
}
