import { useEffect, useRef } from 'react'
import L from 'leaflet'
import 'leaflet/dist/leaflet.css'
import { useCityStore, ZoneStatus } from '../store/cityStore'
import MapAlertOverlay from './MapAlertOverlay'

const ZONE_COLORS: Record<ZoneStatus, string> = {
  normal:     '#22c55e',
  watch:      '#eab308',
  warning:    '#f97316',
  critical:   '#ef4444',
  evacuating: '#a855f7',
}

const RESOURCE_ICONS: Record<string, string> = {
  ambulance:   '🚑',
  fire_truck:  '🚒',
  police:      '🚓',
  power_crew:  '⚡',
}

// Real Hyderabad road network — major corridors connecting the monitored junctions
// These are actual road centerline segments between underpass junctions
const ROAD_SEGMENTS = [
  // Mehdipatnam ↔ Tolichowki (Ring Road / Rethibowli Rd)
  { from: [17.3951, 78.4293], to: [17.4049, 78.4112], road: 'Rethibowli Road', id: 'meh_tol' },
  // Mehdipatnam ↔ Narayanguda (Masab Tank Rd)
  { from: [17.3951, 78.4293], to: [17.3918, 78.4861], road: 'Masab Tank Road', id: 'meh_nar' },
  // Narayanguda ↔ Malakpet (Tilak Road)
  { from: [17.3918, 78.4861], to: [17.3693, 78.4997], road: 'Tilak Road', id: 'nar_mal' },
  // Malakpet ↔ LB Nagar (NH-44 / Salar Jung)
  { from: [17.3693, 78.4997], to: [17.3471, 78.5518], road: 'NH-44 South', id: 'mal_lb' },
  // Tolichowki ↔ Narayanguda (Inner Ring Road)
  { from: [17.4049, 78.4112], to: [17.3918, 78.4861], road: 'Inner Ring Road', id: 'tol_nar' },
]

// Incident type → road impact severity color
const INCIDENT_ROAD_COLOR: Record<string, string> = {
  road_accident:        '#ef4444',
  road_blocked:         '#f97316',
  crowd_surge:          '#f97316',
  vehicle_stranded:     '#f97316',
  traffic_signal_issue: '#eab308',
  road_flood:           '#3b82f6',
  default:              '#ef4444',
}

