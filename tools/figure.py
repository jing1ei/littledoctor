"""Print the silhouette path for the anatomical figure in web/body.html.

The body is authored once as a right-hand profile and then mirrored, so it
cannot drift out of symmetry by hand-editing. Change a landmark below, run
this, and paste the one line it prints into the `.sil` path.

    python3 tools/figure.py
"""
from __future__ import annotations

# ── the right half of the body, top of the head → crotch ────────────────────
# every entry is one cubic segment: (control 1, control 2, end point)
RIGHT = [
    ((216, 28), (229, 40), (229, 59)),
    ((229, 78), (220, 94), (211, 100)),
    ((210, 108), (211, 116), (218, 120)),
    ((231, 126), (258, 129), (266, 144)),
    ((275, 161), (279, 188), (284, 212)),
    ((290, 238), (300, 266), (304, 292)),
    ((307, 309), (313, 326), (309, 336)),
    ((305, 343), (299, 338), (297, 330)),
    ((295, 320), (291, 312), (290, 300)),
    ((282, 278), (274, 253), (268, 232)),
    ((261, 211), (257, 185), (250, 170)),
    ((250, 200), (245, 229), (240, 252)),
    ((238, 267), (244, 286), (249, 305)),
    ((255, 327), (252, 352), (247, 377)),
    ((243, 404), (237, 431), (237, 452)),
    ((241, 473), (237, 502), (232, 523)),
    ((229, 541), (229, 557), (231, 568)),
    ((235, 575), (245, 580), (242, 587)),
    ((236, 591), (214, 591), (211, 586)),
    ((207, 578), (211, 566), (212, 552)),
    ((211, 526), (207, 504), (210, 479)),
    ((213, 461), (207, 443), (206, 425)),
    ((205, 391), (202, 364), (200, 346)),
]

TOP = (200, 28)


def mirror(x: float) -> float:
    return 400 - x


def path_d(segments=RIGHT) -> str:
    """Down the right side, then the same segments mirrored and walked back up."""
    out = [f"M{TOP[0]},{TOP[1]}"]
    starts = [TOP] + [seg[2] for seg in segments[:-1]]     # each segment's origin
    for c1, c2, end in segments:
        out.append("C{},{} {},{} {},{}".format(*c1, *c2, *end))
    for (c1, c2, _), start in zip(reversed(segments), reversed(starts)):
        # traversed backwards, so the control points swap and x is mirrored
        out.append("C{},{} {},{} {},{}".format(mirror(c2[0]), c2[1],
                                               mirror(c1[0]), c1[1],
                                               mirror(start[0]), start[1]))
    out.append("Z")
    return " ".join(out)


if __name__ == "__main__":
    print(path_d())
