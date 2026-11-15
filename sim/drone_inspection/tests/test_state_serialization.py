"""Published state must work with Starlette's standard JSON encoder."""
import json
from pathlib import Path
import tempfile
import unittest

from inspection_sim.config import Config, ROOT
from inspection_sim.engine import Simulation


class StateSerializationTests(unittest.TestCase):
    def test_spawn_state_and_saved_run_are_json_serializable(self):
        config = Config.load(ROOT / 'configs/demo.json')
        config.renderer = 'bullet'
        simulation = Simulation(config, ui_watchdog=False)
        try:
            simulation.initialize()
            state = json.loads(json.dumps(simulation.read(), allow_nan=False))
            self.assertTrue(state['ready'])
            self.assertEqual(len(state['scene']['spawn']['grid_cell']), 3)
            self.assertGreater(state['scene']['spawn']['reachable_neighbors'], 0)
            with tempfile.TemporaryDirectory() as folder:
                simulation.run_folder = Path(folder)
                simulation.save_run()
                report = json.loads((Path(folder) / 'run.json').read_text())
                self.assertEqual(report['scene']['spawn'], state['scene']['spawn'])
        finally:
            if simulation.renderer:
                simulation.renderer.close()
            if simulation.physics:
                simulation.physics.close()


if __name__ == '__main__':
    unittest.main()
