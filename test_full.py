"""Test full server broadcast pipeline."""
import asyncio, sys, json
sys.path.insert(0, '.')

from backend.simulation.engine import SimulationEngine
from backend.core.city_state import CityState

engine = SimulationEngine()

async def broadcast_city_state(state: CityState):
    try:
        snapshot = state.to_snapshot()
        msg = json.dumps({"type": "city_state", "data": snapshot})
        print(f"tick {state.tick}: JSON OK, len={len(msg)}")
    except Exception as e:
        print(f"tick {state.tick}: JSON ERROR — {e}")
        import traceback; traceback.print_exc()

async def main():
    engine.on_tick(broadcast_city_state)
    engine.tick_interval = 0.05
    engine.trigger_scenario('flood_sept2024')
    task = asyncio.create_task(engine.start())
    await asyncio.sleep(0.8)
    engine.stop()
    task.cancel()

asyncio.run(main())
