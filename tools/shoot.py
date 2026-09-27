"""Screenshot the rendered page — including hover and pinned states.

A development aid: the body view only shows its detail panel on hover, so a
still image of the page tells you very little unless you can ask for a state.

    python3 tools/shoot.py                            # the resting page
    python3 tools/shoot.py --hover liver              # liver hovered
    python3 tools/shoot.py --hover bone --pin         # pinned open (full detail)
    python3 tools/shoot.py --sheet findings           # a sheet open
    python3 tools/shoot.py --lang zh --sheet trends    # in Chinese
    python3 tools/shoot.py --size 430x932 --out /tmp/phone.png

Requires playwright (`pip install playwright && playwright install chromium`).
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PAGE = ROOT / "site" / "index.html"


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--page", default=str(PAGE))
    ap.add_argument("--hover", default=None, help="system id to hover, e.g. liver")
    ap.add_argument("--pin", action="store_true", help="click it open (full detail)")
    ap.add_argument("--sheet", choices=("findings", "tests", "trends", "visit", "about", "more"))
    ap.add_argument("--lang", default=None, help="en | zh — press the language switch first")
    ap.add_argument("--fill", default=None,
                    help="type into a field first, e.g. '#q=甲状腺' (archive search)")
    ap.add_argument("--click", default=None, help="extra selector to click, e.g. '.f[data-v=bad]'")
    ap.add_argument("--size", default="1440x900")
    ap.add_argument("--out", default="/tmp/littledocter.png")
    ap.add_argument("--full", action="store_true", help="full-page instead of viewport")
    ap.add_argument("--el", default=None, help="screenshot one element, e.g. #fig or .panel")
    args = ap.parse_args()

    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        sys.exit("playwright not installed — pip install playwright && playwright install chromium")

    w, _, h = args.size.partition("x")
    with sync_playwright() as p:
        browser = p.chromium.launch()
        page = browser.new_page(viewport={"width": int(w), "height": int(h)},
                                device_scale_factor=2)
        problems: list[str] = []
        page.on("console", lambda m: m.type == "error" and problems.append(m.text))
        page.on("pageerror", lambda e: problems.append(str(e)))
        page.goto(Path(args.page).resolve().as_uri())
        page.wait_for_timeout(1400)          # fonts, meters, fades
        if args.lang:
            page.click(f'#lang button[data-lang="{args.lang}"]')
            page.wait_for_timeout(700)
        if args.fill:
            sel, _, val = args.fill.partition("=")
            page.fill(sel, val)
            page.wait_for_timeout(500)
        if args.click:
            page.click(args.click)
            page.wait_for_timeout(500)
        if args.hover:
            sel = f'.hit[data-sys="{args.hover}"]'
            if not page.query_selector(sel):
                sys.exit(f"no such system on the figure: {args.hover}")
            page.hover(sel)
            page.wait_for_timeout(700)
            if args.pin:
                page.click(sel)
                page.wait_for_timeout(700)
        if args.sheet:
            if args.sheet in ("findings", "more"):
                page.click(f'[data-sheet="{args.sheet}"]')
            else:
                page.click('[data-sheet="more"]')
                page.click(f'[data-go-sheet="{args.sheet}"]')
            page.wait_for_timeout(900)
        if args.el:
            page.locator(args.el).screenshot(path=args.out)
        else:
            page.screenshot(path=args.out, full_page=args.full)
        browser.close()
    for p_ in problems:
        print("console:", p_)
    print(args.out)


if __name__ == "__main__":
    main()
