from __future__ import annotations

import argparse
import dataclasses
import re
import sys
from collections import Counter
from pathlib import Path
from typing import Iterable


@dataclasses.dataclass(frozen=True)
class QuestionSpec:
    key: str
    title: str
    title_patterns: tuple[re.Pattern[str], ...]
    allow_multiple: bool = False


ANALYSIS_MARKERS = ("企業分析", "기업분석")
ANSWER_MARKERS = ("面接回答集", "면접답변집", "면접 답변집", "面接 答변집", "面接答변집")

QUESTION_SPECS: tuple[QuestionSpec, ...] = (
    QuestionSpec(
        key="지원동기",
        title="지원동기 (志望動機)",
        title_patterns=(
            re.compile(r"志望動機"),
            re.compile(r"지원\s*동기"),
        ),
    ),
    QuestionSpec(
        key="취활의축",
        title="취활의 축 (就活の軸)",
        title_patterns=(
            re.compile(r"就活の軸"),
            re.compile(r"취활"),
        ),
    ),
    QuestionSpec(
        key="커리어_5_10",
        title="5년·10년 후 커리어 계획 (キャリアパス)",
        title_patterns=(
            re.compile(r"キャリア"),
            re.compile(r"커리어"),
            re.compile(r"5\s*年|10\s*年|5\s*년|10\s*년"),
        ),
    ),
    QuestionSpec(
        key="입사후기여",
        title="입사 후 어떻게 기여하고 싶은가 (入社後の貢献)",
        title_patterns=(
            re.compile(r"入社後の貢献"),
            re.compile(r"입사\s*후"),
            re.compile(r"기여"),
        ),
    ),
    QuestionSpec(
        key="1지망",
        title="우리 회사가 1지망인가? (第一志望か)",
        title_patterns=(
            re.compile(r"第一志望"),
            re.compile(r"第\s*1\s*志望"),
            re.compile(r"第何志望"),
            re.compile(r"志望順位"),
            re.compile(r"1\s*지망"),
            re.compile(r"제\s*1\s*지망"),
        ),
    ),
    QuestionSpec(
        key="왜당사",
        title="왜 동종 타사가 아닌 당사인가 (なぜ当社 / 同業他社ではなく)",
        title_patterns=(
            re.compile(r"なぜ当社"),
            re.compile(r"同業他社ではなく"),
            re.compile(r"동종\s*타사"),
            re.compile(r"왜.*당사"),
        ),
        allow_multiple=True,
    ),
    QuestionSpec(
        key="강점약점",
        title="당사의 강점과 약점 (強みと弱み)",
        title_patterns=(
            re.compile(r"強み"),
            re.compile(r"弱み"),
            re.compile(r"강점"),
            re.compile(r"약점"),
        ),
        allow_multiple=True,
    ),
    QuestionSpec(
        key="역질문",
        title="역질문 (逆質問)",
        title_patterns=(
            re.compile(r"逆質問"),
            re.compile(r"역\s*질문"),
        ),
    ),
)


def read_text(path: Path) -> str:
    return path.read_text(encoding="utf-8", errors="replace")


def write_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="\n") as f:
        f.write(text)


def is_analysis_name(name: str) -> bool:
    return any(marker in name for marker in ANALYSIS_MARKERS)


def is_answer_name(name: str) -> bool:
    return any(marker in name for marker in ANSWER_MARKERS) or "面接" in name or "면접" in name


@dataclasses.dataclass(frozen=True)
class FolderDocs:
    folder: Path
    md_files: tuple[Path, ...]
    analysis_files: tuple[Path, ...]
    answer_files: tuple[Path, ...]
    integrated_files: tuple[Path, ...]
    other_files: tuple[Path, ...]


def classify_folder(folder: Path) -> FolderDocs:
    md_files = tuple(sorted(folder.glob("*.md"), key=lambda p: p.name))
    analysis_files: list[Path] = []
    answer_files: list[Path] = []
    integrated_files: list[Path] = []
    other_files: list[Path] = []

    for path in md_files:
        name = path.name
        if name == "기업분석_진행현황.md":
            other_files.append(path)
            continue

        analysis = is_analysis_name(name)
        answer = is_answer_name(name)
        if analysis and answer:
            integrated_files.append(path)
        elif analysis:
            analysis_files.append(path)
        elif answer:
            answer_files.append(path)
        else:
            other_files.append(path)

    return FolderDocs(
        folder=folder,
        md_files=md_files,
        analysis_files=tuple(analysis_files),
        answer_files=tuple(answer_files),
        integrated_files=tuple(integrated_files),
        other_files=tuple(other_files),
    )


