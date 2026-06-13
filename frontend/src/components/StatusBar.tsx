import { useEffect, useState, memo, useCallback } from 'react'
import { useCityStore } from '../store/cityStore'
import { motion } from 'framer-motion'

const API_KEY = import.meta.env.VITE_API_KEY ?? ''
const RAKSHAK_KEY = 'rakshak_2026_a9XkP7mN4vQ2sL8dF5wR1zC6'

function useMuteState() {
  const [muted, setMuted] = useState(false)
  const [loading, setLoading] = useState(false)

  // Sync from server on mount
  useEffect(() => {
    fetch('http://localhost:8001/alerts/status', { headers: { 'X-API-Key': RAKSHAK_KEY } })
      .then(r => r.json()).then(d => setMuted(d.muted)).catch(() => {})
  }, [])

  const toggle = useCallback(async () => {
    setLoading(true)
    const endpoint = muted ? '/alerts/unmute' : '/alerts/mute'
    try {
      await fetch(`http://localhost:8001${endpoint}`, {
        method: 'POST', headers: { 'X-API-Key': RAKSHAK_KEY },
      })
      setMuted(m => !m)
    } finally {
      setLoading(false)
    }
  }, [muted])

  return { muted, loading, toggle }
}

const Clock = memo(function Clock() {
  const [clock, setClock] = useState(() => new Date().toLocaleTimeString('en-IN', { hour12: false }))
  useEffect(() => {
    const id = setInterval(() => setClock(new Date().toLocaleTimeString('en-IN', { hour12: false })), 1000)
    return () => clearInterval(id)
  }, [])
  return (
    <div style={{
      padding: '5px 12px', borderRadius: 8,
      background: 'rgba(255,255,255,0.6)', border: '1px solid rgba(0,0,0,0.06)',
      fontSize: 12, fontWeight: 600, color: '#475569', letterSpacing: 1, minWidth: 70, textAlign: 'center',
    }}>{clock}</div>
  )
})

