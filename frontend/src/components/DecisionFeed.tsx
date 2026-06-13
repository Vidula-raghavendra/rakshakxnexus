import { useState, useEffect, useRef } from 'react'
import { useCityStore, Decision, AlertDispatch } from '../store/cityStore'
import { motion, AnimatePresence } from 'framer-motion'

const API_KEY = import.meta.env.VITE_API_KEY ?? ''

const PRIORITY_COLORS = {
  low:      '#6b7280',
  medium:   '#3b82f6',
  high:     '#f97316',
  critical: '#ef4444',
}

const STATUS_LABELS: Record<string, string> = {
  auto_executed:     '✅ AUTO-EXEC',
  pending_approval:  '⏳ NEEDS APPROVAL',
  approved_executed: '✅ APPROVED',
  rejected:          '❌ REJECTED',
  manual_executed:   '👤 MANUAL',
  no_action:         '— IDLE',
}

const ACTION_ICONS: Record<string, string> = {
  reroute_traffic:                 '🚦',
  dispatch_resource:               '🚑',
  activate_backup_power:           '⚡',
  shed_substation_load:            '🔌',
  begin_evacuation:                '🚨',
  reposition_resources_preemptive: '📍',
  no_action:                       '💤',
}

// Parse Gemini deliberation stream into labelled steps
function parseDeliberation(text: string) {
  const lines = text.split('\n').filter(l => l.trim())
  return lines.map(line => {
    const m = line.match(/^>\s*(PERCEIVE|RISK|CONFLICT|DECISION|REQUIRES_APPROVAL|HUMAN_READABLE):\s*(.*)/)
    if (m) return { label: m[1], content: m[2], raw: false }
    return { label: '', content: line, raw: true }
  })
}

const STEP_COLORS: Record<string, string> = {
  PERCEIVE:          '#60a5fa',
  RISK:              '#f97316',
  CONFLICT:          '#eab308',
  DECISION:          '#22c55e',
  REQUIRES_APPROVAL: '#a855f7',
  HUMAN_READABLE:    '#e2e8f0',
}

// Build recommended control room steps from a decision
function buildSteps(d: Decision): string[] {
  const steps: string[] = []
  switch (d.action_type) {
    case 'reroute_traffic': {
      const zone = (d.parameters.zone_id as string || '').replace(/_/g, ' ')
      const divert = ((d.parameters.divert_to as string[]) || []).map(z => z.replace(/_/g, ' ')).join(', ')
      steps.push(`Close entry to ${zone}`)
      steps.push(`Redirect traffic via ${divert || 'alternate route'}`)
      steps.push('Deploy traffic police at diversion point')
      steps.push('Update variable message signs on approaches')
      break
    }
    case 'dispatch_resource': {
      const res = d.parameters.resource_id as string || ''
      const dest = (d.parameters.destination_id as string || '').replace(/_/g, ' ')
      steps.push(`Dispatch ${res.toUpperCase()} to ${dest}`)
      steps.push('Confirm ETA with unit via radio')
      steps.push('Clear route if road is congested')
      break
    }
    case 'activate_backup_power': {
      const hosp = (d.parameters.hospital_id as string || '').replace(/_/g, ' ')
      steps.push(`Switch ${hosp} to backup generator`)
      steps.push('Verify generator fuel level (min 4hr reserve)')
      steps.push('Alert hospital facilities manager')
      steps.push('Log outage in BESCOM fault tracker')
      break
    }
    case 'begin_evacuation': {
      const zone = (d.parameters.zone_id as string || '').replace(/_/g, ' ')
      steps.push(`Issue PA announcement at ${zone}`)
      steps.push('Open evacuation route — confirm road is clear')
      steps.push('Dispatch police escort for pedestrians')
      steps.push('Coordinate with nearest relief centre')
      break
    }
    case 'shed_substation_load': {
      const sub = (d.parameters.substation_id as string || '').replace(/_/g, ' ')
      const frac = Math.round(((d.parameters.target_fraction as number) || 0.7) * 100)
      steps.push(`Reduce ${sub} load to ${frac}% capacity`)
      steps.push('Verify hospitals on this feed have backup active')
      steps.push('Notify affected commercial zones')
      break
    }
    default:
      steps.push('Monitor situation — no physical action required')
  }
  return steps
}

