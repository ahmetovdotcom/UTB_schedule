"""Импорт дисциплин из рабочих учебных планов (РУП) Platonus в БД.

Каждый файл выгрузки — РУП одной образовательной программы: таблица «План
учебного процесса» с колонками «Код дисциплины», «Наименование дисциплины»,
«Академический период», часы (лекции / лабораторные / практические / ...).
Скрипт читает все файлы, берёт каждую дисциплину один раз и добавляет в
таблицу subjects те, которых там ещё нет.

Запуск (из папки backend):
    python import_subjects.py ~/Downloads/rup/              # все .xls/.xlsx в папке
    python import_subjects.py report.xls report2.xls        # или список файлов
    python import_subjects.py ~/Downloads/rup/ --dry-run    # только показать
    python import_subjects.py ~/Downloads/rup/ --export rup.csv
        # + сохранить все строки РУП (программа, группы, семестр, часы) —
        #   пригодится, чтобы потом создать карточки нагрузки автоматически

Для .xls нужен пакет xlrd, для .xlsx — openpyxl:
    pip install xlrd openpyxl

Правила:
  - одна дисциплина = одно название; сравнение без учёта регистра, лишних
    пробелов, «ё/е» и вида кавычек/тире («Иностранный язык» в 1 и 2 семестре
    и в разных программах — одна запись). Сохраняется первое написание;
  - строки без аудиторных часов (производственная/преддипломная практика,
    итоговая аттестация) не импортируются — пар по ним в расписании нет;
  - РУП на казахском и русском одной программы содержат одни и те же дисциплины
    с одинаковыми кодами: по умолчанию (--lang ru) они склеиваются и берётся
    русское название; дисциплина, которая есть только на казахском, остаётся
    с казахским названием. --lang kz — наоборот, --lang all — оба варианта;
  - уже существующие в БД предметы с тем же названием не дублируются;
  - ничего не удаляет.
"""
from __future__ import annotations

import argparse
import asyncio
import csv
import re
import sys
from dataclasses import asdict, dataclass, field
from pathlib import Path

HEADER_NAME = ("наименование дисциплины", "пәннің атауы", "name of discipline", "discipline name")
HEADER_CODE = ("код дисциплины", "пәннің коды", "discipline code", "code of discipline")
HEADER_PERIOD = (
    "академический период", "академиялық кезең", "академиялық мерзім", "академическом период",
    "academic period",
)
# Sub-header texts under «Количество часов / Сағаттар саны» (ru / kz / en).
HOUR_COLUMNS = {
    "lectures": ("лекци", "дәріс", "lecture"),
    "labs": ("лаборатор", "зертхана", "laborator"),
    "practicals": ("практическ", "практикалық", "тәжірибелік", "семинар", "practical"),
    "studio": ("студий", "студиялық", "studio"),
}
# «Практика» (производственная) — отдельная колонка, не аудиторные часы; только точное совпадение,
# чтобы не спутать с «Практические» / «Практикалық».
PRACTICE_HEADERS = ("практика", "practice", "internship")
STOP_WORDS = (
    "средняя недельная нагрузка", "орташа апталық", "итого", "барлығы", "оқу жоспары бойынша", "total",
)
KAZAKH_LETTERS = set("әғқңөұүһіӘҒҚҢӨҰҮҺІ")


@dataclass
class RupRow:
    file: str
    program: str
    groups: str
    code: str
    name: str
    period: str
    credits: str
    lectures: float = 0
    labs: float = 0
    practicals: float = 0
    studio: float = 0
    practice: float = 0

    @property
    def classroom_hours(self) -> float:
        return self.lectures + self.labs + self.practicals + self.studio


@dataclass
class Plan:
    rows: list[RupRow] = field(default_factory=list)
    problems: list[str] = field(default_factory=list)


# --- reading -------------------------------------------------------------------


def read_sheet_rows(path: Path) -> list[list[str]]:
    """First sheet as a list of rows of strings ('' for empty cells)."""
    suffix = path.suffix.lower()
    if suffix == ".xls":
        try:
            import xlrd
        except ImportError:
            sys.exit("Для .xls нужен пакет xlrd: pip install xlrd  (или сохраните файл как .xlsx)")
        sheet = xlrd.open_workbook(str(path)).sheet_by_index(0)
        return [[_cell_text(v) for v in sheet.row_values(r)] for r in range(sheet.nrows)]
    if suffix in (".xlsx", ".xlsm"):
        try:
            import openpyxl
        except ImportError:
            sys.exit("Для .xlsx нужен пакет openpyxl: pip install openpyxl")
        workbook = openpyxl.load_workbook(str(path), read_only=True, data_only=True)
        sheet = workbook.worksheets[0]
        return [[_cell_text(v) for v in row] for row in sheet.iter_rows(values_only=True)]
    raise ValueError(f"{path.name}: поддерживаются только .xls и .xlsx")


