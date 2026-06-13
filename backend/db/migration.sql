-- NEXUS Supabase schema
-- Apply via: mcp__supabase__apply_migration (name: nexus_initial_schema)

create table if not exists nexus_scenario_runs (
  id            uuid primary key default gen_random_uuid(),
  scenario      text not null,
  status        text not null default 'running',  -- running | completed
  summary       jsonb,
  created_at    timestamptz not null default now(),
  updated_at    timestamptz not null default now()
);

create table if not exists nexus_decisions (
  id                  uuid primary key default gen_random_uuid(),
  decision_id         text,
  scenario_run_id     uuid references nexus_scenario_runs(id),
  tick                integer,
  action_type         text,
  confidence          float,
  priority            text,
  status              text,
  human_readable      text,
  governor_reasoning  text,
  agent_votes         jsonb,
  conflicts           jsonb,
  counterfactual      jsonb,
  reversible          boolean,
  parameters          jsonb,
  created_at          timestamptz not null default now()
);

create table if not exists nexus_incidents (
  id               uuid primary key default gen_random_uuid(),
  scenario_run_id  uuid references nexus_scenario_runs(id),
  incident_type    text,
  camera_id        text,
  lat              float,
  lng              float,
  confidence       float,
  severity         text,
  description      text,
  zone_id          text,
  created_at       timestamptz not null default now()
);

-- Indexes for dashboard queries
create index if not exists nexus_decisions_run_idx   on nexus_decisions(scenario_run_id, created_at desc);
create index if not exists nexus_incidents_run_idx   on nexus_incidents(scenario_run_id, created_at desc);
create index if not exists nexus_incidents_sev_idx   on nexus_incidents(severity);

-- Enable realtime so the frontend can subscribe directly if needed
alter publication supabase_realtime add table nexus_decisions;
alter publication supabase_realtime add table nexus_incidents;