function ConfidenceBar({ value }: { value: number }) {
  const color = value >= 0.85 ? '#22c55e' : value >= 0.6 ? '#f97316' : '#ef4444'
  const label = value >= 0.85 ? 'HIGH — AUTO' : value >= 0.6 ? 'MED — APPROVAL' : 'LOW — HOLD'
  return (
    <div style={{ marginTop: 6 }}>
      <div style={{ display: 'flex', justifyContent: 'space-between', marginBottom: 2 }}>
        <span style={{ fontSize: 9, color: '#475569', textTransform: 'uppercase', letterSpacing: 1 }}>Confidence</span>
        <span style={{ fontSize: 9, color }}>{(value * 100).toFixed(0)}% · {label}</span>
      </div>
      <div style={{ height: 3, background: '#1e293b', borderRadius: 2 }}>
        <div style={{ width: `${value * 100}%`, height: '100%', background: color, borderRadius: 2,
          boxShadow: `0 0 6px ${color}66` }} />
      </div>
    </div>
  )
}

function ControlRoomCard({ d }: { d: Decision }) {
  const steps = buildSteps(d)
  const priorityColor = PRIORITY_COLORS[d.priority] || '#6b7280'
  return (
    <div style={{
      marginTop: 10, padding: '8px 10px',
      background: '#020c1a', border: `1px solid ${priorityColor}44`,
      borderLeft: `3px solid ${priorityColor}`, borderRadius: 4,
    }}>
      <div style={{ fontSize: 8, color: priorityColor, textTransform: 'uppercase', letterSpacing: 1, marginBottom: 6 }}>
        🎛 CONTROL ROOM — RECOMMENDED STEPS
      </div>
      {steps.map((s, i) => (
        <div key={i} style={{ display: 'flex', gap: 8, marginBottom: 4, alignItems: 'flex-start' }}>
          <span style={{
            fontSize: 8, color: priorityColor, fontWeight: 700, minWidth: 16,
            background: `${priorityColor}22`, borderRadius: 2, textAlign: 'center', padding: '1px 2px',
          }}>{i + 1}</span>
          <span style={{ fontSize: 10, color: '#94a3b8', lineHeight: 1.4 }}>{s}</span>
        </div>
      ))}
      <div style={{
        marginTop: 6, paddingTop: 6, borderTop: '1px solid #1e293b',
        fontSize: 9, color: '#334155',
        display: 'flex', justifyContent: 'space-between',
      }}>
        <span>Confidence: {(d.confidence * 100).toFixed(0)}%</span>
        <span>{d.reversible ? '↩ reversible' : '⚠ irreversible'}</span>
        <span>Tick {d.tick}</span>
      </div>
    </div>
  )
}

