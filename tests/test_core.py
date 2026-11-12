import json
import os
import shutil
import sys
import tarfile
import threading
import time
import zipfile
from datetime import date, datetime

import pytest

from directory_commander_tux_tux.core import archive, commands, context_menu, desktop_apps, fsops, listing, rename, settings_io, utilities
from directory_commander_tux_tux.core.config import Config
from directory_commander_tux_tux.core.fsops import Collision, ProgressSink


def write(path, text="x"):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as fout:
        fout.write(text)
    return str(path)


def read(path):
    with open(path, encoding="utf-8") as fin:
        return fin.read()


# ---- listado y orden ----

def test_natural_sort_dirs_first(tmp_path):
    for name in ("file10.txt", "file2.txt", "File1.txt"):
        write(tmp_path / name)
    (tmp_path / "zdir").mkdir()
    (tmp_path / "adir").mkdir()
    names = [e.name for e in listing.sort_entries(listing.list_dir(str(tmp_path)))]
    assert names == ["adir", "zdir", "File1.txt", "file2.txt", "file10.txt"]


def test_sort_reverse_keeps_dirs_first(tmp_path):
    write(tmp_path / "a.txt")
    write(tmp_path / "b.txt")
    (tmp_path / "d").mkdir()
    names = [e.name for e in listing.sort_entries(listing.list_dir(str(tmp_path)), "name", True)]
    assert names == ["d", "b.txt", "a.txt"]


def test_extensionless_text_file_type_and_sort(tmp_path, monkeypatch):
    write(tmp_path / "docker.txt", "contenido")
    write(tmp_path / "Notas", "mis notas")
    (tmp_path / "binario").write_bytes(b"\x00\x01\x02\x03")
    entries = {e.name: e for e in listing.list_dir(str(tmp_path))}
    # En Linux, Notas se clasifica como Archivo TXT y binario como Archivo
    monkeypatch.setattr(sys, "platform", "linux")
    assert entries["docker.txt"].type_label == "Archivo TXT"
    assert entries["Notas"].type_label == "Archivo TXT"
    assert entries["binario"].type_label == "Archivo"
    sorted_names = [e.name for e in listing.sort_entries(list(entries.values()), "type")]
    assert sorted_names == ["binario", "docker.txt", "Notas"]
    # En Windows, Notas mantiene su etiqueta de Archivo
    monkeypatch.setattr(sys, "platform", "win32")
    assert entries["Notas"].type_label == "Archivo"
    assert entries["docker.txt"].type_label == "Archivo TXT"


@pytest.mark.skipif(sys.platform == "win32", reason="regla de ocultos de POSIX")
def test_hidden_posix(tmp_path):
    write(tmp_path / ".secreto")
    write(tmp_path / "visible")
    assert [e.name for e in listing.list_dir(str(tmp_path))] == ["visible"]
    assert len(listing.list_dir(str(tmp_path), show_hidden=True)) == 2


def test_flat_view(tmp_path):
    write(tmp_path / "a" / "b" / "c.txt")
    write(tmp_path / "d.txt")
    entries = listing.list_dir(str(tmp_path), flat=True)
    assert sorted(e.display_name for e in entries) == sorted([os.path.join("a", "b", "c.txt"), "d.txt"])
    assert all(not e.is_dir for e in entries)


def test_filter():
    assert listing.matches_filter("Foto.JPG", "*.jpg")
    assert listing.matches_filter("Foto.JPG", "foto")
    assert not listing.matches_filter("Foto.JPG", "*.png")
    assert listing.matches_filter("x", "")


def test_format_size():
    assert listing.format_size(10) == "10 B"
    assert listing.format_size(2048) == "2.00 KB"
    assert listing.format_size(int(3.64 * 1024 ** 4)) == "3.64 TB"
    assert listing.format_size(int(23.2 * 1024 ** 3)) == "23.20 GB"
    assert listing.format_size(1023) == "1023 B"


# ---- copiar y mover ----

def test_copy_tree_and_metadata(tmp_path):
    src = tmp_path / "src"
    write(src / "sub" / "a.txt", "hola")
    os.utime(src / "sub" / "a.txt", (1000000, 1000000))
    dest = tmp_path / "dest"
    dest.mkdir()
    result = fsops.transfer([str(src)], str(dest))
    assert result.ok
    assert read(dest / "src" / "sub" / "a.txt") == "hola"
    assert int(os.path.getmtime(dest / "src" / "sub" / "a.txt")) == 1000000
    assert (src / "sub" / "a.txt").exists()


def test_move_removes_source(tmp_path):
    src = write(tmp_path / "a.txt", "1")
    dest = tmp_path / "d"
    dest.mkdir()
    assert fsops.transfer([src], str(dest), move=True).ok
    assert not os.path.exists(src)
    assert read(dest / "a.txt") == "1"


@pytest.mark.parametrize("policy,expected,extra", [
    (Collision.REPLACE, "nuevo", False),
    (Collision.SKIP, "viejo", False),
    (Collision.RENAME, "viejo", True),
])
def test_collision_policies(tmp_path, policy, expected, extra):
    src = write(tmp_path / "s" / "a.txt", "nuevo")
    write(tmp_path / "d" / "a.txt", "viejo")
    fsops.transfer([src], str(tmp_path / "d"), policy=policy)
    assert read(tmp_path / "d" / "a.txt") == expected
    assert (tmp_path / "d" / "a (2).txt").exists() is extra


def test_skip_on_move_keeps_source(tmp_path):
    src = tmp_path / "s" / "dir"
    write(src / "a.txt", "nuevo")
    write(tmp_path / "d" / "dir" / "a.txt", "viejo")
    result = fsops.transfer([str(src)], str(tmp_path / "d"), move=True, policy=Collision.SKIP)
    assert result.skipped == 1
    assert read(src / "a.txt") == "nuevo"
    assert read(tmp_path / "d" / "dir" / "a.txt") == "viejo"


def test_copy_into_itself_rejected(tmp_path):
    src = tmp_path / "a"
    (src / "b").mkdir(parents=True)
    result = fsops.transfer([str(src)], str(src / "b"))
    assert len(result.errors) == 1
    assert not (src / "b" / "a").exists()


def test_sibling_with_same_prefix_is_not_inside(tmp_path):
    (tmp_path / "abc").mkdir()
    (tmp_path / "abcd").mkdir()
    assert not fsops.is_inside(str(tmp_path / "abcd"), str(tmp_path / "abc"))
    assert fsops.transfer([str(tmp_path / "abc")], str(tmp_path / "abcd")).ok


def test_copy_same_dir_duplicates(tmp_path):
    src = write(tmp_path / "a.txt", "1")
    fsops.transfer([src], str(tmp_path))
    assert (tmp_path / "a (2).txt").exists()


def test_move_same_dir_is_noop(tmp_path):
    src = write(tmp_path / "a.txt", "1")
    result = fsops.transfer([src], str(tmp_path), move=True)
    assert result.skipped == 1 and os.path.exists(src)


