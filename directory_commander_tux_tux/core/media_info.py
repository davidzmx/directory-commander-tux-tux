"""Lee datos de archivos de audio y vídeo sin dependencias, solo mirando sus cabeceras.

Los archivos pueden estar dañados o ser hostiles: cada lector limita lo que lee y un formato
inesperado da `None` en lugar de una excepción. FFmpeg es opcional y solo se usa si está instalado.
"""
from __future__ import annotations

import json
import os
import struct
import subprocess
import sys
from dataclasses import dataclass

from .i18n import tr

VIDEO_EXTENSIONS = {"mp4", "m4v", "mov", "mkv", "webm", "avi", "wmv", "mpg", "mpeg", "flv", "ts", "m2ts", "3gp", "ogv"}
AUDIO_EXTENSIONS = {"mp3", "flac", "wav", "m4a", "aac", "ogg", "oga", "opus", "wma", "aiff", "aif"}
MAX_HEADER = 32 * 1024 * 1024     # lo máximo que se carga de una cabecera (`moov` en MP4, etc.)
MAX_COVER = 16 * 1024 * 1024
MP4_CONTAINERS = {b"moov", b"trak", b"mdia", b"minf", b"stbl", b"udta", b"ilst"}
FFMPEG_TIMEOUT = 6


@dataclass
class MediaInfo:
    kind: str = ""  # video | audio
    width: int = 0
    height: int = 0
    duration: float = 0.0
    codec: str = ""
    audio_codec: str = ""
    bitrate: int = 0  # bits por segundo
    sample_rate: int = 0
    channels: int = 0
    frame_rate: float = 0.0
    title: str = ""
    artist: str = ""
    album: str = ""
    cover: bytes | None = None


_CODEC_NAMES = {"avc1": "H.264", "avc3": "H.264", "h264": "H.264", "mpeg4/iso/avc": "H.264", "hev1": "H.265", "hvc1": "H.265",
                "hevc": "H.265", "mpegh/iso/hevc": "H.265", "av01": "AV1", "av1": "AV1", "vp09": "VP9", "vp9": "VP9",
                "vp8": "VP8", "mp4v": "MPEG-4", "fmp4": "MPEG-4", "xvid": "Xvid", "divx": "DivX", "mp4a": "AAC", "aac": "AAC",
                "opus": "Opus", "vorbis": "Vorbis", "mpeg/l3": "MP3", "mp3": "MP3", "flac": "FLAC", "ac3": "AC-3", "eac3": "E-AC-3"}


def codec_name(raw: str) -> str:
    """Nombre conocido de un códec, en lugar del identificador interno del contenedor."""
    return _CODEC_NAMES.get(raw.lower(), raw)


def is_media_extension(extension: str) -> bool:
    return extension in VIDEO_EXTENSIONS or extension in AUDIO_EXTENSIONS


def format_duration(seconds: float) -> str:
    total = int(round(seconds))
    hours, rest = divmod(total, 3600)
    minutes, secs = divmod(rest, 60)
    return f"{hours}:{minutes:02d}:{secs:02d}" if hours else f"{minutes}:{secs:02d}"


def read_media_info(path: str, ffmpeg_dir: str = "") -> MediaInfo | None:
    """Datos del archivo, o None si no es multimedia o no se puede entender."""
    extension = os.path.splitext(path)[1].lstrip(".").lower()
    reader = _READERS.get(extension)
    info = None
    if reader is not None:
        try:
            with open(path, "rb") as fin:
                info = reader(fin, os.path.getsize(path))
        except (OSError, ValueError, TypeError, struct.error, IndexError, OverflowError):
            info = None
    if info is None and is_media_extension(extension):
        info = _read_with_ffprobe(path, ffmpeg_dir)
    if info is not None:
        if not info.kind:
            info.kind = "video" if extension in VIDEO_EXTENSIONS else "audio"
        # Un archivo dañado puede declarar cualquier valor: lo absurdo se trata como desconocido.
        if not 0 <= info.duration < 10 ** 9:
            info.duration = 0.0
        if not (0 <= info.width < 10 ** 6 and 0 <= info.height < 10 ** 6):
            info.width = info.height = 0
    return info


