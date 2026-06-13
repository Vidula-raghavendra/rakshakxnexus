"""Minimal test: run engine for 20 ticks with flood scenario, no FastAPI."""
import asyncio, sys
sys.path.insert(0, '.')

from backend.simulation.engine import SimulationEngine

snapshots = []

async def on_tick(state):
    z = state.zones.get('mehdipatnam_up')
    if z:
        snapshots.append({
            'tick': state.tick,
            'rain': z.rainfall_mm_per_hour,
            'status': z.status.value,
            'flooded': z.is_flooded,
            'cascade': len(state.cascade_chain),
        })

async def main():
    engine = SimulationEngine()
    engine.on_tick(on_tick)
    engine.tick_interval = 0.05  # fast for test
    engine.trigger_scenario('flood_sept2024')

    task = asyncio.create_task(engine.start())
    await asyncio.sleep(1.5)  # 30 ticks at 0.05s each
    engine.stop()
    task.cancel()

    for s in snapshots:
        print(f"tick {s['tick']:2d}: rain={s['rain']:.0f}  status={s['status']}  flooded={s['flooded']}  cascade={s['cascade']}")

asyncio.run(main())
