#!/usr/bin/env python3
# Copyright © 2026 Shell Information Technology International B.V.
# All Rights Reserved.

"""Visualize layouts from the ALPS-Bench dataset in ``data/``.

Select layouts by id, by row index, at random, or all of a split, then
show them in a window and/or save them as PNG files.

Samples of the ``test`` split only carry the task input, so only the
premise and the entrance/exit are drawn; the elements to place are not.

Examples:
    # Show the first two layouts of the validation split.
    python scripts/visualize_layouts.py --index 0 1

    # Save layouts by id (a unique id prefix is enough) to ./visualizations.
    python scripts/visualize_layouts.py --id 2a9d53a4 6e585b77 --save

    # Save rows 0-9 and 20 of the train split into a custom folder.
    python scripts/visualize_layouts.py --split train --index 0-9 20 \\
        --save out/

    # Save 5 random augmented training layouts and show them.
    python scripts/visualize_layouts.py --augmented --random 5 --save --show

    # Save every layout of the test split (inputs only).
    python scripts/visualize_layouts.py --split test --all --save
"""

from __future__ import annotations

import argparse
import json
import math
import random
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import matplotlib.colors as mcolors
import matplotlib.patheffects as patheffects
from matplotlib.backends.backend_agg import FigureCanvasAgg
from matplotlib.figure import Figure
from matplotlib.lines import Line2D
from matplotlib.patches import Patch
from shapely.geometry import Polygon

DEFAULT_DATA_DIR = Path(__file__).resolve().parents[1] / "data"
DEFAULT_SAVE_DIR = Path("visualizations")
SPLITS = ("train", "val", "test")
DEFAULT_SPLIT = "val"
DEFAULT_DPI = 300
MAX_SHOWN = 10

LAYOUT_CLASS = "Layout Polygon"
FIGURE_WIDTH_IN = 12.0
MIN_FIGURE_HEIGHT_IN = 4.0
MAX_FIGURE_HEIGHT_IN = 18.0
PLOT_MARGIN_FRACTION = 0.04

BOUNDARY_LINEWIDTH = 1.4
ELEMENT_LINEWIDTH = 0.8
EDGE_DARKEN_FACTOR = 0.65

BOUNDARY_ZORDER = 1
UNDERGROUND_ZORDER = 1.5
UNDERGROUND_LABEL_ZORDER = 1.75
ELEMENT_ZORDER = 2
LABEL_ZORDER = 3

# Underground classes are drawn below surface elements, hatched and dashed.
UNDERGROUND_CLASSES = frozenset({"Underground Service Facility"})
UNDERGROUND_FILL_ALPHA = 0.5
UNDERGROUND_HATCH = "////"
UNDERGROUND_LINESTYLE = (0, (4, 2))

MIN_LABEL_FONT_SIZE = 6.0
MAX_LABEL_FONT_SIZE = 11.0
LABEL_FONT_SCALE = 0.55
LABEL_TEXT_COLOR = "#1A1A1A"
LABEL_HALO_COLOR = "#FFFFFF"
LABEL_HALO_WIDTH = 2.2
LEGEND_FONT_SIZE = 9.0

LAYOUT_BOUNDARY_COLOR = "#2B2B2B"
DEFAULT_FILL_COLOR = "#B0B0B0"

CLASS_COLORS = {
    "Entrance": "#5B8C5A",
    "Exit": "#B5654A",
    "Car Service Area": "#6C93B8",
    "Bike Service Area": "#93C0D9",
    "HGV Service Area": "#3F6C8A",
    "EV Service Area": "#7FAE8C",
    "Car Parking": "#D9AE58",
    "Bike Parking": "#E8CD8A",
    "Priority Parking": "#C97B4A",
    "HGV Parking": "#9C7A4E",
    "Shop": "#B98868",
    "Underground Service Facility": "#8C7FA0",
    "Underground Access Point": "#5E5570",
}

ABBREVIATIONS = {
    "Entrance": "ENTRANCE",
    "Exit": "EXIT",
    "Car Service Area": "CSA",
    "Bike Service Area": "BSA",
    "HGV Service Area": "HGVS",
    "EV Service Area": "EVSA",
    "Car Parking": "CP",
    "Bike Parking": "BP",
    "Priority Parking": "PP",
    "HGV Parking": "HGVP",
    "Shop": "SHOP",
    "Underground Service Facility": "USF",
    "Underground Access Point": "UAP",
}

# Legend order follows functional grouping, not alphabetical order.
CLASS_ORDER = tuple(ABBREVIATIONS)


@dataclass
class Selected:
    """A layout picked for visualization.

    :param split: Split the layout was read from.
    :param index: 0-based row index within the split file.
    :param layout: The parsed layout record.
    """

    split: str
    index: int
    layout: dict[str, Any]