export default function StatusBar() {
  const snapshot    = useCityStore(s => s.snapshot)
  const connected   = useCityStore(s => s.connected)
  const decisions   = useCityStore(s => s.decisions)
  const rakshakIncidents = useCityStore(s => s.rakshakIncidents)
  const { muted, loading, toggle } = useMuteState()

  const criticalZones = snapshot
    ? Object.values(snapshot.zones as any).filter((z: any) => z.status === 'critical').length : 0
  const activeIncidents = rakshakIncidents.filter(i => Date.now() / 1000 - i.timestamp < 300).length
  const pendingDecisions = decisions.filter(d => d.status === 'pending_approval').length

  const handleScenario = async (sc: string) => {
    await fetch('/api/scenario', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json', 'X-API-Key': API_KEY },
      body: JSON.stringify({ scenario: sc }),
    })
  }

  return (
    <div style={{
      height: 52,
      background: 'rgba(255,255,255,0.7)',
      backdropFilter: 'blur(20px)',
      WebkitBackdropFilter: 'blur(20px)',
      borderBottom: '1px solid rgba(255,255,255,0.9)',
      boxShadow: '0 1px 12px rgba(100,130,200,0.08)',
      display: 'flex', alignItems: 'center', padding: '0 18px', gap: 14,
      flexShrink: 0, zIndex: 10,
    }}>

      {/* Logo */}
      <div style={{ display: 'flex', alignItems: 'center', gap: 9 }}>
        <svg width="32" height="32" viewBox="0 0 200 220" fill="none" xmlns="http://www.w3.org/2000/svg">
          <path d="M100 8L180 45V110C180 155 145 192 100 210C55 192 20 155 20 110V45L100 8Z" fill="none" stroke="#b8a577" strokeWidth="8" strokeLinejoin="round"/>
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
        <div>
          <div style={{ fontSize: 13, fontWeight: 700, color: '#1e293b', letterSpacing: 0.5 }}>Rakshak</div>
          <div style={{ fontSize: 9, color: '#94a3b8', letterSpacing: 0.5 }}>AI-Powered Command & Control</div>
        </div>
      </div>

      <div style={{ width: 1, height: 28, background: 'rgba(0,0,0,0.06)' }} />

      {/* Live status */}
      <div style={{ display: 'flex', alignItems: 'center', gap: 6 }}>
        <motion.div
          animate={{ opacity: connected ? [1, 0.3, 1] : 1 }}
          transition={{ repeat: Infinity, duration: 1.5 }}
          style={{ width: 7, height: 7, borderRadius: '50%', background: connected ? '#22c55e' : '#ef4444' }}
        />
        <span style={{ fontSize: 11, fontWeight: 600, color: connected ? '#16a34a' : '#dc2626' }}>
          {connected ? 'LIVE' : 'OFFLINE'}
        </span>
      </div>

      {/* Stat chips */}
      {activeIncidents > 0 && (
        <motion.div animate={{ scale: [1, 1.03, 1] }} transition={{ repeat: Infinity, duration: 1.5 }}
          style={{
            padding: '4px 10px', borderRadius: 20,
            background: 'rgba(239,68,68,0.1)', border: '1px solid rgba(239,68,68,0.25)',
            fontSize: 11, fontWeight: 600, color: '#dc2626', display: 'flex', alignItems: 'center', gap: 5,
          }}>
          <span>⚠</span> {activeIncidents} Active Incident{activeIncidents > 1 ? 's' : ''}
        </motion.div>
      )}

      {criticalZones > 0 && (
        <div style={{
          padding: '4px 10px', borderRadius: 20,
          background: 'rgba(249,115,22,0.1)', border: '1px solid rgba(249,115,22,0.25)',
          fontSize: 11, fontWeight: 600, color: '#ea580c',
        }}>
          🔴 {criticalZones} Critical Zone{criticalZones > 1 ? 's' : ''}
        </div>
      )}

      {pendingDecisions > 0 && (
        <motion.div animate={{ opacity: [1, 0.6, 1] }} transition={{ repeat: Infinity, duration: 0.8 }}
          style={{
            padding: '4px 10px', borderRadius: 20,
            background: 'rgba(234,179,8,0.12)', border: '1px solid rgba(234,179,8,0.3)',
            fontSize: 11, fontWeight: 600, color: '#ca8a04',
          }}>
          ⏳ {pendingDecisions} Awaiting Approval
        </motion.div>
      )}

      <div style={{ flex: 1 }} />

      {/* Scenario controls */}
      <button onClick={() => handleScenario('normal')} style={{
        padding: '5px 12px', borderRadius: 8, cursor: 'pointer', fontSize: 11, fontWeight: 500,
        background: 'rgba(255,255,255,0.7)', border: '1px solid rgba(0,0,0,0.08)',
        color: '#64748b', backdropFilter: 'blur(10px)',
      }}>Normal Day</button>

      <button onClick={() => handleScenario('flood_sept2024')} style={{
        padding: '5px 12px', borderRadius: 8, cursor: 'pointer', fontSize: 11, fontWeight: 600,
        background: 'rgba(59,130,246,0.1)', border: '1px solid rgba(59,130,246,0.25)',
        color: '#2563eb', backdropFilter: 'blur(10px)',
      }}>🌊 Flood Scenario</button>

      <a href="http://localhost:8001" target="_blank" rel="noopener noreferrer" style={{
        padding: '5px 12px', borderRadius: 8, fontSize: 11, fontWeight: 600, textDecoration: 'none',
        background: 'rgba(34,197,94,0.1)', border: '1px solid rgba(34,197,94,0.25)', color: '#16a34a',
      }}>📹 Rakshak ↗</a>

      {/* Alert mute toggle */}
      <button
        onClick={toggle}
        disabled={loading}
        title={muted ? 'Alerts muted — click to unmute' : 'Alerts active — click to mute calls'}
        style={{
          padding: '5px 12px', borderRadius: 8, cursor: loading ? 'wait' : 'pointer',
          fontSize: 11, fontWeight: 600, display: 'flex', alignItems: 'center', gap: 5,
          background: muted ? 'rgba(239,68,68,0.1)' : 'rgba(255,255,255,0.7)',
          border: muted ? '1px solid rgba(239,68,68,0.3)' : '1px solid rgba(0,0,0,0.08)',
          color: muted ? '#dc2626' : '#64748b',
          opacity: loading ? 0.6 : 1,
          transition: 'all 0.2s',
        }}>
        {muted ? '🔕 Calls Muted' : '🔔 Calls On'}
      </button>

      <Clock />
    </div>
  )
}