def test_type_conflict_never_destroys(tmp_path):
    src = write(tmp_path / "s" / "x", "archivo")
    write(tmp_path / "d" / "x" / "dentro.txt", "importante")
    result = fsops.transfer([src], str(tmp_path / "d"), policy=Collision.REPLACE)
    assert result.errors
    assert read(tmp_path / "d" / "x" / "dentro.txt") == "importante"


def test_cancel_leaves_no_partial(tmp_path):
    src = tmp_path / "big.bin"
    src.write_bytes(b"0" * (fsops.CHUNK * 3))
    dest = tmp_path / "d"
    dest.mkdir()
    sink = ProgressSink()
    sink._on_progress = lambda done, total, name: sink.cancel()
    result = fsops.transfer([str(src)], str(dest), sink=sink)
    assert result.cancelled
    assert os.listdir(dest) == []


def test_replace_failure_keeps_original_when_cancelled(tmp_path):
    src = tmp_path / "big.bin"
    src.write_bytes(b"0" * (fsops.CHUNK * 3))
    write(tmp_path / "d" / "big.bin", "original")
    sink = ProgressSink()
    sink._on_progress = lambda done, total, name: sink.cancel()
    fsops.transfer([str(src)], str(tmp_path / "d"), policy=Collision.REPLACE, sink=sink)
    assert read(tmp_path / "d" / "big.bin") == "original"


def test_move_cross_device_falls_back_to_copy(tmp_path, monkeypatch):
    src = tmp_path / "s" / "dir"
    write(src / "a.txt", "1")
    (tmp_path / "d").mkdir()
    monkeypatch.setattr(fsops.os, "rename", lambda *a: (_ for _ in ()).throw(OSError(18, "EXDEV")))
    assert fsops.transfer([str(src)], str(tmp_path / "d"), move=True).ok
    assert not src.exists()
    assert read(tmp_path / "d" / "dir" / "a.txt") == "1"


def test_move_fallback_keeps_source_when_copy_fails(tmp_path, monkeypatch):
    src = tmp_path / "s" / "dir"
    write(src / "a.txt", "1")
    (tmp_path / "d").mkdir()
    monkeypatch.setattr(fsops.os, "rename", lambda *a: (_ for _ in ()).throw(OSError(18, "EXDEV")))
    real_open = open

    def failing_open(path, mode="r", *a, **k):
        if "b" in mode and str(path).endswith("a.txt"):
            raise PermissionError("denegado")
        return real_open(path, mode, *a, **k)

    monkeypatch.setattr("builtins.open", failing_open)
    result = fsops.transfer([str(src)], str(tmp_path / "d"), move=True)
    assert result.errors
    assert (src / "a.txt").exists()


def symlinks_available(tmp_path):
    try:
        os.symlink(tmp_path, tmp_path / "probe", target_is_directory=True)
        os.unlink(tmp_path / "probe")
        return True
    except OSError:
        return False


def test_delete_symlink_keeps_target(tmp_path):
    if not symlinks_available(tmp_path):
        pytest.skip("este sistema no permite crear enlaces simbólicos")
    target = tmp_path / "real"
    write(target / "a.txt")
    link = tmp_path / "enlace"
    os.symlink(target, link, target_is_directory=True)
    assert fsops.delete_paths([str(link)], permanent=True).ok
    assert (target / "a.txt").exists() and not os.path.lexists(link)


def test_copy_does_not_follow_symlink(tmp_path):
    if not symlinks_available(tmp_path):
        pytest.skip("este sistema no permite crear enlaces simbólicos")
    target = tmp_path / "real"
    write(target / "a.txt")
    src = tmp_path / "s"
    src.mkdir()
    os.symlink(target, src / "enlace", target_is_directory=True)
    dest = tmp_path / "d"
    dest.mkdir()
    assert fsops.transfer([str(src)], str(dest)).ok
    assert os.path.islink(dest / "s" / "enlace")


# ---- borrar ----

def test_trash_failure_never_deletes(tmp_path):
    path = write(tmp_path / "a.txt")
    result = fsops.delete_paths([path], trash=lambda p: False)
    assert result.errors and os.path.exists(path)


def test_permanent_delete(tmp_path):
    write(tmp_path / "d" / "a.txt")
    assert fsops.delete_paths([str(tmp_path / "d")], permanent=True).ok
    assert not (tmp_path / "d").exists()


def test_filesystem_root_can_never_be_deleted():
    root = os.path.abspath(os.sep)
    assert fsops.is_filesystem_root(root)
    result = fsops.delete_paths([root], permanent=True)
    assert result.errors and result.done == 0
    with pytest.raises(PermissionError):
        fsops.remove_tree(root)
    assert fsops.transfer([root], str(os.getcwd()), move=True).errors


def test_transfer_to_something_that_is_not_a_folder(tmp_path):
    src = write(tmp_path / "a.txt")
    assert fsops.transfer([src], str(tmp_path / "no-existe")).errors
    assert fsops.transfer([src], "<computer>").errors
    assert os.path.exists(src)


def test_validate_name():
    assert fsops.validate_name("") and fsops.validate_name("..") and fsops.validate_name("a/b")
    assert fsops.validate_name("bueno.txt") is None


def test_hash(tmp_path):
    path = write(tmp_path / "a", "abc")
    assert fsops.hash_file(path, "sha256").startswith("ba7816bf")
    with pytest.raises(ValueError):
        fsops.hash_file(path, "crc")


def test_tree_size(tmp_path):
    write(tmp_path / "d" / "a", "12345")
    write(tmp_path / "d" / "e" / "b", "123")
    assert fsops.tree_size(str(tmp_path / "d")) == (8, 2)


# ---- renombrado ----

def test_rename_numbering_like_preset():
    spec = rename.RenameSpec("regex", "(.*)", "[#]", True, "none", 1, 1, 4)
    assert rename.compute_names(["a.txt", "b.jpg"], spec) == ["0001.txt", "0002.jpg"]


def test_rename_regex_groups_and_case():
    spec = rename.RenameSpec("regex", r"(\w+)_(\w+)", r"\2 \1", True, "upper")
    assert rename.compute_names(["uno_dos.txt"], spec) == ["DOS UNO.txt"]


def test_rename_wildcard():
    spec = rename.RenameSpec("wildcard", "foto*", "imagen*", True)
    assert rename.compute_names(["foto1.jpg", "otro.jpg"], spec) == ["imagen1.jpg", "otro.jpg"]


def test_rename_simple_is_literal():
    spec = rename.RenameSpec("simple", "a.b", r"x\1", False)
    assert rename.compute_names(["a.b.txt", "axb"], spec) == [r"x\1.txt", "axb"]


def test_rename_bad_regex():
    with pytest.raises(ValueError):
        rename.compute_names(["a"], rename.RenameSpec("regex", "(", ""))
    with pytest.raises(ValueError):
        rename.compute_names(["a"], rename.RenameSpec("regex", "a", r"\5"))


