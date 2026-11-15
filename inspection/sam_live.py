"""Serve a phone camera page and annotate sampled frames with SAM 3."""
import argparse
from io import BytesIO
from pathlib import Path
from threading import Lock
from time import monotonic
from unittest.mock import patch

import cv2
from flask import Flask, Response, request
import numpy as np
from PIL import Image

PROMPTS = (
    ("hole in wall", (245, 165, 50)),
    ("peeling paint", (70, 220, 100)),
    ("crack in wall", (255, 75, 85)),
)


def create_app(predict, default_prompts=None, streaming=False):
    app = Flask(__name__)
    app.config["MAX_CONTENT_LENGTH"] = 5 * 1024 * 1024
    inference_lock = Lock()
    latest = None

    @app.get("/")
    def camera():
        page = Path(__file__).with_suffix(".html").read_text(encoding="utf-8-sig")
        if default_prompts:
            page = page.replace("hole in wall\npeeling paint\ncrack in wall", "\n".join(default_prompts))
        return Response(page, mimetype="text/html")

    @app.get("/latest")
    def latest_frame():
        if latest is None:
            return Response(status=204)
        return Response(latest, mimetype="image/jpeg", headers={"Cache-Control": "no-store"})

    @app.post("/inspect")
    def inspect_frame():
        nonlocal latest
        if request.mimetype != "image/jpeg":
            return "Expected a JPEG frame", 415
        try:
            with Image.open(BytesIO(request.get_data())) as source:
                if source.format != "JPEG" or max(source.size) > 1920:
                    return "Expected a JPEG up to 1920 pixels per side", 400
                image = source.convert("RGB")
        except (OSError, ValueError, Image.DecompressionBombError):
            return "Invalid JPEG frame", 400

        custom = request.args.getlist("prompt")
        if len(custom) > 8 or any(not prompt.strip() or len(prompt) > 120 for prompt in custom):
            return "Enter up to eight prompts of 120 characters each", 400
        prompts = [prompt.strip() for prompt in custom] or default_prompts or [label for label, _ in PROMPTS]

        if not inference_lock.acquire(blocking=False):
            return "Inference already running", 429
        try:
            if streaming:
                latest = predict(image, prompts, request.args.get("stream"))
            else:
                latest = predict(image, prompts)
            return Response(latest, mimetype="image/jpeg")
        except ValueError as error:
            return str(error), 400
        finally:
            inference_lock.release()

    return app


def build_predictor(checkpoint, threshold, mixed_precision=False):
    import torch
    from sam3.model_builder import build_sam3_image_model
    from sam3.model.sam3_image_processor import Sam3Processor

    options = {}
    if checkpoint:
        options = {"checkpoint_path": checkpoint, "load_from_HF": False}
    if mixed_precision:
        model = build_sam3_image_model(device="cpu", **options)
        model.backbone.bfloat16()
        model.cuda()
    else:
        model = build_sam3_image_model(**options)
    processor = Sam3Processor(model,
                              confidence_threshold=threshold)

    @torch.inference_mode()
    @torch.autocast("cuda", dtype=torch.bfloat16, enabled=mixed_precision)
    def predict(image, prompts):
        state = processor.set_image(image)

        def results():
            for prompt in prompts:
                processor.reset_all_prompts(state)
                result = processor.set_text_prompt(state=state, prompt=prompt)
                yield prompt, result["masks"].cpu().numpy(), result["scores"].float().cpu().tolist()

        return render_masks(image, results())

    return predict


def render_masks(image, results):
    annotated = np.array(image)
    for index, (prompt, masks, scores) in enumerate(results):
        color = PROMPTS[index % len(PROMPTS)][1]
        for mask, score in zip(masks, scores):
            region = mask.reshape(annotated.shape[:2]).astype(bool)
            rows, cols = np.nonzero(region)
            if not rows.size:
                continue
            annotated[region] = 0.55 * annotated[region] + 0.45 * np.array(color)
            cv2.putText(annotated, f"{prompt} {score:.2f}",
                        (int(cols.min()), max(22, int(rows.min()))),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.65, color, 2, cv2.LINE_AA)
    buffer = BytesIO()
    Image.fromarray(annotated).save(buffer, format="JPEG", quality=88)
    return buffer.getvalue()



def build_sam31_predictor(checkpoint, threshold):
    import torch
    from sam3.model_builder import build_sam3_multiplex_video_predictor

    predictor = build_sam3_multiplex_video_predictor(
        checkpoint_path=checkpoint, use_fa3=False, use_rope_real=False
    )
    predictor.bf16_context.__exit__(None, None, None)
    model = predictor.model
    backbone = model.detector.backbone
    encode_image = backbone.forward_image

    @torch.inference_mode()
    @torch.autocast("cuda", dtype=torch.bfloat16)
    def predict(image, prompts):
        state = model.init_state(resource_path=[image])
        features = {}

        def encode_once(samples, *, need_sam3_out=True, need_interactive_out=True,
                        need_propagation_out=True):
            # SAM resets prompt state on every call. Cache only image features
            # for this single-frame request, keyed by the requested outputs.
            key = (need_sam3_out, need_interactive_out, need_propagation_out)
            if key not in features:
                features[key] = encode_image(
                    samples, need_sam3_out=need_sam3_out,
                    need_interactive_out=need_interactive_out,
                    need_propagation_out=need_propagation_out,
                )
            return features[key].copy()

        def results():
            for prompt in prompts:
                _, output = model.add_prompt(state, frame_idx=0, text_str=prompt,
                                             output_prob_thresh=threshold)
                yield prompt, output["out_binary_masks"], output["out_probs"]

        # create_app serializes inference requests on this model.
        with patch.object(backbone, "forward_image", encode_once):
            return render_masks(image, results())

    return predict


