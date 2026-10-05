"""Скрипт пересоздания и заполнения БД реальными данными КазУТБ (3 курс).

Запуск: python in.py

Что здесь реальное, а что нет:
- кафедры и аудитории учебного корпуса №1 (блоки 1 и 2) — из приложения 1 к
  приказу «Закрепление аудиторного фонда за кафедрами» (тип помещения,
  ответственная кафедра, число посадочных мест);
- остальные аудитории (REAL_ROOMS) — только номера, вместимость и тип пока
  заглушки, заменить, когда будут данные по остальным корпусам;
- кафедры преподавателей и распределение нагрузки — случайные (демо).
"""
import asyncio
import random

from database.models import (
    AcademicLoad,
    AcademicLoadGroup,
    Base,
    Building,
    Department,
    Room,
    StudentGroup,
    Subject,
    Teacher,
    TimeSlot,
    async_session,
    engine,
)

RANDOM_SEED = 20260914

BUILDING_NAMES = ["Главный корпус (№1)", "Корпус №2", "Корпус №3"]

# Названия как в приказе о закреплении аудиторного фонда (опечатки выровнены).
SGD = "Социально-гуманитарные дисциплины"
FIN = "Финансы и учет"
TLP = "Технология легкой промышленности и дизайна"
ECON = "Экономика и управление"
TOUR = "Туризм и сервис"
LANG = "Государственный и иностранные языки"
CEA = "Компьютерная инженерия и автоматизация"
AUT = "Автоматизация и управление"
CHEM = "Химия, химическая технология и экология"
TECH = "Технология и стандартизация"
IT = "Информационные технологии"
DEPARTMENTS = [SGD, FIN, TLP, ECON, TOUR, LANG, CEA, AUT, CHEM, TECH, IT]

# Учебный корпус №1 (корпус 1 в BUILDING_NAMES).
# (номер, тип, кафедра, мест, этаж). Блок 1 — "1/300", блок 2 — "1/Б2-101".
# TODO уточнить: 305 ARTpoint и 405 спец. кабинет — мест в приказе нет ("-"),
# у 306 и 302 (блок 2) в бумажном списке есть рукописные пометки к числу мест,
# строка №14 в приказе пропущена (после 13 сразу 15).
KORPUS_1_ROOMS = [
    # 1 блок
    ("1/300", "lecture_hall", SGD, 62, 3),
    ("1/302", "classroom", FIN, 26, 3),
    ("1/306", "lecture_hall", TLP, 62, 3),
    ("1/303", "classroom", ECON, 16, 3),
    ("1/305", "other", TLP, 0, 3),  # ARTpoint — пространство студенческого творчества
    ("1/400", "special", TOUR, 24, 4),  # Центр практического обучения ресторанному делу
    ("1/402", "classroom", LANG, 26, 4),
    ("1/403", "classroom", LANG, 18, 4),
    ("1/404", "laboratory", CEA, 18, 4),  # Лаборатория физики и электротехники
    ("1/405", "special", TOUR, 0, 4),  # Техника и тактика активных видов туризма
    ("1/406", "lecture_hall", TOUR, 62, 4),
    ("1/500", "lecture_hall", SGD, 60, 5),
    ("1/505", "lecture_hall", FIN, 62, 5),
    # 2 блок
    ("1/Б2-101", "computer_class", AUT, 20, 1),
    ("1/Б2-103", "classroom", CHEM, 30, 1),
    ("1/Б2-104", "classroom", CHEM, 18, 1),
    ("1/Б2-105", "classroom", TECH, 30, 1),
    ("1/Б2-201", "computer_class", IT, 31, 2),
    ("1/Б2-202", "computer_class", IT, 20, 2),
    ("1/Б2-203", "classroom", ECON, 18, 2),
    ("1/Б2-204", "classroom", FIN, 34, 2),
    ("1/Б2-205", "classroom", TLP, 32, 2),
    ("1/Б2-207", "classroom", SGD, 30, 2),
    ("1/Б2-208", "computer_class", AUT, 18, 2),
    ("1/Б2-209", "classroom", LANG, 32, 2),
    ("1/Б2-210", "classroom", AUT, 26, 2),
    ("1/Б2-211", "classroom", SGD, 18, 2),
    ("1/Б2-212", "computer_class", TLP, 28, 2),
    ("1/Б2-301", "lecture_hall", FIN, 58, 3),
    ("1/Б2-302", "lecture_hall", SGD, 54, 3),
    ("1/Б2-303", "classroom", SGD, 18, 3),
]

