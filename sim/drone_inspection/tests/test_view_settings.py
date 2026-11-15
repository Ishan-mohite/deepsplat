import unittest

from inspection_sim.config import Config, ROOT
from inspection_sim.engine import Simulation


class ViewSettingsTests(unittest.TestCase):
    def setUp(self):
        config = Config.load(ROOT / 'configs/demo.json')
        config.renderer = 'bullet'
        self.sim = Simulation(config, ui_watchdog=False)
        self.sim.initialize()

    def tearDown(self):
        self.sim.physics.close()

    def test_pose_visibility_invariant_and_reenabling(self):
        sim = self.sim
        self.assertEqual(sim.read()['view_settings'], {'show_urdf': True, 'show_fov': False, 'show_trajectory': False})
        with self.assertRaisesRegex(ValueError, 'at least one'):
            sim.apply({'type': 'view', 'show_urdf': False})
        sim.apply({'type': 'view', 'show_fov': True})
        sim.apply({'type': 'view', 'show_urdf': False, 'show_trajectory': True})
        sim.render()
        self.assertFalse(sim.read()['view_settings']['show_urdf'])
        with self.assertRaisesRegex(ValueError, 'at least one'):
            sim.apply({'type': 'view', 'show_fov': False})
        sim.apply({'type': 'view', 'show_urdf': True})
        sim.apply({'type': 'view', 'show_fov': False})
        sim.render()
        self.assertTrue(sim.read()['view_settings']['show_urdf'])

    def test_invalid_values_do_not_change_settings(self):
        before = dict(self.sim.view_settings)
        for command in [{'type': 'view', 'show_fov': 1}, {'type': 'view', 'unknown': True}]:
            with self.assertRaises(ValueError):
                self.sim.apply(command)
            self.assertEqual(self.sim.view_settings, before)


if __name__ == '__main__':
    unittest.main()