# ---------------------------------------------------------------------------
# Loading and selection
# ---------------------------------------------------------------------------


def split_file(data_dir: Path, split: str, augmented: bool) -> Path:
    """Return the dataset file holding the layouts of ``split``."""
    if split == "test":
        return data_dir / "test" / "test_constraints.jsonl"
    suffix = "_augmented" if augmented else ""
    return data_dir / split / f"{split}{suffix}.jsonl"


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    """Read all non-empty lines of a JSONL file."""
    with path.open(encoding="utf-8") as file:
        return [json.loads(line) for line in file if line.strip()]


def parse_indices(tokens: list[str]) -> list[int]:
    """Expand tokens such as ``3`` and ``2-5`` (inclusive) into indices."""
    indices: list[int] = []
    for token in tokens:
        start, sep, end = token.partition("-")
        try:
            first = int(start)
            last = int(end) if sep else first
        except ValueError:
            raise SystemExit(
                f"Invalid index {token!r}; use N or A-B."
            ) from None
        if last < first:
            raise SystemExit(f"Invalid index range {token!r}.")
        indices.extend(range(first, last + 1))
    return indices


class Dataset:
    """Lazily loads split files from the dataset directory."""

    def __init__(self, data_dir: Path, augmented: bool) -> None:
        self.data_dir = data_dir
        self.augmented = augmented
        self._cache: dict[str, list[dict[str, Any]] | None] = {}

    def rows(self, split: str, *, required: bool) -> list[dict[str, Any]]:
        """Return the rows of ``split``.

        :param required: If ``True``, a missing file is an error;
            otherwise an empty list is returned.
        """
        if split not in self._cache:
            path = split_file(self.data_dir, split, self.augmented)
            self._cache[split] = read_jsonl(path) if path.is_file() else None
        rows = self._cache[split]
        if rows is None:
            if required:
                path = split_file(self.data_dir, split, self.augmented)
                raise SystemExit(f"Dataset file not found: {path}")
            return []
        return rows

    def find_id(self, wanted: str, splits: list[str]) -> Selected:
        """Resolve an exact layout id, or a unique id prefix."""
        exact: list[Selected] = []
        prefixed: list[Selected] = []
        for split in splits:
            rows = self.rows(split, required=len(splits) == 1)
            for index, layout in enumerate(rows):
                layout_id = str(layout["id"])
                if layout_id == wanted:
                    exact.append(Selected(split, index, layout))
                elif layout_id.startswith(wanted):
                    prefixed.append(Selected(split, index, layout))
        matches = exact or prefixed
        if not matches:
            hint = "" if self.augmented else " (try --augmented)"
            raise SystemExit(
                f"No layout with id {wanted!r} in {', '.join(splits)}{hint}."
            )
        if len(matches) > 1:
            raise SystemExit(
                f"Id prefix {wanted!r} is ambiguous; it matches "
                f"{len(matches)} layouts."
            )
        return matches[0]


def select_layouts(args: argparse.Namespace) -> list[Selected]:
    """Collect the layouts requested on the command line, without repeats."""
    dataset = Dataset(args.data_dir, args.augmented)
    index_split = args.split or ("train" if args.augmented else DEFAULT_SPLIT)
    selected: list[Selected] = []

    for wanted in args.id or []:
        id_splits = (
            [args.split]
            if args.split
            else ["train"]
            if args.augmented
            else list(SPLITS)
        )
        selected.append(dataset.find_id(wanted, id_splits))

    if args.index:
        rows = dataset.rows(index_split, required=True)
        for index in parse_indices(args.index):
            if not 0 <= index < len(rows):
                raise SystemExit(
                    f"Index {index} is out of range for {index_split} "
                    f"({len(rows)} layouts)."
                )
            selected.append(Selected(index_split, index, rows[index]))

    if args.random or args.all:
        rows = dataset.rows(index_split, required=True)
        if args.all:
            chosen = list(range(len(rows)))
        else:
            count = min(args.random, len(rows))
            chosen = sorted(
                random.Random(args.seed).sample(range(len(rows)), count)
            )
        selected.extend(Selected(index_split, i, rows[i]) for i in chosen)

    unique: dict[tuple[str, int], Selected] = {}
    for item in selected:
        unique.setdefault((item.split, item.index), item)
    return list(unique.values())


# ---------------------------------------------------------------------------
# Rendering
# ---------------------------------------------------------------------------


def _darken(hex_color: str, factor: float) -> str:
    """Scale a ``#RRGGBB`` color towards black."""
    red, green, blue = mcolors.to_rgb(hex_color)
    return mcolors.to_hex((red * factor, green * factor, blue * factor))


