"""Импорт преподавателей по кафедрам из списка ППС (Excel) в БД.

Источник — сводный список ППС КазУТБ по кафедрам: колонки «п/н», «ФИО»,
«Должность», «Академическая/ученая степень». Каждая кафедра — блок:
штатные (обычно первая строка — заведующий кафедрой), затем «Совмещение»
и «Совместители» (подписаны не везде — тогда это просто следующие блоки
после пустых строк).

Названия кафедр в выгрузке есть не у всех блоков, поэтому кафедра блока
определяется так:
  1) по строке-заголовку «Кафедра "..."», если она есть;
  2) иначе — по заведующему кафедрой (HEADS ниже, сверено с kaztbu.edu.kz).
Если блок не удалось привязать к кафедре, скрипт останавливается и ничего
не пишет — добавьте заведующего в HEADS или заголовок в Excel.

Запуск (из папки backend):
    python import_teachers.py ~/Downloads/ППС.xlsx --dry-run   # только показать
    python import_teachers.py ~/Downloads/ППС.xlsx
    python import_teachers.py ~/Downloads/ППС.xlsx --export teachers.csv
        # + сохранить итоговый список (ФИО, кафедра, должность, степень) для сверки

Для .xlsx нужен пакет openpyxl, для .xls — xlrd:
    pip install openpyxl xlrd

Правила:
  - один человек = одна запись в teachers (ФИО сравниваются без учёта регистра,
    лишних пробелов и «ё/е»). Если человек есть в нескольких кафедрах, кафедра
    берётся оттуда, где он штатный; если он везде совместитель — первая;
  - не импортируются строки-заглушки: «Вакансия», «Председатель АК», «Члены АК»,
    «Зарубеж.ппс», «Тех.секретарь», «ГД», «ДИТ», пустые ФИО и итоги;
  - пометки после «/» в ФИО («/ув», «/почасов») отрезаются и выводятся в отчёт;
  - кафедры, которых ещё нет в БД, добавляются (как в import_rooms.py);
  - уже существующие преподаватели не дублируются; если у существующего не
    указана кафедра — проставляется, другая кафедра не перезаписывается
    (выводится предупреждение);
  - ничего не удаляет.
"""
from __future__ import annotations

import argparse
import asyncio
import csv
import difflib
import re
import sys
from dataclasses import asdict, dataclass
from pathlib import Path

# --- Кафедры (названия как в БД / import_rooms.py) ----------------------------
SGD = "Социально-гуманитарные дисциплины"
FIN = "Финансы и учет"
TLP = "Технология легкой промышленности и дизайна"
ECON = "Экономика и управление"
TOUR = "Туризм и сервис"
LANG = "Государственный и иностранные языки"
CEA = "Компьютерная инженерия и автоматизация"
CHEM = "Химия, химическая технология и экология"
TECH = "Технология и стандартизация"
IT = "Информационные технологии"
PE = "Физическое воспитание"

# Заведующий кафедрой -> кафедра (kaztbu.edu.kz/kafedry/..., сентябрь 2026).
# Нужен для блоков без заголовка «Кафедра "..."».
HEADS = {
    "Кожабергенова Каламкуль Доктырбековна": TLP,
    "Магауянов Даурен Абильтаевич": SGD,
    "Сатбаев Ерлан Кабдуллович": PE,
    "Канафиева Куланда Кабылсеитовна": LANG,
    "Нұртай Жадыра Тастенбекқызы": CHEM,
    "Акишев Каршыга Максутович": CEA,
    "Абдукаримова Алия Амировна": IT,
    "Жунусова Алия Анархановна": TOUR,
    "Абдильдинова Найля Ермухановна": ECON,
    "Бекбусинова Гульнафиз Кенжебековна": FIN,
}

# Исправления ФИО: как в Excel -> как записывать в БД. Сюда же — один человек,
# записанный в разных блоках по-разному (иначе будет две записи).
FIX_NAMES = {
    "Усипбаева Перизат Сагинбековна": "Усипбаева Перизат Сагынбековна",  # ГиИЯ и Туризм
    "Галым Избанович Кулекешов": "Кулекешов Галым Избанович",  # имя и фамилия переставлены
}

STAFF, COMBINED, EXTERNAL = "штатный", "совмещение", "совместитель"
KINDS = (STAFF, COMBINED, EXTERNAL)
LABELS = {"совмещение": COMBINED, "совместители": EXTERNAL, "совместитель": EXTERNAL}
HEAD_POSITION = "заведующий кафедрой"
PLACEHOLDER = re.compile(r"^(вакансия|председатель|члены ак|зарубеж|тех\.? ?секретарь|гд$|дит$)")


@dataclass
class TeacherRow:
    row: int
    full_name: str
    department: str
    kind: str
    position: str
    degree: str
    note: str = ""  # пометка после «/» в ФИО