def test_rename_problems():
    problems = rename.find_problems(["a", "b", "c"], ["x", "x", "otro"], {"otro"})
    assert set(problems) == {0, 1, 2}


def test_apply_renames_swap(tmp_path):
    write(tmp_path / "a", "A")
    write(tmp_path / "b", "B")
    assert rename.apply_renames(str(tmp_path), ["a", "b"], ["b", "a"]) == []
    assert read(tmp_path / "a") == "B" and read(tmp_path / "b") == "A"


def test_apply_renames_no_overwrite(tmp_path):
    write(tmp_path / "a", "A")
    write(tmp_path / "ocupado", "O")
    errors = rename.apply_renames(str(tmp_path), ["a"], ["ocupado"])
    assert errors and read(tmp_path / "ocupado") == "O" and read(tmp_path / "a") == "A"


# ---- comandos ----

def test_parse_command():
    cmd = commands.parse('copy MOVE TO="C:\\mi carpeta" here')
    assert cmd.name == "copy" and cmd.has("move") and cmd.has("HERE")
    assert cmd.get("to") == "C:\\mi carpeta"


def test_parse_empty():
    assert commands.parse("   ") is None


def test_parse_path_keeps_case():
    assert commands.parse('Go "/home/Ana/Mis Fotos"').first == "/home/Ana/Mis Fotos"


def test_expand_args_no_injection():
    tokens = commands.tokenize("prog --x {filepath} {allfilepath}")
    args = commands.expand_args(tokens, "/s", "/d", ["/a b; rm -rf", "/c"])
    assert args == ["prog", "--x", "/a b; rm -rf", "/a b; rm -rf", "/c"]


# ---- comprimidos ----

@pytest.fixture
def zip_file(tmp_path):
    path = tmp_path / "datos.zip"
    with zipfile.ZipFile(path, "w") as zf:
        zf.writestr("carpeta/a.txt", "A")
        zf.writestr("b.txt", "B")
    return str(path)


def test_split_archive_path(zip_file):
    assert archive.split_archive_path(zip_file) == (zip_file, "")
    assert archive.split_archive_path(os.path.join(zip_file, "carpeta", "a.txt")) == (zip_file, "carpeta/a.txt")
    assert archive.split_archive_path(os.path.dirname(zip_file)) is None


def test_list_zip_root_and_child(zip_file):
    assert sorted(e.name for e in listing.list_dir(zip_file)) == ["b.txt", "carpeta"]
    assert [e.name for e in listing.list_dir(os.path.join(zip_file, "carpeta"))] == ["a.txt"]


def test_copy_from_zip(zip_file, tmp_path):
    dest = tmp_path / "out"
    dest.mkdir()
    assert fsops.transfer([os.path.join(zip_file, "carpeta")], str(dest)).ok
    assert read(dest / "carpeta" / "a.txt") == "A"


def test_write_into_zip_rejected(zip_file, tmp_path):
    src = write(tmp_path / "x.txt")
    assert fsops.transfer([src], zip_file).errors


def test_zip_slip_is_ignored(tmp_path):
    path = tmp_path / "malo.zip"
    with zipfile.ZipFile(path, "w") as zf:
        zf.writestr("../../fuera.txt", "x")
        zf.writestr("/abs.txt", "x")
        zf.writestr("C:/win.txt", "x")
        zf.writestr("bien.txt", "x")
    out = tmp_path / "ex" / "out"
    out.mkdir(parents=True)
    archive.extract_to(str(path), "", str(out))
    assert os.listdir(out) == ["bien.txt"]
    assert not (tmp_path / "ex" / "fuera.txt").exists()


def test_tar_listing_and_extract(tmp_path):
    write(tmp_path / "src" / "a.txt", "T")
    path = tmp_path / "x.tar.gz"
    with tarfile.open(path, "w:gz") as tf:
        tf.add(tmp_path / "src", arcname="src")
    assert [e.name for e in listing.list_dir(str(path))] == ["src"]
    out = tmp_path / "out"
    out.mkdir()
    assert fsops.transfer([os.path.join(str(path), "src")], str(out)).ok
    assert read(out / "src" / "a.txt") == "T"


def test_create_zip(tmp_path):
    write(tmp_path / "d" / "e" / "a.txt", "Z")
    (tmp_path / "d" / "vacia").mkdir()
    target = tmp_path / "r.zip"
    archive.create_zip([str(tmp_path / "d")], str(target))
    names = zipfile.ZipFile(target).namelist()
    assert "d/e/a.txt" in names and "d/vacia/" in names


def test_corrupt_archive_raises_oserror(tmp_path):
    path = tmp_path / "roto.zip"
    path.write_bytes(b"no soy un zip")
    with pytest.raises(OSError):
        listing.list_dir(str(path))


# ---- utilidades ----

def test_compare_dirs(tmp_path):
    a, b = tmp_path / "a", tmp_path / "b"
    write(a / "igual.txt", "1")
    write(b / "igual.txt", "1")
    os.utime(a / "igual.txt", (1000, 1000))
    os.utime(b / "igual.txt", (1000, 1000))
    write(a / "solo_a.txt")
    write(b / "dir_b" / "x.txt")
    write(a / "cambia.txt", "nuevo!")
    write(b / "cambia.txt", "v")
    os.utime(a / "cambia.txt", (5000, 5000))
    os.utime(b / "cambia.txt", (1000, 1000))
    result = {i.rel: i.status for i in utilities.compare_dirs(str(a), str(b))}
    assert result == {"solo_a.txt": "only_a", "dir_b": "only_b", "cambia.txt": "newer_a"}


def test_compare_by_content_detects_same_size_change(tmp_path):
    a, b = tmp_path / "a", tmp_path / "b"
    write(a / "x", "aaaa")
    write(b / "x", "bbbb")
    os.utime(a / "x", (1000, 1000))
    os.utime(b / "x", (1000, 1000))
    assert utilities.compare_dirs(str(a), str(b)) == []
    assert utilities.compare_dirs(str(a), str(b), by_content=True)[0].status == "differs"


def test_find_duplicates(tmp_path):
    write(tmp_path / "a.txt", "igual")
    write(tmp_path / "sub" / "b.txt", "igual")
    write(tmp_path / "c.txt", "otro!")
    write(tmp_path / "vacio.txt", "")
    write(tmp_path / "vacio2.txt", "")
    groups = utilities.find_duplicates([str(tmp_path)])
    assert groups == [sorted([str(tmp_path / "a.txt"), str(tmp_path / "sub" / "b.txt")])]


def test_find_duplicates_overlapping_roots_not_duplicate(tmp_path):
    write(tmp_path / "s" / "a.txt", "igual")
    assert utilities.find_duplicates([str(tmp_path), str(tmp_path / "s")]) == []


# ---- configuración ----

def test_config_roundtrip(tmp_path):
    cfg = Config(str(tmp_path))
    cfg["theme"] = "light"
    cfg.save()
    assert Config(str(tmp_path))["theme"] == "light"


