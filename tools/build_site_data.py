"""
Merge every raw/output/<slug>.json (produced by parse_pdf.py, or hand
transcribed) into the single flattened data/benchmarks.json the site reads.

Run: python build_site_data.py manifest.json raw/output ../data/benchmarks.json
"""
import json
import re
import sys
from pathlib import Path

CODE_RE = re.compile(r'([A-Z]{2,6})\s?(\d)-(\d{2})[a-z]?')

# Some PDFs are sub-parts of one real curriculum area; group them under a
# single display name so the site's area filter doesn't fragment them.
AREA_OVERRIDES = {
    "modern-languages-early": "Modern Languages",
    "hwb-food-health": "Health and Wellbeing",
    "hwb-personal-social": "Health and Wellbeing",
    "hwb-pe": "Health and Wellbeing",
}


def first_code_parts(codes):
    for code in codes:
        m = CODE_RE.search(code)
        if m:
            return m.group(1), int(m.group(2))
    return "", 0


def main():
    manifest_path = Path(sys.argv[1])
    raw_dir = Path(sys.argv[2])
    out_path = Path(sys.argv[3])

    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))

    rows = []
    total_records = 0
    for entry in manifest:
        slug = entry["slug"]
        area = AREA_OVERRIDES.get(slug, entry["title"])
        data_path = raw_dir / f"{slug}.json"
        if not data_path.exists():
            continue
        records = json.loads(data_path.read_text(encoding="utf-8"))
        total_records += len(records)
        for i, r in enumerate(records):
            area_code, level = first_code_parts(r["codes"])
            eo_code = " / ".join(r["codes"])
            for j, benchmark in enumerate(r["benchmarks"]):
                rows.append({
                    "id": f"{slug}-{i}-{j}",
                    "area": area,
                    "areaCode": area_code,
                    "level": level,
                    "organiser": r["organiser"],
                    "eoCode": eo_code,
                    "eoText": r["eo_text"],
                    "benchmark": benchmark,
                })

    out_path.write_text(json.dumps(rows, indent=2, ensure_ascii=False), encoding="utf-8")
    areas = sorted({r["area"] for r in rows})
    print(f"{total_records} EO records -> {len(rows)} benchmark rows across {len(areas)} areas")
    for a in areas:
        print(" -", a, sum(1 for r in rows if r["area"] == a))


if __name__ == "__main__":
    main()
