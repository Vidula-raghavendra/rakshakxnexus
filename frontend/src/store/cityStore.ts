import { create } from 'zustand'

export type ZoneStatus = 'normal' | 'watch' | 'warning' | 'critical' | 'evacuating'

export interface ZoneState {
  id: string
  name: string
  lat: number
  lng: number
  status: ZoneStatus
  rainfall_mm_per_hour: number
  water_level_m: number
  is_flooded: boolean
  roads_blocked: string[]
  evacuation_progress: number
}

export interface SubstationState {
  id: string; name: string; lat: number; lng: number
  online: boolean; load_mw: number; max_load_mw: number
  flood_risk: boolean; overloaded: boolean
}

export interface HospitalState {
  id: string; name: string; lat: number; lng: number
  has_power: boolean; backup_power_active: boolean
  accessible: boolean; capacity_used: number
}

export interface ResourceState {
  id: string; type: string; lat: number; lng: number
  status: string; assigned_to: string | null
}

export interface Decision {
  id: string
  tick: number
  action_type: string
  confidence: number
  priority: 'low' | 'medium' | 'high' | 'critical'
  status: string
  human_readable: string
  reasoning: string
  reversible: boolean
  parameters: Record<string, unknown>
  timestamp?: number
}

export interface RakshakIncident {
  incident_type: string
  lat: number; lng: number
  confidence: number
  camera_id: string
  severity: string
  description: string
  zone_id: string | null
  timestamp: number
}

export interface AlertDispatch {
  incident_type: string
  severity: string
  timestamp: number
  alerts: Array<{
    dept: string
    dept_name: string
    to: string
    sms?: string
    call?: string
    status?: string
    reason?: string
  }>
}

export interface CascadeEvent {
  type: string; severity: string
  description?: string; hospital?: string; substation?: string
  lat?: number; lng?: number; timestamp: number
}

export interface Vehicle {
  id: string
  type: string
  zone_id: string
  lat: number
  lng: number
  speed: number
  status: 'moving' | 'slow' | 'stopped' | 'stranded' | 'evacuating'
}

export interface VehicleIncident {
  incident_type: string
  lat: number
  lng: number
  zone_id: string
  description: string
  severity: string
  vehicle_id: string
  timestamp: number
  source: string
}

export interface CitySnapshot {
  tick: number
  simulation_time: number
  scenario_active: string
  zones: Record<string, ZoneState>
  substations: Record<string, SubstationState>
  hospitals: Record<string, HospitalState>
  resources: Record<string, ResourceState>
  edge_congestion: Record<string, number>
  blocked_edges: string[]
  recent_decisions: Decision[]
  cascade_chain: CascadeEvent[]
  vehicles: Vehicle[]
  footfall: Record<string, number>
}

export interface AgentVote {
  agent_id: string
  agent_name: string
  recommended_action: string
  parameters: Record<string, unknown>
  confidence: number
  reasoning: string
  risk_flag: string
  priority: string
  timestamp: number
}

export interface AgentState {
  agent_id: string
  agent_name: string
  color: string
  is_thinking: boolean
  last_vote: AgentVote | null
}

export interface MapAlert {
  id: string
  type: 'rakshak' | 'vehicle' | 'cascade' | 'governor'
  lat: number
  lng: number
  title: string
  description: string
  severity: string
  timestamp: number
  details: Record<string, unknown>
}

interface CityStore {
  snapshot: CitySnapshot | null
  decisions: Decision[]
  rakshakIncidents: RakshakIncident[]
  alertDispatches: AlertDispatch[]
  vehicleIncidents: VehicleIncident[]
  mapAlerts: MapAlert[]
  agentStates: Record<string, AgentState>
  connected: boolean
  scenario: string
  pendingApprovals: Decision[]
  rulesMode: boolean
  rulesEngineResult: { alerts: number; status: string; reason: string; resolved: boolean } | null
  redTeamFired: boolean
  nexusStream: {
    session_id: string
    text: string
    thinking: boolean
    done: boolean
  } | null
  simulatedCounterfactual: {
    without_intervention: string
    with_intervention: string
    time_window_seconds: number
    lives_at_risk: string
    additional_stranded: number
    additional_power_out: number
  } | null

  setSnapshot: (s: CitySnapshot) => void
  addDecision: (d: Decision) => void
  addRakshakIncident: (i: RakshakIncident) => void
  addAlertDispatch: (a: AlertDispatch) => void
  addVehicleIncident: (i: VehicleIncident) => void
  addMapAlert: (a: MapAlert) => void
  dismissAlert: (id: string) => void
  setAgentStates: (states: AgentState[]) => void
  updateAgentVote: (vote: AgentVote) => void
  setConnected: (v: boolean) => void
  setScenario: (s: string) => void
  setPendingApprovals: (list: Decision[]) => void
  setRulesMode: (v: boolean) => void
  setRulesEngineResult: (r: CityStore['rulesEngineResult']) => void
  setRedTeamFired: (v: boolean) => void
  appendStreamChunk: (chunk: { session_id: string; chunk: string; thinking?: boolean; done?: boolean }) => void
  setSimulatedCounterfactual: (c: CityStore['simulatedCounterfactual']) => void
}

