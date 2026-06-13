import { useWebSocket } from './hooks/useWebSocket'
import StatusBar from './components/StatusBar'
import CityMap from './components/CityMap'
import DecisionFeed from './components/DecisionFeed'
import CascadeGraph from './components/CascadeGraph'
import InfraPanel from './components/InfraPanel'
import AgentConsole from './components/AgentConsole'
import CounterfactualPanel from './components/CounterfactualPanel'
import CctvPanel from './components/CctvPanel'

export default function App() {
  useWebSocket()

  return (
    <div style={{
      width: '100vw', height: '100vh',
      background: '#020617', color: '#e2e8f0',
      display: 'flex', flexDirection: 'column',
      overflow: 'hidden',
      fontFamily: 'monospace',
    }}>
      {/* Top bar */}
      <StatusBar />

      {/* Main area */}
      <div style={{ flex: 1, display: 'flex', overflow: 'hidden', minHeight: 0 }}>

        {/* Left panel — CCTV feeds + infrastructure */}
        <div style={{
          width: 230,
          borderRight: '1px solid #0f172a',
          display: 'flex',
          flexDirection: 'column',
          overflow: 'hidden',
          flexShrink: 0,
        }}>
          {/* CCTV 2x2 grid — top half */}
          <div style={{ flex: '0 0 220px', borderBottom: '1px solid #0f172a', overflow: 'hidden' }}>
            <CctvPanel />
          </div>
          {/* Infra status — bottom half */}
          <div style={{ flex: 1, overflow: 'hidden' }}>
            <InfraPanel />
          </div>
        </div>

        {/* Center — map */}
        <div style={{ flex: 1, position: 'relative', overflow: 'hidden' }}>
          <CityMap />
        </div>

        {/* Right panel — decisions | counterfactual | cascade */}
        <div style={{
          width: 310,
          borderLeft: '1px solid #0f172a',
          display: 'flex',
          flexDirection: 'column',
          overflow: 'hidden',
          flexShrink: 0,
        }}>
          <div style={{ flex: 1, minHeight: 0, overflow: 'hidden' }}>
            <DecisionFeed />
          </div>
          <div style={{ height: 180, borderTop: '1px solid #0f172a', flexShrink: 0 }}>
            <CounterfactualPanel />
          </div>
          <div style={{ height: 160, borderTop: '1px solid #0f172a', flexShrink: 0 }}>
            <CascadeGraph />
          </div>
        </div>
      </div>

      {/* Bottom — Agent Council (full width) */}
      <div style={{ height: 200, borderTop: '1px solid #0f172a', flexShrink: 0 }}>
        <AgentConsole />
      </div>
    </div>
  )
}
