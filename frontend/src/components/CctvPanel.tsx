import { useState, useEffect } from 'react'
import { motion, AnimatePresence } from 'framer-motion'
import { useCityStore } from '../store/cityStore'

const CAMERAS = [
  { id: 'cam_meh_001', label: 'Mehdipatnam UP', sub: 'Entry', zone: 'mehdipatnam_up' },
  { id: 'cam_tol_001', label: 'Tolichowki UP',  sub: 'Entry', zone: 'tolichowki_up' },
  { id: 'cam_nar_001', label: 'Narayanguda UP', sub: '',      zone: 'narayanguda_up' },
  { id: 'cam_mal_001', label: 'Malakpet UP',    sub: '',      zone: 'malakpet_up' },
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
  vehicle_stranded:     '🚘 VEHICLE STRANDED',
  crowd_surge:          '👥 CROWD SURGE',
  road_blocked:         '🚧 ROAD BLOCKED',
  women_safety:         '🚨 SAFETY ALERT',
  fight_violence:       '⚠ VIOLENCE',
  garbage_dumping:      '🗑 GARBAGE DUMPING',
  animal_on_road:       '🐄 ANIMAL ON ROAD',
  traffic_signal_issue: '🚦 SIGNAL ISSUE',
  abandoned_object:     '💼 ABANDONED OBJECT',
  building_damage:      '🏚 BUILDING DAMAGE',
}