# ---------------------------------------------------------------- MP4 / MOV / M4A
def _read_mp4(fin, size: int) -> MediaInfo | None:
    position = 0
    while position + 8 <= size:
        fin.seek(position)
        header = fin.read(16)
        box_size, box_type = struct.unpack(">I4s", header[:8])
        header_size = 8
        if box_size == 1:
            box_size = struct.unpack(">Q", header[8:16])[0]
            header_size = 16
        elif box_size == 0:
            box_size = size - position
        if box_size < header_size:
            return None
        if box_type == b"moov":
            if box_size - header_size > MAX_HEADER:
                return None
            fin.seek(position + header_size)
            return _parse_moov(fin.read(box_size - header_size), size)
        position += box_size
    return None


def _boxes(data: bytes, start: int = 0, end: int | None = None):
    end = len(data) if end is None else min(end, len(data))
    position = start
    while position + 8 <= end:
        box_size, box_type = struct.unpack(">I4s", data[position:position + 8])
        body = position + 8
        if box_size == 1:
            if position + 16 > end:
                return
            box_size = struct.unpack(">Q", data[position + 8:position + 16])[0]
            body = position + 16
        elif box_size == 0:
            box_size = end - position
        if box_size < body - position or position + box_size > end:
            return
        yield box_type, body, position + box_size
        position += box_size


def _parse_moov(data: bytes, file_size: int) -> MediaInfo:
    info = MediaInfo()
    has_video = False
    for box, start, end in _boxes(data):
        if box == b"mvhd":
            version = data[start]
            if version == 1:
                scale, duration = struct.unpack(">IQ", data[start + 20:start + 32])
            else:
                scale, duration = struct.unpack(">II", data[start + 12:start + 20])
            if scale:
                info.duration = duration / scale
        elif box == b"trak":
            has_video = _parse_trak(data, start, end, info) or has_video
        elif box == b"udta":
            _parse_udta(data, start, end, info)
    info.kind = "video" if has_video else "audio"
    if info.duration:
        info.bitrate = int(file_size * 8 / info.duration)
    return info


def _child(data: bytes, start: int, end: int, *path: bytes) -> tuple[int, int] | None:
    for name in path:
        for box, body, box_end in _boxes(data, start, end):
            if box == name:
                start, end = body, box_end
                break
        else:
            return None
    return start, end


def _parse_trak(data: bytes, start: int, end: int, info: MediaInfo) -> bool:
    handler = _child(data, start, end, b"mdia", b"hdlr")
    if handler is None:
        return False
    kind = data[handler[0] + 8:handler[0] + 12]
    table = _child(data, start, end, b"mdia", b"minf", b"stbl", b"stsd")
    if kind == b"vide":
        header = _child(data, start, end, b"tkhd")
        if header is not None:
            info.width = struct.unpack(">I", data[header[1] - 8:header[1] - 4])[0] >> 16
            info.height = struct.unpack(">I", data[header[1] - 4:header[1]])[0] >> 16
        if table is not None:
            info.codec = _fourcc(data[table[0] + 12:table[0] + 16])
        return True
    if kind == b"soun" and table is not None:
        info.audio_codec = _fourcc(data[table[0] + 12:table[0] + 16])
        entry = table[0] + 8  # versión+banderas y número de entradas
        info.channels = struct.unpack(">H", data[entry + 24:entry + 26])[0]
        info.sample_rate = struct.unpack(">I", data[entry + 32:entry + 36])[0] >> 16
    return False


def _fourcc(raw: bytes) -> str:
    return raw.decode("latin-1").strip("\0 ")


