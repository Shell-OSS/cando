# Copyright © 2026 Shell Information Technology International B.V.
# All Rights Reserved.

"""Utility functions for hard constraints verification."""

from shapely.geometry import MultiPolygon, Point, Polygon
from shapely.ops import nearest_points


def corrections_for_outside_geom(
    outside_geom, layout_polygon
) -> tuple[float, float]:
    """Computes signed per-axis corrections to move `outside_geom` fully
    inside `layout_polygon`.

    For each vertex of the outside geometry the nearest point on the layout
    boundary is found. The signed per-axis delta (boundary - vertex) indicates
    both the distance and the direction the element must travel along each
    axis. The delta with the largest absolute value is kept per axis, so that
    even the furthest-outside vertex is covered.

    The sign convention is:
        - Positive x_correction: element must move in the +x direction.
        - Negative x_correction: element must move in the -x direction.
        - Positive y_correction: element must move in the +y direction.
        - Negative y_correction: element must move in the -y direction.

    Args:
        outside_geom: Shapely geometry representing the region (or element)
            that is outside the layout.
        layout_polygon: Shapely Polygon for the layout boundary.

    Returns:
        tuple[float, float]: (x_correction, y_correction)
    """
    layout_boundary = layout_polygon.exterior

    # Collect all vertices from outside geometry.
    if isinstance(outside_geom, Polygon):
        polys = [outside_geom]
    elif isinstance(outside_geom, MultiPolygon):
        polys = list(outside_geom.geoms)
    else:
        polys = [outside_geom]

    x_correction = 0.0
    y_correction = 0.0

    for poly in polys:
        if not hasattr(poly, "exterior"):
            continue
        for x, y in poly.exterior.coords:
            p_vertex = Point(x, y)
            _, p_boundary = nearest_points(p_vertex, layout_boundary)
            dx = p_boundary.x - x
            dy = p_boundary.y - y
            if abs(dx) > abs(x_correction):
                x_correction = dx
            if abs(dy) > abs(y_correction):
                y_correction = dy

    return x_correction, y_correction
