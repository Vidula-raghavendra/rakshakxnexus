import { useCityStore } from '../store/cityStore'

export default function InfraPanel() {
  const snapshot = useCityStore(s => s.snapshot)

  if (!snapshot) return (
    <div style={{ padding: 14, height: '100%', display: 'flex', alignItems: 'center', justifyContent: 'center' }}>
      <span style={{ fontSize: 11, color: '#94a3b8' }}>Loading...</span>
    </div>
  )

  const zones = Object.values(snapshot.zones as any)
  const hospitals = Object.values(snapshot.hospitals as any)
  const substations = Object.values(snapshot.substations as any)

  const criticalZones = zones.filter((z: any) => z.status === 'critical').length
  const warningZones  = zones.filter((z: any) => z.status === 'warning').length
  const floodedZones  = zones.filter((z: any) => z.is_flooded).length
  const normalZones   = zones.length - criticalZones - warningZones

  return (
    <div style={{ height: '100%', display: 'flex', flexDirection: 'column', overflow: 'hidden' }}>
      <div style={{ padding: '10px 12px 8px', borderBottom: '1px solid rgba(0,0,0,0.06)', flexShrink: 0 }}>
        <span style={{ fontSize: 12, fontWeight: 700, color: '#1e293b' }}>Infrastructure</span>
      </div>

      <div style={{ flex: 1, overflowY: 'auto', padding: '8px 12px' }}>

        {/* Zone summary */}
        <div style={{ marginBottom: 12 }}>
          <div style={{ fontSize: 9, fontWeight: 600, color: '#94a3b8', textTransform: 'uppercase', letterSpacing: 0.5, marginBottom: 6 }}>Zones</div>
          <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 5 }}>
            {[
              { label: 'Critical', count: criticalZones, color: '#ef4444', bg: 'rgba(239,68,68,0.08)' },
              { label: 'Warning',  count: warningZones,  color: '#f97316', bg: 'rgba(249,115,22,0.08)' },
              { label: 'Flooded',  count: floodedZones,  color: '#3b82f6', bg: 'rgba(59,130,246,0.08)' },
              { label: 'Normal',   count: normalZones,   color: '#22c55e', bg: 'rgba(34,197,94,0.08)' },
            ].map(item => (
              <div key={item.label} style={{
                padding: '7px 9px', borderRadius: 10,
                background: item.count > 0 ? item.bg : 'rgba(255,255,255,0.4)',
                border: `1px solid ${item.count > 0 ? item.color + '30' : 'rgba(0,0,0,0.06)'}`,
              }}>
                <div style={{ fontSize: 17, fontWeight: 700, color: item.count > 0 ? item.color : '#cbd5e1' }}>{item.count}</div>
                <div style={{ fontSize: 9, color: item.count > 0 ? item.color : '#94a3b8', fontWeight: 500 }}>{item.label}</div>
              </div>
            ))}
          </div>
        </div>

        {/* Hospitals */}
        <div style={{ marginBottom: 12 }}>
          <div style={{ fontSize: 9, fontWeight: 600, color: '#94a3b8', textTransform: 'uppercase', letterSpacing: 0.5, marginBottom: 6 }}>Hospitals</div>
          {hospitals.slice(0, 3).map((h: any) => (
            <div key={h.id} style={{
              display: 'flex', alignItems: 'center', justifyContent: 'space-between',
              padding: '6px 10px', borderRadius: 8, marginBottom: 4,
              background: !h.has_power ? 'rgba(239,68,68,0.06)' : 'rgba(255,255,255,0.4)',
              border: `1px solid ${!h.has_power ? 'rgba(239,68,68,0.2)' : 'rgba(0,0,0,0.06)'}`,
            }}>
              <div>
                <div style={{ fontSize: 10, fontWeight: 500, color: '#1e293b' }}>🏥 {(h.name || h.id).replace(' Hospital', '')}</div>
                {h.backup_power_active && <div style={{ fontSize: 9, color: '#ea580c' }}>⚡ Backup active</div>}
              </div>
              <div style={{ display: 'flex', gap: 4 }}>
                <span style={{ fontSize: 8, padding: '2px 5px', borderRadius: 5, background: h.has_power ? 'rgba(34,197,94,0.1)' : 'rgba(239,68,68,0.1)', color: h.has_power ? '#16a34a' : '#dc2626', fontWeight: 600 }}>
                  {h.has_power ? '✓ PWR' : '✗ PWR'}
                </span>
                <span style={{ fontSize: 8, padding: '2px 5px', borderRadius: 5, background: h.accessible ? 'rgba(34,197,94,0.1)' : 'rgba(239,68,68,0.1)', color: h.accessible ? '#16a34a' : '#dc2626', fontWeight: 600 }}>
                  {h.accessible ? '✓ ACC' : '✗ BLK'}
                </span>
              </div>
            </div>
          ))}
        </div>

        {/* Substations */}
        <div>
          <div style={{ fontSize: 9, fontWeight: 600, color: '#94a3b8', textTransform: 'uppercase', letterSpacing: 0.5, marginBottom: 6 }}>Substations</div>
          {substations.slice(0, 3).map((s: any) => {
            const loadPct = s.max_load_mw > 0 ? s.load_mw / s.max_load_mw : 0
            return (
              <div key={s.id} style={{
                padding: '6px 10px', borderRadius: 8, marginBottom: 4,
                background: s.overloaded ? 'rgba(239,68,68,0.06)' : 'rgba(255,255,255,0.4)',
                border: `1px solid ${s.overloaded ? 'rgba(239,68,68,0.2)' : 'rgba(0,0,0,0.06)'}`,
              }}>
                <div style={{ display: 'flex', justifyContent: 'space-between', marginBottom: 4 }}>
                  <span style={{ fontSize: 10, fontWeight: 500, color: '#1e293b' }}>⚡ {(s.name || s.id).replace(' Substation', '')}</span>
                  <span style={{ fontSize: 9, color: s.overloaded ? '#dc2626' : '#16a34a', fontWeight: 600 }}>{s.overloaded ? 'OVERLOAD' : 'OK'}</span>
                </div>
                <div style={{ height: 3, background: 'rgba(0,0,0,0.08)', borderRadius: 2 }}>
                  <div style={{ width: `${Math.min(loadPct * 100, 100)}%`, height: '100%', borderRadius: 2, background: loadPct > 0.9 ? '#ef4444' : loadPct > 0.7 ? '#f97316' : '#22c55e' }} />
                </div>
                <div style={{ fontSize: 9, color: '#94a3b8', marginTop: 3 }}>{s.load_mw?.toFixed(0)} / {s.max_load_mw?.toFixed(0)} MW</div>
              </div>
            )
          })}
        </div>
      </div>
    </div>
  )
}