def test_corrupt_config_recovers(tmp_path):
    (tmp_path / "config.json").write_text("{roto", encoding="utf-8")
    cfg = Config(str(tmp_path))
    assert cfg.warning and cfg["theme"] == "dark"
    assert (tmp_path / "config.json.corrupto").exists()


def test_config_from_previous_name_is_adopted_once(tmp_path, monkeypatch):
    monkeypatch.delenv("DIRECTORY_COMMANDER_TUX_TUX_CONFIG", raising=False)
    monkeypatch.setenv("APPDATA", str(tmp_path))
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    legacy_name = "DirectoryTux" if sys.platform == "win32" else "directory-tux"
    new_name = "DirectoryCommanderTuxTux" if sys.platform == "win32" else "directory-commander-tux-tux"
    legacy = tmp_path / legacy_name / "config.json"
    legacy.parent.mkdir()
    legacy.write_text('{"config_version": 2, "favorites": [{"label": "f", "path": "/x"}]}', encoding="utf-8")
    cfg = Config()
    assert cfg["favorites"] == [{"label": "f", "path": "/x"}]
    assert (tmp_path / new_name / "config.json").exists() and legacy.exists()
    legacy.write_text('{"config_version": 2, "favorites": []}', encoding="utf-8")
    assert Config()["favorites"] == [{"label": "f", "path": "/x"}]


def test_explicit_config_folder_does_not_adopt_previous_config(tmp_path, monkeypatch):
    monkeypatch.setenv("APPDATA", str(tmp_path))
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    legacy = tmp_path / ("DirectoryTux" if sys.platform == "win32" else "directory-tux") / "config.json"
    legacy.parent.mkdir()
    legacy.write_text('{"config_version": 2, "favorites": [{"label": "f", "path": "/x"}]}', encoding="utf-8")
    monkeypatch.setenv("DIRECTORY_COMMANDER_TUX_TUX_CONFIG", str(tmp_path / "elegida"))
    assert Config()["favorites"] == []


def test_without_previous_config_starts_clean(tmp_path, monkeypatch):
    monkeypatch.delenv("DIRECTORY_COMMANDER_TUX_TUX_CONFIG", raising=False)
    monkeypatch.setenv("APPDATA", str(tmp_path))
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    assert Config()["favorites"] == []


def test_old_config_is_migrated_keeping_user_data(tmp_path):
    (tmp_path / "config.json").write_text(
        '{"dual": "Off", "toolbar": [{"label": "x", "command": "Go UP"}], '
        '"favorites": [{"label": "f", "path": "/x"}]}', encoding="utf-8")
    cfg = Config(str(tmp_path))
    assert cfg["favorites"] == [{"label": "f", "path": "/x"}]
    assert cfg["toolbar"][0]["icon"] == "goback"
    assert "dual" not in cfg.data


# ---- deshacer, nombres y enlaces ----

def test_transfer_records_what_it_created(tmp_path):
    src = write(tmp_path / "s" / "a.txt", "1")
    write(tmp_path / "d" / "b.txt", "x")
    existing_src = write(tmp_path / "s" / "b.txt", "2")
    result = fsops.transfer([src, existing_src], str(tmp_path / "d"), policy=Collision.REPLACE)
    assert result.placed == [(src, str(tmp_path / "d" / "a.txt"), True),
                             (existing_src, str(tmp_path / "d" / "b.txt"), False)]


def test_transfer_rename_policy_counts_as_created(tmp_path):
    src = write(tmp_path / "s" / "a.txt", "1")
    write(tmp_path / "d" / "a.txt", "x")
    result = fsops.transfer([src], str(tmp_path / "d"), policy=Collision.RENAME)
    assert result.placed == [(src, str(tmp_path / "d" / "a (2).txt"), True)]


def test_transfer_new_name(tmp_path):
    src = write(tmp_path / "s" / "a.txt", "1")
    (tmp_path / "d").mkdir()
    assert fsops.transfer([src], str(tmp_path / "d"), new_name="otro.txt").ok
    assert read(tmp_path / "d" / "otro.txt") == "1"
    with pytest.raises(ValueError):
        fsops.transfer([src, src], str(tmp_path / "d"), new_name="x")


def test_transfer_new_name_in_same_folder(tmp_path):
    src = write(tmp_path / "a.txt", "1")
    assert fsops.transfer([src], str(tmp_path), new_name="b.txt").ok
    assert read(tmp_path / "b.txt") == "1" and os.path.exists(src)


def test_hardlink(tmp_path):
    src = write(tmp_path / "a.txt", "1")
    (tmp_path / "d").mkdir()
    link = fsops.make_link(src, str(tmp_path / "d"), "hardlink")
    assert os.path.samefile(src, link)
    with pytest.raises(ValueError):
        fsops.make_link(src, str(tmp_path / "d"), "junction")


def test_attributes_roundtrip(tmp_path):
    path = write(tmp_path / "a.txt")
    key = "h" if os.name == "nt" else "ox"
    other = "a" if os.name == "nt" else "ur"
    before = fsops.get_attributes(path)
    fsops.set_attributes(path, {key: True})
    after = fsops.get_attributes(path)
    assert after[key] is True and after[other] == before[other]
    fsops.set_attributes(path, {key: False})
    assert fsops.get_attributes(path)[key] is False


# ---- compatibilidad de comandos ----

@pytest.mark.parametrize("line", [
    "Go UP BACK USEQUALKEYS", "Go HOMEDIR USEQUALKEYS", "Go REFRESH=source", "Go /desktop USEQUALKEYS",
    'Go "C:\\mi carpeta"', "Copy MOVE HERE CREATEFOLDER", "Copy MAKELINK=softlink", "Delete SHIFT",
    "Set VIEW=Cycle,LargeIcons,List", "Set SPACER", "Set CONTENTTYPE", "@nodeselect", "Select", "Show SLIDESHOW FULLSCREEN",
    "Marker TOOLBAR", "Run prog --x {filepath}",
])
def test_supported_lines(line):
    assert commands.unsupported_reason(line, {"VIEW"}) is None


@pytest.mark.parametrize("line", [
    "Copy UPDATEALL X", "Copy MAKELINK=junction", "Delete SECURE", "Set ADMIN=Toggle", "Copy ZIP SENDMAIL",
    "Properties FOLDEROPTIONS", 'Go ftp:// USEQUALKEYS', "Go /network", "=return(1)", "[", "Print",
    "https://{gps-redirect}?page=faq", "Help CHECKUPDATE", "Select DUPES", "Set UTILITY=Email,Toggle",
    "Toolbar LIST=sets", 'If (x = "y") Then', "Find PRESET=!list HERE",
])
def test_unsupported_lines_are_refused(line):
    assert commands.unsupported_reason(line, {"VIEW"}) is not None


