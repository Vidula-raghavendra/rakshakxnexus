import { useState, useEffect } from 'react'
import { AnimatePresence, motion } from 'framer-motion'
import { useCityStore } from '../store/cityStore'

const CAMERAS = [
  { id: 'cam_meh_001', label: 'Mehdipatnam', sub: 'UP Entry', zone: 'mehdipatnam_up' },
  { id: 'cam_tol_001', label: 'Tolichowki',  sub: 'UP Entry', zone: 'tolichowki_up' },
  { id: 'cam_nar_001', label: 'Narayanguda', sub: 'UP',       zone: 'narayanguda_up' },
  { id: 'cam_mal_001', label: 'Malakpet',    sub: 'UP',       zone: 'malakpet_up' },
]

const SEVERITY_COLORS: Record<string, string> = {
  critical: '#ef4444',
  high:     '#f97316',
  medium:   '#eab308',
  low:      '#3b82f6',
}

const INCIDENT_LABELS: Record<string, string> = {
  road_accident:        '🚗 ROAD ACCIDENT',
  road_flood:           '🌊 ROAD FLOOD',
  vehicle_stranded:     '🚘 STRANDED',
  crowd_surge:          '👥 CROWD SURGE',
  road_blocked:         '🚧 ROAD BLOCKED',
  women_safety:         '🚨 SAFETY ALERT',
  fight_violence:       '⚠ VIOLENCE',
  garbage_dumping:      '🗑 GARBAGE',
  animal_on_road:       '🐄 ANIMAL',
  traffic_signal_issue: '🚦 SIGNAL ISSUE',
  abandoned_object:     '💼 ABANDONED OBJ',
  building_damage:      '🏚 BLDG DAMAGE',
}