def _parse_udta(data: bytes, start: int, end: int, info: MediaInfo) -> None:
    meta = _child(data, start, end, b"meta")
    if meta is None:
        return
    # `meta` es una "full box": sus hijos empiezan 4 bytes después.
    tags = _child(data, meta[0] + 4, meta[1], b"ilst")
    if tags is None:
        return
    fields = {b"\xa9nam": "title", b"\xa9ART": "artist", b"\xa9alb": "album"}
    for box, body, box_end in _boxes(data, tags[0], tags[1]):
        value = _child(data, body, box_end, b"data")
        if value is None:
            continue
        payload = data[value[0] + 8:value[1]]
        if box in fields:
            setattr(info, fields[box], payload.decode("utf-8", "replace"))
        elif box == b"covr" and len(payload) <= MAX_COVER:
            info.cover = bytes(payload)


# ---------------------------------------------------------------- RIFF: WAV y AVI
def _riff_chunks(fin, start: int, end: int):
    position = start
    while position + 8 <= end:
        fin.seek(position)
        chunk, chunk_size = struct.unpack("<4sI", fin.read(8))
        yield chunk, position + 8, chunk_size
        position += 8 + chunk_size + (chunk_size & 1)


def _read_wav(fin, size: int) -> MediaInfo | None:
    if fin.read(4) != b"RIFF" or fin.read(4) is None or fin.read(4) != b"WAVE":
        return None
    info = MediaInfo(kind="audio")
    byte_rate = 0
    for chunk, start, chunk_size in _riff_chunks(fin, 12, size):
        if chunk == b"fmt ":
            fin.seek(start)
            tag, info.channels, info.sample_rate, byte_rate = struct.unpack("<HHII", fin.read(12))
            info.audio_codec = {1: "PCM", 3: tr("PCM (coma flotante)"), 85: "MP3"}.get(tag, f"0x{tag:04x}")
            info.bitrate = byte_rate * 8
        elif chunk == b"data" and byte_rate:
            info.duration = min(chunk_size, size - start) / byte_rate
            return info
    return info if byte_rate else None


def _read_avi(fin, size: int) -> MediaInfo | None:
    if fin.read(4) != b"RIFF" or fin.read(4) is None or fin.read(4) != b"AVI ":
        return None
    info = MediaInfo(kind="video")
    for chunk, start, chunk_size in _riff_chunks(fin, 12, size):
        if chunk == b"LIST":
            fin.seek(start)
            if fin.read(4) != b"hdrl":
                return info if info.width else None
            for sub, sub_start, sub_size in _riff_chunks(fin, start + 4, start + chunk_size):
                if sub == b"avih":
                    fin.seek(sub_start)
                    head = fin.read(40)
                    micro, _rate, _pad, _flags, frames = struct.unpack("<5I", head[:20])
                    info.width, info.height = struct.unpack("<2I", head[32:40])
                    if micro:
                        info.duration = frames * micro / 1_000_000
                        info.frame_rate = 1_000_000 / micro
                elif sub == b"LIST":
                    fin.seek(sub_start)
                    if fin.read(4) == b"strl":
                        _read_avi_stream(fin, sub_start + 4, sub_start + sub_size, info)
            if info.duration:
                info.bitrate = int(size * 8 / info.duration)
            return info
    return None


def _read_avi_stream(fin, start: int, end: int, info: MediaInfo) -> None:
    for chunk, chunk_start, _size in _riff_chunks(fin, start, end):
        if chunk == b"strh":
            fin.seek(chunk_start)
            kind, handler = struct.unpack("<4s4s", fin.read(8))
            if kind == b"vids":
                info.codec = _fourcc(handler)
            return


