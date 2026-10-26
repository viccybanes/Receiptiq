"""Shared OCR row geometry; coordinates are evidence, not proof of correct text."""

import math
import os
from statistics import median


def layout_assistance_enabled():
    return os.environ.get("RECEIPTIQ_LAYOUT_ASSISTANCE", "1") != "0"


def valid_box(box):
    return (isinstance(box, (list, tuple)) and len(box) == 4 and
            all(type(v) in (int, float) and math.isfinite(v) for v in box) and
            box[0] < box[2] and box[1] < box[3])


def _polygon_geometry(line):
    points = line.get("polygon")
    if not isinstance(points, (list, tuple)) or len(points) != 4:
        return None
    if not all(isinstance(p, (list, tuple)) and len(p) == 2 and
               all(type(v) in (int, float) and math.isfinite(v) for v in p) for p in points):
        return None
    # Paddle returns top-left, top-right, bottom-right, bottom-left.
    left = [(points[0][i] + points[3][i]) / 2 for i in (0, 1)]
    right = [(points[1][i] + points[2][i]) / 2 for i in (0, 1)]
    width = right[0] - left[0]
    height = (points[3][1] - points[0][1] + points[2][1] - points[1][1]) / 2
    if height <= 0 or width < max(8, height * 2):
        return None
    slope = (right[1] - left[1]) / width
    return (slope, height) if abs(slope) <= math.tan(math.radians(8)) else None


def group_rows(lines):
    """Group disjoint column fragments using text height and residual baseline tilt.

    Old saved OCR runs without polygons still use height-aware box centers.
    Each fragment belongs to one row; overlapping same-column lines stay separate.
    """
    valid = [line for line in lines if isinstance(line, dict) and
             isinstance(line.get("text"), str) and valid_box(line.get("box")) and
             type(line.get("page", 0)) is int and line.get("page", 0) >= 0]
    rows = []
    if not layout_assistance_enabled():
        for line in sorted(valid, key=lambda v: (v.get("page", 0), v["box"][1], v["box"][0])):
            if rows and rows[-1]["page"] == line.get("page", 0) and abs(rows[-1]["y"] - line["box"][1]) < 12:
                rows[-1]["lines"].append(line)
            else:
                rows.append({"page": line.get("page", 0), "y": line["box"][1], "lines": [line]})
    else:
        for page in sorted({line.get("page", 0) for line in valid}):
            fragments = [line for line in valid if line.get("page", 0) == page]
            slopes = [geometry[0] for line in fragments if (geometry := _polygon_geometry(line))]
            slope = median(slopes) if len(slopes) >= 2 else 0
            projected = []
            for line in fragments:
                left, top, right, bottom = line["box"]
                geometry = _polygon_geometry(line)
                height = geometry[1] if geometry else bottom - top
                center = (top + bottom - slope * (left + right)) / 2
                projected.append((center, height, line))
            page_rows = []
            for center, height, line in sorted(projected, key=lambda v: (v[0], v[2]["box"][0])):
                matches = []
                # ponytail: quadratic in OCR fragments; receipt-sized pages need no spatial index.
                for row in page_rows:
                    if abs(row["y"] - center) > min(row["height"], height) * 0.55:
                        continue
                    left, _, right, _ = line["box"]
                    if any(min(right, other["box"][2]) - max(left, other["box"][0]) >
                           min(right - left, other["box"][2] - other["box"][0]) * 0.2
                           for other in row["lines"]):
                        continue
                    matches.append(row)
                if matches:
                    row = min(matches, key=lambda r: abs(r["y"] - center))
                    row["lines"].append(line)
                    row["height"] = min(row["height"], height)
                else:
                    page_rows.append({"page": page, "y": center, "height": height, "lines": [line]})
            rows.extend(sorted(page_rows, key=lambda r: r["y"]))
    for index, row in enumerate(rows):
        fragments = sorted(row["lines"], key=lambda v: v["box"][0])
        row.update(lines=fragments, row_id=index, text=" ".join(line["text"] for line in fragments),
                   ids=[line["id"] for line in fragments if "id" in line],
                   box=[min(line["box"][0] for line in fragments), min(line["box"][1] for line in fragments),
                        max(line["box"][2] for line in fragments), max(line["box"][3] for line in fragments)])
    return rows


def detail_bands(lines, dimensions):
    """Bound a dense, uncertain single-page table to at most four reading sections.

    OCR locates likely rows, but its words/numbers never become replacement values.
    Sections cover the whole page, including items OCR missed above/below the table.
    """
    if len(dimensions) != 1:
        return []
    width, height = dimensions[0]
    runs, run = [], []
    for row in group_rows(lines):
        fragments = row["lines"]
        plausible = (row["page"] == 0 and len(fragments) >= 4 and
                     row["box"][2] - row["box"][0] >= width * .55 and
                     sum(bool(any(c.isdigit() for c in line["text"])) for line in fragments) >= 2)
        if run and (not plausible or row["y"] - run[-1]["y"] > max(row["height"], run[-1]["height"]) * 2):
            runs.append(run)
            run = []
        if plausible:
            run.append(row)
    if run:
        runs.append(run)
    dense = [rows for rows in runs if len(rows) >= 8]
    if len(dense) != 1 or len(dense[0]) > 20:
        return []
    rows = dense[0]
    scores = [line["score"] for row in rows for line in row["lines"]
              if type(line.get("score")) in (int, float) and math.isfinite(line["score"]) and
              0 <= line["score"] <= 1]
    if not scores or median(scores) >= .85:
        return []
    cuts = [0.0] + [(rows[i - 1]["y"] + rows[i]["y"]) / 2 / height for i in range(5, len(rows), 5)] + [1.0]
    if any(not 0 <= top < bottom <= 1 or bottom - top < .04 for top, bottom in zip(cuts, cuts[1:])):
        return []
    return [[0.0, top, 1.0, bottom] for top, bottom in zip(cuts, cuts[1:])]