def build_yolo_predictor(checkpoint, threshold):
    from ultralytics import YOLO

    model = YOLO(checkpoint)
    names = {name: index for index, name in model.names.items()}

    def predict(image, prompts):
        classes = [names[prompt] for prompt in prompts if prompt in names]
        if not classes:
            raise ValueError("Enter a YOLO class: " + ", ".join(names))
        frame = cv2.cvtColor(np.asarray(image), cv2.COLOR_RGB2BGR)
        result = model.predict(frame, classes=classes, conf=threshold,
                               imgsz=640, verbose=False)[0]
        ok, jpeg = cv2.imencode(".jpg", result.plot())
        if not ok:
            raise RuntimeError("Could not encode the detection frame")
        return jpeg.tobytes()

    return predict


def build_streaming_predictor(checkpoint, threshold, resolution):
    import torch
    from transformers import Sam3VideoModel, Sam3VideoProcessor

    if not torch.cuda.is_available():
        raise RuntimeError("SAM 3 streaming requires CUDA in this app")
    device = "cuda"
    dtype = torch.bfloat16 if torch.cuda.is_bf16_supported() else torch.float16
    model = Sam3VideoModel.from_pretrained(
        checkpoint, dtype=dtype, device_map=device,
        image_size=resolution, score_threshold_detection=threshold,
        new_det_thresh=threshold,
    ).eval()
    processor = Sam3VideoProcessor.from_pretrained(
        checkpoint, size={"height": resolution, "width": resolution}
    )
    session = stream_key = None
    last_frame = frame_count = 0

    @torch.inference_mode()
    def predict(image, prompts, stream_id=None):
        nonlocal session, stream_key, last_frame, frame_count
        key = (stream_id, image.size, tuple(prompts))
        if key != stream_key or monotonic() - last_frame > 10 or frame_count >= 300:
            session = processor.init_video_session(
                inference_device=device, processing_device="cpu",
                video_storage_device="cpu", dtype=dtype,
            )
            session = processor.add_text_prompt(session, list(prompts))
            stream_key = key
            frame_count = 0
        try:
            inputs = processor(images=image, device=device, return_tensors="pt").to(device)
            outputs = model(inference_session=session, frame=inputs.pixel_values[0])
            frame_count += 1
            result = processor.postprocess_outputs(
                session, outputs, original_sizes=inputs.original_sizes
            )
            labels = {
                int(obj_id): prompt
                for prompt, ids in result["prompt_to_obj_ids"].items()
                for obj_id in ids
            }
            groups = {prompt: ([], []) for prompt in prompts}
            for obj_id, mask, score in zip(
                result["object_ids"].tolist(), result["masks"].cpu().numpy(),
                result["scores"].tolist(),
            ):
                prompt = labels.get(obj_id)
                if prompt in groups and score >= threshold:
                    groups[prompt][0].append(mask)
                    groups[prompt][1].append(score)
            return render_masks(image, ((prompt, *group) for prompt, group in groups.items()))
        except Exception:
            session = stream_key = None
            raise
        finally:
            last_frame = monotonic()

    return predict


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--cert")
    parser.add_argument("--key")
    parser.add_argument("--mixed-precision", action="store_true")
    parser.add_argument("--checkpoint")
    parser.add_argument("--model", choices=("sam3", "sam3.1", "sam3-stream", "yolo"), default="sam3")
    parser.add_argument("--threshold", type=float, default=0.5)
    parser.add_argument("--port", type=int, default=8443)
    parser.add_argument("--stream-resolution", type=int, choices=(560, 1008), default=560)
    args = parser.parse_args()
    if not 0 <= args.threshold <= 1:
        parser.error("Threshold must be in [0, 1]")
    if bool(args.cert) != bool(args.key):
        parser.error("Provide both --cert and --key for LAN access")
    default_prompts = None
    if args.model == "yolo":
        if not args.checkpoint:
            parser.error("--model yolo requires --checkpoint")
        predictor = build_yolo_predictor(args.checkpoint, args.threshold)
        default_prompts = ["peeling_paint"]
    elif args.model == "sam3-stream":
        predictor = build_streaming_predictor(
            args.checkpoint or "facebook/sam3", args.threshold, args.stream_resolution
        )
    elif args.model == "sam3.1":
        predictor = build_sam31_predictor(args.checkpoint, args.threshold)
    else:
        predictor = build_predictor(args.checkpoint, args.threshold, args.mixed_precision)
    create_app(predictor, default_prompts, streaming=args.model == "sam3-stream").run(host="0.0.0.0" if args.cert else "127.0.0.1", port=args.port,
                              ssl_context=(args.cert, args.key) if args.cert else None)


if __name__ == "__main__":
    main()
