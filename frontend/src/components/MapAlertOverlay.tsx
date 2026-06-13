import { useEffect } from 'react'
import { motion, AnimatePresence } from 'framer-motion'
import { useCityStore, MapAlert } from '../store/cityStore'

const INC_EMOJI: Record<string, string> = {
  road_accident: '🚗', road_flood: '🌊', vehicle_stranded: '🚘',
  crowd_surge: '👥', road_blocked: '🚧', women_safety: '🚨',
  fight_violence: '⚠️', traffic_signal_issue: '🚦',
  garbage_dumping: '🗑', animal_on_road: '🐄',
}

function AlertCard({ alert, onDismiss }: { alert: MapAlert; onDismiss: () => void }) {
  useEffect(() => {
    const ttl = alert.severity === 'critical' || alert.severity === 'high' ? 25000 : 15000
    const t = setTimeout(onDismiss, ttl)
    return () => clearTimeout(t)
  }, [alert.id])

  const incType = alert.details?.incident_type as string | undefined
  const emoji = INC_EMOJI[incType || ''] || '📹'
  const typeLabel = (incType || alert.title.replace('📹 CCTV: ', ''))
    .replace(/_/g, ' ').replace(/\b\w/g, c => c.toUpperCase())
  const camShort = ((alert.details?.camera as string) || '').replace('cam_', '').toUpperCase().replace('_', '-')
  const conf = alert.details?.confidence as string | undefined

  return (
    <motion.div
      layout
      initial={{ opacity: 0, y: -12, scale: 0.95 }}
      animate={{ opacity: 1, y: 0, scale: 1 }}
      exit={{ opacity: 0, y: -8, scale: 0.93 }}
      transition={{ duration: 0.22 }}
      style={{
        background: 'rgba(255,255,255,0.72)',
        backdropFilter: 'blur(22px)',
        WebkitBackdropFilter: 'blur(22px)',
        border: '1px solid rgba(255,255,255,0.85)',
        borderLeft: '3px solid rgba(59,130,246,0.5)',
        borderRadius: 14,
        padding: '12px 14px',
        marginBottom: 8,
        maxWidth: 300,
        boxShadow: '0 4px 24px rgba(100,130,200,0.14), inset 0 1px 0 rgba(255,255,255,0.9)',
      }}
    >
      <div style={{ display: 'flex', alignItems: 'flex-start', gap: 10 }}>
        <span style={{ fontSize: 20, lineHeight: 1, flexShrink: 0 }}>{emoji}</span>
        <div style={{ flex: 1, minWidth: 0 }}>
          <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'flex-start', gap: 6, marginBottom: 3 }}>
            <span style={{ fontSize: 12, fontWeight: 700, color: '#1e293b', lineHeight: 1.3 }}>{typeLabel}</span>
            <span style={{ fontSize: 9, color: '#94a3b8', whiteSpace: 'nowrap', flexShrink: 0 }}>
              {new Date(alert.timestamp * 1000).toLocaleTimeString('en-IN', { hour12: false })}
            </span>
          </div>
          <div style={{ fontSize: 10, color: '#64748b', lineHeight: 1.45, marginBottom: 8 }}>
            {alert.description}
          </div>
          <div style={{ display: 'flex', gap: 5, flexWrap: 'wrap', marginBottom: 8 }}>
            {camShort && (
              <span style={{ fontSize: 9, padding: '2px 7px', borderRadius: 6, background: 'rgba(0,0,0,0.05)', color: '#64748b' }}>
                📷 {camShort}
              </span>
            )}
            {conf && (
              <span style={{ fontSize: 9, padding: '2px 7px', borderRadius: 6, background: 'rgba(0,0,0,0.05)', color: '#64748b' }}>
                ⚡ {conf}
              </span>
            )}
          </div>
          <div style={{ display: 'flex', gap: 6 }}>
            <button onClick={onDismiss} style={{
              flex: 1, padding: '6px 0', borderRadius: 8, cursor: 'pointer',
              background: 'rgba(34,197,94,0.1)', border: '1px solid rgba(34,197,94,0.25)',
              color: '#16a34a', fontSize: 11, fontWeight: 600,
            }}>✓ Resolved</button>
            <button onClick={onDismiss} style={{
              flex: 1, padding: '6px 0', borderRadius: 8, cursor: 'pointer',
              background: 'rgba(255,255,255,0.6)', border: '1px solid rgba(0,0,0,0.08)',
              color: '#94a3b8', fontSize: 11, fontWeight: 500,
            }}>Dismiss</button>
          </div>
        </div>
      </div>
    </motion.div>
  )
}

export default function MapAlertOverlay() {
  const mapAlerts = useCityStore(s => s.mapAlerts)
  const dismissAlert = useCityStore(s => s.dismissAlert)

  // Only show the single most recent alert
  const latest = mapAlerts[0]
  if (!latest) return null

  return (
    <div style={{
      position: 'absolute', top: 12, right: 12, zIndex: 1000,
      pointerEvents: 'auto', width: 300,
    }}>
      <AnimatePresence mode="wait">
        <AlertCard key={latest.id} alert={latest} onDismiss={() => dismissAlert(latest.id)} />
      </AnimatePresence>
      {mapAlerts.length > 1 && (
        <div style={{
          padding: '4px 10px', borderRadius: 10, textAlign: 'center',
          background: 'rgba(255,255,255,0.55)', backdropFilter: 'blur(16px)',
          border: '1px solid rgba(255,255,255,0.8)',
          fontSize: 10, color: '#94a3b8',
        }}>
          +{mapAlerts.length - 1} more · <span
            style={{ color: '#3b82f6', cursor: 'pointer', fontWeight: 600 }}
            onClick={() => dismissAlert(latest.id)}
          >next →</span>
        </div>
      )}
    </div>
  )
}
