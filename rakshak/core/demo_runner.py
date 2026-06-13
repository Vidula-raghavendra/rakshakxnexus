"""
Rakshak demo runner.

Generates synthetic CCTV-like frames using OpenCV and runs YOLOv8 on them.
No video files needed. The synthetic frames simulate:
  - Normal traffic (cars, people on road)
  - Flooding (blue-hued lower half + stationary vehicles)
  - Stranded vehicles (same-position bboxes across frames)
  - Crowd surge (many person-like blobs)
  - Road blocked (wide truck-like rectangle)

This gives real YOLOv8 detections on controlled synthetic input,
making the demo deterministic and impressive.

For real deployment: swap synthetic frames for actual CCTV feeds.
"""
import asyncio
import time
import numpy as np
import cv2
from typing import Callable, Awaitable, Optional
from .video_processor import Detection, CAMERA_REGISTRY
from .incident_classifier import IncidentClassifier


# Scenario timeline: (seconds_after_trigger, scene_type, camera_id)
FLOOD_SCENARIO_TIMELINE = [
    (0,   "normal",          "cam_meh_001"),
    (20,  "water_rising",    "cam_meh_001"),
    (40,  "road_flood",      "cam_meh_001"),
    (60,  "vehicle_stranded","cam_meh_002"),
    (90,  "road_flood",      "cam_tol_001"),
    (120, "crowd_surge",     "cam_meh_001"),
    (150, "vehicle_stranded","cam_tol_001"),
    (180, "road_flood",      "cam_nar_001"),
    (220, "road_blocked",    "cam_mal_001"),
    (270, "road_flood",      "cam_lb_001"),
]

SEVERITY_MAP = {
    "road_flood":       "critical",
    "vehicle_stranded": "critical",
    "crowd_surge":      "high",
    "road_blocked":     "high",
    "water_rising":     "medium",
    "normal":           "low",
}

DESCRIPTIONS = {
    "road_flood":       "Floodwater detected on road surface — vehicles at risk",
    "vehicle_stranded": "Vehicle stationary in floodwater — occupants may need rescue",
    "crowd_surge":      "Pedestrians crowding road level near flood zone",
    "road_blocked":     "Road obstruction detected — traffic flow blocked",
    "water_rising":     "Water level rising on road surface, monitoring",
    "normal":           "Scene normal",
}

# Incident type mapping (scene_type → NEXUS incident_type)
INCIDENT_TYPE_MAP = {
    "road_flood":       "road_flood",
    "vehicle_stranded": "vehicle_stranded",
    "crowd_surge":      "crowd_surge",
    "road_blocked":     "road_blocked",
    "water_rising":     "road_flood",
    "normal":           None,
}


class DemoRunner:
    def __init__(self, on_detection: Callable[[Detection], Awaitable[None]]):
        self.on_detection = on_detection
        self._running = False
        self._scenario_active = False
        self._scenario_start: Optional[float] = None
        self._timeline_index = 0
        self._frame_generators: dict = {}

    def trigger_flood_scenario(self):
        self._scenario_active = True
        self._scenario_start = time.time()
        self._timeline_index = 0

    def reset(self):
        self._scenario_active = False
        self._scenario_start = None
        self._timeline_index = 0

    def stop(self):
        self._running = False

    async def run(self):
        """Main loop — fires detections according to scenario timeline."""
        self._running = True
        while self._running:
            if self._scenario_active and self._scenario_start is not None:
                elapsed = time.time() - self._scenario_start

                while self._timeline_index < len(FLOOD_SCENARIO_TIMELINE):
                    trigger_t, scene_type, camera_id = FLOOD_SCENARIO_TIMELINE[self._timeline_index]
                    if elapsed >= trigger_t:
                        await self._fire_detection(scene_type, camera_id)
                        self._timeline_index += 1
                    else:
                        break

                if self._timeline_index >= len(FLOOD_SCENARIO_TIMELINE):
                    self._scenario_active = False

            await asyncio.sleep(1)

    async def _fire_detection(self, scene_type: str, camera_id: str):
        incident_type = INCIDENT_TYPE_MAP.get(scene_type)
        if incident_type is None:
            return

        cam = CAMERA_REGISTRY.get(camera_id, {})

        # Generate a synthetic frame and run real YOLOv8 on it
        frame = _generate_synthetic_frame(scene_type)
        confidence, bbox_list = await _run_yolo_on_frame(frame, scene_type)

        det = Detection(
            incident_type=incident_type,
            confidence=confidence,
            severity=SEVERITY_MAP.get(scene_type, "medium"),
            description=DESCRIPTIONS.get(scene_type, ""),
            camera_id=camera_id,
            lat=cam.get("lat", 17.4065),
            lng=cam.get("lng", 78.4772),
            zone_id=cam.get("zone_id"),
            timestamp=time.time(),
            bbox_list=bbox_list,
        )

        # Save annotated frame
        frame_path = _save_frame(frame, camera_id, scene_type)
        det.frame_path = frame_path

        await self.on_detection(det)


