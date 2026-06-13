import asyncio, sys
sys.path.insert(0, '.')
from backend.simulation.engine import SimulationEngine

async def test():
    engine = SimulationEngine()
    engine.trigger_scenario('flood_sept2024')
    for i in range(16):
        await engine._step()
        z = engine.state.zones['mehdipatnam_up']
        print(f'tick {engine.state.tick:2d}: rain={z.rainfall_mm_per_hour:.0f}mm/hr  status={z.status.value}  flooded={z.is_flooded}  scenario_tick={engine._scenario_tick}')

asyncio.run(test())