export default function CityMap() {
  const mapRef = useRef<L.Map | null>(null)
  const containerRef = useRef<HTMLDivElement>(null)
  const zoneLayerRef = useRef<L.LayerGroup | null>(null)
  const resourceLayerRef = useRef<L.LayerGroup | null>(null)
  const infraLayerRef = useRef<L.LayerGroup | null>(null)
  const rakshakLayerRef = useRef<L.LayerGroup | null>(null)
  const trafficLayerRef = useRef<L.LayerGroup | null>(null)
  const zoomRef = useRef<number>(13)

  const snapshot = useCityStore(s => s.snapshot)
  const rakshakIncidents = useCityStore(s => s.rakshakIncidents)

  // Init map once
  useEffect(() => {
    if (!containerRef.current || mapRef.current) return

    const map = L.map(containerRef.current, {
      center: [17.3900, 78.4600],
      zoom: 13,
      zoomControl: false,
      attributionControl: false,
    })

    L.tileLayer('https://{s}.basemaps.cartocdn.com/dark_all/{z}/{x}/{y}{r}.png', {
      attribution: '© OpenStreetMap © CARTO',
      subdomains: 'abcd',
      maxZoom: 19,
    }).addTo(map)

    L.control.zoom({ position: 'bottomright' }).addTo(map)

    trafficLayerRef.current = L.layerGroup().addTo(map)
    zoneLayerRef.current = L.layerGroup().addTo(map)
    infraLayerRef.current = L.layerGroup().addTo(map)
    resourceLayerRef.current = L.layerGroup().addTo(map)
    rakshakLayerRef.current = L.layerGroup().addTo(map)

    map.on('zoomend', () => { zoomRef.current = map.getZoom() })

    mapRef.current = map
    return () => { map.remove(); mapRef.current = null }
  }, [])

  // Draw base road network with traffic flow
  useEffect(() => {
    if (!trafficLayerRef.current) return
    trafficLayerRef.current.clearLayers()

    // Gather which roads have active incidents
    const now = Date.now() / 1000
    const activeIncidents = rakshakIncidents.filter(i => now - i.timestamp < 300)

    ROAD_SEGMENTS.forEach(seg => {
      // Check if any active incident is near either endpoint of this segment
      const segLat = (seg.from[0] + seg.to[0]) / 2
      const segLng = (seg.from[1] + seg.to[1]) / 2

      const nearbyIncident = activeIncidents.find(inc => {
        const dlat = inc.lat - segLat, dlng = inc.lng - segLng
        return Math.sqrt(dlat * dlat + dlng * dlng) < 0.025
      })

      const roadColor = nearbyIncident
        ? (INCIDENT_ROAD_COLOR[nearbyIncident.incident_type] || INCIDENT_ROAD_COLOR.default)
        : '#1e3a5f'
      const weight = nearbyIncident ? 6 : 3
      const opacity = nearbyIncident ? 0.9 : 0.45

      // Base road line
      const line = L.polyline([seg.from as [number, number], seg.to as [number, number]], {
        color: roadColor,
        weight,
        opacity,
      })
      line.bindTooltip(
        nearbyIncident
          ? `<b style="color:${roadColor}">⚠ ${nearbyIncident.incident_type.replace(/_/g, ' ').toUpperCase()}</b><br/>${seg.road}`
          : seg.road,
        { sticky: true }
      )
      trafficLayerRef.current!.addLayer(line)

      // Traffic flow direction arrows (dashes flowing along road)
      if (!nearbyIncident) {
        const arrow = L.polyline([seg.from as [number, number], seg.to as [number, number]], {
          color: '#3b82f6',
          weight: 1,
          opacity: 0.3,
          dashArray: '4 8',
        })
        trafficLayerRef.current!.addLayer(arrow)
      } else {
        // Congestion pulse — wider translucent overlay on blocked road
        const pulse = L.polyline([seg.from as [number, number], seg.to as [number, number]], {
          color: roadColor,
          weight: 14,
          opacity: 0.15,
        })
        trafficLayerRef.current!.addLayer(pulse)

        // Direction arrows blocked (red Xs on endpoints)
        const midIcon = L.divIcon({
          html: `<div style="
            color:${roadColor};font-size:14px;font-weight:900;
            text-shadow:0 0 6px ${roadColor};
            background:rgba(0,0,0,0.7);border-radius:2px;
            padding:1px 3px;border:1px solid ${roadColor}
          ">BLOCKED</div>`,
          className: '',
          iconAnchor: [30, 8],
        })
        L.marker([segLat, segLng], { icon: midIcon }).addTo(trafficLayerRef.current!)
      }
    })
  }, [rakshakIncidents])

  // Update zone circles
  useEffect(() => {
    if (!snapshot || !zoneLayerRef.current) return
    zoneLayerRef.current.clearLayers()

    Object.values(snapshot.zones).forEach(zone => {
      const color = ZONE_COLORS[zone.status]
      const radius = zone.is_flooded ? 600 : 350
      const opacity = zone.is_flooded ? 0.35 : 0.15

      const circle = L.circle([zone.lat, zone.lng], {
        radius,
        color,
        fillColor: color,
        fillOpacity: opacity,
        weight: zone.is_flooded ? 3 : 1,
      })

      const footfall = snapshot.footfall?.[zone.id]
      const footfallStr = footfall !== undefined
        ? `<br/>Footfall: ~${footfall.toLocaleString()}`
        : ''

      circle.bindTooltip(`
        <div style="font-family:monospace;font-size:12px">
          <b>${zone.name}</b><br/>
          Status: <span style="color:${color}">${zone.status.toUpperCase()}</span><br/>
          Rainfall: ${zone.rainfall_mm_per_hour.toFixed(0)} mm/hr<br/>
          Water: ${zone.water_level_m.toFixed(2)}m
          ${zone.is_flooded ? '<br/><b style="color:#ef4444">⚠ FLOODED</b>' : ''}
          ${footfallStr}
        </div>
      `, { permanent: false, sticky: true })

      zoneLayerRef.current!.addLayer(circle)

      if (zone.status === 'critical' || zone.status === 'evacuating') {
        const pulse = L.circle([zone.lat, zone.lng], {
          radius: radius * 1.5,
          color, fillColor: color, fillOpacity: 0.04, weight: 1,
        })
        zoneLayerRef.current!.addLayer(pulse)
      }
    })
  }, [snapshot?.zones, snapshot?.tick, snapshot?.footfall])

  // Update infrastructure
  useEffect(() => {
    if (!snapshot || !infraLayerRef.current) return
    infraLayerRef.current.clearLayers()

    Object.values(snapshot.hospitals).forEach(hosp => {
      const color = !hosp.has_power ? '#ef4444' : hosp.backup_power_active ? '#f97316' : '#22c55e'
      const icon = L.divIcon({
        html: `<div style="
          background:${color};border-radius:50%;width:16px;height:16px;
          border:2px solid white;display:flex;align-items:center;justify-content:center;
          font-size:10px;color:white;font-weight:bold;box-shadow:0 0 6px ${color}
        ">H</div>`,
        className: '',
        iconSize: [16, 16],
        iconAnchor: [8, 8],
      })
      const marker = L.marker([hosp.lat, hosp.lng], { icon })
      marker.bindTooltip(`
        <b>${hosp.name}</b><br/>
        Power: ${hosp.has_power ? '✅' : '❌'}
        ${hosp.backup_power_active ? ' (Backup)' : ''}<br/>
        Accessible: ${hosp.accessible ? '✅' : '❌'}
      `, { sticky: true })
      infraLayerRef.current!.addLayer(marker)
    })

    Object.values(snapshot.substations).forEach(sub => {
      const color = sub.overloaded ? '#ef4444' : sub.online ? '#3b82f6' : '#6b7280'
      const icon = L.divIcon({
        html: `<div style="
          background:${color};width:14px;height:14px;border:2px solid white;
          display:flex;align-items:center;justify-content:center;font-size:9px;
          color:white;box-shadow:0 0 6px ${color};transform:rotate(45deg)
        ">⚡</div>`,
        className: '',
        iconSize: [14, 14],
        iconAnchor: [7, 7],
      })
      const marker = L.marker([sub.lat, sub.lng], { icon })
      marker.bindTooltip(`
        <b>${sub.name}</b><br/>
        Load: ${sub.load_mw.toFixed(0)}/${sub.max_load_mw.toFixed(0)} MW<br/>
        ${sub.overloaded ? '<b style="color:#ef4444">⚠ OVERLOADED</b>' : 'Status: OK'}
        ${sub.flood_risk ? '<br/>⚠ Flood risk zone' : ''}
      `, { sticky: true })
      infraLayerRef.current!.addLayer(marker)
    })
  }, [snapshot?.hospitals, snapshot?.substations, snapshot?.tick])

  // Update resource markers
  useEffect(() => {
    if (!snapshot || !resourceLayerRef.current) return
    resourceLayerRef.current.clearLayers()

    Object.values(snapshot.resources).forEach(res => {
      const emoji = RESOURCE_ICONS[res.type] || '📍'
      const opacity = res.status === 'available' ? 1.0 : 0.7
      const icon = L.divIcon({
        html: `<div style="font-size:18px;opacity:${opacity};
          filter:${res.status === 'en_route' ? 'drop-shadow(0 0 4px #fff)' : 'none'};
          transition:all 0.5s ease">${emoji}</div>`,
        className: '',
        iconSize: [20, 20],
        iconAnchor: [10, 10],
      })
      const marker = L.marker([res.lat, res.lng], { icon })
      marker.bindTooltip(`${res.id} (${res.type})<br/>Status: ${res.status}${res.assigned_to ? `<br/>→ ${res.assigned_to}` : ''}`, { sticky: true })
      resourceLayerRef.current!.addLayer(marker)
    })
  }, [snapshot?.resources, snapshot?.tick])


  // Rakshak incident markers — pinned ON the road centerline
  useEffect(() => {
    if (!rakshakLayerRef.current) return
    rakshakLayerRef.current.clearLayers()

    const now = Date.now() / 1000
    rakshakIncidents.filter(inc => now - inc.timestamp < 300).forEach((inc) => {
      const age = now - inc.timestamp
      const opacity = Math.max(0.4, 1 - age / 300)
      const roadColor = INCIDENT_ROAD_COLOR[inc.incident_type] || INCIDENT_ROAD_COLOR.default

      // Outer pulse circle
      const pulse = L.circle([inc.lat, inc.lng], {
        radius: 180,
        color: roadColor,
        fillColor: roadColor,
        fillOpacity: 0.12 * opacity,
        weight: 2,
        opacity: opacity * 0.6,
      })
      rakshakLayerRef.current!.addLayer(pulse)

      // Pin icon directly on road
      const icon = L.divIcon({
        html: `<div style="
          position:relative;
          background:${roadColor};
          border:2px solid white;
          border-radius:4px 4px 4px 0;
          padding:3px 6px;
          font-size:9px;color:white;font-weight:900;
          opacity:${opacity};
          white-space:nowrap;font-family:monospace;
          box-shadow:0 0 10px ${roadColor}88;
          transform:rotate(0deg);
          letter-spacing:0.5px;
        ">📹 ${inc.incident_type.replace(/_/g, ' ').toUpperCase()}<br/>
        <span style="font-size:8px;opacity:0.8;font-weight:400">
          ${(inc.confidence * 100).toFixed(0)}% · ${inc.camera_id.replace('cam_', '').toUpperCase()}
        </span></div>`,
        className: '',
        iconAnchor: [0, 28],
      })
      const marker = L.marker([inc.lat, inc.lng], { icon })
      marker.bindTooltip(`
        <b>Rakshak / YOLOv8n</b><br/>
        <b style="color:${roadColor}">${inc.incident_type.replace(/_/g, ' ').toUpperCase()}</b><br/>
        Camera: ${inc.camera_id}<br/>
        Confidence: ${(inc.confidence * 100).toFixed(0)}%<br/>
        Severity: ${inc.severity.toUpperCase()}<br/>
        ${inc.description}
      `, { sticky: true })
      rakshakLayerRef.current!.addLayer(marker)
    })
  }, [rakshakIncidents])

  const activeIncidentCount = rakshakIncidents.filter(i => Date.now() / 1000 - i.timestamp < 300).length

  return (
    <div style={{ width: '100%', height: '100%', position: 'relative', background: '#0f172a' }}>
      <div ref={containerRef} style={{ width: '100%', height: '100%' }} />
      <MapAlertOverlay />

      {activeIncidentCount > 0 && (
        <div style={{
          position: 'absolute', bottom: 40, left: 8,
          background: 'rgba(2,6,23,0.9)', border: '1px solid #1e293b',
          padding: '4px 10px', borderRadius: 4, fontSize: 10,
          fontFamily: 'monospace', pointerEvents: 'none',
        }}>
          <span style={{ color: '#ef4444' }}>📹 {activeIncidentCount} CCTV incident{activeIncidentCount > 1 ? 's' : ''} active</span>
        </div>
      )}
    </div>
  )
}
