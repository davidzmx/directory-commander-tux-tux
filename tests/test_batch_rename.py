"""Renombrado masivo: las reglas de sus dos pestañas, las secuencias y las variables."""
import datetime
import os
import struct

import pytest

from directory_commander_tux_tux.core import batch_rename
from directory_commander_tux_tux.core.batch_rename import BatchRule, FileDetails, new_names, next_in_sequence

NAMES = ["Qwen-Image2_1_00156_.png", "Qwen-Image2_1_00153_.png", "foto.JPG", ".gitignore", "sin extension"]


@pytest.mark.parametrize("value, following", [("001", "002"), ("009", "010"), ("099", "100"), ("9", "10"), ("0A", "0B"), ("AZ", "BA"),
                                              ("Z", "AA"), ("z", "aa"), ("a9", "b0"), ("01-9", "02-0"), ("", "1")])
def test_each_sequence_counts_like_an_odometer(value, following):
    assert next_in_sequence(value) == following


def test_the_name_is_replaced_with_the_text_and_its_sequence():
    rule = BatchRule(name_on=True, name_text="foto_", name_sequence_on=True, name_sequence="001")
    assert new_names(NAMES, rule) == ["foto_001.png", "foto_002.png", "foto_003.JPG", "foto_004", "foto_005"]


@pytest.mark.parametrize("place, expected", [("before", "x_Qwen-Image2_1_00156_.png"), ("after", "Qwen-Image2_1_00156_x_.png")])
def test_the_text_can_go_before_or_after_the_old_name(place, expected):
    assert new_names(NAMES[:1], BatchRule(name_on=True, name_text="x_" if place == "before" else "x_", name_place=place)) == [expected]


@pytest.mark.parametrize("case, expected", [("lower", "qwen-image2_1_00156_.png"), ("upper", "QWEN-IMAGE2_1_00156_.png"),
                                            ("sentence", "Qwen-image2_1_00156_.png"), ("title", "Qwen-Image2_1_00156_.png")])
def test_the_name_can_change_case_without_touching_the_extension(case, expected):
    rule = BatchRule(name_on=True, name_text="", name_place="after", name_case_on=True, name_case=case)
    assert new_names(NAMES[:1], rule) == [expected]


def test_the_extension_has_its_own_rules():
    rule = BatchRule(extension_on=True, extension_text="", extension_place="after", extension_case_on=True, extension_case="lower")
    assert new_names(["foto.JPG", "sin extension"], rule) == ["foto.jpg", "sin extension"]
    rule = BatchRule(extension_on=True, extension_text="bak", extension_place="replace", extension_sequence_on=True, extension_sequence="1")
    assert new_names(["a.txt", "b.txt"], rule) == ["a.bak1", "b.bak2"]
    assert new_names(["a.txt"], BatchRule(extension_on=True, extension_text="old.", extension_place="before")) == ["a.old.txt"]


@pytest.mark.parametrize("place, expected", [("position", "QwenNUEVO-Image2_1_00156_.png"), ("before", "Qwen-Image2_1_NUEVO00156_.png"),
                                             ("after", "Qwen-Image2_1_00156NUEVO_.png")])
def test_insert_at_a_position_or_next_to_a_text(place, expected):
    rule = BatchRule(insert_on=True, insert_text="NUEVO", insert_place=place, insert_position=4, insert_before="00156", insert_after="00156")
    assert new_names(NAMES[:1], rule) == [expected]


@pytest.mark.parametrize("apply_to", ["name", "extension", "both"])
def test_insert_before_the_extension_goes_at_the_end_of_the_name(apply_to):
    rule = BatchRule(insert_on=True, insert_text="_v2", insert_place="before_extension", apply_to=apply_to)
    assert new_names(["foto.final.jpg", "sin extension", ".gitignore"], rule) == ["foto.final_v2.jpg", "sin extension_v2", ".gitignore_v2"]


