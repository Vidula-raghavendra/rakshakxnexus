import { useEffect, useRef } from 'react'
import { useCityStore, MapAlert } from '../store/cityStore'

const API_KEY = import.meta.env.VITE_API_KEY ?? ''
const WS_CITY = `ws://localhost:8000/ws/city?token=${API_KEY}`
const WS_DECISIONS = `ws://localhost:8000/ws/decisions?token=${API_KEY}`
const WS_RAKSHAK = `ws://localhost:8001/stream?token=${API_KEY}`

let _alertCounter = 0
function makeAlertId() { return `alert_${Date.now()}_${_alertCounter++}` }

export function useWebSocket() {
  const {
    setSnapshot, addDecision, addRakshakIncident,
    addMapAlert, setConnected, setPendingApprovals, setAgentStates, updateAgentVote,
    setRulesMode, setRulesEngineResult, setSimulatedCounterfactual, appendStreamChunk,
    addAlertDispatch,
  } = useCityStore()
  const lastCounterfactualFetch = useRef(0)
  const cityWs = useRef<WebSocket | null>(null)
  const decisionWs = useRef<WebSocket | null>(null)
  const rakshakWs = useRef<WebSocket | null>(null)

  useEffect(() => {
    let cityRetry: ReturnType<typeof setTimeout>
    let decRetry: ReturnType<typeof setTimeout>

    function connectCity() {
      const ws = new WebSocket(WS_CITY)
      cityWs.current = ws

      ws.onopen = () => setConnected(true)
      ws.onclose = () => {
        setConnected(false)
        cityRetry = setTimeout(connectCity, 2000)
      }
      ws.onerror = () => ws.close()
      ws.onmessage = (e) => {
        try {
          const msg = JSON.parse(e.data)
          if (msg.type === 'city_state') {
            setSnapshot(msg.data)
            // Fetch real counterfactual whenever a zone goes critical (throttled to once per 30s)
            const now = Date.now()
            const hasCritical = Object.values(msg.data.zones ?? {}).some(
              (z: any) => z.status === 'critical'
            )
            if (hasCritical && now - lastCounterfactualFetch.current > 30000) {
              lastCounterfactualFetch.current = now
              fetch('/api/counterfactual', { method: 'POST', headers: { 'X-API-Key': API_KEY } })
                .then(r => r.json())
                .then(data => setSimulatedCounterfactual(data))
                .catch(() => {})
            }
          }
        } catch {}
      }
    }

    function connectDecisions() {
      const ws = new WebSocket(WS_DECISIONS)
      decisionWs.current = ws

      ws.onclose = () => { decRetry = setTimeout(connectDecisions, 2000) }
      ws.onerror = () => ws.close()
      ws.onmessage = (e) => {
        try {
          const msg = JSON.parse(e.data)

          if (msg.type === 'decision') {
            addDecision(msg.data)
          }

          if (msg.type === 'decision_history') {
            msg.data.forEach(addDecision)
            setPendingApprovals(msg.data.filter((d: any) => d.status === 'pending_approval'))
          }
          if (msg.type === 'agent_states') {
            setAgentStates(msg.data)
          }
          if (msg.type === 'agent_vote') {
            updateAgentVote(msg.data)
          }

          if (msg.type === 'rakshak_incident') {
            const inc = msg.data
            addRakshakIncident(inc)
            const alert: MapAlert = {
              id: makeAlertId(),
              type: 'rakshak',
              lat: inc.lat,
              lng: inc.lng,
              title: `📹 CCTV: ${inc.incident_type.replace(/_/g, ' ').toUpperCase()}`,
              description: inc.description,
              severity: inc.severity,
              timestamp: inc.timestamp,
              details: {
                camera: inc.camera_id,
                confidence: `${(inc.confidence * 100).toFixed(0)}%`,
                zone: inc.zone_id,
              },
            }
            addMapAlert(alert)
          }

          if (msg.type === 'stream_chunk') {
            appendStreamChunk(msg.data)
          }

          if (msg.type === 'rules_mode') {
            setRulesMode(msg.data.enabled)
          }

          if (msg.type === 'rules_engine_result') {
            setRulesEngineResult(msg.data)
          }

        } catch {}
      }
    }

    function connectRakshak() {
      const ws = new WebSocket(WS_RAKSHAK)
      rakshakWs.current = ws
      ws.onclose = () => { setTimeout(connectRakshak, 3000) }
      ws.onerror = () => ws.close()
      ws.onmessage = (e) => {
        try {
          const msg = JSON.parse(e.data)

          // Detection from Rakshak — pin on map and add to store
          if (msg.type === 'rakshak_incident' || msg.type === 'detection') {
            const inc = msg.data
            if (inc.incident_type && inc.incident_type !== 'none') {
              addRakshakIncident(inc)
              addMapAlert({
                id: makeAlertId(),
                type: 'rakshak',
                lat: inc.lat,
                lng: inc.lng,
                title: `📹 CCTV: ${inc.incident_type.replace(/_/g, ' ').toUpperCase()}`,
                description: inc.description,
                severity: inc.severity,
                timestamp: inc.timestamp,
                details: {
                  camera: inc.camera_id,
                  confidence: `${(inc.confidence * 100).toFixed(0)}%`,
                  zone: inc.zone_id,
                },
              })
            }
          }

          if (msg.type === 'alert_dispatched') {
            addAlertDispatch({
              ...msg.data,
              timestamp: Date.now() / 1000,
            })
            const a = msg.data
            const depts = (a.alerts || []).map((d: { dept_name: string }) => d.dept_name).join(', ')
            addMapAlert({
              id: makeAlertId(),
              type: 'rakshak',
              lat: 17.3900,
              lng: 78.4600,
              title: `📞 ALERTED: ${depts}`,
              description: `${a.incident_type.replace(/_/g, ' ')} — calls + SMS sent`,
              severity: a.severity,
              timestamp: Date.now() / 1000,
              details: { alerts: a.alerts },
            })
          }
        } catch {}
      }
    }

    connectCity()
    connectDecisions()
    connectRakshak()

    return () => {
      clearTimeout(cityRetry)
      clearTimeout(decRetry)
      cityWs.current?.close()
      decisionWs.current?.close()
      rakshakWs.current?.close()
    }
  }, [])
}
