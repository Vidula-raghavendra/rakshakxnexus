import { useEffect, useRef } from 'react'
import { motion, AnimatePresence } from 'framer-motion'
import { useCityStore } from '../store/cityStore'

const ACTION_ICONS: Record<string, string> = {
  reroute_traffic:                 '🚦',
  dispatch_resource:               '🚑',
  activate_backup_power:           '⚡',
  shed_substation_load:            '🔌',
  begin_evacuation:                '🚨',
  reposition_resources_preemptive: '📍',
  no_action:                       '💤',
}

// Colorize the streamed reasoning lines
function colorizeToken(text: string): React.ReactNode[] {
  const lines = text.split('\n')
  return lines.map((line, i) => {
    let color = '#475569'
    if (line.startsWith('> PERCEIVE:')) color = '#38bdf8'
    else if (line.startsWith('> RISK:')) color = '#f97316'
    else if (line.startsWith('> CONFLICT:')) color = '#a855f7'
    else if (line.startsWith('> DECISION:')) color = '#22c55e'
    else if (line.startsWith('> REQUIRES_APPROVAL:')) color = '#fbbf24'
    else if (line.startsWith('> HUMAN_READABLE:')) color = '#e2e8f0'
    return (
      <span key={i} style={{ color, display: 'block', lineHeight: 1.6 }}>
        {line || '\u00a0'}
      </span>
    )
  })
}