def key(text: str) -> str:
    """Comparison key for names of people and departments."""
    text = text.lower().replace("ё", "е")
    text = re.sub(r"[\"'«»“”„]", "", text)
    return re.sub(r"\s+", " ", text).strip()


HEADS_BY_KEY = {key(name): dept for name, dept in HEADS.items()}
FIX_NAMES_BY_KEY = {key(k): v for k, v in FIX_NAMES.items()}


# --- Excel -----------------------------------------------------------------------


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


def _department_from_header(text: str) -> str | None:
    """'Кафедра " Технология и стандартизация"' -> 'Технология и стандартизация'."""
    match = re.match(r"^\s*кафедра\b\s*(.+)$", text, re.IGNORECASE)
    if not match:
        return None
    name = re.sub(r"[\"'«»“”„]", "", match.group(1))
    name = re.sub(r"\s+", " ", name).strip()
    return name or None


def parse_staff_list(path: Path) -> tuple[list[TeacherRow], list[str], list[str]]:
    """Returns (people rows, skipped placeholder rows, problems)."""
    rows: list[TeacherRow] = []
    skipped: list[str] = []
    problems: list[str] = []

    department: str | None = None
    fresh_header = False  # section was just opened by a «Кафедра "..."» header
    kind = STAFF
    had_data_in_block = False
    gap = False  # empty row(s) since the last data row

    for number, cells in enumerate(read_sheet_rows(path), start=1):
        cells = (cells + [""] * 4)[:4]
        _, name_cell, position, degree = cells
        if not any(cells):
            gap = gap or had_data_in_block
            continue

        header = next((d for d in (_department_from_header(c) for c in cells[:2]) if d), None)
        if header:
            department, fresh_header, kind = header, True, STAFF
            had_data_in_block, gap = False, False
            continue

        label = next((LABELS[key(c)] for c in cells[:2] if key(c) in LABELS), None)
        if label:
            kind, gap, had_data_in_block = label, False, False
            continue

        if key(position) == HEAD_POSITION:
            if not fresh_header:
                department = HEADS_BY_KEY.get(key(name_cell.split("/")[0]))
                if department is None:
                    problems.append(
                        f"строка {number}: заведующий «{name_cell}» — не знаю, какая это кафедра "
                        "(добавьте его в HEADS или строку «Кафедра \"...\"» над блоком)"
                    )
            kind, gap = STAFF, False
        elif gap:
            # next unlabeled block of the same department: штатные -> совмещение -> совместители
            kind = KINDS[min(KINDS.index(kind) + 1, len(KINDS) - 1)]
            gap = False
        fresh_header = False

        if not name_cell or re.fullmatch(r"[\d\s.,]+", name_cell):
            continue  # итоги («23», «387»), пустые строки нумерации, «По сост. на ...»
        had_data_in_block = True
        if department is None:
            problems.append(f"строка {number}: «{name_cell}» — выше нет ни кафедры, ни заведующего")
            continue

        full_name, _, note = name_cell.partition("/")
        full_name = re.sub(r"\s+", " ", full_name).strip()
        if PLACEHOLDER.match(key(full_name)):
            skipped.append(f"строка {number}: {name_cell} ({department})")
            continue
        full_name = FIX_NAMES_BY_KEY.get(key(full_name), full_name)
        rows.append(TeacherRow(number, full_name, department, kind, position, degree, note.strip()))

    return rows, skipped, problems


def unique_teachers(rows: list[TeacherRow]) -> dict[str, TeacherRow]:
    """One row per person: the department where they are штатный, else the first one."""
    chosen: dict[str, TeacherRow] = {}
    for row in rows:
        current = chosen.get(key(row.full_name))
        if current is None or (current.kind != STAFF and row.kind == STAFF):
            chosen[key(row.full_name)] = row
    return chosen


