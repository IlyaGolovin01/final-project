"""База знаний: загрузка FAQ из JSON и нечёткий поиск по свободному тексту.

Поиск намеренно сделан без внешних NLP-библиотек: нормализация текста,
грубый стемминг русских окончаний, совпадение по ключевым словам и
похожесть строк из стандартного difflib. Этого достаточно для FAQ
интернет-магазина и не требует ничего устанавливать.
"""

from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass, field
from difflib import SequenceMatcher
from typing import Any

import config

log = logging.getLogger(__name__)


# --------------------------------------------------------------------------- #
#  Нормализация текста                                                        #
# --------------------------------------------------------------------------- #
STOPWORDS = {
    "а", "бы", "был", "была", "было", "были", "быть", "в", "вам", "вас", "весь",
    "во", "вот", "все", "всё", "вы", "да", "для", "до", "его", "ее", "её", "если",
    "есть", "еще", "ещё", "же", "за", "и", "из", "или", "им", "как", "который",
    "меня", "мне", "мной", "мой", "моя", "мы", "на", "над", "нам", "нас", "не",
    "него", "нее", "неё", "нет", "ни", "но", "о", "об", "он", "она", "они", "от",
    "по", "под", "при", "про", "с", "себя", "со", "так", "также", "там", "то",
    "тоже", "у", "уже", "что", "чтобы", "эта", "эти", "это", "этот", "я",
    "здравствуйте", "привет", "добрый", "день", "вечер", "утро", "пожалуйста",
    "скажите", "подскажите", "хочу", "нужно", "надо", "можно", "мог", "могу",
    "спасибо", "ваш", "ваша", "ваши", "тут", "здесь", "очень", "просто",
}

_SUFFIXES = (
    "иваешь", "ываешь", "ирующ", "ующ", "ающ", "ывш", "ившись",
    "ами", "ями", "ого", "его", "ому", "ему", "ыми", "ими",
    "ешь", "ишь", "ете", "ите", "ают", "яют", "ует", "уют", "ился", "илась",
    "ая", "яя", "ое", "ее", "ые", "ие", "ой", "ей", "ый", "ий", "ом", "ем",
    "ах", "ях", "ов", "ев", "ии", "ия", "ию", "ье", "ья", "ул", "ил", "ал",
    "ла", "ло", "ли", "ть", "ся",
    "у", "ю", "а", "я", "ы", "и", "о", "е", "ь", "й",
)
_SUFFIXES_SORTED = tuple(sorted(set(_SUFFIXES), key=len, reverse=True))

_PUNCT_RE = re.compile(r"[^0-9a-zа-я\s]+")
_SPACE_RE = re.compile(r"\s+")


def normalize(text: str) -> str:
    """Приводит текст к нижнему регистру без пунктуации и лишних пробелов."""
    text = (text or "").lower().replace("ё", "е").replace("_", " ")
    text = _PUNCT_RE.sub(" ", text)
    return _SPACE_RE.sub(" ", text).strip()


def stem(word: str) -> str:
    """Очень грубый стемминг: отрезает частые русские окончания."""
    if len(word) <= 4:
        return word
    for suffix in _SUFFIXES_SORTED:
        if word.endswith(suffix) and len(word) - len(suffix) >= 4:
            return word[: -len(suffix)]
    return word


def stems(text: str) -> set[str]:
    """Множество основ значимых слов текста."""
    result = set()
    for word in normalize(text).split():
        if word in STOPWORDS or len(word) < 3:
            continue
        result.add(stem(word))
    return result


# --------------------------------------------------------------------------- #
#  Модель данных                                                              #
# --------------------------------------------------------------------------- #
@dataclass
class Category:
    id: str
    title: str
    emoji: str = "•"
    dept: str = config.DEFAULT_DEPARTMENT

    @property
    def label(self) -> str:
        return f"{self.emoji} {self.title}".strip()


@dataclass
class Item:
    id: str
    cat: str
    question: str
    answer: str
    dept: str = config.DEFAULT_DEPARTMENT
    keywords: list[str] = field(default_factory=list)

    # предрассчитанные для поиска поля
    _q_norm: str = ""
    _q_stems: set[str] = field(default_factory=set)
    _kw_phrases: list[str] = field(default_factory=list)
    _kw_stems: set[str] = field(default_factory=set)

    def prepare(self) -> None:
        self._q_norm = normalize(self.question)
        self._q_stems = stems(self.question)
        self._kw_phrases = []
        self._kw_stems = set()
        for raw in self.keywords:
            norm = normalize(raw)
            if not norm:
                continue
            if " " in norm:
                self._kw_phrases.append(norm)
            self._kw_stems |= stems(norm)

    @property
    def short_question(self) -> str:
        """Обрезанный вопрос для подписи кнопки (лимит Telegram — 64 символа)."""
        text = self.question
        return text if len(text) <= 58 else text[:57].rstrip() + "…"


@dataclass
class Match:
    item: Item
    score: float


