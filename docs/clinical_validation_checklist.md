# CORRECT Clinical Validation Checklist

**Version:** 1.0  
**Date:** 2026-04-15  
**Purpose:** Structured protocol for validating the CORRECT research software with clinical reviewers (Radiologist and Radiation Oncologist) prior to and during study participation.

---

## Overview

Validation should be performed by at least one **Radiation Oncologist (RO)** and one **Radiologist (RAD)** reviewing the same processed study side-by-side. A technical operator (e.g., medical physicist or software administrator) should be present to manage the system and record results. This document covers both **non-PACS mode** (standalone file output, local review) and **PACS-integrated mode** (results delivered to clinical PACS/VNA for workstation review).

---

## Section 1 — Pre-Validation Setup (Technical Operator)

These items are completed before the clinical review session begins.

- [ ] CORRECT application is running and DICOM listener is confirmed active (`echoscu` ping returns success)
- [ ] Configuration (`config.yaml`) reviewed and documented — note whether anonymization is enabled, which contour name filters are active, and which output series are enabled
- [ ] A known, representative test case (RT plan + CT + RTSTRUCT from treatment planning workstation) has been identified for transmission
- [ ] In PACS mode: confirm destination PACS/VNA AE title, host, and port are correctly configured and reachable
- [ ] In non-PACS mode: confirm output directory is accessible and reviewers know where to open files (e.g., Weasis, Horos, OsiriX, Slicer, or OHIF)
- [ ] Logs directory is accessible: `~/CORRECT_logs/application.log` and `~/CORRECT_logs/transaction.log`
- [ ] Burn-in disclaimer text is confirmed in config (`burn_in_text` field) and is appropriate for study context
- [ ] Anonymization settings reviewed: if enabled, a test patient ID has been pre-mapped in `pid_mapping.json`

---

## Section 2 — Transmission and Reception (Technical Operator)

- [ ] Send test study from treatment planning workstation (C-STORE to CORRECT listener on port 11112)
- [ ] Confirm `application.log` shows successful file receipt and study lock acquisition
- [ ] Confirm processing completes: log shows "Study processing complete" or equivalent without ERROR entries
- [ ] Confirm `transaction.log` has a completed audit entry for the study UID
- [ ] In PACS mode: confirm output series appear in destination PACS within expected timeframe
- [ ] In non-PACS mode: confirm output files are present in the shared output or working directory

---

## Section 3 — Radiation Oncologist Review (RO)

The RO should review the processed output series in the clinical workstation or designated viewer. They are the ground truth for contour accuracy and clinical intent.

### 3.1 Contour Identity and Completeness
- [ ] All expected contour structures (as drawn in the treatment planning system) are visually present in the overlay series
- [ ] No contours are missing that should appear (cross-check against original RTSTRUCT file structure names)
- [ ] No contours that should be excluded (per the `ignore_contour_names_containing` filter) are erroneously displayed
- [ ] Contour labels or structure names are identifiable (if shown in overlay or GSPS annotation)

### 3.2 Geometric Accuracy
- [ ] Contour boundaries in the overlay series align correctly with the corresponding CT anatomy slice-by-slice
- [ ] No gross registration errors (e.g., contours shifted relative to CT anatomy)
- [ ] 3D extent of contours is preserved: topmost, bottommost, and central slices all appear correctly
- [ ] Contour shape is consistent with what was drawn in the planning system (no obvious smoothing artifacts or missing vertices)

### 3.3 Output Series Review
- [ ] **Augmented CT series** (overlay planes, DICOM group 0x6000): contour is visible as an overlay and does not obscure diagnostic anatomy
- [ ] **GSPS series** (if enabled): graphical annotation displays correctly on the CT series in the workstation; overlay color is appropriate
- [ ] **DICOM Segmentation series** (if enabled): segmentation objects load and are associated with the correct CT frame of reference
- [ ] **Secondary Capture series** (if enabled): rendered visualization is legible and anatomically plausible
- [ ] Burn-in disclaimer text ("FOR RESEARCH USE ONLY — NOT FOR CLINICAL USE") is clearly visible on all relevant series
- [ ] Series numbers and descriptions (e.g., "RESEARCH ONLY: Unapproved Treatment Plan CT w Mask") are correctly labeled in the workstation

### 3.4 RO Sign-Off Questions
- [ ] Do the contours in the CORRECT output faithfully represent the structures drawn in the treatment planning system?
- [ ] Would this output be useful for the intended research purpose?
- [ ] Are there any contours that appeared incorrectly (document which ones if yes)?