function CameraSlot({ cam, snapshot }: { cam: typeof CAMERAS[0]; snapshot: any }) {
  const [feedOk, setFeedOk] = useState(false)
  const [ts, setTs] = useState('')
  const [pollSrc, setPollSrc] = useState('')
  const mapAlerts = useCityStore(s => s.mapAlerts)

  const recentAlert = mapAlerts.find(a =>
    a.details?.camera === cam.id && (Date.now() / 1000 - a.timestamp) < 15
  )

  const zone = snapshot?.zones?.[cam.zone]
  const isFlooded = zone?.is_flooded
  const isCritical = zone?.status === 'critical'

  const alertColor = recentAlert ? (SEVERITY_COLORS[recentAlert.severity] || '#ef4444')
    : isCritical ? '#ef4444'
    : isFlooded ? '#f97316'
    : null

  useEffect(() => {
    const id = setInterval(() => setTs(new Date().toLocaleTimeString('en-IN', { hour12: false })), 1000)
    return () => clearInterval(id)
  }, [])

  useEffect(() => {
    let alive = true
    const poll = () => {
      if (!alive) return
      const src = `http://localhost:8001/frames/${cam.id}/latest.jpg?t=${Date.now()}`
      const img = new Image()
      img.onload = () => { if (!alive) return; setFeedOk(true); setPollSrc(src); setTimeout(poll, 1200) }
      img.onerror = () => { if (!alive) return; setFeedOk(false); setTimeout(poll, 3000) }
      img.src = src
    }
    poll()
    return () => { alive = false }
  }, [cam.id])

  return (
    <div style={{
      position: 'relative', overflow: 'hidden', borderRadius: 8,
      background: feedOk ? '#000' : 'rgba(15,23,42,0.85)',
      border: `1.5px solid ${alertColor ? alertColor + '80' : 'rgba(255,255,255,0.15)'}`,
      boxShadow: alertColor ? `0 0 10px ${alertColor}30` : 'none',
      transition: 'border-color 0.3s, box-shadow 0.3s',
      animation: (recentAlert?.severity === 'critical' || isCritical) ? 'camFlash 1s ease-in-out infinite' : 'none',
    }}>
      {feedOk && pollSrc && (
        <img src={pollSrc} style={{ width: '100%', height: '100%', objectFit: 'cover', position: 'absolute', inset: 0 }} />
      )}

      {!feedOk && (
        <div style={{
          width: '100%', height: '100%',
          display: 'flex', flexDirection: 'column', alignItems: 'center', justifyContent: 'center',
          background: 'rgba(15,23,42,0.9)',
        }}>
          <div style={{ fontSize: 18, marginBottom: 3, opacity: 0.3 }}>📡</div>
          <div style={{ fontSize: 8, color: '#475569', letterSpacing: 2 }}>NO SIGNAL</div>
        </div>
      )}

      {/* Scanline */}
      <div style={{ position: 'absolute', inset: 0, pointerEvents: 'none',
        background: 'repeating-linear-gradient(0deg, transparent, transparent 3px, rgba(0,0,0,0.06) 3px, rgba(0,0,0,0.06) 4px)' }} />

      {/* Top HUD */}
      <div style={{ position: 'absolute', top: 4, left: 5, right: 5, display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
        {feedOk && (
          <div style={{ display: 'flex', alignItems: 'center', gap: 4 }}>
            <div style={{ width: 5, height: 5, borderRadius: '50%', background: '#ef4444' }} />
            <span style={{ fontSize: 7, color: 'rgba(255,255,255,0.5)', letterSpacing: 1 }}>{ts}</span>
          </div>
        )}
        <span style={{ fontSize: 7, color: 'rgba(255,255,255,0.35)', letterSpacing: 1, marginLeft: 'auto' }}>
          {cam.id.toUpperCase().replace('CAM_', '').replace('_', '-')}
        </span>
      </div>

      {/* Incident badge */}
      <AnimatePresence>
        {recentAlert && (
          <motion.div
            initial={{ opacity: 0, y: -4 }} animate={{ opacity: 1, y: 0 }} exit={{ opacity: 0 }}
            style={{ position: 'absolute', top: 16, left: 0, right: 0, display: 'flex', justifyContent: 'center' }}>
            <div style={{
              padding: '1px 6px', borderRadius: 3, fontSize: 8, fontWeight: 800,
              letterSpacing: 0.5, textTransform: 'uppercase',
              background: `${SEVERITY_COLORS[recentAlert.severity]}22`,
              border: `1px solid ${SEVERITY_COLORS[recentAlert.severity]}`,
              color: SEVERITY_COLORS[recentAlert.severity],
            }}>
              {INCIDENT_LABELS[recentAlert.details?.incident_type as string] || recentAlert.title.replace('📹 CCTV: ', '')}
            </div>
          </motion.div>
        )}
      </AnimatePresence>

      {/* Bottom label */}
      <div style={{
        position: 'absolute', bottom: 0, left: 0, right: 0,
        background: 'linear-gradient(transparent, rgba(0,0,0,0.85))',
        padding: '10px 6px 4px',
      }}>
        <div style={{ fontSize: 8, fontWeight: 700, color: 'rgba(255,255,255,0.85)', letterSpacing: 0.5 }}>{cam.label}</div>
        <div style={{ fontSize: 7, color: alertColor ? alertColor : '#64748b' }}>
          {isCritical ? 'CRITICAL' : isFlooded ? 'FLOODED' : cam.sub}
          {zone ? ` · ${zone.rainfall_mm_per_hour.toFixed(0)}mm/hr` : ''}
        </div>
      </div>
    </div>
  )
}

export default function CctvPanel() {
  const snapshot = useCityStore(s => s.snapshot)

  return (
    <div style={{ height: '100%', display: 'flex', flexDirection: 'column', overflow: 'hidden' }}>
      <div style={{
        padding: '10px 12px 8px', borderBottom: '1px solid rgba(0,0,0,0.06)',
        display: 'flex', justifyContent: 'space-between', alignItems: 'center', flexShrink: 0,
      }}>
        <span style={{ fontSize: 12, fontWeight: 700, color: '#1e293b' }}>CCTV Feeds</span>
        <a href="http://localhost:8001" target="_blank" rel="noopener noreferrer"
          style={{ fontSize: 10, color: '#3b82f6', textDecoration: 'none', fontWeight: 500 }}>
          Full View ↗
        </a>
      </div>

      <div style={{
        flex: 1, display: 'grid', gridTemplateColumns: '1fr 1fr', gridTemplateRows: '1fr 1fr',
        gap: 5, padding: 8, minHeight: 0,
      }}>
        {CAMERAS.map(cam => <CameraSlot key={cam.id} cam={cam} snapshot={snapshot} />)}
      </div>
    </div>
  )
}