# Лабораторные по этим дисциплинам ставим в компьютерный класс.
COMPUTER_SUBJECT_KEYWORDS = (
    "нейрон", "облачн", "тестирован", "аналитик", "программирован", "it-", "ит-", "big data",
    "devops", "безопасность и этика ии", "ar и vr", "моделирован", "1с", "digital", "информационн", "машиналық",
)

TEACHER_NAMES = [
    "Абишева А.А.", "Сагинбай Г.", "Қыдырғалиұлы Д.", "Малик О.", "Жандырова А.С.",
    "Бельгибаева Л.М.", "Хайруллина Р.Б.", "Есіркеп Г.Е.", "Еркин А.", "Смайлова Н.Т.",
    "Оралханова Б.", "Даян А.", "Байбулова М.К.", "Бақтымбет А.С.", "Муканова К.А.",
    "Жоламанова М.Ж.", "Канафина С.А.", "Рахимжанова А.М.", "Сериков К.", "Рыстыгулова В.Б.",
    "Бектурганова А.А.", "Оразаева А.Р.", "Жунусова Г.А.", "Байсингиров Ж.Б.", "Бекзат А.",
    "Саттыкова Б.", "Жумажан Н.", "Нургалиев Н.У.", "Абдыкаримова Ш.Т.", "Құрал А.",
    "Куандикова Г.Е.", "Серимбетов Б.А.", "Магзумова Л.К.", "Жуматаева А.Р.", "Умьянова С.Ж.",
    "Мұқибек Г.К.", "Ишанғалиев Е.Т.", "Сафуани Ж.Е.", "Жумабекова А.К.", "Байсбай М.М.",
    "Мерейхан Л.", "Дулат М.", "Бисеков А.Т.", "Касымова С.Б.", "Наетова Г.А.",
    "Акбаракова А.", "Амангельдина М.А.", "Рахметова С.", "Асанова Ж.А.", "Бегайдарова А.К.",
    "Жұбатқан Ж.С.", "Сәрсенбек Ш.Б.", "Бабасов А.Г.", "Муканова Б.Ш.", "Злодеева О.Г.",
    "Шакишева К.М.", "Джумагалиева А.М.", "Омарова Е.О.", "Берстембаева Р.К.", "Беспаев М.Е.",
    "Тасанова Г.Д.", "Шарипова Д.Б.", "Сактаганова Г.С.", "Каркимбаева Ш.И.", "Ибраимова С.Ж.",
    "Насакаева Б.Е.", "Сагиндыкова Г.М.", "Рейдолда С.Р.", "Гордеева Е.А.", "Шукенова Д.К.",
    "Байдаирова К.Б.", "Кусайнов Х.Х.", "Кирдасинова К.А.", "Кашкинбаева К.Т.", "Мирзоева Е.Ш.",
    "Князева К.Ж.", "Кожумов А.С.", "Кунанбаева К.Б.", "Усипбаева П.", "Тәжен А.Н.",
    "Раманкулова А.М.", "Терджан Тансель", "Жармухамедова М.Е.", "Курманкулова Н.Ж.", "Нурмадиева Г.Д.",
    "Ярдякова И.В.", "Есилов А.Б.", "Акишева А.", "Төлемис М.", "Сатканова Р.Г.",
    "Акимов Ж.М.", "Флерко Т.Г.", "Ахметова Г.Б.", "Смайыл А.", "Джангельдина Н.Н.",
    "Алимбекова Д.А.", "Зейнуллин А.А.", "Саменова Ж.К.", "Калыхбергенова С.Ж.", "Бетжанова А.Ж.",
    "Мамытова Н.С.", "Туребаева П.Д.", "Джолбузоков Ә.", "Тыкежанова Г.М.", "Аубакирова А.",
    "Байтасова С.", "Карманова Г.К.", "Жалғасбеков А.З.", "Агайдарова А.А.", "Тютебаева К.",
    "Мухамбеталина Д.Ж.", "Торемуратова Р.", "Шонтаев А.Ж.", "Ибраева А.Б.", "Ботбаева Ж.Т."
]

