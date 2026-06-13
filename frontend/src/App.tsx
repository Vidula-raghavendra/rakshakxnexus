import { useWebSocket } from './hooks/useWebSocket'
import StatusBar from './components/StatusBar'
import CityMap from './components/CityMap'
import DecisionFeed from './components/DecisionFeed'
import CctvPanel from './components/CctvPanel'
import InfraPanel from './components/InfraPanel'

export default function App() {
  useWebSocket()

  return (
    <div style={{
      width: '100vw', height: '100vh',
      background: 'linear-gradient(135deg, #e8edf5 0%, #f0f4fa 40%, #e4eaf4 100%)',
      display: 'flex', flexDirection: 'column',
      overflow: 'hidden',
      fontFamily: "'Inter', 'Segoe UI', sans-serif",
    }}>
      <style>{`
        @import url('https://fonts.googleapis.com/css2?family=Inter:wght@300;400;500;600;700&display=swap');
        * { box-sizing: border-box; }
        ::-webkit-scrollbar { width: 4px; }
        ::-webkit-scrollbar-track { background: transparent; }
        ::-webkit-scrollbar-thumb { background: rgba(100,130,200,0.2); border-radius: 2px; }
        .glass {
          background: rgba(255,255,255,0.55);
          backdrop-filter: blur(20px);
          -webkit-backdrop-filter: blur(20px);
          border: 1px solid rgba(255,255,255,0.8);
          box-shadow: 0 4px 24px rgba(100,130,200,0.08), inset 0 1px 0 rgba(255,255,255,0.9);
        }
        .glass-dark {
          background: rgba(255,255,255,0.35);
          backdrop-filter: blur(16px);
          -webkit-backdrop-filter: blur(16px);
          border: 1px solid rgba(255,255,255,0.6);
          box-shadow: 0 2px 12px rgba(100,130,200,0.06);
        }
        @keyframes pulse-dot { 0%,100%{opacity:1} 50%{opacity:0.3} }
        @keyframes camFlash { 0%,100%{border-color:rgba(239,68,68,0.6)} 50%{border-color:rgba(239,68,68,0.2)} }
        @keyframes slideIn { from{opacity:0;transform:translateY(6px)} to{opacity:1;transform:translateY(0)} }
      `}</style>

      <StatusBar />

      <div style={{ flex: 1, display: 'flex', gap: 10, padding: '10px 12px 12px', overflow: 'hidden', minHeight: 0 }}>

        {/* Left — CCTV + Infra */}
        <div style={{ width: 260, display: 'flex', flexDirection: 'column', gap: 10, flexShrink: 0 }}>
          <div className="glass" style={{ flex: '0 0 260px', borderRadius: 16, overflow: 'hidden' }}>
            <CctvPanel />
          </div>
          <div className="glass" style={{ flex: 1, borderRadius: 16, overflow: 'hidden', minHeight: 0 }}>
            <InfraPanel />
          </div>
        </div>

        {/* Center — Map */}
        <div className="glass" style={{ flex: 1, borderRadius: 16, overflow: 'hidden', position: 'relative' }}>
          <CityMap />
        </div>

        {/* Right — AI Governor + Detection Log */}
        <div style={{ width: 300, display: 'flex', flexDirection: 'column', gap: 10, flexShrink: 0 }}>
          <div className="glass" style={{ flex: 1, borderRadius: 16, overflow: 'hidden', minHeight: 0 }}>
            <DecisionFeed />
          </div>
        </div>

      </div>
    </div>
  )
}
