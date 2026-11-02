"""Lectura de metadatos con archivos mínimos construidos a mano, sin depender de FFmpeg."""
import os
import struct
import subprocess
import wave

import pytest

from directory_commander_tux_tux.core import media_info
from directory_commander_tux_tux.core.media_info import format_duration, read_media_info


def box(name: bytes, payload: bytes = b"") -> bytes:
    return struct.pack(">I4s", len(payload) + 8, name) + payload


def mp4_bytes(tags: bytes = b"", moov_first: bool = False) -> bytes:
    mvhd = box(b"mvhd", bytes(12) + struct.pack(">II", 1000, 90500) + bytes(80))
    tkhd = box(b"tkhd", bytes(76) + struct.pack(">II", 1920 << 16, 1080 << 16))
    video_stsd = box(b"stsd", bytes(8) + box(b"avc1", bytes(78)))
    video = box(b"trak", tkhd + box(b"mdia", box(b"hdlr", bytes(8) + b"vide" + bytes(12))
                                    + box(b"minf", box(b"stbl", video_stsd))))
    audio_entry = bytes(16) + struct.pack(">HH", 2, 16) + bytes(4) + struct.pack(">I", 48000 << 16)
    audio_stsd = box(b"stsd", bytes(8) + box(b"mp4a", audio_entry))
    audio = box(b"trak", box(b"mdia", box(b"hdlr", bytes(8) + b"soun" + bytes(12))
                             + box(b"minf", box(b"stbl", audio_stsd))))
    moov = box(b"moov", mvhd + video + audio + tags)
    ftyp = box(b"ftyp", b"isom" + bytes(4))
    mdat = box(b"mdat", bytes(1000))
    return ftyp + moov + mdat if moov_first else ftyp + mdat + moov


def ilst(**fields: bytes) -> bytes:
    names = {"title": b"\xa9nam", "artist": b"\xa9ART", "album": b"\xa9alb", "cover": b"covr"}
    items = b"".join(box(names[k], box(b"data", bytes(8) + v)) for k, v in fields.items())
    return box(b"udta", box(b"meta", bytes(4) + box(b"ilst", items)))


@pytest.mark.parametrize("moov_first", [True, False])
def test_mp4_video(tmp_path, moov_first):
    path = tmp_path / "v.mp4"
    path.write_bytes(mp4_bytes(moov_first=moov_first))
    info = read_media_info(str(path))
    assert info.kind == "video" and (info.width, info.height) == (1920, 1080)
    assert info.duration == pytest.approx(90.5)
    assert info.codec == "avc1" and info.audio_codec == "mp4a"
    assert info.sample_rate == 48000 and info.channels == 2


def test_m4a_tags_and_cover(tmp_path):
    path = tmp_path / "a.mp4"
    path.write_bytes(mp4_bytes(ilst(title="Canción".encode(), artist=b"Artista", album=b"Disco", cover=b"\x89PNGdatos")))
    info = read_media_info(str(path))
    assert (info.title, info.artist, info.album) == ("Canción", "Artista", "Disco")
    assert info.cover == b"\x89PNGdatos"


def test_wav_real(tmp_path):
    path = tmp_path / "s.wav"
    with wave.open(str(path), "wb") as out:
        out.setnchannels(2)
        out.setsampwidth(2)
        out.setframerate(44100)
        out.writeframes(bytes(44100 * 2 * 2 * 3))
    info = read_media_info(str(path))
    assert info.kind == "audio" and info.audio_codec == "PCM"
    assert info.duration == pytest.approx(3.0)
    assert info.sample_rate == 44100 and info.channels == 2


def flac_bytes(comments=(), picture: bytes | None = None) -> bytes:
    sample_rate, channels, bits, samples = 44100, 2, 16, 44100 * 5
    packed = (sample_rate << 44) | ((channels - 1) << 41) | ((bits - 1) << 36) | samples
    info = bytes(10) + packed.to_bytes(8, "big") + bytes(16)
    blocks = [(0, info)]
    if comments:
        vendor = b"prueba"
        body = struct.pack("<I", len(vendor)) + vendor + struct.pack("<I", len(comments))
        for comment in comments:
            raw = comment.encode()
            body += struct.pack("<I", len(raw)) + raw
        blocks.append((4, body))
    if picture:
        mime = b"image/png"
        body = struct.pack(">I", 3) + struct.pack(">I", len(mime)) + mime + struct.pack(">I", 0) + bytes(16)
        body += struct.pack(">I", len(picture)) + picture
        blocks.append((6, body))
    data = b"fLaC"
    for index, (kind, body) in enumerate(blocks):
        flag = 0x80 if index == len(blocks) - 1 else 0
        data += bytes([flag | kind]) + len(body).to_bytes(3, "big") + body
    return data + bytes(100)


