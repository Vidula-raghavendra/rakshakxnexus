import { useEffect, useState } from 'react'
import { useCityStore } from '../store/cityStore'
import { motion } from 'framer-motion'

export default function StatusBar() {
  const snapshot = useCityStore(s => s.snapshot)
  const connected = useCityStore(s => s.connected)
  const scenario = useCityStore(s => s.scenario)
  const decisions = useCityStore(s => s.decisions)
  const rulesMode = useCityStore(s => s.rulesMode)
  const rulesEngineResult = useCityStore(s => s.rulesEngineResult)
  const [redTeamLoading, setRedTeamLoading] = useState(false)
  const [redTeamFired, setRedTeamFiredLocal] = useState(false)
  const [clock, setClock] = useState('')

  useEffect(() => {
    const tick = () => setClock(new Date().toLocaleTimeString('en-IN', { hour12: false }))
    tick()
    const id = setInterval(tick, 1000)
    return () => clearInterval(id)
  }, [])

  const criticalZones = snapshot
    ? Object.values(snapshot.zones as any).filter((z: any) => z.status === 'critical').length
    : 0
  const warningZones = snapshot
    ? Object.values(snapshot.zones as any).filter((z: any) => z.status === 'warning').length
    : 0
  const powerOutages = snapshot
    ? Object.values(snapshot.hospitals as any).filter((h: any) => !h.has_power).length
    : 0
  const pendingDecisions = decisions.filter(d => d.status === 'pending_approval').length
  const autoExecuted = decisions.filter(d => d.status === 'auto_executed').length

  const isFlood = scenario === 'flood_sept2024'

  // Threat level
  const threatLevel = criticalZones > 2 ? 'CRITICAL'
    : criticalZones > 0 || powerOutages > 0 ? 'HIGH'
    : warningZones > 0 ? 'ELEVATED'
    : 'NOMINAL'
  const threatColor = threatLevel === 'CRITICAL' ? '#ef4444'
    : threatLevel === 'HIGH' ? '#f97316'
    : threatLevel === 'ELEVATED' ? '#eab308'
    : '#22c55e'

  const handleScenario = async (sc: string) => {
    await fetch('/api/scenario', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ scenario: sc }),
    })
  }

  const handleRedTeam = async () => {
    setRedTeamLoading(true)
    await fetch('/api/redteam', { method: 'POST' })
    setRedTeamLoading(false)
    setRedTeamFiredLocal(true)
    setTimeout(() => setRedTeamFiredLocal(false), 8000)
  }

  const handleRulesToggle = async () => {
    await fetch('/api/rules-mode', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ enabled: !rulesMode }),
    })
  }

  return (
    <div style={{
      height: 44,
      background: '#020617',
      borderBottom: `2px solid ${isFlood ? '#7f1d1d' : '#0f172a'}`,
      display: 'flex', alignItems: 'center', padding: '0 14px', gap: 12,
      fontFamily: 'monospace', fontSize: 11, flexShrink: 0,
      transition: 'border-color 0.5s',
    }}>

      {/* Logo */}
      <div style={{ display: 'flex', alignItems: 'baseline', gap: 6 }}>
        <span style={{ color: '#3b82f6', fontWeight: 900, fontSize: 15, letterSpacing: 3 }}>NEXUS</span>
        <span style={{ color: '#1e293b', fontSize: 8, letterSpacing: 2, textTransform: 'uppercase' }}>City Governor</span>
      </div>

      {/* Divider */}
      <div style={{ width: 1, height: 20, background: '#0f172a' }} />

      {/* Connection */}
      <div style={{ display: 'flex', alignItems: 'center', gap: 5 }}>
        <motion.div
          animate={{ opacity: connected ? [1, 0.3, 1] : 1 }}
          transition={{ repeat: Infinity, duration: 1.5 }}
          style={{ width: 6, height: 6, borderRadius: '50%', background: connected ? '#22c55e' : '#ef4444' }}
        />
        <span style={{ fontSize: 9, color: connected ? '#22c55e' : '#ef4444', letterSpacing: 1 }}>
          {connected ? 'LIVE' : 'OFFLINE'}
        </span>
      </div>

      {/* Tick */}
      {snapshot && (
        <span style={{ color: '#1e293b', fontSize: 9 }}>T{snapshot.tick}</span>
      )}

      {/* Threat level */}
      <motion.div
        animate={threatLevel === 'CRITICAL' ? { opacity: [1, 0.6, 1] } : {}}
        transition={{ repeat: Infinity, duration: 1 }}
        style={{
          padding: '2px 8px', borderRadius: 3,
          background: `${threatColor}18`,
          border: `1px solid ${threatColor}44`,
          color: threatColor,
          fontSize: 9, fontWeight: 900, letterSpacing: 1, textTransform: 'uppercase',
        }}
      >
        ◈ {threatLevel}
      </motion.div>

      {/* Scenario badge */}
      <div style={{
        padding: '2px 8px', borderRadius: 3,
        background: isFlood ? '#7f1d1d' : '#0f172a',
        border: `1px solid ${isFlood ? '#ef4444' : '#1e293b'}`,
        color: isFlood ? '#fca5a5' : '#334155',
        fontSize: 9, fontWeight: 700, textTransform: 'uppercase', letterSpacing: 1,
      }}>
        {isFlood ? '🌊 SEPT 2024 FLOOD' : '● NORMAL OPS'}
      </div>

      {/* Alerts */}
      {criticalZones > 0 && (
        <motion.div
          animate={{ scale: [1, 1.04, 1] }}
          transition={{ repeat: Infinity, duration: 1 }}
          style={{
            padding: '2px 8px', borderRadius: 3,
            background: '#7f1d1d', border: '1px solid #ef4444',
            color: '#fca5a5', fontSize: 9, fontWeight: 700,
          }}
        >
          ⚠ {criticalZones} CRITICAL
        </motion.div>
      )}

      {powerOutages > 0 && (
        <div style={{
          padding: '2px 8px', borderRadius: 3,
          background: '#431407', border: '1px solid #f97316',
          color: '#fed7aa', fontSize: 9, fontWeight: 700,
        }}>
          ⚡ {powerOutages} NO POWER
        </div>
      )}

      {pendingDecisions > 0 && (
        <motion.div
          animate={{ opacity: [1, 0.5, 1] }}
          transition={{ repeat: Infinity, duration: 0.8 }}
          style={{
            padding: '2px 8px', borderRadius: 3,
            background: '#1e1407', border: '1px solid #eab308',
            color: '#fde68a', fontSize: 9, fontWeight: 700,
          }}
        >
          ⏳ {pendingDecisions} AWAITING APPROVAL
        </motion.div>
      )}

      <div style={{ flex: 1 }} />

      {/* Auto-executed count */}
      {autoExecuted > 0 && (
        <div style={{ fontSize: 9, color: '#22c55e33', letterSpacing: 1 }}>
          {autoExecuted} AUTO-EXEC
        </div>
      )}

      {/* Scenario buttons */}
      <button
        onClick={() => handleScenario('normal')}
        style={{
          padding: '3px 10px', borderRadius: 3, cursor: 'pointer', fontSize: 9,
          background: !isFlood ? '#0c1f3a' : '#0f172a',
          border: `1px solid ${!isFlood ? '#3b82f6' : '#1e293b'}`,
          color: !isFlood ? '#93c5fd' : '#334155',
          fontFamily: 'monospace', fontWeight: 700, letterSpacing: 1,
        }}
      >
        NORMAL DAY
      </button>

      {/* Rules vs NEXUS toggle */}
      <button
        onClick={handleRulesToggle}
        title={rulesMode ? 'Switch back to NEXUS multi-agent' : 'Show what a rules engine would do'}
        style={{
          padding: '3px 10px', borderRadius: 3, cursor: 'pointer', fontSize: 9,
          background: rulesMode ? '#1a0505' : '#0f172a',
          border: `1px solid ${rulesMode ? '#ef4444' : '#1e293b'}`,
          color: rulesMode ? '#fca5a5' : '#475569',
          fontFamily: 'monospace', fontWeight: 700, letterSpacing: 1,
        }}
      >
        {rulesMode ? '⚠ RULES ENGINE' : '⚖ NEXUS AI'}
      </button>

      {/* Rules Engine deadlock badge (shown when in rules mode and deadlocked) */}
      {rulesMode && rulesEngineResult && !rulesEngineResult.resolved && (
        <motion.div
          animate={{ opacity: [1, 0.5, 1] }}
          transition={{ repeat: Infinity, duration: 0.9 }}
          style={{
            padding: '2px 8px', borderRadius: 3,
            background: '#7f1d1d', border: '1px solid #ef4444',
            color: '#fca5a5', fontSize: 9, fontWeight: 700, letterSpacing: 0.5,
          }}
        >
          {rulesEngineResult.status}
        </motion.div>
      )}

      {/* Red Team button */}
      <motion.button
        onClick={handleRedTeam}
        disabled={redTeamLoading}
        animate={redTeamFired ? { scale: [1, 1.06, 1] } : {}}
        transition={{ duration: 0.3 }}
        style={{
          padding: '3px 12px', borderRadius: 3, cursor: redTeamLoading ? 'wait' : 'pointer',
          fontSize: 9, fontFamily: 'monospace', fontWeight: 900, letterSpacing: 1,
          background: redTeamFired ? '#7f1d1d' : '#1a0505',
          border: `1px solid ${redTeamFired ? '#ef4444' : '#7f1d1d'}`,
          color: redTeamFired ? '#ffffff' : '#ef4444',
          boxShadow: redTeamFired ? '0 0 8px #ef444488' : 'none',
          transition: 'all 0.2s',
        }}
      >
        {redTeamLoading ? '⚡ INJECTING...' : redTeamFired ? '🔴 RED TEAM ACTIVE' : '☢ RED TEAM'}
      </motion.button>

      {/* Rakshak link */}
      <a
        href="http://localhost:8001"
        target="_blank"
        rel="noopener noreferrer"
        style={{
          padding: '2px 8px', borderRadius: 3, textDecoration: 'none',
          background: '#0c1a0c', border: '1px solid #16a34a',
          color: '#86efac', fontSize: 8, fontWeight: 700, letterSpacing: 1,
        }}
      >
        📹 RAKSHAK ↗
      </a>

      {/* Clock */}
      <div style={{ color: '#334155', fontSize: 11, letterSpacing: 1, minWidth: 60, textAlign: 'right' }}>
        {clock}
      </div>
    </div>
  )
}