def _cell_text(value) -> str:
    if value is None:
        return ""
    if isinstance(value, float) and value.is_integer():
        value = int(value)
    return re.sub(r"\s+", " ", str(value)).strip()


def _to_hours(text: str) -> float:
    match = re.match(r"^\d+(?:[.,]\d+)?$", text.strip())
    return float(match.group().replace(",", ".")) if match else 0.0


def _find_col(row: list[str], keys: tuple[str, ...]) -> int | None:
    for index, cell in enumerate(row):
        lowered = cell.lower()
        if any(key in lowered for key in keys):
            return index
    return None


def _after_colon(text: str) -> str:
    return text.split(":", 1)[1].strip() if ":" in text else text.strip()


def parse_rup(path: Path) -> Plan:
    plan = Plan()
    rows = read_sheet_rows(path)

    program_ru = program_kz = ""
    groups: list[str] = []
    header_index = None
    for index, row in enumerate(rows):
        joined = " ".join(c for c in row if c)
        lowered = joined.lower()
        # Both RUP languages carry the program line; prefer the Russian one (the Kazakh
        # export has both, and the Kazakh line comes first).
        if not program_ru and "наименование образовательной программы" in lowered:
            program_ru = _after_colon(joined)
        if not program_kz and "білім беру бағдарламасының коды" in lowered:
            program_kz = _after_colon(joined)
        for cell_text in row:
            match = re.search(r"группа\s*/\s*group\s*(.*)$", cell_text, flags=re.I)
            if match:
                groups.append(match.group(1))
        if _find_col(row, HEADER_NAME) is not None and _find_col(row, HEADER_CODE) is not None:
            header_index = index
            break

    program = program_ru or program_kz
    if header_index is None:
        plan.problems.append(f"{path.name}: не найдена таблица с колонками «Код/Наименование дисциплины»")
        return plan

    header = rows[header_index]
    name_col = _find_col(header, HEADER_NAME)
    code_col = _find_col(header, HEADER_CODE)
    period_col = _find_col(header, HEADER_PERIOD)
    credits_col = _find_col(header, ("кредит", "credit"))

    # Hour columns sit in the sub-header rows right below the main header.
    hour_cols: dict[str, int] = {}
    for sub in rows[header_index + 1 : header_index + 4]:
        for i, text in enumerate(sub):
            lowered = text.lower().strip()
            if not lowered or i in hour_cols.values():
                continue
            if lowered in PRACTICE_HEADERS and "practice" not in hour_cols:
                hour_cols["practice"] = i
                continue
            for key, words in HOUR_COLUMNS.items():
                if key not in hour_cols and any(lowered.startswith(w) for w in words):
                    hour_cols[key] = i
                    break
    missing = [k for k in HOUR_COLUMNS if k not in hour_cols]
    if missing:
        plan.problems.append(
            f"{path.name}: не найдены колонки часов {missing} — такие строки могут считаться "
            "«без аудиторных часов»"
        )

    def cell(row: list[str], col: int | None) -> str:
        return row[col] if col is not None and col < len(row) else ""

    group_codes = []
    for part in groups:
        group_codes.extend(g for g in re.split(r"[,;\s]+", part.strip(" _")) if g)
    group_text = ", ".join(dict.fromkeys(group_codes))

    for row in rows[header_index + 1 :]:
        # Totals rows end the table. Only after the first subject: the sub-header right below
        # the main header starts with «Всего» / «Барлығы» too.
        first = next((c for c in row if c), "").lower()
        if plan.rows and any(first.startswith(w) for w in STOP_WORDS):
            break
        name = cell(row, name_col)
        code = cell(row, code_col)
        if not name or not code or any(k in name.lower() for k in HEADER_NAME):
            continue
        rup_row = RupRow(
            file=path.name,
            program=program,
            groups=group_text,
            code=code,
            name=name,
            period=cell(row, period_col),
            credits=cell(row, credits_col),
        )
        for key, col in hour_cols.items():
            setattr(rup_row, key, _to_hours(cell(row, col)))
        plan.rows.append(rup_row)

    if not plan.rows:
        plan.problems.append(f"{path.name}: таблица найдена, но дисциплин в ней нет")
    return plan


# --- deduplication ---------------------------------------------------------------


def subject_key(name: str) -> str:
    text = name.lower().replace("ё", "е")
    text = re.sub(r"[«»“”„\"']", "", text)
    text = re.sub(r"[‐‑‒–—−]", "-", text)
    text = re.sub(r"\s*-\s*", "-", text)
    text = re.sub(r"\s+", " ", text)
    return text.strip(" .;,")


def is_kazakh(text: str) -> bool:
    return any(ch in KAZAKH_LETTERS for ch in text)


