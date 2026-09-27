# Put your reports here

Anything goes in this folder, mixed freely:

- `.pdf` — exported checkup reports, lab PDFs
- `.jpg` / `.png` — photos or scans of paper slips
- `.txt` / `.md` — typed-out results
- `.csv` — columns `date, marker, value, unit`
- `.yaml` — self-reported / home measurements (copy
  `../sample_reports/self_reported.yaml`)

Name files with the date when you can — `2024-05-12_annual.pdf` — but a date
found inside the document always wins.

Then run, from the project root:

```bash
python3 littledocter.py run --no-vision --open
```

Reports are ignored by Git. The generated site also contains health data; share it only intentionally.