def test_inserting_next_to_a_missing_text_leaves_the_name_and_a_far_position_goes_at_the_end():
    rule = BatchRule(insert_on=True, insert_text="X", insert_place="before", insert_before="no está")
    assert new_names(["a.txt"], rule) == ["a.txt"]
    assert new_names(["a.txt"], BatchRule(insert_on=True, insert_text="X", insert_position=50)) == ["aX.txt"]


def test_replace_a_range_by_position():
    rule = BatchRule(replace_on=True, replace_kind="range", replace_from=0, replace_to=5, replace_with="")
    assert new_names(NAMES[:1], rule) == ["Image2_1_00156_.png"]


def test_replace_a_text_with_or_without_case_and_with_regular_expressions():
    rule = BatchRule(replace_on=True, replace_kind="search", replace_search="qwen", replace_with="Flux")
    assert new_names(NAMES[:1], rule) == ["Flux-Image2_1_00156_.png"]
    rule.case_sensitive = True
    assert new_names(NAMES[:1], rule) == NAMES[:1]
    rule = BatchRule(replace_on=True, replace_kind="search", replace_search=r"_(\d+)_$", replace_regex=True, replace_with="-$1")
    assert new_names(NAMES[:2], rule) == ["Qwen-Image2_1-00156.png", "Qwen-Image2_1-00153.png"]
    with pytest.raises(ValueError):
        new_names(NAMES, BatchRule(replace_on=True, replace_kind="search", replace_search="(", replace_regex=True))


def test_advanced_options_can_work_on_the_extension_or_on_both():
    rule = BatchRule(replace_on=True, replace_kind="search", replace_search="jpg", replace_with="jpeg", apply_to="extension")
    assert new_names(["jpg.jpg"], rule) == ["jpg.jpeg"]
    rule.apply_to = "both"
    assert new_names(["jpg.jpg"], rule) == ["jpeg.jpeg"]


def test_basic_options_go_first_and_advanced_ones_work_on_their_result():
    rule = BatchRule(name_on=True, name_text="foto", name_sequence_on=True, name_sequence="1", insert_on=True, insert_text="_", insert_position=4)
    assert new_names(["a.png", "b.png"], rule) == ["foto_1.png", "foto_2.png"]


def test_variables_take_the_dates_of_each_file():
    moment = datetime.datetime(2011, 10, 8, 14, 5, 30)
    details = FileDetails(modified=moment.timestamp(), taken=datetime.datetime(2020, 1, 2, 3, 4, 5), width=640, height=480,
                          tags={"MP3ARTIST": "Artista"})
    rule = BatchRule(name_on=True, name_text="%Y-%m-%d %X %#Y %WIDTH%x%HEIGHT% %MP3ARTIST% 100%%")
    assert new_names(["a.jpg"], rule, lambda _index: details) == ["2011-10-08 14_05_30 2020 640x480 Artista 100%.jpg"]
    assert batch_rename.expand_variables("%D", details) == "sábado, 8 de octubre de 2011"
    assert batch_rename.expand_variables("%#Y", FileDetails(modified=moment.timestamp())) == "2011"  # sin fecha de captura, la del archivo


def _jpeg_with_capture_date(date: str) -> bytes:
    """Un JPEG con un EXIF mínimo: el bloque EXIF con la fecha de captura."""
    value = date.encode("ascii") + b"\0"
    exif_ifd = 8 + 2 + 12 + 4  # tras la cabecera y el IFD0 de una entrada
    data_offset = exif_ifd + 2 + 12 + 4
    tiff = (b"II*\0" + struct.pack("<I", 8) + struct.pack("<H", 1) + struct.pack("<HHII", 0x8769, 4, 1, exif_ifd) + struct.pack("<I", 0)
            + struct.pack("<H", 1) + struct.pack("<HHII", 0x9003, 2, len(value), data_offset) + struct.pack("<I", 0) + value)
    block = b"Exif\0\0" + tiff
    return b"\xff\xd8\xff\xe1" + struct.pack(">H", len(block) + 2) + block + b"\xff\xda\0\x02" + b"\0" * 10 + b"\xff\xd9"


