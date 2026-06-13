"""
Rakshak video processor.
Reads video files (or webcam/RTSP), runs YOLOv8n inference per frame,
classifies incidents, and emits detection events.

YOLOv8n is downloaded automatically by Ultralytics on first run (~6MB).
No GPU required — runs on CPU at ~5-15fps for typical CCTV resolution.
"""
import asyncio
import time
import os
from pathlib import Path
from typing import Optional, Callable, Awaitable
import cv2
import numpy as np
from ultralytics import YOLO
from .incident_classifier import IncidentClassifier, IncidentResult

MODEL_PATH = Path(__file__).parent.parent / "models" / "yolov8n.pt"
# Ultralytics downloads yolov8n.pt automatically if not found at this path


class Detection:
    def __init__(self, incident_type: str, confidence: float, severity: str,
                 description: str, camera_id: str, lat: float, lng: float,
                 zone_id: Optional[str], timestamp: float, frame_path: Optional[str] = None,
                 bbox_list: list = None):
        self.incident_type = incident_type
        self.confidence = confidence
        self.severity = severity
        self.description = description
        self.camera_id = camera_id
        self.lat = lat
        self.lng = lng
        self.zone_id = zone_id
        self.timestamp = timestamp
        self.frame_path = frame_path
        self.bbox_list = bbox_list or []

    def to_dict(self) -> dict:
        return {
            "incident_type": self.incident_type,
            "confidence": self.confidence,
            "severity": self.severity,
            "description": self.description,
            "camera_id": self.camera_id,
            "lat": self.lat,
            "lng": self.lng,
            "zone_id": self.zone_id,
            "timestamp": self.timestamp,
            "frame_path": self.frame_path,
            "bbox_list": self.bbox_list,
        }


# Camera registry — maps camera_id to geo coordinates and NEXUS zone
CAMERA_REGISTRY = {
    # Coords are the actual road junction centroids — snapped to OpenStreetMap road geometry
    "cam_meh_001": {"lat": 17.3951, "lng": 78.4293, "zone_id": "mehdipatnam_up", "name": "Mehdipatnam UP Junction"},
    "cam_meh_002": {"lat": 17.3960, "lng": 78.4281, "zone_id": "mehdipatnam_up", "name": "Mehdipatnam UP Mid"},
    "cam_tol_001": {"lat": 17.4049, "lng": 78.4112, "zone_id": "tolichowki_up",  "name": "Tolichowki UP Junction"},
    "cam_nar_001": {"lat": 17.3918, "lng": 78.4861, "zone_id": "narayanguda_up", "name": "Narayanguda Junction"},
    "cam_mal_001": {"lat": 17.3693, "lng": 78.4997, "zone_id": "malakpet_up",    "name": "Malakpet Junction"},
    "cam_lb_001":  {"lat": 17.3471, "lng": 78.5518, "zone_id": "lb_nagar_up",    "name": "LB Nagar Junction"},
}