def report(rows: list[TeacherRow], chosen: dict[str, TeacherRow], skipped: list[str]) -> None:
    departments: dict[str, dict[str, int]] = {}
    for row in rows:
        departments.setdefault(row.department, {k: 0 for k in KINDS})[row.kind] += 1
    assigned: dict[str, int] = {}
    for row in chosen.values():
        assigned[row.department] = assigned.get(row.department, 0) + 1

    print(f"Строк с преподавателями: {len(rows)}, уникальных людей: {len(chosen)}")
    print(f"{'Кафедра':45} штат совм совмест  -> в БД")
    for dept, counts in departments.items():
        print(
            f"{dept:45} {counts[STAFF]:4} {counts[COMBINED]:4} {counts[EXTERNAL]:7}  -> {assigned.get(dept, 0)}"
        )

    by_person: dict[str, list[TeacherRow]] = {}
    for row in rows:
        by_person.setdefault(key(row.full_name), []).append(row)
    multi = {k: v for k, v in by_person.items() if len({r.department for r in v}) > 1}
    if multi:
        print(f"\nЕсть в нескольких кафедрах ({len(multi)}) — записан в кафедру, отмеченную *:")
        for k, occurrences in multi.items():
            main = chosen[k].department
            places = "; ".join(
                f"{'*' if r.department == main else ''}{r.department} ({r.kind})"
                for r in occurrences
            )
            print(f"  {chosen[k].full_name}: {places}")

    notes = [r for r in chosen.values() if r.note]
    if notes:
        print("\nПометки в ФИО (отрезаны):")
        for r in notes:
            print(f"  строка {r.row}: {r.full_name} /{r.note} ({r.department})")

    short = [
        r
        for r in chosen.values()
        if len(r.full_name.split()) < 2 or "." in r.full_name or any(len(w) == 1 for w in r.full_name.split())
    ]
    if short:
        print("\nНеполные ФИО (импортируются как есть):")
        print("  " + "; ".join(f"{r.full_name} ({r.department})" for r in short))

    names = sorted(chosen)
    similar = [
        (a, b)
        for i, a in enumerate(names)
        for b in names[i + 1 :]
        if a.split()[0] == b.split()[0] and difflib.SequenceMatcher(None, a, b).ratio() > 0.9
    ]
    if similar:
        print("\nПохожие ФИО — проверьте, не один ли это человек (если да — добавьте в FIX_NAMES):")
        for a, b in similar:
            print(f"  {chosen[a].full_name} ({chosen[a].department}) ~ {chosen[b].full_name} ({chosen[b].department})")

    if skipped:
        print(f"\nПропущено строк-заглушек: {len(skipped)} (вакансии, председатели АК и т.п.)")


# --- database --------------------------------------------------------------------


async def apply(teachers: list[TeacherRow], dry_run: bool) -> None:
    from sqlalchemy import select

    from database.models import Department, Teacher, async_session, init_db

    await init_db()
    async with async_session() as session:
        departments = {key(d.name): d for d in (await session.execute(select(Department))).scalars()}
        new_departments = []
        for name in dict.fromkeys(t.department for t in teachers):
            if key(name) not in departments:
                departments[key(name)] = Department(name=name)
                session.add(departments[key(name)])
                new_departments.append(name)
        await session.flush()  # ids for new departments

        existing = {key(t.full_name): t for t in (await session.execute(select(Teacher))).scalars()}
        added = updated = same = 0
        for row in teachers:
            department = departments[key(row.department)]
            teacher = existing.get(key(row.full_name))
            if teacher is None:
                teacher = Teacher(full_name=row.full_name, department_id=department.id)
                session.add(teacher)
                existing[key(row.full_name)] = teacher
                added += 1
            elif teacher.department_id is None:
                teacher.department_id = department.id
                updated += 1
            else:
                same += 1
                if teacher.department_id != department.id:
                    print(f"! {row.full_name}: в БД другая кафедра (id {teacher.department_id}), не меняю")

        if dry_run:
            await session.rollback()
        else:
            await session.commit()

    prefix = "[dry-run] было бы: " if dry_run else ""
    if new_departments:
        print(f"{prefix}новые кафедры: {', '.join(new_departments)}")
    print(
        f"{prefix}преподавателей добавлено {added}, проставлена кафедра {updated}, "
        f"уже были в БД {same}"
    )


def main() -> None:
    parser = argparse.ArgumentParser(description="Импорт преподавателей по кафедрам из списка ППС")
    parser.add_argument("file", help="Excel со списком ППС (.xlsx/.xls)")
    parser.add_argument("--dry-run", action="store_true", help="ничего не записывать в БД")
    parser.add_argument("--export", metavar="CSV", help="сохранить итоговый список в CSV")
    args = parser.parse_args()

    path = Path(args.file).expanduser()
    if not path.exists():
        sys.exit(f"Нет такого файла: {args.file}")

    rows, skipped, problems = parse_staff_list(path)
    for problem in problems:
        print("!", problem)
    if problems:
        sys.exit("Не все блоки привязаны к кафедрам — в БД ничего не записано.")

    chosen = unique_teachers(rows)
    report(rows, chosen, skipped)
    teachers = sorted(chosen.values(), key=lambda r: r.row)

    if args.export:
        with open(args.export, "w", newline="", encoding="utf-8-sig") as fh:
            writer = csv.DictWriter(fh, fieldnames=list(asdict(teachers[0]).keys()), delimiter=";")
            writer.writeheader()
            writer.writerows(asdict(t) for t in teachers)
        print(f"Список сохранён в {args.export}")

    print()
    if teachers:
        asyncio.run(apply(teachers, dry_run=args.dry_run))


if __name__ == "__main__":
    main()
