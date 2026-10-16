# Copyright © 2026 Shell Information Technology International B.V.
# All Rights Reserved.

"""Utility functions for converting between polygons and bounding boxes."""

import logging
import re
from typing import Literal

import numpy as np
import pandas as pd
from shapely.geometry import Polygon

logger = logging.getLogger(__name__)


def polygon_to_bbox(
    polygon: Polygon,
) -> tuple[float, float, float, float, float]:
    """
    Converts a shapely polygon to bounding box format
    (min_x, min_y, max_x, max_y, r), where:
    - (min_x, min_y) is the bottom-left corner
    - (max_x, max_y) is the top-right corner
    - r is the counter-clockwise rotation angle in degrees (0-180)
    """
    rect = polygon.minimum_rotated_rectangle

    # Shapely closes rings by repeating the first point at the end; drop it.
    coords = np.array(rect.exterior.coords)[:-1]

    min_x = coords[:, 0].min()
    min_y = coords[:, 1].min()
    max_x = coords[:, 0].max()
    max_y = coords[:, 1].max()

    # Calculate rotation angle
    edges = np.diff(coords, axis=0)
    lengths = np.sqrt(np.sum(edges**2, axis=1))
    longest_edge_idx = np.argmax(lengths)
    edge = edges[longest_edge_idx]
    angle = np.degrees(np.arctan2(edge[1], edge[0]))

    angle = angle % 180

    return (
        float(round(min_x, 2)),
        float(round(min_y, 2)),
        float(round(max_x, 2)),
        float(round(max_y, 2)),
        float(round(angle, 2)),
    )


def bbox_to_polygon(
    bbox: tuple[float, float, float, float, float],
) -> Polygon:
    """Converts rotated bounding box format to polygon coordinates.

    Args:
        bbox (tuple): (x, y, w, h, theta)
            where:
            - (x,y) is the center point
            - w,h are width and height
            - theta is the counter-clockwise rotation angle in degrees (0-180)

    Returns:
        Polygon: Shapely Polygon object representing the rotated rectangle.
    """

    x, y, w, h, theta = bbox

    theta_rad = np.radians(theta)

    # Corner offsets before rotation, relative to the center.
    dx = w / 2
    dy = h / 2
    corners = np.array(
        [
            [-dx, -dy],  # bottom left
            [dx, -dy],  # bottom right
            [dx, dy],  # top right
            [-dx, dy],  # top left
        ]
    )

    rotation_matrix = np.array(
        [
            [np.cos(theta_rad), -np.sin(theta_rad)],
            [np.sin(theta_rad), np.cos(theta_rad)],
        ]
    )

    rotated_corners = np.dot(corners, rotation_matrix.T)
    rotated_corners[:, 0] += x
    rotated_corners[:, 1] += y

    # Shapely requires closed rings: repeat the first point at the end.
    polygon_coords = np.vstack((rotated_corners, rotated_corners[0]))

    return Polygon(polygon_coords)


def get_rounded(x: Polygon, y: int) -> Polygon | float:
    """Rounds the coordinates of a polygon to y decimal places."""
    a = list(x.exterior.coords)
    a = [(round(i[0], y), round(i[1], y)) for i in a]
    try:
        return Polygon(a)
    except Exception as exc:
        logger.debug("Failed to round polygon coordinates: %s", exc)
        return np.nan