HEADING_RE = re.compile(r"^(#{2,3})\s+(.*\S)\s*$")


@dataclasses.dataclass(frozen=True)
class Section:
    heading_line: str
    heading_level: int
    title: str
    body: str


def split_analysis_and_answer(full_text: str) -> tuple[str, str]:
    analysis_markers = set(ANALYSIS_MARKERS)
    answer_markers = {"回答集", "답변집"}

    # 1) Prefer an explicit "answer-only" heading (contains 답변집/回答集 but not 기업분석/企業分析).
    offset = 0
    for line in full_text.splitlines(keepends=True):
        if re.match(r"^#{1,2}\s+", line):
            if any(m in line for m in answer_markers) and not any(m in line for m in analysis_markers):
                start_idx = offset
                return full_text[:start_idx].rstrip() + "\n", full_text[start_idx:].lstrip()
        offset += len(line)

    # 2) Fallback: split at the first interview-question heading (지원동기/志望動機).
    offset = 0
    for line in full_text.splitlines(keepends=True):
        if HEADING_RE.match(line):
            if QUESTION_SPECS[0].title_patterns[0].search(line) or QUESTION_SPECS[0].title_patterns[1].search(line):
                start_idx = offset
                return full_text[:start_idx].rstrip() + "\n", full_text[start_idx:].lstrip()
        offset += len(line)

    return full_text, ""


def extract_sections(text: str) -> tuple[Section, ...]:
    lines = text.splitlines(keepends=True)
    headings: list[tuple[int, int, str]] = []
    for idx, line in enumerate(lines):
        m = HEADING_RE.match(line)
        if not m:
            continue
        level = len(m.group(1))
        title = m.group(2).strip()
        headings.append((idx, level, title))

    sections: list[Section] = []
    for i, (start_line, level, title) in enumerate(headings):
        end_line = len(lines)
        for j in range(i + 1, len(headings)):
            next_line, next_level, _ = headings[j]
            if next_level <= level:
                end_line = next_line
                break
        heading_line = lines[start_line].rstrip("\r\n")
        body = "".join(lines[start_line + 1 : end_line]).rstrip()
        sections.append(
            Section(
                heading_line=heading_line,
                heading_level=level,
                title=title,
                body=body,
            )
        )
    return tuple(sections)


def find_question_sections(answer_text: str) -> dict[str, list[Section]]:
    sections = extract_sections(answer_text)
    by_key: dict[str, list[Section]] = {spec.key: [] for spec in QUESTION_SPECS}

    for section in sections:
        for spec in QUESTION_SPECS:
            if any(p.search(section.title) for p in spec.title_patterns):
                by_key[spec.key].append(section)
                if not spec.allow_multiple:
                    break

    return by_key


