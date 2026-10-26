"""PaddleOCR adapter. Only explicit model setup uses the network."""

import os
import tarfile
import tempfile
import urllib.request
from pathlib import Path

from .db import data_directory

MODELS = ("PP-OCRv5_mobile_det", "en_PP-OCRv4_mobile_rec")
MODEL_SOURCE = "https://paddle-model-ecology.bj.bcebos.com/paddlex/official_inference_model/paddle3.0.0/"
FONTS = ("PingFang-SC-Regular.ttf", "simfang.ttf")


def model_paths(root):
    return [root / "models" / (name + "_infer") for name in MODELS]


def models_ready(root):
    return all((path / "inference.pdiparams").is_file() and
               ((path / "inference.json").is_file() or (path / "inference.pdmodel").is_file())
               for path in model_paths(root)) and all(
                   (root / "models" / "cache" / "fonts" / name).is_file() for name in FONTS)


def install_models(root=None):
    root = root or data_directory()
    destination = root / "models"
    destination.mkdir(parents=True, exist_ok=True)
    for name, target in zip(MODELS, model_paths(root)):
        if (target / "inference.pdiparams").exists():
            continue
        print(f"Downloading {name} from the official Paddle model host...", flush=True)
        with tempfile.TemporaryDirectory(dir=destination) as temporary:
            archive = Path(temporary) / "model.tar"
            urllib.request.urlretrieve(MODEL_SOURCE + name + "_infer.tar", archive)
            with tarfile.open(archive) as tar:
                tar.extractall(temporary, filter="data")
            extracted = Path(temporary) / (name + "_infer")
            if not (extracted / "inference.pdiparams").is_file():
                raise RuntimeError(f"Downloaded {name} has no inference weights.")
            extracted.replace(target)
    # PaddleX imports its visualization fonts even when only text recognition is used.
    # Cache these during explicit setup so the first offline inference cannot trigger a download.
    fonts = destination / "cache" / "fonts"
    fonts.mkdir(parents=True, exist_ok=True)
    for name in FONTS:
        target = fonts / name
        if not target.exists():
            print(f"Downloading PaddleX support font {name}...", flush=True)
            temporary = target.with_suffix(".download")
            try:
                urllib.request.urlretrieve(
                    "https://paddle-model-ecology.bj.bcebos.com/paddlex/PaddleX3.0/fonts/" + name, temporary)
                temporary.replace(target)
            finally:
                temporary.unlink(missing_ok=True)
    if not models_ready(root):
        raise RuntimeError("Model installation incomplete. Retry model setup.")
    print("OCR models ready. Receipt processing can now work offline.", flush=True)


class PaddleEngine:
    def __init__(self, root):
        self.root = root
        self.pipeline = None

    def recognize(self, image):
        if self.pipeline is None:
            if not models_ready(self.root):
                raise RuntimeError("OCR models missing. Run scripts/setup.ps1 or python -m app.ocr, then Retry failed.")
            os.environ["PADDLE_PDX_DISABLE_MODEL_SOURCE_CHECK"] = "True"
            os.environ["PADDLE_PDX_CACHE_HOME"] = str(self.root / "models" / "cache")
            from paddleocr import PaddleOCR

            detection, recognition = model_paths(self.root)
            self.pipeline = PaddleOCR(
                text_detection_model_name=MODELS[0], text_detection_model_dir=str(detection),
                text_recognition_model_name=MODELS[1], text_recognition_model_dir=str(recognition),
                use_doc_orientation_classify=False, use_doc_unwarping=False, use_textline_orientation=False,
                device="cpu", enable_mkldnn=False, cpu_threads=4,
            )
        lines = []
        for result in self.pipeline.predict(str(image)):
            for text, score, polygon in zip(result["rec_texts"], result["rec_scores"], result["rec_polys"]):
                points = polygon.tolist() if hasattr(polygon, "tolist") else polygon
                xs, ys = zip(*points)
                lines.append({"text": text, "score": float(score), "polygon": points,
                              "box": [float(min(xs)), float(min(ys)), float(max(xs)), float(max(ys))]})
        return lines


if __name__ == "__main__":
    install_models()