def parse_geometry(geometry_input: str | dict) -> Polygon:
    """Converts a string or dict representation into a Shapely Polygon.

    Handles formats:
    - (x1 y1, x2 y2, ...)
    - ((x1,y1), (x2,y2), ...)
    - ((x1, y1), (x2, y2), ...)  # with space after comma
    - ((x1 y1, x2 y2, ...))
    - Polygon(x1 y1, x2 y2, ...)
    - Polygon((x1,y1), (x2,y2), ...)
    - Polygon(x1, y1, x2, y2, ...)  # sequential coordinates
    - {'x': x, 'y': y, 'w': w, 'h': h, 'angle': angle}  # rotated box
    """

    geometry_str = str(geometry_input)
    geometry_str = re.sub(r"Polygon\s*", "", geometry_str)
    geometry_str = re.sub(r"POLYGON\s*", "", geometry_str)
    geometry_str = geometry_str.strip("()")

    # Rotated box dictionary format: {'x','y','w','h','angle'} as 5 CSV
    # values.
    if "), (" not in geometry_str and len(geometry_str.split(",")) == 5:
        try:
            x_str, y_str, w_str, h_str, angle_str = geometry_str.split(",")
            x, y, w, h, angle = (
                round(float(x_str), 2),
                round(float(y_str), 2),
                round(float(w_str), 2),
                round(float(h_str), 2),
                round(float(angle_str), 2),
            )

            # Convert angle to radians
            angle_rad = np.radians(angle)

            # Calculate the four corners of the rotated rectangle
            cos_a = np.cos(angle_rad)
            sin_a = np.sin(angle_rad)

            w2, h2 = w / 2, h / 2

            corners = [(-w2, -h2), (w2, -h2), (w2, h2), (-w2, h2)]

            rotated_corners = [
                (x + (px * cos_a - py * sin_a), y + (px * sin_a + py * cos_a))
                for px, py in corners
            ]

            # Shapely requires closed rings: repeat the first point at
            # the end.
            rotated_corners.append(rotated_corners[0])

            return Polygon(rotated_corners)
        except ValueError:
            # If conversion fails, treat as regular string format
            pass

    coords: list[tuple[float, float]] = []

    try:
        # Split on '), (' if present (handles nested parentheses format)
        if "), (" in geometry_str:
            pairs = geometry_str.split("), (")
            for pair in pairs:
                pair = pair.strip("() ")
                if "," in pair:
                    # Handle "x,y" format
                    x, y = map(float, pair.split(","))
                else:
                    # Handle "x y" format
                    x, y = map(float, pair.split())
                coords.append((x, y))
            return Polygon(coords)
        else:
            # Handle sequential coordinates "x1, y1, x2, y2, ..."
            numbers = [float(x.strip()) for x in geometry_str.split(",")]
            coords = list(zip(numbers[::2], numbers[1::2], strict=False))
            return Polygon(coords)
    except ValueError:
        # If conversion fails, try alternative parsing methods
        pass

    try:
        numbers = [float(x.strip()) for x in geometry_str.split(",")]
        coords = list(zip(numbers[::2], numbers[1::2], strict=False))
        return Polygon(coords)
    except ValueError:
        # If conversion fails, try alternative parsing methods
        pass

    try:
        parsed: list[tuple[float, float]] = []
        for point in geometry_str.split(","):
            x, y = map(float, point.split())
            parsed.append((x, y))
        coords = parsed
    except ValueError:
        pass

    return Polygon(coords)


def dataframe_to_structured_format(
    df: pd.DataFrame, polygons_format: Literal["list", "polygons"] = "list"
) -> dict:
    """
    Converts layout dataframe to a structured dict format.

    Args:
        df (pd.DataFrame): DataFrame with columns ['id', 'ann_class_id',
            'geometry', 'bbox'].
        polygons_format (Literal["list", "polygons"]): Format of the polygon
            coordinates in the output. "list" returns coordinates as lists of
            [x, y], "polygons" returns Shapely Polygon objects.

    Returns:
        dict: Structured dictionary with 'elements' key containing list of
            element dictionaries.

            Example:
            {
                "elements": [
                    {
                        "id": ...,
                        "class": ...,
                        "polygon": [[x1, y1], [x2, y2], ...],
                        "bbox": {
                            "min_x": ...,
                            "min_y": ...,
                            "max_x": ...,
                            "max_y": ...
                        },
                        "rotation_deg": ...
                    },
                    ...
            }
    """
    df_copy = df.copy()

    if polygons_format == "list":
        df_copy["polygon"] = df_copy["geometry"].apply(
            lambda geom: [
                [round(x, 2), round(y, 2)]
                for x, y in list(geom.exterior.coords)[:-1]
            ]
        )
    else:
        df_copy["polygon"] = df_copy["geometry"].apply(
            lambda geom: get_rounded(geom, 2)
        )

    elements = df_copy.apply(
        lambda row: {
            "id": row["id"],
            "class": row["ann_class_id"],
            "polygon": row["polygon"],
            "bbox": {
                "min_x": row["bbox"][0][0],
                "min_y": row["bbox"][0][1],
                "max_x": row["bbox"][0][2],
                "max_y": row["bbox"][0][3],
            },
            "rotation_deg": row["bbox"][0][4],
        },
        axis=1,
    ).tolist()

    return {"elements": elements}