def normalize_answer(company_name: str, answer_text: str) -> tuple[str, dict[str, bool]]:
    missing: dict[str, bool] = {}

    # Preserve minimal metadata at the top (title + 작성일/기반자료 등) but drop extra sections.
    header_lines: list[str] = []
    for line in answer_text.splitlines():
        if line.startswith("## ") or line.startswith("### "):
            break
        header_lines.append(line)
    header = "\n".join(header_lines).strip()

    # Drop trailing horizontal rules to avoid duplicated separators.
    header_clean_lines = header.splitlines()
    while header_clean_lines and not header_clean_lines[-1].strip():
        header_clean_lines.pop()
    while header_clean_lines and header_clean_lines[-1].strip() == "---":
        header_clean_lines.pop()
        while header_clean_lines and not header_clean_lines[-1].strip():
            header_clean_lines.pop()
    header = "\n".join(header_clean_lines).strip()
    if not header:
        header = f"# {company_name} 면접답변집"
    elif not header.lstrip().startswith("#"):
        header = f"# {company_name} 면접답변집\n\n{header}"

    question_sections = find_question_sections(answer_text)

    blocks: list[str] = [header, "", "---", ""]
    for idx, spec in enumerate(QUESTION_SPECS, start=1):
        sections = question_sections.get(spec.key) or []
        if not sections:
            missing[spec.key] = True
            blocks.append(f"## Q{idx}. {spec.title}")
            blocks.append("")
            blocks.append("> TODO: 작성 필요 (한국어/日本語 버전 포함)")
            blocks.append("")
            continue

        missing[spec.key] = False
        blocks.append(f"## Q{idx}. {spec.title}")
        blocks.append("")

        if spec.allow_multiple:
            for s_idx, section in enumerate(sections, start=1):
                # Keep original heading as a subheading for traceability.
                blocks.append(f"### 참고 {s_idx}: {section.title}")
                blocks.append("")
                if section.body:
                    blocks.append(section.body)
                    blocks.append("")
        else:
            section = sections[0]
            if section.body:
                blocks.append(section.body)
                blocks.append("")

    normalized = "\n".join(blocks).rstrip() + "\n"
    return normalized, missing


def pick_best(paths: Iterable[Path]) -> Path | None:
    # Prefer larger files; if tie, prefer latest modified.
    candidates = list(paths)
    if not candidates:
        return None
    candidates.sort(key=lambda p: (p.stat().st_size, p.stat().st_mtime), reverse=True)
    return candidates[0]


def _safe_console(text: str) -> str:
    encoding = sys.stdout.encoding or "utf-8"
    return text.encode(encoding, errors="backslashreplace").decode(encoding, errors="replace")


def analyze(root: Path) -> int:
    corp_root = next((p for p in root.iterdir() if p.is_dir() and p.name.startswith("03_")), None)
    if not corp_root:
        raise SystemExit(f"03_* directory not found under: {root}")

    folders = sorted([p for p in corp_root.iterdir() if p.is_dir()], key=lambda p: p.name)
    missing_counter: Counter[str] = Counter()

    worst: list[tuple[int, Path, list[str]]] = []
    for folder in folders:
        docs = classify_folder(folder)

        answer_candidate = pick_best(docs.answer_files) or pick_best(docs.integrated_files)
        if not answer_candidate:
            continue

        text = read_text(answer_candidate)
        _, answer_part = split_analysis_and_answer(text) if answer_candidate in docs.integrated_files else ("", text)
        normalized, missing = normalize_answer(folder.name, answer_part)
        # Just compute missing; don't write.
        missing_keys = [spec.key for spec in QUESTION_SPECS if missing.get(spec.key)]
        for k in missing_keys:
            missing_counter[k] += 1
        worst.append((len(missing_keys), answer_candidate, missing_keys))

    worst.sort(key=lambda x: (-x[0], str(x[1])))
    print(f"Company folders: {len(folders)}")
    print(f"Answer candidates analyzed: {len(worst)}")
    print("\nWorst missing (top 15):")
    for n_missing, path, missing_keys in worst[:15]:
        if n_missing == 0:
            break
        rel = _safe_console(str(path.relative_to(corp_root)))
        print(f"- {n_missing} missing: {rel} :: {', '.join(missing_keys)}")

    print("\nMissing counts:")
    for spec in QUESTION_SPECS:
        print(f"- {spec.key}: {missing_counter.get(spec.key, 0)}")

    return 0