function DecisionCard({ d, onApprove, onReject }: {
  d: Decision
  onApprove?: () => void
  onReject?: () => void
}) {
  const [expanded, setExpanded] = useState(d.status === 'pending_approval')
  const priorityColor = PRIORITY_COLORS[d.priority] || '#6b7280'
  const statusLabel = STATUS_LABELS[d.status] || d.status
  const actionIcon = ACTION_ICONS[d.action_type] || '⚡'
  const isPending = d.status === 'pending_approval'

  return (
    <motion.div
      layout
      initial={{ opacity: 0, x: 20 }}
      animate={{ opacity: 1, x: 0 }}
      exit={{ opacity: 0, x: -20 }}
      transition={{ duration: 0.2 }}
      style={{
        background: isPending ? '#0c1a0c' : '#040d1a',
        border: `1px solid ${isPending ? priorityColor : '#0f1e2e'}`,
        borderLeft: `3px solid ${priorityColor}`,
        borderRadius: 4,
        padding: '8px 10px',
        marginBottom: 6,
        cursor: 'pointer',
      }}
      onClick={() => setExpanded(e => !e)}
    >
      {/* Header */}
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'flex-start' }}>
        <div style={{ display: 'flex', gap: 6, alignItems: 'center', flexWrap: 'wrap' }}>
          <span style={{ fontSize: 10, color: '#1e3a5f', fontFamily: 'monospace' }}>T{d.tick}</span>
          <span style={{
            fontSize: 8, background: `${priorityColor}22`, color: priorityColor,
            padding: '1px 5px', borderRadius: 10, textTransform: 'uppercase', fontWeight: 700,
          }}>{d.priority}</span>
          <span style={{ fontSize: 8, color: '#334155' }}>{statusLabel}</span>
        </div>
        <span style={{ fontSize: 9, color: '#1e3a5f' }}>{expanded ? '▲' : '▼'}</span>
      </div>

      <div style={{ display: 'flex', alignItems: 'center', gap: 6, marginTop: 5 }}>
        <span style={{ fontSize: 13 }}>{actionIcon}</span>
        <span style={{ fontSize: 11, color: '#cbd5e1', fontWeight: 700, fontFamily: 'monospace',
          textTransform: 'uppercase', letterSpacing: 0.5 }}>
          {d.action_type.replace(/_/g, ' ')}
        </span>
      </div>

      <div style={{ fontSize: 10, color: '#64748b', marginTop: 3, lineHeight: 1.4 }}>
        {d.human_readable}
      </div>

      <ConfidenceBar value={d.confidence} />

      <AnimatePresence>
        {expanded && (
          <motion.div
            initial={{ opacity: 0, height: 0 }}
            animate={{ opacity: 1, height: 'auto' }}
            exit={{ opacity: 0, height: 0 }}
            transition={{ duration: 0.15 }}
            style={{ overflow: 'hidden' }}
          >
            {d.reasoning && (
              <div style={{ marginTop: 8, paddingTop: 6, borderTop: '1px solid #0f1e2e' }}>
                <div style={{ fontSize: 8, color: '#1e3a5f', textTransform: 'uppercase', letterSpacing: 1, marginBottom: 4 }}>
                  🤖 Gemini 2.0 Flash Reasoning
                </div>
                <div style={{ fontSize: 10, color: '#475569', lineHeight: 1.5, fontStyle: 'italic' }}>
                  "{d.reasoning}"
                </div>
              </div>
            )}

            {d.parameters && Object.keys(d.parameters).length > 0 && (
              <div style={{ marginTop: 6 }}>
                <div style={{ fontSize: 8, color: '#1e3a5f', textTransform: 'uppercase', letterSpacing: 1, marginBottom: 3 }}>
                  Parameters
                </div>
                {Object.entries(d.parameters).map(([k, v]) => (
                  <div key={k} style={{ display: 'flex', gap: 8, fontSize: 9, marginBottom: 2 }}>
                    <span style={{ color: '#1e3a5f', minWidth: 80 }}>{k}:</span>
                    <span style={{ color: '#475569' }}>{JSON.stringify(v)}</span>
                  </div>
                ))}
              </div>
            )}

            {/* Control room actionable card */}
            {d.status !== 'no_action' && <ControlRoomCard d={d} />}

            {isPending && onApprove && onReject && (
              <div style={{ display: 'flex', gap: 8, marginTop: 10 }} onClick={e => e.stopPropagation()}>
                <button onClick={onApprove} style={{
                  flex: 1, padding: '6px 0', background: '#16a34a', color: 'white',
                  border: 'none', borderRadius: 4, cursor: 'pointer', fontSize: 11,
                  fontWeight: 700, fontFamily: 'monospace',
                }}>✅ APPROVE</button>
                <button onClick={onReject} style={{
                  flex: 1, padding: '6px 0', background: '#dc2626', color: 'white',
                  border: 'none', borderRadius: 4, cursor: 'pointer', fontSize: 11,
                  fontWeight: 700, fontFamily: 'monospace',
                }}>❌ REJECT</button>
              </div>
            )}
          </motion.div>
        )}
      </AnimatePresence>
    </motion.div>
  )
}

// Live AI deliberation stream panel
function DeliberationStream() {
  const nexusStream = useCityStore(s => s.nexusStream)
  const ref = useRef<HTMLDivElement>(null)

  useEffect(() => {
    if (ref.current) ref.current.scrollTop = ref.current.scrollHeight
  }, [nexusStream?.text])

  if (!nexusStream && !nexusStream) return null
  const isThinking = nexusStream?.thinking
  const isDone = nexusStream?.done
  const steps = nexusStream?.text ? parseDeliberation(nexusStream.text) : []

  return (
    <div style={{
      margin: '0 0 8px 0',
      background: '#020c18',
      border: '1px solid #0f2a40',
      borderLeft: '3px solid #3b82f6',
      borderRadius: 4,
      overflow: 'hidden',
    }}>
      <div style={{
        padding: '5px 8px', background: '#020d1c',
        display: 'flex', justifyContent: 'space-between', alignItems: 'center',
        borderBottom: '1px solid #0f2a40',
      }}>
        <span style={{ fontSize: 8, color: '#3b82f6', textTransform: 'uppercase', letterSpacing: 1 }}>
          {isThinking ? '⟳ AI DELIBERATING...' : isDone ? '✓ DELIBERATION COMPLETE' : '🤖 RAKSHAK AI STREAM'}
        </span>
        {isThinking && (
          <span style={{ fontSize: 8, color: '#1e3a5f' }}>Gemini 2.0 Flash</span>
        )}
      </div>
      <div ref={ref} style={{
        maxHeight: 200, overflowY: 'auto', padding: '6px 8px',
        fontFamily: 'monospace',
      }}>
        {steps.map((step, i) => (
          <div key={i} style={{ marginBottom: step.raw ? 2 : 5 }}>
            {step.label ? (
              <div style={{ display: 'flex', gap: 6, alignItems: 'flex-start' }}>
                <span style={{
                  fontSize: 8, color: STEP_COLORS[step.label] || '#475569',
                  fontWeight: 900, minWidth: 90, textTransform: 'uppercase', letterSpacing: 0.5,
                  paddingTop: 1,
                }}>▸ {step.label}</span>
                <span style={{
                  fontSize: 10, color: STEP_COLORS[step.label] || '#64748b',
                  lineHeight: 1.5, flex: 1,
                  fontWeight: step.label === 'DECISION' ? 700 : 400,
                }}>{step.content}</span>
              </div>
            ) : (
              <span style={{ fontSize: 9, color: '#1e3a5f' }}>{step.content}</span>
            )}
          </div>
        ))}
        {isThinking && (
          <span style={{ fontSize: 10, color: '#1e3a5f', animation: 'none' }}>▋</span>
        )}
      </div>
    </div>
  )
}

