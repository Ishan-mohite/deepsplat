"""Replay a video through SAM 3 and export crack observations."""
import argparse
import json
from itertools import count
from pathlib import Path
import time

import cv2
import numpy as np
from PIL import Image


def project(pixel, intrinsics, pose, plane):
    """Intersect an OpenCV camera ray with a plane in map coordinates."""
    ray = pose[:3, :3] @ np.linalg.solve(intrinsics, [*pixel, 1])
    origin = pose[:3, 3]
    normal = plane[:3]
    denominator = normal @ ray
    if abs(denominator) < 1e-8:
        return None
    distance = -(normal @ origin + plane[3]) / denominator
    return (origin + distance * ray).tolist() if distance > 0 else None


def inspect(args, predict):
    geometry = json.loads(args.geometry.read_text()) if args.geometry else None
    video = cv2.VideoCapture(str(args.video))
    try:
        fps = video.get(cv2.CAP_PROP_FPS)
        if not video.isOpened() or not np.isfinite(fps) or fps <= 0:
            raise ValueError("Could not open video with a valid frame rate")
        args.output.mkdir(parents=True, exist_ok=False)
        issues = {}
        if args.previous:
            for line in args.previous.read_text(encoding="utf-8").splitlines():
                record = json.loads(line)
                if record["issue_id"] and record["position_m"] is not None:
                    issues.setdefault(record["issue_id"], record["position_m"])
        next_sample = 0
        start = time.monotonic()
        with (args.output / "observations.jsonl").open("w", encoding="utf-8") as log:
            for index in count():
                ok, frame = video.read()
                if not ok:
                    break
                timestamp = index / fps
                if timestamp < next_sample:
                    continue
                next_sample = timestamp + 1 / args.rate
                if args.realtime:
                    time.sleep(max(0, start + timestamp - time.monotonic()))
                height, width = frame.shape[:2]
                x, y, w, h = args.crop or (0, 0, width, height)
                if min(x, y) < 0 or min(w, h) <= 0 or x + w > width or y + h > height:
                    raise ValueError("Crop must fit within the decoded frame")
                crop = frame[y:y+h, x:x+w]
                masks, scores = predict(Image.fromarray(cv2.cvtColor(crop, cv2.COLOR_BGR2RGB)))
                overlay = frame.copy()
                used_issues = set()
                for detection, (mask, score) in enumerate(zip(masks, scores)):
                    mask = mask.reshape(h, w).astype(bool)
                    rows, cols = np.nonzero(mask)
                    if not rows.size:
                        continue
                    # Choose a foreground pixel near the mask centroid.
                    center = np.argmin((cols - cols.mean())**2 + (rows - rows.mean())**2)
                    pixel = [int(cols[center]) + x, int(rows[center]) + y]
                    point = None
                    pose = geometry["poses"].get(str(index)) if geometry else None
                    if pose is not None:
                        point = project(pixel, np.asarray(geometry["intrinsics"]),
                                        np.asarray(pose), np.asarray(geometry["plane"]))
                    issue_id = None
                    if point is not None:
                        candidates = [(np.linalg.norm(np.subtract(point, anchor)), i)
                                      for i, anchor in issues.items() if i not in used_issues]
                        distance, issue = min(candidates, default=(float("inf"), None))
                        if distance > args.match_radius:
                            issue = f"crack-{max((int(key.split('-')[-1]) for key in issues), default=0) + 1}"
                            issues[issue] = point
                        used_issues.add(issue)
                        issue_id = issue
                    name = f"{index:06d}-{detection}-mask.png"
                    full_mask = np.zeros((height, width), dtype=np.uint8)
                    full_mask[y:y+h, x:x+w] = mask.astype(np.uint8) * 255
                    save_image(args.output / name, full_mask)
                    overlay[y:y+h, x:x+w][mask] = (0, 0, 255)
                    log.write(json.dumps({
                        "frame": index, "seconds": timestamp, "score": float(score),
                        "pixel": pixel, "position_m": point, "issue_id": issue_id,
                        "mask": name, "overlay": f"{index:06d}-overlay.jpg",
                    }) + "\n")
                preview = cv2.addWeighted(frame, 0.55, overlay, 0.45, 0)
                save_image(args.output / f"{index:06d}-overlay.jpg", preview)
                log.flush()
                if args.preview:
                    cv2.imshow("DeepSplat crack inspection", preview)
                    if cv2.waitKey(1) & 0xff in (27, ord("q")):
                        break
    finally:
        video.release()
        if args.preview:
            cv2.destroyAllWindows()


def save_image(path, image):
    if not cv2.imwrite(str(path), image):
        raise OSError(f"Could not write {path}")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("video", type=Path)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--prompt", default="crack in concrete")
    parser.add_argument("--rate", type=float, default=2, help="Maximum sampled frames per second")
    parser.add_argument("--threshold", type=float, default=0.5)
    parser.add_argument("--crop", type=int, nargs=4, metavar=("X", "Y", "WIDTH", "HEIGHT"))
    parser.add_argument("--checkpoint", type=Path)
    parser.add_argument("--geometry", type=Path)
    parser.add_argument("--previous", type=Path, help="Observations from a previous pass in the same map")
    parser.add_argument("--match-radius", type=float, default=0.15)
    parser.add_argument("--preview", action="store_true")
    parser.add_argument("--realtime", action="store_true", help="Pace replay by video timestamps")
    args = parser.parse_args()
    if not (np.isfinite(args.rate) and args.rate > 0 and 0 <= args.threshold <= 1
            and np.isfinite(args.match_radius) and args.match_radius > 0):
        parser.error("Rate and match radius must be positive and threshold must be in [0, 1]")

    from sam3.model_builder import build_sam3_image_model
    from sam3.model.sam3_image_processor import Sam3Processor

    options = {}
    if args.checkpoint:
        options = {"checkpoint_path": str(args.checkpoint), "load_from_HF": False}
    processor = Sam3Processor(build_sam3_image_model(**options),
                              confidence_threshold=args.threshold)

    def predict(image):
        state = processor.set_image(image)
        output = processor.set_text_prompt(state=state, prompt=args.prompt)
        return output["masks"].cpu().numpy(), output["scores"].float().cpu().numpy()

    inspect(args, predict)


if __name__ == "__main__":
    main()