@pytest.mark.parametrize("line", [
    "Copy UPDATEALL", "Copy UPDATEEXISTING", "Copy ARCHIVE=single HERE", "Copy ADDTOZIP", "Recent CLEAR",
    "Print FOLDER AS=txt TO=clip FLATVIEW=no QUIET", "Print Folder", "CreateFolder ZIP", "Image HERE CONVERT=png",
    "Image ROTATE=90 HERE REPLACE PRESERVEDATE", 'Toolbar NAME="Favorites Bar" STATE=top LOCAL=no TOGGLE',
    "Select SOURCETODEST=in,noext DESELECTNOMATCH", "Select DESTTOSOURCE=notin DESELECTNOMATCH", "Undo REDO",
    "Go NEWTAB=nofocus,findexisting FROMSEL", "Go TABGROUPSAVE !both", "Go DRIVEBUTTONS=lettersbeforelabels HEADING",
    'Copy DUPLICATE PATTERN * AS "* - {date|D#yyyy-MM-dd} {time|T#HH-mm-ss}" IGNOREEXT', "http://example.test/ayuda",
    "Rename PRESET=!list,favesonly HEADING", "Close PROGRAM=confirm", "Set UTILITY=FileLog,Toggle",
])
def test_lines_supported_now(line):
    assert commands.unsupported_reason(line, {"UTILITY"}) is None


# ---- botones con varias líneas ----

def test_keydown_blocks_run_only_one_variant():
    lines = ["@keydown:none", "Copy", "@keydown:shift", "Copy MOVE", "@keydown:ctrl", "Delete"]
    assert commands.plan_function(lines, set()) == [("run", "Copy")]
    assert commands.plan_function(lines, {"shift"}) == [("run", "Copy MOVE")]
    assert commands.plan_function(lines, {"ctrl"}) == [("run", "Delete")]
    assert commands.plan_function(lines, {"alt"}) == []


def test_keydown_common_any_and_negation():
    lines = ["@keydown:any", "A", "@keydown:!shift", "B", "@keydown:ctrlshift", "C", "@keydown:common", "D"]
    assert commands.plan_function(lines, set()) == [("run", "B"), ("run", "D")]
    assert commands.plan_function(lines, {"shift"}) == [("run", "A"), ("run", "D")]
    assert commands.plan_function(lines, {"ctrl", "shift"}) == [("run", "A"), ("run", "B"), ("run", "C"), ("run", "D")]


def test_confirm_modifier_is_a_step():
    plan = commands.plan_function(["@nodeselect", "@confirm:¿Seguro?|Sí|No", "Delete"])
    assert plan == [("confirm", "¿Seguro?"), ("run", "Delete")]


@pytest.mark.parametrize("modifier", ["@if:Set VIEW=Details", "@set x=1", "@admin", "@ctx:=x", "@runmode:hide", "@filesonly"])
def test_unknown_modifier_blocks_the_function(modifier):
    # Ignorarlo cambiaría lo que hace el botón: es mejor desactivarlo.
    assert commands.function_unavailable([modifier, "Copy"], set()) is not None


def test_display_modifiers_do_not_block():
    lines = ["@nodeselect", "@disablenosel", "@toggle:if", "@icon:x,Set VIEW=Details", "@keydown:none", "Copy"]
    assert commands.function_unavailable(lines, set()) is None


def test_one_bad_line_blocks_the_whole_function():
    assert commands.function_unavailable(["Copy", "Delete SECURE"], set()) is not None


# ---- actualizar, dividir, unir ----

def _files_with_times(tmp_path):
    src = tmp_path / "s"
    dst = tmp_path / "d"
    write(src / "nuevo.txt", "n")
    write(src / "reciente.txt", "src")
    write(src / "antiguo.txt", "src")
    write(dst / "reciente.txt", "dst")
    write(dst / "antiguo.txt", "dst")
    os.utime(src / "reciente.txt", (5000, 5000))
    os.utime(dst / "reciente.txt", (1000, 1000))
    os.utime(src / "antiguo.txt", (1000, 1000))
    os.utime(dst / "antiguo.txt", (5000, 5000))
    return [str(src / n) for n in ("nuevo.txt", "reciente.txt", "antiguo.txt")], dst


def test_update_all_copies_new_and_newer_only(tmp_path):
    sources, dst = _files_with_times(tmp_path)
    result = fsops.transfer(sources, str(dst), policy=Collision.UPDATE_ALL)
    assert result.skipped == 1
    assert read(dst / "nuevo.txt") == "n" and read(dst / "reciente.txt") == "src" and read(dst / "antiguo.txt") == "dst"


def test_update_existing_never_adds_files(tmp_path):
    sources, dst = _files_with_times(tmp_path)
    fsops.transfer(sources, str(dst), policy=Collision.UPDATE_EXISTING)
    assert not (dst / "nuevo.txt").exists()
    assert read(dst / "reciente.txt") == "src" and read(dst / "antiguo.txt") == "dst"


def test_update_inside_folder_recurses(tmp_path):
    write(tmp_path / "s" / "dir" / "a.txt", "src")
    write(tmp_path / "d" / "dir" / "a.txt", "dst")
    os.utime(tmp_path / "s" / "dir" / "a.txt", (9000, 9000))
    os.utime(tmp_path / "d" / "dir" / "a.txt", (1000, 1000))
    fsops.transfer([str(tmp_path / "s" / "dir")], str(tmp_path / "d"), policy=Collision.UPDATE_ALL)
    assert read(tmp_path / "d" / "dir" / "a.txt") == "src"


def test_split_and_join_roundtrip(tmp_path):
    data = bytes(range(256)) * 10
    src = tmp_path / "datos.bin"
    src.write_bytes(data)
    out = tmp_path / "partes"
    out.mkdir()
    result = fsops.split_file(str(src), str(out), 1000)
    assert result.ok and result.done == 3
    assert sorted(os.listdir(out)) == ["datos.bin.001", "datos.bin.002", "datos.bin.003"]
    assert (out / "datos.bin.003").stat().st_size == 560
    parts = fsops.split_sequence(str(out / "datos.bin.001"))
    assert len(parts) == 3
    joined = tmp_path / "unido.bin"
    assert fsops.join_files(parts, str(joined)).ok
    assert joined.read_bytes() == data
    assert src.read_bytes() == data


def test_split_exact_multiple_has_no_empty_part(tmp_path):
    src = tmp_path / "a.bin"
    src.write_bytes(b"x" * 2000)
    fsops.split_file(str(src), str(tmp_path), 1000)
    assert (tmp_path / "a.bin.002").stat().st_size == 1000
    assert not (tmp_path / "a.bin.003").exists()


def test_split_empty_file(tmp_path):
    src = tmp_path / "vacio.bin"
    src.write_bytes(b"")
    result = fsops.split_file(str(src), str(tmp_path), 1000)
    assert result.ok and (tmp_path / "vacio.bin.001").stat().st_size == 0


