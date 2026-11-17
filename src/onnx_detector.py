# -*- coding: utf-8 -*-
"""
ONNX Runtime YOLOv8 detector — zero PyTorch in the inference path.

Pipeline: letterbox resize -> ONNX Runtime (FP32) -> per-class NMS.

NMS alignment note (R1 finding): the R1 validation used class-agnostic
``cv2.dnn.NMSBoxes`` over all classes, which suppressed overlapping boxes
of *different* classes (e.g. a ``van`` box overlapping a ``car`` box) and
produced a van count of 43 vs 37 for the ultralytics torch pipeline.
Ultralytics runs NMS per class by default (``agnostic_nms=False``), so
this module does the same: NMS is applied separately within each class,
with IoU 0.45 and a 300-box cap, matching ultralytics defaults.

Classes are the 10 VisDrone classes of the exported weights; the
name -> counting-class mapping lives in engine.py (same table as the
thesis package's detect_system.py), so COCO weights can be swapped in
by changing NAMES.
"""

import cv2
import numpy as np
import onnxruntime as ort

# VisDrone class names, in the order of the exported model's output.
VISDRONE_NAMES = [
    "pedestrian", "people", "bicycle", "car", "van",
    "truck", "tricycle", "awning-tricycle", "bus", "motor",
]

COCO_NAMES = [
    "person", "bicycle", "car", "motorcycle", "airplane", "bus",
    "train", "truck", "boat", "traffic light", "fire hydrant",
    "stop sign", "parking meter", "bench", "bird", "cat", "dog",
    "horse", "sheep", "cow", "elephant", "bear", "zebra", "giraffe",
    "backpack", "umbrella", "handbag", "tie", "suitcase", "frisbee",
    "skis", "snowboard", "sports ball", "kite", "baseball bat",
    "baseball glove", "skateboard", "surfboard", "tennis racket",
    "bottle", "wine glass", "cup", "fork", "knife", "spoon", "bowl",
    "banana", "apple", "sandwich", "orange", "broccoli", "carrot",
    "hot dog", "pizza", "donut", "cake", "chair", "couch",
    "potted plant", "bed", "dining table", "toilet", "tv", "laptop",
    "mouse", "remote", "keyboard", "cell phone", "microwave", "oven",
    "toaster", "sink", "refrigerator", "book", "clock", "vase",
    "scissors", "teddy bear", "hair drier", "toothbrush",
]


def letterbox(img, size=640):
    """Resize keeping aspect ratio, pad with 114 grey (ultralytics style)."""
    h, w = img.shape[:2]
    s = min(size / h, size / w)
    nh, nw = int(round(h * s)), int(round(w * s))
    r = cv2.resize(img, (nw, nh), interpolation=cv2.INTER_LINEAR)
    canvas = np.full((size, size, 3), 114, np.uint8)
    top = (size - nh) // 2
    left = (size - nw) // 2
    canvas[top:top + nh, left:left + nw] = r
    return canvas, s, left, top


class OnnxDetector:
    """YOLOv8 detector backed by ONNX Runtime only."""

    def __init__(self, model_path, names=VISDRONE_NAMES, imgsz=640,
                 conf=0.35, iou=0.45, max_det=300):
        self.session = ort.InferenceSession(
            model_path, providers=["CPUExecutionProvider"])
        self.input_name = self.session.get_inputs()[0].name
        self.names = list(names)
        self.imgsz = int(imgsz)
        self.conf = float(conf)
        self.iou = float(iou)
        self.max_det = int(max_det)

    def detect(self, img):
        """Return list of dicts: x1, y1, x2, y2 (original pixels),
        conf, cls (int), cls_name (str)."""
        canvas, s, dx, dy = letterbox(img, self.imgsz)
        x = canvas[:, :, ::-1].transpose(2, 0, 1)[None].astype(np.float32) / 255.0
        out = self.session.run(None, {self.input_name: x})[0][0]  # (14, 8400)
        out = out.T  # (8400, 14): cx, cy, w, h, 10 class scores
        scores = out[:, 4:]
        cls = scores.argmax(1)
        cf = scores.max(1)
        keep = np.nonzero(cf > self.conf)[0]
        if keep.size == 0:
            return []
        # Sort by confidence desc, then NMS per class (ultralytics default).
        keep = keep[np.argsort(-cf[keep])]
        boxes_xywh = out[keep, :4]
        boxes_xyxy = np.empty_like(boxes_xywh)
        boxes_xyxy[:, 0] = boxes_xywh[:, 0] - boxes_xywh[:, 2] / 2
        boxes_xyxy[:, 1] = boxes_xywh[:, 1] - boxes_xywh[:, 3] / 2
        boxes_xyxy[:, 2] = boxes_xywh[:, 0] + boxes_xywh[:, 2] / 2
        boxes_xyxy[:, 3] = boxes_xywh[:, 1] + boxes_xywh[:, 3] / 2
        kept = []
        for c in np.unique(cls[keep]):
            idxs = np.nonzero(cls[keep] == c)[0]
            # cv2.dnn.NMSBoxes expects [x, y, w, h], not xyxy.
            b = [[float(boxes_xyxy[i][0]), float(boxes_xyxy[i][1]),
                  float(boxes_xyxy[i][2] - boxes_xyxy[i][0]),
                  float(boxes_xyxy[i][3] - boxes_xyxy[i][1])]
                 for i in idxs]
            f = cf[keep[idxs]].tolist()
            sel = cv2.dnn.NMSBoxes(b, f, self.conf, self.iou)
            if len(sel) == 0:
                continue
            for j in np.array(sel).flatten():
                kept.append(keep[idxs[int(j)]])
        kept = kept[:self.max_det]
        h, w = img.shape[:2]
        dets = []
        for j in kept:
            cx, cy, bw, bh = out[j, :4]
            x1 = (cx - bw / 2 - dx) / s
            y1 = (cy - bh / 2 - dy) / s
            x2 = (cx + bw / 2 - dx) / s
            y2 = (cy + bh / 2 - dy) / s
            dets.append({
                "x1": float(np.clip(x1, 0, w)),
                "y1": float(np.clip(y1, 0, h)),
                "x2": float(np.clip(x2, 0, w)),
                "y2": float(np.clip(y2, 0, h)),
                "conf": float(cf[j]),
                "cls": int(cls[j]),
                "cls_name": self.names[int(cls[j])],
            })
        return dets
