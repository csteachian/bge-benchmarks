"""
Parse official Education Scotland CfE "Benchmarks" PDFs into structured JSON.

Approach
--------
These PDFs lay Benchmarks out as a 3-4 column table (an optional "strand"
label | Curriculum Organiser | Experience & Outcome + code | Benchmarks),
with the leftmost label sometimes printed sideways. We use `pdfplumber` word
boxes (real PDF point-coordinates, not character-grid cells) so that column
boundaries stay consistent even when a document switches font size between
its "Early/First/.../Fourth Level" sections:

  1. For every line, find gaps between adjacent words wider than normal
     inter-word spacing. Cluster those gap positions across the whole
     document to find the table's true column boundaries (in points).
  2. Slice each line's words into columns at those boundaries.
  3. Locate EO codes (e.g. "TCH 0-05a") in whichever column contains most of
     them - that anchors one block per Experience & Outcome. Everything
     between consecutive anchors, in every column, belongs to that block.
  4. Forward-fill the organiser column (only printed once per merged cell)
     and split the benchmarks column's block text on its bullet character.

Run: python parse_pdf.py <manifest.json> <out_dir>
"""
import difflib
import json
import re
import sys
import unicodedata
from pathlib import Path

import pdfplumber

BULLET_CHARS = ("", "•", "●", "•")
EO_CODE_RE = re.compile(r'\b([A-Z]{2,6})\s?(\d)-(\d{2})([a-z]?)\b')
GAP_THRESHOLD = 11.0  # points; normal inter-word gap is ~2-4pt at these font sizes
CLUSTER_TOL = 6.0     # points; merge candidate gutters within this distance
LINE_TOL = 2.5        # points; words within this 'top' distance are one line


def clean_text(s: str) -> str:
    s = unicodedata.normalize("NFKC", s)
    # Some of these PDFs render heavily-tracked/justified text as individual
    # letters with wide gaps, e.g. "D e s c r i b e s" -> collapse it back.
    s = re.sub(
        r'(?:\b[A-Za-z]\b[ \t]){3,}\b[A-Za-z]\b',
        lambda m: m.group(0).replace(" ", ""),
        s,
    )
    s = s.replace("’", "'").replace("‘", "'")
    s = s.replace("“", '"').replace("”", '"').replace("„", '"')
    s = s.replace("–", "-").replace("—", "-")
    s = re.sub(r'[ \t]+', ' ', s)
    s = re.sub(r'\s+([.,;:])', r'\1', s)
    return s.strip(" \t-")


def extract_lines(page):
    """Group a page's words into visual lines: list of [(x0,x1,text), ...]."""
    words = page.extract_words(use_text_flow=False, keep_blank_chars=False)
    words.sort(key=lambda w: (round(w["top"] / LINE_TOL), w["x0"]))
    lines = []
    cur = []
    cur_top = None
    for w in words:
        if cur_top is None or abs(w["top"] - cur_top) <= LINE_TOL:
            cur.append(w)
            cur_top = w["top"] if cur_top is None else cur_top
        else:
            lines.append(sorted(cur, key=lambda w: w["x0"]))
            cur = [w]
            cur_top = w["top"]
    if cur:
        lines.append(sorted(cur, key=lambda w: w["x0"]))
    return lines


HEADER_PATTERNS = [
    re.compile(r'Curriculum\s+Organisers?', re.I),
    re.compile(r'Experiences and [Oo]utcomes', re.I),
    re.compile(r'for planning learning', re.I),
    re.compile(r'^\s*and assessment\s*$', re.I),
    re.compile(r'Benchmarks to support', re.I),
    re.compile(r'professional judgement', re.I),
    re.compile(r'^\s*Benchmarks?\s*[-–—]', re.I),
    re.compile(r'\b(Early|First|Second|Third|Fourth)\s+Level\b.*Benchmarks', re.I),
    re.compile(r'Benchmarks.*\b(Early|First|Second|Third|Fourth)\s+Level\b', re.I),
    re.compile(r'^\s*(Early|First|Second|Third|Fourth)\s+Level\s+[A-Z]', re.I),
    re.compile(r'Organisers?\s+teaching\s+and\s+assessment', re.I),
    re.compile(r'judgement\s+of\s+achievement\s+of\s+a\s+level', re.I),
]