def _label_angle(coords: list[tuple[float, ...]]) -> float:
    """Return a text angle in [-90, 90] along the longest polygon edge."""
    max_length = 0.0
    angle = 0.0
    for (x1, y1), (x2, y2) in zip(coords, coords[1:], strict=False):
        length = math.hypot(x2 - x1, y2 - y1)
        if length > max_length:
            max_length = length
            angle = math.degrees(math.atan2(y2 - y1, x2 - x1))
    if angle > 90:
        angle -= 180
    elif angle < -90:
        angle += 180
    return angle


def _draw_element(ax: Any, polygon: Polygon, class_name: str) -> None:
    """Draw one element polygon with its class color."""
    color = CLASS_COLORS.get(class_name, DEFAULT_FILL_COLOR)
    underground = class_name in UNDERGROUND_CLASSES
    x, y = polygon.exterior.xy
    ax.fill(
        x,
        y,
        facecolor=color,
        edgecolor=_darken(color, EDGE_DARKEN_FACTOR),
        linewidth=ELEMENT_LINEWIDTH,
        alpha=UNDERGROUND_FILL_ALPHA if underground else 1.0,
        hatch=UNDERGROUND_HATCH if underground else None,
        linestyle=UNDERGROUND_LINESTYLE if underground else "solid",
        zorder=UNDERGROUND_ZORDER if underground else ELEMENT_ZORDER,
    )


def _add_label(ax: Any, polygon: Polygon, class_name: str) -> None:
    """Write the class abbreviation at the polygon centroid."""
    abbreviation = ABBREVIATIONS.get(class_name)
    if not abbreviation:
        return
    centroid = polygon.centroid
    font_size = min(
        max(math.sqrt(polygon.area) * LABEL_FONT_SCALE, MIN_LABEL_FONT_SIZE),
        MAX_LABEL_FONT_SIZE,
    )
    underground = class_name in UNDERGROUND_CLASSES
    ax.annotate(
        abbreviation,
        (centroid.x, centroid.y),
        fontsize=font_size,
        ha="center",
        va="center",
        rotation=_label_angle(list(polygon.exterior.coords)),
        color=LABEL_TEXT_COLOR,
        zorder=UNDERGROUND_LABEL_ZORDER if underground else LABEL_ZORDER,
        path_effects=[
            patheffects.withStroke(
                linewidth=LABEL_HALO_WIDTH, foreground=LABEL_HALO_COLOR
            )
        ],
    )


def _add_legend(ax: Any, present: set[str]) -> None:
    """Add a legend of the classes present in the layout."""
    handles: list[Any] = [
        Line2D(
            [0],
            [0],
            color=LAYOUT_BOUNDARY_COLOR,
            linewidth=BOUNDARY_LINEWIDTH,
        )
    ]
    labels = ["Site Boundary"]
    for class_name in [*CLASS_ORDER, *sorted(present - set(CLASS_ORDER))]:
        if class_name not in present:
            continue
        color = CLASS_COLORS.get(class_name, DEFAULT_FILL_COLOR)
        underground = class_name in UNDERGROUND_CLASSES
        abbreviation = ABBREVIATIONS.get(class_name)
        handles.append(
            Patch(
                facecolor=color,
                edgecolor=_darken(color, EDGE_DARKEN_FACTOR),
                alpha=UNDERGROUND_FILL_ALPHA if underground else 1.0,
                hatch=UNDERGROUND_HATCH if underground else None,
                linestyle=UNDERGROUND_LINESTYLE if underground else "solid",
            )
        )
        labels.append(
            f"{class_name} ({abbreviation})" if abbreviation else class_name
        )
    ax.legend(
        handles,
        labels,
        loc="upper left",
        bbox_to_anchor=(1.02, 1.0),
        frameon=False,
        fontsize=LEGEND_FONT_SIZE,
        borderaxespad=0.0,
        handlelength=1.3,
        handleheight=1.3,
    )


