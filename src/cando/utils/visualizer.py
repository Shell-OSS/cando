# Copyright © 2026 Shell Information Technology International B.V.
# All Rights Reserved.

"""Utility functions for visualizing geometric layouts."""

import base64
import io

import geopandas as gpd
import matplotlib.pyplot as plt
import numpy as np
from matplotlib.backends.backend_agg import FigureCanvasAgg
from matplotlib.figure import Figure
from shapely import Polygon

from cando.constants.classes import INPUT_CLASSES
from cando.constants.colors import CLASS_COLOR_MAP, PATH_COLOR_MAP

ABRV_LABEL_MAP = {
    "Entrance": "ENTRANCE",
    "Exit": "EXIT",
    "Car Service Area": "CSA",
    "Bike Service Area": "BSA",
    "HGV Service Area": "HGVS",
    "Underground Service Facility": "USF",
    "Underground Access Point": "UAP",
    "Car Parking": "CP",
    "Bike Parking": "BP",
    "Priority Parking": "PP",
    "HGV Parking": "HGVP",
    "Shop": "SHOP",
    "EV Service Area": "EVSA",
    "OBSTACLE": "OBST",
}


def get_plot_label(class_name: str) -> str:
    """Returns the visualization label for a class name."""
    return ABRV_LABEL_MAP.get(class_name, class_name)


