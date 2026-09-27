# Working on littledocter

Read this before changing the project.

## What this is

A local pipeline that reads a folder of medical reports and renders two static
HTML pages. No server, no network calls, no database. `pyyaml` is the only hard
dependency; everything else degrades with a warning.

```
reports/*  ──ingest──▶ data/measurements.json ──analyse──▶ data/analysis.json ──render──▶ site/*.html
```

## The five-minute loop

```bash
cd littledocter
python3 littledocter.py check                  # knowledge base + parser fixtures — must print "ok"
python3 littledocter.py demo                   # sample record → data/demo/ + site/
open site/index.html                     # or: python3 -m http.server -d site
```

Working on someone's real record:

```bash
python3 littledocter.py init                   # reports/ + data/profile.yaml from the template
python3 littledocter.py run                    # ingest → analyse → render
python3 littledocter.py run --today 2024-01-01 # replay the record as of a past date
```

Every command accepts `--reports/--data/--site/--profile` **before or after** the
subcommand, so nothing has to be built inside the repo:

```bash
python3 littledocter.py run --reports ~/scans --profile ~/me.yaml --data /tmp/me --site /tmp/me/site
```

## Before you commit

```bash
python3 littledocter.py check                              # KB cross-references + parser fixtures
python3 -m unittest discover -s tests -v              # pipeline regressions
python3 -m pyflakes src/*.py littledocter.py tools/*.py    # no unused names, no typos
python3 littledocter.py demo                               # the whole pipeline, end to end
python3 tools/verify_figure.py                       # every organ mark inside the silhouette
```

If you touched `web/*.html`, also check the scripts parse and click through the
five sheets:

```bash
python3 - <<'PY'
import re, pathlib, subprocess
for f in ["web/body.html", "web/archive.html"]:
    js = "\n;\n".join(re.findall(r"<script[^>]*>(.*?)</script>",
                                 pathlib.Path(f).read_text(), re.S))
    pathlib.Path("/tmp/chk.js").write_text(js)
    r = subprocess.run(["node", "--check", "/tmp/chk.js"], capture_output=True, text=True)
    print(f, "OK" if r.returncode == 0 else r.stderr)
PY
python3 tools/shoot.py --lang zh --hover liver --pin --out /tmp/z.png   # needs Playwright
```

The templates are rendered by string substitution in `src/render.py`; there is no
build step and no bundler. Edit `web/body.html`, re-run `render`, reload.

## Where things live

| Question | File |
| --- | --- |
| A lab line is misread | `src/parse.py` + a fixture in `knowledge/parser_fixtures.yaml` |
| A marker, its ranges, units, aliases | `knowledge/markers.yaml` |
| Which markers belong to a system | `knowledge/systems.yaml` (`core` / `extended`) |
| A pattern finding ("iron-deficient, not anaemic yet") | `knowledge/rules.yaml` |
| When a test is due by age/sex | `knowledge/screening.yaml` |
| A diagnosis name → its system | `knowledge/conditions.yaml` |
| Status, certainty, findings, actions | `src/analyze.py` |
| The bilingual trend sentences | `src/trend.py` |
| What the page is allowed to see | `src/render.py` (`*_KEYS` whitelists) |
| The page itself | `web/body.html`, `web/archive.html` |

`python3 littledocter.py check` refuses ambiguous aliases, unknown rule targets, unknown
systems, and warns about a marker that is on no panel — add new entries and run it.

## Rules this codebase holds itself to

1. **Never invent a number.** Everything on the page traces to a record in
   `reports/`. Derived values are declared in `knowledge/derived.yaml` and labelled
   as derived.
2. **Missing is a state.** "Never measured" and "gone stale" are distinct and both
   visible. A system with a perfect panel from 2019 is not settled.
3. **Alarm is spent, not sprayed.** Wording is action-oriented; `urgent` severity
   is reserved for what is genuinely urgent. See "How it chooses its words" in the
   README before rewording anything.
4. **Known conditions calm the colour, never the content.** `display_state` may
   read `managed`; the underlying `status` is untouched and still drives findings,
   trends and the doctor's sheet. `urgent` findings are never turned into
   `managed`.
5. **Nothing moves without a click.** Hover may preview (`peek`); only a click
   pins. Keep preview and pinned states separate.
6. **Both languages, always.** Any new user-visible string needs `_zh`. In the
   templates use `t("English", "中文")`, never a bare literal.
7. **Say what failed.** Prefer a warning in `Analysis.warnings` (it surfaces in
   *About this page*) over a silent `except: pass`.
8. **The reader owns their data.** No uploads. `reports/`, `data/` and `site/` are
   gitignored; keep them that way. Source files are only copied into `site/` with
   an explicit `--with-originals`.

## Things that are easy to get wrong

- **Argparse.** Shared options use `argparse.SUPPRESS` plus `OPTION_DEFAULTS` in
  `src/cli.py` so a subparser cannot overwrite an option given before the command.
  If you add a shared flag, add it there too, and test both orders.
- **`data/` vs `data/demo/`.** `demo` writes to `data/demo/` when `data/` already
  holds someone's real analysis. Do not "simplify" that away.
- **Payload whitelists.** `src/render.py` copies only listed keys into the page. A
  new field in `analyze.py` or `trend.py` will not appear on the page until it is
  added to the matching `*_KEYS` tuple.
- **Text-size zoom.** It is applied to the report, sheets, legend and footer — not
  to `html` — because zooming the root pushes the one-screen layout off screen.
- **Sheet geometry.** `--headh` / `--footh` are measured in JS so the drawer sits
  between the header and the tabs, and both stay clickable. If you move the header
  or footer, keep `measureChrome()` honest.
- **Print.** `body.sheeting` means a drawer is open, and print then emits only that
  drawer — that is how "For your doctor" prints as a single clean page.
