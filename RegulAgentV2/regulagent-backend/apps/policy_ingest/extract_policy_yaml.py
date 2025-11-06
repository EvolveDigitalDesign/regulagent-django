import re, yaml
from datetime import datetime
from pathlib import Path

# ---------- CONFIG ----------
INPUT_TXT = 'apps/policy_ingest/3.14.txt'
OUTPUT_YAML = 'apps/policy_ingest/tx_rrc_w3a_base_policy_pack.yaml'
# ----------------------------

def sectionize(text):
    """Split rule §3.14 into subsections (a)–(k) using top-level headers only."""
    # Match only headers at line start like "(d) General ..." and avoid nested (i), (ii) etc
    parts = re.split(r"(?m)^\(([a-k])\)\s(?=[A-Z])", text)
    sections = {'intro': parts[0].strip()}
    for i in range(1, len(parts), 2):
        key = parts[i]
        val = parts[i + 1].strip()
        first_line = val.splitlines()[0]
        title = re.sub(r"^[A-Z].*?\.?\s*", lambda m: m.group(0).strip(), first_line)
        sections[key] = {
            "title": first_line.strip(),
            "text": val
        }
    return sections


def extract_values(text):
    """Extract quantitative plugging parameters."""
    reqs = {}

    def add_req(key, val, citation, desc, unit=None):
        reqs[key] = {
            "value": val,
            "citation_keys": [citation],
            "text": desc
        }
        if unit:
            reqs[key]["unit"] = unit

    # Surface casing shoe plug
    if re.search(r"100\s*feet.*surface casing.*shoe", text, re.I):
        add_req(
            "surface_casing_shoe_plug_min_ft",
            100, "tx.tac.16.3.14(e)(2)",
            "Cement plug across surface casing shoe: minimum 100 ft (50 ft above and below).",
            "ft"
        )

    # DUQW isolation
    if re.search(r"50\s*feet.*above.*below.*usable", text, re.I):
        add_req(
            "uqw_isolation_min_len_ft",
            100, "tx.tac.16.3.14(g)(1)",
            "Plug across base of deepest usable-quality water stratum: 50 ft above and 50 ft below."
        )

    # CIBP cement cover
    if re.search(r"20\s*feet.*bridge plug", text, re.I):
        add_req(
            "cement_above_cibp_min_ft",
            20, "tx.tac.16.3.14(g)(3)",
            "At least 20 ft of cement must be placed on top of each cast-iron bridge plug."
        )

    # Mud weight requirement
    if re.search(r"9-?1/?2", text):
        add_req(
            "mud_weight_min_ppg",
            9.5, "tx.tac.16.3.14(d)(9)",
            "Mud-laden fluid ≥9½ ppg and ≥40 s funnel viscosity fills uncemented portions.",
            "ppg"
        )

    # Top plug
    if re.search(r"10[-\s]?foot cement plug.*top", text, re.I):
        add_req(
            "top_plug_length_ft",
            10, "tx.tac.16.3.14(d)(8)",
            "Onshore wells require a 10 ft cement plug at the top.",
            "ft"
        )

    # Casing cut
    if re.search(r"three feet below the ground surface", text, re.I):
        add_req(
            "casing_cut_below_surface_ft",
            3, "tx.tac.16.3.14(d)(8)",
            "Casing shall be cut off 3 ft below ground surface.",
            "ft"
        )

    # Volume excess
    if re.search(r"10%.*each 1,000 feet", text):
        add_req(
            "plug_volume_excess_percent",
            10, "tx.tac.16.3.14(d)(11)",
            "All plugs except the top plug require +10 % slurry volume per 1000 ft depth.",
            "percent per 1000 ft depth"
        )

    return reqs


def main():
    text = Path(INPUT_TXT).read_text(encoding="utf-8")
    sections = sectionize(text)
    requirements = extract_values(text)

    policy_pack = {
        "policy_id": "tx.w3a",
        "policy_version": "2025.10.0",
        "jurisdiction": "TX",
        "form": "W-3A",
        "effective_from": "2025-07-01",
        "citations":
            {
                "source": "16 TAC §3.14",
                "link": "https://www.law.cornell.edu/regulations/texas/title-16/part-1/chapter-3/section-3.14"
            },
        "metadata": {
            "generatedAt": datetime.utcnow().isoformat() + "Z",
            "sourceFile": INPUT_TXT
        },
        "sections": sections,
        "base": {"requirements": requirements}
    }

    with open(OUTPUT_YAML, "w", encoding="utf-8") as f:
        yaml.safe_dump(policy_pack, f, sort_keys=False, allow_unicode=True)

    print(f"✅ YAML policy pack written to {OUTPUT_YAML}")
    print(f"   Sections: {len(sections)}  Requirements: {len(requirements)}")


if __name__ == "__main__":
    main()