export default function AgentConsole() {
  const nexusStream = useCityStore(s => s.nexusStream)
  const decisions = useCityStore(s => s.decisions)
  const rulesMode = useCityStore(s => s.rulesMode)
  const rulesEngineResult = useCityStore(s => s.rulesEngineResult)
  const lastDecision = decisions[0]
  const scrollRef = useRef<HTMLDivElement>(null)

  // Auto-scroll as tokens arrive
  useEffect(() => {
    if (scrollRef.current) {
      scrollRef.current.scrollTop = scrollRef.current.scrollHeight
    }
  }, [nexusStream?.text])

  return (
    <div style={{
      background: '#020a14',
      borderTop: '1px solid #0f172a',
      display: 'flex',
      flexDirection: 'column',
      height: '100%',
      fontFamily: 'monospace',
      position: 'relative',
    }}>
      {/* Header */}
      <div style={{
        padding: '6px 12px',
        borderBottom: '1px solid #0f172a',
        display: 'flex',
        justifyContent: 'space-between',
        alignItems: 'center',
        flexShrink: 0,
      }}>
        <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
          <span style={{ fontSize: 9, color: '#334155', textTransform: 'uppercase', letterSpacing: 2 }}>
            NEXUS — Live Reasoning
          </span>
          {nexusStream?.thinking && (
            <motion.span
              animate={{ opacity: [1, 0.3, 1] }}
              transition={{ repeat: Infinity, duration: 0.7 }}
              style={{ fontSize: 9, color: '#38bdf8', letterSpacing: 1 }}
            >
              ● THINKING
            </motion.span>
          )}
          {nexusStream && !nexusStream.thinking && !nexusStream.done && (
            <motion.span
              animate={{ opacity: [1, 0.3, 1] }}
              transition={{ repeat: Infinity, duration: 0.5 }}
              style={{ fontSize: 9, color: '#22c55e', letterSpacing: 1 }}
            >
              ● STREAMING
            </motion.span>
          )}
        </div>
        {lastDecision && lastDecision.status !== 'no_action' && (
          <div style={{ display: 'flex', alignItems: 'center', gap: 6 }}>
            <span style={{ fontSize: 11 }}>{ACTION_ICONS[lastDecision.action_type] ?? '⚡'}</span>
            <span style={{
              fontSize: 9, fontWeight: 700, letterSpacing: 1,
              color: lastDecision.status === 'auto_executed' ? '#22c55e'
                   : lastDecision.status === 'pending_approval' ? '#fbbf24'
                   : '#94a3b8',
              textTransform: 'uppercase',
            }}>
              {lastDecision.action_type.replace(/_/g, ' ')}
            </span>
            <span style={{
              fontSize: 9,
              color: lastDecision.confidence >= 0.8 ? '#22c55e'
                   : lastDecision.confidence >= 0.6 ? '#f97316' : '#ef4444',
            }}>
              {(lastDecision.confidence * 100).toFixed(0)}%
            </span>
          </div>
        )}
      </div>

      {/* Stream body */}
      <div
        ref={scrollRef}
        style={{
          flex: 1,
          padding: '10px 14px',
          overflowY: 'auto',
          fontSize: 11,
          lineHeight: 1.6,
          scrollbarWidth: 'none',
        }}
      >
        {nexusStream ? (
          <div>
            {colorizeToken(nexusStream.text)}
            {/* Blinking cursor while streaming */}
            {!nexusStream.done && (
              <motion.span
                animate={{ opacity: [1, 0, 1] }}
                transition={{ repeat: Infinity, duration: 0.8 }}
                style={{ color: '#22c55e', fontSize: 13 }}
              >▋</motion.span>
            )}
          </div>
        ) : (
          <div style={{ color: '#1e293b', fontSize: 10, paddingTop: 4 }}>
            Awaiting next governance cycle...
          </div>
        )}
      </div>

      {/* Last decision summary bar */}
      {lastDecision && lastDecision.human_readable && (
        <div style={{
          padding: '5px 12px',
          borderTop: '1px solid #0f172a',
          fontSize: 10,
          color: '#475569',
          flexShrink: 0,
          display: 'flex',
          alignItems: 'center',
          gap: 6,
        }}>
          <span style={{ color: '#1e293b', fontSize: 9, letterSpacing: 1, textTransform: 'uppercase', flexShrink: 0 }}>
            LAST ACTION:
          </span>
          <span style={{ flex: 1, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
            {lastDecision.human_readable}
          </span>
          {lastDecision.status === 'pending_approval' && (
            <motion.span
              animate={{ opacity: [1, 0.4, 1] }}
              transition={{ repeat: Infinity, duration: 1 }}
              style={{ fontSize: 9, color: '#fbbf24', flexShrink: 0 }}
            >
              ⏳ AWAITING APPROVAL
            </motion.span>
          )}
        </div>
      )}

      {/* Rules Engine overlay */}
      <AnimatePresence>
        {rulesMode && (
          <motion.div
            initial={{ opacity: 0 }}
            animate={{ opacity: 1 }}
            exit={{ opacity: 0 }}
            style={{
              position: 'absolute', inset: 0,
              background: 'rgba(2, 6, 23, 0.93)',
              display: 'flex', flexDirection: 'column',
              alignItems: 'center', justifyContent: 'center',
              gap: 10, padding: 16,
              border: '1px solid #ef444433',
            }}
          >
            <div style={{ fontSize: 11, color: '#334155', letterSpacing: 2, textTransform: 'uppercase' }}>
              RULES ENGINE MODE
            </div>
            {rulesEngineResult ? (
              <>
                <motion.div
                  animate={!rulesEngineResult.resolved ? { opacity: [1, 0.4, 1] } : {}}
                  transition={{ repeat: Infinity, duration: 0.9 }}
                  style={{
                    fontSize: 22, fontWeight: 900, letterSpacing: 2,
                    color: rulesEngineResult.resolved ? '#22c55e' : '#ef4444',
                    textAlign: 'center',
                  }}
                >
                  {rulesEngineResult.status}
                </motion.div>
                <div style={{ fontSize: 11, color: '#475569', textAlign: 'center', maxWidth: 500, lineHeight: 1.5 }}>
                  {rulesEngineResult.reason}
                </div>
              </>
            ) : (
              <div style={{ fontSize: 13, color: '#334155' }}>Waiting for alerts...</div>
            )}
            <div style={{ fontSize: 9, color: '#1e293b', marginTop: 8 }}>
              Toggle ⚖ NEXUS AI in the top bar to see real-time reasoning
            </div>
          </motion.div>
        )}
      </AnimatePresence>
    </div>
  )
}