# ---------------------------------------------------------------- FLAC
def _read_flac(fin, size: int) -> MediaInfo | None:
    start = _skip_id3(fin)
    fin.seek(start)
    if fin.read(4) != b"fLaC":
        return None
    info = MediaInfo(kind="audio", audio_codec="FLAC")
    while True:
        header = fin.read(4)
        if len(header) < 4:
            break
        last = header[0] & 0x80
        block = header[0] & 0x7F
        length = int.from_bytes(header[1:4], "big")
        if block == 0:
            raw = fin.read(length)
            packed = int.from_bytes(raw[10:18], "big")
            info.sample_rate = packed >> 44
            info.channels = ((packed >> 41) & 7) + 1
            samples = packed & ((1 << 36) - 1)
            if info.sample_rate:
                info.duration = samples / info.sample_rate
        elif block == 4 and length <= MAX_HEADER:
            _parse_vorbis_comment(fin.read(length), info)
        elif block == 6 and length <= MAX_COVER and info.cover is None:
            info.cover = _parse_flac_picture(fin.read(length))
        else:
            fin.seek(length, os.SEEK_CUR)
        if last:
            break
    if info.duration:
        info.bitrate = int(size * 8 / info.duration)
    return info if info.sample_rate else None


def _parse_vorbis_comment(data: bytes, info: MediaInfo) -> None:
    position = 4 + struct.unpack("<I", data[:4])[0]
    count = struct.unpack("<I", data[position:position + 4])[0]
    position += 4
    fields = {"TITLE": "title", "ARTIST": "artist", "ALBUM": "album"}
    for _ in range(min(count, 1024)):
        length = struct.unpack("<I", data[position:position + 4])[0]
        position += 4
        key, _sep, value = data[position:position + length].decode("utf-8", "replace").partition("=")
        position += length
        if key.upper() in fields and not getattr(info, fields[key.upper()]):
            setattr(info, fields[key.upper()], value)


def _parse_flac_picture(data: bytes) -> bytes | None:
    position = 4
    length = struct.unpack(">I", data[position:position + 4])[0]
    position += 4 + length
    length = struct.unpack(">I", data[position:position + 4])[0]
    position += 4 + length + 16
    length = struct.unpack(">I", data[position:position + 4])[0]
    picture = data[position + 4:position + 4 + length]
    return bytes(picture) if picture else None


# ---------------------------------------------------------------- MP3
_MPEG_BITRATES = {
    (3, 1): (0, 32, 40, 48, 56, 64, 80, 96, 112, 128, 160, 192, 224, 256, 320),
    (2, 1): (0, 8, 16, 24, 32, 40, 48, 56, 64, 80, 96, 112, 128, 144, 160),
}
_MPEG_RATES = {3: (44100, 48000, 32000), 2: (22050, 24000, 16000), 0: (11025, 12000, 8000)}
_ID3_TEXT = {b"TIT2": "title", b"TPE1": "artist", b"TALB": "album", b"TT2": "title", b"TP1": "artist", b"TAL": "album"}


def _skip_id3(fin) -> int:
    fin.seek(0)
    head = fin.read(10)
    if len(head) == 10 and head[:3] == b"ID3":
        return 10 + _synchsafe(head[6:10])
    return 0


def _synchsafe(raw: bytes) -> int:
    return (raw[0] << 21) | (raw[1] << 14) | (raw[2] << 7) | raw[3]


def _decode_id3_text(raw: bytes) -> str:
    if not raw:
        return ""
    encoding = {0: "latin-1", 1: "utf-16", 2: "utf-16-be", 3: "utf-8"}.get(raw[0], "latin-1")
    return raw[1:].decode(encoding, "replace").split("\0")[0].strip()


def _parse_id3(fin, info: MediaInfo) -> None:
    fin.seek(0)
    head = fin.read(10)
    if len(head) < 10 or head[:3] != b"ID3":
        return
    version = head[3]
    total = min(_synchsafe(head[6:10]), MAX_HEADER)
    data = fin.read(total)
    position = 0
    id_size, header_size = (3, 6) if version == 2 else (4, 10)
    while position + header_size <= len(data):
        frame = data[position:position + id_size]
        if not frame.strip(b"\0"):
            return
        if version == 2:
            length = int.from_bytes(data[position + 3:position + 6], "big")
        elif version == 4:
            length = _synchsafe(data[position + 4:position + 8])
        else:
            length = int.from_bytes(data[position + 4:position + 8], "big")
        body = data[position + header_size:position + header_size + length]
        if frame in _ID3_TEXT:
            setattr(info, _ID3_TEXT[frame], _decode_id3_text(body))
        elif frame == b"APIC" and info.cover is None:
            info.cover = _parse_apic(body)
        position += header_size + length


