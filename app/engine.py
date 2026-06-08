"""Движок интерактивной новеллы — data-driven.

История описывается JSON-структурой (см. stories/*.json). Движок умеет:
  • безопасно вычислять условия (`influence >= 2`, `romance_damian > romance_leonard`)
    без eval() — через разбор AST с белым списком узлов;
  • применять эффекты выбора к переменным (`{"cunning": 1}`);
  • фильтровать видимые блоки/реплики и варианты выбора по условиям;
  • разрешать авто-переходы (`next`) и групповые развилки концовок (`route_group`);
  • вычислять заслуженные достижения по текущим переменным.

Формат сцены:
    {
      "chapter": "Глава 1 — Завещание",     # баннер при смене главы (опц.)
      "title":   "Кабинет адвоката",         # подпись-локация (опц.)
      "bg":      "lawyer_office",             # ключ фона из backgrounds (опц.)
      "fx":      {"flag": 1},                 # эффекты при входе в сцену (опц.)
      "blocks":  [ {"speaker": "Леонард", "text": "...", "if": "...", "fx": {...}} ],
      "choices": [ {"text": "...", "fx": {...}, "cost": 15, "goto": "scene_x", "if": "..."} ],
      "next":    "scene_y"  |  [ {"if": "...", "goto": "..."}, {"goto": "..."} ],
      "route_group": "romance",               # развилка по группе концовок
      "complete": {"groups": ["romance","business"], "code_from": "business"},
      "secret":  true,                        # сцена открывает секретную концовку
      "final":   true                         # терминальный экран (кнопки «в меню»)
    }
"""
from __future__ import annotations

import ast
import logging
import operator
from dataclasses import dataclass
from typing import Any

log = logging.getLogger("engine")

# ── Безопасный вычислитель условий ───────────────────────────
_BIN = {ast.Add: operator.add, ast.Sub: operator.sub, ast.Mult: operator.mul}
_CMP = {
    ast.Gt: operator.gt, ast.GtE: operator.ge, ast.Lt: operator.lt,
    ast.LtE: operator.le, ast.Eq: operator.eq, ast.NotEq: operator.ne,
}


def eval_condition(expr: str | None, variables: dict[str, Any]) -> bool:
    """Истинность условия. Пустое/None → True. Неизвестные имена → 0."""
    if expr is None or expr == "":
        return True
    try:
        node = ast.parse(str(expr), mode="eval").body
        return bool(_ev(node, variables))
    except Exception as e:  # noqa: BLE001 — любое выражение-битьё трактуем как False
        log.warning("плохое условие %r: %s", expr, e)
        return False


def _ev(node: ast.AST, v: dict[str, Any]) -> Any:
    if isinstance(node, ast.BoolOp):
        vals = [_ev(x, v) for x in node.values]
        return all(vals) if isinstance(node.op, ast.And) else any(vals)
    if isinstance(node, ast.UnaryOp):
        x = _ev(node.operand, v)
        if isinstance(node.op, ast.Not):
            return not x
        if isinstance(node.op, ast.USub):
            return -x
        if isinstance(node.op, ast.UAdd):
            return +x
        raise ValueError("unary op")
    if isinstance(node, ast.BinOp):
        op = _BIN.get(type(node.op))
        if op is None:
            raise ValueError("bin op")
        return op(_ev(node.left, v), _ev(node.right, v))
    if isinstance(node, ast.Compare):
        left = _ev(node.left, v)
        ok = True
        for o, comp in zip(node.ops, node.comparators):
            right = _ev(comp, v)
            fn = _CMP.get(type(o))
            if fn is None:
                raise ValueError("cmp op")
            ok = ok and fn(left, right)
            left = right
        return ok
    if isinstance(node, ast.Name):
        return v.get(node.id, 0)
    if isinstance(node, ast.Constant):
        return node.value
    raise ValueError(f"unsupported: {type(node).__name__}")


# ── Эффекты ──────────────────────────────────────────────────
def apply_effects(variables: dict[str, Any], fx: dict[str, Any] | None) -> None:
    """Применяет эффекты к переменным. Числа суммируются, строки/булевы — присваиваются."""
    for key, val in (fx or {}).items():
        if isinstance(val, bool):
            variables[key] = 1 if val else 0
        elif isinstance(val, (int, float)):
            variables[key] = variables.get(key, 0) + val
        else:
            variables[key] = val


