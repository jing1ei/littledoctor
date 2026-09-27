"""Browser regression checks. Run after `littledocter.py demo`; requires Playwright."""
from pathlib import Path
import argparse
from playwright.sync_api import sync_playwright, expect

def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--browser", choices=("chromium", "webkit"), default="chromium")
    args = parser.parse_args()
    site = Path(__file__).resolve().parent.parent / 'site'
    with sync_playwright() as pw:
        b = getattr(pw, args.browser).launch()
        errors = []
        requests = []
        for width, height in [(1440, 900), (1081, 700), (1080, 700), (768, 1024), (390, 844), (320, 568), (844, 390)]:
            print('viewport', width, height, flush=True)
            p = b.new_page(viewport={'width': width, 'height': height}, reduced_motion='reduce')
            p.on('pageerror', lambda e: errors.append(str(e)))
            p.on('request', lambda r: requests.append(r.url) if r.url.startswith('http') else None)
            p.goto((site / 'index.html').as_uri())
            for lang in ['en', 'zh']:
                p.click(f'#lang button[data-lang="{lang}"]')
                p.click('#txt button[data-text="xl"]')
                assert p.evaluate('document.documentElement.scrollWidth<=innerWidth'), ('body overflow', width, lang)
                nav = p.locator('footer.foot nav').bounding_box()
                assert nav['y'] >= 0 and nav['y'] + nav['height'] <= height + 1, nav
                for button in p.locator('footer.foot nav .tbtn').all():
                    expect(button).to_be_in_viewport(ratio=1)
                    assert button.evaluate('(e)=>{const r=e.getBoundingClientRect();return e.contains(document.elementFromPoint(r.x+r.width/2,r.y+r.height/2))}')
                for key in ['findings', 'tests', 'trends', 'visit', 'about']:
                    if key == 'findings':
                        p.click('[data-sheet="findings"]')
                    else:
                        p.click('[data-sheet="more"]')
                        p.click(f'[data-go-sheet="{key}"]')
                    expect(p.locator('#sheet')).to_be_visible()
                    box = p.locator('#sheet').bounding_box()
                    nav = p.locator('footer.foot nav').bounding_box()
                    assert box['y'] + box['height'] <= nav['y'] + 1, (width, box, nav)
                    assert p.locator('.stage').evaluate('(e)=>e.inert')
                    assert p.locator('#sheet-body').evaluate('(e)=>e.scrollWidth<=e.clientWidth+1'), ('sheet overflow', width, key, lang)
                    if key == 'visit':
                        expect(p.locator('#sheet-print')).to_be_visible()
                        p.emulate_media(media='print')
                        expect(p.locator('#sheet-back')).to_be_hidden()
                        p.emulate_media(media='screen')
                    if key != 'findings':
                        p.click('#sheet-back')
                        expect(p.locator('[data-go-sheet="visit"]')).to_be_visible()
                        p.keyboard.press('Escape')
                p.keyboard.press('Escape')
                expect(p.locator('#sheet')).to_be_hidden()
                assert not p.locator('.stage').evaluate('(e)=>e.inert')
            p.click('#home-btn')
            expect(p.locator('footer.foot nav .tbtn')).to_have_count(4)
            expect(p.locator('#report details')).to_have_count(2)
            p.locator('[data-layer="overview-systems"] summary').click()
            expect(p.locator('.irow')).to_have_count(15)
            p.locator('[data-layer="overview-systems"] summary').click()
            p.locator('.hit[data-sys="liver"]').click()
            expect(p.locator('#report details')).to_have_count(4)
            p.locator('[data-layer="liver-history"] summary').click()
            expect(p.locator('.story').first).to_be_visible()

            assert p.locator('.bar span').evaluate('(e)=>getComputedStyle(e).transform') != 'matrix(0, 0, 0, 1, 0, 0)'
            if width <= 1080:
                assert p.locator('.report').bounding_box()['y'] < height
            p.goto((site / 'archive.html').as_uri())
            expect(p.locator('footer.foot nav .tbtn')).to_have_count(4)
            p.evaluate('scrollTo(0,document.body.scrollHeight)')
            for button in p.locator('footer.foot nav .tbtn').all():
                expect(button).to_be_in_viewport(ratio=1)
            for lang in ['en', 'zh']:
                p.click(f'#lang button[data-lang="{lang}"]')
                p.click('#txt button[data-text="xl"]')
                assert p.evaluate('document.documentElement.scrollWidth<=innerWidth'), ('archive overflow', width, lang)
                p.fill('#q', 'ferritin')
                expect(p.locator('article.doc').first).to_be_visible()
                p.fill('#q', 'missing_not_here')
                expect(p.locator('article.doc')).to_have_count(0)
                p.click('#clear')
                expect(p.locator('article.doc')).to_have_count(8)
            if width <= 640:
                assert p.locator('.search').bounding_box()['height'] < height * 0.5
            p.locator('[data-full]').first.click()
            assert p.evaluate('document.activeElement.hasAttribute("data-full")')
            expect(p.locator('[data-full]').first).to_have_attribute('aria-expanded', 'true')
            p.locator('#filter-panel').evaluate('(e)=>e.open=true')
            p.locator('.f[data-k="flag"][data-v="bad"]').click()
            assert p.evaluate('document.activeElement.dataset.v') == 'bad'
            p.close()
        assert not errors, errors
        assert not requests, requests
        b.close()
    print('PASS: layouts, languages, sheets, controls, focus, reduced motion, archive recovery, offline loading')
if __name__ == '__main__':
    main()
