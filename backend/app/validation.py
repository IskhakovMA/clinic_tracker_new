"""Безопасная диагностика: только тип ошибки и путь известного поля, без ввода."""
from functools import lru_cache
from pydantic import BaseModel
from . import models


@lru_cache(maxsize=1)
def _field_names() -> frozenset[str]:
    names = {"body", "query", "path", "header", "cookie"}
    for value in vars(models).values():
        if isinstance(value, type) and issubclass(value, BaseModel):
            for name, field in value.model_fields.items():
                names.add(name)
                if isinstance(field.alias, str):
                    names.add(field.alias)
    return frozenset(names)


def safe_validation_errors(errors: list[dict]) -> list[dict]:
    result = []
    for error in errors:
        loc = [part if isinstance(part, int) or part in _field_names() else "unknown_field"
               for part in error.get("loc", ())]
        result.append({"type": error["type"], "loc": loc,
                       "msg": "Некорректное значение поля"})
    return result
