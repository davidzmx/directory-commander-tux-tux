"""Buscar archivos: cada opción por separado, el comando de Linux y que los dos caminos encuentren lo mismo."""
import os
import shutil
import subprocess
import sys
import time
import zipfile
from datetime import date, datetime, timedelta

import pytest

from directory_commander_tux_tux.core import archive, file_search
from directory_commander_tux_tux.core.file_search import (build_command, compile_text, date_matches, default_state, describe_command, is_excluded, merged_state,
                                                     run_search, size_matches, time_matches)


def query(text, **options):
    return {"text": text, "wildcards": False, "regex": False, "any_word": False, "case": False, "diacritics": False, "partial": True, **options}


# ---- nombre y texto ----

@pytest.mark.parametrize("options, text, subject, expected", [
    ({}, "mascot", "Mi_MASCOTA.png", True),
    ({"case": True}, "mascot", "Mi_MASCOTA.png", False),
    ({"partial": False}, "mascot", "mascota", False),
    ({"partial": False}, "mascota", "MASCOTA", True),
    ({"wildcards": True}, "*.png", "a.png", True),
    ({"wildcards": True}, "m?scota", "mascota", True),
    ({"wildcards": True, "partial": False}, "a*", "ba", False),
    ({"wildcards": True, "partial": False}, "a*", "ab", True),
    ({"wildcards": False}, "a*", "ab", False),
    ({"regex": True}, r"^\d{4}_", "2023_foto", True),
    ({"regex": True}, r"^\d{4}_", "foto2023_", False),
    ({"any_word": True}, "perro gato", "mi gato.png", True),
    ({"any_word": True}, "perro gato", "pez.png", False),
    ({"any_word": False}, "perro gato", "mi gato.png", False),
    ({"diacritics": True}, "camion", "Camión.png", True),
    ({"diacritics": True}, "camión", "camion.png", True),
    ({"diacritics": False}, "camion", "Camión.png", False),
])
def test_text_options(options, text, subject, expected):
    assert bool(compile_text(query(text, **options))(subject)) is expected


def test_nothing_written_asks_for_nothing():
    assert compile_text(query("")) is None
    assert compile_text(query("   ", any_word=True)) is None


def test_bad_regular_expression_is_explained():
    with pytest.raises(ValueError):
        compile_text(query("(", regex=True))


def test_exact_text_in_a_file_means_a_whole_word():
    matcher = compile_text(query("cat", partial=False), whole_word_when_exact=True)
    assert matcher("the cat sat") and not matcher("concatenate")


# ---- fecha, hora y tamaño ----

NOON = datetime(2026, 6, 15, 12, 0).timestamp()


@pytest.mark.parametrize("op, first, second, expected", [
    ("ignore", "2000-01-01", "2000-01-01", True),
    ("on", "2026-06-15", "", True), ("on", "2026-06-14", "", False),
    ("before", "2026-06-16", "", True), ("before", "2026-06-15", "", False),
    ("after", "2026-06-14", "", True), ("after", "2026-06-15", "", False),
    ("between", "2026-06-01", "2026-06-30", True), ("between", "2026-06-30", "2026-06-01", True), ("between", "2026-07-01", "2026-07-30", False),
    ("not_between", "2026-07-01", "2026-07-30", True), ("not_between", "2026-06-01", "2026-06-30", False),
])
def test_date(op, first, second, expected):
    assert date_matches({"op": op, "first": first, "second": second or first}, NOON) is expected


@pytest.mark.parametrize("amount, unit, ago, expected", [(10, "minutes", 300, True), (10, "minutes", 900, False), (2, "hours", 3600, True), (1, "days", 90000, False),
                                                         (2, "weeks", 86400 * 10, True), (1, "months", 86400 * 40, False), (1, "years", 86400 * 300, True)])
def test_date_within(amount, unit, ago, expected):
    now = 1_000_000_000.0
    assert date_matches({"op": "within", "amount": amount, "unit": unit}, now - ago, now) is expected