const SEV_COLORS: Record<string, string> = {
  critical: '#ef4444',
  high:     '#f97316',
  medium:   '#3b82f6',
  low:      '#6b7280',
}

function CctvIncidentLog() {
  const rakshakIncidents = useCityStore(s => s.rakshakIncidents)
  if (rakshakIncidents.length === 0) return null

  return (
    <div style={{ marginBottom: 8 }}>
      <div style={{ fontSize: 8, color: '#334155', textTransform: 'uppercase', letterSpacing: 1, marginBottom: 4 }}>
        📹 CCTV Detection Log ({rakshakIncidents.length})
      </div>
      {rakshakIncidents.slice(0, 20).map((inc, i) => {
        const color = SEV_COLORS[inc.severity] || '#6b7280'
        return (
          <div key={i} style={{
            background: '#020c18', border: `1px solid ${color}22`,
            borderLeft: `3px solid ${color}`, borderRadius: 3,
            padding: '5px 8px', marginBottom: 3,
          }}>
            <div style={{ display: 'flex', justifyContent: 'space-between', marginBottom: 2 }}>
              <span style={{ fontSize: 9, color, fontWeight: 700, textTransform: 'uppercase' }}>
                {(inc.incident_type || '').replace(/_/g, ' ')}
              </span>
              <span style={{ fontSize: 8, color: '#334155' }}>
                {new Date((inc.timestamp || 0) * 1000).toLocaleTimeString('en-IN', { hour12: false })}
              </span>
            </div>
            <div style={{ fontSize: 9, color: '#475569', lineHeight: 1.3 }}>
              {inc.description}
            </div>
            <div style={{ display: 'flex', gap: 8, marginTop: 3, fontSize: 8, color: '#1e3a5f' }}>
              <span>📷 {inc.camera_id}</span>
              <span>⚡ {Math.round((inc.confidence || 0) * 100)}%</span>
              {inc.zone_id && <span>📍 {inc.zone_id.replace(/_/g, ' ')}</span>}
            </div>
          </div>
        )
      })}
    </div>
  )
}

const DEPT_COLORS: Record<string, string> = {
  police:    '#3b82f6',
  ambulance: '#ef4444',
  ghmc:      '#22c55e',
}

