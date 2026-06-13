import { motion, AnimatePresence } from 'framer-motion'
import { useCityStore } from '../store/cityStore'

export default function CounterfactualPanel() {
  const decisions = useCityStore(s => s.decisions)
  const snapshot = useCityStore(s => s.snapshot)
  const simulatedCF = useCityStore(s => s.simulatedCounterfactual)

  // Prefer the real simulated counterfactual (actual engine fork) over LLM-provided text
  const withCounterfactual = decisions.find(d => (d as any).counterfactual)
  const llmCF = withCounterfactual ? (withCounterfactual as any).counterfactual : null
  const cf = simulatedCF ?? llmCF

  // Running totals from all decisions
  const interventions = decisions.filter(d => d.status === 'auto_executed' || d.status === 'approved_executed').length
  const rejected = decisions.filter(d => d.status === 'rejected').length
  const strandedNow = snapshot?.vehicles?.filter((v: any) => v.status === 'stranded').length ?? 0

  return (
    <div style={{
      height: '100%',
      background: '#020617',
      fontFamily: 'monospace',
      display: 'flex',
      flexDirection: 'column',
      borderTop: '1px solid #0f172a',
    }}>
      <div style={{
        padding: '6px 12px',
        borderBottom: '1px solid #0f172a',
        fontSize: 9,
        color: '#334155',
        textTransform: 'uppercase',
        letterSpacing: 2,
        display: 'flex',
        justifyContent: 'space-between',
      }}>
        <span>Counterfactual</span>
        <span style={{ color: simulatedCF ? '#22c55e' : '#1e293b', fontSize: 8 }}>
          {simulatedCF ? '● SIMULATED FORK' : 'What happens if we don\'t act?'}
        </span>
      </div>

      <AnimatePresence mode="wait">
        {cf ? (
          <motion.div
            key={withCounterfactual?.id}
            initial={{ opacity: 0 }}
            animate={{ opacity: 1 }}
            style={{ flex: 1, display: 'flex', flexDirection: 'column', padding: '8px', gap: 6 }}
          >
            {/* Split view */}
            <div style={{ display: 'flex', gap: 6, flex: 1 }}>
              {/* Without NEXUS */}
              <div style={{
                flex: 1,
                background: '#1a0505',
                border: '1px solid #ef444433',
                borderRadius: 4,
                padding: '8px',
                fontSize: 10,
              }}>
                <div style={{ color: '#ef4444', fontSize: 9, fontWeight: 700, marginBottom: 5, letterSpacing: 1 }}>
                  ✗ WITHOUT NEXUS
                </div>
                <div style={{ color: '#94a3b8', lineHeight: 1.5 }}>
                  {cf.without_intervention}
                </div>
                <div style={{
                  marginTop: 6, padding: '3px 6px',
                  background: '#ef444422', borderRadius: 3,
                  fontSize: 9, color: '#fca5a5',
                }}>
                  Risk: {cf.lives_at_risk?.toUpperCase() ?? 'UNKNOWN'}
                </div>
              </div>

              {/* With NEXUS */}
              <div style={{
                flex: 1,
                background: '#051a0a',
                border: '1px solid #22c55e33',
                borderRadius: 4,
                padding: '8px',
                fontSize: 10,
              }}>
                <div style={{ color: '#22c55e', fontSize: 9, fontWeight: 700, marginBottom: 5, letterSpacing: 1 }}>
                  ✓ WITH NEXUS
                </div>
                <div style={{ color: '#94a3b8', lineHeight: 1.5 }}>
                  {cf.with_intervention}
                </div>
                <div style={{
                  marginTop: 6, padding: '3px 6px',
                  background: '#22c55e22', borderRadius: 3,
                  fontSize: 9, color: '#86efac',
                }}>
                  Window: {cf.time_window_seconds}s to act
                </div>
              </div>
            </div>
          </motion.div>
        ) : (
          <motion.div
            key="no-cf"
            style={{
              flex: 1, display: 'flex', flexDirection: 'column',
              alignItems: 'center', justifyContent: 'center', padding: 12,
            }}
          >
            {/* Live stats even without counterfactual */}
            <div style={{ width: '100%', display: 'flex', gap: 6 }}>
              {[
                { label: 'Interventions', value: interventions, color: '#22c55e' },
                { label: 'Rejected', value: rejected, color: '#ef4444' },
                { label: 'Stranded', value: strandedNow, color: '#f97316' },
              ].map(stat => (
                <div key={stat.label} style={{
                  flex: 1,
                  background: '#0a0f1e',
                  border: `1px solid ${stat.color}22`,
                  borderRadius: 4,
                  padding: '8px 6px',
                  textAlign: 'center',
                }}>
                  <div style={{ fontSize: 20, fontWeight: 900, color: stat.color }}>{stat.value}</div>
                  <div style={{ fontSize: 9, color: '#334155', textTransform: 'uppercase', letterSpacing: 1 }}>
                    {stat.label}
                  </div>
                </div>
              ))}
            </div>
            <div style={{ marginTop: 8, fontSize: 9, color: '#1e293b' }}>
              Counterfactual appears when Simulation Agent runs
            </div>
          </motion.div>
        )}
      </AnimatePresence>
    </div>
  )
}