@pytest.mark.parametrize("op, first, second, moment, expected", [
    ("ignore", "00:00", "00:00", (12, 0), True), ("at", "12:00", "", (12, 0), True), ("at", "12:01", "", (12, 0), False),
    ("before", "13:00", "", (12, 0), True), ("after", "13:00", "", (12, 0), False), ("between", "11:00", "13:00", (12, 0), True),
    ("not_between", "11:00", "13:00", (12, 0), False), ("around_1", "12:59", "", (12, 0), True), ("around_1", "13:01", "", (12, 0), False),
    ("around_6", "18:00", "", (12, 0), True), ("around_1", "00:30", "", (23, 45), True),
])
def test_time(op, first, second, moment, expected):
    stamp = datetime(2026, 6, 15, *moment).timestamp()
    assert time_matches({"op": op, "first": first, "second": second or first}, stamp) is expected


@pytest.mark.parametrize("op, first, second, unit, size, expected", [
    ("ignore", 0, 0, "bytes", 5, True), ("equals", 5, 0, "bytes", 5, True), ("equals", 5, 0, "bytes", 6, False),
    ("smaller", 1, 0, "KB", 1023, True), ("smaller", 1, 0, "KB", 1024, False), ("greater", 1, 0, "KB", 1025, True), ("greater", 1, 0, "KB", 1024, False),
    ("between", 1, 2, "MB", 1500000, True), ("between", 2, 1, "MB", 1500000, True), ("not_between", 1, 2, "MB", 1500000, False), ("not_between", 1, 2, "MB", 10, True),
    ("around_25", 100, 0, "bytes", 125, True), ("around_25", 100, 0, "bytes", 126, False), ("around_50", 100, 0, "bytes", 50, True), ("around_50", 100, 0, "bytes", 49, False),
])
def test_size(op, first, second, unit, size, expected):
    assert size_matches({"op": op, "first": first, "second": second, "unit": unit}, size) is expected


# ---- recorrer ----

@pytest.fixture
def tree(tmp_path):
    root = tmp_path / "raiz"
    (root / "sub" / "dentro").mkdir(parents=True)
    (root / "otra").mkdir()
    (root / ".oculta").mkdir()
    (root / "excluida").mkdir()
    files = {"mascota.png": "imagen", "Mascota2.PNG": "imagen", "nota.txt": "el perro y la AGUJA", "camión.txt": "sin nada", "sub/dentro/hondo.txt": "aguja escondida",
             "sub/mascota_sub.jpg": "x" * 2000, "otra/informe.pdf": "y" * 50, ".oculta/mascota_oculta.txt": "aguja", "excluida/mascota_excluida.txt": "aguja"}
    for name, text in files.items():
        (root / name).write_text(text, encoding="utf-8")
    with zipfile.ZipFile(root / "paquete.zip", "w", compression=zipfile.ZIP_DEFLATED) as handle:
        handle.writestr("mascota_zip.txt", "aguja en el zip")
        handle.writestr("carpeta/otro.txt", "nada")
    if sys.platform == "win32":
        subprocess.run(["attrib", "+h", str(root / ".oculta")], check=True)
    return root


def spec(root, **changes):
    state = default_state()
    state["roots"] = [str(root)]
    state["exclude"] = []
    for key, value in changes.items():
        if isinstance(value, dict) and isinstance(state.get(key), dict):
            state[key] = {**state[key], **value}
        else:
            state[key] = value
    return state


def names_of(found) -> list[str]:
    return sorted(os.path.basename(path) for path in found.paths)


def test_name_with_wildcards_like_the_reference(tree):
    found = run_search(spec(tree, name={"text": "*mascot*"}))
    assert names_of(found) == sorted(["Mascota2.PNG", "mascota.png", "mascota_excluida.txt", "mascota_oculta.txt", "mascota_sub.jpg"])


def test_without_subfolders(tree):
    assert names_of(run_search(spec(tree, name={"text": "mascot"}, subfolders=False))) == ["Mascota2.PNG", "mascota.png"]


def test_excluding_by_name_path_and_with_the_final_wildcard(tree):
    for pattern in ("excluida", "excluida\\*", "excluida/*", str(tree / "excluida")):
        assert "mascota_excluida.txt" not in names_of(run_search(spec(tree, name={"text": "mascot"}, exclude=[pattern]))), pattern


