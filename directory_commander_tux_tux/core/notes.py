"""Almacenamiento y gestión de notas y fragmentos de texto rápido. Sin Qt."""
from __future__ import annotations

import json
import os
import time
import uuid
from dataclasses import asdict, dataclass

from .i18n import tr

NOTES_FILE = "notes.json"


@dataclass
class NoteItem:
    id: str
    title: str
    content: str
    updated_at: float


def title_for(content: str, fallback: str = "") -> str:
    """Extrae un título a partir de la primera línea no vacía del contenido."""
    lines = [line.strip() for line in content.splitlines() if line.strip()]
    if lines:
        first = lines[0]
        return first[:40] + ("…" if len(first) > 40 else "")
    return fallback or tr("Nueva nota")


def create_note(content: str = "", title: str = "") -> NoteItem:
    now = time.time()
    return NoteItem(
        id=uuid.uuid4().hex[:10],
        title=title or title_for(content),
        content=content,
        updated_at=now,
    )


def notes_file_path(directory: str) -> str:
    return os.path.join(directory, NOTES_FILE)


def load_notes(directory: str) -> list[NoteItem]:
    path = notes_file_path(directory)
    if not os.path.isfile(path):
        return []
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        if not isinstance(data, list):
            return []
        items = []
        for raw in data:
            if isinstance(raw, dict) and "id" in raw:
                items.append(NoteItem(
                    id=str(raw.get("id", "")),
                    title=str(raw.get("title", "")),
                    content=str(raw.get("content", "")),
                    updated_at=float(raw.get("updated_at", 0.0)),
                ))
        return items
    except (OSError, json.JSONDecodeError, ValueError):
        return []


def save_notes(directory: str, notes: list[NoteItem]) -> None:
    os.makedirs(directory, exist_ok=True)
    target = notes_file_path(directory)
    tmp = target + ".tmp"
    data = [asdict(n) for n in notes]
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    os.replace(tmp, target)


def update_note(notes: list[NoteItem], note_id: str, content: str, title: str | None = None) -> bool:
    for note in notes:
        if note.id == note_id:
            note.content = content
            note.title = title if title is not None else title_for(content)
            note.updated_at = time.time()
            return True
    return False


def delete_note(notes: list[NoteItem], note_id: str) -> bool:
    original_len = len(notes)
    notes[:] = [n for n in notes if n.id != note_id]
    return len(notes) < original_len


def export_notes_to_file(filepath: str, notes: list[NoteItem]) -> None:
    target = filepath
    tmp = target + ".tmp"
    data = [asdict(n) for n in notes]
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    os.replace(tmp, target)


def import_notes_from_file(filepath: str, current_notes: list[NoteItem]) -> int:
    """Importa notas desde un archivo JSON o de texto. Retorna la cantidad de notas agregadas."""
    if not os.path.isfile(filepath):
        return 0
    added = 0
    # Intentar formato JSON
    try:
        with open(filepath, "r", encoding="utf-8") as f:
            data = json.load(f)
        if isinstance(data, list):
            existing_ids = {n.id for n in current_notes}
            for raw in data:
                if isinstance(raw, dict) and "content" in raw:
                    nid = str(raw.get("id", "")) or uuid.uuid4().hex[:10]
                    if nid in existing_ids:
                        nid = uuid.uuid4().hex[:10]
                    title = str(raw.get("title", "")) or title_for(str(raw.get("content", "")))
                    new_item = NoteItem(
                        id=nid,
                        title=title,
                        content=str(raw.get("content", "")),
                        updated_at=float(raw.get("updated_at", time.time())),
                    )
                    current_notes.append(new_item)
                    existing_ids.add(nid)
                    added += 1
            if added > 0:
                return added
    except (json.JSONDecodeError, UnicodeDecodeError, ValueError):
        pass

    # Si no es JSON o no tenía notas válidas, importar como nota individual de texto
    try:
        with open(filepath, "r", encoding="utf-8") as f:
            content = f.read()
        if content.strip():
            filename = os.path.splitext(os.path.basename(filepath))[0]
            new_item = create_note(content, title=filename)
            current_notes.append(new_item)
            return 1
    except OSError:
        pass
    return 0


