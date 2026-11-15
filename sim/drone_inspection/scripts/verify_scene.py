"""Render a scene for visual QA and check camera/physics depth alignment.

Run from drone_inspection: python scripts/verify_scene.py --config configs/hm3d.json
"""
import argparse
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import numpy as np
from PIL import Image, ImageDraw
from scipy.spatial.transform import Rotation

from inspection_sim.config import Config, ROOT
from inspection_sim.coordinates import camera_pose
from inspection_sim.engine import Simulation
from inspection_sim.rendering import BulletRenderer


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--config', required=True)
    parser.add_argument('--renderer', choices=['habitat', 'bullet'])
    parser.add_argument('--output', type=Path, default=ROOT / '.cache/visual-check')
    args = parser.parse_args()
    config = Config.load(args.config)
    if args.renderer:
        config.renderer = args.renderer
    args.output.mkdir(parents=True, exist_ok=True)
    simulation = Simulation(config, ui_watchdog=False)
    report = {'renderer': config.renderer, 'views': [], 'passed': True}
    try:
        simulation.initialize()
        report['spawn'] = simulation.geometry.report['spawn']
        position = simulation.physics.state()[0]
        reference = BulletRenderer(simulation.geometry, config, simulation.physics)
        width, height = config.resolution
        montage = Image.new('RGB', (2 * width, 2 * (height + 40)), 'white')
        draw = ImageDraw.Draw(montage)
        initial_yaw = Rotation.from_quat(config.initial_quaternion_xyzw).as_euler('xyz')[2]
        for index in range(4):
            yaw = initial_yaw + index * np.pi / 2
            eye, rotation = camera_pose(position, Rotation.from_euler('z', yaw).as_quat())
            frame = simulation.renderer.capture(eye, rotation, 0)
            assert frame.rgb.shape == (height, width, 3), 'Invalid RGB image dimensions'
            assert frame.depth.shape == (height, width), 'Invalid depth image dimensions'
            rgb = frame.rgb.astype(np.uint8)
            gray = rgb.mean(axis=2)
            valid = np.isfinite(frame.depth)
            sample = {'yaw_degrees': index * 90, 'mean': float(gray.mean()),
                      'contrast': float(gray.std()), 'black_fraction': float((gray < 5).mean()),
                      'valid_depth_fraction': float(valid.mean())}
            bullet = reference.capture(eye, rotation, 0)
            overlap = valid & np.isfinite(bullet.depth)
            sample['depth_overlap_fraction'] = float(overlap.mean())
            error = np.abs(frame.depth[overlap] - bullet.depth[overlap])
            sample['median_depth_error_m'] = float(np.median(error)) if error.size else None
            sample['passed'] = bool(sample['black_fraction'] < .2 and sample['contrast'] > 5
                                    and sample['valid_depth_fraction'] > .8
                                    and sample['depth_overlap_fraction'] > .8
                                    and sample['median_depth_error_m'] is not None
                                    and sample['median_depth_error_m'] < .05)
            report['passed'] &= sample['passed']
            report['views'].append(sample)
            image = Image.fromarray(rgb)
            image.save(args.output / ('onboard-%d.png' % (index * 90)))
            x = (index % 2) * width
            y = (index // 2) * (height + 40)
            montage.paste(image, (x, y + 40))
            draw.text((x + 8, y + 12), 'Onboard · %d degrees' % (index * 90), fill='black')
            print(json.dumps(sample), flush=True)
        montage.save(args.output / 'camera-montage.png')
        for view, data in simulation.images.items():
            (args.output / ('initial-%s.jpg' % view)).write_bytes(data)
        (args.output / 'report.json').write_text(json.dumps(report, indent=2, allow_nan=False))
        print('Visual QA artifacts:', args.output, flush=True)
        if not report['passed']:
            raise SystemExit('Visual QA failed: inspect the saved frames and report.')
    finally:
        if simulation.renderer:
            simulation.renderer.close()
        if simulation.physics:
            simulation.physics.close()


if __name__ == '__main__':
    main()
