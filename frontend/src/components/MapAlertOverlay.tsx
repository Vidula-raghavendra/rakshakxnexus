import { useEffect } from 'react'
import { motion, AnimatePresence } from 'framer-motion'
import { useCityStore, MapAlert } from '../store/cityStore'

const SEVERITY_COLORS: Record<string, { border: string; bg: string; text: string }> = {
  critical: { border: '#ef4444', bg: '#1a0505', text: '#fca5a5' },
  high:     { border: '#f97316', bg: '#1a0a03', text: '#fed7aa' },
  medium:   { border: '#eab308', bg: '#17130a', text: '#fde68a' },
  low:      { border: '#3b82f6', bg: '#060f1e', text: '#93c5fd' },
}

const SOURCE_ICON: Record<string, string> = {
  rakshak:  '📹',
  vehicle:  '🚗',
  cascade:  '⚡',
  governor: '🤖',
}

function AlertCard({ alert, onDismiss }: { alert: MapAlert; onDismiss: () => void }) {
  const colors = SEVERITY_COLORS[alert.severity] ?? SEVERITY_COLORS.medium
  const icon = SOURCE_ICON[alert.type] ?? '⚠'

  // Auto-dismiss after 12s for low/medium, 20s for high/critical
  useEffect(() => {
    const ttl = alert.severity === 'critical' || alert.severity === 'high' ? 20000 : 12000
    const t = setTimeout(onDismiss, ttl)
    return () => clearTimeout(t)
  }, [alert.id])

  return (
    <motion.div
      layout
      initial={{ opacity: 0, x: 60, scale: 0.92 }}
      animate={{ opacity: 1, x: 0, scale: 1 }}
      exit={{ opacity: 0, x: 60, scale: 0.88 }}
      transition={{ duration: 0.25 }}
      style={{
        background: colors.bg,
        border: `1px solid ${colors.border}`,
        borderLeft: `3px solid ${colors.border}`,
        borderRadius: 6,
        padding: '10px 12px',
        marginBottom: 8,
        fontFamily: 'monospace',
        fontSize: 11,
        cursor: 'pointer',
        boxShadow: `0 0 12px ${colors.border}33`,
        maxWidth: 280,
      }}
      onClick={onDismiss}
    >
      {/* Header */}
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'flex-start', marginBottom: 4 }}>
        <div style={{ color: colors.text, fontWeight: 700, fontSize: 12, lineHeight: 1.3 }}>
          {icon} {alert.title}
        </div>
        <div style={{ color: '#334155', fontSize: 9, marginLeft: 8, whiteSpace: 'nowrap' }}>
          {new Date(alert.timestamp * 1000).toLocaleTimeString()}
        </div>
      </div>

      {/* Description */}
      <div style={{ color: '#94a3b8', marginBottom: 6, lineHeight: 1.4 }}>
        {alert.description}
      </div>

      {/* Details */}
      {Object.entries(alert.details).map(([k, v]) => (
        <div key={k} style={{ display: 'flex', gap: 6, marginBottom: 2 }}>
          <span style={{ color: '#475569', minWidth: 70, textTransform: 'capitalize' }}>{k}:</span>
          <span style={{ color: '#64748b' }}>{String(v)}</span>
        </div>
      ))}

      {/* Severity badge + dismiss hint */}
      <div style={{ display: 'flex', justifyContent: 'space-between', marginTop: 6, alignItems: 'center' }}>
        <span style={{
          padding: '1px 6px', borderRadius: 10,
          background: colors.border + '22', border: `1px solid ${colors.border}55`,
          color: colors.text, fontSize: 9, textTransform: 'uppercase', letterSpacing: 1,
        }}>
          {alert.severity}
        </span>
        <span style={{ color: '#1e293b', fontSize: 9 }}>click to dismiss</span>
      </div>
    </motion.div>
  )
}

export default function MapAlertOverlay() {
  const mapAlerts = useCityStore(s => s.mapAlerts)
  const dismissAlert = useCityStore(s => s.dismissAlert)

  if (mapAlerts.length === 0) return null

  return (
    <div style={{
      position: 'absolute',
      top: 12,
      right: 12,
      zIndex: 1000,
      maxHeight: 'calc(100% - 24px)',
      overflowY: 'auto',
      overflowX: 'hidden',
      pointerEvents: 'auto',
    }}>
      <AnimatePresence mode="sync">
        {mapAlerts.slice(0, 6).map(alert => (
          <AlertCard
            key={alert.id}
            alert={alert}
            onDismiss={() => dismissAlert(alert.id)}
          />
        ))}
      </AnimatePresence>
    </div>
  )
}