**RO Reviewer Name:** _______________________________  
**Date:** _______________  
**Signature:** _______________________________  
**Notes:** ___________________________________________________________________

---

## Section 4 — Radiologist Review (RAD)

The radiologist focuses on image quality, anatomy visibility, and whether the overlay presentation is compatible with diagnostic reading workflows.

### 4.1 Image Quality and Fidelity
- [ ] CT image quality in the processed series is comparable to the original CT (no pixel corruption, unexpected windowing changes, or compression artifacts)
- [ ] DICOM metadata in the processed series is consistent (correct patient ID, study date, series description)
- [ ] Images load correctly in the radiology workstation (no unsupported transfer syntax errors or display failures)

### 4.2 Overlay Legibility
- [ ] Contour overlays are visually distinguishable from CT anatomy without obscuring clinically important structures
- [ ] GSPS overlay color and opacity are appropriate for the viewing environment (report if too bright/faint)
- [ ] Burn-in text does not obstruct diagnostic anatomy on key slices

### 4.3 PACS Integration (PACS Mode Only)
- [ ] Processed series appear under the correct patient/study in the PACS worklist
- [ ] Study metadata (patient name, MRN, accession number) matches expectation based on anonymization settings
- [ ] No duplicate or orphaned series appear in PACS under the same study
- [ ] Series appear in a logical order relative to the original CT series
- [ ] Hanging protocols (if configured) associate the overlay series correctly with the base CT

### 4.4 Non-PACS Mode (Standalone Viewer)
- [ ] Files can be opened directly in local DICOM viewer without errors
- [ ] Series metadata is intact and viewable
- [ ] Multi-frame series (if applicable) scroll correctly

### 4.5 RAD Sign-Off Questions
- [ ] Is the image quality of the processed output acceptable for research review?
- [ ] Does the overlay presentation support the intended comparison workflow?
- [ ] Are there any display or metadata issues that would interfere with use?

**RAD Reviewer Name:** _______________________________  
**Date:** _______________  
**Signature:** _______________________________  
**Notes:** ___________________________________________________________________

---

## Section 5 — Joint Review (RO + RAD Together)

These items require both reviewers present at the same workstation.

- [ ] Both reviewers confirm they are looking at the same study and the same set of output series
- [ ] Side-by-side comparison of contour from planning system (if accessible) vs. CORRECT overlay confirms spatial consistency
- [ ] Both agree that the anonymization level (if enabled) is appropriate for the study protocol
- [ ] Both reviewers confirm the burn-in disclaimer is acceptable and correctly worded for research use
- [ ] Discuss and document any discrepancy between what the RO drew and what the RAD sees in the overlay

---

## Section 6 — Failure and Edge Case Testing

The following scenarios should be tested at least once during the validation period.

| Scenario | Expected Behavior | Pass/Fail | Notes |
|---|---|---|---|
| Send study with **no RTSTRUCT** file | Study logged as warning; no output generated; no crash | | |
| Send study with **partial RTSTRUCT** (some contours empty) | Non-empty contours processed; empty ones skipped gracefully | | |
| Send study where **contour name matches ignore filter** | Ignored contour absent from output; others present | | |
| Send **duplicate study** (same StudyUID twice) | Second transmission handled safely (lock mechanism); no corruption | | |
| Send study where **destination PACS is unreachable** (PACS mode) | Error logged; study quarantined; no crash | | |
| Review **quarantine directory** contents | Quarantined studies accessible for re-processing or investigation | | |

---

## Section 7 — Audit Log Review (Technical Operator + RO)

- [ ] `transaction.log` entries are reviewed for the test cases above
- [ ] Each processed study has a corresponding audit entry with study UID, sender IP, and AE title
- [ ] Error entries (if any) are documented and understood
- [ ] Log retention and access controls are appropriate for the study protocol

---

## Section 8 — Final Validation Sign-Off

| Item | Status | Reviewer |
|---|---|---|
| RO contour accuracy review complete | | |
| RAD image quality review complete | | |
| PACS integration confirmed (if applicable) | | |
| Audit log reviewed | | |
| Edge case scenarios tested | | |
| Burn-in and anonymization settings confirmed | | |

**Overall Validation Result:** `PASS / FAIL / CONDITIONAL PASS`

**Conditions or Required Changes (if conditional):**
___________________________________________________________________
___________________________________________________________________

**Technical Operator:** _______________________________  Date: _______________  
**Radiation Oncologist:** _______________________________  Date: _______________  
**Radiologist:** _______________________________  Date: _______________  
