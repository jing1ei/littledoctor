# Clinical scope

littledocter is an adult record-review aid. It is not clinically validated for diagnosis, treatment, pregnancy, children, or emergency triage. Severe or rapidly worsening symptoms require appropriate medical care regardless of the page's colours.

## Interpretation

- Simple printed laboratory intervals take precedence after known unit conversion. Catalog ranges are fallback examples, not individualized treatment targets.
- Unknown units, censored values such as `<6.5`, conflicting readings and context-sensitive measures are withheld from automatic interpretation or shown for review. Check the original report.
- Derived values are labelled. Historical age-dependent formulas require a birth date; pregnancy and age restrictions apply. A calculation does not establish a diagnosis.
- Findings are prompts for clinical review. Generic abnormal imaging does not diagnose fatty liver or thyroid nodules. TSH alone does not determine a thyroid subtype. Insulin-related ratios do not independently diagnose insulin resistance or PCOS.
- Current pattern rules exclude stale inputs. Combined results must be within three months; independent alternative tests are evaluated separately. This is a conservative software guard, not a validated biological window for every marker.
- Trends describe sampled records, not disease onset or prognosis. They do not fully adjust for assay changes, medicines or biological variation.
- Coverage is a weighted checklist, not a health score. Missing records do not prove a test was never done or that it is required.
- “Under care” is a display convention based on profile conditions; it does not establish that every abnormal result in the system is being treated.

## Nutrition, sleep and mental health

Food guidance supports varied, sustainable choices adapted to medical needs and resources. No result automatically prescribes supplements, fluid intake, protein targets or weight loss.

Sleep and psychological measures need symptom duration, daily function and personal context. Questionnaire totals and wearables do not diagnose disorders. A PHQ-9 total cannot assess self-harm risk or replace direct assessment. Persistent insomnia may warrant CBT-I assessment; short sleep alone does not automatically require a sleep study.

## Optional screening reference

Automatic screening is **off by default**. Selecting `screening_guideline: USPSTF` in the profile enables a US reference schedule, not a universal or Chinese guideline. A clinician must review anatomy, symptoms, previous abnormalities, family history and local policy before using it.

Additional profile flags under `flags:`:

| Flag | Required review |
| --- | --- |
| `ldct_eligibility_confirmed: true` | Confirm smoking exposure, cessation interval and suitability for curative treatment. The reference uses ages 50–80, ≥20 pack-years, current smoking or quitting within 15 years. |
| `psa_shared_decision: true` | Shared decision about PSA testing; reference ages 55–69. |
| `colorectal_colonoscopy_selected: true` or `colorectal_stool_selected: true` | Select one appropriate strategy, not both routinely. |
| `cervical_cytology_selected: true` or `cervical_hpv_selected: true` | Confirm cervix, age, prior results and one appropriate strategy. |
| `clinician_requested_<target>: true` | Explicit indication for a condition-specific test in `knowledge/screening.yaml`. Stored cadence still needs individual review. |

An in-date normal screening alternative suppresses duplicate reminders. This is not a complete surveillance or preventive-care system.

## Evidence references

These sources support specific design decisions; they do not validate every retained range or rule. Reviewed September 2026.

- [NIDDK: Diabetes tests](https://www.niddk.nih.gov/health-information/diabetes/overview/tests-diagnosis) and [insulin resistance](https://www.niddk.nih.gov/health-information/diabetes/overview/what-is-diabetes/prediabetes-insulin-resistance).
- [Endocrine Society: Vitamin D](https://www.endocrine.org/clinical-practice-guidelines/vitamin-d-for-prevention-of-disease).
- [WHO: Healthy diet](https://www.who.int/news-room/fact-sheets/detail/healthy-diet).
- [NHLBI: Insomnia treatment](https://www.nhlbi.nih.gov/health/insomnia/treatment) and [NIMH: Depression](https://www.nimh.nih.gov/health/topics/depression).
- [NIDDK: Fatty liver diagnosis](https://www.niddk.nih.gov/health-information/liver-disease/nafld-nash/diagnosis) and [NKF: Chronic kidney disease](https://www.kidney.org/kidney-topics/chronic-kidney-disease-ckd).
- USPSTF: [osteoporosis](https://www.uspreventiveservicestaskforce.org/uspstf/recommendation/osteoporosis-screening), [lung cancer](https://www.uspreventiveservicestaskforce.org/uspstf/recommendation/lung-cancer-screening), [breast cancer](https://www.uspreventiveservicestaskforce.org/uspstf/recommendation/breast-cancer-screening), [cervical cancer](https://www.uspreventiveservicestaskforce.org/uspstf/recommendation/cervical-cancer-screening), [colorectal cancer](https://www.uspreventiveservicestaskforce.org/uspstf/recommendation/colorectal-cancer-screening), [prostate cancer](https://www.uspreventiveservicestaskforce.org/uspstf/recommendation/prostate-cancer-screening), [carotid screening](https://www.uspreventiveservicestaskforce.org/uspstf/recommendation/carotid-artery-stenosis-screening), and [ECG screening](https://www.uspreventiveservicestaskforce.org/uspstf/recommendation/cardiovascular-disease-risk-screening-with-electrocardiography).

## Limits

No complete critical-value assessment, medication-interaction evaluation, prospective clinical validation or clinician sign-off is implemented. OCR and parsing errors remain possible. Keep source verification and clinical judgment in the workflow.