def normalize_all(root: Path, *, dry_run: bool) -> int:
    corp_root = next((p for p in root.iterdir() if p.is_dir() and p.name.startswith("03_")), None)
    if not corp_root:
        raise SystemExit(f"03_* directory not found under: {root}")

    changed_answer_files: list[Path] = []
    created_integrated_files: list[Path] = []

    folders = sorted([p for p in corp_root.iterdir() if p.is_dir()], key=lambda p: p.name)
    for folder in folders:
        docs = classify_folder(folder)

        analysis_src = pick_best(docs.analysis_files)
        answer_src = pick_best(docs.answer_files)
        integrated_src = pick_best(docs.integrated_files)

        analysis_text = ""
        answer_text = ""

        if analysis_src:
            analysis_text = read_text(analysis_src).rstrip() + "\n"
        if answer_src:
            answer_text = read_text(answer_src)
        elif integrated_src:
            full = read_text(integrated_src)
            analysis_part, answer_part = split_analysis_and_answer(full)
            analysis_text = analysis_text or analysis_part
            answer_text = answer_part

        if not answer_text and not analysis_text:
            continue

        normalized_answer, _missing = normalize_answer(folder.name, answer_text)

        # Normalize separate answer file in-place (if it exists).
        if answer_src:
            if not dry_run:
                write_text(answer_src, normalized_answer)
            changed_answer_files.append(answer_src)

        # Always create/update canonical integrated file.
        canonical_integrated = folder / f"{folder.name}_企業分析_面接回答集.md"
        integrated_text_parts: list[str] = []
        if analysis_text:
            integrated_text_parts.append(analysis_text.rstrip())
        if integrated_text_parts:
            integrated_text_parts.append("\n---\n")
        integrated_text_parts.append(normalized_answer.rstrip())
        integrated_text = "\n".join(integrated_text_parts).rstrip() + "\n"

        if not dry_run:
            write_text(canonical_integrated, integrated_text)
        created_integrated_files.append(canonical_integrated)

    print(f"Normalized answer files: {len(changed_answer_files)}")
    print(f"Created/updated integrated files: {len(created_integrated_files)}")
    if dry_run:
        print("(dry-run) No files written.")
    return 0


def cleanup_archives(root: Path, *, dry_run: bool) -> int:
    corp_root = next((p for p in root.iterdir() if p.is_dir() and p.name.startswith("03_")), None)
    if not corp_root:
        raise SystemExit(f"03_* directory not found under: {root}")

    moved = 0
    folders = sorted([p for p in corp_root.iterdir() if p.is_dir()], key=lambda p: p.name)
    for folder in folders:
        canonical_integrated = folder / f"{folder.name}_企業分析_面接回答集.md"
        if not canonical_integrated.exists():
            continue

        docs = classify_folder(folder)
        candidates = list(docs.analysis_files) + list(docs.answer_files) + list(docs.integrated_files)
        candidates = [p for p in candidates if p.name != canonical_integrated.name]
        if not candidates:
            continue

        archive_dir = folder / "_archive"
        if not dry_run:
            archive_dir.mkdir(parents=True, exist_ok=True)

        for path in candidates:
            target = archive_dir / path.name
            if target.exists():
                # Avoid overwriting an existing archive file.
                stem = target.stem
                suffix = target.suffix
                i = 1
                while True:
                    alt = archive_dir / f"{stem}.{i}{suffix}"
                    if not alt.exists():
                        target = alt
                        break
                    i += 1
            if dry_run:
                moved += 1
                continue
            path.replace(target)
            moved += 1

    print(f"Archived redundant md files: {moved}")
    if dry_run:
        print("(dry-run) No files moved.")
    return 0


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="QA + normalize corp docs in Claude_workdir/03_*")
    parser.add_argument(
        "--root",
        type=Path,
        default=Path(r"C:\Users\PC\Downloads\Claude_workdir"),
        help="Claude_workdir root path",
    )
    sub = parser.add_subparsers(dest="cmd", required=True)

    sub.add_parser("analyze", help="Print missing-question stats (no writes)")

    p_norm = sub.add_parser("normalize", help="Normalize answer docs and create integrated docs")
    p_norm.add_argument("--dry-run", action="store_true", help="Compute changes only")

    p_clean = sub.add_parser("cleanup", help="Move redundant analysis/answer/integrated md files to _archive/")
    p_clean.add_argument("--dry-run", action="store_true", help="Compute moves only")

    return parser.parse_args()


def main() -> int:
    args = parse_args()
    root: Path = args.root

    if args.cmd == "analyze":
        return analyze(root)
    if args.cmd == "normalize":
        return normalize_all(root, dry_run=bool(args.dry_run))
    if args.cmd == "cleanup":
        return cleanup_archives(root, dry_run=bool(args.dry_run))
    raise SystemExit(f"Unknown command: {args.cmd}")


if __name__ == "__main__":
    raise SystemExit(main())