def base_code(code: str) -> str:
    """'IYa 1104-26 (2)' -> 'IYA1104-26' — same discipline in another semester."""
    code = re.sub(r"\([^)]*\)\s*$", "", code)
    return re.sub(r"\s+", "", code).upper()


def program_code(program: str) -> str:
    match = re.search(r"\b\d[A-ZА-ЯB]\d{5}\b", program.upper())
    return match.group() if match else program


def unique_subjects(rows: list[RupRow], lang: str = "ru") -> tuple[list[str], list[RupRow]]:
    """(unique classroom subject names, skipped non-classroom rows).

    The Kazakh and the Russian РУП of one programme list the same disciplines
    under the same codes. With lang="ru" (or "kz") the rows of one programme
    that share a code are merged and the Russian (Kazakh) name is kept when the
    other language also exists; a discipline found in only one language keeps
    that name. lang="all" keeps both names.
    """
    skipped = [r for r in rows if r.classroom_hours <= 0]
    classroom = [r for r in rows if r.classroom_hours > 0]

    chosen: list[str] = []
    if lang == "all":
        chosen = [r.name.strip() for r in classroom]
    else:
        by_code: dict[tuple[str, str], list[RupRow]] = {}
        for row in classroom:
            by_code.setdefault((program_code(row.program), base_code(row.code)), []).append(row)
        want_kazakh = lang == "kz"
        for group in by_code.values():
            preferred = [r for r in group if is_kazakh(r.name) == want_kazakh]
            chosen.append((preferred or group)[0].name.strip())

    seen: dict[str, str] = {}
    for name in chosen:
        seen.setdefault(subject_key(name), name)
    return list(seen.values()), skipped


def collect_files(inputs: list[str]) -> list[Path]:
    files: list[Path] = []
    for item in inputs:
        path = Path(item).expanduser()
        if path.is_dir():
            files.extend(sorted(p for p in path.rglob("*") if p.suffix.lower() in (".xls", ".xlsx")))
        elif path.exists():
            files.append(path)
        else:
            sys.exit(f"Нет такого файла или папки: {item}")
    # skip Excel lock files like "~$report.xlsx"
    return [f for f in files if not f.name.startswith("~$")]


# --- database --------------------------------------------------------------------


async def apply(names: list[str], dry_run: bool) -> None:
    from sqlalchemy import select

    from database.models import Subject, async_session, init_db

    await init_db()
    async with async_session() as session:
        existing = {subject_key(s.name) for s in (await session.execute(select(Subject))).scalars()}
        new = [n for n in names if subject_key(n) not in existing]
        session.add_all(Subject(name=n) for n in new)
        if dry_run:
            await session.rollback()
        else:
            await session.commit()
    prefix = "[dry-run] было бы: " if dry_run else ""
    print(f"{prefix}предметов добавлено {len(new)}, уже были в БД {len(names) - len(new)}")


def main() -> None:
    parser = argparse.ArgumentParser(description="Импорт дисциплин из РУП Platonus")
    parser.add_argument("inputs", nargs="+", help="файлы .xls/.xlsx или папки с ними")
    parser.add_argument("--dry-run", action="store_true", help="ничего не записывать в БД")
    parser.add_argument("--export", metavar="CSV", help="сохранить все строки РУП в CSV")
    parser.add_argument(
        "--lang",
        choices=("ru", "kz", "all"),
        default="ru",
        help="если есть РУП на обоих языках: ru — брать русские названия (по умолчанию), "
        "kz — казахские, all — импортировать оба варианта",
    )
    args = parser.parse_args()

    files = collect_files(args.inputs)
    if not files:
        sys.exit("Не найдено ни одного .xls/.xlsx")

    all_rows: list[RupRow] = []
    problems: list[str] = []
    for path in files:
        plan = parse_rup(path)
        all_rows.extend(plan.rows)
        problems.extend(plan.problems)

    names, skipped = unique_subjects(all_rows, lang=args.lang)
    print(f"Файлов: {len(files)}, строк дисциплин: {len(all_rows)}, уникальных предметов: {len(names)}")
    if skipped:
        skipped_names = sorted({r.name for r in skipped})
        print(f"Без аудиторных часов, не импортируются ({len(skipped_names)}): " + "; ".join(skipped_names))
    for problem in problems:
        print("!", problem)

    if args.export:
        with open(args.export, "w", newline="", encoding="utf-8-sig") as fh:
            fieldnames = list(asdict(all_rows[0]).keys()) if all_rows else ["file"]
            writer = csv.DictWriter(fh, fieldnames=fieldnames, delimiter=";")
            writer.writeheader()
            writer.writerows(asdict(r) for r in all_rows)
        print(f"Строки РУП сохранены в {args.export}")

    if names:
        asyncio.run(apply(names, dry_run=args.dry_run))


if __name__ == "__main__":
    main()