class KnowledgeBase:
    def __init__(self) -> None:
        self.categories: dict[str, Category] = {}
        self.items: dict[str, Item] = {}
        self.version: str = "—"
        self._placeholders: dict[str, str] = {}

    # ---------------------------------------------------------------- загрузка
    def load(self, path=None) -> None:
        path = path or config.FAQ_PATH
        with open(path, "r", encoding="utf-8") as fp:
            raw: dict[str, Any] = json.load(fp)

        self.version = str(raw.get("version", "—"))
        self.categories = {}
        for entry in raw.get("categories", []):
            cat = Category(
                id=str(entry["id"]),
                title=str(entry.get("title", entry["id"])),
                emoji=str(entry.get("emoji", "•")),
                dept=str(entry.get("dept", config.DEFAULT_DEPARTMENT)),
            )
            self.categories[cat.id] = cat

        self.items = {}
        for entry in raw.get("items", []):
            item = Item(
                id=str(entry["id"]),
                cat=str(entry.get("cat", "")),
                question=str(entry.get("q", "")).strip(),
                answer=str(entry.get("a", "")).strip(),
                dept=str(entry.get("dept") or self._cat_dept(entry.get("cat"))),
                keywords=[str(k) for k in entry.get("kw", [])],
            )
            item.prepare()
            self.items[item.id] = item

        self._placeholders = {
            "{shop}": config.SHOP_NAME,
            "{site}": config.SHOP_SITE,
            "{phone}": config.SUPPORT_PHONE,
            "{email}": config.SUPPORT_EMAIL,
            "{hours}": config.WORKING_HOURS,
            "{sla}": str(config.SLA_HOURS),
        }
        log.info(
            "База знаний загружена: %d категорий, %d вопросов (версия %s)",
            len(self.categories),
            len(self.items),
            self.version,
        )

    def _cat_dept(self, cat_id: Any) -> str:
        cat = self.categories.get(str(cat_id or ""))
        return cat.dept if cat else config.DEFAULT_DEPARTMENT

    # ------------------------------------------------------------------ доступ
    def render(self, text: str) -> str:
        """Подставляет контакты магазина вместо плейсхолдеров."""
        for key, value in self._placeholders.items():
            text = text.replace(key, value)
        return text

    def get(self, item_id: str) -> Item | None:
        return self.items.get(item_id)

    def category(self, cat_id: str) -> Category | None:
        return self.categories.get(cat_id)

    def items_of(self, cat_id: str) -> list[Item]:
        return [i for i in self.items.values() if i.cat == cat_id]

    def ordered_categories(self) -> list[Category]:
        return list(self.categories.values())

    # ------------------------------------------------------------------- поиск
    def score(self, query_norm: str, query_stems: set[str], item: Item) -> float:
        """Оценка релевантности вопроса запросу в диапазоне 0..1."""
        if not query_norm:
            return 0.0

        # 1. Точное вхождение ключевой фразы — самый сильный сигнал.
        phrase_hit = 0.0
        for phrase in item._kw_phrases:
            if phrase in query_norm:
                phrase_hit = 1.0
                break

        # 2. Доля ключевых слов, найденных в запросе.
        kw_score = 0.0
        if item._kw_stems and query_stems:
            hits = len(query_stems & item._kw_stems)
            if hits:
                # нормируем на «сколько вообще могло совпасть»
                denominator = min(len(item._kw_stems), max(2, len(query_stems)))
                kw_score = min(1.0, hits / denominator)

        # 3. Пересечение с самим текстом вопроса.
        overlap = 0.0
        if query_stems and item._q_stems:
            overlap = len(query_stems & item._q_stems) / len(query_stems)

        # 4. Похожесть строк целиком — ловит опечатки и перефразировки.
        seq = SequenceMatcher(None, query_norm, item._q_norm).ratio()

        score = 0.42 * kw_score + 0.26 * overlap + 0.18 * seq + 0.14 * phrase_hit

        # Короткие запросы из одного слова легко дают ложные срабатывания
        # по seq-похожести — слегка придерживаем их.
        if len(query_stems) <= 1 and phrase_hit == 0.0 and kw_score == 0.0:
            score *= 0.6

        return round(min(1.0, score), 4)

    def search(self, query: str, limit: int | None = None) -> list[Match]:
        """Возвращает совпадения, отсортированные по убыванию релевантности."""
        limit = limit or max(config.FAQ_MAX_SUGGESTIONS, 5)
        query_norm = normalize(query)
        query_stems = stems(query)
        matches = [
            Match(item=item, score=self.score(query_norm, query_stems, item))
            for item in self.items.values()
        ]
        matches.sort(key=lambda m: (-m.score, m.item.id))
        return [m for m in matches if m.score > 0][:limit]

    # ------------------------------------------- определение отдела по тексту
    # Маркеры внутри одного списка не должны быть вложены друг в друга:
    # «оплат» внутри «плат» давало одному слову двойной вес. Голых цифр
    # («500», «404») тоже избегаем — они встречаются в ценах и суммах.
    # Пересечение между отделами допустимо и иногда полезно: «промокод»
    # содержит «код», счёт становится равным, и заявка уходит в отдел
    # продаж — как и должно быть.
    IT_MARKERS = (
        "сайт", "ошибк", "плат", "карт", "списал", "списан", "чек", "кабинет",
        "пароль", "вход", "войти", "логин", "смс", "код", "корзин", "браузер",
        "приложен", "зависа", "глюч", "виснет", "не работает", "не грузит",
        "не открыва", "не проход", "баг", "сервер",
    )
    SALES_MARKERS = (
        "товар", "заказ", "доставк", "возврат", "обмен", "гарант", "брак",
        "цен", "скидк", "промокод", "бонус", "балл", "склад",
        "наличи", "курьер", "посылк", "накладн", "размер", "цвет", "модел",
        "вернуть", "верните", "чинить", "ремонт", "акци",
    )

    def classify_department(self, text: str) -> str:
        """Грубое определение отдела по тексту обращения.

        Используется только как подсказка: пользователь всегда может
        выбрать отдел сам, а сотрудник — переназначить заявку.
        """
        norm = normalize(text)
        it_score = sum(1 for marker in self.IT_MARKERS if marker in norm)
        sales_score = sum(1 for marker in self.SALES_MARKERS if marker in norm)
        if it_score > sales_score:
            return config.DEPT_IT
        if sales_score > it_score:
            return config.DEPT_SALES
        return config.DEFAULT_DEPARTMENT


# Единственный экземпляр на всё приложение.
kb = KnowledgeBase()
