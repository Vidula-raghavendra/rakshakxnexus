import { motion } from 'framer-motion'
import { useCityStore } from '../store/cityStore'

const STATUS_COLORS: Record<string, string> = {
  normal:     '#22c55e',
  watch:      '#eab308',
  warning:    '#f97316',
  critical:   '#ef4444',
  evacuating: '#a855f7',
}

export default function InfraPanel() {
  const snapshot = useCityStore(s => s.snapshot)

  if (!snapshot) return (
    <div style={{ padding: 12, color: '#1e293b', fontFamily: 'monospace', fontSize: 10, textAlign: 'center', paddingTop: 24 }}>
      CONNECTING...
    </div>
  )

  return (
    <div style={{
      height: '100%', overflowY: 'auto', background: '#020617',
      fontFamily: 'monospace', fontSize: 10,
    }}>

      {/* Power Grid */}
      <SectionHeader title="Power Grid" />
      {Object.values(snapshot.substations as any).map((s: any) => {
        const pct = Math.min(100, (s.load_mw / s.max_load_mw) * 100)
        const status = s.overloaded ? 'critical' : !s.online ? 'warning' : pct > 85 ? 'watch' : 'normal'
        const color = STATUS_COLORS[status]
        return (
          <div key={s.id} style={{ padding: '5px 10px', borderBottom: '1px solid #0a0f1e' }}>
            <div style={{ display: 'flex', justifyContent: 'space-between', marginBottom: 3 }}>
              <span style={{ color: '#64748b', fontSize: 9, maxWidth: 110, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
                {s.name}
              </span>
              <span style={{ color, fontSize: 9, fontWeight: 700 }}>
                {s.overloaded ? 'OVERLOAD' : !s.online ? 'OFFLINE' : `${pct.toFixed(0)}%`}
              </span>
            </div>
            <div style={{ height: 3, background: '#0f172a', borderRadius: 1, overflow: 'hidden' }}>
              <motion.div
                animate={{ width: `${pct}%` }}
                transition={{ duration: 0.8, ease: 'easeOut' }}
                style={{ height: '100%', background: color, borderRadius: 1 }}
              />
            </div>
            <div style={{ fontSize: 8, color: '#334155', marginTop: 2 }}>
              {s.load_mw.toFixed(0)} / {s.max_load_mw.toFixed(0)} MW
            </div>
          </div>
        )
      })}

      {/* Hospitals */}
      <SectionHeader title="Hospitals" />
      {Object.values(snapshot.hospitals as any).map((h: any) => {
        const status = !h.has_power ? 'critical' : h.backup_power_active ? 'warning' : !h.accessible ? 'warning' : 'normal'
        const color = STATUS_COLORS[status]
        return (
          <div key={h.id} style={{ padding: '5px 10px', borderBottom: '1px solid #0a0f1e' }}>
            <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
              <span style={{ color: '#64748b', fontSize: 9, maxWidth: 110, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
                {h.name}
              </span>
              <div style={{ display: 'flex', alignItems: 'center', gap: 4 }}>
                {!h.accessible && (
                  <span style={{ fontSize: 8, color: '#f97316', fontWeight: 700 }}>INAC</span>
                )}
                <span style={{
                  fontSize: 8, fontWeight: 700, color,
                  padding: '1px 4px', borderRadius: 2,
                  background: `${color}18`, border: `1px solid ${color}33`,
                }}>
                  {!h.has_power ? 'NO PWR' : h.backup_power_active ? 'BACKUP' : 'ONLINE'}
                </span>
              </div>
            </div>
            {/* Power indicator bar */}
            <div style={{ height: 2, background: '#0f172a', borderRadius: 1, marginTop: 4, overflow: 'hidden' }}>
              <motion.div
                animate={{ width: h.has_power ? '100%' : '0%' }}
                transition={{ duration: 0.5 }}
                style={{
                  height: '100%',
                  background: h.backup_power_active ? '#f97316' : h.has_power ? '#22c55e' : '#ef4444',
                  borderRadius: 1,
                }}
              />
            </div>
          </div>
        )
      })}

      {/* Resources */}
      <SectionHeader title="Field Units" />
      {Object.values(snapshot.resources as any).map((r: any) => {
        const status = r.status === 'available' ? 'normal' : r.status === 'en_route' ? 'watch' : 'warning'
        const color = STATUS_COLORS[status]
        const typeIcon: Record<string, string> = { ambulance: '🚑', fire_truck: '🚒', police: '🚔', rescue: '⛑️' }
        return (
          <div key={r.id} style={{
            padding: '4px 10px', borderBottom: '1px solid #0a0f1e',
            display: 'flex', alignItems: 'center', gap: 6,
          }}>
            <span style={{ fontSize: 11 }}>{typeIcon[r.type] || '🚨'}</span>
            <div style={{ flex: 1, minWidth: 0 }}>
              <div style={{ color: '#475569', fontSize: 9, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
                {r.id}
              </div>
            </div>
            <span style={{
              fontSize: 8, fontWeight: 700, color,
              padding: '1px 4px', borderRadius: 2,
              background: `${color}18`, border: `1px solid ${color}33`,
              textTransform: 'uppercase', flexShrink: 0,
            }}>
              {r.status.replace('_', ' ')}
            </span>
          </div>
        )
      })}

    </div>
  )
}

function SectionHeader({ title }: { title: string }) {
  return (
    <div style={{
      padding: '5px 10px',
      background: '#040c14',
      borderBottom: '1px solid #0f172a',
      borderTop: '1px solid #0f172a',
      color: '#334155',
      fontSize: 8,
      textTransform: 'uppercase',
      letterSpacing: 2,
    }}>
      {title}
    </div>
  )
}