function AlertLog() {
  const alertDispatches = useCityStore(s => s.alertDispatches)
  if (alertDispatches.length === 0) return null

  return (
    <div style={{ marginBottom: 8 }}>
      <div style={{ fontSize: 8, color: '#334155', textTransform: 'uppercase', letterSpacing: 1, marginBottom: 4 }}>
        📞 Emergency Alerts Sent
      </div>
      {alertDispatches.slice(0, 5).map((a, i) => (
        <div key={i} style={{
          background: '#020c18', border: '1px solid #0f2a40',
          borderLeft: '3px solid #ef4444', borderRadius: 3,
          padding: '6px 8px', marginBottom: 4,
        }}>
          <div style={{ display: 'flex', justifyContent: 'space-between', marginBottom: 4 }}>
            <span style={{ fontSize: 9, color: '#ef4444', fontWeight: 700, textTransform: 'uppercase' }}>
              {a.incident_type.replace(/_/g, ' ')}
            </span>
            <span style={{ fontSize: 8, color: '#334155' }}>
              {new Date(a.timestamp * 1000).toLocaleTimeString('en-IN', { hour12: false })}
            </span>
          </div>
          {a.alerts.map((dept, j) => (
            <div key={j} style={{
              display: 'flex', gap: 6, alignItems: 'center',
              padding: '3px 0', borderTop: j > 0 ? '1px solid #0f172a' : 'none',
            }}>
              <span style={{
                fontSize: 8, color: DEPT_COLORS[dept.dept] || '#64748b',
                fontWeight: 700, minWidth: 60, textTransform: 'uppercase',
              }}>
                {dept.dept}
              </span>
              <span style={{ fontSize: 8, color: '#475569', flex: 1 }}>
                {dept.to}
              </span>
              {dept.status === 'skipped' ? (
                <span style={{ fontSize: 8, color: '#475569' }}>⚠ {dept.reason}</span>
              ) : (
                <span style={{ fontSize: 8, display: 'flex', gap: 6 }}>
                  {dept.sms && (
                    <span style={{ color: dept.sms === 'sent' ? '#22c55e' : '#ef4444' }}>
                      {dept.sms === 'sent' ? '✓ SMS' : '✗ SMS'}
                    </span>
                  )}
                  {dept.call && (
                    <span style={{ color: dept.call === 'initiated' ? '#22c55e' : '#ef4444' }}>
                      {dept.call === 'initiated' ? '✓ CALL' : '✗ CALL'}
                    </span>
                  )}
                </span>
              )}
            </div>
          ))}
        </div>
      ))}
    </div>
  )
}

export default function DecisionFeed() {
  const decisions = useCityStore(s => s.decisions)
  const pendingApprovals = useCityStore(s => s.pendingApprovals)
  const snapshot = useCityStore(s => s.snapshot)
  const nexusStream = useCityStore(s => s.nexusStream)

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

  const criticalZones = snapshot
    ? Object.values(snapshot.zones).filter(z => z.status === 'critical' || z.status === 'warning')
    : []

  return (
    <div style={{ height: '100%', display: 'flex', flexDirection: 'column', background: '#020617', fontFamily: 'monospace' }}>
      {/* Header */}
      <div style={{ padding: '8px 10px', borderBottom: '1px solid #0f172a', background: '#020617', flexShrink: 0 }}>
        <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
          <span style={{ fontSize: 9, color: '#334155', textTransform: 'uppercase', letterSpacing: 1 }}>
            AI Governor · Rakshak
          </span>
          <span style={{ fontSize: 8, color: '#1e3a5f' }}>Gemini 2.0 Flash</span>
        </div>
        {pendingApprovals.length > 0 && (
          <div style={{
            marginTop: 4, background: '#3d0a0a', border: '1px solid #ef4444',
            borderRadius: 3, padding: '3px 8px', fontSize: 9, color: '#fca5a5',
          }}>
            ⚠ {pendingApprovals.length} decision{pendingApprovals.length > 1 ? 's' : ''} awaiting approval
          </div>
        )}
        {criticalZones.length > 0 && (
          <div style={{ marginTop: 3, fontSize: 9, color: '#334155' }}>
            Monitoring: {criticalZones.map(z => z.name.replace(' Underpass', '')).join(', ')}
          </div>
        )}
      </div>

      <div style={{ flex: 1, overflowY: 'auto', padding: '8px 8px' }}>
        {/* CCTV detection log from Rakshak */}
        <CctvIncidentLog />

        {/* Emergency alert dispatch log */}
        <AlertLog />

        {/* Live deliberation stream */}
        {nexusStream && <DeliberationStream />}

        {/* Pending approvals */}
        {pendingApprovals.length > 0 && (
          <div style={{ marginBottom: 10 }}>
            <div style={{ fontSize: 8, color: '#f97316', marginBottom: 5, textTransform: 'uppercase', letterSpacing: 1 }}>
              ⚠ Awaiting Human Approval
            </div>
            <AnimatePresence>
              {pendingApprovals.map(d => (
                <DecisionCard key={d.id} d={d}
                  onApprove={() => handleApprove(d.id)}
                  onReject={() => handleReject(d.id)} />
              ))}
            </AnimatePresence>
          </div>
        )}

        {/* Decision history */}
        <AnimatePresence>
          {decisions.filter(d => d.status !== 'pending_approval').slice(0, 30).map(d => (
            <DecisionCard key={d.id} d={d} />
          ))}
        </AnimatePresence>

        {decisions.length === 0 && !nexusStream && (
          <div style={{ color: '#0f2035', fontSize: 11, textAlign: 'center', marginTop: 40 }}>
            <div style={{ fontSize: 24, marginBottom: 8 }}>🤖</div>
            Rakshak AI is watching the city...
          </div>
        )}
      </div>
    </div>
  )
}