def _parse_apic(body: bytes) -> bytes | None:
    """Portada en ID3v2: codificación, MIME\\0, tipo, descripción\\0, imagen."""
    if len(body) < 4 or len(body) > MAX_COVER:
        return None
    encoding = body[0]
    mime_end = body.index(b"\0", 1)
    position = mime_end + 2
    if encoding in (1, 2):
        while position + 1 < len(body) and body[position:position + 2] != b"\0\0":
            position += 2
        position += 2
    else:
        position = body.index(b"\0", position) + 1
    return bytes(body[position:]) or None


def _read_mp3(fin, size: int) -> MediaInfo | None:
    start = _skip_id3(fin)
    fin.seek(start)
    window = fin.read(256 * 1024)
    for offset in range(len(window) - 4):
        if window[offset] != 0xFF or (window[offset + 1] & 0xE0) != 0xE0:
            continue
        version = (window[offset + 1] >> 3) & 3
        layer = (window[offset + 1] >> 1) & 3
        rate_index = window[offset + 2] >> 4
        freq_index = (window[offset + 2] >> 2) & 3
        if version == 1 or layer != 1 or rate_index in (0, 15) or freq_index == 3:
            continue  # no es una cabecera MPEG capa III válida
        table = _MPEG_BITRATES[(3, 1)] if version == 3 else _MPEG_BITRATES[(2, 1)]
        bitrate = table[rate_index] * 1000
        sample_rate = _MPEG_RATES[version][freq_index]
        channels = 1 if (window[offset + 3] >> 6) == 3 else 2
        info = MediaInfo(kind="audio", audio_codec="MP3", bitrate=bitrate, sample_rate=sample_rate, channels=channels)
        samples_per_frame = 1152 if version == 3 else 576
        frames = _xing_frames(window, offset, version, channels)
        audio_bytes = size - start - offset
        if frames:
            info.duration = frames * samples_per_frame / sample_rate
            info.bitrate = int(audio_bytes * 8 / info.duration) if info.duration else bitrate
        else:
            info.duration = audio_bytes * 8 / bitrate
        _parse_id3(fin, info)
        return info
    return None


def _xing_frames(window: bytes, offset: int, version: int, channels: int) -> int:
    side = (32 if channels == 2 else 17) if version == 3 else (17 if channels == 2 else 9)
    position = offset + 4 + side
    if window[position:position + 4] in (b"Xing", b"Info"):
        flags = struct.unpack(">I", window[position + 4:position + 8])[0]
        if flags & 1:
            return struct.unpack(">I", window[position + 8:position + 12])[0]
    return 0


# ---------------------------------------------------------------- Matroska / WebM
_UNKNOWN_SIZE = object()


def _ebml_id(fin) -> int | None:
    first = fin.read(1)
    if not first or first[0] == 0:
        return None
    length = 8 - first[0].bit_length() + 1
    return int.from_bytes(first + fin.read(length - 1), "big")


def _ebml_size(fin):
    first = fin.read(1)
    if not first or first[0] == 0:
        return None
    length = 8 - first[0].bit_length() + 1
    value = (first[0] & ((1 << (8 - length)) - 1))
    raw = fin.read(length - 1)
    all_ones = value == (1 << (8 - length)) - 1 and all(b == 0xFF for b in raw)
    return _UNKNOWN_SIZE if all_ones else (value << (8 * (length - 1))) | int.from_bytes(raw, "big")


def _ebml_children(fin, end: int):
    while fin.tell() < end:
        element = _ebml_id(fin)
        size = _ebml_size(fin)
        if element is None or size is None:
            return
        start = fin.tell()
        yield element, start, size
        if size is _UNKNOWN_SIZE:
            return
        fin.seek(start + size)


def _ebml_known_children(fin, end: int):
    """Como `_ebml_children`, pero para elementos internos: un tamaño desconocido termina el recorrido."""
    for element, start, length in _ebml_children(fin, end):
        if length is _UNKNOWN_SIZE:
            return
        yield element, start, length