def test_split_refuses_to_overwrite_and_cleans_up(tmp_path):
    src = tmp_path / "a.bin"
    src.write_bytes(b"x" * 3000)
    write(tmp_path / "a.bin.002", "importante")
    result = fsops.split_file(str(src), str(tmp_path), 1000)
    assert result.errors
    assert read(tmp_path / "a.bin.002") == "importante"
    assert not (tmp_path / "a.bin.001").exists()


def test_split_cancel_removes_parts(tmp_path):
    src = tmp_path / "a.bin"
    src.write_bytes(b"x" * 3000)
    sink = ProgressSink()
    sink._on_progress = lambda done, total, name: sink.cancel()
    result = fsops.split_file(str(src), str(tmp_path), 1000, sink)
    assert result.cancelled and sorted(os.listdir(tmp_path)) == ["a.bin"]


def test_join_never_overwrites(tmp_path):
    write(tmp_path / "a.001", "1")
    write(tmp_path / "ya", "existe")
    assert fsops.join_files([str(tmp_path / "a.001")], str(tmp_path / "ya")).errors
    assert read(tmp_path / "ya") == "existe"


def test_split_sequence_rejects_other_names(tmp_path):
    write(tmp_path / "a.txt", "x")
    assert fsops.split_sequence(str(tmp_path / "a.txt")) == []


def test_friendly_dates():
    moment = datetime(2026, 3, 4, 5, 6)
    stamp = moment.timestamp()
    assert listing.format_time(stamp, "off", date(2026, 3, 4)) == "2026-03-04 05:06"
    assert listing.format_time(stamp, "today", date(2026, 3, 4)) == "Hoy 05:06"
    assert listing.format_time(stamp, "today", date(2026, 3, 5)) == "2026-03-04 05:06"
    assert listing.format_time(stamp, "normal", date(2026, 3, 5)) == "Ayer 05:06"
    assert listing.format_time(stamp, "normal", date(2026, 3, 6)) == "2026-03-04 05:06"


def test_date_codes():
    moment = datetime(2026, 3, 4, 5, 6, 7)
    assert commands.expand_date_codes("{date|yyyy-MM-dd} {time|HH-mm-ss}", moment) == "2026-03-04 05-06-07"
    assert commands.expand_date_codes("sin codigos", moment) == "sin codigos"


# ---- archivos JSON de favoritos y configuración ----

def test_favorites_roundtrip(tmp_path):
    path = tmp_path / "f.json"
    favorites = [{"label": "Ñandú", "path": "/datos/fotos"}, {"label": "Otra", "path": "C:\\x y"}]
    settings_io.export_favorites(str(path), favorites)
    assert '"format": "directory-commander-tux-tux-favorites"' in path.read_text(encoding="utf-8")
    assert "Ñandú" in path.read_text(encoding="utf-8")
    assert settings_io.read_favorites(str(path)) == (favorites, 0)


def test_favorites_accept_plain_list_and_discard_invalid(tmp_path):
    path = tmp_path / "f.json"
    path.write_text('[{"path": "/a"}, {"label": "x"}, "texto", {"path": ""}, {"path": 5}, {"label": "B", "path": "/b"}]',
                    encoding="utf-8")
    found, skipped = settings_io.read_favorites(str(path))
    assert found == [{"label": "/a", "path": "/a"}, {"label": "B", "path": "/b"}] and skipped == 4


@pytest.mark.parametrize("content", ["no es json", "{}", "5", '{"format": "otra-cosa", "favorites": []}', '{"favorites": 3}'])
def test_favorites_reject_other_files(tmp_path, content):
    path = tmp_path / "f.json"
    path.write_text(content, encoding="utf-8")
    with pytest.raises(ValueError):
        settings_io.read_favorites(str(path))


def test_settings_export_leaves_out_what_belongs_to_this_computer(tmp_path):
    path = tmp_path / "c.json"
    settings_io.export_settings(str(path), {"theme": "light", "favorites": [], "session": {"tabs": [["/privado"]]},
                                            "recent": [{"path": "/privado"}], "ffmpeg_dir": "/opt/ffmpeg", "otra": 1})
    text = path.read_text(encoding="utf-8")
    assert "theme" in text and "privado" not in text and "ffmpeg" not in text and "otra" not in text


def test_settings_roundtrip(tmp_path):
    path = tmp_path / "c.json"
    data = {"theme": "light", "confirm_delete": False, "rename_presets": {"p": {"mode": "regex"}},
            "styles": [{"name": "Mío"}], "toolbar": [{"separator": True}], "favorites": [{"label": "a", "path": "/a"}]}
    settings_io.export_settings(str(path), data)
    assert settings_io.read_settings(str(path)) == (data, [])


def test_settings_discard_invalid_values(tmp_path):
    path = tmp_path / "c.json"
    path.write_text(json.dumps({"format": settings_io.SETTINGS_FORMAT, "version": 1, "settings": {
        "theme": "rosa", "confirm_delete": "no", "toolbar": ["botón roto"], "styles": [{"sin": "nombre"}], "session": {},
        "ffmpeg_dir": "/malicioso", "show_hidden": True, "desconocida": 1}}), encoding="utf-8")
    settings, ignored = settings_io.read_settings(str(path))
    assert settings == {"show_hidden": True}
    assert set(ignored) == {"theme", "confirm_delete", "toolbar", "styles", "session", "ffmpeg_dir", "desconocida"}


def test_settings_accept_copy_of_config_file_without_session(tmp_path):
    path = tmp_path / "config.json"
    path.write_text('{"config_version": 2, "theme": "light", "session": {"tabs": []}, "ffmpeg_dir": "/x"}', encoding="utf-8")
    assert settings_io.read_settings(str(path)) == ({"theme": "light"}, [])


@pytest.mark.parametrize("content", ["no es json", "[1, 2]", '{"theme": "light"}', '{"format": "otra"}',
                                     '{"format": "directory-commander-tux-tux-settings", "settings": 3}'])
def test_settings_reject_other_files(tmp_path, content):
    path = tmp_path / "c.json"
    path.write_text(content, encoding="utf-8")
    with pytest.raises(ValueError):
        settings_io.read_settings(str(path))


def test_oversized_file_is_refused(tmp_path, monkeypatch):
    path = tmp_path / "c.json"
    path.write_text("[]", encoding="utf-8")
    monkeypatch.setattr(settings_io, "MAX_FILE_SIZE", 1)
    with pytest.raises(ValueError):
        settings_io.read_favorites(str(path))


def test_failed_export_keeps_previous_file(tmp_path, monkeypatch):
    path = tmp_path / "f.json"
    path.write_text("anterior", encoding="utf-8")
    monkeypatch.setattr(settings_io.json, "dump", lambda *a, **k: (_ for _ in ()).throw(OSError("disco lleno")))
    with pytest.raises(OSError):
        settings_io.export_favorites(str(path), [])
    assert path.read_text(encoding="utf-8") == "anterior"
    assert os.listdir(tmp_path) == ["f.json"]


