import importlib.util
import io
import json
from pathlib import Path
import struct
import tempfile
import unittest
from contextlib import redirect_stdout
import zipfile

spec = importlib.util.spec_from_file_location('validator', Path(__file__).with_name('validate_capture.py'))
validator = importlib.util.module_from_spec(spec)
spec.loader.exec_module(validator)


class ValidatorTests(unittest.TestCase):
    def jpeg(self, width=1920, height=1440):
        # Metadata-only fixture for the marker parser; camera JPEG encoding is verified on device.
        payload = struct.pack('>BHHB', 8, height, width, 1) + bytes([1, 0x11, 0])
        return b'\xff\xd8\xff\xc0' + struct.pack('>H', len(payload) + 2) + payload + b'\xff\xd9'

    def test_jpeg_dimensions_and_no_orientation(self):
        self.assertEqual(validator.jpeg_metadata(self.jpeg()), ((1920, 1440), False))
        with self.assertRaises(ValueError):
            validator.jpeg_metadata(b'not JPEG')

    def test_wrapped_zip_and_corrupt_dimensions(self):
        frame = dict(file='frames/000001.jpg', t=100.0, camera_to_world=[1, 0, 0, 0, 0, 1, 0, 0, 0, 0, 1, 0, 0, 0, 0, 1],
                     intrinsics=dict(fx=1400, fy=1400, cx=960, cy=720, width=1920, height=1440), interface_orientation='portrait')
        photo = dict(frame, file='photo.jpg', high_res=True)
        manifest = dict(schema_version=1, capture_id='test', map_id='room', video=dict(frames=[frame], duration_s=1, fps_target=5, source_fps=60),
                        photo=photo, imu=dict(files={kind: f'imu/{kind}.csv' for kind in ['accel', 'gyro', 'motion']},
                                             sample_counts=dict(accel=2, gyro=2, motion=2)), geo=None)
        with tempfile.TemporaryDirectory() as directory:
            archive = Path(directory) / 'capture.zip'
            def write(width):
                with zipfile.ZipFile(archive, 'w') as output:
                    output.writestr('UUID/capture.json', json.dumps(manifest))
                    for name in ['photo.jpg', 'frames/000001.jpg']:
                        output.writestr('UUID/' + name, self.jpeg(width))
                    for kind in ['accel', 'gyro', 'motion']:
                        output.writestr(f'UUID/imu/{kind}.csv', 't,x\n100,0\n100.01,0\n')
            write(1920)
            with redirect_stdout(io.StringIO()):
                validator.validate(archive)
            write(1000)
            with self.assertRaisesRegex(AssertionError, 'size mismatch'):
                validator.validate(archive)


if __name__ == '__main__':
    unittest.main()