def test_excluding_a_file_pattern(tree):
    assert "mascota.png" not in names_of(run_search(spec(tree, name={"text": "mascot"}, exclude=["*.png"])))


def test_excluding_hidden_folders(tree):
    found = names_of(run_search(spec(tree, name={"text": "mascot"}, exclude_hidden=True)))
    assert "mascota_oculta.txt" not in found and "mascota_sub.jpg" in found


def test_is_excluded_follows_the_system_case_rule():
    assert is_excluded(os.sep + "a" + os.sep + "b", "b", ["b"])
    assert is_excluded(os.sep + "a" + os.sep + "B", "B", ["b"]) is (sys.platform == "win32")


def test_folders_also_match_by_name(tree):
    assert "dentro" in names_of(run_search(spec(tree, name={"text": "dentro"})))


def test_kind_files_folders_and_type(tree):
    assert names_of(run_search(spec(tree, name={"text": "o"}, kind={"kind": "folders"}))) == sorted(["dentro", ".oculta", "otra"])
    assert "dentro" not in names_of(run_search(spec(tree, name={"text": "dentro"}, kind={"kind": "files"})))
    images = names_of(run_search(spec(tree, kind={"kind": "type", "content_type": "images"})))
    assert images == sorted(["Mascota2.PNG", "mascota.png", "mascota_sub.jpg"])


def test_text_inside_files_ignores_case_and_folders(tree):
    found = names_of(run_search(spec(tree, content={"text": "aguja"})))
    assert found == sorted(["nota.txt", "hondo.txt", "mascota_oculta.txt", "mascota_excluida.txt"])


def test_text_found_after_the_first_block_and_across_two(tmp_path):
    (tmp_path / "grande.txt").write_bytes(b"x" * (1024 * 1024 - 2) + b"AGUJA" + b"y" * 100)
    assert names_of(run_search(spec(tmp_path, content={"text": "aguja"}))) == ["grande.txt"]


@pytest.mark.parametrize("encoding, utf8, found", [pytest.param("utf-16", False, True, marks=pytest.mark.skipif(sys.platform != "win32", reason="en Linux busca grep, que no lee UTF-16")),
                                                   ("utf-8-sig", False, True), ("utf-8", True, True)])
def test_text_encodings(tmp_path, encoding, utf8, found):
    (tmp_path / "a.txt").write_bytes("camión azul".encode(encoding))
    assert bool(run_search(spec(tmp_path, content={"text": "camión", "utf8": utf8})).paths) is found


def test_utf16_is_read_when_the_program_itself_reads_the_text(tmp_path):
    """En Linux, «Ignorar acentos» es lo que hace que no busque grep."""
    (tmp_path / "a.txt").write_bytes("camión azul".encode("utf-16"))
    assert run_search(spec(tmp_path, content={"text": "camion", "diacritics": True})).paths


@pytest.mark.skipif(sys.platform != "win32", reason="en Linux el texto se lee siempre como UTF-8")
def test_without_assuming_utf8_it_is_read_with_the_system_encoding(tmp_path):
    (tmp_path / "a.txt").write_bytes("camión".encode("utf-8"))
    assert not run_search(spec(tmp_path, content={"text": "camión", "utf8": False})).paths
    (tmp_path / "a.txt").write_bytes("camión".encode("cp1252"))
    assert run_search(spec(tmp_path, content={"text": "camión", "utf8": False})).paths


def test_size_applies_to_files_only(tree):
    found = names_of(run_search(spec(tree, size={"op": "greater", "first": 1, "unit": "KB"})))
    assert found == ["mascota_sub.jpg"]


def test_date_filter(tree):
    old = time.time() - 86400 * 100
    os.utime(tree / "nota.txt", (old, old))
    recent = names_of(run_search(spec(tree, name={"text": "nota"}, date={"op": "within", "amount": 7, "unit": "days"})))
    assert recent == []
    stale = names_of(run_search(spec(tree, name={"text": "nota"}, date={"op": "before", "first": date.today().isoformat()})))
    assert stale == ["nota.txt"]


