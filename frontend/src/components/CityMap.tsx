import React, { useEffect, useRef } from 'react'
import L from 'leaflet'
import 'leaflet/dist/leaflet.css'
import { useCityStore } from '../store/cityStore'
import MapAlertOverlay from './MapAlertOverlay'

const INC_EMOJI: Record<string, string> = {
  road_accident: '🚗', road_flood: '🌊', vehicle_stranded: '🚘',
  crowd_surge: '👥', road_blocked: '🚧', women_safety: '🚨',
  fight_violence: '⚠️', traffic_signal_issue: '🚦',
  garbage_dumping: '🗑', animal_on_road: '🐄',
}

const INC_COLOR: Record<string, string> = {
  road_accident: '#3b82f6', road_flood: '#6366f1', vehicle_stranded: '#8b5cf6',
  crowd_surge: '#f97316', road_blocked: '#64748b', women_safety: '#ec4899',
  fight_violence: '#f97316', traffic_signal_issue: '#3b82f6', default: '#3b82f6',
}

export default function CityMap() {
  const mapRef = useRef<L.Map | null>(null)
  const containerRef = useRef<HTMLDivElement>(null)
  const rakshakLayerRef = useRef<L.LayerGroup | null>(null)

  const rakshakIncidents = useCityStore(s => s.rakshakIncidents)
  const dismissAlert = useCityStore(s => s.dismissAlert)
  const mapAlerts = useCityStore(s => s.mapAlerts)

  // Init map once
  useEffect(() => {
    if (!containerRef.current || mapRef.current) return

    const map = L.map(containerRef.current, {
      center: [17.3900, 78.4600],
      zoom: 13,
      zoomControl: false,
      attributionControl: false,
    })

    L.tileLayer('https://{s}.basemaps.cartocdn.com/light_all/{z}/{x}/{y}{r}.png', {
      attribution: '© OpenStreetMap © CARTO',
      subdomains: 'abcd',
      maxZoom: 19,
    }).addTo(map)

    L.control.zoom({ position: 'bottomright' }).addTo(map)
    rakshakLayerRef.current = L.layerGroup().addTo(map)

    mapRef.current = map
    return () => { map.remove(); mapRef.current = null }
  }, [])

  // Draw CCTV incident pins — clean glass pill style, not red
  useEffect(() => {
    if (!rakshakLayerRef.current) return
    rakshakLayerRef.current.clearLayers()

    const now = Date.now() / 1000
    rakshakIncidents.filter(inc => now - inc.timestamp < 300).forEach((inc) => {
      const age = now - inc.timestamp
      const opacity = Math.max(0.6, 1 - age / 300)
      const color = INC_COLOR[inc.incident_type] || INC_COLOR.default
      const emoji = INC_EMOJI[inc.incident_type] || '📹'
      const typeLabel = inc.incident_type.replace(/_/g, ' ').replace(/\b\w/g, (c: string) => c.toUpperCase())

      // Subtle pulse ring
      const pulse = L.circle([inc.lat, inc.lng], {
        radius: 220,
        color,
        fillColor: color,
        fillOpacity: 0.06 * opacity,
        weight: 1.5,
        opacity: opacity * 0.4,
      })
      rakshakLayerRef.current!.addLayer(pulse)

      // Glass pill marker
      const icon = L.divIcon({
        html: `<div style="
          display:flex;align-items:center;gap:5px;
          background:rgba(255,255,255,0.82);
          backdrop-filter:blur(16px);-webkit-backdrop-filter:blur(16px);
          border:1px solid rgba(255,255,255,0.9);
          border-left:3px solid ${color};
          border-radius:20px;
          padding:5px 10px 5px 7px;
          box-shadow:0 3px 16px rgba(100,130,200,0.18),inset 0 1px 0 rgba(255,255,255,0.95);
          opacity:${opacity};
          white-space:nowrap;
          font-family:'Inter','Segoe UI',sans-serif;
          pointer-events:auto;
        ">
          <span style="font-size:13px;line-height:1">${emoji}</span>
          <div>
            <div style="font-size:10px;font-weight:700;color:#1e293b;line-height:1.2">${typeLabel}</div>
            <div style="font-size:8px;color:#94a3b8;margin-top:1px">${inc.camera_id.replace('cam_','').toUpperCase().replace('_','-')} · ${(inc.confidence*100).toFixed(0)}%</div>
          </div>
        </div>`,
        className: '',
        iconAnchor: [0, 20],
      })

      const marker = L.marker([inc.lat, inc.lng], { icon })
      marker.bindTooltip(`
        <div style="font-family:'Inter',sans-serif;font-size:12px;min-width:160px">
          <b style="color:#1e293b">${typeLabel}</b><br/>
          <span style="color:#64748b">${inc.description}</span><br/>
          <small style="color:#94a3b8">Camera: ${inc.camera_id} · ${(inc.confidence*100).toFixed(0)}%</small>
        </div>
      `, { sticky: true })
      rakshakLayerRef.current!.addLayer(marker)
    })
  }, [rakshakIncidents])

  const activeCount = rakshakIncidents.filter(i => Date.now() / 1000 - i.timestamp < 300).length

  return (
    <div style={{ width: '100%', height: '100%', position: 'relative' }}>
      <div ref={containerRef} style={{ width: '100%', height: '100%' }} />
      <MapAlertOverlay />
      <LiveCctvStrip />

      {activeCount > 0 && (
        <div style={{
          position: 'absolute', bottom: 135, left: 10,
          background: 'rgba(255,255,255,0.65)',
          backdropFilter: 'blur(14px)', WebkitBackdropFilter: 'blur(14px)',
          border: '1px solid rgba(255,255,255,0.85)',
          padding: '4px 14px', borderRadius: 20, fontSize: 11, fontWeight: 600,
          color: '#3b82f6', pointerEvents: 'none',
          boxShadow: '0 2px 12px rgba(100,130,200,0.12)',
        }}>
          📹 {activeCount} CCTV detection{activeCount > 1 ? 's' : ''} active
        </div>
      )}
    </div>
  )
}