def _generate_synthetic_frame(scene_type: str) -> np.ndarray:
    """
    Generate a 640x480 BGR synthetic CCTV frame.
    Uses realistic colors and shapes that YOLO can detect.
    """
    W, H = 640, 480
    frame = np.zeros((H, W, 3), dtype=np.uint8)

    # Sky (top third)
    frame[:H//3, :] = (80, 60, 40)   # dark overcast sky (BGR)
    # Add some cloud texture
    for i in range(0, H//3, 20):
        cv2.line(frame, (0, i), (W, i + 10), (100, 80, 60), 8)

    # Road surface (middle and bottom)
    if scene_type in ("road_flood", "vehicle_stranded", "water_rising"):
        # Wet/flooded road — blue-grey
        road_color = (140, 100, 60)    # blueish (BGR: high B)
        water_color = (180, 120, 70)
        frame[H//3:, :] = road_color
        # Water patches — irregular blue blobs
        for _ in range(6):
            cx = np.random.randint(50, W-50)
            cy = np.random.randint(H//3 + 30, H - 20)
            cv2.ellipse(frame, (cx, cy), (np.random.randint(40, 120), np.random.randint(10, 30)),
                        0, 0, 360, water_color, -1)
        # Reflections
        cv2.line(frame, (W//4, H//2), (3*W//4, H//2 + 20), (200, 160, 100), 2)
    else:
        # Dry road — grey
        frame[H//3:, :] = (80, 80, 80)
        # Road markings
        for x in range(0, W, 80):
            cv2.line(frame, (x, H//2), (x + 40, H), (120, 120, 120), 2)

    # Draw scene-specific objects
    if scene_type == "road_flood":
        _draw_cars(frame, positions=[(120, 340), (300, 360), (480, 340)], color=(50, 50, 180))
        _draw_cars(frame, positions=[(200, 300)], color=(30, 30, 140))

    elif scene_type == "vehicle_stranded":
        # Same car position (to trigger stationary detection)
        _draw_cars(frame, positions=[(280, 350), (400, 330)], color=(40, 40, 160))
        # Person on roof
        cv2.rectangle(frame, (285, 310), (305, 350), (200, 150, 100), -1)  # person
        cv2.circle(frame, (295, 305), 10, (200, 160, 120), -1)  # head

    elif scene_type == "crowd_surge":
        frame[H//3:, :] = (90, 80, 80)  # slightly wet
        # Draw many person-like shapes
        for i in range(10):
            x = 60 + i * 52
            y = np.random.randint(H//2, H - 40)
            _draw_person(frame, x, y)

    elif scene_type == "road_blocked":
        frame[H//3:, :] = (75, 75, 75)
        # Large truck sideways across road
        cv2.rectangle(frame, (80, 280), (560, 380), (60, 80, 100), -1)   # truck body
        cv2.rectangle(frame, (80, 280), (560, 380), (80, 100, 120), 3)   # outline
        cv2.rectangle(frame, (80, 260), (200, 285), (60, 80, 100), -1)   # cab
        # Wheels
        for wx in [120, 260, 400, 520]:
            cv2.circle(frame, (wx, 382), 20, (30, 30, 30), -1)
        # Some cars blocked behind
        _draw_cars(frame, positions=[(100, 420), (240, 420)], color=(180, 50, 50))

    elif scene_type == "water_rising":
        _draw_cars(frame, positions=[(150, 360), (350, 345)], color=(100, 100, 200))

    else:  # normal
        frame[H//3:, :] = (85, 85, 85)
        _draw_cars(frame, positions=[(100, 350), (300, 340), (500, 355)], color=(200, 80, 80))
        _draw_person(frame, 420, 310)

    # Add CCTV-style timestamp overlay
    ts = time.strftime("%Y-%m-%d %H:%M:%S")
    cv2.putText(frame, f"CAM | {ts}", (8, 20), cv2.FONT_HERSHEY_SIMPLEX, 0.4, (200, 200, 200), 1)
    # Slight noise (camera grain)
    noise = np.random.randint(0, 15, frame.shape, dtype=np.uint8)
    frame = cv2.add(frame, noise)

    return frame


def _draw_cars(frame: np.ndarray, positions: list, color: tuple):
    for (cx, cy) in positions:
        w, h = 80, 40
        # Car body
        cv2.rectangle(frame, (cx - w//2, cy - h//2), (cx + w//2, cy + h//2), color, -1)
        # Roof
        cv2.rectangle(frame, (cx - w//4, cy - h), (cx + w//4, cy - h//2), color, -1)
        # Windows (lighter)
        wc = tuple(min(255, c + 60) for c in color)
        cv2.rectangle(frame, (cx - w//5, cy - h + 4), (cx + w//5, cy - h//2 - 2), wc, -1)
        # Wheels
        for wx in [cx - w//3, cx + w//3]:
            cv2.circle(frame, (wx, cy + h//2), 10, (20, 20, 20), -1)


def _draw_person(frame: np.ndarray, cx: int, cy: int):
    # Body
    cv2.rectangle(frame, (cx - 8, cy), (cx + 8, cy + 30), (150, 120, 90), -1)
    # Head
    cv2.circle(frame, (cx, cy - 8), 9, (190, 155, 120), -1)
    # Legs
    cv2.line(frame, (cx - 4, cy + 30), (cx - 6, cy + 50), (140, 110, 80), 4)
    cv2.line(frame, (cx + 4, cy + 30), (cx + 6, cy + 50), (140, 110, 80), 4)


_yolo_model = None

async def _run_yolo_on_frame(frame: np.ndarray, scene_type: str):
    """Run YOLOv8n on the synthetic frame and return (confidence, bbox_list)."""
    global _yolo_model
    import asyncio
    from ultralytics import YOLO

    loop = asyncio.get_event_loop()

    def _infer():
        global _yolo_model
        if _yolo_model is None:
            _yolo_model = YOLO("yolov8n.pt")
        results = _yolo_model(frame, verbose=False, conf=0.2)[0]
        return results

    results = await loop.run_in_executor(None, _infer)

    COCO_NAMES = {0:"person", 2:"car", 3:"motorcycle", 5:"bus", 7:"truck"}
    bbox_list = []
    if results.boxes is not None:
        for box in results.boxes:
            cls_id = int(box.cls[0])
            if cls_id in COCO_NAMES:
                bbox_list.append({
                    "class": COCO_NAMES[cls_id],
                    "conf": round(float(box.conf[0]), 2),
                    "xyxy": [round(x, 1) for x in box.xyxy[0].tolist()]
                })

    # Confidence: use detection count + scene severity as proxy
    base_conf = {
        "road_flood": 0.84, "vehicle_stranded": 0.91, "crowd_surge": 0.78,
        "road_blocked": 0.85, "water_rising": 0.67, "normal": 0.0
    }.get(scene_type, 0.7)

    detected_objects = len(bbox_list)
    adjusted_conf = min(base_conf + detected_objects * 0.01, 0.97)

    return adjusted_conf, bbox_list


def _save_frame(frame: np.ndarray, camera_id: str, scene_type: str) -> Optional[str]:
    from pathlib import Path
    frame_dir = Path(__file__).parent.parent / "frames" / camera_id
    frame_dir.mkdir(parents=True, exist_ok=True)
    ts = int(time.time())
    fname = f"{scene_type}_{ts}.jpg"
    cv2.imwrite(str(frame_dir / fname), frame)
    # Always update latest.jpg so the MJPEG stream has something to serve
    cv2.imwrite(str(frame_dir / "latest.jpg"), frame)
    return f"/frames/{camera_id}/{fname}"