def write_desktop(folder, name, **fields):
    folder.mkdir(exist_ok=True)
    lines = ["[Desktop Entry]", "Type=Application"] + [f"{key.replace('_es', '[es]')}={value}" for key, value in fields.items()]
    (folder / name).write_text("\n".join(lines), encoding="utf-8")


def test_desktop_apps_for_a_file_type(tmp_path):
    folder = tmp_path / "apps"
    write_desktop(folder, "editor.desktop", Name="Zed", Exec="zed %F", MimeType="text/plain;")
    write_desktop(folder, "visor.desktop", Name="Abc", Exec="abc %u", MimeType="text/plain;image/png;", Icon="abc")
    write_desktop(folder, "imagenes.desktop", Name="Fotos", Exec="fotos %f", MimeType="image/png;")
    write_desktop(folder, "oculto.desktop", Name="Oculto", Exec="o %f", MimeType="text/plain;", NoDisplay="true")
    write_desktop(folder, "sin-orden.desktop", Name="Roto", MimeType="text/plain;")
    (folder / "basura.desktop").write_text("no es un desktop = [", encoding="utf-8")
    found = desktop_apps.apps_for(["text/plain"], [str(folder)])
    assert [app.name for app in found] == ["Abc", "Zed"] and found[0].icon == "abc"


def test_desktop_file_of_the_user_replaces_the_system_one(tmp_path):
    user, system = tmp_path / "usuario", tmp_path / "sistema"
    write_desktop(user, "editor.desktop", Name="Mío", Exec="mio %f", MimeType="text/plain;")
    write_desktop(system, "editor.desktop", Name="Sistema", Exec="sis %f", MimeType="text/plain;")
    assert [app.name for app in desktop_apps.apps_for(["text/plain"], [str(user), str(system)])] == ["Mío"]


def test_desktop_app_name_in_the_language_of_the_system(tmp_path, monkeypatch):
    folder = tmp_path / "apps"
    write_desktop(folder, "a.desktop", Name="Files", Name_es="Archivos", Exec="a %f", MimeType="text/plain;")
    for variable in ("LANGUAGE", "LC_ALL"):
        monkeypatch.delenv(variable, raising=False)
    monkeypatch.setenv("LANG", "es_MX.UTF-8")
    assert desktop_apps.apps_for(["text/plain"], [str(folder)])[0].name == "Archivos"


def test_desktop_app_command():
    command = desktop_apps.command_for
    App = desktop_apps.DesktopApp
    assert command(App("a", 'prog --abrir "%f" %i %c'), "/x y/z.txt") == ["prog", "--abrir", "/x y/z.txt"]
    assert command(App("a", "prog %U"), "/x") == ["prog", "/x"]
    assert command(App("a", "prog --sin-codigo"), "/x") == ["prog", "--sin-codigo", "/x"]
    assert command(App("a", "prog 100%%"), "/x") == ["prog", "100%", "/x"]


def big_file(path, megabytes=4):
    path.write_bytes(b"x" * (megabytes * 1024 * 1024))
    return str(path)


def test_transfer_state_at_the_end(tmp_path):
    src = tmp_path / "a"
    src.mkdir()
    (src / "sub").mkdir()
    (src / "uno.bin").write_bytes(b"1" * 3000)
    (src / "sub" / "dos.bin").write_bytes(b"2" * 1000)
    dest = tmp_path / "b"
    dest.mkdir()
    sink = ProgressSink()
    assert sink.transfer is None
    fsops.transfer([str(src)], str(dest), sink=sink)
    state = sink.transfer
    assert state.ready and state.total_bytes == 4000 and state.bytes_done == 4000
    assert state.total_items == 2 and state.items_done == 2


def test_state_counts_a_fast_move(tmp_path):
    src = tmp_path / "a"
    src.mkdir()
    (src / "uno.bin").write_bytes(b"1" * 3000)
    dest = tmp_path / "b"
    dest.mkdir()
    sink = ProgressSink()
    fsops.transfer([str(src)], str(dest), move=True, sink=sink)
    assert sink.transfer.bytes_done == 3000 and sink.transfer.items_done == 1 and (dest / "a" / "uno.bin").exists()


def test_state_counts_what_collisions_skip(tmp_path):
    src = tmp_path / "a.bin"
    src.write_bytes(b"1" * 3000)
    dest = tmp_path / "b"
    dest.mkdir()
    (dest / "a.bin").write_bytes(b"viejo")
    sink = ProgressSink()
    result = fsops.transfer([str(src)], str(dest), policy=Collision.SKIP, sink=sink)
    assert result.skipped == 1 and sink.transfer.bytes_done == 3000 and sink.transfer.items_done == 1


def test_state_shows_the_file_being_copied(tmp_path):
    src = big_file(tmp_path / "grande.bin")
    dest = tmp_path / "b"
    dest.mkdir()
    seen = []

    def watch(done, total, name):
        state = sink.transfer
        seen.append((state.current_name, state.current_size, state.current_done))

    sink = ProgressSink(watch)
    fsops.transfer([src], str(dest), sink=sink)
    assert seen and all(name == "grande.bin" and size == 4 * 1024 * 1024 for name, size, _done in seen)


def test_progress_is_reported_at_the_end_without_one_per_chunk(tmp_path):
    src = big_file(tmp_path / "grande.bin", 24)
    dest = tmp_path / "b"
    dest.mkdir()
    reports = []
    fsops.transfer([src], str(dest), sink=ProgressSink(lambda done, total, _name: reports.append((done, total))))
    assert reports[-1] == (24 * 1024 * 1024, 24 * 1024 * 1024)
    assert len(reports) < 24


def run_in_thread(function):
    outcome = {}
    thread = threading.Thread(target=lambda: outcome.update(result=function()))
    thread.start()
    return thread, outcome


def slow_writes(monkeypatch, delay=0.05):
    """Un fragmento cada `delay`: que dé tiempo a pulsar los botones."""
    real_add = fsops._Transfer.add

    def add(self, nbytes):
        time.sleep(delay)
        real_add(self, nbytes)

    monkeypatch.setattr(fsops._Transfer, "add", add)


def wait_for(predicate, seconds=5.0):
    end = time.monotonic() + seconds
    while time.monotonic() < end:
        if predicate():
            return True
        time.sleep(0.005)
    return False


def test_pause_stops_the_copy_until_resumed(tmp_path, monkeypatch):
    slow_writes(monkeypatch)
    src = big_file(tmp_path / "grande.bin", 8)
    dest = tmp_path / "b"
    dest.mkdir()
    sink = ProgressSink()
    thread, outcome = run_in_thread(lambda: fsops.transfer([src], str(dest), sink=sink))
    assert wait_for(lambda: sink.transfer and sink.transfer.bytes_done > 0)
    sink.pause()
    time.sleep(0.1)
    stopped_at = sink.transfer.bytes_done
    time.sleep(0.2)
    assert sink.transfer.bytes_done == stopped_at and thread.is_alive()
    sink.resume()
    thread.join(10)
    assert not thread.is_alive() and outcome["result"].ok and (dest / "grande.bin").stat().st_size == 8 * 1024 * 1024


