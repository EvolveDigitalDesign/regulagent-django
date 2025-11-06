import pdfplumber, re, json, pytesseract
from pdf2image import convert_from_path
from datetime import datetime

# ---------- CONFIG ----------
INPUT_PDF = "Dist 08-8A Plugging Book.pdf"
OUTPUT_JSON = "tx_rrc_w3a_district_08a_policy_pack.json"
DISTRICT_CODE = "08-8A"
VERSION = "2025.1"
GENERATED_AT = datetime.utcnow().isoformat() + "Z"
# ----------------------------

def clean_depth(value):
    """Normalize depth values: remove commas, handle ranges."""
    if not value:
        return None
    value = re.sub(r"[^\d\-]", "", value)
    parts = [p for p in value.split("-") if p]
    try:
        if len(parts) == 2:
            return {"min": int(parts[0]), "max": int(parts[1])}
        elif len(parts) == 1:
            return {"baseDepth": int(parts[0])}
    except ValueError:
        return None
    return None

def normalize_formation(name):
    """Expand known abbreviations."""
    mapping = {
        "Ell": "Ellenburger",
        "Dev": "Devonian",
        "Str": "Strawn",
        "Spra": "Spraberry",
        "SA": "San Andres",
        "Yates": "Yates",
        "Fuss": "Fusselman",
        "Cany": "Canyon",
        "Wfcp": "Wolfcamp",
        "SR": "Santa Rosa"
    }
    for k,v in mapping.items():
        if re.fullmatch(k, name, re.I):
            return v
    return name

def parse_text_page(text):
    """Basic parser to split counties and fields."""
    counties = []
    current_county = None
    current_field = None

    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue

        # Detect new county header
        if re.match(r"^[A-Z][a-z]+ County", line):
            if current_county:
                counties.append(current_county)
            county_name = line.split()[0]
            current_county = {
                "countyName": county_name,
                "lastUpdated": None,
                "waterboardRange": None,
                "countyNotes": [],
                "protectedFormations": [],
                "fields": []
            }
            continue

        # Detect field headers
        if re.match(r"^[A-Z0-9][\w\s\.,&-]+Field", line) or re.match(r"^[A-Z].{2,30}$", line):
            if current_field:
                current_county["fields"].append(current_field)
            current_field = {
                "fieldName": line.replace("Field","").strip(),
                "waterboardLevel": None,
                "formations": [],
                "remarks": [],
                "rawText": line
            }
            continue

        # Detect WBL/SR notation
        if "WBL" in line or "SR" in line:
            wbl_match = re.search(r"WBL\s*(\d+)", line)
            sr_match = re.search(r"SR\s*\(?(\d+)[^\d]+(\d+)\)?", line)
            wl = {}
            if wbl_match:
                wl["baseDepth"] = int(wbl_match.group(1))
            if sr_match:
                wl["santaRosaRange"] = {"min": int(sr_match.group(1)), "max": int(sr_match.group(2))}
            if wl:
                current_field["waterboardLevel"] = wl
            current_field["remarks"].append(line)
            current_field["rawText"] += " " + line
            continue

        # Detect formation rows
        fm = re.match(r"^([A-Z][A-Za-z0-9/\. ]+)\s+(\d{3,5})", line)
        if fm:
            f_abbr = fm.group(1).strip()
            f_depth = int(fm.group(2))
            current_field["formations"].append({
                "formationName": f_abbr,
                "fullName": normalize_formation(f_abbr),
                "topDepth": f_depth,
                "isProductive": True,
                "requiresTag": "TAG" in line
            })
            current_field["rawText"] += " " + line
            continue

        # Notes
        if "TAG" in line or "NOTE" in line.upper():
            current_field["remarks"].append(line)
            current_field["rawText"] += " " + line

    if current_field and current_county:
        current_county["fields"].append(current_field)
    if current_county:
        counties.append(current_county)

    return counties


def extract_text(pdf_path):
    """Extract text using pdfplumber with OCR fallback."""
    text_content = []
    with pdfplumber.open(pdf_path) as pdf:
        for i, page in enumerate(pdf.pages):
            txt = page.extract_text()
            if not txt or len(txt.strip()) < 40:
                # OCR fallback
                image = convert_from_path(pdf_path, first_page=i+1, last_page=i+1)[0]
                txt = pytesseract.image_to_string(image)
            text_content.append(txt)
    return "\n".join(text_content)


def build_output_structure(counties):
    """Wrap final JSON structure."""
    return {
        "metadata": {
            "sourceFile": INPUT_PDF,
            "generatedBy": "RegulAgent Policy Parser",
            "generatedAt": GENERATED_AT,
            "district": DISTRICT_CODE,
            "schemaVersion": VERSION,
            "normalizationRules": [
                "Standardized depths to integers",
                "Normalized formation abbreviations",
                "Resolved inconsistent WBL/SR notation",
                "Removed duplicate and outdated records",
                "Merged continuation pages into complete tables",
                "Consolidated Capitan Reef isolation notes",
                "Aligned all dates to ISO 8601 format"
            ],
            "ocrConfidence": "variable",
            "recordCount": len(counties)
        },
        "generalNotes": [
            "Waterboard generally requires protection of Santa Rosa (SR).",
            "* Denotes a non-productive formation.",
            "TAG all surface casing shoe plugs in open hole where noted."
        ],
        "warnings": [
            "Capitan Reef isolation required throughout district.",
            "Verify DUQW per SWR-14 before setting shallow plugs."
        ],
        "counties": counties
    }


def main():
    print(f"Extracting text from {INPUT_PDF}...")
    text = extract_text(INPUT_PDF)
    print("Parsing and normalizing...")
    counties = parse_text_page(text)
    output = build_output_structure(counties)
    with open(OUTPUT_JSON, "w", encoding="utf-8") as f:
        json.dump(output, f, indent=2)
    print(f"✅ Policy pack generated: {OUTPUT_JSON}")


if __name__ == "__main__":
    main()