# ── Обёртка истории ──────────────────────────────────────────
@dataclass
class Story:
    data: dict[str, Any]

    @property
    def id(self) -> str:
        return self.data["id"]

    @property
    def title(self) -> str:
        return self.data["title"]

    @property
    def short(self) -> str:
        return self.data.get("short", "")

    @property
    def description(self) -> str:
        return self.data.get("description", "")

    @property
    def cover(self) -> str:
        return self.data.get("cover", "📖")

    @property
    def price(self) -> int:
        return int(self.data.get("price", 0))

    @property
    def available(self) -> bool:
        return bool(self.data.get("available", True))

    @property
    def start_scene(self) -> str:
        return self.data["start"]

    def init_vars(self) -> dict[str, Any]:
        return dict(self.data.get("vars", {}))

    def scene(self, scene_id: str) -> dict[str, Any] | None:
        return self.data.get("scenes", {}).get(scene_id)

    def background_url(self, bg: str | None) -> str | None:
        if not bg:
            return None
        return self.data.get("backgrounds", {}).get(bg)

    def group(self, name: str) -> list[dict[str, Any]]:
        return self.data.get("ending_groups", {}).get(name, [])

    @property
    def achievements(self) -> dict[str, dict[str, Any]]:
        return self.data.get("achievements", {})

    @property
    def stats(self) -> dict[str, dict[str, Any]]:
        """Характеристики: var -> {name, emoji, kind: 'main'|'romance'}."""
        return self.data.get("stats", {})

    @property
    def characters(self) -> dict[str, dict[str, Any]]:
        """Персонажи: имя -> {emoji, desc?, hero?: bool}."""
        return self.data.get("characters", {})

    def main_stats(self) -> list[tuple[str, dict[str, Any]]]:
        return [(k, s) for k, s in self.stats.items() if s.get("kind") == "main"]

    def romance_stats(self) -> list[tuple[str, dict[str, Any]]]:
        return [(k, s) for k, s in self.stats.items() if s.get("kind") == "romance"]

    def chapter_count(self) -> int:
        return len({sc["chapter"] for sc in self.data.get("scenes", {}).values() if sc.get("chapter")})

    def scene_count(self) -> int:
        return len(self.data.get("scenes", {}))


# ── Видимость блоков / выборов ───────────────────────────────
def visible_blocks(scene: dict[str, Any], variables: dict[str, Any]) -> list[dict[str, Any]]:
    return [b for b in scene.get("blocks", []) if eval_condition(b.get("if"), variables)]


def visible_choices(scene: dict[str, Any], variables: dict[str, Any]) -> list[dict[str, Any]]:
    return [c for c in scene.get("choices", []) if eval_condition(c.get("if"), variables)]


# ── Переходы и развилки ──────────────────────────────────────
def eval_group(story: Story, group_name: str, variables: dict[str, Any]) -> dict[str, Any] | None:
    """Возвращает первый подходящий вариант группы концовок (последний — дефолт без if)."""
    for opt in story.group(group_name):
        if eval_condition(opt.get("if"), variables):
            return opt
    return None


def resolve_next(story: Story, scene: dict[str, Any], variables: dict[str, Any]) -> str | None:
    """Куда идём из сцены без выбора пользователя (авто-переход)."""
    if "route_group" in scene:
        opt = eval_group(story, scene["route_group"], variables)
        return opt.get("goto") if opt else None
    nxt = scene.get("next")
    if nxt is None:
        return None
    if isinstance(nxt, str):
        return nxt
    for opt in nxt:
        if eval_condition(opt.get("if"), variables):
            return opt.get("goto")
    return None


# ── Достижения ───────────────────────────────────────────────
def evaluate_achievements(story: Story, variables: dict[str, Any]) -> set[str]:
    """Коды достижений, чьи условия истинны при текущих переменных."""
    out: set[str] = set()
    for code, spec in story.achievements.items():
        if eval_condition(spec.get("if"), variables):
            out.add(code)
    return out