function CameraSlot({ cam, snapshot }: { cam: typeof CAMERAS[0]; snapshot: any }) {
  const [feedOk, setFeedOk] = useState(false)
  const [ts, setTs] = useState('')
  const [pollSrc, setPollSrc] = useState('')
  const mapAlerts = useCityStore(s => s.mapAlerts)

  // Check if this camera has a recent alert
  const recentAlert = mapAlerts.find(a =>
    a.details?.camera === cam.id && (Date.now() / 1000 - a.timestamp) < 15
  )

  const zone = snapshot?.zones?.[cam.zone]
  const isFlooded = zone?.is_flooded
  const isCritical = zone?.status === 'critical'
  const borderColor = recentAlert
    ? SEVERITY_COLORS[recentAlert.severity] || '#ef4444'
    : isCritical ? '#ef4444'
    : isFlooded ? '#f97316'
    : feedOk ? '#22c55e33'
    : '#0f172a'

  useEffect(() => {
    const interval = setInterval(() => {
      setTs(new Date().toLocaleTimeString('en-IN', { hour12: false }))
    }, 1000)
    return () => clearInterval(interval)
  }, [])

  // Poll the latest.jpg snapshot to detect feed liveness and show frames.
  // MJPEG onLoad is unreliable in browsers; polling JPEG is more robust.
  useEffect(() => {
    let alive = true
    const poll = () => {
      if (!alive) return
      const src = `http://localhost:8001/frames/${cam.id}/latest.jpg?t=${Date.now()}`
      const img = new Image()
      img.onload = () => {
        if (!alive) return
        setFeedOk(true)
        setPollSrc(src)
        setTimeout(poll, 500)
      }
      img.onerror = () => {
        if (!alive) return
        setFeedOk(false)
        setTimeout(poll, 2000)
      }
      img.src = src
    }
    poll()
    return () => { alive = false }
  }, [cam.id])

  return (
    <div style={{
      position: 'relative',
      background: '#040c14',
      border: `1px solid ${borderColor}`,
      borderRadius: 2,
      overflow: 'hidden',
      transition: 'border-color 0.3s',
      animation: (recentAlert?.severity === 'critical' || isCritical) ? 'camFlash 1s ease-in-out infinite' : 'none',
    }}>
      {/* Polled JPEG feed */}
      {feedOk && pollSrc && (
        <img
          src={pollSrc}
          style={{
            width: '100%', height: '100%', objectFit: 'cover',
            position: 'absolute', inset: 0,
          }}
        />
      )}

      {/* No signal placeholder */}
      {!feedOk && (
        <div style={{
          width: '100%', height: '100%',
          display: 'flex', flexDirection: 'column',
          alignItems: 'center', justifyContent: 'center',
          background: 'repeating-linear-gradient(0deg, #050a14 0px, #050a14 2px, #040c14 2px, #040c14 4px)',
        }}>
          <div style={{ fontSize: 16, opacity: 0.2, marginBottom: 4 }}>📡</div>
          <div style={{ fontSize: 8, color: '#1e293b', letterSpacing: 2, textTransform: 'uppercase' }}>No Signal</div>
        </div>
      )}

      {/* Scanline overlay */}
      <div style={{
        position: 'absolute', inset: 0, pointerEvents: 'none',
        background: 'repeating-linear-gradient(0deg, transparent, transparent 3px, rgba(0,0,0,0.08) 3px, rgba(0,0,0,0.08) 4px)',
      }} />

      {/* Live dot */}
      {feedOk && (
        <div style={{
          position: 'absolute', top: 5, left: 5,
          width: 5, height: 5, borderRadius: '50%', background: '#ef4444',
        }} />
      )}

      {/* Timestamp */}
      {feedOk && (
        <div style={{
          position: 'absolute', top: 4, left: 14,
          fontSize: 8, color: '#64748b',
          background: 'rgba(0,0,0,0.7)', padding: '1px 4px', borderRadius: 1,
        }}>
          {ts}
        </div>
      )}

      {/* Camera ID top-right */}
      <div style={{
        position: 'absolute', top: 4, right: 4,
        fontSize: 8, color: '#334155',
        background: 'rgba(0,0,0,0.7)', padding: '1px 4px', borderRadius: 1,
        letterSpacing: 1,
      }}>
        {cam.id.toUpperCase().replace('CAM_', '').replace('_', '-')}
      </div>

      {/* Incident overlay */}
      <AnimatePresence>
        {recentAlert && (
          <motion.div
            initial={{ opacity: 0, y: -4 }}
            animate={{ opacity: 1, y: 0 }}
            exit={{ opacity: 0 }}
            style={{
              position: 'absolute', top: 18, left: 0, right: 0,
              display: 'flex', justifyContent: 'center',
            }}
          >
            <div style={{
              padding: '1px 6px', borderRadius: 2, fontSize: 8, fontWeight: 900,
              letterSpacing: 1, textTransform: 'uppercase',
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
        background: 'linear-gradient(transparent, rgba(0,0,0,0.9))',
        padding: '12px 6px 4px',
      }}>
        <div style={{ fontSize: 8, fontWeight: 700, color: '#e2e8f0', letterSpacing: 1, textTransform: 'uppercase' }}>
          {cam.label}
        </div>
        {cam.sub && <div style={{ fontSize: 7, color: '#475569' }}>{cam.sub}</div>}
        {zone && (
          <div style={{
            fontSize: 7, color: isCritical ? '#ef4444' : isFlooded ? '#f97316' : '#334155',
            fontWeight: isCritical ? 700 : 400,
          }}>
            {zone.rainfall_mm_per_hour.toFixed(0)} mm/hr {isCritical ? '· CRITICAL' : isFlooded ? '· FLOODED' : ''}
          </div>
        )}
      </div>
    </div>
  )
}

export default function CctvPanel() {
  const snapshot = useCityStore(s => s.snapshot)

  return (
    <div style={{
      height: '100%',
      background: '#020617',
      display: 'flex',
      flexDirection: 'column',
    }}>
      <div style={{
        padding: '5px 10px',
        borderBottom: '1px solid #0f172a',
        display: 'flex',
        justifyContent: 'space-between',
        alignItems: 'center',
        flexShrink: 0,
      }}>
        <span style={{ fontSize: 9, color: '#334155', textTransform: 'uppercase', letterSpacing: 2 }}>
          CCTV · Live Feeds
        </span>
        <a
          href="http://localhost:8001"
          target="_blank"
          rel="noopener noreferrer"
          style={{
            fontSize: 8, color: '#1e3a5f', textDecoration: 'none',
            letterSpacing: 1, textTransform: 'uppercase',
          }}
        >
          FULL VIEW ↗
        </a>
      </div>

      <div style={{
        flex: 1,
        display: 'grid',
        gridTemplateColumns: '1fr 1fr',
        gridTemplateRows: '1fr 1fr',
        gap: 2,
        padding: 2,
        background: '#000',
        minHeight: 0,
      }}>
        {CAMERAS.map(cam => (
          <CameraSlot key={cam.id} cam={cam} snapshot={snapshot} />
        ))}
      </div>

      <style>{`
        @keyframes camFlash {
          0%, 100% { border-color: #ef4444; }
          50% { border-color: #7f1d1d; }
        }
      `}</style>
    </div>
  )
}