def _read_mkv(fin, size: int) -> MediaInfo | None:
    if fin.read(4) != b"\x1a\x45\xdf\xa3":
        return None
    fin.seek(0)
    info = MediaInfo(kind="audio")
    for element, start, length in _ebml_children(fin, size):
        if element != 0x18538067:  # Segment
            continue
        end = size if length is _UNKNOWN_SIZE else start + length
        seen = 0
        for child, child_start, child_length in _ebml_children(fin, min(end, size)):
            if child_length is _UNKNOWN_SIZE:
                break
            if child == 0x1549A966:  # Info
                _mkv_info(fin, child_start, child_length, info)
                seen += 1
            elif child == 0x1654AE6B:  # Tracks
                _mkv_tracks(fin, child_start, child_length, info)
                seen += 1
            if seen == 2:
                break
        if seen == 0:
            return None
        if info.duration:
            info.bitrate = int(size * 8 / info.duration)
        return info
    return None


def _ebml_read(fin, length: int) -> bytes:
    return fin.read(min(length, MAX_HEADER))


def _ebml_float(raw: bytes) -> float:
    return struct.unpack(">f" if len(raw) == 4 else ">d", raw)[0]


def _mkv_info(fin, start: int, length: int, info: MediaInfo) -> None:
    fin.seek(start)
    scale = 1_000_000
    duration = 0.0
    for element, child_start, child_length in _ebml_known_children(fin, start + length):
        fin.seek(child_start)
        if element == 0x2AD7B1:
            scale = int.from_bytes(_ebml_read(fin, child_length), "big")
        elif element == 0x4489:
            duration = _ebml_float(_ebml_read(fin, child_length))
        elif element == 0x7BA9:
            info.title = _ebml_read(fin, child_length).decode("utf-8", "replace")
    info.duration = duration * scale / 1_000_000_000


def _mkv_tracks(fin, start: int, length: int, info: MediaInfo) -> None:
    fin.seek(start)
    for element, child_start, child_length in _ebml_known_children(fin, start + length):
        if element == 0xAE:
            _mkv_track(fin, child_start, child_length, info)
            fin.seek(child_start + child_length)


def _mkv_track(fin, start: int, length: int, info: MediaInfo) -> None:
    fin.seek(start)
    track_type = 0
    codec = ""
    video = audio = None
    frame_nanoseconds = 0
    for element, child_start, child_length in _ebml_known_children(fin, start + length):
        fin.seek(child_start)
        if element == 0x83:
            track_type = int.from_bytes(_ebml_read(fin, child_length), "big")
        elif element == 0x23E383:
            frame_nanoseconds = int.from_bytes(_ebml_read(fin, child_length), "big")
        elif element == 0x86:
            codec = _ebml_read(fin, child_length).decode("ascii", "replace").rstrip("\0")
        elif element == 0xE0:
            video = (child_start, child_length)
        elif element == 0xE1:
            audio = (child_start, child_length)
        fin.seek(child_start + child_length)
    if track_type == 1 and video:
        info.kind = "video"
        info.codec = info.codec or codec.replace("V_", "")
        if frame_nanoseconds:
            info.frame_rate = 1_000_000_000 / frame_nanoseconds
        fin.seek(video[0])
        for element, child_start, child_length in _ebml_known_children(fin, video[0] + video[1]):
            fin.seek(child_start)
            if element == 0xB0:
                info.width = int.from_bytes(_ebml_read(fin, child_length), "big")
            elif element == 0xBA:
                info.height = int.from_bytes(_ebml_read(fin, child_length), "big")
            fin.seek(child_start + child_length)
    elif track_type == 2 and audio:
        info.audio_codec = info.audio_codec or codec.replace("A_", "")
        fin.seek(audio[0])
        for element, child_start, child_length in _ebml_known_children(fin, audio[0] + audio[1]):
            fin.seek(child_start)
            if element == 0xB5:
                info.sample_rate = int(_ebml_float(_ebml_read(fin, child_length)))
            elif element == 0x9F:
                info.channels = int.from_bytes(_ebml_read(fin, child_length), "big")
            fin.seek(child_start + child_length)


