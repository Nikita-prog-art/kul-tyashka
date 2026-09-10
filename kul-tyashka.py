#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Культяшка — CLI-оцифровка механик одноимённого «сянься-рогалика».

Один файл, только стандартная библиотека. Запуск:

    python3 kul-tyashka.py <команда> [ключи]
    python3 kul-tyashka.py help [тема]
    python3 kul-tyashka.py <команда> --help

Основные команды:
    roll        — обычные броски костей («4d6+2d8+5», «1d100»)
    dc          — бросок с классом сложности (проверки, прорывы)
    event       — случайное событие: для каждого КС нужно заранее указать исход
    continent   — генерация и разбор континентов (номер 1d10000000 → характеристики;
                  в каждом квадрате ровно 2 суперконтинента со своими характеристиками;
                  название континента задаёт пользователь, программа его не придумывает)
    chance      — «шанс один на миллиард/миллион»
    training    — тренировки открытия меридианов
    breakthrough— прорывы на стадии культивации (на «один из тысячи» — испытания Небес)
    combat      — боевые раунды и полные бои (система ран и штрафов)
    character   — персонажи в json (ГГ — gg.json в корне, прочие — <континент>/<имя>.json)
    shop        — магазин очков (талант, корни, уроки, навыки)
    xp          — подсчёт очков за прожитую жизнь
    stages      — справочник стадий культивации
    meridians   — справочник меридианов

Обозначения:
    «подтверждено» — значения, встречающиеся в тексте произведения.
    «допущение»    — правила, которых в тексте нет; помечены в выводе.