def test_cancel_works_while_paused(tmp_path, monkeypatch):
    slow_writes(monkeypatch)
    src = big_file(tmp_path / "grande.bin", 8)
    dest = tmp_path / "b"
    dest.mkdir()
    sink = ProgressSink()
    thread, outcome = run_in_thread(lambda: fsops.transfer([src], str(dest), sink=sink))
    assert wait_for(lambda: sink.transfer and sink.transfer.bytes_done > 0)
    sink.pause()
    sink.cancel()
    thread.join(5)
    assert not thread.is_alive() and outcome["result"].cancelled and os.listdir(dest) == []


@pytest.mark.parametrize("paused", [False, True])
def test_skip_leaves_this_file_and_copies_the_next(tmp_path, monkeypatch, paused):
    slow_writes(monkeypatch)
    folder = tmp_path / "a"
    folder.mkdir()
    big_file(folder / "1_grande.bin", 8)
    (folder / "2_otro.bin").write_bytes(b"2" * 10)
    dest = tmp_path / "b"
    dest.mkdir()
    sink = ProgressSink()
    sources = [str(folder / "1_grande.bin"), str(folder / "2_otro.bin")]
    thread, outcome = run_in_thread(lambda: fsops.transfer(sources, str(dest), sink=sink))
    assert wait_for(lambda: sink.transfer and sink.transfer.bytes_done > 0)
    if paused:
        sink.pause()
        time.sleep(0.05)
    sink.skip_current()
    if paused:
        assert wait_for(lambda: sink.transfer.current_name == "2_otro.bin")
        sink.resume()
    thread.join(10)
    result = outcome["result"]
    assert result.skipped == 1 and not result.errors and sorted(os.listdir(dest)) == ["2_otro.bin"]
    state = sink.transfer
    assert state.bytes_done == state.total_bytes and state.items_done == 2


def test_skipping_a_file_keeps_it_when_moving_a_folder(tmp_path, monkeypatch):
    slow_writes(monkeypatch)
    folder = tmp_path / "a"
    folder.mkdir()
    big_file(folder / "grande.bin", 8)
    dest = tmp_path / "b"
    dest.mkdir()
    monkeypatch.setattr(os, "rename", lambda *_a: (_ for _ in ()).throw(OSError("otro volumen")))
    sink = ProgressSink()
    thread, _outcome = run_in_thread(lambda: fsops.transfer([str(folder)], str(dest), move=True, sink=sink))
    assert wait_for(lambda: sink.transfer and sink.transfer.bytes_done > 0)
    sink.skip_current()
    thread.join(10)
    assert (folder / "grande.bin").stat().st_size == 8 * 1024 * 1024  # el origen no se borra


def test_drop_operation_moves_within_a_volume_and_copies_to_another(tmp_path, monkeypatch):
    a, b = tmp_path / "a", tmp_path / "b"
    a.mkdir()
    b.mkdir()
    file = a / "f.txt"
    file.write_text("x", encoding="utf-8")
    assert fsops.drop_operation([str(file)], str(b)) == "move"
    assert fsops.drop_operation([str(file)], str(b), force_copy=True) == "copy"
    monkeypatch.setattr(fsops, "same_volume", lambda *_a: False)
    assert fsops.drop_operation([str(file)], str(b)) == "copy"
    assert fsops.drop_operation([str(file)], str(b), force_move=True) == "move"


def test_drop_operation_refuses_what_makes_no_sense(tmp_path):
    a = tmp_path / "a"
    (a / "dentro").mkdir(parents=True)
    file = a / "f.txt"
    file.write_text("x", encoding="utf-8")
    assert fsops.drop_operation([], str(a)) is None
    assert fsops.drop_operation([str(file)], str(a)) is None  # ya está ahí
    assert fsops.drop_operation([str(file)], str(a), force_copy=True) is None
    assert fsops.drop_operation([str(a)], str(a)) is None  # en sí misma
    assert fsops.drop_operation([str(a)], str(a / "dentro")) is None  # dentro de sí misma
    assert fsops.drop_operation([str(file)], str(tmp_path / "no-existe")) is None


def test_drop_operation_with_compressed_files(zip_file, tmp_path):
    inside = os.path.join(zip_file, "carpeta", "a.txt")
    assert fsops.drop_operation([inside], str(tmp_path), force_move=True) == "copy"  # de dentro de un comprimido no se puede mover
    outside = tmp_path / "f.txt"
    outside.write_text("x", encoding="utf-8")
    assert fsops.drop_operation([str(outside)], zip_file) is None
    assert fsops.drop_operation([zip_file], str(tmp_path / "otra") if (tmp_path / "otra").mkdir() is None else "") == "move"


def test_is_inside_archive(zip_file):
    assert not archive.is_inside_archive(zip_file)
    assert archive.is_inside_archive(os.path.join(zip_file, "carpeta", "a.txt"))
    assert not archive.is_inside_archive(os.path.dirname(zip_file))


def test_open_with_and_browse_are_in_the_file_menu_of_an_older_configuration():
    layout = context_menu.normalize_layout({"order": ["Abrir", "Copiar"], "hidden": []}, context_menu.FILE)
    assert layout["order"].index(context_menu.OPEN_WITH) == 1 and layout["order"].index(context_menu.BROWSE_ARCHIVE) == 4  # detrás de «Editar Markdown» y «Ver JSON»


def test_verified_copy_keeps_good_copies_and_drops_bad_ones(tmp_path, monkeypatch):
    from directory_commander_tux_tux.core import fsops as module

    source, target = tmp_path / "origen", tmp_path / "destino"
    source.mkdir()
    target.mkdir()
    (source / "bueno.bin").write_bytes(os.urandom(3 * 1024 * 1024 + 7))
    result = module.transfer([str(source / "bueno.bin")], str(target), verify=True)
    assert result.ok and (target / "bueno.bin").read_bytes() == (source / "bueno.bin").read_bytes()
    monkeypatch.setattr(module, "_written_hash", lambda path, sink: b"otro")  # el disco devolvió algo distinto de lo escrito
    (source / "malo.bin").write_bytes(b"datos")

    def other_disk(*_args):
        raise OSError("otro disco")  # mover a otro disco es copiar y después borrar el original

    monkeypatch.setattr(module.os, "rename", other_disk)
    result = module.transfer([str(source / "malo.bin")], str(target), move=True, verify=True)
    monkeypatch.undo()
    assert result.errors and "no es igual" in result.errors[0][1]
    assert not (target / "malo.bin").exists() and not list(target.glob("*" + module.PART_SUFFIX))
    assert (source / "malo.bin").exists()  # al mover, el original se queda
    result = module.transfer([str(source / "malo.bin")], str(target))  # sin comprobar, ni se mira
    assert result.ok and (target / "malo.bin").exists()