const DEFAULT_AGENTS: Record<string, AgentState> = {
  emergency:      { agent_id: 'emergency',      agent_name: 'Emergency Agent',      color: '#ef4444', is_thinking: false, last_vote: null },
  infrastructure: { agent_id: 'infrastructure', agent_name: 'Infrastructure Agent', color: '#3b82f6', is_thinking: false, last_vote: null },
  adversary:      { agent_id: 'adversary',      agent_name: 'Adversary Agent',      color: '#f97316', is_thinking: false, last_vote: null },
  simulation:     { agent_id: 'simulation',     agent_name: 'Simulation Agent',     color: '#a855f7', is_thinking: false, last_vote: null },
}

export const useCityStore = create<CityStore>((set) => ({
  snapshot: null,
  decisions: [],
  rakshakIncidents: [],
  alertDispatches: [],
  vehicleIncidents: [],
  mapAlerts: [],
  agentStates: DEFAULT_AGENTS,
  connected: false,
  scenario: 'normal',
  pendingApprovals: [],
  rulesMode: false,
  rulesEngineResult: null,
  redTeamFired: false,
  nexusStream: null,
  simulatedCounterfactual: null,

  setSnapshot: (s) => set({ snapshot: s, scenario: s.scenario_active }),
  addDecision: (d) => set((state) => ({
    decisions: [d, ...state.decisions].slice(0, 100),
    pendingApprovals: d.status === 'pending_approval'
      ? [d, ...state.pendingApprovals]
      : state.pendingApprovals.filter(p => p.id !== d.id),
  })),
  addAlertDispatch: (a) => set((state) => ({
    alertDispatches: [a, ...state.alertDispatches].slice(0, 20),
  })),
  addRakshakIncident: (i) => set((state) => {
    const now = Date.now() / 1000
    // Remove stale (>5min) and dedupe same camera+type within 30s
    const filtered = state.rakshakIncidents.filter(existing =>
      now - existing.timestamp < 300 &&
      !(existing.camera_id === i.camera_id && existing.incident_type === i.incident_type && now - existing.timestamp < 30)
    )
    return { rakshakIncidents: [i, ...filtered].slice(0, 50) }
  }),
  addVehicleIncident: (i) => set((state) => ({
    vehicleIncidents: [i, ...state.vehicleIncidents].slice(0, 100),
  })),
  addMapAlert: (a) => set((state) => ({
    mapAlerts: [a, ...state.mapAlerts].slice(0, 20),
  })),
  dismissAlert: (id) => set((state) => ({
    mapAlerts: state.mapAlerts.filter(a => a.id !== id),
  })),
  setAgentStates: (states) => set({
    agentStates: Object.fromEntries(states.map(s => [s.agent_id, s])),
  }),
  updateAgentVote: (vote) => set((state) => ({
    agentStates: {
      ...state.agentStates,
      [vote.agent_id]: {
        ...(state.agentStates[vote.agent_id] ?? DEFAULT_AGENTS[vote.agent_id]),
        last_vote: vote,
        is_thinking: false,
      },
    },
  })),
  setConnected: (v) => set({ connected: v }),
  setScenario: (s) => set({ scenario: s }),
  setPendingApprovals: (list) => set({ pendingApprovals: list }),
  setRulesMode: (v) => set({ rulesMode: v }),
  setRulesEngineResult: (r) => set({ rulesEngineResult: r }),
  setRedTeamFired: (v) => set({ redTeamFired: v }),
  appendStreamChunk: (msg) => set((state) => {
    if ((msg as any).error) {
      // Show clean error briefly then clear — don't leave raw API errors on screen
      setTimeout(() => set({ nexusStream: null }), 4000)
      return { nexusStream: { session_id: msg.session_id, text: msg.chunk, thinking: false, done: true } }
    }
    if (msg.thinking) {
      return { nexusStream: { session_id: msg.session_id, text: '', thinking: true, done: false } }
    }
    if (msg.done) {
      return { nexusStream: state.nexusStream ? { ...state.nexusStream, thinking: false, done: true } : null }
    }
    const prev = state.nexusStream
    if (!prev || prev.session_id !== msg.session_id) {
      return { nexusStream: { session_id: msg.session_id, text: msg.chunk, thinking: false, done: false } }
    }
    return { nexusStream: { ...prev, text: prev.text + msg.chunk, thinking: false } }
  }),
  setSimulatedCounterfactual: (c) => set({ simulatedCounterfactual: c }),
}))