def test_the_capture_date_of_a_photo_is_read_from_its_exif(tmp_path):
    photo = tmp_path / "foto.jpg"
    photo.write_bytes(_jpeg_with_capture_date("2019:07:04 18:30:00"))
    assert batch_rename.photo_taken(str(photo)) == datetime.datetime(2019, 7, 4, 18, 30)
    (tmp_path / "otra.jpg").write_bytes(b"\xff\xd8\xff\xd9")
    assert batch_rename.photo_taken(str(tmp_path / "otra.jpg")) is None
    rule = BatchRule(name_on=True, name_text="%#Y%#m%#d")
    details = batch_rename.read_details(str(photo), rule)
    assert new_names(["foto.jpg"], rule, lambda _index: details) == ["20190704.jpg"]


def test_details_are_only_read_when_the_rules_use_them(tmp_path, monkeypatch):
    path = tmp_path / "a.mp3"
    path.write_bytes(b"x")
    called = []
    monkeypatch.setattr(batch_rename, "photo_taken", lambda p: called.append("exif"))
    monkeypatch.setattr(batch_rename, "mp3_tags", lambda p: called.append("mp3") or {})
    batch_rename.read_details(str(path), BatchRule(name_on=True, name_text="%Y"))
    assert called == []
    batch_rename.read_details(str(path), BatchRule(name_on=True, name_text="%#Y %MP3TITLE%"))
    assert called == ["exif", "mp3"]


def test_rules_survive_a_round_trip_and_bad_values_are_ignored():
    rule = BatchRule(name_on=True, name_text="x", insert_position=3, replace_regex=True)
    assert BatchRule.from_dict(rule.to_dict()) == rule
    assert BatchRule.from_dict({"kind": "batch", "name_on": "sí", "insert_position": True, "name_text": 5}) == BatchRule()
    assert BatchRule.from_dict(None) == BatchRule()


def test_profiles_of_the_previous_version_are_translated():
    old = {"mode": "regex", "pattern": r"(\d+)", "replacement": r"n\1", "ignore_ext": True, "case": "upper"}
    assert new_names(["a12.txt"], BatchRule.from_dict(old)) == ["An12.txt"]  # las mayúsculas, ahora antes del reemplazo
    wildcard = {"mode": "wildcard", "pattern": "img*", "replacement": "foto*", "ignore_ext": True, "case": "none"}
    assert new_names(["img7.png"], BatchRule.from_dict(wildcard)) == ["foto7.png"]
    simple = {"mode": "simple", "pattern": "a", "replacement": "b", "ignore_ext": False, "case": "none"}
    assert new_names(["casa.a"], BatchRule.from_dict(simple)) == ["cbsb.b"]


def test_mp3_tags_include_year_track_genre_and_comment(tmp_path):
    def frame(name: bytes, text: bytes) -> bytes:
        body = b"\0" + text
        return name + struct.pack(">I", len(body)) + b"\0\0" + body

    comment = b"COMM" + struct.pack(">I", 9) + b"\0\0" + b"\0spa\0hola"
    tags = frame(b"TIT2", b"Cancion") + frame(b"TYER", b"1999") + frame(b"TRCK", b"7/12") + frame(b"TCON", b"(17)Rock") + comment
    header = b"ID3\x03\0\0" + bytes([(len(tags) >> 21) & 0x7F, (len(tags) >> 14) & 0x7F, (len(tags) >> 7) & 0x7F, len(tags) & 0x7F])
    mpeg = b"\xff\xfb\x90\x64" + b"\0" * 413
    (tmp_path / "a.mp3").write_bytes(header + tags + mpeg * 20)
    found = batch_rename.mp3_tags(str(tmp_path / "a.mp3"))
    assert (found["MP3TITLE"], found["MP3YEAR"], found["MP3TRACK"], found["MP3GENRE"], found["MP3COMMENT"]) == ("Cancion", "1999", "7/12", "Rock", "hola")
    assert found["MP3MODE"] and found["MP3BITRATE"] == "128"
    assert os.path.exists(tmp_path / "a.mp3")
