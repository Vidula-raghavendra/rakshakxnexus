"""
Rakshak Incident Classifier — multi-domain detection using YOLOv8 COCO classes.

Detects 12 incident categories using object co-occurrence, spatial heuristics,
density, and temporal consistency across frames. No fine-tuning required.

Categories:
  ROAD_ACCIDENT       — vehicle collisions, overturned vehicles, crash scenes
  ROAD_FLOOD          — water on road, vehicles in flood
  VEHICLE_STRANDED    — stationary vehicles in hazard zone
  CROWD_SURGE         — dangerous crowd density
  ROAD_BLOCKED        — large vehicles blocking road
  WOMEN_SAFETY        — lone woman at night, surrounded by group
  FIGHT_VIOLENCE      — person on ground, surrounding group, raised objects
  GARBAGE_DUMPING     — waste accumulation in public spaces
  ANIMAL_ON_ROAD      — stray animals creating hazard
  TRAFFIC_SIGNAL_ISSUE— damaged/missing signals, signal violation patterns
  ABANDONED_OBJECT    — unattended bags/luggage in public
  BUILDING_DAMAGE     — structural collapse indicators
"""

from dataclasses import dataclass, field
from typing import Optional
import numpy as np

# ── COCO class IDs ────────────────────────────────────────────────────────────
PERSON       = 0
BICYCLE      = 1
CAR          = 2
MOTORCYCLE   = 3
BUS          = 5
TRUCK        = 7
BOAT         = 8
TRAFFIC_LIGHT= 9
STOP_SIGN    = 11
BENCH        = 13
CAT          = 15
DOG          = 16
HORSE        = 17
COW          = 19
BACKPACK     = 24
UMBRELLA     = 25
HANDBAG      = 26
SUITCASE     = 28
BOTTLE       = 39
CHAIR        = 56

COCO_NAMES = {
    0:'person', 1:'bicycle', 2:'car', 3:'motorcycle', 5:'bus', 7:'truck',
    8:'boat', 9:'traffic light', 11:'stop sign', 13:'bench', 15:'cat',
    16:'dog', 17:'horse', 19:'cow', 24:'backpack', 25:'umbrella',
    26:'handbag', 28:'suitcase', 39:'bottle', 56:'chair',
}

VEHICLE_CLASSES   = {CAR, MOTORCYCLE, BUS, TRUCK, BICYCLE}
LARGE_VEHICLE     = {BUS, TRUCK}
ANIMAL_CLASSES    = {CAT, DOG, HORSE, COW}
LUGGAGE_CLASSES   = {BACKPACK, SUITCASE, HANDBAG}


@dataclass
class IncidentResult:
    incident_type: str
    confidence: float
    severity: str          # low | medium | high | critical
    description: str
    object_count: int
    bbox_list: list = field(default_factory=list)


def _iou(a, b) -> float:
    """Intersection over union of two [x1,y1,x2,y2] boxes."""
    ix1 = max(a[0], b[0]); iy1 = max(a[1], b[1])
    ix2 = min(a[2], b[2]); iy2 = min(a[3], b[3])
    inter = max(0, ix2 - ix1) * max(0, iy2 - iy1)
    if inter == 0:
        return 0.0
    area_a = (a[2]-a[0]) * (a[3]-a[1])
    area_b = (b[2]-b[0]) * (b[3]-b[1])
    return inter / (area_a + area_b - inter)


def _centroid(box):
    return ((box[0]+box[2])/2, (box[1]+box[3])/2)


def _dist(a, b):
    return ((a[0]-b[0])**2 + (a[1]-b[1])**2) ** 0.5


