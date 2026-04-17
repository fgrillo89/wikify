import io, re, sys
from collections import Counter
from pathlib import Path

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

bib = Path("data/corpora/ald_all_marker/corpus_papers.bib").read_text(encoding="utf-8")
entries = re.findall(r"@article\{([^,]+),((?:[^@]|(?<!\n)@)*?)\n\}", bib, re.DOTALL)

wd = len(re.findall(r"\btitle\s*=\s*\{Word Document\}", bib))
issn_j = len(re.findall(r"\bjournal\s*=\s*\{[^}]*ISSN[^}]*\}", bib))
bs_auth = len(re.findall(r"\bauthor\s*=\s*\{[^}]*\\\\", bib))
file_ext = len(re.findall(r"\bauthor\s*=\s*\{[^}]*\.(?:pdf|docx)", bib))
inst_pat = re.compile(
    r"\bauthor\s*=\s*\{[^}]*(?:Air Force|Research Laboratory|University|Institute of)[^}]*\}",
    re.IGNORECASE,
)
inst_auth = len(inst_pat.findall(bib))
dois = re.findall(r"doi\s*=\s*\{([^}]+)\}", bib)
dup_doi = sum(1 for d, c in Counter(dois).items() if c > 1)
missing_doi = sum(1 for _, body in entries if "doi =" not in body)

print("| metric | before | after |")
print("|---|---|---|")
print(f"| total entries | 208 | {len(entries)} |")
print(f"| Word Document titles | 30 | {wd} |")
print(f"| ISSN-as-journal | 4+ | {issn_j} |")
print(f"| authors with trailing backslash | 5+ | {bs_auth} |")
print(f"| authors containing .pdf/.docx | 1 | {file_ext} |")
print(f"| authors with institution fragment | many | {inst_auth} |")
print(f"| duplicate DOIs | 1 (Goul) | {dup_doi} |")
print(
    f"| missing DOI | 52/208 (25%) | "
    f"{missing_doi}/{len(entries)} ({100*missing_doi/len(entries):.1f}%) |",
)

print()
print("Known bad titles surviving:")
for pat, name in [
    (r"\btitle\s*=\s*\{Conflict of Interest\}", "Conflict of Interest"),
    (r"\btitle\s*=\s*\{[0-9]+\s+Introduction\}", "N Introduction"),
    (r"\btitle\s*=\s*\{Abstract\}", "Abstract"),
    (r"\btitle\s*=\s*\{University of", "University of..."),
    (r"\btitle\s*=\s*\{ELECTRONIC MATERIALS", "ELECTRONIC MATERIALS"),
    (r"\btitle\s*=\s*\{\[.*?\]\(", "[md link]("),
    (r"\btitle\s*=\s*\{\*\*ISSN", "**ISSN"),
    (r"\btitle\s*=\s*\{and\s", 'starts with "and "'),
]:
    n = len(re.findall(pat, bib))
    if n:
        print(f"  {name!r}: {n}")
