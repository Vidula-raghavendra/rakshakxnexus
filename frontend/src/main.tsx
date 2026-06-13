import React from 'react'
import ReactDOM from 'react-dom/client'
import App from './App'

// Global reset
const style = document.createElement('style')
style.textContent = `
  *, *::before, *::after { box-sizing: border-box; margin: 0; padding: 0; }
  body { overflow: hidden; background: #020617; }
  ::-webkit-scrollbar { width: 4px; }
  ::-webkit-scrollbar-track { background: #0f172a; }
  ::-webkit-scrollbar-thumb { background: #1e293b; border-radius: 2px; }
  .nexus-pulse { animation: nexus-pulse-anim 2s ease-in-out infinite; }
  @keyframes nexus-pulse-anim {
    0%, 100% { opacity: 0.08; }
    50% { opacity: 0.2; }
  }
  .leaflet-container { background: #0f172a !important; }
`
document.head.appendChild(style)

ReactDOM.createRoot(document.getElementById('root')!).render(
  <React.StrictMode>
    <App />
  </React.StrictMode>
)