def test_inside_archives_only_when_asked(tree):
    assert "mascota_zip.txt" not in names_of(run_search(spec(tree, name={"text": "mascota_zip"})))
    found = run_search(spec(tree, name={"text": "mascota_zip"}, archives=True))
    assert found.paths == [os.path.join(str(tree / "paquete.zip"), "mascota_zip.txt")]


def test_text_inside_archives(tree):
    found = names_of(run_search(spec(tree, content={"text": "en el zip"}, archives=True)))
    assert found == ["mascota_zip.txt"]


def test_folder_that_cannot_be_read_is_noted(tree, monkeypatch):
    missing = str(tree / "no_existe")
    found = run_search({**spec(tree), "roots": [missing]})
    assert found.denied == [missing] and not found.paths


def test_cancelling_stops(tree):
    found = run_search(spec(tree), is_cancelled=lambda: True)
    assert found.cancelled and not found.paths


def test_results_arrive_in_batches(tmp_path, monkeypatch):
    monkeypatch.setattr(file_search, "BATCH", 2)
    for number in range(5):
        (tmp_path / f"{number}.txt").write_text("x")
    batches = []
    found = run_search(spec(tmp_path), on_batch=lambda batch: batches.append(list(batch)))
    assert [len(batch) for batch in batches] == [2, 2, 1] and len(found.paths) == 5


# ---- reglas del modo avanzado ----

def clause(field, value, join="and", negate=False):
    return {"join": join, "negate": negate, "field": field, "value": value}


def test_rules_and(tree):
    state = spec(tree, advanced=True, clauses=[clause("name", query("mascota")), clause("kind", {"kind": "type", "content_type": "images"})])
    assert names_of(run_search(state)) == sorted(["Mascota2.PNG", "mascota.png", "mascota_sub.jpg"])


def test_rules_or(tree):
    state = spec(tree, advanced=True, clauses=[clause("name", query("informe")), clause("name", query("hondo"), join="or")])
    assert names_of(run_search(state)) == ["hondo.txt", "informe.pdf"]


def test_rules_no_match(tree):
    state = spec(tree, advanced=True, clauses=[clause("name", query("mascota")), clause("content", query("aguja"), negate=True), clause("kind", {"kind": "files"})])
    assert "mascota_excluida.txt" not in names_of(run_search(state)) and "mascota.png" in names_of(run_search(state))


def test_rules_are_evaluated_from_left_to_right(tree):
    state = spec(tree, advanced=True, clauses=[clause("name", query("informe")), clause("name", query("hondo"), join="or"), clause("name", query("txt"))])
    assert names_of(run_search(state)) == ["hondo.txt"]


def test_rule_on_attribute(tree):
    state = spec(tree, advanced=True, clauses=[clause("attribute", {"attribute": "hidden"})])
    assert ".oculta" in names_of(run_search(state))


def test_rule_on_size_and_date(tree):
    state = spec(tree, advanced=True, clauses=[clause("size", {"op": "greater", "first": 1, "unit": "KB"}), clause("date", {"op": "within", "amount": 1, "unit": "days", "field": "modified"})])
    assert names_of(run_search(state)) == ["mascota_sub.jpg"]


def test_without_rules_everything_matches(tree):
    assert run_search(spec(tree, advanced=True, clauses=[])).paths


# ---- plantillas ----

def test_a_template_with_the_old_windows_index_still_works(tree):
    state = merged_state({"index": "perro", "name": {"text": "nota"}})
    assert "index" not in state and state["name"]["text"] == "nota"

def test_incomplete_or_odd_template_still_works():
    state = merged_state({"name": {"text": "x"}, "subfolders": False, "exclude": "no es una lista", "inventado": 1})
    assert state["name"]["text"] == "x" and state["name"]["wildcards"] is True and state["subfolders"] is False
    assert isinstance(state["exclude"], list) and "inventado" not in state
    assert merged_state("basura") == default_state()


# ---- el comando de Linux ----

def command_of(root="/datos", **changes):
    state = spec(root, **changes)
    state["roots"] = [root]
    return build_command(state)


