"""Counterexamples for clinical overinterpretation; not clinical validation."""
import unittest

from src.analyze import analyze, status_of
from src.derive import compute_derived
from src.ingest import make_measurement
from src.kb import kb


class ClinicalReasoningTests(unittest.TestCase):
    def setUp(self):
        self.K = kb()
        self.profile = {"sex": "female", "birth_date": "1980-01-01"}

    def row(self, marker, value, when="2026-09-01", unit="", ref=""):
        return make_measurement(self.K, marker, value, unit, when, "fixture", ref_raw=ref)

    def result(self, rows=(), exams=(), profile=None):
        return analyze({"measurements": list(rows), "exams": list(exams)},
                       self.profile if profile is None else profile, self.K, "2026-09-27")

    def ids(self, result):
        return {f["id"] for f in result["findings"]}

    def test_abnormal_imaging_is_not_specific_diagnosis(self):
        result = self.result(exams=[{"exam": "abdominal_ultrasound", "date": "2026-09-01",
                                     "status": "abnormal", "impression": "Gallstone"},
                                    {"exam": "thyroid_ultrasound", "date": "2026-09-01",
                                     "status": "abnormal", "impression": "Diffuse change"}])
        self.assertNotIn("fatty_liver_confirmed", self.ids(result))
        self.assertNotIn("thyroid_nodule", self.ids(result))
        self.assertEqual(result["exams"]["abdominal_ultrasound"]["impression"], "Gallstone")

    def test_diabetes_range_is_not_prediabetes(self):
        result = self.result(self.row("hba1c", 8.0))
        self.assertIn("diabetes_range", self.ids(result))
        self.assertNotIn("prediabetes", self.ids(result))
        self.assertIn("prediabetes", self.ids(self.result(self.row("hba1c", 6.0))))

    def test_current_result_not_suppressed_by_unrelated_old_branch(self):
        result = self.result(self.row("hba1c", 8) + self.row("glucose_fasting", 5, "2019-01-01"))
        self.assertIn("diabetes_range", self.ids(result))
        finding = next(f for f in result["findings"] if f["id"] == "diabetes_range")
        self.assertEqual([e["id"] for e in finding["evidence"]], ["hba1c"])

    def test_combinations_still_require_nearby_dates(self):
        result = self.result(self.row("triglycerides", 2) + self.row("hdl_c", 0.8, "2026-01-01"))
        self.assertNotIn("atherogenic_dyslipidemia", self.ids(result))

    def test_normal_history_is_not_called_unmeasured(self):
        result = self.result(self.row("alt", 20, "2025-01-01") + self.row("alt", 22))
        story = result["trends"]["systems"]["liver"]
        self.assertEqual(story["visits"], 2)
        self.assertEqual(story["trajectory"], "stable")

    def test_uninterpreted_system_is_not_in_range(self):
        result = self.result(self.row("phq9", 18) + self.row("gad7", 14))
        system = next(s for s in result["systems"] if s["id"] == "neuro_sleep")
        self.assertEqual(system["status"], "unknown")

    def test_nonfinite_and_qualitative_conflicts(self):
        rows = self.row("hba1c", 5)
        rows[0]["value"] = float("nan")
        self.assertNotIn("hba1c", self.result(rows)["markers"])
        result = self.result(self.row("hbsag", "positive") + self.row("hbsag", "negative"))
        self.assertEqual(result["markers"]["hbsag"]["status"], "context")

    def test_pregnancy_and_impossible_lipid_derivation(self):
        rows = self.row("creatinine", 80) + self.row("total_cholesterol", 2) + self.row("hdl_c", 3)
        derived = compute_derived(self.K, rows, dict(self.profile, flags={"pregnant": True}))
        self.assertFalse({"egfr", "non_hdl_c"} & {r["marker"] for r in derived})

    def test_normal_alt_tsh_and_vitamin_d_do_not_trigger_optimal_band_alerts(self):
        rows = self.row("alt", 24) + self.row("tsh", 3) + self.row("vitamin_d", 25)
        result = self.result(rows)
        self.assertNotIn("liver_enzymes_suboptimal", self.ids(result))
        self.assertNotIn("tsh_high_normal", self.ids(result))
        self.assertNotIn("vitamin_d_low", self.ids(result))
        self.assertEqual(result["markers"]["vitamin_d"]["status"], "steady")

    def test_tsh_does_not_assert_normal_ft4(self):
        result = self.result(self.row("tsh", 8))
        finding = next(f for f in result["findings"] if f["id"] == "subclinical_hypothyroid")
        self.assertIn("alone cannot distinguish", finding["says"])
        self.assertNotIn("subclinical", finding["title"].lower())

    def test_no_pcos_or_insulin_resistance_diagnosis_from_surrogates(self):
        rows = self.row("testosterone_total", 3) + self.row("lh", 15) + self.row("homa_ir", 4)
        self.assertFalse({"pcos_pattern", "insulin_resistance"} & self.ids(self.result(rows)))

    def test_missing_demographics_and_unselected_schedule(self):
        self.assertEqual(self.result(profile={})["screening"], [])
        self.assertEqual(self.result()["screening"], [])
        result = self.result(profile={"screening_guideline": "USPSTF"})
        self.assertEqual(result["screening"], [])
        result = self.result(profile={"age": 70, "screening_guideline": "USPSTF"})
        self.assertFalse({"s_mammo", "s_dxa", "s_psa"} & {s["id"] for s in result["screening"]})

    def test_no_routine_dxa_at_35_or_ct_for_secondhand_smoke(self):
        profile = {"age": 35, "sex": "female", "screening_guideline": "USPSTF"}
        result = self.result(profile=profile)
        self.assertNotIn("s_dxa", {s["id"] for s in result["screening"]})
        profile.update(age=55, flags={"heavy_secondhand_smoke": True})
        self.assertNotIn("s_ldct", {s["id"] for s in self.result(profile=profile)["screening"]})

    def test_screening_alternatives_not_both_due(self):
        profile = dict(self.profile, screening_guideline="USPSTF",
                       flags={"cervical_hpv_selected": True})
        result = self.result(exams=[{"exam": "cervical_cytology", "date": "2025-09-01",
                                     "status": "normal", "impression": "normal"}], profile=profile)
        self.assertFalse({"s_cervical", "s_cervical_hpv"} & {s["id"] for s in result["screening"]})

    def test_stale_results_do_not_drive_current_findings(self):
        result = self.result(self.row("hba1c", 9, "2019-01-01"))
        self.assertNotIn("diabetes_range", self.ids(result))
        self.assertEqual(result["markers"]["hba1c"]["value"], 9)
        self.assertNotEqual(next(s for s in result["systems"] if s["id"] == "metabolic")["status"], "attention")

    def test_unknown_unit_not_guessed_or_interpreted(self):
        result = self.result(self.row("glucose_fasting", 180, unit="mystery"))
        self.assertNotIn("glucose_fasting", result["markers"])
        self.assertTrue(any("unverified unit" in w for w in result["kb_warnings"]))

    def test_censored_values_not_treated_as_exact(self):
        result = self.result(self.row("hba1c", "<6.5"))
        self.assertNotIn("hba1c", result["markers"])

    def test_printed_reference_not_overridden(self):
        result = self.result(self.row("alt", 45, ref="0-50"))
        self.assertEqual(result["markers"]["alt"]["status"], "steady")
        self.assertNotIn("liver_enzymes_suboptimal", self.ids(result))
        self.assertIn("0-50", result["markers"]["alt"]["band"])

    def test_historical_age_and_conflicting_inputs(self):
        rows = self.row("creatinine", 80)
        self.assertEqual(compute_derived(self.K, rows, {"sex": "female", "age": 40}), [])
        rows += self.row("creatinine", 120)
        self.assertFalse(any(r["marker"] == "egfr" for r in compute_derived(self.K, rows, self.profile)))

    def test_source_reference_unit_conversion_and_ambiguous_range(self):
        result = self.result(self.row("glucose_fasting", 90, unit="mg/dL", ref="70-99"))
        self.assertEqual(result["markers"]["glucose_fasting"]["status"], "steady")
        result = self.result(self.row("alt", 45, ref="ask lab"))
        self.assertEqual(result["markers"]["alt"]["status"], "context")

    def test_pregnancy_and_child_do_not_use_adult_rules(self):
        for profile in [{"age": 12, "sex": "female"},
                        dict(self.profile, flags={"pregnant": True})]:
            result = self.result(self.row("hba1c", 8, ref="4-6"), profile=profile)
            self.assertEqual(result["markers"]["hba1c"]["status"], "context")
            self.assertEqual(result["findings"], [])

    def test_psychological_and_phase_dependent_results_require_context(self):
        for mid in ["hrv", "stress_score", "phq9", "gad7", "lh"]:
            self.assertEqual(status_of(self.K.markers[mid], 15, None, "female")[0], "context")
        self.assertNotIn("sleep_apnea_suspicion", self.ids(self.result(
            self.row("bmi", 28) + self.row("sleep_duration", 6))))
        profile = dict(self.profile, flags={"snoring": True, "daytime_sleepiness": True})
        self.assertIn("sleep_apnea_suspicion", self.ids(self.result(profile=profile)))


if __name__ == "__main__":
    unittest.main()