SUBJECT_NAMES = [
    "Нейронные сети и их приложения", "Облачные информационные системы (SaaS, PaaS)",
    "Робототехника и ІоТ-технологии", "Тестирование программного обеспечения",
    "ІТ-аналитика и прогнозирование", "Визуальное программирование и интеллектуальный анализ данных",
    "Теория принятия решений", "Насосно-компрессорные станции нефтегазопроводов и хранилищ",
    "Технология продуктов общественного питания", "Национальный стиль в графическом дизайне",
    "Проектирование и эксплуатация нефтегазохранилищ", "Корпоративная IT-инфраструктура",
    "Бизнес анализ и digital marketing", "Противокоррозионная защита нефтегазового оборудования",
    "Комплексная логистика пищевых предприятий", "Проектирование фирменного стиля",
    "Магистральные трубопроводы", "Переработка отходов пищевых производств",
    "Капитальный ремонт скважин", "Технология мяса и мясных продуктов",
    "Обработка больших данных (Big Data)", "Графический дизайн интерьера",
    "Технология молока и молочных продуктов", "Финансово-экономический анализ проектов",
    "AR и VR технологии", "Основы DevOps", "Патентоведение и разработка портфолио",
    "Безопасность и этика ИИ", "Компьютерное моделирование месторождений нефти и газа",
    "Основы планирования НИРС", "Сандық экономика", "Бейімделгіш киімді жобалау",
    "Машиналық оқыту элементтері бар өнеркәсіптік роботтарды жобалау", "Сапаны басқару",
    "Майлау материалдарының химиялық технологиясы", "Бас киімдерді жобалау",
    "Автоматика және жаңартылатын энергия көздерін басқару жүйелері",
    "Мейрамхана ісіндегі тамақтану өнімдерін өндіру технологиясы", "Табиғи және синтетикалық каучуктарды өндіру",
    "Сәулеттегі ұлттық стиль", "Өндірістік роботтардың кинематикасы және динамикасы",
    "Саяси менеджмент", "Қызмет көрсету саласындағы логистика", "Органикалық және мұнай химия синтезінің технологиясы",
    "Қоғамдық тамақтану өнімдерінің технологиясы", "Үлкен объектілерді сәулеттік жобалау",
    "Ғылыми зерттеулердің негіздері", "Автоматтандырылған жүйелердің жабдықтарын қорғау әдістері",
    "Гастрономиялық туризм", "Сүт және ет өнеркәсібі кәсіпорындарындағы технологиялық есептеулер",
    "Топтама бұйымдарды жобалау", "Қоғамдық ғимараттардың интерьерін жобалау",
    "Химия өнеркәсібіндегі еңбекті қорғау", "Сақтандыру менеджменті", "Мейрамхана бизнесі",
    "Заманауи тігін кәсіпорындарын жобалау", "Сервис саласындағы электронды жүйелер",
    "Стратегиялық жоспарлау", "Банкеттер мен қабылдауларды ұйымдастыру", "Корпоративные финансы",
    "Ценообразование", "Учет и отчетность субъектов МСБ", "Страхование", "AI-маркетинг",
    "Инновационный менеджмент", "Лидерство", "Этика государственного служащего",
    "Банковское дело", "Логистика", "Финансовый учет 2", "Учет и отчетность в банках",
    "Анализ банковской деятельности", "Налоги и налогообложение", "Управленческий учет",
    "Устойчивое развитие, экология и БЖД", "1С: Предприятие", "Тайм менеджмент",
    "Деловой турецкий язык", "Сакральный туризм", "Культурный туризм",
    "Организация международного туризма", "Китайский язык в индустрии гостеприимства",
    "Турецкий язык начальный уровень", "Технология продаж в туристской деятельности",
    "HR менеджмент в сфере услуг", "Управление качеством туристских услуг",
    "Визовая поддержка и страхование в туристском бизнесе", "Бизнес-планирование в РДГБ",
    "MICE туризм", "Барное дело и эногастрономия", "Мультикультурность в туризме",
    "Экологический туризм", "Основы калькуляции в ресторанном деле",
    "Проведение и крепление горных выработок", "Безопасность инженерных коммуникаций",
    "Нефтепромысловое оборудование", "Основные процессы и аппараты химического производства",
    "Промышленная экология", "Контроль и оценка качества сырья", "Промысловая геофизика",
    "Физико-химические методы анализа", "Экология почв", "Стандартизация и сертификация на предприятиях",
    "Разрушение горных пород взрывом", "Бурение скважин", "Биотехнология растений",
    "Холодильная техника и технология", "Горные машины и оборудование", "Пищевая биотехнология"
]