def is_header_line(line) -> bool:
    text = " ".join(w["text"] for w in line)
    return any(p.search(text) for p in HEADER_PATTERNS)


def page_has_bullets(lines) -> bool:
    for line in lines:
        for w in line:
            if any(b in w["text"] for b in BULLET_CHARS):
                return True
    return False


def page_is_content(lines) -> bool:
    """A real benchmarks-table page, as opposed to narrative front matter
    (which sometimes uses its own bullet lists, so bullets alone aren't a
    reliable signal - front matter never contains an EO code)."""
    if not page_has_bullets(lines):
        return False
    text = " ".join(w["text"] for line in lines for w in line)
    return bool(EO_CODE_RE.search(text))


def line_gap_candidates(line):
    gaps = []
    for a, b in zip(line, line[1:]):
        if any(bc in a["text"] for bc in BULLET_CHARS):
            continue  # the space after a bullet marker isn't a column gutter
        gap = b["x0"] - a["x1"]
        if gap > GAP_THRESHOLD:
            gaps.append((a["x1"] + b["x0"]) / 2)
    return gaps


def cluster(points, tol):
    if not points:
        return []
    points = sorted(points)
    clusters = [[points[0]]]
    for p in points[1:]:
        if p - clusters[-1][-1] <= tol:
            clusters[-1].append(p)
        else:
            clusters.append([p])
    return clusters


def compute_gutters(all_pages_lines, min_fraction=0.06):
    # total_lines is the threshold's denominator - it must count every
    # content line, header lines included, so that stripping headers (which
    # only affects which lines contribute *candidates*, to avoid header text
    # polluting a column) doesn't itself shift the 6%-of-lines bar and flip
    # a borderline noise cluster in or out.
    total_lines = 0
    candidates = []
    for lines in all_pages_lines:
        if not page_is_content(lines):
            continue
        for line in lines:
            if len(line) < 2:
                continue
            total_lines += 1
            if is_header_line(line):
                continue
            candidates.extend(line_gap_candidates(line))
    if not candidates or total_lines == 0:
        return []
    clusters = cluster(candidates, CLUSTER_TOL)
    min_count = max(3, int(min_fraction * total_lines))
    strong = [c for c in clusters if len(c) >= min_count]
    return sorted(sum(c) / len(c) for c in strong)


def compute_gutters_x0(all_pages_lines, min_count=3, tol=6.0):
    """Fallback for short documents: an organiser cell spanning several EO
    records only shows its gap a handful of times, too few for the doc-wide
    ratio in compute_gutters() to trust - but a column's left edge (the
    following word's exact x0) repeats at almost the same point every time
    it occurs, however rarely, so a small absolute count is enough here.
    """
    candidates = []
    for lines in all_pages_lines:
        if not page_is_content(lines):
            continue
        for line in lines:
            if len(line) < 2 or is_header_line(line):
                continue
            for a, b in zip(line, line[1:]):
                if any(bc in a["text"] for bc in BULLET_CHARS):
                    continue
                if b["x0"] - a["x1"] > GAP_THRESHOLD:
                    candidates.append(b["x0"])
    clusters = cluster(candidates, tol)
    strong = [c for c in clusters if len(c) >= min_count]
    return sorted(sum(c) / len(c) for c in strong)


def assign_columns(line, gutters):
    """Bucket a line's words into columns using the shared gutters, joining
    each column's words into a single text string."""
    ncols = len(gutters) + 1
    buckets = [[] for _ in range(ncols)]
    for w in line:
        ci = 0
        while ci < len(gutters) and w["x0"] >= gutters[ci]:
            ci += 1
        buckets[ci].append(w["text"])
    return [" ".join(b) for b in buckets]