def test_flac(tmp_path):
    path = tmp_path / "m.flac"
    path.write_bytes(flac_bytes(["TITLE=Tema", "artist=Banda", "ALBUM=Disco"], b"PORTADA"))
    info = read_media_info(str(path))
    assert info.duration == pytest.approx(5.0) and info.sample_rate == 44100 and info.channels == 2
    assert (info.title, info.artist, info.album) == ("Tema", "Banda", "Disco")
    assert info.cover == b"PORTADA"


def synchsafe(value: int) -> bytes:
    return bytes((value >> shift) & 0x7F for shift in (21, 14, 7, 0))


def id3_frame(name: bytes, body: bytes) -> bytes:
    return name + synchsafe(len(body)) + bytes(2) + body


def mp3_bytes(frames: int = 100, tags: bytes = b"") -> bytes:
    header = b"\xff\xfb\x90\x64"  # MPEG1 capa III, 128 kb/s, 44.1 kHz, estéreo
    frame_size = 417
    xing = header + bytes(32) + b"Xing" + struct.pack(">II", 1, frames) + bytes(frame_size - 48)
    audio = xing + (header + bytes(frame_size - 4)) * 3
    if tags:
        return b"ID3\x04\0\0" + synchsafe(len(tags)) + tags + audio
    return audio


def test_mp3_with_tags_and_cover(tmp_path):
    cover = b"\0image/png\0\x03\0IMAGEN"
    tags = (id3_frame(b"TIT2", b"\x03T\xc3\xadtulo") + id3_frame(b"TPE1", b"\x00Autor")
            + id3_frame(b"TALB", b"\x01\xff\xfeD\0i\0s\0c\0o\0") + id3_frame(b"APIC", cover))
    path = tmp_path / "c.mp3"
    path.write_bytes(mp3_bytes(1000, tags))
    info = read_media_info(str(path))
    assert info.audio_codec == "MP3" and info.sample_rate == 44100 and info.channels == 2
    assert info.duration == pytest.approx(1000 * 1152 / 44100)
    assert (info.title, info.artist, info.album) == ("Título", "Autor", "Disco")
    assert info.cover == b"IMAGEN"


def test_mp3_without_xing_estimates_from_bitrate(tmp_path):
    path = tmp_path / "c.mp3"
    path.write_bytes(b"\xff\xfb\x90\x64" + bytes(16000 * 10 - 4))
    info = read_media_info(str(path))
    assert info.bitrate == 128000 and info.duration == pytest.approx(10.0)


def avi_bytes() -> bytes:
    avih = struct.pack("<5I", 40000, 0, 0, 0, 250) + bytes(12) + struct.pack("<2I", 640, 480) + bytes(16)
    strh = b"vids" + b"H264" + bytes(48)
    strl = b"LIST" + struct.pack("<I", 4 + 8 + len(strh)) + b"strl" + b"strh" + struct.pack("<I", len(strh)) + strh
    hdrl_body = b"hdrl" + b"avih" + struct.pack("<I", len(avih)) + avih + strl
    body = b"AVI " + b"LIST" + struct.pack("<I", len(hdrl_body)) + hdrl_body
    return b"RIFF" + struct.pack("<I", len(body)) + body


def test_avi(tmp_path):
    path = tmp_path / "v.avi"
    path.write_bytes(avi_bytes())
    info = read_media_info(str(path))
    assert (info.width, info.height) == (640, 480) and info.duration == pytest.approx(10.0)
    assert info.codec == "H264" and info.frame_rate == pytest.approx(25.0)


