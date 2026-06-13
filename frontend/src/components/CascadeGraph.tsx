import { useEffect, useRef } from 'react'
import * as d3 from 'd3'
import { useCityStore } from '../store/cityStore'

const SEVERITY_COLORS: Record<string, string> = {
  critical: '#ef4444',
  high:     '#f97316',
  warning:  '#eab308',
  medium:   '#3b82f6',
  low:      '#22c55e',
}

const EVENT_LABELS: Record<string, string> = {
  power_loss:              '⚡ POWER LOST',
  backup_power_activated:  '🔋 BACKUP ON',
  road_flooded:            '🌊 ROAD FLOODED',
  vehicle_stranded:        '🚗 STRANDED',
  rakshak_road_flood:      '📹 FLOOD DET.',
  rakshak_vehicle_stranded:'📹 STRANDED',
  rakshak_crowd_surge:     '📹 CROWD',
  rakshak_road_blocked:    '📹 BLOCKED',
}

export default function CascadeGraph() {
  const svgRef = useRef<SVGSVGElement>(null)
  const snapshot = useCityStore(s => s.snapshot)

  useEffect(() => {
    const svg = d3.select(svgRef.current!)
    svg.selectAll('*').remove()

    const cascade = snapshot?.cascade_chain
    if (!cascade || cascade.length === 0) {
      svg.append('text')
        .attr('x', '50%').attr('y', '55%')
        .attr('text-anchor', 'middle')
        .attr('fill', '#1e293b').attr('font-size', 10).attr('font-family', 'monospace')
        .text('No cascade — city stable')
      return
    }

    const { width, height } = svgRef.current!.getBoundingClientRect()
    const W = width || 300
    const H = height || 150

    const events = cascade.slice(-8)
    const nodeR = 18
    const spacing = Math.min(60, (W - 40) / Math.max(events.length, 1))

    // Arrow marker
    const defs = svg.append('defs')
    defs.append('marker')
      .attr('id', 'cg-arrow')
      .attr('viewBox', '0 -4 8 8')
      .attr('refX', 7).attr('refY', 0)
      .attr('markerWidth', 5).attr('markerHeight', 5)
      .attr('orient', 'auto')
      .append('path').attr('d', 'M0,-4L8,0L0,4').attr('fill', '#334155')

    const startX = 28
    const cy = H / 2

    // Draw connections
    for (let i = 0; i < events.length - 1; i++) {
      const x1 = startX + i * spacing + nodeR
      const x2 = startX + (i + 1) * spacing - nodeR
      svg.append('line')
        .attr('x1', x1).attr('y1', cy)
        .attr('x2', x2).attr('y2', cy)
        .attr('stroke', '#334155').attr('stroke-width', 1)
        .attr('marker-end', 'url(#cg-arrow)')
    }

    // Draw nodes
    events.forEach((ev: any, i) => {
      const cx = startX + i * spacing
      const color = SEVERITY_COLORS[ev.severity] || '#475569'
      const label = EVENT_LABELS[ev.type] || ev.type.replace(/_/g, ' ').substring(0, 12)

      const g = svg.append('g').attr('transform', `translate(${cx},${cy})`)

      // Glow for critical
      if (ev.severity === 'critical') {
        g.append('circle')
          .attr('r', nodeR + 4).attr('fill', color).attr('opacity', 0.12)
      }

      g.append('circle')
        .attr('r', nodeR)
        .attr('fill', '#0a0f1e')
        .attr('stroke', color)
        .attr('stroke-width', ev.severity === 'critical' ? 2 : 1)

      // Icon/severity dot
      g.append('circle')
        .attr('r', 4).attr('cy', -4)
        .attr('fill', color)

      // Label below
      g.append('text')
        .attr('y', nodeR + 11)
        .attr('text-anchor', 'middle')
        .attr('fill', color)
        .attr('font-size', 7.5)
        .attr('font-family', 'monospace')
        .text(label)

      // Sub-label (hospital or substation name)
      const sub = ev.hospital || ev.substation || ''
      if (sub) {
        g.append('text')
          .attr('y', nodeR + 20)
          .attr('text-anchor', 'middle')
          .attr('fill', '#334155')
          .attr('font-size', 7)
          .attr('font-family', 'monospace')
          .text(sub.replace(' Hospital', '').replace(' Substation', '').substring(0, 10))
      }
    })

    // Event count label
    svg.append('text')
      .attr('x', W - 6).attr('y', 12)
      .attr('text-anchor', 'end')
      .attr('fill', '#334155').attr('font-size', 9).attr('font-family', 'monospace')
      .text(`${cascade.length} events`)

  }, [snapshot?.cascade_chain, snapshot?.tick])

  return (
    <div style={{ height: '100%', background: '#020617', display: 'flex', flexDirection: 'column' }}>
      <div style={{
        padding: '5px 10px', borderBottom: '1px solid #0f172a',
        fontSize: 9, color: '#334155', textTransform: 'uppercase', letterSpacing: 2,
        display: 'flex', justifyContent: 'space-between',
      }}>
        <span>Cascade Chain</span>
        {(snapshot?.cascade_chain?.length ?? 0) > 0 && (
          <span style={{ color: '#ef4444' }}>ACTIVE</span>
        )}
      </div>
      <svg ref={svgRef} style={{ flex: 1, width: '100%' }} />
    </div>
  )
}