def choose_column_roles(rows_per_page, ncols):
    """Decide, once for the whole document, which columns make up each of
    the three logical fields. A genuine gutter can still get spuriously
    over-split (e.g. by justification stretching inside one cell), so each
    role is a *range* of raw columns, merging any such split back together:
    everything left of the EO code column is "organiser", everything from
    the bullet column rightward is "benchmarks", and whatever remains in
    between is "eo".
    """
    code_hits = [0] * ncols
    bullet_hits = [0] * ncols
    for rows in rows_per_page:
        for row in rows:
            for ci, seg in enumerate(row):
                if EO_CODE_RE.search(seg):
                    code_hits[ci] += 1
                if any(b in seg for b in BULLET_CHARS):
                    bullet_hits[ci] += 1
    if sum(code_hits) == 0:
        return None
    benchmark_col = max(range(ncols), key=lambda c: bullet_hits[c])
    eo_candidates = [c for c in range(ncols) if c < benchmark_col]
    if not eo_candidates:
        return None
    eo_col = max(eo_candidates, key=lambda c: code_hits[c])
    if code_hits[eo_col] == 0:
        return None

    # Some documents (e.g. Technologies) print an extra "strand" label
    # sideways to the left of the real organiser column. pdfplumber can't
    # reconstruct rotated text order, so that column comes out as short
    # scrambled fragments - detect and drop it rather than let it corrupt
    # the real organiser text it gets merged with.
    organiser_cols = []
    for c in range(0, eo_col):
        lengths = [len(row[c].strip()) for rows in rows_per_page for row in rows if row[c].strip()]
        if not lengths:
            continue
        lengths.sort()
        median = lengths[len(lengths) // 2]
        if median > 5:
            organiser_cols.append(c)

    return {
        "organiser": organiser_cols,
        "eo": list(range(eo_col, benchmark_col)),
        "benchmark": list(range(benchmark_col, ncols)),
    }


def split_benchmarks(text):
    parts = re.split("|".join(re.escape(b) for b in BULLET_CHARS), text)
    return [clean_text(p) for p in parts if clean_text(p)]


def join_cols_for_lines(rows, cols, start, end):
    """Rejoin a (possibly multi-column) field's text, left-to-right per
    line then top-to-bottom, for rows[start..end] inclusive."""
    per_line = []
    for i in range(start, end + 1):
        seg = " ".join(rows[i][c] for c in cols if rows[i][c].strip())
        if seg.strip():
            per_line.append(seg)
    return " ".join(per_line)


def parse_page_rows(rows, roles):
    if not rows:
        return []
    eo_cols = roles["eo"]
    organiser_cols = roles["organiser"]
    benchmark_cols = roles["benchmark"]
    if not eo_cols:
        return []

    code_line_idxs = []
    for i, row in enumerate(rows):
        seg = " ".join(row[c] for c in eo_cols)
        m = EO_CODE_RE.search(seg)
        if not m:
            continue
        remainder = EO_CODE_RE.sub("", seg)
        if len(remainder.split()) <= 4:
            code_line_idxs.append(i)
    if not code_line_idxs:
        return []

    blocks = []
    prev_end = 0
    for idx in code_line_idxs:
        blocks.append((prev_end, idx))
        prev_end = idx + 1

    records = []
    last_organiser = ""
    for start, end in blocks:
        eo_raw = join_cols_for_lines(rows, eo_cols, start, end)
        codes = EO_CODE_RE.findall(eo_raw)
        code_strs = ["{} {}-{}{}".format(*c) for c in codes]
        eo_text = clean_text(EO_CODE_RE.sub("", eo_raw))

        bench_raw = join_cols_for_lines(rows, benchmark_cols, start, end)
        benchmarks = split_benchmarks(bench_raw)

        organiser_text = ""
        if organiser_cols:
            organiser_text = clean_text(join_cols_for_lines(rows, organiser_cols, start, end))
            if organiser_text:
                last_organiser = organiser_text
            else:
                organiser_text = last_organiser

        if not code_strs or not eo_text:
            continue
        records.append({
            "codes": code_strs,
            "eo_text": eo_text,
            "organiser": organiser_text,
            "benchmarks": benchmarks,
        })
    return records


def garble_score(text):
    """Higher = more likely mangled by a bad column split (lots of stray
    1-2 letter fragments, as happens when a rotated side-label bleeds in)."""
    words = text.split()
    if not words:
        return 1.0
    short = sum(1 for w in words if len(w) <= 2)
    return short / len(words)


def dedupe_records(records):
    """Some EO blocks get captured twice - typically when they straddle a
    page break, once truncated/garbled and once cleanly. Only collapse
    records that share the same EO code(s) AND whose benchmark text is
    substantially overlapping (not just coincidentally sharing one generic
    bullet) - a single EO code legitimately repeats across several distinct
    organiser rows in some documents (e.g. PE), and those must stay separate.
    """
    groups = {}
    order = []
    for r in records:
        key = tuple(r["codes"])
        if key not in groups:
            groups[key] = []
            order.append(key)
        groups[key].append(r)

    out = []
    for key in order:
        group = groups[key]
        kept = []
        for r in group:
            merged = False
            r_text = " ".join(r["benchmarks"])
            for k in kept:
                k_text = " ".join(k["benchmarks"])
                ratio = difflib.SequenceMatcher(None, r_text, k_text).ratio()
                if ratio > 0.6:
                    # Likely the same block captured twice - a clean column
                    # split for one benchmark text usually came from a clean
                    # split of that same block's organiser text too, so use
                    # organiser garble as the tie-break rather than length.
                    if garble_score(r["organiser"]) < garble_score(k["organiser"]):
                        kept[kept.index(k)] = r
                    merged = True
                    break
            if not merged:
                kept.append(r)
        out.extend(kept)
    return out


def parse_pdf(pdf_path: Path):
    with pdfplumber.open(pdf_path) as pdf:
        all_lines = [extract_lines(page) for page in pdf.pages]

    # Column-boundary detection (compute_gutters*) needs the *unfiltered*
    # lines - its noise threshold is a fraction of total content lines, and
    # stripping headers first would shift that denominator and could flip a
    # borderline cluster. Actually building records must not see header
    # text at all, so that pass uses header-stripped lines.
    content_lines = [[l for l in lines if not is_header_line(l)] for lines in all_lines]

    # The 6%-of-lines threshold works for every document in this corpus
    # except very short ones (few enough lines that noise clusters pass it
    # too, or too few that the real organiser/EO boundary never reaches the
    # threshold at all). Only for those does it produce zero records, so
    # retry stricter thresholds and finally the x0-based fallback solely in
    # that case - documents that already work never take this path.
    gutter_attempts = [
        lambda: compute_gutters(all_lines, 0.06),
        lambda: compute_gutters(all_lines, 0.10),
        lambda: compute_gutters(all_lines, 0.15),
        lambda: compute_gutters(all_lines, 0.20),
        lambda: compute_gutters_x0(all_lines),
    ]
    for get_gutters in gutter_attempts:
        gutters = get_gutters()
        if not gutters:
            continue
        ncols = len(gutters) + 1

        rows_per_page = []
        for lines in content_lines:
            if not page_is_content(lines):
                rows_per_page.append([])
                continue
            rows_per_page.append([assign_columns(line, gutters) for line in lines])

        roles = choose_column_roles(rows_per_page, ncols)
        if roles is None:
            continue

        all_records = []
        for rows in rows_per_page:
            all_records.extend(parse_page_rows(rows, roles))
        all_records = dedupe_records(all_records)
        if all_records:
            return all_records
    return []


def main():
    manifest_path = Path(sys.argv[1])
    out_dir = Path(sys.argv[2])
    out_dir.mkdir(parents=True, exist_ok=True)
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))

    report_lines = []
    for entry in manifest:
        pdf_path = Path(entry["file"])
        slug = entry["slug"]
        manual_path = Path(__file__).parent / "raw" / f"{slug}.manual.json"
        out_path = out_dir / f"{slug}.json"
        if manual_path.exists():
            # Hand-transcribed by a human because auto-extraction couldn't
            # separate the columns cleanly - never overwrite this with a
            # re-parse of the PDF.
            records = json.loads(manual_path.read_text(encoding="utf-8"))
            out_path.write_text(json.dumps(records, indent=2, ensure_ascii=False), encoding="utf-8")
            n_bench = sum(len(r["benchmarks"]) for r in records)
            line = f"{slug:24s} eo_records={len(records):4d} benchmarks={n_bench:4d}  (manual: {manual_path.name})"
            report_lines.append(line)
            print(line)
            continue
        records = parse_pdf(pdf_path)
        out_path.write_text(json.dumps(records, indent=2, ensure_ascii=False), encoding="utf-8")
        n_bench = sum(len(r["benchmarks"]) for r in records)
        line = f"{slug:24s} eo_records={len(records):4d} benchmarks={n_bench:4d}  ({pdf_path.name})"
        report_lines.append(line)
        print(line)

    (out_dir / "_report.txt").write_text("\n".join(report_lines), encoding="utf-8")


if __name__ == "__main__":
    main()