Логика смерти от удара: разность ≥ 50 + бонус стадии мгновенно убивает/обезвреживает
(стадия Открытия Меридианов: бонус = число открытых меридианов;
Накопление Ци: бонус = 20 + слой; далее — допущения, см. stages).
"""

import argparse
import json
import math
import os
import random
import re
import sys

VERSION = "1.0"

# ============================================================
# Случайность
# ============================================================

_rng = random.Random()


def set_seed(seed):
    if seed is not None:
        _rng.seed(seed)


def roll_die(sides: int) -> int:
    return _rng.randint(1, max(1, int(sides)))


def roll_dice(count: int, sides: int):
    return [roll_die(sides) for _ in range(int(count))]


# ============================================================
# Парсер бросков: «4d6+2d8+5», «1d100+45-16», «10x2d6»
# ============================================================

_TOKEN_RE = re.compile(r"\d+[xх]\d+[dD]\d+|[+-]?\d+[dD]\d+|[+-]?\d+")


def roll_expr(expr):
    """Возвращает (сумма, список строк-пояснений)."""
    s = str(expr).replace(" ", "")
    tokens = _TOKEN_RE.findall(s)
    if not tokens:
        raise ValueError(f"не удалось разобрать выражение: {expr!r}")
    total = 0
    lines = []
    for tok in tokens:
        m = re.fullmatch(r"(\d+)[xх](\d+)[dD](\d+)", tok)
        if m:
            k, c, sd = int(m.group(1)), int(m.group(2)), int(m.group(3))
            rolls = roll_dice(c, sd)
            sub = sum(rolls)
            val = sub * k
            lines.append(f"{k}×({c}d{sd}: [{','.join(map(str, rolls))}])")
            total += val
            continue
        m = re.fullmatch(r"([+-]?)(\d+)[dD](\d+)", tok)
        if m:
            sign = -1 if m.group(1) == "-" else 1
            c, sd = int(m.group(2)), int(m.group(3))
            rolls = roll_dice(c, sd)
            sub = sum(rolls)
            total += sign * sub
            lines.append(f"{'-' if sign < 0 else ''}{c}d{sd}: [{','.join(map(str, rolls))}]")
            continue
        m = re.fullmatch(r"([+-]?)(\d+)", tok)
        if m:
            sign = -1 if m.group(1) == "-" else 1
            v = int(m.group(2))
            total += sign * v
            lines.append(f"{'-' if sign < 0 else ''}{v}")
    return total, lines


def fmt_num(n) -> str:
    """Число без лишних нулей: 76.6584, 410.4, 5.80608."""
    if isinstance(n, int):
        return str(n)
    return (f"{n:.6f}".rstrip("0").rstrip("."))


def fmt_xp(n) -> str:
    return fmt_num(round(n, 6))


# ============================================================
# Меридианы
# ============================================================

MERIDIANS = [
    ("позвоночника", 10),
    ("верхний рук", 20),
    ("верхний ног", 30),
    ("нижний рук", 40),
    ("нижний ног", 50),
    ("лёгких", 60),
    ("желудка", 70),
    ("печени", 80),
    ("гениталий", 90),
    ("сердца", 100),
    ("пяти чувств", 110),
    ("мозга", 120),
]
MERIDIAN_TOTAL = sum(d for _, d in MERIDIANS)  # 780

ELEMENTS = ["Дерево", "Огонь", "Земля", "Металл", "Вода"]


def meridian_by_target(target):
    for name, diff in MERIDIANS:
        if diff == target:
            return name, diff
    return None, None


# ============================================================
# Стадии культивации
# ============================================================

# Срок жизни, великий предел, «один из тысячи» (испытание Небес), примечания
STAGES = [
    ("Открытия Меридианов", "12 меридианов, сложность 10..120 (сумма 780)",
     "60–80 лет + 5 за каждые 2 меридиана (до +30)", False, False,
     "Каждый меридиан +1d6 к боевым броскам. Скрытый 13-й меридиан открывается отдельно."),
    ("Накопления Ци", "нулевой великий предел; КС = 10/e^(-10 + 0,1·чистота корней); "
     "до 20–25 лет", "120 + 10·(слой-1), до 200 на 9 слое", False, False,
     "Пороговая чистота корней 77%. Меридианы «всегда полны»: каждый даёт +6 вместо 1d6."),
    ("Закладывания Фундамента", "1 из 20; КС 210 (калибровано: типичный претендент ~50% — допущение)",
     "250 + 25·(пластина-1), до 400 на 7",
     False, False,
     "Число трещин основания: 9 - (чистота-40)//15 (мин. 1 без 13-го меридиана, мин. 0 с ним) — допущение."),
    ("Золотого Ядра", "первый великий предел; 1 из 1000; основной бросок КС 270 + испытание Небес (мир "
     "испытания, внутренний демон, финал ~50% для среднего) — допущение",
     "600 → 1000", True, True,
     "Одна попытка за жизнь; провал разрушает культивацию. Штраф за каждую трещину основания -20. "
     "Трещин > 6 — основание крошится."),
    ("Пробуждения Души", "1 из 20; КС 340 (допущение)", "2000–3000", False, False,
     "На этой стадии впервые можно создать призрака."),
    ("Ищущего Дао", "1 из 20; КС 380 (допущение)", "5000", False, False,
     "Отчаянные и безумные поиски своего Дао."),
    ("Постигшего Дао", "второй великий предел; 1 из 1000; КС 400 + испытание Небес "
     "(по той же схеме, что и Ядро) — допущение",
     "~10 000", True, True,
     "«Создавший Золотое Ядро раздавит десяток не создавших, Постигший Дао — сотню Ищущих»."),
    ("Отделения Духа", "1 из 20; КС 420 (допущение)", "~20 000 (допущение)", False, False,
     "У культиваторов этой стадии «весьма вольные отношения со смертью»."),
    ("Последнего Паломничества", "1 из 20; КС 460 (допущение)", "неизвестно", False, False,
     "Паломничество к Северным/Южным Краесветным горам; идти может любой, но монстры сильнее."),
    ("Вознесения", "третий великий предел; паломничество к центру Краесветных гор, босс — "
     "мистический зверь, затем испытание Небес",
     "истинное бессмертие", True, True,
     "В среднем одно Вознесение раз в тысячу лет; ощущается всеми людьми мира."),
    ("Вечного Императора", "вне системы; количество Императоров и Вознесений не сходится",
     "не ограничен", False, False,
     "~200 суперконтинентов с Вечными Императорами; 2 великих (Му и ???). Ци Шихуанди правит 5,5 млн лет."),
]

# Бонус стадии для порога «50 + бонус» в бою (мгновенная смерть/обезвреживание)
STAGE_COMBAT_BONUS = {
    "меридианы": ("открытые меридианы", lambda lv: lv, "подтверждено"),
    "накопление": ("слой", lambda lv: 20 + lv, "подтверждено"),
    "фундамент": ("пластина", lambda lv: 40 + 5 * lv, "допущение"),
    "ядро": ("заполнение", lambda lv: 80 + 5 * lv, "допущение"),
    "душа": ("—", lambda lv: 120, "допущение"),
    "дао": ("—", lambda lv: 160, "допущение"),
    "постигший": ("—", lambda lv: 180, "допущение"),
    "отделение": ("—", lambda lv: 200, "допущение"),
    "паломничество": ("—", lambda lv: 240, "допущение"),
    "император": ("—", lambda lv: 1000, "допущение"),
}

# Сколько смертельных ран нужно для гибели (меридианы — 2, дальше +1 за стадию; допущение)
COMBAT_STAGE_ORDER = ["меридианы", "накопление", "фундамент", "ядро", "душа", "дао",
                      "постигший", "отделение", "паломничество", "император"]


def deaths_needed(stage):
    idx = COMBAT_STAGE_ORDER.index(stage) if stage in COMBAT_STAGE_ORDER else 0
    return 2 + idx


# ============================================================
# Континенты
# ============================================================

# Цифры номера: 1-2 — квадрат (координаты), 3 — фауна, 4 — Ци, 5 — размер,
# 6 — политика, 7 — мораль. (допущения помечены)
FAUNA = [
    (0, 1, "абсолютная", 1.4, "0 подтверждено, 1 допущение"),
    (2, 3, "сильная", 1.2, "2 подтверждено, 3 допущение"),
    (4, 6, "обычная", 1.0, "4/5/6 подтверждено"),
    (7, 8, "слабая", 0.9, "8 подтверждено, 7 допущение"),
    (9, 9, "???", 1.6, "допущение"),
]
QI = [
    (0, 0, "???", 2.0, "допущение"),
    (1, 2, "дефицит", 1.5, "1 подтверждено, 2 допущение"),
    (3, 5, "баланс", 1.0, "3/5 подтверждено, 4 допущение"),
    (6, 8, "достаток", 0.8, "7 подтверждено, 6/8 допущение"),
    (9, 9, "изобилие", 0.5, "9 подтверждено"),
]
SIZE = [
    (0, 0, "???", 1.0, "допущение"),
    (1, 2, "маленький", 1.0, "1 подтверждено"),
    (3, 6, "средний", 1.0, "6 подтверждено"),
    (7, 8, "большой", 1.0, "7 подтверждено"),
    (9, 9, "гигантский", 1.0, "9 подтверждено"),
]
POLITICS = [
    (0, 0, "лоскутный", 1.3, "подтверждено"),
    (1, 1, "раздробленный", 1.2, "подтверждено"),
    (2, 3, "воюющих царств", 1.1, "3 подтверждено"),
    (4, 6, "???", 1.0, "допущение"),
    (7, 8, "объединённый", 0.8, "7/8 подтверждено"),
    (9, 9, "имперский", 0.7, "подтверждено"),
]
MORALITY = [
    (0, 0, "обречённый", 2.0, "подтверждено"),
    (1, 1, "падший", 1.5, "подтверждено"),
    (2, 2, "демонический", 1.2, "подтверждено"),
    (3, 6, "сражающийся", 1.0, "3 подтверждено"),
    (7, 8, "ортодоксальный", 0.8, "7 подтверждено"),
    (9, 9, "праведный?", 0.4, "допущение"),
]

CONTINENT_COLUMNS = [
    ("фауна", 3, FAUNA),
    ("Ци", 4, QI),
    ("размер", 5, SIZE),
    ("политика", 6, POLITICS),
    ("мораль", 7, MORALITY),
]

QI_TRAINING_MOD = {"???": 3, "дефицит": -2, "баланс": 0, "достаток": 1, "изобилие": 2}
# достаток +1, ??? +3 — допущения

# Суперконтиненты: ровно по два на квадрат (100 квадратов → 200). Обычные
# характеристики к ним не применимы: Ци — изобилие, размер — гигантский
# (площадь ×4 от гигантского), политика — имперская; фауна и мораль — по номеру;
# множитель очков фиксирован — ×2 (допущение).
SUPERCONTINENTS_PER_SQUARE = 2
SUPERCONTINENT_MULT = 2.0


def _band_lookup(table, digit):
    for lo, hi, name, mult, note in table:
        if lo <= digit <= hi:
            return name, mult, note
    raise ValueError(f"цифра {digit} вне таблицы")


def parse_continent_number(number) -> dict:
    s = re.sub(r"\D", "", str(number))
    if not s:
        raise ValueError("пустой номер континента")
    s = s.zfill(7)
    if len(s) > 7:
        raise ValueError(f"номер слишком длинный: {number!r}")
    digits = [int(c) for c in s]
    out = {
        "номер": s,
        "номер_формат": f"{s[0]} {s[1:4]} {s[4:]}",
        "квадрат": s[0:2],
        "характеристики": {},
        "множители": {},
    }
    for col_name, pos, table in CONTINENT_COLUMNS:
        name, mult, note = _band_lookup(table, digits[pos - 1])
        out["характеристики"][col_name] = {"значение": name, "множитель": mult, "источник": note}
        if col_name == "размер":
            # размер никогда не влияет на скорость набора очков
            out["множители"][col_name] = 1.0
        else:
            out["множители"][col_name] = mult
    return out


# Великие суперконтиненты занимают конкретные номера, Бабилония и Шемаха —
# оба в квадрате 09, в порядке позиций. У остальных суперконтинентов имени
# в тексте нет, поэтому выводится «???».
GREAT_SUPERCONTINENTS = {
    "0000000": {"тип": "великий суперконтинент", "название": "Му",
                "император": "Ци Шихуанди", "горы": "Южные Краесветные горы",
                "мораль": "обречённый", "источник": "подтверждено"},
    "9999999": {"тип": "великий суперконтинент", "название": "???",
                "император": "Ци ???", "горы": "Северные Краесветные горы",
                "мораль": "праведный", "источник": "подтверждено"},
}
_GREAT_NUMBERS = {"00": "0000000", "99": "9999999"}
NAMED_SUPERCONTINENT_SQUARE = "09"
NAMED_SUPERCONTINENTS = [
    {"тип": "суперконтинент", "название": "Бабилония",
     "император": "Ци Семирамида", "горы": None, "источник": "подтверждено"},
    {"тип": "суперконтинент", "название": "Шемаха",
     "император": "Ци Шемаха", "горы": None, "источник": "подтверждено"},
]
ORDINARY_SUPERCONTINENT = {"тип": "суперконтинент", "название": "???",
                           "император": "Ци ???", "горы": None,
                           "источник": "подтверждено"}


def _supercontinent_slots(square: str):
    """Две позиции (0..99999) внутри квадрата, занятые суперконтинентами.

    Позиция — последние пять цифр номера (цифры 3-7). Выбор детерминирован
    квадратом: в каждом квадрате их ровно два, всего 200. В квадратах великих
    суперконтинентов одна позиция закреплена номером (0000000 и 9999999)."""
    rng = random.Random(f"суперконтиненты:{square}")
    forced = _GREAT_NUMBERS.get(square)
    if forced is None:
        return rng.sample(range(100000), SUPERCONTINENTS_PER_SQUARE)
    pos = int(forced[2:7])
    rest = rng.sample([p for p in range(100000) if p != pos],
                      SUPERCONTINENTS_PER_SQUARE - 1)
    return [pos] + rest


def supercontinent_for(number: str):
    """Суперконтинент для номера или None.

    В каждом квадрате (первые две цифры) ровно два суперконтинента, всего 200:
    Му — номер 0000000, великий ??? — 9999999, Бабилония и Шемаха — квадрат 09.
    Результат детерминирован номером: --seed и порядок вызовов не влияют."""
    s = str(number).zfill(7)
    square, pos = s[:2], int(s[2:7])
    slots = _supercontinent_slots(square)
    if pos not in slots:
        return None
    if s in GREAT_SUPERCONTINENTS:
        return GREAT_SUPERCONTINENTS[s]
    if square == NAMED_SUPERCONTINENT_SQUARE:
        return NAMED_SUPERCONTINENTS[slots.index(pos)]
    return ORDINARY_SUPERCONTINENT


def apply_supercontinent(parsed: dict, supercont: dict):
    """Подменяет характеристики номера-суперконтинента.

    Ци, размер и политика фиксированы, фауна остаётся по номеру. У великих
    суперконтинентов фиксирована и мораль (Му — обречённый, северный ??? —
    праведный). Множитель очков фиксирован — ×2 (допущение)."""
    chars = parsed["характеристики"]
    chars["Ци"] = {"значение": "изобилие", "множитель": None,
                   "источник": "фиксировано для суперконтинента (допущение)"}
    chars["размер"] = {"значение": "гигантский", "множитель": None,
                       "источник": "площадь ×4 от гигантского (допущение)"}
    chars["политика"] = {"значение": "имперский", "множитель": None,
                         "источник": "фиксировано для суперконтинента (допущение)"}
    if supercont.get("мораль"):
        chars["мораль"] = {"значение": supercont["мораль"], "множитель": None,
                           "источник": "фиксировано для великого суперконтинента"}
    parsed["множители"] = {"итог": SUPERCONTINENT_MULT}


def print_continent(parsed, supercont):
    c = parsed
    chars = " ".join(f"{v['значение']}" for k, v in c["характеристики"].items())
    if supercont:
        print(f"[Суперконтинент (номер {c['номер_формат']}, квадрат {c['квадрат']}). Характеристики: {chars}]")
        morality = (f"мораль — «{supercont['мораль']}» (фиксировано для великого суперконтинента)"
                    if supercont.get("мораль") else "мораль — по номеру")
        print("  (Ци — изобилие, размер — гигантский (площадь ×4 от гигантского), политика — "
              f"имперская; фауна — по номеру, {morality} — допущение)")
        print(f"[Множитель очков: ×{fmt_num(SUPERCONTINENT_MULT)} (фиксированный для суперконтинента)]")
        s = supercont
        gory = f", {s['горы']}" if s.get("горы") else ""
        print(f"[{s['тип'].capitalize()} «{s['название']}»{gory}. Правит {s['император']} ({s['источник']}).]")
    else:
        print(f"[Континент (номер {c['номер_формат']}). Характеристики: квадрат {c['квадрат']}, {chars}]")
        mults = "; ".join(f"{k} ×{fmt_num(v)}" for k, v in c["множители"].items())
        print(f"[Множители очков: {mults}]")
        for col, v in c["характеристики"].items():
            if "допущение" in v["источник"]:
                print(f"  ({col}: значение «{v['значение']}» — {v['источник']})")
    if c["квадрат"] in ("00", "01"):
        print("  (квадрат у края карты — вероятно, континент соседствует с великим суперконтинентом Му; "
              "за рождение рядом полагаются особые достижения)")


# ============================================================
# События (таблица КС)
# ============================================================

def parse_ks(text):
    parts = [p.strip() for p in text.replace(",", "/").split("/") if p.strip()]
    vals = sorted({int(p) for p in parts})
    if not vals:
        raise ValueError("пустой список КС")
    return vals


def parse_origin_table(text):
    """«30:варвар; 85:крестьяне; 100:горожане» -> [(30, «варвар»), ...]"""
    rows = []
    for part in text.split(";"):
        part = part.strip()
        if not part:
            continue
        ks_s, _, outcome = part.partition(":")
        rows.append((int(ks_s.strip()), outcome.strip()))
    rows.sort()
    if not rows:
        raise ValueError("пустая таблица происхождения")
    return rows


def origin_outcome(table, roll):
    for ks, outcome in table:
        if roll <= ks:
            return ks, outcome
    return None, None


def run_event(ks_list, outcomes, dice_expr):
    total, lines = roll_expr(dice_expr)
    print(f"{{Бросок {' + '.join(lines)} = {total}}}")
    chosen = None
    for k in ks_list:
        if total <= k:
            chosen = k
            break
    if chosen is None:
        print(f"Бросок {total} выше максимального КС {max(ks_list)} — исход не определён.")
        return
    print(f"{total} <= {chosen} → {outcomes[chosen]}")


# ============================================================
# Шанс «один на миллиард»
# ============================================================

def chance_roll(level: int):
    """level: 0-2 — «один на миллиард», 3-4 — «один на миллион»."""
    if level == 0:
        d1, d2 = roll_die(1000), roll_die(1000)
        ok = d1 <= 3 and d2 <= 3
        desc = f"бросок 2d1000: {d1}, {d2}"
    elif level == 1:
        d1, d2 = roll_die(1000), roll_die(100)
        ok = d1 <= 3 and d2 <= 3
        desc = f"бросок 1d1000, 1d100: {d1}, {d2}"
    elif level == 2:
        d1, d2 = roll_die(1000), roll_die(10)
        ok = d1 <= 3 and d2 <= 3
        desc = f"бросок 1d1000, 1d10: {d1}, {d2}"
    elif level == 3:
        d1 = roll_die(1000)
        ok = d1 <= 9
        desc = f"бросок 1d1000: {d1}"
    else:
        d1 = roll_die(100)
        ok = d1 <= 9
        desc = f"бросок 1d100: {d1}"
    names = {0: "«один на миллиард» (9 из миллиона)", 1: "«один на миллиард» (9 из ста тысяч)",
             2: "«один на миллиард» (9 из десяти тысяч)", 3: "«один на миллион» (9 из тысячи)",
             4: "«один на миллион» (9 из ста, максимум)"}
    print(f"{{Шанс {names.get(level, level)}, {desc}. {'Успех' if ok else 'Провал'}.}}")
    return ok


# ============================================================
# Тренировки (открытие меридианов)
# ============================================================

def training_dice_count(talent):
    return talent // 10 + 2


def run_training(talent, age, qi, resources=0, sect_resources=0, despair=False,
                 current=None, target=None):
    n = training_dice_count(talent)
    qi_mod = QI_TRAINING_MOD.get(qi, 0)
    rolls = roll_dice(n, talent)
    base = sum(rolls)
    total = base + qi_mod + resources + sect_resources
    if despair:
        total = total // 2
    print(f"{{Тренировка, бросок {n}d{talent}" +
          (f" {qi_mod:+d} (Ци: {qi})" if qi_mod else "") +
          (f" + {resources} (ресурсы)" if resources else "") +
          (f" + {sect_resources} (ресурсы секты)" if sect_resources else "") +
          (" / 2 (отчаяние)" if despair else "") +
          f": [{','.join(map(str, rolls))}] = {total}}}")
    if age > 70:
        print("  (внимание: в этом возрасте тренировки обычно полностью прекращены)")
    elif age > 50:
        print("  (после 50 лет броски тренировок выполняются раз в 10 лет)")
    elif age > 20:
        print("  (после 20 лет броски тренировок выполняются раз в 5 лет)")
    if current is not None and target is not None:
        new = current + total
        name, _ = meridian_by_target(target)
        if new >= target:
            leftover = new - target
            nxt = MERIDIANS[MERIDIANS.index((name, target)) + 1] if name and name != "мозга" else None
            if nxt:
                print(f"Меридиан {name} ({current}/{target} → {new}/{target}) открыт! +1d6 к боевым броскам, "
                      f"+5 лет жизни за каждые два меридиана. Остаток {leftover} переходит в меридиан {nxt[0]} "
                      f"({leftover}/{nxt[1]}).")
            else:
                print(f"Меридиан {name} открыт! Все двенадцать меридианов открыты (+12d6, +30 лет).")
        else:
            print(f"Меридиан {name} прогрессирует: {current}/{target} → {new}/{target}.")
    return total


# ============================================================
# Прорывы на стадии
# ============================================================

def conductivity(purity):
    return math.exp(-10 + 0.1 * purity)


def breakthrough_nakoplenie(talent, purity, bonus_expr, method_req=None,
                            element_share=None, element_req=None, ladder=False):
    roll, lines = roll_expr("1d100")
    parts = [f"1d100: {roll}", f"{talent} (талант)"]
    total = roll + talent
    if bonus_expr:
        b, bl = roll_expr(bonus_expr)
        parts.append(f"{b} (бонусы: {' + '.join(bl)})")
        total += b
    if ladder:
        parts.append("1 (лестница в небо)")
        total += 1
    if method_req is not None and purity < method_req:
        pen = method_req - purity
        parts.append(f"-{pen} (чистота корней {purity}% при пороге метода {method_req}%)")
        total -= pen
    if element_share is not None and element_req is not None:
        if element_share >= element_req:
            parts.append(f"5 ({element_share}% огненных корней при пороге {element_req}%)")
            total += 5
        else:
            print(f"  (внимание: {element_share}% стихийных корней меньше порога метода {element_req}%)")
    cond = conductivity(purity)
    ks = 10 / cond
    print(f"{{Попытка прорыва на стадию Накопления Ци. {' + '.join(parts)} = {total}. "
          f"Чистота корней {purity}%. Коэффициент проводимости корней e^(-10 + 0,1·{purity}) = "
          f"e^({-10 + 0.1 * purity:.1f}) = {cond:.4f}. КС прорыва 10/{cond:.4f} = {fmt_num(round(ks, 4))}. "
          f"{'Успех' if total >= ks else 'Провал'}.}}")
    if total >= ks:
        print("Даньтянь открыт — нулевой великий предел пройден. Стадия Накопления Ци, слой 1 "
              "(жизнь 120 лет).")
    else:
        print("Провал. До следующей попытки нужен перерыв (жертвы выделяют раз в год). "
              "Получен урок «повышение чистоты корней +1» (после каждого провала).")
    return total >= ks


def tribulation_mods(talent, purity, method=0, resources=0, sacrifices=0):
    """Модификатор финального броска испытания Небес.

    Калибровка: средний культиватор, удовлетворяющий требованиям прорыва
    (талант 100, чистота корней 90%, обычный метод, без ресурсов и жертв),
    получает модификатор 0 и проходит испытание с шансом ~50% (1d100 против КС 50).
    Допущение."""
    return (talent - 100) / 2 + (purity - 90) + method / 2 + resources / 2 + sacrifices / 2


def run_tribulation(stage_name, talent, purity, method=0, resources=0, sacrifices=0,
                    will_bonus=0, will_ks=None, final_ks=None, demon_choice=None,
                    one_shot=True):
    """Испытание Небес: переход в мир испытания -> разговор с внутренним демоном ->
    финальное преодоление. Средний претендент проходит с шансом ~50% (допущение)."""
    print(f"{{Испытание Небес ({stage_name})! Культиватор переносится в мир испытания.}}")
    will_k = will_ks if will_ks is not None else 30
    choice = demon_choice
    if choice is None:
        if sys.stdin.isatty():
            ans = input("Внутренний демон предлагает демонический путь. "
                        "1 — устоять, 2 — уступить [1]: ").strip() or "1"
            choice = "resist" if ans == "1" else "yield"
        else:
            choice = "resist"
    if choice == "yield":
        print("{Внутренний демон: культиватор уступил искушению — испытание провалено.}")
        return False
    w = roll_die(100)
    passed = w + will_bonus >= will_k
    will_mod = 10 if passed else -20
    print(f"{{Внутренний демон. Проверка воли: 1d100: {w} + {will_bonus} (воля) "
          f"против КС {will_k} (допущение) — {'устоял' if passed else 'дрогнул'}; "
          f"модификатор финального броска {will_mod:+d}.}}")
    f_k = final_ks if final_ks is not None else 50
    mods = tribulation_mods(talent, purity, method, resources, sacrifices)
    r = roll_die(100)
    total = r + mods + will_mod
    print(f"{{Финальное преодоление: 1d100: {r} + {fmt_num(mods)} (талант/корни/метод/ресурсы) "
          f"{will_mod:+d} (воля) = {fmt_num(total)} (КС {fmt_num(f_k)}). "
          f"{'Выдержано' if total >= f_k else 'Провал'}.}}")
    if total < f_k:
        print("Небеса отвергли культиватора." +
              (" Культивация разрушена (инвалид или труп)." if one_shot else ""))
        return False
    print("Испытание Небес выдержано.")
    return True


def breakthrough_high(stage, talent, purity, method=0, resources=0, sacrifices=0,
                      cracks=None, thirteenth=False, trib_ks=None, will_ks=None,
                      will_bonus=0, demon_choice=None, no_tribulation=False):
    config = {
        # КС подобраны (допущение) так, что типичный претендент, удовлетворяющий
        # требованиям стадии, проходит основной бросок с шансом ~50%;
        # испытание Небес дополнительно даёт ~50% для среднего претендента.
        "фундамент": (210, False),
        "ядро": (270, True),
        "душа": (340, False),
        "дао": (380, False),
        "постигший": (400, True),
        "отделение": (420, False),
        "паломничество": (460, False),
    }
    if stage not in config:
        raise ValueError(f"неизвестная стадия {stage}")
    ks, has_trib = config[stage]
    one_shot = stage == "ядро"
    if cracks is None:
        if thirteenth:
            cracks = 0  # «Именно так можно избежать трещин» (скрытый меридиан)
        else:
            cracks = 9 - (purity - 40) // 15
            cracks = max(1, min(9, cracks))
    parts = []
    roll, _ = roll_expr("1d100")
    total = roll
    parts.append(f"1d100: {roll}")
    parts.append(f"{talent} (талант)")
    total += talent
    if purity:
        b = 3 * (purity - 77)
        parts.append(f"{b} (чистота корней {purity}%)")
        total += b
    if method:
        parts.append(f"{method} (метод)")
        total += method
    if resources:
        parts.append(f"{resources} (ресурсы)")
        total += resources
    if sacrifices:
        parts.append(f"{sacrifices} (жертвоприношения)")
        total += sacrifices
    if stage == "ядро":
        if cracks > 6:
            print(f"{{Прорыв на стадию Золотого Ядра: трещин в основании {cracks} (> 6) — "
                  f"основание крошится под собственным весом. Автоматический провал (и, вероятно, смерть).}}")
            return False
        parts.append(f"{-20 * cracks} (трещины основания: {cracks})")
        total -= 20 * cracks
    print(f"{{Попытка прорыва на стадию {stage.capitalize()} (КС {ks} — допущение). "
          f"{' + '.join(parts)} = {total}. {'Успех' if total >= ks else 'Провал'}.}}")
    ok = total >= ks
    if not ok:
        if one_shot:
            print("Одна-единственная ошибка — культивация разрушена (инвалид или труп).")
        else:
            print("Провал. Можно пробовать снова (перерыв между попытками).")
        return False
    if has_trib and not no_tribulation:
        if not run_tribulation(stage.capitalize(), talent, purity, method, resources, sacrifices,
                               will_bonus, will_ks, trib_ks, demon_choice, one_shot):
            return False
    margin = total - ks
    if stage == "фундамент":
        print(f"Стадия Закладывания Фундамента достигнута. Трещин в основании: {cracks} "
              f"(9 — фундамент склонен крошиться). Жизнь 250 + 25·(пластина-1).")
    elif stage == "ядро":
        quality = "высшее" if margin >= 150 else ("среднее" if margin >= 50 else "низшее")
        print(f"Золотое Ядро создано (качество: {quality} — допущение). Жизнь 600→1000 лет.")
    else:
        print(f"Стадия «{stage}» достигнута.")
    return True


def breakthrough_voznesenie(talent, purity, resources=0, method=0, sacrifices=0,
                            trib_ks=None, will_ks=None, will_bonus=0, demon_choice=None,
                            no_tribulation=False, boss_ks=None, journey_ks=None):
    j_ks = journey_ks if journey_ks is not None else 150
    print(f"{{Паломничество к центру Краесветных гор (допущение: три перехода)...}}")
    for i in range(1, 4):
        r = roll_die(100)
        total = r + talent // 4 + (3 * (purity - 77)) // 2 + resources
        ok = total >= j_ks
        print(f"  Переход {i}/3: 1d100: {r} + {talent // 4} (талант) + "
              f"{(3 * (purity - 77)) // 2} (корни) + {resources} (ресурсы) = {total} "
              f"(КС {j_ks}) — {'успех' if ok else 'провал'}")
        if not ok:
            print("Паломник погиб в горах.")
            return False
    b_ks = boss_ks if boss_ks is not None else 160
    r = roll_die(100)
    bmod = talent // 2 + (purity - 77) + resources
    total = r + bmod
    print(f"{{Центр Краесветных гор. Страж — мистический зверь! Решающий бросок: "
          f"1d100: {r} + {bmod} (талант/корни/ресурсы) = {total} (КС {b_ks} — допущение). "
          f"{'Зверь повержен' if total >= b_ks else 'Провал'}.}}")
    if total < b_ks:
        print("Паломник погиб в бою со стражем.")
        return False
    if not no_tribulation:
        if not run_tribulation("Вознесение", talent, purity, method, resources, sacrifices,
                               will_bonus, will_ks, trib_ks, demon_choice, one_shot=False):
            print("Небеса не приняли паломника.")
            return False
    print("ВОЗНЕСЕНИЕ! Истинное бессмертие. Это ощутили все люди мира. "
          "В среднем такое случается раз в тысячу лет.")
    return True


# ============================================================
# Бой
# ============================================================

WOUNDS = [
    (1, 5, "Царапина", -1),
    (6, 9, "Синяк/порез", -2),
    (10, 20, "Рана", -5),
    (21, 39, "Серьёзная рана", -10),
]


def wound_result(diff, stage, stage_level, deaths):
    thr = 50 + STAGE_COMBAT_BONUS[stage][1](stage_level)
    if diff >= thr:
        return {"текст": f"диапазон {thr}+", "исход": "смертельный удар — цель убита / полностью обезврежена",
                "штраф": None, "смертей": 1, "диапазон_легенда": True}
    if diff >= 40:
        deaths += 1
        if deaths >= deaths_needed(stage):
            return {"текст": f"диапазон 40–{thr - 1}", "исход": f"смертельная рана №{deaths} — цель убита",
                    "штраф": None, "смертей": deaths, "диапазон_легенда": False}
        return {"текст": f"диапазон 40–{thr - 1}", "исход": f"смертельная рана, штраф -20",
                "штраф": -20, "смертей": deaths, "диапазон_легенда": False}
    for lo, hi, name, pen in WOUNDS:
        if lo <= diff <= hi:
            return {"текст": f"диапазон {lo}–{hi}", "исход": f"{name.lower()}, штраф {pen}",
                    "штраф": pen, "смертей": deaths, "диапазон_легенда": False}
    return {"текст": "0", "исход": "атака безуспешна", "штраф": 0, "смертей": deaths,
            "диапазон_легенда": False}


class Side:
    def __init__(self, name, pool, weapon=0, training_atk=0, training_def=0,
                 meridians=0, flat_meridians=0, extra_atk="", extra_def="",
                 technique_atk=0, technique_def=0, passive=0, barrier=0, penalty=0):
        self.name = name
        self.pool = int(pool)
        self.weapon = weapon
        self.training_atk = training_atk
        self.training_def = training_def
        self.meridians = meridians
        self.flat_meridians = flat_meridians
        self.extra_atk = extra_atk or ""
        self.extra_def = extra_def or ""
        self.technique_atk = technique_atk
        self.technique_def = technique_def
        self.passive = passive
        self.barrier = barrier
        self.penalty = penalty
        self.deaths = 0

    def roll_attack(self, alloc):
        parts = []
        total = 0
        if alloc > 0:
            die = roll_die(alloc)
            total += die
            parts.append(f"1d{alloc}: {die}")
        if self.weapon:
            parts.append(f"{self.weapon} (оружие)")
            total += self.weapon
        if self.training_atk:
            parts.append(f"{self.training_atk} (тренировки)")
            total += self.training_atk
        if self.meridians:
            rolls = roll_dice(self.meridians, 6)
            total += sum(rolls)
            parts.append(f"{self.meridians}d6: [{','.join(map(str, rolls))}] = {sum(rolls)} (меридианы)")
        if self.flat_meridians:
            parts.append(f"{self.flat_meridians} (меридианы всегда полны/кольцо)")
            total += self.flat_meridians
        if self.extra_atk:
            v, ls = roll_expr(self.extra_atk)
            total += v
            parts.append(f"({self.extra_atk}: {' + '.join(ls)} = {v})" if len(ls) > 1
                         else f"({self.extra_atk}: {ls[0]})")
        if self.technique_atk:
            parts.append(f"{self.technique_atk} (техника)")
            total += self.technique_atk
        if self.penalty:
            parts.append(f"{self.penalty} (штраф ран)")
            total += self.penalty
        return total, parts

    def roll_defense(self, alloc):
        parts = []
        total = 0
        if alloc > 0:
            die = roll_die(alloc)
            total += die
            parts.append(f"1d{alloc}: {die}")
        if self.training_def:
            parts.append(f"{self.training_def} (тренировки)")
            total += self.training_def
        if self.meridians:
            rolls = roll_dice(self.meridians, 6)
            total += sum(rolls)
            parts.append(f"{self.meridians}d6: [{','.join(map(str, rolls))}] = {sum(rolls)} (меридианы)")
        if self.flat_meridians:
            parts.append(f"{self.flat_meridians} (меридианы всегда полны/кольцо)")
            total += self.flat_meridians
        if self.extra_def:
            v, ls = roll_expr(self.extra_def)
            total += v
            parts.append(f"({self.extra_def}: {' + '.join(ls)} = {v})" if len(ls) > 1
                         else f"({self.extra_def}: {ls[0]})")
        if self.penalty:
            parts.append(f"{self.penalty} (штраф ран)")
            total += self.penalty
        return total, parts


def resolve_against_defense(atk_total, def_total, def_parts, layers):
    """Возвращает (остаток, строки-пояснения)."""
    out = []
    remain = atk_total - def_total
    if def_parts:
        out.append(f"Защита, бросок {' + '.join(def_parts)} = {def_total}.")
    else:
        out.append("Защиты нет (вся Ци ушла в техники/атаку).")
    if remain <= 0:
        out.append("Атака отражена, остаток 0.")
        return 0, out
    for name, val in layers:
        if val <= 0:
            continue
        if remain <= val:
            out.append(f"{name} {val} поглощает остаток {remain}.")
            return 0, out
        remain -= val
        out.append(f"{name} {val} пробит(а), остаток {remain}.")
    return remain, out


def resolve_round(attacker: Side, defender: Side, att_alloc, def_alloc,
                  def_stage, def_level):
    atk, atk_parts = attacker.roll_attack(att_alloc)
    print(f"{{Атака {attacker.name}, бросок {' + '.join(atk_parts) if atk_parts else 'нет'} = {atk}}}")
    layers = []
    if defender.technique_def:
        layers.append(("Барьер техники", defender.technique_def))
    if defender.passive:
        layers.append(("Пассивная защита", defender.passive))
    if defender.barrier:
        layers.append(("Барьер Ци", defender.barrier))
    if def_alloc > 0:
        d, d_parts = defender.roll_defense(def_alloc)
        remain, lines = resolve_against_defense(atk, d, d_parts, layers)
        for ln in lines:
            print("  " + ln)
    elif layers:
        remain, lines = resolve_against_defense(atk, 0, [], layers)
        for ln in lines:
            print("  " + ln)
    else:
        remain = atk
        print(f"  {defender.name} не защищается, остаток {remain}.")
    diff = remain
    res = wound_result(diff, def_stage, def_level, defender.deaths)
    if res["штраф"] is not None:
        defender.penalty += res["штраф"]
    if res["смертей"]:
        defender.deaths = res["смертей"]
    print(f"  Разность {diff}, {res['текст']} → {res['исход']}.")
    return res


# ============================================================
# Магазин
# ============================================================

# Магазин: ассортимент хранится в shop.json (по умолчанию магазин пуст).
SHOP_FILE = "shop.json"

# Встроенные предметы: id -> (название, цена по умолчанию; None = динамическая).
DYNAMIC_ITEMS = {"талант_грань", "талант_кость", "талант_бонус", "корни_макс",
                 "корни_мин", "шанс_1млрд", "шанс_1млн", "урок"}

SKILL_ITEMS = {
    "тренировка_защиты_дилетант": (2, "Тренировки дилетанта в защите (+2%)"),
    "тренировка_защиты_новобранец": (5, "Тренировки новобранца в защите (+5%)"),
    "тренировка_защиты_молодой_боец": (10, "Тренировки молодого бойца в защите (+10%, с 10 лет)"),
    "тренировка_атаки_молодой_боец": (5, "Тренировки молодого бойца в атаке (+10%, с 10 лет)"),
    "тренировка_атаки_боец": (10, "Тренировки бойца в атаке (+15%, с 10 лет)"),
    "копьё_дилетант": (2, "Тренировки дилетанта с коротким копьём (+2% атаки)"),
    "копьё_новичок": (5, "Тренировки новичка с коротким копьём (+5% атаки, +2% защиты)"),
    "меч_раз_в_жизни": (5, "Лишь раз в жизни держал в руках меч (-10% → -5% к атаке мечом)"),
}

KNOWN_ITEMS = {
    "талант_грань": ("Увеличить максимальный талант (кость таланта)", None),
    "талант_кость": ("Добавить кость таланта", None),
    "талант_бонус": ("Плоский бонус к таланту", None),
    "корни_макс": ("Минимальная величина максимальной чистоты корней +5%", None),
    "корни_мин": ("Минимальная чистота корней +1%", None),
    "урок": ("Урок (на выбор)", None),
    "шанс_1млрд": ("Шанс «один на миллиард» (уровни 1-3)", None),
    "шанс_1млн": ("Шанс «один на миллион» (уровни 1-2)", None),
    "младенческая_смертность": ("Отмена младенческой смертности", 1),
    "выбор_пола": ("Выбор пола (множители 0,8/1,2/1 вместо 1/1,5)", 1),
    "тринадцатый_меридиан": ("Тринадцатый меридиан (скрытый)", 10000),
    "лестница_в_небо": ("Лестница в небо (+1% к прорыву Накопления Ци)", 5),
    "травничество": ("Базовое травничество", 5),
    "великий_успех": ("Урок: великий успех (накопитель)", 10),
    "заряд_великого_успеха": ("Заряд великого успеха", 100),
    "в_память_о_цао": ("Урок: в память о Цао", 5),
    "зеркало_славы": ("Заплатка: Зеркало славы", 0),
    "работа_над_ошибками": ("Урок: работа над ошибками", 0),
    "телосложение_огня": ("Огненное телосложение (обычное)", 20),
    "очищение_телосложения": ("Очищение телосложения", 200),
    "метод_пылающего_козла": ("Фолиант: метод Пылающего Козла, Скачущего По Курганам Врагов", 5),
}
for _sid, (_cost, _name) in SKILL_ITEMS.items():
    KNOWN_ITEMS[_sid] = (_name, _cost)


def load_shop():
    if not os.path.exists(SHOP_FILE):
        return {"предметы": []}
    with open(SHOP_FILE, encoding="utf-8") as f:
        return json.load(f)


def save_shop(data):
    with open(SHOP_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def shop_find(shop, item_id):
    for it in shop.get("предметы", []):
        if it["id"] == item_id:
            return it
    return None


def dynamic_cost(item_id, p):
    """Динамическая цена встроенного предмета по состоянию персонажа."""
    if item_id == "талант_грань":
        t = p["талант"]
        return 1 if t["грань"] == 0 else t["грань"] + 1
    if item_id == "талант_кость":
        return 10 * (2 ** (p["талант"]["кости"] - 1))
    if item_id == "талант_бонус":
        return 5 * (p["талант"]["бонус"] + 1)
    if item_id == "корни_макс":
        return 5
    if item_id == "корни_мин":
        return p["корни"]["мин"] + 1
    if item_id == "шанс_1млрд":
        return [1, 10, 100][p["шанс_1млрд"]]
    if item_id == "шанс_1млн":
        return [10, 100][p["шанс_1млн"]]
    if item_id == "урок":
        return 5 * (len(p["уроки"]) + 1)
    raise ValueError(f"нет динамической цены для «{item_id}»")


LESSONS_CATALOG = [
    ("будь сильнее", "5 (1-й урок)", "+5% макс. корней, +1% чистоты, талант до 1d2"),
    ("бей наотмашь!", "5+", "+2 к атакам ближнего боя и без оружия"),
    ("демонические культиваторы — зло", "5+", "+5% к сопротивлению демоническому искусу"),
    ("направление на север", "5+", "+5% к поиску севера"),
    ("змеиный яд опасен для жизни!", "5+", "+5% сопротивление ядам"),
    ("невыученные уроки", "5+", "можно отложить 1 урок на 1 жизнь"),
    ("будь талантливей!", "5+", "+5 макс. корней, +3 чистоты, кость таланта до d6"),
    ("ученье — свет!", "5+", "+5% экзамены; +1 алхимия/артефакторика/талисманы"),
    ("следуй по стопам молодого поколения!", "5+", "+10% экзамены; +2 алхимия/артефакторика/талисманы"),
    ("путь наверх", "5+", "накопитель «горожане» (+20% за жизнь)"),
    ("чем же всё закончилось?", "0", "переродиться ребёнком Ли Бай и Ли Сяо (Светлые Террасы)"),
    ("не запретное знание", "5+", "самотренировки с 8 лет, вдвое медленнее, риск повредить меридиан"),
    ("не забывай о чистоте корней!", "5+", "+10 макс. корней, +2 чистоты"),
    ("север где-то там!", "5+", "+10% к поиску севера"),
    ("говорить словами через рот", "5+", "+5% к успешным разговорам"),
    ("работа ног", "5+", "+2 к защите"),
    ("окрестности суперконтинента Му", "5+", "накопитель «окрестности Му» (+1% за жизнь)"),
    ("демонические культиваторы — здорово!", "5+", "инверсия «…— зло»: +5% поддаться искусу"),
    ("демоническое вознесение для чайников", "5+", "+5% демоническим прорывам (Накопление/Фундамент/Ядро)"),
    ("огненное телосложение (обычное)", "5+", "стихийные корни — огонь; +1 талант, +5/+1 корни"),
    ("неопалимая", "5+", "+10 к защите от огня (только с огненным телосложением)"),
    ("невыученные уроки всё ещё не выучены", "5+", "второй слот «невыученных уроков»"),
    ("ВОЛЯ!", "5+", "+10 к броскам воли (после критуспеха)"),
    ("великий успех", "10 (фикс.)", "накопитель «великий успех»"),
    ("заряд великого успеха", "100 (фикс.)", "+1 заряд в накопитель великого успеха"),
    ("работа над ошибками", "0 (фикс.)", "сдвиг континента на 3 градации к ортодоксальному"),
    ("максимум зарядов великого успеха", "5+", "макс. зарядов великого успеха → 2"),
    ("в память о Цао", "5 (фикс.)", "семья важнее культивации (малый шанс)"),
    ("дуэлянт", "5+", "+5% атаки, +5% защиты"),
    ("метод Пылающего Козла…", "5 (фикс.)", "фолиант: плохой метод (Накопление Ци)"),
    ("повышение чистоты корней", "5+", "+1% чистоты (после каждого провала прорыва)"),
    ("эта цена корней слишком велика!", "5+", "цена корней вдвое меньше (пререквизит: мин. 10%)"),
    ("навык дипломатии", "5+", "+5% к успешным разговорам"),
]


# ============================================================
# Персонажи (json)
# ============================================================

def default_char(name, continent):
    return {
        "имя": name,
        "гг": continent is None,
        "континент": continent,
        "баланс_xp": 0.0,
        "покупки": {
            "младенческая_смертность": False,
            "выбор_пола": False,
            "талант": {"кости": 1, "грань": 0, "бонус": 0},
            "корни": {"мин": 0, "макс": 0},
            "шанс_1млрд": 0,
            "шанс_1млн": 0,
            "лестница_в_небо": False,
            "травничество": False,
            "тринадцатый_меридиан": False,
            "телосложение": None,
            "уроки": [],
            "навыки": [],
            "предметы": [],
            "накопители": {
                "горожане": {"заряд": 0, "макс": 1, "прогресс": 0.0},
                "окрестности_му": {"заряд": 0, "макс": 1, "прогресс": 0.0},
                "великий_успех": {"заряд": 0, "макс": 1, "прогресс": 0.0},
            },
        },
        "жизни": [],
        "заметка": "",
    }


def load_char(path):
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def save_char(path, data):
    parent = os.path.dirname(path)
    if parent:
        os.makedirs(parent, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def char_file(args):
    gg = getattr(args, "gg", False)
    name = getattr(args, "name", None)
    cont = getattr(args, "continent", None)
    if gg or (not name and not cont):
        return "gg.json"
    if not cont:
        raise SystemExit("ошибка: для обычного персонажа укажите --continent <континент>")
    return os.path.join(cont, name + ".json")


def show_char(data):
    p = data["покупки"]
    t = p["талант"]
    r = p["корни"]
    print(f"Имя: {data['имя']}")
    print(f"ГГ: {'да' if data['гг'] else 'нет'}; континент: {data['континент']}")
    print(f"Баланс XP: {fmt_xp(data['баланс_xp'])}")
    if t["грань"] == 0:
        print("Талант: 0 (кость таланта не куплена — культивация невозможна)")
    else:
        print(f"Талант: {t['кости']}d{t['грань']}" + (f"+{t['бонус']}" if t["бонус"] else ""))
    print(f"Корни: мин. {r['мин']}% / макс. {r['макс']}%")
    print(f"Младенческая смертность отменена: {'да' if p['младенческая_смертность'] else 'нет'}")
    print(f"Выбор пола: {'да' if p['выбор_пола'] else 'нет'}")
    print(f"Шанс «один на миллиард»: уровень {p['шанс_1млрд']}; «один на миллион»: уровень {p['шанс_1млн']}")
    print(f"Тринадцатый меридиан: {'да' if p['тринадцатый_меридиан'] else 'нет'}")
    print(f"Телосложение: {p['телосложение'] or 'нет'}")
    print(f"Уроки: {', '.join(p['уроки']) if p['уроки'] else 'нет'}")
    print(f"Навыки: {', '.join(p['навыки']) if p['навыки'] else 'нет'}")
    bought = p.get("предметы", [])
    if bought:
        print("Купленные предметы: " + ", ".join(
            f"{it['id']}" for it in bought))
    for name, nk in p["накопители"].items():
        print(f"Накопитель «{name}»: заряд {nk['заряд']}/{nk['макс']}, прогресс {fmt_num(nk['прогресс'])}%")
    print(f"Жизней прожито: {len(data['жизни'])}")
    for life in data["жизни"]:
        print("  " + json.dumps(life, ensure_ascii=False))
    if data["заметка"]:
        print(f"Заметка: {data['заметка']}")


def body_bonuses(p):
    if p["телосложение"] == "огненное (обычное)":
        return {"талант": 1, "макс_корней": 5, "мин_корней": 1}
    return {"талант": 0, "макс_корней": 0, "мин_корней": 0}


# ============================================================
# CLI
# ============================================================

def build_parser():
    parser = argparse.ArgumentParser(
        prog="kul-tyashka.py",
        description="Культяшка — оцифровка механик. Команды: help, roll, dc, event, continent, "
                    "chance, training, breakthrough, combat, character, shop, xp, stages, meridians.")
    parser.add_argument("--seed", type=int, default=None, help="зерно генератора случайных чисел")
    parser.add_argument("--version", action="version", version=f"kul-tyashka {VERSION}")
    sub = parser.add_subparsers(dest="command", required=True)

    # ---- help ----
    p = sub.add_parser("help", help="общая справка или справка по команде")
    p.add_argument("topic", nargs="?", help="команда/тема (roll, event, continent, combat, ...)")
    p.set_defaults(func=cmd_help)

    # ---- roll ----
    p = sub.add_parser("roll", help="обычный бросок костей")
    p.add_argument("expr", help="выражение, например «4d6+2d8+5» или «1d100+45-16»")
    p.add_argument("--comment", help="подпись броска")
    p.set_defaults(func=cmd_roll)

    # ---- dc ----
    p = sub.add_parser("dc", help="бросок с классом сложности (проверка)")
    p.add_argument("--roll", required=True, help="бросок с модификаторами, например «1d100+5»")
    p.add_argument("--ks", required=True, type=float, help="класс сложности")
    p.add_argument("--on-success", help="что происходит при успехе")
    p.add_argument("--on-failure", help="что происходит при провале")
    p.set_defaults(func=cmd_dc)

    # ---- event ----
    p = sub.add_parser("event", help="случайное событие: исход нужно указать для каждого КС")
    p.add_argument("--ks", help="классы сложности через слеш: «1/5/10/25/35/80/90/95/99/100»")
    p.add_argument("--outcomes", help="исходы через «;» в том же порядке, что и КС (неинтерактивный режим)")
    p.add_argument("--dice", default="1d100", help="бросок (по умолчанию 1d100)")
    p.set_defaults(func=cmd_event)

    # ---- continent ----
    p = sub.add_parser("continent", help="генерация и разбор континентов")
    cs = p.add_subparsers(dest="sub")
    q = cs.add_parser("generate", help="сгенерировать континент (бросок 1d10000000)")
    q.add_argument("--number", help="использовать заданный номер вместо броска")
    q.add_argument("--name", help="название континента (задаёт пользователь; иначе — спросит: терминал или строка на stdin)")
    q.add_argument("--save", action="store_true", help="сохранить <континент>/continent.json")
    q.add_argument("--no-super", action="store_true", help="не учитывать суперконтинент")
    q.set_defaults(func=cmd_continent_generate)
    q = cs.add_parser("parse", help="разобрать номер континента")
    q.add_argument("number", help="номер, например «3261701» или «3 261 701»")
    q.add_argument("--name", help="название континента (задаёт пользователь; иначе — спросит: терминал или строка на stdin)")
    q.add_argument("--save", action="store_true", help="сохранить <континент>/continent.json")
    q.add_argument("--no-super", action="store_true", help="не учитывать суперконтинент")
    q.set_defaults(func=cmd_continent_parse)
    q = cs.add_parser("list", help="список сохранённых континентов")
    q.set_defaults(func=cmd_continent_list)

    # ---- chance ----
    p = sub.add_parser("chance", help="шанс «один на миллиард/миллион»")
    p.add_argument("--level", type=int, default=0,
                   help="уровень улучшения: 0-2 — миллиард, 3-4 — миллион")
    p.set_defaults(func=cmd_chance)

    # ---- training ----
    p = sub.add_parser("training", help="тренировка открытия меридианов")
    p.add_argument("--talent", type=int, required=True, help="талант")
    p.add_argument("--age", type=int, required=True, help="возраст")
    p.add_argument("--qi", choices=["???", "дефицит", "баланс", "достаток", "изобилие"],
                   default="баланс", help="уровень Ци на континенте")
    p.add_argument("--resources", type=int, default=0, help="личные ресурсы")
    p.add_argument("--sect-resources", type=int, default=0, help="ресурсы секты")
    p.add_argument("--despair", action="store_true", help="отчаяние: скорость вдвое ниже")
    p.add_argument("--current", type=int, help="текущий прогресс меридиана")
    p.add_argument("--target", type=int, help="сложность текущего меридиана (10/20/30/.../120)")
    p.set_defaults(func=cmd_training)

    # ---- breakthrough ----
    p = sub.add_parser("breakthrough", help="попытка прорыва на стадию культивации")
    p.add_argument("stage", choices=["накопление", "фундамент", "ядро", "душа", "дао",
                                     "постигший", "отделение", "паломничество", "вознесение"])
    p.add_argument("--talent", type=int, required=True, help="талант")
    p.add_argument("--purity", type=int, required=True, help="чистота корней, %% ")
    p.add_argument("--bonus", default="", help="бонусы/штрафы к броску (выражение, напр. «2+5+5+5+5-16»)")
    p.add_argument("--method-req", type=int, help="требование метода к общей чистоте корней, %% ")
    p.add_argument("--element-share", type=int, help="доля стихийных корней, %% ")
    p.add_argument("--element-req", type=int, help="требование метода к стихийным корням, %% ")
    p.add_argument("--ladder", action="store_true", help="учтена «лестница в небо» (+1)")
    p.add_argument("--method", type=int, default=0, help="качество метода (0-100, допущение)")
    p.add_argument("--resources", type=int, default=0, help="ресурсы: формации, талисманы, алхимия (0-100)")
    p.add_argument("--sacrifices", type=int, default=0, help="жертвоприношения (0-60)")
    p.add_argument("--cracks", type=int, help="трещины основания (для Золотого Ядра)")
    p.add_argument("--thirteenth", action="store_true", help="скрытый 13-й меридиан открыт")
    p.add_argument("--tribulation-ks", type=int, help="свой КС финального преодоления испытания (по умолч. 50)")
    p.add_argument("--will-ks", type=int, help="свой КС проверки воли против внутреннего демона (по умолч. 30)")
    p.add_argument("--will-bonus", type=int, default=0, help="бонус к проверке воли (ВОЛЯ!, уроки)")
    p.add_argument("--demon-choice", choices=["resist", "yield"],
                   help="решение в разговоре с внутренним демоном (иначе — интерактивный вопрос)")
    p.add_argument("--boss-ks", type=int, help="КС стража в центре Краесветных гор (для вознесения, по умолч. 160)")
    p.add_argument("--journey-ks", type=int, help="КС переходов к центру Краесветных гор (по умолч. 150)")
    p.add_argument("--no-tribulation", action="store_true", help="не бросать испытание Небес")
    p.set_defaults(func=cmd_breakthrough)

    # ---- combat ----
    p = sub.add_parser("combat", help="боевые раунды и бои")
    cs = p.add_subparsers(dest="sub")
    q = cs.add_parser("round", help="один раунд боя")
    add_side_args(q, "attacker", "атакующий")
    add_side_args(q, "defender", "защищающийся")
    q.add_argument("--stage", choices=list(STAGE_COMBAT_BONUS.keys()), default="меридианы",
                   help="стадия защищающегося (для порога смерти)")
    q.add_argument("--stage-level", type=int, default=0,
                   help="уровень внутри стадии (для меридиан — число открытых меридианов)")
    q.set_defaults(func=cmd_combat_round)
    q = cs.add_parser("fight", help="бой до смерти (интерактивный)")
    add_side_args(q, "attacker", "атакующий")
    add_side_args(q, "defender", "защищающийся")
    q.add_argument("--stage", choices=list(STAGE_COMBAT_BONUS.keys()), default="меридианы")
    q.add_argument("--stage-level", type=int, default=0)
    q.add_argument("--max-rounds", type=int, default=30)
    q.set_defaults(func=cmd_combat_fight)

    # ---- character ----
    p = sub.add_parser("character", help="персонажи в json (ГГ — gg.json, прочие — <континент>/<имя>.json)")
    cs = p.add_subparsers(dest="sub")
    q = cs.add_parser("create", help="создать персонажа")
    q.add_argument("name", nargs="?", default="Ли Лун", help="имя")
    q.add_argument("--continent", help="континент (для не-ГГ)")
    q.add_argument("--gg", action="store_true", help="создать ГГ (gg.json в корне)")
    q.set_defaults(func=cmd_character_create)
    q = cs.add_parser("show", help="показать персонажа")
    q.add_argument("--name", help="имя")
    q.add_argument("--continent", help="континент")
    q.add_argument("--gg", action="store_true")
    q.set_defaults(func=cmd_character_show)
    q = cs.add_parser("add-xp", help="добавить очки")
    q.add_argument("xp", type=float)
    q.add_argument("--name"); q.add_argument("--continent"); q.add_argument("--gg", action="store_true")
    q.set_defaults(func=cmd_character_add_xp)
    q = cs.add_parser("reborn", help="переродиться: пол, происхождение, корни, талант, шанс")
    q.add_argument("--gender", choices=["auto", "м", "ж"], default="auto")
    q.add_argument("--origin-table", help="таблица происхождения: «30:варвар; 85:крестьяне; 100:горожане»")
    q.add_argument("--origin-table2", help="отдельная таблица для накопителя «горожане»")
    q.add_argument("--new-continent", action="store_true", help="сгенерировать новый континент")
    q.add_argument("--save-continent", action="store_true", help="сохранить новый континент в json")
    q.add_argument("--continent-name", help="название нового континента (задаёт пользователь; иначе — спросит)")
    q.add_argument("--name"); q.add_argument("--continent"); q.add_argument("--gg", action="store_true")
    q.set_defaults(func=cmd_character_reborn)
    q = cs.add_parser("nakopitel", help="накопитель: progress N | charge N | spend")
    q.add_argument("which", choices=["горожане", "окрестности_му", "великий_успех"])
    q.add_argument("action", choices=["progress", "charge", "spend"])
    q.add_argument("value", type=float, nargs="?", default=None)
    q.add_argument("--name"); q.add_argument("--continent"); q.add_argument("--gg", action="store_true")
    q.set_defaults(func=cmd_character_nakopitel)
    q = cs.add_parser("note", help="добавить заметку")
    q.add_argument("text")
    q.add_argument("--name"); q.add_argument("--continent"); q.add_argument("--gg", action="store_true")
    q.set_defaults(func=cmd_character_note)

    # ---- shop ----
    p = sub.add_parser("shop", help="магазин очков (ассортимент в shop.json, по умолчанию пуст)")
    cs = p.add_subparsers(dest="sub")
    q = cs.add_parser("list", help="показать ассортимент магазина (shop.json)")
    q.set_defaults(func=cmd_shop_list)
    q = cs.add_parser("lessons", help="каталог известных уроков (справочно)")
    q.set_defaults(func=cmd_shop_lessons)
    q = cs.add_parser("add", help="добавить предмет в магазин (shop.json)")
    q.add_argument("id", help="id предмета (или свой)")
    q.add_argument("--name", help="название (для нестандартного — обязательно)")
    q.add_argument("--cost", type=float, help="цена в XP (для нестандартного — обязательно); "
                   "для встроенных с динамической ценой можно переопределить")
    q.add_argument("--note", help="описание")
    q.set_defaults(func=cmd_shop_add)
    q = cs.add_parser("remove", help="убрать предмет из магазина")
    q.add_argument("id")
    q.set_defaults(func=cmd_shop_remove)
    q = cs.add_parser("buy", help="купить предмет (списывает XP у персонажа)")
    q.add_argument("item", help="id предмета (см. shop list)")
    q.add_argument("--lesson-name", help="название урока (для item=урок)")
    q.add_argument("--name"); q.add_argument("--continent"); q.add_argument("--gg", action="store_true")
    q.set_defaults(func=cmd_shop_buy)

    # ---- xp ----
    p = sub.add_parser("xp", help="подсчёт очков за прожитую жизнь")
    p.add_argument("--subtotal", type=float, help="подытог достижений (до множителей)")
    p.add_argument("--gender", help="пол: м / ж / м(выбор) / ж(выбор) / случайный(выбор)")
    p.add_argument("--fauna", help="фауна")
    p.add_argument("--qi", help="уровень Ци")
    p.add_argument("--politics", help="политика")
    p.add_argument("--morality", help="мораль")
    p.add_argument("--death", help="тип смерти")
    p.set_defaults(func=cmd_xp)

    # ---- stages / meridians ----
    q = sub.add_parser("stages", help="справочник стадий культивации")
    q.set_defaults(func=cmd_stages)
    q = sub.add_parser("meridians", help="справочник меридианов")
    q.set_defaults(func=cmd_meridians)

    return parser


def add_side_args(parser, prefix, label):
    parser.add_argument(f"--{prefix}-pool", type=int, help=f"пул кубов {label} (напр. 70)")
    parser.add_argument(f"--{prefix}-attack", type=str, default=None,
                        help="кубы в атаку (число, «половина», «все», «ничего»)")
    parser.add_argument(f"--{prefix}-defense", type=str, default=None,
                        help="кубы в защиту (по умолчанию пул минус атака)")
    parser.add_argument(f"--{prefix}-weapon", type=int, default=0, help="бонус оружия (нож=5, меч=10, копьё=5)")
    parser.add_argument(f"--{prefix}-training-atk", type=int, default=0, help="бонус тренировок атаки")
    parser.add_argument(f"--{prefix}-training-def", type=int, default=0, help="бонус тренировок защиты")
    parser.add_argument(f"--{prefix}-meridians", type=int, default=0, help="меридианы кубами (Nd6)")
    parser.add_argument(f"--{prefix}-flat-meridians", type=int, default=0,
                        help="плоский бонус меридианов (Накопление Ци / кольцо)")
    parser.add_argument(f"--{prefix}-extra-atk", type=str, default="", help="доп. бонус атаки («10x2d6»)")
    parser.add_argument(f"--{prefix}-extra-def", type=str, default="", help="доп. бонус защиты")
    parser.add_argument(f"--{prefix}-technique-atk", type=int, default=0, help="бонус техники атаки")
    parser.add_argument(f"--{prefix}-technique-def", type=int, default=0, help="барьер техники защиты")
    parser.add_argument(f"--{prefix}-passive", type=int, default=0, help="пассивная защита")
    parser.add_argument(f"--{prefix}-barrier", type=int, default=0, help="барьер Ци")
    parser.add_argument(f"--{prefix}-penalty", type=int, default=0, help="текущий штраф от ран")


def alloc_value(text, pool, what):
    if text is None:
        return pool // 2
    text = str(text).strip().lower()
    if text in ("половина", "пол", "half"):
        return pool // 2
    if text in ("все", "всё", "all"):
        return pool
    if text in ("ничего", "0", "none"):
        return 0
    try:
        v = int(text)
    except ValueError:
        raise SystemExit(f"не понял распределение «{text}» ({what})")
    if v < 0 or v > pool:
        raise SystemExit(f"распределение {v} вне пула {pool}")
    return v


def make_side(prefix, args):
    return Side(
        name=prefix,
        pool=args.__dict__.get(f"{prefix}_pool"),
        weapon=args.__dict__.get(f"{prefix}_weapon") or 0,
        training_atk=args.__dict__.get(f"{prefix}_training_atk") or 0,
        training_def=args.__dict__.get(f"{prefix}_training_def") or 0,
        meridians=args.__dict__.get(f"{prefix}_meridians") or 0,
        flat_meridians=args.__dict__.get(f"{prefix}_flat_meridians") or 0,
        extra_atk=args.__dict__.get(f"{prefix}_extra_atk") or "",
        extra_def=args.__dict__.get(f"{prefix}_extra_def") or "",
        technique_atk=args.__dict__.get(f"{prefix}_technique_atk") or 0,
        technique_def=args.__dict__.get(f"{prefix}_technique_def") or 0,
        passive=args.__dict__.get(f"{prefix}_passive") or 0,
        barrier=args.__dict__.get(f"{prefix}_barrier") or 0,
        penalty=args.__dict__.get(f"{prefix}_penalty") or 0,
    )


# ---- команды ----

def cmd_help(args):
    if not args.topic:
        print(OVERVIEW)
        return
    topic = args.topic
    parsers = {
        "roll": "roll", "dc": "dc", "event": "event", "continent": "continent",
        "chance": "chance", "training": "training", "breakthrough": "breakthrough",
        "combat": "combat", "character": "character", "shop": "shop", "xp": "xp",
        "stages": "stages", "meridians": "meridians",
    }
    if topic in parsers:
        # повторно строим парсер — проще напечатать справку вручную
        build_parser().parse_args([topic, "--help"])
    else:
        print(f"Неизвестная тема «{topic}». Темы: {', '.join(parsers)}.")


OVERVIEW = """\
КУЛЬТЯШКА — оцифровка механик. Быстрый обзор:

  roll <выражение>             обычные броски: «4d6+2d8+5», «1d100+45-16», «10x2d6»
  dc --roll ... --ks ...        бросок с классом сложности
  event                         случайное событие: исход указывается для каждого КС
  continent generate|parse|list генерация/разбор континентов (номер 1d10000000; 2 суперконтинента на квадрат); название задаёт пользователь
  chance                        «шанс один на миллиард/миллион»
  training                      тренировки открытия меридианов: (талант//10+2)d(талант)
  breakthrough <стадия>         прорывы; «один из тысячи» — испытания Небес (мир испытания, внутренний демон, ~50% для среднего)
  combat round|fight            бой: пулы кубов, раны, штрафы, порог 50+бонус стадии
  character create|show|reborn|add-xp|nakopitel|note   (reborn: таблица происхождения задаётся пользователем)
  shop list|add|remove|buy|lessons   магазин; ассортимент пользователь наполняет сам (shop.json)
  xp                            подсчёт очков за жизнь (достижения × множители)
  stages / meridians            справочники

Персонажи: ГГ — gg.json в корне; прочие — <континент>/<имя>.json.
«допущение» в выводе — правило, которого нет в тексте произведения.
Подробнее: help <команда> или <команда> --help.
"""


def cmd_roll(args):
    total, lines = roll_expr(args.expr)
    prefix = f"{{{args.comment}, " if args.comment else "{"
    print(prefix + f"бросок {' + '.join(lines)} = {total}}}")
    print(f"Итого: {total}")


def cmd_dc(args):
    total, lines = roll_expr(args.roll)
    print(f"{{Бросок {' + '.join(lines)} = {total}. КС {fmt_num(args.ks)}. "
          f"{'Успех' if total >= args.ks else 'Провал'} (разность {fmt_num(total - args.ks)}).}}")
    if total >= args.ks and args.on_success:
        print(f"Успех → {args.on_success}")
    if total < args.ks and args.on_failure:
        print(f"Провал → {args.on_failure}")


def cmd_event(args):
    ks = args.ks
    if not ks:
        ks = input("Классы сложности через слеш (например 1/5/10/25/35/80/90/95/99/100): ")
    ks_list = parse_ks(ks)
    outcomes = {}
    if args.outcomes:
        vals = [v.strip() for v in args.outcomes.split(";")]
        if len(vals) != len(ks_list):
            raise SystemExit(f"исходов {len(vals)}, а КС {len(ks_list)}")
        outcomes = {k: v for k, v in zip(ks_list, vals)}
    else:
        print("Укажите исход для каждого класса сложности (что произойдёт, если бросок <= КС):")
        for k in ks_list:
            outcomes[k] = input(f"  бросок <= {k}: ")
    run_event(ks_list, outcomes, args.dice)


def ask_continent_name(number):
    """Название континента задаёт пользователь — программа его не придумывает.

    Спрашиваем и в терминале, и когда название подано на stdin (канал/heredoc):
    характеристики уже напечатаны выше. Если stdin исчерпан, подсказываем номер —
    по нему те же характеристики воспроизводятся повторным запуском."""
    while True:
        try:
            name = input("Название континента: ").strip()
        except EOFError:
            raise SystemExit(
                "ошибка: название континента задаёт пользователь — укажите --name "
                "(в character reborn — --continent-name).\n"
                f"  Те же характеристики даёт номер {number}: "
                f"continent parse {number} --name «название»")
        if name:
            return name
        print("  (название не может быть пустым)")


def _continent_flow(number, save, no_super, name=None):
    parsed = parse_continent_number(number)
    supercont = None if no_super else supercontinent_for(parsed["номер"])
    if supercont:
        apply_supercontinent(parsed, supercont)
    # Сначала показываем характеристики и множители — название задаёт пользователь
    print_continent(parsed, supercont)
    final_name = name or ask_continent_name(parsed["номер"])
    print(f"[Название: {final_name}]")
    if save:
        data = {"название": final_name, **parsed, "суперконтинент": supercont}
        path = os.path.join(final_name, "continent.json")
        if os.path.exists(path):
            print(f"[ВНИМАНИЕ: {path} уже существует — сохранение пропущено]")
        else:
            save_char(path, data)
            print(f"[Сохранено: {path}]")
    return parsed, supercont, final_name


def cmd_continent_generate(args):
    number = args.number
    if not number:
        number = str(_rng.randrange(10_000_000)).zfill(7)
        print(f"{{Определение континента, бросок 1d10000000: {number}}}")
    _continent_flow(number, args.save, args.no_super, args.name)


def cmd_continent_parse(args):
    _continent_flow(args.number, args.save, args.no_super, args.name)


def cmd_continent_list(args):
    found = False
    for entry in sorted(os.listdir(".")):
        path = os.path.join(entry, "continent.json")
        if os.path.isdir(entry) and os.path.isfile(path):
            try:
                data = load_char(path)
                print(f"{data.get('название', entry)} — номер {data.get('номер_формат', '?')}")
                found = True
            except Exception:
                print(f"{entry} — повреждённый continent.json")
    if not found:
        print("Сохранённых континентов нет (используйте continent generate --save).")


def cmd_chance(args):
    chance_roll(args.level)


def cmd_training(args):
    run_training(args.talent, args.age, args.qi, args.resources, args.sect_resources,
                 args.despair, args.current, args.target)


def cmd_breakthrough(args):
    if args.stage == "накопление":
        breakthrough_nakoplenie(args.talent, args.purity, args.bonus, args.method_req,
                                args.element_share, args.element_req, args.ladder)
    elif args.stage == "вознесение":
        breakthrough_voznesenie(args.talent, args.purity, args.resources, args.method,
                                args.sacrifices, args.tribulation_ks, args.will_ks,
                                args.will_bonus, args.demon_choice, args.no_tribulation,
                                args.boss_ks, args.journey_ks)
    else:
        breakthrough_high(args.stage, args.talent, args.purity, args.method, args.resources,
                          args.sacrifices, args.cracks, args.thirteenth,
                          args.tribulation_ks, args.will_ks, args.will_bonus,
                          args.demon_choice, args.no_tribulation)


def _require_side(prefix, args):
    if args.__dict__.get(f"{prefix}_pool") is None:
        raise SystemExit(f"укажите --{prefix}-pool (пул кубов)")


def cmd_combat_round(args):
    _require_side("attacker", args)
    _require_side("defender", args)
    attacker = make_side("attacker", args)
    defender = make_side("defender", args)
    att_alloc = alloc_value(args.attacker_attack, attacker.pool, "атака")
    if args.defender_defense is None:
        def_alloc = defender.pool - alloc_value(args.defender_attack, defender.pool, "атака")
    else:
        def_alloc = alloc_value(args.defender_defense, defender.pool, "защита")
    resolve_round(attacker, defender, att_alloc, def_alloc, args.stage, args.stage_level)


def cmd_combat_fight(args):
    _require_side("attacker", args)
    _require_side("defender", args)
    attacker = make_side("attacker", args)
    defender = make_side("defender", args)
    if args.attacker_pool is None or args.defender_pool is None:
        raise SystemExit("для боя нужны --attacker-pool и --defender-pool")
    print(f"Бой: {attacker.name} против {defender.name} (пулы {attacker.pool}/{defender.pool}, "
          f"стадия {args.stage}, уровень {args.stage_level})")
    for rnd in range(1, args.max_rounds + 1):
        print(f"\n--- Раунд {rnd} ---")
        try:
            a = input(f"Кубы {attacker.name} в атаку (пул {attacker.pool}, Enter = половина): ").strip()
            d = input(f"Кубы {defender.name} в атаку (пул {defender.pool}, Enter = половина): ").strip()
        except EOFError:
            print("(ввод окончен — бой прерван)")
            return
        att_alloc = alloc_value(a or None, attacker.pool, "атака")
        def_att = alloc_value(d or None, defender.pool, "атака")
        def_def = defender.pool - def_att
        # обе стороны атакуют одновременно
        r1 = resolve_round(attacker, defender, att_alloc, def_def, args.stage, args.stage_level)
        if "убита" in r1["исход"] or "обезврежена" in r1["исход"]:
            print("[Поздравляем, вы умерли!]" if "убита" in r1["исход"] else
                  f"[{defender.name} выведен(а) из строя]")
            return
        att_def = attacker.pool - att_alloc
        r2 = resolve_round(defender, attacker, def_att, att_def, args.stage, args.stage_level)
        if "убита" in r2["исход"] or "обезврежена" in r2["исход"]:
            print("[Поздравляем, вы умерли!]" if "убита" in r2["исход"] else
                  f"[{attacker.name} выведен(а) из строя]")
            return
    print(f"Бой не завершён за {args.max_rounds} раундов.")


def cmd_character_create(args):
    path = char_file(args)
    if os.path.exists(path):
        raise SystemExit(f"{path} уже существует")
    cont = None if (args.gg or not args.continent) else args.continent
    save_char(path, default_char(args.name, cont))
    print(f"[Персонаж создан: {path}]")


def _load_for(args):
    path = char_file(args)
    if not os.path.exists(path):
        raise SystemExit(f"{path} не найден (character create)")
    return path, load_char(path)


def cmd_character_show(args):
    path, data = _load_for(args)
    show_char(data)


def cmd_character_add_xp(args):
    path, data = _load_for(args)
    data["баланс_xp"] += args.xp
    save_char(path, data)
    print(f"[Баланс {data['имя']}: {fmt_xp(data['баланс_xp'])} XP]")


def cmd_character_note(args):
    path, data = _load_for(args)
    data["заметка"] = (data["заметка"] + "\n" if data["заметка"] else "") + args.text
    save_char(path, data)
    print(f"[Заметка добавлена: {path}]")


def cmd_character_nakopitel(args):
    path, data = _load_for(args)
    nk = data["покупки"]["накопители"][args.which]
    if args.action == "progress":
        v = args.value if args.value is not None else 0
        nk["прогресс"] += v
        while nk["прогресс"] >= 100 and nk["заряд"] < nk["макс"]:
            nk["прогресс"] -= 100
            nk["заряд"] += 1
        if nk["прогресс"] >= 100:
            nk["прогресс"] = 100.0
    elif args.action == "charge":
        v = int(args.value or 1)
        nk["заряд"] = min(nk["макс"], nk["заряд"] + v)
    else:  # spend
        nk["заряд"] = max(0, nk["заряд"] - int(args.value or 1))
    save_char(path, data)
    print(f"[Накопитель «{args.which}»: заряд {nk['заряд']}/{nk['макс']}, прогресс {fmt_num(nk['прогресс'])}%]")


def cmd_character_reborn(args):
    path, data = _load_for(args)
    p = data["покупки"]
    body = body_bonuses(p)
    life_no = len(data["жизни"]) + 1
    print(f"\n=== Перерождение {life_no} ({data['имя']}) ===")

    # Пол
    if p["выбор_пола"] and args.gender == "auto":
        g = input("Пол (м/ж): ").strip().lower()
        while g not in ("м", "ж"):
            g = input("Пол (м/ж): ").strip().lower()
        gender = g
    elif p["выбор_пола"]:
        gender = args.gender
    else:
        gender = "ж" if roll_die(2) == 1 else "м"
        print(f"{{Выбор пола, 1 - девочка, 2 - мальчик, 1d2: {'1' if gender == 'ж' else '2'}. "
              f"{'Девочка' if gender == 'ж' else 'Мальчик'}.}}")
    mult = {"м": "1", "ж": "1,5"}[gender] if not p["выбор_пола"] else \
        {"м": "0,8", "ж": "1,2"}[gender]
    print(f"(множитель пола: ×{mult})")

    # Происхождение: варианты исходов и их КС определяет пользователь
    d = roll_die(100)
    nak = p["накопители"]["горожане"]
    table1 = parse_origin_table(args.origin_table) if args.origin_table else None
    table2 = parse_origin_table(args.origin_table2) if args.origin_table2 else None
    if table1 is None and sys.stdin.isatty():
        s = input("Таблица происхождения (КС:исход через «;», например "
                  "«30:варвар/приграничье; 85:крестьяне; 100:горожане и выше»): ").strip()
        if s:
            table1 = parse_origin_table(s)
    if table1 is None:
        table1 = [(30, "варвар / житель приграничья"), (85, "крестьяне"),
                  (100, "горожане и выше")]
    used_nakopitel = False
    if nak["заряд"] > 0:
        ans = input(f"Использовать заряд накопителя «горожане»? (д/н): ").strip().lower() \
            if sys.stdin.isatty() else "н"
        if ans in ("д", "y", "yes", "да"):
            nak["заряд"] -= 1
            used_nakopitel = True
    if used_nakopitel and table2 is not None:
        ks, origin = origin_outcome(table2, d)
        if origin is None:
            origin = f"выше максимального КС {table2[-1][0]} — исход не определён"
    elif used_nakopitel:
        # без отдельной таблицы накопитель гарантирует верхний исход
        origin = table1[-1][1]
    else:
        ks, origin = origin_outcome(table1, d)
        if origin is None:
            origin = f"выше максимального КС {table1[-1][0]} — исход не определён"
    print(f"{{Социальное происхождение, 1d100: {d}. {origin.capitalize()}.}}")

    # Корни
    max_purity_bonus = p["корни"]["макс"] + body["макс_корней"]
    r = roll_die(max(1, 100 - max_purity_bonus)) if max_purity_bonus < 100 else 100
    max_purity = r + max_purity_bonus
    min_bonus = p["корни"]["мин"] + body["мин_корней"]
    min_bonus = min(min_bonus, max_purity)
    purity = max_purity if max_purity == min_bonus else roll_die(max(1, max_purity - min_bonus)) + min_bonus
    print(f"{{Максимальная чистота корней, 1d{max(1, 100 - max_purity_bonus)} + {max_purity_bonus}: {max_purity}}}")
    print(f"{{Чистота корней, 1d{max(1, max_purity - min_bonus)} + {min_bonus}: {purity}}}")

    # Талант
    t = p["талант"]
    if t["грань"] == 0:
        talent = 0
        print("{{Талант, 0. Культивация невозможна.}}")
    else:
        rolls = roll_dice(t["кости"], t["грань"])
        talent = sum(rolls) + t["бонус"] + body["талант"]
        flat = f" + {t['бонус']}" if t["бонус"] else ""
        flat_body = f" + {body['талант']} (телосложение)" if body["талант"] else ""
        print(f"{{Талант, бросок {t['кости']}d{t['грань']}{flat}{flat_body}: "
              f"[{','.join(map(str, rolls))}]{flat}{flat_body} = {talent}}}")
    print(f"[Талант: {talent}]")

    # Стихии корней
    elements = {}
    if purity > 0:
        n_el = roll_die(4) + 1
        left = purity
        shares = []
        for i in range(n_el):
            if i == n_el - 1:
                if left > 0:
                    shares.append(left)
            else:
                v = roll_die(max(1, left - (n_el - i - 1)))
                shares.append(v)
                left -= v
                if left <= 0:
                    break
        order = ELEMENTS[:]
        _rng.shuffle(order)
        for i, el in enumerate(order[:len(shares)]):
            elements[el] = shares[i]
        print(f"{{Стихии корней (всего {len(shares)}): " +
              ", ".join(f"{el} {v}" for el, v in sorted(elements.items(), key=lambda kv: -kv[1])) + "}}")
        if p["телосложение"] == "огненное (обычное)":
            mx = max(elements, key=elements.get)
            if mx != "Огонь":
                elements["Огонь"], elements[mx] = elements[mx], elements["Огонь"]
                print("  (огненное телосложение: Огонь становится преобладающей стихией)")

    # Шанс
    lvl = p["шанс_1млн"] + 3 if p["шанс_1млн"] else p["шанс_1млрд"]
    chance_ok = chance_roll(lvl)

    # Континент
    cont = data["континент"]
    if args.new_continent:
        number = str(_rng.randrange(10_000_000)).zfill(7)
        print(f"{{Определение континента, бросок 1d10000000: {number}}}")
        parsed, supercont, name = _continent_flow(number, args.save_continent, False,
                                                  args.continent_name)
        cont = name
        data["континент"] = cont
    elif cont:
        print(f"(континент: {cont})")

    # Младенческая смертность
    infant_death = False
    if not p["младенческая_смертность"]:
        d = roll_die(100)
        print(f"{{Без защиты от младенческой смертности: 30% шанс умереть до 5 лет. 1d100: {d}.}}")
        if d <= 30:
            infant_death = True
            print("[Жизнь оборвалась в младенчестве от болезни. Купите отмену младенческой смертности!]")

    # Накопители: прогресс за прожитую жизнь
    if "путь наверх" in p["уроки"]:
        p["накопители"]["горожане"]["прогресс"] += 20
    if "окрестности суперконтинента Му" in p["уроки"]:
        p["накопители"]["окрестности_му"]["прогресс"] += 1
    for nk_name, nk in p["накопители"].items():
        while nk["прогресс"] >= 100 and nk["заряд"] < nk["макс"]:
            nk["прогресс"] -= 100
            nk["заряд"] += 1
            print(f"[Накопитель «{nk_name}» полностью заряжен: {nk['заряд']}/{nk['макс']}]")

    # Заряд великого успеха
    gu = p["накопители"]["великий_успех"]
    gu_age = None
    if gu["заряд"] > 0 and input(f"Использовать заряд «великий успех»? (д/н): ").strip().lower() in ("д", "y", "yes", "да"):
        gu["заряд"] -= 1
        gu_age = input("Возраст события (5/10/15): ").strip() or "10"
        print(f"[Заряд великого успеха потрачен: случайное событие в {gu_age} лет станет критическим успехом]")

    data["жизни"].append({
        "номер": life_no,
        "пол": gender,
        "происхождение": origin,
        "происхождение_таблица": table1,
        "чистота_корней": purity,
        "талант": talent,
        "стихии": elements,
        "континент": cont,
        "шанс_один_на_миллиард": "успех" if chance_ok else "провал",
        "великий_успех_возраст": gu_age,
        "смерть": "младенчество (болезнь)" if infant_death else None,
    })
    save_char(path, data)
    print(f"\n[Жизнь {life_no} записана в {path}. Итоговые очки — командой xp, затем character add-xp.]")


def cmd_shop_list(args):
    shop = load_shop()
    items = shop.get("предметы", [])
    if not items:
        print("Магазин пуст (shop.json). Добавьте товары: shop add <id> [--name ...] [--cost ...]")
        print("Встроенные id (цена и эффект известны программе):")
        print("  " + ", ".join(sorted(KNOWN_ITEMS)))
        print("Нестандартные предметы: shop add <id> --name «...» --cost N — покупка просто спишет очки.")
        return
    for it in items:
        if it.get("динамическая"):
            price = "динамическая"
        else:
            price = fmt_xp(it.get("цена", 0))
        print(f"  {it['id']:<28} {str(price):<14} {it.get('название', '')}")


def cmd_shop_lessons(args):
    print("Каталог известных уроков (справочно; в магазин добавляются через shop add):")
    for name, cost, desc in LESSONS_CATALOG:
        print(f"  {name:<48} {cost:<16} {desc}")


def cmd_shop_add(args):
    shop = load_shop()
    if shop_find(shop, args.id):
        raise SystemExit(f"предмет «{args.id}» уже в магазине")
    if args.id in KNOWN_ITEMS:
        kn, kc = KNOWN_ITEMS[args.id]
        name = args.name or kn
        if kc is None and args.cost is None:
            item = {"id": args.id, "название": name, "динамическая": True,
                    "описание": args.note or ""}
        else:
            cost = args.cost if args.cost is not None else kc
            item = {"id": args.id, "название": name, "цена": cost, "описание": args.note or ""}
    else:
        if not args.name or args.cost is None:
            raise SystemExit("для нестандартного предмета укажите --name и --cost")
        item = {"id": args.id, "название": args.name, "цена": args.cost,
                "описание": args.note or ""}
    shop.setdefault("предметы", []).append(item)
    save_shop(shop)
    price = "динамическая цена" if item.get("динамическая") else f"за {fmt_xp(item['цена'])} XP"
    print(f"[Добавлено в {SHOP_FILE}: {args.id} — {item['название']} ({price})]")


def cmd_shop_remove(args):
    shop = load_shop()
    items = shop.get("предметы", [])
    kept = [it for it in items if it["id"] != args.id]
    if len(kept) == len(items):
        raise SystemExit(f"предмета «{args.id}» нет в магазине")
    shop["предметы"] = kept
    save_shop(shop)
    print(f"[Убрано из магазина: {args.id}]")


def cmd_shop_buy(args):
    shop = load_shop()
    entry = shop_find(shop, args.item)
    if entry is None:
        raise SystemExit(f"предмета «{args.item}» нет в магазине (shop add {args.item} ...)")
    path, data = _load_for(args)
    p = data["покупки"]
    p.setdefault("предметы", [])
    item = args.item
    if entry.get("динамическая"):
        cost = dynamic_cost(item, p)
    else:
        cost = entry["цена"]

    if item == "талант_грань":
        t = p["талант"]
        new = 1 if t["грань"] == 0 else t["грань"] + 1
        if new > 20:
            print("  (внимание: выше 1d20 — только после первого достижения стадии Накопления Ци)")
        t["грань"] = new
    elif item == "талант_кость":
        t = p["талант"]
        n = t["кости"]
        if t["грань"] < (n + 1) ** 2:
            raise SystemExit(f"требование: грань ≥ {(n + 1) ** 2} (сейчас {t['грань']})")
        t["кости"] = n + 1
    elif item == "талант_бонус":
        t = p["талант"]
        limit = 5 if p["телосложение"] == "огненное (обычное)" else 4
        if t["бонус"] >= limit:
            raise SystemExit(f"плоский бонус уже максимален (+{limit})")
        t["бонус"] += 1
    elif item == "корни_макс":
        r = p["корни"]
        if r["макс"] >= 100:
            raise SystemExit("максимум уже 100%")
        r["макс"] = min(100, r["макс"] + 5)
    elif item == "корни_мин":
        r = p["корни"]
        if r["мин"] >= r["макс"]:
            raise SystemExit("минимальная чистота достигла максимума")
        r["мин"] += 1
    elif item == "младенческая_смертность":
        if p["младенческая_смертность"]:
            raise SystemExit("уже куплено")
        p["младенческая_смертность"] = True
    elif item == "выбор_пола":
        if p["выбор_пола"]:
            raise SystemExit("уже куплено")
        p["выбор_пола"] = True
    elif item == "шанс_1млрд":
        lv = p["шанс_1млрд"]
        if lv >= 3:
            raise SystemExit("максимум (после этого — «один на миллион»)")
        p["шанс_1млрд"] = lv + 1
    elif item == "шанс_1млн":
        lv = p["шанс_1млн"]
        if lv >= 2:
            raise SystemExit("максимум (9 из 100)")
        p["шанс_1млн"] = lv + 1
    elif item == "лестница_в_небо":
        if p["лестница_в_небо"]:
            raise SystemExit("уже куплено")
        p["лестница_в_небо"] = True
    elif item == "травничество":
        if p["травничество"]:
            raise SystemExit("уже куплено")
        p["травничество"] = True
    elif item == "тринадцатый_меридиан":
        if p["тринадцатый_меридиан"]:
            raise SystemExit("уже куплено")
        p["тринадцатый_меридиан"] = True
    elif item == "урок":
        name = args.lesson_name or input("Название урока: ").strip()
        p["уроки"].append(name)
        if name in ("путь наверх", "окрестности суперконтинента Му", "великий успех"):
            print("  (накопитель появился в «покупках» персонажа)")
    elif item in ("великий_успех", "в_память_о_цао", "зеркало_славы", "работа_над_ошибками"):
        p["уроки"].append(item)
    elif item == "заряд_великого_успеха":
        nk = p["накопители"]["великий_успех"]
        if nk["заряд"] >= nk["макс"]:
            raise SystemExit("накопитель уже полон")
        nk["заряд"] += 1
    elif item == "телосложение_огня":
        if p["телосложение"]:
            raise SystemExit(f"уже есть телосложение ({p['телосложение']})")
        p["телосложение"] = "огненное (обычное)"
    elif item == "очищение_телосложения":
        if not p["телосложение"]:
            raise SystemExit("телосложения нет — очищать нечего")
        p["телосложение"] = None
    elif item == "метод_пылающего_козла":
        p["уроки"].append("метод Пылающего Козла (фолиант)")
    elif item in SKILL_ITEMS:
        if item in p["навыки"]:
            raise SystemExit("навык уже куплен")
        p["навыки"].append(item)
    else:
        # нестандартный предмет из shop.json: просто записываем покупку
        p["предметы"].append({"id": item, "название": entry.get("название", item),
                              "цена": cost, "описание": entry.get("описание", "")})

    if data["баланс_xp"] < cost:
        raise SystemExit(f"не хватает очков: нужно {fmt_xp(cost)}, есть {fmt_xp(data['баланс_xp'])}")
    data["баланс_xp"] -= cost
    save_char(path, data)
    print(f"[Куплено: {item} за {fmt_xp(cost)} XP. Остаток: {fmt_xp(data['баланс_xp'])} XP]"
          + (f" (всего платных уроков: {len(p['уроки'])})" if item == "урок" else ""))


# Множители очков
GENDER_MULT = {"м": 1.0, "ж": 1.5, "м(выбор)": 0.8, "ж(выбор)": 1.2, "случайный(выбор)": 1.0}
DEATH_MULT = {
    "позорная": 0.7, "заурядная насильственная": 1.0, "ритуалистическая": 1.1,
    "немного героическая": 1.2, "естественная от старости": 1.3, "съеденная душа": 1.2,
}


def _mult_of(table, key):
    for lo, hi, name, mult, note in table:
        if name == key:
            return mult
    return None


def cmd_xp(args):
    subtotal = args.subtotal
    if subtotal is None:
        subtotal = float(input("Подытог достижений (сумма XP до множителей): ").replace(",", "."))
    gender = args.gender or _choose("Пол", list(GENDER_MULT.keys()))
    fauna = args.fauna or _choose("Фауна", [f[2] for f in FAUNA])
    qi = args.qi or _choose("Уровень Ци", [f[2] for f in QI])
    politics = args.politics or _choose("Политика", [f[2] for f in POLITICS])
    morality = args.morality or _choose("Мораль", [f[2] for f in MORALITY])
    death = args.death or _choose("Тип смерти", list(DEATH_MULT.keys()))
    m = GENDER_MULT[gender] * _mult_of(FAUNA, fauna) * _mult_of(QI, qi) * 1.0 \
        * _mult_of(POLITICS, politics) * _mult_of(MORALITY, morality) * DEATH_MULT[death]
    total = subtotal * m
    print(f"{{Итог: {fmt_num(subtotal)} × {fmt_num(GENDER_MULT[gender])} × "
          f"{fmt_num(_mult_of(FAUNA, fauna))} × {fmt_num(_mult_of(QI, qi))} × 1 (размер) × "
          f"{fmt_num(_mult_of(POLITICS, politics))} × {fmt_num(_mult_of(MORALITY, morality))} × "
          f"{fmt_num(DEATH_MULT[death])} = {fmt_xp(total)} XP}}")
    print(f"Баланс за жизнь: {fmt_xp(total)} XP (добавить персонажу: character add-xp {fmt_xp(total)})")


def _choose(prompt, options):
    print(f"{prompt}:")
    for i, o in enumerate(options, 1):
        print(f"  {i}. {o}")
    while True:
        try:
            s = input("> ").strip()
            if s.isdigit() and 1 <= int(s) <= len(options):
                return options[int(s) - 1]
            if s in options:
                return s
        except (KeyboardInterrupt, EOFError):
            raise SystemExit(1)
        print("Не понял, попробуйте ещё раз.")


def cmd_stages(args):
    print("Стадии культивации:")
    for name, how, life, limit, trib, note in STAGES:
        flags = []
        if limit:
            flags.append("великий предел")
        if trib:
            flags.append("испытание Небес")
        print(f"\n  {name}" + (f"  [{', '.join(flags)}]" if flags else ""))
        print(f"    Переход: {how}")
        print(f"    Срок жизни: {life}")
        print(f"    Примечание: {note}")
    print("\nПорог смерти в бою: разность ≥ 50 + бонус стадии.")
    print("  Открытия Меридианов: 50 + открытые меридианы (подтверждено).")
    print("  Накопление Ци: 50 + 20 + слой (подтверждено).")
    print("  Далее — допущения: Фундамент 50+40+5·пластина, Ядро 50+80+5·заполнение, "
          "Душа 170, Дао 210, Постигший 230, Отделение 250, Паломничество 290, Император 1050.")
    print("Прорыв Накопления Ци: КС = 10 / e^(-10 + 0,1·чистота). Пороговая чистота 77%.")
    print("Испытание Небес («один из тысячи» — Ядро, Постигший Дао, Вознесение):")
    print("  1. переход в мир испытания; 2. разговор с внутренним демоном (проверка воли);")
    print("  3. финальное преодоление: 1d100 + модификаторы против КС 50 — калибровано так,")
    print("     что средний культиватор, удовлетворяющий требованиям прорыва (талант 100,")
    print("     чистота корней 90%, обычный метод, без ресурсов), проходит с шансом ~50%.")
    print("Вознесение: паломничество к центру Краесветных гор, в конце — босс, "
          "мистический зверь (КС 160), затем испытание Небес.")


def cmd_meridians(args):
    print("Меридианы (сложность растёт в арифметической прогрессии, сумма 780):")
    for i, (name, diff) in enumerate(MERIDIANS, 1):
        print(f"  {i:>2}. {name:<16} сложность {diff}")
    print("  Скрытый 13-й меридиан — обнаруживается только после открытия всех 12 "
          "(или покупкой за 10000 XP).")
    print("Каждый меридиан: +1d6 к боевым броскам; каждые два — +5 лет жизни.")
    print("Тренировка: (талант//10 + 2)d(талант) + модификаторы Ци (+2 изобилие, 0 баланс, "
          "-2 дефицит).")
    print("Расписание: ежегодно 8–20 лет, раз в 5 лет до 50, раз в 10 лет до 70, дальше — нет.")


def main(argv=None):
    parser = build_parser()
    args = parser.parse_args(argv)
    set_seed(args.seed)
    try:
        args.func(args)
    except KeyboardInterrupt:
        print("\nПрервано.")
        sys.exit(1)


if __name__ == "__main__":
    main()
