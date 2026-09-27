"""Check that every organ mark stays inside the body outline.

The figure is hand-authored SVG, so the only reliable containment test is the
browser's own geometry engine. This samples points along each organ path and
asks the silhouette whether it contains them.

    python3 tools/verify_figure.py
"""
from __future__ import annotations

import sys
from pathlib import Path

from playwright.sync_api import sync_playwright

SITE = Path(__file__).resolve().parent.parent / "site" / "index.html"

JS = r"""
() => {
  const fig = document.querySelector('#fig');
  const sil = fig.querySelector('.sil');
  const out = [];
  const OFF_BODY = {cancer_screening: 1};   // a dial, not a place
  fig.querySelectorAll('.sysg').forEach(g => {
    const sys = g.dataset.sys;
    if (OFF_BODY[sys]) return;
    let worst = null, n = 0;
    g.querySelectorAll('.organ, .node').forEach(el => {
      let len, sample = [];
      if (el.tagName === 'path') {
        len = el.getTotalLength();
        for (let i = 0; i <= 24; i++) sample.push(el.getPointAtLength(len * i / 24));
      } else {
        const cx = +el.getAttribute('cx'), cy = +el.getAttribute('cy'),
              rx = +(el.getAttribute('r') || el.getAttribute('rx')),
              ry = +(el.getAttribute('r') || el.getAttribute('ry'));
        for (let i = 0; i < 16; i++) {
          const a = i / 16 * Math.PI * 2;
          sample.push({x: cx + Math.cos(a) * rx, y: cy + Math.sin(a) * ry});
        }
      }
      sample.forEach(p => {
        const q = new DOMPoint(p.x, p.y);
        if (!sil.isPointInFill(q)) {
          n++;
          // how far outside, measured against the mirror axis at x = 200
          const d = Math.abs(p.x - 200);
          if (!worst || d > worst.d) worst = {x: Math.round(p.x), y: Math.round(p.y), d};
        }
      });
    });
    if (n) out.push({sys, outside: n, worst});
  });
  return out;
}
"""

def main() -> int:
    with sync_playwright() as pw:
        b = pw.chromium.launch()
        page = b.new_page(viewport={"width": 1440, "height": 900})
        page.goto(SITE.as_uri())
        page.wait_for_timeout(400)
        bad = page.evaluate(JS)
        b.close()
    if not bad:
        print("every organ mark sits inside the body outline")
        return 0
    for r in bad:
        w = r["worst"]
        print(f"{r['sys']:<18} {r['outside']:>3} sampled points outside "
              f"(worst near x={w['x']}, y={w['y']})")
    return 1


if __name__ == "__main__":
    sys.exit(main())
