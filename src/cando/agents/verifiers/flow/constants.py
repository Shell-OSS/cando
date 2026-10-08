# Copyright © 2026 Shell Information Technology International B.V.
# All Rights Reserved.

"""Constants for flow verification agent."""

ALL_SCENARIOS = {
    "car": [
        ["Car Service Area"],
        ["Car Service Area", "Car Parking"],
        ["Car Service Area", "Priority Parking"],
        ["Car Parking"],
        ["EV Service Area"],
    ],
    "bike": [
        ["Bike Service Area"],
        ["Bike Service Area", "Bike Parking"],
        ["Bike Parking"],
    ],
    "hgv": [
        ["HGV Service Area"],
        ["HGV Service Area", "HGV Parking"],
        ["HGV Parking"],
    ],
}

AVERAGE_VEHICLE_SIZES = {
    "car": (4.5, 1.8),
    "bike": (2.0, 0.75),
    "hgv": (16.5, 2.55),
}

UNDERGROUND_CLASSES = ["Underground Service Facility"]

NO_BACKTRACKING_CLASSES = [
    "Car Service Area",
    "Bike Service Area",
    "HGV Service Area",
]