_READERS = {
    "mp4": _read_mp4, "m4v": _read_mp4, "mov": _read_mp4, "m4a": _read_mp4, "3gp": _read_mp4,
    "wav": _read_wav, "avi": _read_avi, "flac": _read_flac, "mp3": _read_mp3, "mkv": _read_mkv, "webm": _read_mkv,
}


# ---------------------------------------------------------------- FFmpeg opcional
def find_tool(name: str, extra_dir: str = "") -> str | None:
    """Ruta absoluta de una herramienta. No mira la carpeta actual: un `ffmpeg.exe` dejado
    en la carpeta que se está viendo nunca debe ejecutarse."""
    filename = name + (".exe" if sys.platform == "win32" else "")
    folders = ([extra_dir] if extra_dir else []) + os.environ.get("PATH", "").split(os.pathsep)
    for folder in folders:
        if folder and os.path.isabs(folder):
            candidate = os.path.join(folder, filename)
            if os.path.isfile(candidate):
                return candidate
    return None


def _run_tool(args: list[str]) -> bytes | None:
    flags = subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0
    try:
        done = subprocess.run(args, capture_output=True, timeout=FFMPEG_TIMEOUT, creationflags=flags,
                              stdin=subprocess.DEVNULL, check=False)
    except (OSError, subprocess.SubprocessError):
        return None
    return done.stdout if done.returncode == 0 else None


def _read_with_ffprobe(path: str, ffmpeg_dir: str) -> MediaInfo | None:
    tool = find_tool("ffprobe", ffmpeg_dir)
    if tool is None:
        return None
    output = _run_tool([tool, "-v", "error", "-print_format", "json", "-show_format", "-show_streams", path])
    if not output:
        return None
    try:
        data = json.loads(output)
    except ValueError:
        return None
    if not data.get("streams"):
        return None  # con un archivo dañado responde sin error, pero sin ninguna pista
    info = MediaInfo(kind="audio")
    fmt = data.get("format") or {}
    info.duration = _number(fmt.get("duration"))
    info.bitrate = int(_number(fmt.get("bit_rate")))
    tags = {str(k).lower(): v for k, v in (fmt.get("tags") or {}).items()}
    for stream in data.get("streams") or []:  # OGG guarda las etiquetas en la pista, no en el contenedor
        for key, value in (stream.get("tags") or {}).items():
            tags.setdefault(str(key).lower(), value)
    info.title, info.artist, info.album = (str(tags.get(k, "")) for k in ("title", "artist", "album"))
    for stream in data.get("streams") or []:
        kind = stream.get("codec_type")
        if kind == "video" and not (stream.get("disposition") or {}).get("attached_pic") and not info.width:
            info.kind = "video"
            info.width, info.height = int(stream.get("width") or 0), int(stream.get("height") or 0)
            info.codec = str(stream.get("codec_name") or "")
        elif kind == "audio" and not info.audio_codec:
            info.audio_codec = str(stream.get("codec_name") or "")
            info.sample_rate = int(_number(stream.get("sample_rate")))
            info.channels = int(stream.get("channels") or 0)
    return info


def _number(value) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0


def video_frame(path: str, ffmpeg_dir: str = "", duration: float = 0.0, width: int = 192) -> bytes | None:
    """Un fotograma en PNG, o None si no hay FFmpeg o no se pudo obtener."""
    tool = find_tool("ffmpeg", ffmpeg_dir)
    if tool is None:
        return None
    moment = min(duration * 0.1, 30.0) if duration else 1.0
    return _run_tool([tool, "-nostdin", "-v", "error", "-ss", f"{moment:.2f}", "-i", path, "-frames:v", "1", "-vf",
                      f"scale={width}:-2", "-f", "image2pipe", "-vcodec", "png", "-"]) or None