def test_command_for_a_plain_name():
    command = command_of(name={"text": "*mascot*"})
    assert command.argv[:5] == ["find", "-H", "/datos", "-mindepth", "1"]
    assert "-iname" in command.argv and "*mascot*" in command.argv and command.argv[-1] == "-print0"
    assert not command.python_checks


def test_command_respects_case_and_exact_name():
    assert "-name" in command_of(name={"text": "a", "case": True}).argv
    assert "a" in command_of(name={"text": "a", "wildcards": False, "partial": False}).argv
    assert "*a*" in command_of(name={"text": "a", "wildcards": False, "partial": True}).argv


def test_command_with_any_word():
    argv = command_of(name={"text": "uno dos", "any_word": True}).argv
    assert "-o" in argv and "*uno*" in argv and "*dos*" in argv


def test_plain_text_with_special_characters_is_not_a_pattern():
    assert r"*a\*b*" in command_of(name={"text": "a*b", "wildcards": False}).argv


def test_command_without_subfolders():
    assert "-maxdepth" in command_of(subfolders=False).argv


def test_command_with_excludes():
    argv = command_of(exclude=["/proc/*", "cache"]).argv
    assert "-prune" in argv and "/proc" in argv and "cache" in argv


def test_command_excluding_hidden_folders():
    argv = command_of(exclude_hidden=True).argv
    assert ".*" in argv and "-prune" in argv


def test_command_for_text():
    argv = command_of(content={"text": "hola mundo"}).argv
    assert "grep" in argv and "-i" in argv and "-F" in argv and "hola mundo" in argv and argv[argv.index("grep") - 1] == "-exec"


def test_command_for_text_options():
    assert "-P" in command_of(content={"text": "a+", "regex": True}).argv
    assert "-i" not in command_of(content={"text": "a", "case": True}).argv
    assert "-w" in command_of(content={"text": "a", "partial": False}).argv


def test_utf8_option_shows_in_the_environment():
    assert command_of(content={"text": "a", "utf8": True}).environment == {"LC_ALL": "C.UTF-8"}


@pytest.mark.parametrize("test, expected", [
    ({"op": "on", "first": "2026-01-10"}, ["-newermt", "2026-01-10", "!", "-newermt", "2026-01-11"]),
    ({"op": "before", "first": "2026-01-10"}, ["!", "-newermt", "2026-01-10"]),
    ({"op": "after", "first": "2026-01-10"}, ["-newermt", "2026-01-11"]),
    ({"op": "between", "first": "2026-01-10", "second": "2026-01-20"}, ["-newermt", "2026-01-10", "!", "-newermt", "2026-01-21"]),
    ({"op": "within", "amount": 2, "unit": "hours"}, ["-mmin", "-120"]),
])
def test_command_for_dates(test, expected):
    argv = command_of(date=test).argv
    position = argv.index(expected[0])
    assert argv[position:position + len(expected)] == expected


@pytest.mark.parametrize("test, expected", [({"op": "equals", "first": 5, "unit": "bytes"}, "5c"), ({"op": "smaller", "first": 1, "unit": "KB"}, "-1024c"),
                                            ({"op": "greater", "first": 1, "unit": "KB"}, "+1024c"), ({"op": "around_25", "first": 100, "unit": "bytes"}, "-126c")])
def test_command_for_sizes(test, expected):
    assert expected in command_of(size=test).argv


def test_what_find_cannot_do_is_said():
    assert command_of(time={"op": "at", "first": "12:00"}).python_checks
    assert command_of(name={"text": "a", "diacritics": True}).python_checks
    assert command_of(name={"text": "a", "regex": True}).python_checks
    assert command_of(archives=True).python_checks


def test_rule_that_find_cannot_do_leaves_all_rules_to_the_program():
    command = command_of(advanced=True, clauses=[clause("name", query("a")), clause("attribute", {"attribute": "hidden"})])
    assert command.python_checks and "-iname" not in command.argv


def test_rules_become_an_expression():
    argv = command_of(advanced=True, clauses=[clause("name", query("a")), clause("name", query("b"), join="or", negate=True)]).argv
    assert "-o" in argv and "!" in argv