def plot_layout(
    json_description: dict,
    graph=None,
    shortest_paths: list | None = None,
    show: bool = True,
    save_path: str | None = None,
    return_base64: bool = False,
    return_figure: bool = False,
) -> str | tuple | None:
    """Plots the layout described by the JSON.

    Args:
        json_description (dict): Dictionary with 'elements' key containing
            list of elements with 'class' and 'polygon'.
        graph (networkx.Graph, optional): Graph to plot on top of the layout.
        shortest_paths (list, optional): List of path dicts with structure
            [{"waypoints": {...}, "path": [...], "length": 123}, ...].
            If provided as simple list of node lists, uses old behavior.
        save_path (str, optional): Path to save the plot.
            If None, shows the plot.
        return_base64 (bool, optional): If True, returns the plot as a
            base64-encoded PNG string.

    Returns:
        str | None: Base64-encoded PNG string if return_base64 is True,
            else None.
        tuple | None: (fig, ax) if return_figure is True, else None.
    """
    elements = json_description["elements"]

    gdf = gpd.GeoDataFrame(
        geometry=[Polygon(item["polygon"]) for item in elements]
    )
    gdf["element_id"] = [item["id"] for item in elements]
    gdf["class_id"] = [item["class"] for item in elements]
    gdf["color"] = [
        CLASS_COLOR_MAP[class_name] for class_name in gdf["class_id"]
    ]

    if show:
        fig, ax = plt.subplots(figsize=(12, 8))
    else:
        fig = Figure(figsize=(12, 8))
        FigureCanvasAgg(fig)
        ax = fig.add_subplot(111)

    # First plot layouts with only edges
    layout_mask = (gdf["class_id"] == INPUT_CLASSES[0]) | (
        gdf["class_id"] == "OBSTACLE LAYOUT"
    )
    if layout_mask.any():
        gdf[layout_mask].plot(
            ax=ax,
            edgecolor=gdf[layout_mask]["color"],
            facecolor="none",
            alpha=1,
            linewidth=2,
        )

    # Then plot non-layouts with alpha=0.5
    non_layout_mask = ~layout_mask
    if non_layout_mask.any():
        for _, row in gdf[non_layout_mask].iterrows():
            gpd.GeoSeries([row.geometry]).plot(
                ax=ax,
                edgecolor="black",
                facecolor=row["color"],
                alpha=0.5,
                linewidth=1,
            )

    # Add labels for each geometry, excluding the layout polygon.
    for _, row in gdf.iterrows():
        if (
            row["class_id"] != INPUT_CLASSES[0]
            and row["class_id"] != "OBSTACLE LAYOUT"
        ):
            centroid = row.geometry.centroid

            area = row.geometry.area
            # Scale font size based on sqrt of area, with min/max limits
            font_size = min(max(np.sqrt(area) / 2, 6), 12)

            # Calculate rotation angle based on longest side
            coords = list(row.geometry.exterior.coords)
            max_length = 0
            angle = 0
            for i in range(len(coords) - 1):
                dx = coords[i + 1][0] - coords[i][0]
                dy = coords[i + 1][1] - coords[i][1]
                length = np.sqrt(dx * dx + dy * dy)
                if length > max_length:
                    max_length = length
                    angle = np.degrees(np.arctan2(dy, dx))

            # Adjust angle to keep text readable
            if angle > 90:
                angle -= 180
            elif angle < -90:
                angle += 180

            ax.annotate(
                f"{row['element_id']} ({get_plot_label(row['class_id'])})",
                (centroid.x, centroid.y),
                fontsize=font_size,
                ha="center",
                va="center",
                rotation=angle,
            )

    if graph:
        for edge in graph.edges():
            node1, node2 = edge
            edge_data = graph[node1][node2]

            # Check if edge has geometry (junction graph)
            geometry = edge_data.get("geometry")

            is_waypoint_edge = False
            node1_data = graph.nodes[node1]
            node2_data = graph.nodes[node2]

            if (
                node1_data.get("type") is not None
                or node2_data.get("type") is not None
            ):
                is_waypoint_edge = True
            elif isinstance(node1, str) or isinstance(node2, str):
                is_waypoint_edge = True

            if geometry:
                x, y = geometry.xy
                color = "g" if is_waypoint_edge else "b"
                linewidth = 1.5 if is_waypoint_edge else 0.8
                alpha = 0.7 if is_waypoint_edge else 0.5
                ax.plot(x, y, color=color, linewidth=linewidth, alpha=alpha)
            else:
                pos1 = graph.nodes[node1]["pos"]
                pos2 = graph.nodes[node2]["pos"]

                if is_waypoint_edge:
                    ax.plot(
                        [pos1[0], pos2[0]],
                        [pos1[1], pos2[1]],
                        "g-",
                        linewidth=1.5,
                        alpha=0.7,
                    )
                else:
                    ax.plot(
                        [pos1[0], pos2[0]],
                        [pos1[1], pos2[1]],
                        "b-",
                        linewidth=0.8,
                        alpha=0.5,
                    )

        waypoint_nodes = [
            n for n, d in graph.nodes(data=True) if d.get("type") is not None
        ]
        for node in waypoint_nodes:
            pos = graph.nodes[node]["pos"]
            ax.plot(pos[0], pos[1], "o", color="#1B5E20", markersize=10)

    if shortest_paths and graph:
        from shapely.geometry import LineString
        from shapely.ops import linemerge

        for idx, path_data in enumerate(shortest_paths):
            path = path_data.get("path", [])
            if not path:
                continue

            # Reconstruct geometric path from junction graph edges
            path_geometries = []
            for i in range(len(path) - 1):
                node1 = path[i]
                node2 = path[i + 1]

                if graph.has_edge(node1, node2):
                    edge_data = graph[node1][node2]
                    geometry = edge_data.get("geometry")

                    if geometry:
                        path_geometries.append(geometry)
                    else:
                        pos1 = graph.nodes[node1]["pos"]
                        pos2 = graph.nodes[node2]["pos"]
                        path_geometries.append(LineString([pos1, pos2]))

            if path_geometries:
                if len(path_geometries) == 1:
                    full_path_geom = path_geometries[0]
                else:
                    merged = linemerge(path_geometries)
                    if merged.geom_type == "LineString":
                        full_path_geom = merged
                    else:
                        full_path_geom = None
                        color = PATH_COLOR_MAP[idx % len(PATH_COLOR_MAP)]
                        length = path_data.get("length", 0)
                        label: str | None = (
                            f"Path {idx + 1} (len={length:.2f})"
                        )

                        for geom in path_geometries:
                            x, y = geom.xy
                            label = (
                                label if geom == path_geometries[0] else None
                            )
                            ax.plot(
                                x,
                                y,
                                color=color,
                                linewidth=2.5,
                                alpha=0.8,
                                label=label,
                            )

                if full_path_geom:
                    x, y = full_path_geom.xy
                    color = PATH_COLOR_MAP[idx % len(PATH_COLOR_MAP)]
                    length = path_data.get("length", 0)
                    label = f"Path {idx + 1} (len={length:.2f})"
                    ax.plot(
                        x,
                        y,
                        color=color,
                        linewidth=2.5,
                        alpha=0.8,
                        label=label,
                    )

    if save_path:
        fig.savefig(save_path, format="png", bbox_inches="tight")

    if show:
        plt.show()

    if return_base64:
        buf = io.BytesIO()
        fig.savefig(buf, format="png", bbox_inches="tight")
        buf.seek(0)
        image_base64 = base64.b64encode(buf.read()).decode("utf-8")
        plt.close(fig)
        return image_base64

    if return_figure:
        return fig, ax

    # Caller isn't keeping a reference (return_figure=False): close now
    # instead of leaving a pyplot-managed figure (show=True) open.
    plt.close(fig)
    return None