GROUPS_DATA = [
    {"code": "ИС-242/1 со", "students_count": 35, "course": 3},
    {"code": "ИС-242/2 со", "students_count": 34, "course": 3},
    {"code": "IT M242 со", "students_count": 15, "course": 3},
    {"code": "ИИ 242 со", "students_count": 15, "course": 3},
    {"code": "НГД-242 со", "students_count": 18, "course": 3},
    {"code": "ТПП-242 со", "students_count": 18, "course": 3},
    {"code": "ДЗ ГД-242 со", "students_count": 12, "course": 3},
    {"code": "IT M 241 со", "students_count": 13, "course": 3},
    {"code": "ИС-241/1 со", "students_count": 28, "course": 3},
    {"code": "ИС-241/2 со", "students_count": 28, "course": 3},
    {"code": "ИС-241/3 со", "students_count": 27, "course": 3},
    {"code": "АиУ-241 со", "students_count": 16, "course": 3},
    {"code": "МН-241 со", "students_count": 5, "course": 3},
    {"code": "РДГБ-241 со", "students_count": 18, "course": 3},
    {"code": "ХТОВ-241 со", "students_count": 15, "course": 3},
    {"code": "ТПП-241 со", "students_count": 15, "course": 3},
    {"code": "ТиКЛП-241 со", "students_count": 12, "course": 3},
    {"code": "ДЗ АРХ-241 со", "students_count": 15, "course": 3},
    {"code": "УиА-242", "students_count": 27, "course": 3},
    {"code": "ФН-242", "students_count": 21, "course": 3},
    {"code": "МТ-242/1", "students_count": 56, "course": 3},
    {"code": "МТ-242/2", "students_count": 25, "course": 3},
    {"code": "ЭК-242", "students_count": 14, "course": 3},
    {"code": "ГМУ-242", "students_count": 19, "course": 3},
    {"code": "РДГБ-242/1", "students_count": 42, "course": 3},
    {"code": "РДГБ-242/2", "students_count": 20, "course": 3},
    {"code": "РДГБ-243", "students_count": 14, "course": 3},
    {"code": "Тур-242", "students_count": 11, "course": 3},
    {"code": "МТур-242/1", "students_count": 46, "course": 3},
    {"code": "МТур-242/2", "students_count": 20, "course": 3},
    {"code": "МТур-243", "students_count": 8, "course": 3},
    {"code": "ГД-242", "students_count": 16, "course": 3},
    {"code": "БЖиЗОС-242", "students_count": 9, "course": 3},
    {"code": "НГД-242", "students_count": 13, "course": 3},
    {"code": "ХТОВ-242", "students_count": 15, "course": 3},
    {"code": "Э/я-242", "students_count": 12, "course": 3},
    {"code": "БТ-242", "students_count": 18, "course": 3},
    {"code": "ТПП-242", "students_count": 15, "course": 3},
    {"code": "ССиМ-242", "students_count": 3, "course": 3},
    {"code": "ТМО-242", "students_count": 10, "course": 3},
]

# Аудитории без данных из приказа (корпус 1 — только те, которых нет в KORPUS_1_ROOMS).
REAL_ROOMS = [
    "1/507", "1/8 (26)",
    "2/20 (36)", "2/23 (36)", "2/302", "2/303", "2/404", "2/405", "2/406а", "2/408", "2/410", "2/411", "2/412", "2/413", "2/414", "2/501", "2/502", "2/504", "2/505", "2/506", "2/508", "2/510", "2/512", "2/514",
    "3/106", "3/108", "3/110", "3/111", "3/112", "3/202", "3/204", "3/205", "3/206", "3/208", "3/209", "3/211", "3/212", "3/213", "3/302", "3/303", "3/304", "3/305", "3/306", "3/308", "3/309", "3/310", "3/311", "3/312", "3/313", "3/4 (20)", "3/401", "3/402", "3/402а", "3/403", "3/404", "3/405", "3/406", "3/409", "3/410", "3/503", "3/504", "3/505", "3/506", "3/509", "3/510", "3/515"
]


def parse_floor(room_number: str) -> int:
    clean = room_number.split("/")[-1].strip()
    if clean.startswith("1") or clean.startswith("8"):
        return 1
    elif clean.startswith("2"):
        return 2
    elif clean.startswith("3"):
        return 3
    elif clean.startswith("4"):
        return 4
    elif clean.startswith("5"):
        return 5
    return 1


