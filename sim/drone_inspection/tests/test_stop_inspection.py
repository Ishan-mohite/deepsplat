import json
import unittest
import numpy as np

from inspection_sim.config import Config, ROOT
from inspection_sim.engine import Simulation


class StopInspectionTests(unittest.TestCase):
    def setUp(self):
        config = Config.load(ROOT / 'configs/demo.json')
        config.renderer = 'bullet'
        self.sim = Simulation(config, ui_watchdog=False)
        self.sim.initialize()

    def tearDown(self):
        if self.sim.renderer:
            self.sim.renderer.close()
        if self.sim.physics:
            self.sim.physics.close()

    def test_stop_resets_progress_without_moving_drone_and_allows_manual_flight(self):
        sim = self.sim
        sim.apply({'type': 'start', 'mode': 'autonomous'})
        sim.coverage.covered[:100] = True
        sim.coverage.unreachable[100:200] = True
        sim.planner.decisions = 7
        folder = sim.run_folder
        sim.apply({'type': 'pause'})
        pose = sim.physics.state()[:2]
        sim.apply({'type': 'cancel'})
        self.assertEqual(sim.state, 'idle')
        self.assertEqual(sim.mode, 'manual')
        self.assertFalse(sim.paused)
        self.assertFalse(sim.coverage.started)
        self.assertEqual(sim.read()['elapsed'], 0)
        self.assertEqual(sim.read()['planner_decisions'], 0)
        np.testing.assert_array_equal(sim.physics.state()[0], pose[0])
        np.testing.assert_array_equal(sim.physics.state()[1], pose[1])
        report = json.loads((folder / 'run.json').read_text())
        self.assertGreater(report['coverage']['inspected_area_m2'], 0)
        self.assertEqual(report['completion_reason'], 'cancelled')
        for step in range(480):
            if step % 5 == 0:
                sim.apply({'type': 'velocity', 'value': [.2, 0, 0, 0]})
                sim.control()
            sim.physics.step()
            self.assertFalse(sim.physics.collided)
        sim.render()
        sim.publish()
        self.assertGreater(sim.physics.state()[0][0] - pose[0][0], .2)
        self.assertEqual(sim.read()['coverage']['inspected_area_m2'], 0)
        self.assertEqual(sim.read()['coverage']['unreachable_area_m2'], 0)
        self.assertEqual(sim.read()['elapsed'], 0)
        self.assertEqual(sim.read()['planner_decisions'], 0)
        sim.apply({'type': 'start', 'mode': 'autonomous'})
        self.assertTrue(sim.coverage.started)
        self.assertFalse(sim.manual_override)
        self.assertEqual(sim.state, 'running')

    def test_stop_does_not_release_emergency_stop(self):
        sim = self.sim
        sim.apply({'type': 'emergency_stop'})
        sim.apply({'type': 'cancel'})
        self.assertTrue(sim.emergency)
        self.assertTrue(sim.paused)
        self.assertFalse(sim.coverage.started)


if __name__ == '__main__':
    unittest.main()
