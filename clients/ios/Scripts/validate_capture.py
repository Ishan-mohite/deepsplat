#!/usr/bin/env python3
"""Read-only inspection of an exported DeepSplat folder or ZIP. Standard library only."""
import argparse
import csv
import io
import json
import math
from pathlib import Path, PurePosixPath
import statistics
import struct
import zipfile


def jpeg_metadata(data):
    if not data.startswith(b'\xff\xd8'):
        raise ValueError('Not a JPEG')
    size = None
    orientation_tag = False
    offset = 2
    while offset + 4 <= len(data):
        if data[offset] != 0xff:
            break
        while offset < len(data) and data[offset] == 0xff:
            offset += 1
        marker = data[offset]
        offset += 1
        if marker in (0xd9, 0xda):
            break
        if marker == 0x01 or 0xd0 <= marker <= 0xd7:
            continue
        length = int.from_bytes(data[offset:offset + 2], 'big')
        if length < 2 or offset + length > len(data):
            raise ValueError('Truncated JPEG segment')
        payload = data[offset + 2:offset + length]
        if marker in (0xc0, 0xc1, 0xc2, 0xc3, 0xc5, 0xc6, 0xc7, 0xc9, 0xca, 0xcb, 0xcd, 0xce, 0xcf):
            height, width = struct.unpack('>HH', payload[1:5])
            size = (width, height)
        if marker == 0xe1 and payload.startswith(b'Exif\x00\x00'):
            tiff = payload[6:]
            endian = '<' if tiff[:2] == b'II' else '>'
            directory = struct.unpack_from(endian + 'I', tiff, 4)[0]
            count = struct.unpack_from(endian + 'H', tiff, directory)[0]
            for index in range(count):
                tag = struct.unpack_from(endian + 'H', tiff, directory + 2 + index * 12)[0]
                orientation_tag |= tag == 0x112
        offset += length
    if size is None:
        raise ValueError('JPEG has no image dimensions')
    return size, orientation_tag


def validate(path):
    archive = None
    if path.is_dir():
        base = path
        if not (base / 'capture.json').exists():
            candidates = list(base.rglob('capture.json'))
            if len(candidates) != 1:
                raise ValueError('Expected exactly one capture.json')
            base = candidates[0].parent
        read = lambda name: (base / name).read_bytes()
    else:
        archive = zipfile.ZipFile(path)
        candidates = [name for name in archive.namelist() if PurePosixPath(name).name == 'capture.json']
        if len(candidates) != 1:
            raise ValueError('ZIP must contain exactly one capture.json')
        prefix = candidates[0][:-len('capture.json')]
        read = lambda name: archive.read(prefix + name)
    try:
        manifest = json.loads(read('capture.json'))
        assert manifest['schema_version'] == 1, 'Unsupported schema'
        assert manifest['map_id'].strip(), 'Missing map ID'
        frames = manifest['video']['frames']
        assert frames, 'No video frames'
        for frame in frames + [manifest['photo']]:
            file = PurePosixPath(frame['file'])
            assert not file.is_absolute() and '..' not in file.parts, 'Unsafe file path'
            assert frame['interface_orientation'] == 'portrait'
            assert len(frame['camera_to_world']) == 16
            assert all(math.isfinite(x) for x in frame['camera_to_world'])
            assert math.isfinite(frame['t'])
            intrinsics = frame['intrinsics']
            size, orientation = jpeg_metadata(read(frame['file']))
            assert size == (intrinsics['width'], intrinsics['height']), f'{file}: intrinsics/image size mismatch'
            assert not orientation, f'{file}: unexpected EXIF orientation tag'
            assert intrinsics['fx'] > 0 and intrinsics['fy'] > 0
            assert 0 < intrinsics['cx'] < size[0] and 0 < intrinsics['cy'] < size[1]
        times = [f['t'] for f in frames]
        gaps = [b - a for a, b in zip(times, times[1:])]
        assert all(gap > 0 for gap in gaps), 'Frames are not monotonic'
        print(f"Capture {manifest['capture_id']} · map {manifest['map_id']}")
        print(f"Frames: {len(frames)} · duration {manifest['video']['duration_s']:.2f} s")
        if gaps:
            target = 1 / manifest['video']['fps_target']
            tolerance = 1 / manifest['video']['source_fps'] + 0.001
            bad = sum(abs(gap - target) > tolerance for gap in gaps)
            print(f'Frame gaps: median {statistics.median(gaps):.4f} s · {bad} gaps outside target ± one source frame')
        counts = {}
        for kind, filename in manifest['imu']['files'].items():
            rows = list(csv.DictReader(io.StringIO(read(filename).decode())))
            assert rows, f'No {kind} IMU rows'
            numbers = [[float(value) for value in row.values()] for row in rows]
            assert all(math.isfinite(value) for row in numbers for value in row)
            imu_times = [float(row['t']) for row in rows]
            assert all(b > a for a, b in zip(imu_times, imu_times[1:])), f'{kind} timestamps not monotonic'
            counts[kind] = len(rows)
            expected = manifest['imu'].get('sample_counts', {}).get(kind)
            assert expected is None or expected == len(rows), f'{kind} count disagrees with manifest'
            rate = (len(rows) - 1) / (imu_times[-1] - imu_times[0]) if len(rows) > 1 else 0
            print(f'{kind}: {len(rows)} rows · {rate:.1f} Hz · uptime {imu_times[0]:.3f}–{imu_times[-1]:.3f}')
        print('Photo:', 'high resolution' if manifest['photo']['high_res'] else 'logged camera-frame fallback')
        print('GPS:', 'present' if manifest['geo'] else 'not recorded')
        print('PASS: file layout, JPEG dimensions/orientation, matrices, timestamps and IMU counts')
    finally:
        if archive:
            archive.close()


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('capture', type=Path)
    args = parser.parse_args()
    try:
        validate(args.capture)
    except (AssertionError, ValueError, KeyError, OSError, struct.error, zipfile.BadZipFile) as error:
        parser.exit(1, f'FAIL: {error}\n')