async def seed() -> None:
    random.seed(RANDOM_SEED)

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
        await conn.run_sync(Base.metadata.create_all)

    async with async_session() as session:
        buildings_map = {
            1: Building(name=BUILDING_NAMES[0]),
            2: Building(name=BUILDING_NAMES[1]),
            3: Building(name=BUILDING_NAMES[2]),
        }
        departments = {name: Department(name=name) for name in DEPARTMENTS}
        session.add_all([*buildings_map.values(), *departments.values()])
        await session.flush()

        rooms = []
        for number, room_type, department, capacity, floor in KORPUS_1_ROOMS:
            rooms.append(
                Room(
                    building_id=buildings_map[1].id,
                    number=number,
                    capacity=capacity,
                    room_type=room_type,
                    department_id=departments[department].id,
                    floor=floor,
                )
            )
        for room_str in REAL_ROOMS:
            parts = room_str.split("/")
            b_num = int(parts[0])
            r_num = parts[1]
            building = buildings_map[b_num]
            floor = parse_floor(room_str)

            # Заглушка: тип и вместимость неизвестны, пока нет приказа по корпусу.
            is_lab = any(kw in r_num for kw in ["112", "213", "510", "515", "8", "23"])
            capacity = random.choice([30, 45, 60, 90])

            rooms.append(
                Room(
                    building_id=building.id,
                    number=room_str,
                    capacity=capacity,
                    room_type="laboratory" if is_lab else ("lecture_hall" if capacity >= 60 else "classroom"),
                    floor=floor,
                )
            )
        session.add_all(rooms)

        teachers = [
            Teacher(full_name=name, department_id=random.choice(list(departments.values())).id)
            for name in TEACHER_NAMES
        ]
        session.add_all(teachers)

        subjects = [Subject(name=name) for name in SUBJECT_NAMES]
        session.add_all(subjects)

        time_slots = [
            TimeSlot(slot_number=1, start_time="08:00", end_time="08:50"),
            TimeSlot(slot_number=2, start_time="09:00", end_time="09:50"),
            TimeSlot(slot_number=3, start_time="10:00", end_time="10:50"),
            TimeSlot(slot_number=4, start_time="11:00", end_time="11:50"),
            TimeSlot(slot_number=5, start_time="12:00", end_time="12:50"),
            TimeSlot(slot_number=6, start_time="13:00", end_time="13:50"),
            TimeSlot(slot_number=7, start_time="14:00", end_time="14:50"),
            TimeSlot(slot_number=8, start_time="15:00", end_time="15:50"),
            TimeSlot(slot_number=9, start_time="16:00", end_time="16:50"),
            TimeSlot(slot_number=10, start_time="17:00", end_time="17:50"),
        ]
        session.add_all(time_slots)

        groups = [
            StudentGroup(code=g["code"], students_count=g["students_count"], course=g["course"])
            for g in GROUPS_DATA
        ]
        session.add_all(groups)
        await session.flush()

        loads: list[AcademicLoad] = []
        load_group_ids: list[list[int]] = []

        for group in groups:
            # Генерация 4-6 дисциплин на группу
            assigned_subjects = random.sample(subjects, k=random.randint(4, 6))
            for subj in assigned_subjects:
                teacher = random.choice(teachers)
                # Лекция
                load_lec = AcademicLoad(
                    subject_id=subj.id,
                    teacher_id=teacher.id,
                    lesson_type="lecture",
                    total_hours_per_week=1,
                )
                loads.append(load_lec)
                load_group_ids.append([group.id])

                # Практика или лабораторная
                lesson_type = random.choice(["practice", "lab"])
                needs_pc = any(kw in subj.name.lower() for kw in COMPUTER_SUBJECT_KEYWORDS)
                load_prac = AcademicLoad(
                    subject_id=subj.id,
                    teacher_id=teacher.id,
                    lesson_type=lesson_type,
                    total_hours_per_week=random.choice([1, 2]),
                    room_type="computer_class" if lesson_type == "lab" and needs_pc else None,
                )
                loads.append(load_prac)
                load_group_ids.append([group.id])

        session.add_all(loads)
        await session.flush()

        load_groups = [
            AcademicLoadGroup(academic_load_id=load.id, group_id=gid)
            for load, gids in zip(loads, load_group_ids)
            for gid in gids
        ]
        session.add_all(load_groups)

        await session.commit()

    print(
        f"Успешно загружено в БД:\n"
        f"- Зданий: {len(buildings_map)}\n"
        f"- Кафедр: {len(departments)}\n"
        f"- Аудиторий: {len(rooms)}\n"
        f"- Преподавателей: {len(teachers)}\n"
        f"- Дисциплин: {len(subjects)}\n"
        f"- Групп: {len(groups)}\n"
        f"- Нагрузок: {len(loads)}"
    )


if __name__ == "__main__":
    asyncio.run(seed())