def convert_to_bbox_layout(layout: dict, fixed_classes: list[str]) -> dict:
    """Converts polygon-based layout to bbox representation for refinement.

    Converts all elements (except fixed classes like Layout/Entrance/Exit)
    from polygon format to bounding box format with center coordinates,
    dimensions, and rotation.

    Args:
        layout (dict): Layout with polygon-based elements.
        fixed_classes (list[str]): List of class names to keep in polygon
            format (e.g., ['Layout Polygon', 'Entrance', 'Exit']).

    Returns:
        dict: Layout with bbox representation:
            - Fixed elements: Keep polygon format
            - Other elements: Convert to {id, class, x, y, width, height,
              theta}
    """
    elements = layout.get("elements", [])
    bbox_elements = []

    for element in elements:
        elem_class = element.get("class", "")
        elem_id = element.get("id")
        polygon_coords = element.get("polygon", [])

        # Keep fixed elements as polygons (read-only reference).
        if elem_class in fixed_classes:
            bbox_elements.append(
                {
                    "id": elem_id,
                    "class": elem_class,
                    "polygon": polygon_coords,
                }
            )
            continue

        if not polygon_coords or len(polygon_coords) < 3:
            continue

        try:
            poly = Polygon(polygon_coords)
            if not poly.is_valid:
                poly = poly.buffer(0)
        except Exception as exc:
            logger.debug(
                "Skipping element %s: invalid polygon (%s)", elem_id, exc
            )
            continue

        min_rect = poly.minimum_rotated_rectangle
        rect_coords = list(min_rect.exterior.coords)

        if len(rect_coords) >= 4:
            edge1 = (
                rect_coords[1][0] - rect_coords[0][0],
                rect_coords[1][1] - rect_coords[0][1],
            )
            edge2 = (
                rect_coords[2][0] - rect_coords[1][0],
                rect_coords[2][1] - rect_coords[1][1],
            )

            len1 = (edge1[0] ** 2 + edge1[1] ** 2) ** 0.5
            len2 = (edge2[0] ** 2 + edge2[1] ** 2) ** 0.5

            # Width is the longer edge, height is the shorter
            if len1 >= len2:
                width = len1
                height = len2
                rotation_rad = np.arctan2(edge1[1], edge1[0])
            else:
                width = len2
                height = len1
                rotation_rad = np.arctan2(edge2[1], edge2[0])

            # Normalize rotation to [-pi/2, pi/2]
            while rotation_rad > np.pi / 2:
                rotation_rad -= np.pi
            while rotation_rad < -np.pi / 2:
                rotation_rad += np.pi

            theta = round(np.degrees(rotation_rad), 2)

            centroid = poly.centroid
            x = round(centroid.x, 2)
            y = round(centroid.y, 2)

            bbox_elements.append(
                {
                    "id": elem_id,
                    "class": elem_class,
                    "x": x,
                    "y": y,
                    "width": round(width, 2),
                    "height": round(height, 2),
                    "theta": theta,
                }
            )

    return {"elements": bbox_elements}


def convert_to_polygons_layout(
    bbox_layout: dict, fixed_classes: list[str]
) -> dict:
    """Converts bbox layout to polygon layout.

    Args:
        bbox_layout (dict): Layout in bbox format.
        fixed_classes (list[str]): List of class names already in polygon
            format (e.g., ['Layout Polygon', 'Entrance', 'Exit']).

    Returns:
        dict: Layout in polygon format.
    """
    elements = bbox_layout.get("elements", [])
    polygon_elements = []

    for element in elements:
        elem_id = element.get("id")
        elem_class = element.get("class", "")

        # Fixed elements are already in polygon format
        if elem_class in fixed_classes:
            polygon_elements.append(element.copy())
            continue

        x = element.get("x")
        y = element.get("y")
        width = element.get("width")
        height = element.get("height")
        theta = element.get("theta", 0)

        bbox_tuple = (x, y, width, height, theta)
        poly = bbox_to_polygon(bbox_tuple)

        coords = list(poly.exterior.coords)
        polygon = [[round(px, 2), round(py, 2)] for px, py in coords[:-1]]
        polygon.append(polygon[0])  # Close the polygon

        polygon_elements.append(
            {"id": elem_id, "class": elem_class, "polygon": polygon}
        )

    return {"elements": polygon_elements}