class VideoProcessor:
    def __init__(self,
                 camera_id: str,
                 video_source,          # path str, int (webcam), or "rtsp://..."
                 on_detection: Callable[[Detection], Awaitable[None]],
                 inference_every_n_frames: int = 3,
                 min_confidence: float = 0.55,
                 save_frames: bool = True):

        self.camera_id = camera_id
        self.video_source = video_source
        self.on_detection = on_detection
        self.inference_every_n_frames = inference_every_n_frames
        self.min_confidence = min_confidence
        self.save_frames = save_frames

        cam = CAMERA_REGISTRY.get(camera_id, {})
        self.lat = cam.get("lat", 17.4065)
        self.lng = cam.get("lng", 78.4772)
        self.zone_id = cam.get("zone_id")

        self._model: Optional[YOLO] = None
        self._classifier = IncidentClassifier(frame_history=8)
        self._running = False
        self._frame_count = 0
        self._last_detection_times: dict = {}  # per incident_type cooldown
        self._detection_cooldown = 2.0

        # Frame save directory
        self._frame_dir = Path(__file__).parent.parent / "frames" / camera_id
        if save_frames:
            self._frame_dir.mkdir(parents=True, exist_ok=True)

    def _load_model(self) -> YOLO:
        if self._model is None:
            print(f"[Rakshak/{self.camera_id}] Loading YOLOv8n...")
            MODEL_PATH.parent.mkdir(parents=True, exist_ok=True)
            # Use local path if it exists, otherwise let ultralytics download to its cache
            model_arg = str(MODEL_PATH) if MODEL_PATH.exists() else "yolov8n.pt"
            self._model = YOLO(model_arg)
            # Cache a copy in our models dir for future runs
            if not MODEL_PATH.exists():
                try:
                    import shutil as _shutil
                    src = self._model.ckpt_path
                    if src and Path(src).exists():
                        _shutil.copy2(src, MODEL_PATH)
                except Exception:
                    pass
            print(f"[Rakshak/{self.camera_id}] Model ready")
        return self._model

    async def run(self):
        """Main loop — inference runs in a thread, results pushed via asyncio.Queue."""
        self._running = True
        loop = asyncio.get_event_loop()

        model = await loop.run_in_executor(None, self._load_model)
        print(f"[Rakshak/{self.camera_id}] Model ready, starting inference loop")

        q: asyncio.Queue = asyncio.Queue(maxsize=32)

        def _infer_thread():
            """Thread: read frames, run YOLO, write latest.jpg, push results to queue."""
            import time as _t
            cap = None
            try:
                cap = cv2.VideoCapture(self.video_source)
                if not cap.isOpened():
                    print(f"[Rakshak/{self.camera_id}] Cannot open video source")
                    return

                fps = cap.get(cv2.CAP_PROP_FPS) or 25
                skip = max(1, int(fps / 5))  # process ~5 fps
                local_count = 0

                while self._running:
                    ret, frame = cap.read()
                    if not ret:
                        # Video ended — stop, do not loop
                        print(f"[Rakshak/{self.camera_id}] Video ended, stopping inference")
                        self._running = False
                        break

                    local_count += 1
                    if local_count % skip != 0:
                        continue

                    small = cv2.resize(frame, (640, 480))
                    results = model(small, verbose=False, conf=0.25)[0]

                    # Write frame immediately — this is the live feed
                    annotated = results.plot()
                    cv2.imwrite(str(self._frame_dir / "latest.jpg"), annotated)

                    # Push to asyncio queue (non-blocking put)
                    try:
                        loop.call_soon_threadsafe(q.put_nowait, (results, small, local_count))
                    except asyncio.QueueFull:
                        pass  # drop if consumer is slow
            finally:
                if cap is not None:
                    cap.release()
                # Delete latest.jpg so the UI shows "No Signal" after stop
                try:
                    (self._frame_dir / "latest.jpg").unlink(missing_ok=True)
                except Exception:
                    pass

        # Start inference thread
        import threading
        t = threading.Thread(target=_infer_thread, daemon=True)
        t.start()

        # Consume results from queue and fire detections
        while self._running:
            try:
                results, small, count = await asyncio.wait_for(q.get(), timeout=1.0)
            except asyncio.TimeoutError:
                continue

            self._frame_count = count
            incident = self._classifier.classify(results, small)

            # Log every non-none classification so we can see what's being detected
            if incident.incident_type != "none":
                print(f"[Rakshak/{self.camera_id}] frame={count} "
                      f"type={incident.incident_type} conf={incident.confidence:.2f} "
                      f"sev={incident.severity} | {incident.description[:60]}")

            if incident.incident_type != "none" and incident.confidence >= self.min_confidence:
                now = time.time()
                last = self._last_detection_times.get(incident.incident_type, 0.0)
                if now - last >= self._detection_cooldown:
                    self._last_detection_times[incident.incident_type] = now
                    frame_path = None
                    if self.save_frames:
                        frame_path = str(self._frame_dir / f"frame_{count:06d}.jpg")
                        cv2.imwrite(frame_path, results.plot())
                    det = Detection(
                        incident_type=incident.incident_type,
                        confidence=incident.confidence,
                        severity=incident.severity,
                        description=incident.description,
                        camera_id=self.camera_id,
                        lat=self.lat,
                        lng=self.lng,
                        zone_id=self.zone_id,
                        timestamp=now,
                        frame_path=frame_path,
                        bbox_list=incident.bbox_list,
                    )
                    await self.on_detection(det)

    def stop(self):
        self._running = False