class IncidentClassifier:
    def __init__(self, frame_history: int = 8):
        self._history: list[list] = []   # list of frames, each frame = list of objects
        self._frame_history = frame_history

    def classify(self, detections, frame: np.ndarray) -> IncidentResult:
        if detections is None or len(detections.boxes) == 0:
            return IncidentResult("none", 0.0, "low", "No objects detected", 0)

        H, W = frame.shape[:2]
        boxes = detections.boxes

        # Parse all detections
        objects = []
        for box in boxes:
            cls_id = int(box.cls[0])
            conf   = float(box.conf[0])
            xyxy   = box.xyxy[0].tolist()
            objects.append({"cls": cls_id, "conf": conf, "xyxy": xyxy})

        self._history.append(objects)
        if len(self._history) > self._frame_history:
            self._history.pop(0)

        # Partition by category
        persons   = [o for o in objects if o["cls"] == PERSON]
        vehicles  = [o for o in objects if o["cls"] in VEHICLE_CLASSES]
        large_veh = [o for o in objects if o["cls"] in LARGE_VEHICLE]
        animals   = [o for o in objects if o["cls"] in ANIMAL_CLASSES]
        luggage   = [o for o in objects if o["cls"] in LUGGAGE_CLASSES]
        bottles   = [o for o in objects if o["cls"] == BOTTLE]
        t_lights  = [o for o in objects if o["cls"] == TRAFFIC_LIGHT]

        bbox_list = [
            {"class": COCO_NAMES.get(o["cls"], str(o["cls"])),
             "conf": round(o["conf"], 2),
             "xyxy": [round(x, 1) for x in o["xyxy"]]}
            for o in objects if o["cls"] in COCO_NAMES
        ]

        # ── Frame colour analysis ─────────────────────────────────────────────
        lower = frame[H//2:, :]
        b_mean = float(lower[:,:,0].mean())
        g_mean = float(lower[:,:,1].mean())
        r_mean = float(lower[:,:,2].mean())
        brightness = (b_mean + g_mean + r_mean) / 3.0

        # Clear blue floodwater
        blue_water = b_mean > g_mean * 1.05 and b_mean > r_mean * 1.05
        # Murky/brown/grey floodwater — channels roughly equal (grey-brown), moderate brightness
        murky_water = (abs(b_mean - g_mean) < 30 and abs(g_mean - r_mean) < 30
                       and 30 < brightness < 160
                       and r_mean < 160)  # not a bright sunny/dry road (which is very bright grey)
        # Reflective wet road — mid-brightness uniform grey (not bright-dry road)
        wet_road = (60 < brightness < 145 and abs(b_mean - r_mean) < 35
                    and abs(g_mean - r_mean) < 30 and r_mean < 145)

        water_hue = blue_water or murky_water or wet_road
        dark_scene = frame.mean() < 60   # nighttime / very dark

        # ── Rule 1: ROAD ACCIDENT ─────────────────────────────────────────────
        # Heuristics: overlapping vehicle bboxes (collision), vehicle at steep
        # angle (bbox width < height = tipped), persons near vehicle in lower frame,
        # multiple vehicles with high overlap.
        accident = self._check_accident(vehicles, persons, H, W)
        if accident:
            conf, desc = accident
            sev = "critical" if conf > 0.80 else "high"
            return IncidentResult("road_accident", conf, sev, desc, len(objects), bbox_list)

        # ── Rule 2: VEHICLE STRANDED (in flood/hazard) ────────────────────────
        if len(self._history) >= 3 and vehicles:
            stranded = self._count_stationary(vehicles, VEHICLE_CLASSES)
            if stranded >= 1:
                if water_hue:
                    conf = min(0.78 + stranded * 0.07, 0.95)
                    return IncidentResult("vehicle_stranded", conf, "critical",
                        f"{stranded} vehicle(s) stationary in flood water", len(objects), bbox_list)

        # ── Rule 3: ROAD FLOOD ────────────────────────────────────────────────
        if water_hue and len(vehicles) >= 1:
            conf = min(0.70 + len(vehicles) * 0.04, 0.92)
            sev  = "critical" if len(vehicles) >= 3 else "high"
            return IncidentResult("road_flood", conf, sev,
                f"Floodwater on road, {len(vehicles)} vehicle(s) in scene", len(objects), bbox_list)

        # ── Rule 4: CROWD SURGE ───────────────────────────────────────────────
        road_persons = [p for p in persons if p["xyxy"][3] > H * 0.35]
        if len(road_persons) >= 6:
            conf = min(0.62 + len(road_persons) * 0.025, 0.92)
            sev  = "critical" if len(road_persons) >= 10 else "high"
            return IncidentResult("crowd_surge", conf, sev,
                f"{len(road_persons)} people massing at road level — crowd risk", len(objects), bbox_list)

        # ── Rule 5: WOMEN SAFETY ─────────────────────────────────────────────
        # Lone person surrounded by ≥3 others, especially in dark scene.
        # Proxy: one person whose centroid is within close range of 3+ others.
        safety = self._check_women_safety(persons, H, W, dark_scene)
        if safety:
            conf, desc = safety
            return IncidentResult("women_safety", conf, "high", desc, len(objects), bbox_list)

        # ── Rule 6: FIGHT / VIOLENCE ──────────────────────────────────────────
        # Person bbox overlaps with another at ground level, or person occupying
        # very low position (fallen), surrounded by others standing.
        fight = self._check_fight(persons, H)
        if fight:
            conf, desc = fight
            return IncidentResult("fight_violence", conf, "high", desc, len(objects), bbox_list)

        # ── Rule 7: ROAD BLOCKED ─────────────────────────────────────────────
        if large_veh:
            for v in large_veh:
                x1,y1,x2,y2 = v["xyxy"]
                w, h = x2-x1, y2-y1
                if w > h * 1.6:   # sideways
                    return IncidentResult("road_blocked", 0.82, "high",
                        "Large vehicle blocking road (sideways orientation)", len(objects), bbox_list)
            if len(large_veh) >= 2:
                return IncidentResult("road_blocked", 0.75, "high",
                    f"{len(large_veh)} large vehicles stacked — road likely blocked", len(objects), bbox_list)

        # ── Rule 8: ANIMAL ON ROAD ────────────────────────────────────────────
        if animals:
            road_animals = [a for a in animals if a["xyxy"][3] > H * 0.4]
            if road_animals:
                large = [a for a in road_animals if a["cls"] in {HORSE, COW}]
                conf  = 0.85 if large else 0.72
                name  = COCO_NAMES.get(road_animals[0]["cls"], "animal")
                sev   = "high" if large else "medium"
                return IncidentResult("animal_on_road", conf, sev,
                    f"{name.capitalize()} on road — collision risk", len(objects), bbox_list)

        # ── Rule 9: ABANDONED OBJECT ──────────────────────────────────────────
        # Unattended luggage/bag with no person within 150px for 3+ frames.
        abandoned = self._check_abandoned(luggage, persons, H)
        if abandoned:
            return IncidentResult("abandoned_object", 0.72, "medium",
                "Unattended bag/luggage in public area — security concern", len(objects), bbox_list)

        # ── Rule 10: TRAFFIC SIGNAL ISSUE ────────────────────────────────────
        # Many vehicles at intersection but no active traffic light detected,
        # or vehicles bunching at a stop sign with no movement.
        if len(vehicles) >= 4 and len(t_lights) == 0:
            stationary = self._count_stationary(vehicles, VEHICLE_CLASSES)
            if stationary >= 3:
                return IncidentResult("traffic_signal_issue", 0.68, "medium",
                    f"{stationary} vehicles stopped — possible signal failure or jam", len(objects), bbox_list)

        # ── Rule 11: GARBAGE DUMPING ──────────────────────────────────────────
        # High bottle count in outdoor scene (proxy for littering/waste dump).
        if len(bottles) >= 4:
            return IncidentResult("garbage_dumping", 0.65, "low",
                f"{len(bottles)} bottles/waste items detected — sanitation issue", len(objects), bbox_list)

        # ── Rule 12: BUILDING DAMAGE ─────────────────────────────────────────
        # Heuristic: large grey/brown area in upper frame (debris, dust cloud)
        # with no sky blue, combined with person presence.
        if persons and self._check_debris_hue(frame, H, W):
            return IncidentResult("building_damage", 0.62, "high",
                "Dust/debris cloud with persons present — possible structural damage", len(objects), bbox_list)

        return IncidentResult("none", 0.0, "low", "Scene normal", len(objects), bbox_list)

    # ── Helper methods ────────────────────────────────────────────────────────

    def _check_accident(self, vehicles, persons, H, W):
        """Detect vehicle collisions via bbox overlap, odd aspect ratios, person proximity."""
        if not vehicles:
            return None

        # Multiple vehicles in lower 2/3 of frame = busy road intersection, raise sensitivity
        road_vehicles = [v for v in vehicles if v["xyxy"][3] > H * 0.3]

        # Overlapping vehicle bboxes = collision (lowered threshold: 0.05)
        for i, v1 in enumerate(road_vehicles):
            for v2 in road_vehicles[i+1:]:
                iou = _iou(v1["xyxy"], v2["xyxy"])
                if iou > 0.05:
                    conf = min(0.72 + iou * 2.0, 0.95)
                    return (conf, f"Vehicle collision detected — bounding boxes overlapping (IoU={iou:.2f})")

        # Tipped/overturned vehicle: taller than wide for car/motorcycle
        for v in road_vehicles:
            if v["cls"] in {CAR, MOTORCYCLE}:
                x1,y1,x2,y2 = v["xyxy"]
                bw, bh = x2-x1, y2-y1
                if bh > bw * 1.05 and bh > 40:
                    return (0.80, "Overturned/tipped vehicle detected")

        # Vehicles very close together (near miss / rear-end) — centroid distance < 60px
        for i, v1 in enumerate(road_vehicles):
            for v2 in road_vehicles[i+1:]:
                d = _dist(_centroid(v1["xyxy"]), _centroid(v2["xyxy"]))
                # Distance relative to their sizes — if centroids closer than sum of half-widths
                w1 = (v1["xyxy"][2] - v1["xyxy"][0]) / 2
                w2 = (v2["xyxy"][2] - v2["xyxy"][0]) / 2
                if d < (w1 + w2) * 0.9:
                    return (0.78, "Vehicles in dangerously close proximity — possible collision")

        # Person on road close to any vehicle (pedestrian struck / accident victim)
        for p in persons:
            px1,py1,px2,py2 = p["xyxy"]
            ph = py2 - py1
            pw = px2 - px1
            pc = _centroid(p["xyxy"])
            person_fallen = pw > ph * 1.1  # wider than tall = lying down (lowered from 1.2)
            if person_fallen and py2 > H * 0.3:
                for v in road_vehicles:
                    if _dist(pc, _centroid(v["xyxy"])) < 160:
                        return (0.85, "Person fallen near vehicle — possible accident victim")

            # Person in road zone close to vehicle (within 100px)
            if py2 > H * 0.4:
                for v in road_vehicles:
                    if _dist(pc, _centroid(v["xyxy"])) < 100:
                        return (0.70, "Person in close proximity to vehicle on road")

        # 3+ vehicles suddenly stationary in road zone (post-collision jam)
        if len(self._history) >= 3 and len(road_vehicles) >= 3:
            stationary = self._count_stationary(road_vehicles, VEHICLE_CLASSES)
            if stationary >= 3:
                return (0.72, f"{stationary} vehicles stopped on road — possible accident blockage")

        return None

    def _check_women_safety(self, persons, H, W, dark_scene):
        """Lone person surrounded by a cluster of others — potential safety concern."""
        if len(persons) < 3:
            return None

        for i, target in enumerate(persons):
            tc = _centroid(target["xyxy"])
            nearby = [p for j, p in enumerate(persons) if j != i and _dist(tc, _centroid(p["xyxy"])) < 120]
            if len(nearby) >= 3:
                # Target is surrounded
                conf = 0.65
                desc = f"Person surrounded by {len(nearby)} others"
                if dark_scene:
                    conf += 0.12
                    desc += " in low-light conditions — safety alert"
                else:
                    desc += " — monitoring for safety"
                if conf >= 0.68:
                    return (conf, desc)
        return None

    def _check_fight(self, persons, H):
        """Detect fighting: person bbox overlapping another, or person fallen on ground."""
        if len(persons) < 2:
            return None

        # Person with very wide bbox relative to height = fallen/on ground
        for p in persons:
            x1,y1,x2,y2 = p["xyxy"]
            pw, ph = x2-x1, y2-y1
            if pw > ph * 1.4 and y2 > H * 0.4:
                # Check others nearby standing
                pc = _centroid(p["xyxy"])
                nearby_standing = [
                    o for o in persons
                    if o is not p
                    and _dist(pc, _centroid(o["xyxy"])) < 150
                    and (o["xyxy"][3] - o["xyxy"][1]) > (o["xyxy"][2] - o["xyxy"][0])  # taller than wide = standing
                ]
                if nearby_standing:
                    return (0.78, f"Person on ground with {len(nearby_standing)} others nearby — possible assault")

        # Two persons whose bboxes significantly overlap
        for i, p1 in enumerate(persons):
            for p2 in persons[i+1:]:
                if _iou(p1["xyxy"], p2["xyxy"]) > 0.20:
                    return (0.72, "Physical altercation — persons in contact")

        return None

    def _check_abandoned(self, luggage, persons, H):
        """Luggage with no person within 150px for several consecutive frames."""
        if not luggage or len(self._history) < 3:
            return False

        for bag in luggage:
            if bag["xyxy"][3] < H * 0.3:
                continue  # skip if in upper frame (unlikely on ground)
            bc = _centroid(bag["xyxy"])
            owner_nearby = any(_dist(bc, _centroid(p["xyxy"])) < 150 for p in persons)
            if owner_nearby:
                continue

            # Check if this bag was also unattended in the last 3 frames
            unattended_frames = 0
            for hist_frame in self._history[-3:]:
                hist_persons = [o for o in hist_frame if o["cls"] == PERSON]
                hist_bags    = [o for o in hist_frame if o["cls"] in LUGGAGE_CLASSES]
                if not hist_bags:
                    break
                closest_bag = min(hist_bags, key=lambda b: _dist(bc, _centroid(b["xyxy"])), default=None)
                if closest_bag:
                    hbc = _centroid(closest_bag["xyxy"])
                    if _dist(bc, hbc) < 60:  # same bag position
                        if not any(_dist(hbc, _centroid(p["xyxy"])) < 150 for p in hist_persons):
                            unattended_frames += 1

            if unattended_frames >= 2:
                return True
        return False

    def _check_debris_hue(self, frame: np.ndarray, H, W) -> bool:
        """Upper half is dominated by grey/brown (dust, debris, smoke) = structural damage proxy."""
        upper = frame[:H//2, :]
        b = float(upper[:,:,0].mean())
        g = float(upper[:,:,1].mean())
        r = float(upper[:,:,2].mean())
        # Dust/debris: channels roughly equal, medium brightness, no blue sky
        grey_brown = abs(b-g) < 25 and abs(g-r) < 25 and 40 < r < 160
        no_blue_sky = not (b > g * 1.1 and b > 100)
        return grey_brown and no_blue_sky

    def _count_stationary(self, current_objs, cls_set) -> int:
        """Count objects whose centroid hasn't moved >35px across last 3 frames."""
        if len(self._history) < 3:
            return 0
        stationary = 0
        for obj in current_objs:
            if obj["cls"] not in cls_set:
                continue
            cx, cy = _centroid(obj["xyxy"])
            matches = 0
            for hist in self._history[-3:]:
                same_cls = [o for o in hist if o["cls"] in cls_set]
                if any(_dist((cx,cy), _centroid(o["xyxy"])) < 35 for o in same_cls):
                    matches += 1
            if matches >= 2:
                stationary += 1
        return stationary