const CCTV_CAMS = [
  { id: 'cam_meh_001', label: 'Mehdipatnam' },
  { id: 'cam_tol_001', label: 'Tolichowki' },
  { id: 'cam_nar_001', label: 'Narayanguda' },
  { id: 'cam_mal_001', label: 'Malakpet' },
]

function LiveCctvStrip() {
  const [feeds, setFeeds] = React.useState<Record<string, string>>({})
  const [expanded, setExpanded] = React.useState<string | null>(null)

  React.useEffect(() => {
    let alive = true
    const intervals: Record<string, ReturnType<typeof setInterval>> = {}
    CCTV_CAMS.forEach(cam => {
      const poll = () => {
        const src = `http://localhost:8001/frames/${cam.id}/latest.jpg?t=${Date.now()}`
        const img = new Image()
        img.onload = () => { if (alive) setFeeds(f => ({ ...f, [cam.id]: src })) }
        img.src = src
      }
      poll()
      intervals[cam.id] = setInterval(poll, 1500)
    })
    return () => { alive = false; Object.values(intervals).forEach(clearInterval) }
  }, [])

  return (
    <div style={{ position: 'absolute', bottom: 10, left: 10, display: 'flex', gap: 6, zIndex: 800 }}>
      {CCTV_CAMS.map(cam => {
        const hasFeed = !!feeds[cam.id]
        const isExp = expanded === cam.id
        return (
          <div key={cam.id} onClick={() => setExpanded(isExp ? null : cam.id)} style={{
            width: isExp ? 200 : 80, height: isExp ? 112 : 50,
            borderRadius: 10, overflow: 'hidden', cursor: 'pointer',
            transition: 'all 0.25s ease',
            background: hasFeed ? '#000' : 'rgba(255,255,255,0.55)',
            backdropFilter: 'blur(16px)', WebkitBackdropFilter: 'blur(16px)',
            border: hasFeed ? '1.5px solid rgba(100,130,200,0.3)' : '1.5px solid rgba(255,255,255,0.7)',
            boxShadow: '0 4px 16px rgba(100,130,200,0.12), inset 0 1px 0 rgba(255,255,255,0.9)',
            position: 'relative',
          }}>
            {hasFeed
              ? <img src={feeds[cam.id]} style={{ width: '100%', height: '100%', objectFit: 'cover', display: 'block' }} />
              : <div style={{ width: '100%', height: '100%', display: 'flex', alignItems: 'center', justifyContent: 'center' }}>
                  <span style={{ fontSize: 16, opacity: 0.25 }}>📡</span>
                </div>
            }
            {hasFeed && <div style={{ position: 'absolute', top: 5, left: 5, width: 5, height: 5, borderRadius: '50%', background: '#ef4444' }} />}
            <div style={{
              position: 'absolute', bottom: 0, left: 0, right: 0,
              background: hasFeed ? 'linear-gradient(transparent,rgba(0,0,0,0.7))' : 'transparent',
              padding: '8px 5px 3px',
            }}>
              <div style={{ fontSize: 8, fontWeight: 700, color: hasFeed ? 'rgba(255,255,255,0.9)' : '#64748b', textAlign: 'center' }}>
                {cam.label}
              </div>
            </div>
          </div>
        )
      })}
    </div>
  )
}
