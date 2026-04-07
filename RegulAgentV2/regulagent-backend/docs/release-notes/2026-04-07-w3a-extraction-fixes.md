# W3A Wizard Fixes — April 7, 2026

## Overview

This update addresses several extraction and usability issues that were discovered while running a Post Plugging Report (W3A) for **API 42-389-33390**. If you ran this well's W3A before today and noticed any of the problems described below, we recommend re-running the wizard to get accurate results.

---

## What Was Reported

A user working API 42-389-33390 encountered the following issues:

1. **Cement sack counts were incomplete** — The surface casing section only showed 600 sacks instead of the correct total of 2,300 sacks across all slurry stages.
2. **Cement top depth was wrong** — The system was reporting a cement top of 24 feet, which was actually the WOC (Waiting on Cement) duration in hours, not a depth.
3. **Same multi-slurry issue on intermediate casing** — Not all slurry stages were being captured for the intermediate casing string.
4. **Interval depths using wrong reference point** — Some casing interval depths were being pulled from the cement placement top instead of the casing shoe, leading to incorrect depth entries.
5. **No way to exclude duplicate documents** — When uploading tickets, there was no way to uncheck a document you didn't want parsed, so duplicate or incorrect files would get pulled in regardless.

---

## What We Fixed

### 1. All Slurry Stages Are Now Captured

**Before:** When a cementing job had multiple slurry stages (e.g., Slurry 1 and Slurry 2), the system was only reading the first stage it found and stopping there.

**After:** The system now reads every slurry stage listed in the W-15 cementing report — Slurry 1, Slurry 2, Slurry 3, and so on — for each casing string. The total sack count reported is the sum across all stages.

For API 42-389-33390, the surface casing cement job had:
- Slurry 1: 1,700 sacks
- Slurry 2: 600 sacks
- **Correct total: 2,300 sacks**

You should now see 2,300 sacks reflected in the report.

---

### 2. Cement Top Depth No Longer Confused with WOC Hours

**Before:** The W-15 report lists WOC (Waiting on Cement) time in hours, and the cement top depth in feet — often in nearby fields. The system was occasionally reading the WOC hours field (e.g., "24 hours") and treating it as a depth of 24 feet.

**After:** The system now specifically identifies and reads the cement top depth field, and separately reads the WOC hours field. These will never be mixed up. A cement top depth will always be a realistic depth value in feet — you won't see a 24-foot cement top on a surface casing job again.

---

### 3. Casing Interval Depths Use the Correct Reference Points

**Before:** For some casing strings, the interval's top and bottom depths were being sourced from the cement placement depths rather than the casing string's own top-of-string and shoe depth.

**After:** Casing interval depths now correctly reflect the casing string itself — top of casing to casing shoe — independent of where cement was actually placed. Cement top and DV tool depth are still recorded separately as part of the cementing data.

---

### 4. You Can Now Exclude Documents Before Parsing

**Before:** Once you uploaded a document in the ticket upload step, it was always included in parsing. There was no way to skip a file you uploaded by mistake or a duplicate you didn't intend to use.

**After:** Each uploaded document now has a checkbox next to it. Documents are included by default. If you want to skip a file:

- Uncheck the checkbox next to that document
- It will appear grayed out with a strikethrough to confirm it's excluded
- When you click **Parse Tickets**, excluded documents will be skipped entirely
- You can re-check a document at any time to include it again

This is especially useful when you have duplicate permit uploads or want to parse only a specific subset of your documents.

---

## How to Verify

If you ran a W3A for API 42-389-33390 before this update, here's how to confirm everything is now correct:

1. **Re-run the W3A** for API 42-389-33390 from the beginning of the wizard.
2. **Surface casing sack count** — Confirm the total sack count reflects all slurry stages. For this well, you should see 2,300 sacks (1,700 + 600), not 600.
3. **Cement top depth** — Verify the cement top depth is a realistic footage depth (typically in the hundreds to thousands of feet range), not a single- or double-digit WOC hour value.
4. **Interval depths** — Confirm that casing interval top and bottom depths match the actual casing top and shoe depths from the completion records, not the cement squeeze depths.
5. **Document exclusion** — Upload two copies of the same document, uncheck one, then click Parse Tickets. Only the checked document should be parsed.

---

## Questions?

If you're still seeing unexpected values after re-running, please reach out with the API number and a screenshot of the affected section. Include which casing string (surface, intermediate, production) and what value you're seeing vs. what you expect.