def test_description_can_be_pasted_in_a_terminal():
    text = describe_command(command_of(name={"text": "con espacio"}, exclude=["/proc/*"]))
    assert text.startswith("find -H /datos") and "'*con espacio*'" in text and r"\(" in text and "-print0" in text
    assert "#" not in text
    assert "\n# " in describe_command(command_of(archives=True))


# ---- lo que encuentra el comando es lo mismo ----

def posix_tools() -> str:
    """Un `find` de verdad, también en Windows si hay Git o Cygwin."""
    if sys.platform != "win32":
        return os.path.dirname(shutil.which("find") or "")
    for folder in (r"C:\Program Files\Git\usr\bin", r"C:\cygwin64\bin"):
        if os.path.exists(os.path.join(folder, "find.exe")) and os.path.exists(os.path.join(folder, "grep.exe")):
            return folder
    return ""


needs_find = pytest.mark.skipif(not posix_tools(), reason="sin find ni grep")


def run_with_find(state) -> list[str]:
    tools = posix_tools()
    command = build_command(state)
    argv = [os.path.join(tools, "find" + (".exe" if sys.platform == "win32" else "")), *[arg.replace("\\", "/") if arg in state["roots"] else arg for arg in command.argv[1:]]]
    done = subprocess.run(argv, capture_output=True, env={**os.environ, "PATH": tools + os.pathsep + os.environ.get("PATH", ""), **command.environment})
    return sorted(os.path.basename(path.rstrip("/")) for path in done.stdout.decode("utf-8", "replace").split("\0") if path)


@needs_find
@pytest.mark.parametrize("changes", [
    {"name": {"text": "*mascot*"}}, {"name": {"text": "mascota"}}, {"name": {"text": "mascota", "case": True}}, {"name": {"text": "nota.txt", "wildcards": False, "partial": False}},
    {"name": {"text": "nota hondo", "any_word": True}}, {"subfolders": False}, {"kind": {"kind": "folders"}}, {"kind": {"kind": "files"}},
    {"kind": {"kind": "type", "content_type": "images"}}, {"content": {"text": "aguja"}}, {"content": {"text": "AGUJA", "case": True}}, {"content": {"text": "agu", "partial": False}},
    {"content": {"text": "aguja|perro", "regex": True}}, {"size": {"op": "greater", "first": 1, "unit": "KB"}}, {"size": {"op": "smaller", "first": 10, "unit": "bytes"}},
    {"size": {"op": "between", "first": 40, "second": 60, "unit": "bytes"}}, {"exclude": ["excluida"]}, {"exclude": ["excluida/*", "sub"]},
    {"date": {"op": "within", "amount": 1, "unit": "days"}}, {"date": {"op": "on", "first": date.today().isoformat()}},
    {"date": {"op": "before", "first": (date.today() + timedelta(days=1)).isoformat()}}, {"date": {"op": "after", "first": date.today().isoformat()}},
    {"advanced": True, "clauses": [clause("name", query("informe")), clause("name", query("hondo"), join="or")]},
    {"advanced": True, "clauses": [clause("name", query("mascota")), clause("kind", {"kind": "folders"}, negate=True)]},
])
def test_command_and_program_find_the_same(tree, changes):
    state = spec(tree, **changes)
    assert run_with_find(state) == names_of(run_search(state)), describe_command(build_command(state))


def test_archives_without_tool_or_error_do_not_stop_the_search(tree):
    (tree / "roto.zip").write_bytes(b"no es un zip")
    found = run_search(spec(tree, name={"text": "nota"}, archives=True))
    assert names_of(found) == ["nota.txt"] and str(tree / "roto.zip") in found.denied
    assert archive is not None


# ---- lo que solo se ve con el `find` y el `grep` de Linux ----
needs_linux = pytest.mark.skipif(sys.platform == "win32", reason="busca con find y grep solo en Linux")