def ebml(element: int, payload: bytes) -> bytes:
    raw_id = element.to_bytes((element.bit_length() + 7) // 8, "big")
    return raw_id + (len(payload) | (1 << 56)).to_bytes(8, "big") + payload


def mkv_bytes(unknown_cluster: bool = False) -> bytes:
    info = ebml(0x2AD7B1, (1000000).to_bytes(3, "big")) + ebml(0x4489, struct.pack(">f", 12500.0))
    video = ebml(0x83, b"\x01") + ebml(0x86, b"V_VP9") + ebml(0xE0, ebml(0xB0, (1280).to_bytes(2, "big")) + ebml(0xBA, (720).to_bytes(2, "big")))
    audio = ebml(0x83, b"\x02") + ebml(0x86, b"A_OPUS") + ebml(0xE1, ebml(0xB5, struct.pack(">f", 48000.0)) + ebml(0x9F, b"\x02"))
    tracks = ebml(0xAE, video) + ebml(0xAE, audio)
    segment = ebml(0x1549A966, info) + ebml(0x1654AE6B, tracks) + ebml(0x1F43B675, bytes(50))
    return ebml(0x1A45DFA3, b"") + ebml(0x18538067, segment)


def test_mkv_frame_rate(tmp_path):
    video = ebml(0x83, b"\x01") + ebml(0x86, b"V_VP9") + ebml(0x23E383, (41708333).to_bytes(4, "big")) + ebml(0xE0, ebml(0xB0, b"\x10"))
    data = ebml(0x1A45DFA3, b"") + ebml(0x18538067, ebml(0x1654AE6B, ebml(0xAE, video)))
    path = tmp_path / "v.mkv"
    path.write_bytes(data)
    assert read_media_info(str(path)).frame_rate == pytest.approx(23.976, abs=0.001)


def test_mkv(tmp_path):
    path = tmp_path / "v.mkv"
    path.write_bytes(mkv_bytes())
    info = read_media_info(str(path))
    assert info.kind == "video" and (info.width, info.height) == (1280, 720)
    assert info.duration == pytest.approx(12.5) and info.codec == "VP9" and info.audio_codec == "OPUS"
    assert info.sample_rate == 48000 and info.channels == 2


@pytest.mark.parametrize("name", ["a.mp4", "a.mp3", "a.flac", "a.wav", "a.avi", "a.mkv", "a.webm", "a.mov", "a.ogg"])
@pytest.mark.parametrize("content", [b"", b"basura", b"\xff" * 4096, b"\0" * 4096, bytes(range(256)) * 16])
def test_garbage_never_raises(tmp_path, name, content, monkeypatch):
    monkeypatch.setattr(media_info, "find_tool", lambda *a: None)
    path = tmp_path / name
    path.write_bytes(content)
    read_media_info(str(path))  # el resultado da igual: lo que importa es que no lance nada


@pytest.mark.parametrize("data,name", [(mp4_bytes(ilst(title=b"x")), "a.mp4"), (mkv_bytes(), "a.mkv"), (avi_bytes(), "a.avi"),
                                       (flac_bytes(["TITLE=a"], b"p"), "a.flac"), (mp3_bytes(), "a.mp3")])
def test_truncated_and_bit_flipped_files_never_raise(tmp_path, data, name, monkeypatch):
    monkeypatch.setattr(media_info, "find_tool", lambda *a: None)
    path = tmp_path / name
    for cut in range(0, len(data), max(1, len(data) // 60)):
        path.write_bytes(data[:cut])
        read_media_info(str(path))
    for position in range(0, len(data), max(1, len(data) // 60)):
        damaged = bytearray(data)
        damaged[position] ^= 0xFF
        path.write_bytes(damaged)
        read_media_info(str(path))


def test_huge_declared_box_is_not_loaded(tmp_path):
    path = tmp_path / "v.mp4"
    path.write_bytes(struct.pack(">I4s", 0xFFFFFFF0, b"moov") + bytes(100))
    assert read_media_info(str(path)) is None


def test_not_media(tmp_path):
    path = tmp_path / "a.txt"
    path.write_text("x", encoding="utf-8")
    assert read_media_info(str(path)) is None
    assert read_media_info(str(tmp_path / "no-existe.mp4")) is None


def test_format_duration():
    assert format_duration(5) == "0:05" and format_duration(65.4) == "1:05" and format_duration(3725) == "1:02:05"


def test_find_tool_never_uses_current_folder(tmp_path, monkeypatch):
    name = "ffmpeg.exe" if os.name == "nt" else "ffmpeg"
    (tmp_path / name).write_bytes(b"planted")
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("PATH", os.pathsep.join([".", ""]))
    assert media_info.find_tool("ffmpeg") is None
    assert media_info.find_tool("ffmpeg", str(tmp_path)) == str(tmp_path / name)


def test_ffprobe_output_is_parsed(tmp_path, monkeypatch):
    path = tmp_path / "a.ogg"
    path.write_bytes(b"OggS")
    monkeypatch.setattr(media_info, "find_tool", lambda *a: "ffprobe")
    monkeypatch.setattr(media_info, "_run_tool", lambda args: b"""{
        "format": {"duration": "61.5", "bit_rate": "128000", "tags": {"TITLE": "Tema", "artist": "Banda"}},
        "streams": [{"codec_type": "audio", "codec_name": "vorbis", "sample_rate": "44100", "channels": 2}]}""")
    info = read_media_info(str(path))
    assert info.kind == "audio" and info.duration == 61.5 and info.audio_codec == "vorbis"
    assert (info.title, info.artist) == ("Tema", "Banda")


def test_a_file_without_tracks_is_not_media_for_ffprobe(tmp_path, monkeypatch):
    path = tmp_path / "a.ogg"
    path.write_bytes(b"OggS")
    monkeypatch.setattr(media_info, "find_tool", lambda *a: "ffprobe")
    monkeypatch.setattr(media_info, "_run_tool", lambda args: b'''{"streams": [], "format": {"nb_streams": 0}}''')
    assert read_media_info(str(path)) is None


def test_tool_failure_is_quiet(monkeypatch):
    def boom(*a, **k):
        raise subprocess.TimeoutExpired("ffmpeg", 1)
    monkeypatch.setattr(subprocess, "run", boom)
    assert media_info._run_tool(["ffmpeg"]) is None
