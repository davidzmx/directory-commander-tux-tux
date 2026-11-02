"""Renombrado en lote con vista previa: texto, comodines o regex, mayúsculas y numeración."""
from __future__ import annotations

import fnmatch
import os
import re
import uuid
from dataclasses import asdict, dataclass

from .fsops import validate_name
from .i18n import tr

NUMBER_TOKEN = "[#]"


@dataclass
class RenameSpec:
    mode: str = "regex"  # simple | wildcard | regex
    pattern: str = "(.*)"
    replacement: str = r"\1"
    ignore_ext: bool = True
    case: str = "none"  # none | upper | lower | title | sentence
    seq_start: int = 1
    seq_step: int = 1
    seq_digits: int = 1

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict) -> "RenameSpec":
        known = {k: v for k, v in data.items() if k in cls.__dataclass_fields__}
        return cls(**known)


def _wildcard_to_regex(pattern: str) -> re.Pattern:
    translated = "".join("(.*)" if c == "*" else "(.)" if c == "?" else re.escape(c) for c in pattern)
    return re.compile(f"^{translated}$", re.IGNORECASE)


def _wildcard_template(replacement: str) -> str:
    counter = 0
    out = []
    for ch in replacement:
        if ch in "*?":
            counter += 1
            out.append(f"\\g<{counter}>")
        elif ch == "\\":
            out.append("\\\\")
        else:
            out.append(ch)
    return "".join(out)


def _apply_case(text: str, case: str) -> str:
    if case == "upper":
        return text.upper()
    if case == "lower":
        return text.lower()
    if case == "title":
        return re.sub(r"[^\W\d_]+", lambda m: m.group(0).capitalize(), text)
    if case == "sentence":
        return text[:1].upper() + text[1:].lower()
    return text


def compute_names(names: list[str], spec: RenameSpec) -> list[str]:
    """Calcula los nombres nuevos sin tocar el disco. ValueError si el patrón no es válido."""
    try:
        if spec.mode == "regex":
            compiled = re.compile(spec.pattern)
            template = spec.replacement
        elif spec.mode == "wildcard":
            compiled = _wildcard_to_regex(spec.pattern)
            template = _wildcard_template(spec.replacement)
        elif spec.mode == "simple":
            compiled = re.compile(re.escape(spec.pattern), re.IGNORECASE) if spec.pattern else None
            template = spec.replacement.replace("\\", "\\\\")
        else:
            raise ValueError(tr("Modo desconocido: {mode}", mode=spec.mode))
    except re.error as exc:
        raise ValueError(tr("Patrón no válido: {exc}", exc=exc)) from exc

    result = []
    number = spec.seq_start
    for name in names:
        stem, ext = os.path.splitext(name) if spec.ignore_ext else (name, "")
        try:
            if compiled is None:
                new_stem = stem
            elif spec.mode == "regex":
                new_stem = compiled.sub(template, stem, count=1) if compiled.pattern else stem
            elif spec.mode == "wildcard":
                new_stem = compiled.sub(template, stem) if compiled.match(stem) else stem
            else:
                new_stem = compiled.sub(lambda _m, t=spec.replacement: t, stem)
        except (re.error, IndexError) as exc:
            raise ValueError(tr("Reemplazo no válido: {exc}", exc=exc)) from exc
        if NUMBER_TOKEN in new_stem:
            new_stem = new_stem.replace(NUMBER_TOKEN, str(number).zfill(max(1, spec.seq_digits)))
            number += spec.seq_step
        result.append(_apply_case(new_stem, spec.case) + ext)
    return result


def find_problems(names: list[str], new_names: list[str], others: set[str] | None = None) -> dict[int, str]:
    """Problemas por índice: nombres inválidos, duplicados o choques con archivos no incluidos."""
    problems: dict[int, str] = {}
    others = {n.casefold() for n in (others or set())}
    selected = {n.casefold() for n in names}
    seen: dict[str, int] = {}
    for index, new in enumerate(new_names):
        reason = validate_name(new)
        if reason:
            problems[index] = reason
            continue
        key = new.casefold()
        if key in seen:
            problems[index] = tr("Nombre duplicado en el lote")
            problems.setdefault(seen[key], tr("Nombre duplicado en el lote"))
        else:
            seen[key] = index
        if key in others and key not in selected:
            problems[index] = tr("Ya existe otro archivo con ese nombre")
    return problems


def apply_renames(directory: str, names: list[str], new_names: list[str]) -> list[tuple[str, str]]:
    """Aplica en dos fases para admitir intercambios (a→b, b→a). Devuelve los errores."""
    errors: list[tuple[str, str]] = []
    pending = [(old, new) for old, new in zip(names, new_names) if old != new]
    staged: list[tuple[str, str, str]] = []
    for old, new in pending:
        temp = f".detux-{uuid.uuid4().hex}"
        try:
            os.rename(os.path.join(directory, old), os.path.join(directory, temp))
            staged.append((temp, old, new))
        except OSError as exc:
            errors.append((old, str(exc)))
    for temp, old, new in staged:
        try:
            if os.path.lexists(os.path.join(directory, new)):
                raise FileExistsError(tr("Ya existe: {new}", new=new))
            os.rename(os.path.join(directory, temp), os.path.join(directory, new))
        except OSError as exc:
            errors.append((old, str(exc)))
            try:
                os.rename(os.path.join(directory, temp), os.path.join(directory, old))
            except OSError as restore_exc:
                errors.append((old, tr("Quedó como {temp}: {restore_exc}", temp=temp, restore_exc=restore_exc)))
    return errors


def matches_glob(name: str, pattern: str) -> bool:
    return fnmatch.fnmatch(name.casefold(), pattern.casefold())
