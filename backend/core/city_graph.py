"""
Hyderabad city graph loaded from OpenStreetMap.
Nodes = intersections. Edges = road segments with flood/capacity metadata.
"""
import os
import json
import pickle
from pathlib import Path
import osmnx as ox
import networkx as nx
import numpy as np

# Real Hyderabad bounding box
HYDERABAD_BBOX = (17.3, 78.3, 17.5, 78.6)  # south, west, north, east
CACHE_PATH = Path(__file__).parent.parent.parent / "data" / "hyderabad_graph.pkl"

# Real flood-prone underpasses in Hyderabad (lat, lng, name, threshold_mm)
FLOOD_ZONES = [
    # flood_threshold_mm = mm/hr rainfall that causes this underpass to flood
    # Sept 2024: peak recorded ~115mm/hr at Banjara Hills station
    # Thresholds scaled so scenario hits critical at peak ticks
    {"id": "mehdipatnam_up", "name": "Mehdipatnam Underpass", "lat": 17.3957, "lng": 78.4290, "flood_threshold_mm": 400, "depth_at_threshold_m": 1.2},
    {"id": "tolichowki_up", "name": "Tolichowki Underpass",   "lat": 17.4052, "lng": 78.4107, "flood_threshold_mm": 350, "depth_at_threshold_m": 0.9},
    {"id": "narayanguda_up","name": "Narayanguda Underpass",  "lat": 17.3921, "lng": 78.4867, "flood_threshold_mm": 280, "depth_at_threshold_m": 0.8},
    {"id": "malakpet_up",   "name": "Malakpet Underpass",     "lat": 17.3700, "lng": 78.5000, "flood_threshold_mm": 310, "depth_at_threshold_m": 1.0},
    {"id": "lb_nagar_up",   "name": "LB Nagar Underpass",     "lat": 17.3468, "lng": 78.5521, "flood_threshold_mm": 260, "depth_at_threshold_m": 0.7},
]

# Critical infrastructure nodes
HOSPITALS = [
    {"id": "osmania",      "name": "Osmania General Hospital",  "lat": 17.3814, "lng": 78.4778, "capacity": 1200},
    {"id": "nizams",       "name": "Nizam's Institute (NIMS)",  "lat": 17.4063, "lng": 78.4605, "capacity": 800},
    {"id": "yashoda_sec",  "name": "Yashoda Hospital Secunderabad", "lat": 17.4399, "lng": 78.4983, "capacity": 500},
    {"id": "care_banjara", "name": "CARE Hospitals Banjara Hills", "lat": 17.4156, "lng": 78.4347, "capacity": 400},
    {"id": "apollo",       "name": "Apollo Hospitals Jubilee Hills", "lat": 17.4241, "lng": 78.4087, "capacity": 600},
]

SUBSTATIONS = [
    {"id": "sub_mehdipatnam", "name": "Mehdipatnam Substation",   "lat": 17.3960, "lng": 78.4310, "load_mw": 85,  "flood_risk": True},
    {"id": "sub_banjara",     "name": "Banjara Hills Substation", "lat": 17.4200, "lng": 78.4300, "load_mw": 120, "flood_risk": False},
    {"id": "sub_ameerpet",    "name": "Ameerpet Substation",      "lat": 17.4375, "lng": 78.4483, "load_mw": 110, "flood_risk": False},
    {"id": "sub_lb_nagar",    "name": "LB Nagar Substation",      "lat": 17.3470, "lng": 78.5510, "load_mw": 75,  "flood_risk": True},
]

# Power dependency: hospital_id -> substation_id (primary)
POWER_DEPENDENCIES = {
    "osmania":      "sub_mehdipatnam",
    "nizams":       "sub_banjara",
    "yashoda_sec":  "sub_ameerpet",
    "care_banjara": "sub_banjara",
    "apollo":       "sub_banjara",
}


def load_city_graph(force_reload: bool = False) -> nx.MultiDiGraph:
    """Load Hyderabad road network. Caches to disk after first fetch."""
    if CACHE_PATH.exists() and not force_reload:
        with open(CACHE_PATH, "rb") as f:
            G = pickle.load(f)
        print(f"[CityGraph] Loaded from cache: {len(G.nodes)} nodes, {len(G.edges)} edges")
        return G

    print("[CityGraph] Fetching from OpenStreetMap (first run, ~30s)...")

    south, west, north, east = HYDERABAD_BBOX
    # osmnx v2 API: graph_from_bbox(bbox=(n,s,e,w))
    G = ox.graph_from_bbox(
        bbox=(north, south, east, west),
        network_type="drive",
        simplify=True,
    )
    G = ox.routing.add_edge_speeds(G)
    G = ox.routing.add_edge_travel_times(G)

    # Annotate flood-susceptible edges near flood zones
    _annotate_flood_susceptibility(G)

    CACHE_PATH.parent.mkdir(parents=True, exist_ok=True)
    with open(CACHE_PATH, "wb") as f:
        pickle.dump(G, f)

    print(f"[CityGraph] Fetched and cached: {len(G.nodes)} nodes, {len(G.edges)} edges")
    return G


def _annotate_flood_susceptibility(G: nx.MultiDiGraph):
    """Mark edges within 500m of known flood zones as flood-susceptible."""
    for u, v, k, data in G.edges(keys=True, data=True):
        data["flood_susceptible"] = False
        data["flood_zone_id"] = None
        data["congestion"] = 0.0  # 0.0–1.0
        data["blocked"] = False

    for zone in FLOOD_ZONES:
        # Find nearest node to flood zone
        nearest = ox.nearest_nodes(G, zone["lng"], zone["lat"])
        # Mark edges within 2-hop radius
        subgraph_nodes = nx.ego_graph(G, nearest, radius=2).nodes()
        for u in subgraph_nodes:
            for v, k, data in G[u].items() if hasattr(G[u], 'items') else []:
                pass  # networkx MultiDiGraph iteration
        for u, v, k, data in G.edges(keys=True, data=True):
            u_data = G.nodes[u]
            v_data = G.nodes[v]
            u_lat, u_lng = u_data.get("y", 0), u_data.get("x", 0)
            dist = _haversine(u_lat, u_lng, zone["lat"], zone["lng"])
            if dist < 0.5:  # 500m
                data["flood_susceptible"] = True
                data["flood_zone_id"] = zone["id"]


def _haversine(lat1, lng1, lat2, lng2) -> float:
    """Return distance in km."""
    R = 6371
    dlat = np.radians(lat2 - lat1)
    dlng = np.radians(lng2 - lng1)
    a = np.sin(dlat/2)**2 + np.cos(np.radians(lat1)) * np.cos(np.radians(lat2)) * np.sin(dlng/2)**2
    return R * 2 * np.arcsin(np.sqrt(a))


def get_infrastructure() -> dict:
    return {
        "flood_zones": FLOOD_ZONES,
        "hospitals": HOSPITALS,
        "substations": SUBSTATIONS,
        "power_dependencies": POWER_DEPENDENCIES,
    }