@needs_linux
@pytest.mark.parametrize("content", [{"text": "aguja"}, {"text": "aguja", "diacritics": True}])
def test_a_pipe_in_the_folder_does_not_hang_the_search_of_text(tmp_path, content):
    (tmp_path / "con.txt").write_text("aguja", encoding="utf-8")
    (tmp_path / "enlace.txt").symlink_to(tmp_path / "con.txt")
    os.mkfifo(tmp_path / "tubo")
    limit = time.monotonic() + 10
    found = file_search.run_search(spec(tmp_path, content=content), lambda: time.monotonic() > limit)
    assert not found.cancelled
    assert sorted(os.path.basename(path) for path in found.paths) == ["con.txt", "enlace.txt"]


@needs_linux
def test_cancelling_is_noticed_while_find_writes_nothing_and_ends_what_it_runs(tmp_path, monkeypatch):
    os.mkfifo(tmp_path / "tubo")
    monkeypatch.setattr(file_search, "_grep_tests", lambda _query: ["-exec", "grep", "-q", "aguja", "--", "{}", ";"])  # sin la protección: se queda leyendo la tubería
    limit = time.monotonic() + 0.3
    spawned = []
    real = subprocess.Popen

    def remember(*arguments, **options):
        spawned.append(real(*arguments, **options))
        return spawned[-1]

    monkeypatch.setattr(file_search.subprocess, "Popen", remember)
    found = file_search.run_search(spec(tmp_path, content={"text": "aguja"}), lambda: time.monotonic() > limit)
    assert found.cancelled and time.monotonic() - limit < 5
    end = time.monotonic() + 5  # el sistema tarda un poco en recoger al grep, que se ha quedado sin padre
    with pytest.raises(ProcessLookupError):
        while time.monotonic() < end:
            os.killpg(spawned[0].pid, 0)
            time.sleep(0.05)


@needs_linux
def test_a_folder_that_is_a_link_is_searched(tmp_path):
    (tmp_path / "real").mkdir()
    (tmp_path / "real" / "nota.txt").write_text("x", encoding="utf-8")
    (tmp_path / "enlace").symlink_to(tmp_path / "real", target_is_directory=True)
    found = file_search.run_search(spec(tmp_path / "enlace", name=query("nota")))
    assert [os.path.basename(path) for path in found.paths] == ["nota.txt"]


@needs_linux
@pytest.mark.parametrize("test", [{"op": "after", "first": "2000-01-01"}, {"op": "within", "amount": 1, "unit": "days"}])
def test_the_creation_date_is_not_asked_of_find(tmp_path, test):
    """`find -newerBt` termina con un error en cuanto se le pide, y no se encontraría nada."""
    (tmp_path / "nota.txt").write_text("x", encoding="utf-8")
    state = spec(tmp_path, date={**test, "field": "created"})
    assert not any(word.startswith("-newerB") or word == "-Bmin" for word in build_command(state).argv)
    assert [os.path.basename(path) for path in file_search.run_search(state).paths] == ["nota.txt"]


@needs_linux
@pytest.mark.skipif(not hasattr(os, "geteuid") or os.geteuid() == 0, reason="root puede leerlo todo")
def test_a_folder_without_permission_is_reported(tmp_path):
    closed = tmp_path / "cerrada"
    closed.mkdir()
    closed.chmod(0)
    try:
        found = file_search.run_search(spec(tmp_path, name=query("nada")))
    finally:
        closed.chmod(0o755)
    assert found.denied == [str(closed)]


def test_a_failure_of_find_is_not_an_empty_result():
    found = file_search.Found()
    with pytest.raises(OSError):
        file_search._report_find_errors("find: predicado inválido `-x'\n".encode(), 1, found)


@needs_linux
def test_wildcards_in_the_text_need_no_escapes_that_grep_complains_about(tmp_path):
    (tmp_path / "a.txt").write_text("hola - mundo (1) a.b+c\n", encoding="utf-8")
    argv = build_command(spec(tmp_path, content={"text": "hola - m*(1) a.b+c", "wildcards": True})).argv
    pattern = argv[argv.index("-e") + 1]
    done = subprocess.run(["grep", "-E", "-e", pattern, str(tmp_path / "a.txt")], capture_output=True)
    assert done.returncode == 0 and done.stderr == b""
