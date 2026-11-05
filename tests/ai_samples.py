"""Archivos fabricados como los guardan los programas de generación con IA: los textos de un PNG, el EXIF de un JPEG o de un WebP
y el comentario de un vídeo. Sin dependencias: cada formato se escribe a mano, con lo justo para que se reconozca."""
import json
import struct
import zlib

COMFY_PROMPT = {
    "3": {"class_type": "KSampler", "inputs": {"seed": 156680208700286, "steps": 20, "cfg": 8.0, "sampler_name": "euler", "scheduler": "normal",
                                               "denoise": 1.0, "model": ["4", 0], "positive": ["6", 0], "negative": ["7", 0], "latent_image": ["5", 0]}},
    "4": {"class_type": "CheckpointLoaderSimple", "inputs": {"ckpt_name": "v1-5-pruned-emaonly.safetensors"}},
    "5": {"class_type": "EmptyLatentImage", "inputs": {"width": 512, "height": 768, "batch_size": 1}},
    "6": {"class_type": "CLIPTextEncode", "inputs": {"text": "un gato astronauta, acuarela", "clip": ["4", 1]}},
    "7": {"class_type": "CLIPTextEncode", "inputs": {"text": "borroso, feo", "clip": ["4", 1]}},
    "8": {"class_type": "VAEDecode", "inputs": {"samples": ["3", 0], "vae": ["4", 2]}},
    "9": {"class_type": "SaveImage", "inputs": {"filename_prefix": "ComfyUI", "images": ["8", 0]}},
}
COMFY_WORKFLOW = {"last_node_id": 9, "nodes": [{"id": i, "type": "Nodo"} for i in range(7)], "links": [], "version": 0.4}
A1111_PARAMETERS = ("un faro en la tormenta, óleo\nNegative prompt: texto, marca de agua\n"
                    'Steps: 28, Sampler: DPM++ 2M, Schedule type: Karras, CFG scale: 6.5, Seed: 1234, Size: 832x1216, Model: juggernautXL, '
                    'Lora hashes: "estilo: 1a2b, otro: 3c4d", Version: v1.10.1')


def _chunk(kind: bytes, data: bytes) -> bytes:
    return struct.pack(">I", len(data)) + kind + data + struct.pack(">I", zlib.crc32(kind + data) & 0xFFFFFFFF)


def png(texts: dict[str, str] | None = None, itxt: dict[str, str] | None = None, after_image: bool = False) -> bytes:
    """Un PNG de 1×1 con esos textos: `tEXt` en latin-1 (o UTF-8, como hace ComfyUI) e `iTXt` comprimido, como hace A1111 con los acentos."""
    extra = b""
    for key, value in (texts or {}).items():
        extra += _chunk(b"tEXt", key.encode("latin-1") + b"\0" + value.encode("utf-8"))
    for key, value in (itxt or {}).items():
        extra += _chunk(b"iTXt", key.encode("latin-1") + b"\0\1\0\0\0" + zlib.compress(value.encode("utf-8")))
    image = _chunk(b"IDAT", zlib.compress(b"\0\0\0\0"))
    body = _chunk(b"IHDR", struct.pack(">IIBBBBB", 1, 1, 8, 2, 0, 0, 0)) + (image + extra if after_image else extra + image)
    return b"\x89PNG\r\n\x1a\n" + body + _chunk(b"IEND", b"")


def comfy_png() -> bytes:
    return png({"prompt": json.dumps(COMFY_PROMPT), "workflow": json.dumps(COMFY_WORKFLOW)})


def exif(entries: dict[int, bytes], user_comment: bytes | None = None, order: str = ">") -> bytes:
    """Un bloque EXIF (TIFF) con textos en el primer directorio y, si se da, el comentario del usuario en el directorio EXIF."""
    mark = b"MM" if order == ">" else b"II"
    tags = sorted(entries.items())
    first_count = len(tags) + (1 if user_comment is not None else 0)
    first_size = 2 + first_count * 12 + 4
    data_at = 8 + first_size
    exif_ifd_at = None
    payload = b""
    fields = b""
    for tag, value in tags:
        value = value + b"\0"
        fields += struct.pack(order + "HHII", tag, 2, len(value), data_at + len(payload))
        payload += value
    if user_comment is not None:
        exif_ifd_at = data_at + len(payload)
        fields += struct.pack(order + "HHII", 0x8769, 4, 1, exif_ifd_at)
    ifd0 = struct.pack(order + "H", first_count) + fields + b"\0\0\0\0"
    block = mark + struct.pack(order + "HI", 42, 8) + ifd0 + payload
    if user_comment is not None:
        comment_at = exif_ifd_at + 2 + 12 + 4
        block += struct.pack(order + "H", 1) + struct.pack(order + "HHII", 0x9286, 7, len(user_comment), comment_at) + b"\0\0\0\0" + user_comment
    return block


def a1111_jpeg(text: str = A1111_PARAMETERS) -> bytes:
    """Como lo guarda A1111 con piexif: el comentario en UNICODE, en UTF-16 big-endian."""
    block = b"Exif\0\0" + exif({}, b"UNICODE\0" + text.encode("utf-16-be"))
    return b"\xff\xd8" + b"\xff\xe1" + struct.pack(">H", len(block) + 2) + block + b"\xff\xdb\0\x04\0\0" + b"\xff\xda\0\x02" + b"\x12" * 500 + b"\xff\xd9"


def comfy_webp() -> bytes:
    """Como lo guarda ComfyUI: el EXIF al final, detrás de la imagen, con «prompt:{…}» en el modelo y «workflow:{…}» en la marca."""
    image = b"VP8L" + struct.pack("<I", 6) + b"\x2f\0\0\0\0\0"
    block = exif({0x0110: ("prompt:" + json.dumps(COMFY_PROMPT)).encode(), 0x010F: ("workflow:" + json.dumps(COMFY_WORKFLOW)).encode()}, order="<")
    tail = b"EXIF" + struct.pack("<I", len(block)) + block + (b"\0" if len(block) & 1 else b"")
    body = b"WEBP" + image + tail
    return b"RIFF" + struct.pack("<I", len(body)) + body


def _box(kind: bytes, payload: bytes) -> bytes:
    return struct.pack(">I", len(payload) + 8) + kind + payload


def comfy_mp4(padding: int = 3 * 1024 * 1024) -> bytes:
    """Como lo guarda la extensión de vídeo de ComfyUI con ffmpeg: el JSON en el comentario (`©cmt`), dentro de `moov`, al final del archivo."""
    comment = json.dumps({"prompt": json.dumps(COMFY_PROMPT), "workflow": COMFY_WORKFLOW}).encode()
    data = _box(b"data", b"\0\0\0\1\0\0\0\0" + comment)
    ilst = _box(b"ilst", _box(b"\xa9cmt", data))
    meta = _box(b"meta", b"\0\0\0\0" + _box(b"hdlr", b"\0" * 8 + b"mdir" + b"\0" * 13) + ilst)
    moov = _box(b"moov", _box(b"mvhd", b"\0" * 100) + _box(b"trak", b"\0" * 5000) + _box(b"udta", meta))
    return _box(b"ftyp", b"isom\0\0\2\0isomiso2mp41") + _box(b"mdat", b"\0" * padding) + moov


def plain_mp4() -> bytes:
    return _box(b"ftyp", b"isom\0\0\2\0isom") + _box(b"mdat", b"\0" * 1000) + _box(b"moov", _box(b"mvhd", b"\0" * 100))