def build_figure(item: Selected, *, interactive: bool) -> Any:
    """Draw one layout and return its figure.

    :param item: The layout to draw.
    :param interactive: Create the figure through pyplot so that it can be
        shown in a window; otherwise a standalone Agg figure is used.
    :raises ValueError: If the layout has no polygon elements.
    """
    placed = [e for e in item.layout["elements"] if "polygon" in e]
    if not placed:
        raise ValueError(f"Layout {item.layout['id']} has no polygons.")
    polygons = [Polygon(e["polygon"]) for e in placed]

    xs = [x for p in polygons for x, _ in p.exterior.coords]
    ys = [y for p in polygons for _, y in p.exterior.coords]
    min_x, max_x, min_y, max_y = min(xs), max(xs), min(ys), max(ys)
    width = max(max_x - min_x, 1e-6)
    height = max(max_y - min_y, 1e-6)

    fig_height = min(
        max(FIGURE_WIDTH_IN * height / width, MIN_FIGURE_HEIGHT_IN),
        MAX_FIGURE_HEIGHT_IN,
    )
    size = (FIGURE_WIDTH_IN, fig_height)
    if interactive:
        import matplotlib.pyplot as plt

        fig = plt.figure(figsize=size)
    else:
        fig = Figure(figsize=size)
        FigureCanvasAgg(fig)
    ax = fig.add_subplot(111)

    present: set[str] = set()
    for element, polygon in zip(placed, polygons, strict=True):
        if element["class"] == LAYOUT_CLASS:
            x, y = polygon.exterior.xy
            ax.fill(
                x,
                y,
                facecolor="none",
                edgecolor=LAYOUT_BOUNDARY_COLOR,
                linewidth=BOUNDARY_LINEWIDTH,
                zorder=BOUNDARY_ZORDER,
            )
        else:
            present.add(element["class"])
            _draw_element(ax, polygon, element["class"])
            _add_label(ax, polygon, element["class"])

    margin = max(width, height) * PLOT_MARGIN_FRACTION
    ax.set_xlim(min_x - margin, max_x + margin)
    ax.set_ylim(min_y - margin, max_y + margin)
    ax.set_aspect("equal")
    ax.set_axis_off()
    _add_legend(ax, present)

    title = f"{item.layout['id']}  ({item.split}, row {item.index})"
    unplaced = len(item.layout["elements"]) - len(placed)
    if unplaced:
        title += f"\n{unplaced} elements to be placed are not shown"
    ax.set_title(title, fontsize=LEGEND_FONT_SIZE)
    return fig


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    """Parse command-line arguments."""
    parser = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    select = parser.add_argument_group("selection")
    select.add_argument(
        "--id",
        nargs="+",
        metavar="ID",
        help="Layout id(s); a unique id prefix is enough. Searches all "
        "splits unless --split is given.",
    )
    select.add_argument(
        "--index",
        nargs="+",
        metavar="N",
        help="0-based row index(es) within the split, e.g. '3 7 10-15'.",
    )
    select.add_argument(
        "--random",
        type=int,
        metavar="N",
        help="N randomly chosen layouts from the split (see --seed).",
    )
    select.add_argument(
        "--all", action="store_true", help="Every layout of the split."
    )
    select.add_argument(
        "--split",
        choices=SPLITS,
        help=f"Split for --index/--random/--all (default: {DEFAULT_SPLIT}; "
        "train with --augmented).",
    )
    select.add_argument(
        "--augmented",
        action="store_true",
        help="Read the augmented training layouts (train_augmented.jsonl).",
    )
    select.add_argument(
        "--seed", type=int, default=42, help="Seed for --random."
    )
    select.add_argument(
        "--data-dir",
        type=Path,
        default=DEFAULT_DATA_DIR,
        help=f"Dataset directory (default: {DEFAULT_DATA_DIR}).",
    )

    output = parser.add_argument_group("output")
    output.add_argument(
        "--show", action="store_true", help="Show the layouts in a window."
    )
    output.add_argument(
        "--save",
        nargs="?",
        const=DEFAULT_SAVE_DIR,
        type=Path,
        metavar="PATH",
        help="Save PNGs to this folder, created if needed (default: "
        f"./{DEFAULT_SAVE_DIR}/). A '.png' path is used as the file name "
        "when a single layout is selected.",
    )
    output.add_argument("--dpi", type=int, default=DEFAULT_DPI)

    args = parser.parse_args(argv)
    if not (args.id or args.index or args.random or args.all):
        parser.error("select layouts with --id, --index, --random or --all.")
    if args.random is not None and args.random < 1:
        parser.error("--random must be at least 1.")
    if args.random and args.all:
        parser.error("--random and --all are mutually exclusive.")
    if not args.show and args.save is None:
        args.show = True
    return args


def output_path(save: Path, item: Selected, *, single: bool) -> Path:
    """Return the PNG path for ``item`` under the ``--save`` argument."""
    if single and save.suffix.lower() == ".png":
        return save
    return save / f"layout_{item.layout['id']}.png"


def main(argv: list[str] | None = None) -> None:
    """CLI entry point."""
    args = parse_args(argv)
    items = select_layouts(args)

    if args.show and len(items) > MAX_SHOWN:
        sys.exit(
            f"Refusing to open {len(items)} windows (max {MAX_SHOWN}); "
            "use --save or select fewer layouts."
        )

    for item in items:
        fig = build_figure(item, interactive=args.show)
        if args.save is not None:
            path = output_path(args.save, item, single=len(items) == 1)
            path.parent.mkdir(parents=True, exist_ok=True)
            fig.savefig(
                path,
                dpi=args.dpi,
                format="png",
                bbox_inches="tight",
                pad_inches=0.15,
                facecolor="white",
            )
            print(f"Saved {path}")

    if args.show:
        import matplotlib.pyplot as plt

        plt.show()


if __name__ == "__main__":
    main()
