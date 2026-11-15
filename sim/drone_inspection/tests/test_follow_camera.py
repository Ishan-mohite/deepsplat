import unittest
import numpy as np

from inspection_sim.config import Config
from inspection_sim.coordinates import camera_pose, intrinsics, look_at
from inspection_sim.rendering import ChaseRenderer, Observation, draw_camera_pose


class FollowCameraTests(unittest.TestCase):
    def test_reuses_scene_renderer_and_shortens_boom_at_wall(self):
        class Renderer:
            def capture(self, position, rotation, timestamp):
                self.position = np.asarray(position)
                return Observation('test', timestamp, np.full((240, 320, 3), 100, dtype=np.uint8),
                                   np.full((240, 320), 10.), position, rotation, intrinsics(320, 240, 90))

        class Physics:
            def segment_clear(self, start, end):
                return end[0] >= -.7

        renderer = Renderer()
        config = Config(resolution=[320, 240])
        position = np.array([0., 0., 1.3])
        lens, rotation = camera_pose(position, [0, 0, 0, 1])
        chase = ChaseRenderer(renderer, config, Physics())
        observation = chase.capture_chase(position, 0, 0, 1.6, .45, 0, lens, rotation,
                                         settings={'show_urdf': False, 'show_fov': True, 'show_trajectory': False})
        self.assertIs(chase.renderer, renderer)
        self.assertGreaterEqual(renderer.position[0], -.7)
        self.assertLess(renderer.position[0], lens[0])
        self.assertLess(np.linalg.norm(renderer.position - lens), 1.6)
        self.assertEqual(observation.rgb.shape, (240, 320, 3))
        self.assertTrue(np.any(np.all(observation.rgb == [126, 162, 255], axis=2)))

    def test_pose_marker_is_occluded_by_nearby_surface(self):
        rgb = np.full((240, 320, 3), 100, dtype=np.uint8)
        eye = np.array([-1., 0., 1.3])
        lens, rotation = camera_pose([0, 0, 1.3], [0, 0, 0, 1])
        view = Observation('test', 0, rgb, np.full((240, 320), .1),
                           eye, look_at(eye, lens), intrinsics(320, 240, 90))
        output = draw_camera_pose(view, lens, rotation, 90)
        # The screen-space legend remains, while the world-space marker is hidden.
        np.testing.assert_array_equal(output[:200], rgb[:200])
        np.testing.assert_array_equal(rgb, np.full((240, 320, 3), 100, dtype=np.uint8))


if __name__ == '__main__':
    unittest.main()
