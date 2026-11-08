"""Lo que guardan en una imagen o un vídeo los programas de generación con IA: el prompt, el flujo de trabajo (workflow)
y con qué programa se hizo.

Cada programa lo guarda a su manera:
- ComfyUI: en los textos de un PNG («prompt», el grafo que se ejecutó, y «workflow», el que se edita), en el EXIF de un WebP
  («prompt:{…}», «workflow:{…}») y en el comentario de un vídeo, como JSON.
- Automatic1111, Forge y compatibles: en el texto «parameters» del PNG o en el comentario EXIF de un JPEG o un WebP:
  el prompt, «Negative prompt: …» y una línea «Steps: 20, Sampler: …».
- Fooocus y SwarmUI: un JSON en «parameters». InvokeAI: «invokeai_metadata». NovelAI: «Software», «Description» y «Comment».
  Midjourney: la descripción, con su «Job ID».

Solo se leen los trozos de metadatos del archivo, no la imagen: en un PNG se saltan los datos de la imagen, en un JPEG se
para al empezar la imagen y en un MP4 se busca la caja de metadatos. Los archivos pueden estar dañados o ser hostiles:
todo lo que se lee tiene un tope y un formato inesperado da «nada», nunca una excepción."""
from __future__ import annotations

import json
import re
import struct
import zlib
from dataclasses import dataclass

from .i18n import N_, tr

PNG_SIGNATURE = b"\x89PNG\r\n\x1a\n"
MAX_TEXT = 16 * 1024 * 1024  # de cada trozo de texto: el flujo de ComfyUI ocupa unos cientos de KB
MAX_CHUNKS = 20_000
HEAD_BYTES = 512 * 1024
VIDEO_EDGE_BYTES = 2 * 1024 * 1024  # un MKV guarda sus etiquetas al principio o al final
MAX_PROMPT = 20_000
VIDEO_EXTENSIONS = {"mp4", "m4v", "mov", "mkv", "webm"}
IMAGE_EXTENSIONS = {"png", "jpg", "jpeg", "webp", "avif", "jxl", "heic"}
_MP4_EXTENSIONS = {"mp4", "m4v", "mov"}
_TEXT_KEYS = {"prompt", "workflow", "parameters", "comment", "description", "software", "invokeai_metadata", "invokeai_graph",
              "invokeai_workflow", "sd-metadata", "dream", "fooocus_scheme", "usercomment", "xml:com.adobe.xmp", "exif",
              "source", "credit", "creatortool", "creator", "digitalsourcetype", "title"}
_PREFIXED_JSON = re.compile(rb"(?i)(prompt|workflow)\s*:\s*(?=\{)")
_VIDEO_JSON = re.compile(rb'\{\s*"(?:prompt|workflow)"\s*:')
_SETTING = re.compile(r'\s*([^:,]+?):\s*("(?:\\.|[^\\"])*"|[^,]*)(?:,|$)')
_A1111_LABELS = {"Steps": N_("Pasos"), "Sampler": N_("Muestreador"), "Schedule type": N_("Planificador"), "CFG scale": "CFG", "Seed": N_("Semilla"),
                 "Size": N_("Tamaño"), "Model": N_("Modelo"), "Denoising strength": N_("Reducción de ruido"), "Version": N_("Versión")}


@dataclass(frozen=True)
class Generation:
    tool: str = ""  # el programa, si se reconoce
    prompt: str = ""
    negative: str = ""
    workflow: bool = False  # lleva el flujo con que se generó, para volver a abrirlo en el programa
    nodes: int = 0  # los nodos de ese flujo, si se sabe
    settings: tuple[tuple[str, str], ...] = ()  # (dato, valor): modelo, pasos, semilla…
    workflow_data: str = ""  # el texto o JSON del flujo de trabajo

    @property
    def has_prompt(self) -> bool:
        return bool(self.prompt)

    @property
    def model(self) -> str:
        return dict(self.settings).get(tr("Modelo"), "")

    @property
    def marks(self) -> tuple[bool, bool, str, str]:
        """Lo que se marca en la lista: prompt, flujo, programa y modelo."""
        return self.has_prompt, self.workflow, self.tool, self.model


# ---------------------------------------------------------------- lectura de los textos de cada formato
def _png_texts(handle) -> dict[str, str]:
    """Los textos del PNG. Los datos de la imagen se saltan sin leerlos; algunos programas guardan sus textos detrás."""
    texts: dict[str, str] = {}
    for _ in range(MAX_CHUNKS):
        header = handle.read(8)
        if len(header) < 8:
            break
        length, kind = struct.unpack(">I4s", header)
        if kind == b"IEND":
            break
        if kind in (b"tEXt", b"zTXt", b"iTXt", b"eXIf", b"caBX") and length <= MAX_TEXT:
            data = handle.read(length)
            handle.seek(4, 1)
            if kind == b"eXIf":
                texts.update(_exif_texts(data))
                continue
            if kind == b"caBX":
                texts["c2pa"] = texts.get("c2pa", "") + data.decode("latin-1", "replace")
                continue
            keyword, _, rest = data.partition(b"\0")
            key = keyword.decode("latin-1").lower()
            if key not in _TEXT_KEYS:
                continue
            try:
                if kind == b"tEXt":
                    value = rest.decode("utf-8") if _is_utf8(rest) else rest.decode("latin-1")
                elif kind == b"zTXt":
                    value = _inflate(rest[1:]).decode("latin-1")
                else:
                    compressed, rest = rest[:1] == b"\1", rest[2:]
                    _language, _, rest = rest.partition(b"\0")
                    _translated, _, rest = rest.partition(b"\0")
                    value = (_inflate(rest) if compressed else rest).decode("utf-8", "replace")
            except (zlib.error, ValueError):
                continue
            texts[key] = value
            if key == "xml:com.adobe.xmp":
                texts.update(_xmp_texts(value.encode("utf-8", "replace")))
        else:
            handle.seek(length + 4, 1)
    return texts


def _is_utf8(data: bytes) -> bool:
    try:
        data.decode("utf-8")
    except UnicodeDecodeError:
        return False
    return True


def _inflate(data: bytes) -> bytes:
    """Con tope: un trozo comprimido hostil podría ocupar gigas al descomprimirlo."""
    decompressor = zlib.decompressobj()
    result = decompressor.decompress(data, MAX_TEXT)
    if decompressor.unconsumed_tail:
        raise ValueError("demasiado grande")
    return result


def _exif_texts(data: bytes) -> dict[str, str]:
    """Del EXIF, los textos donde estos programas guardan lo suyo: el comentario del usuario, la descripción, la marca y el modelo."""
    if data.startswith(b"Exif\0\0"):
        data = data[6:]
    if len(data) < 8 or data[:2] not in (b"II", b"MM"):
        return {}
    order = "<" if data[:2] == b"II" else ">"
    texts: dict[str, str] = {}
    names = {0x010E: "description", 0x010F: "make", 0x0110: "model", 0x0131: "software", 0x013B: "artist",
             0x8298: "copyright", 0x9286: "usercomment", 0x9C9C: "xpcomment"}

    def walk(offset: int, depth: int) -> None:
        if depth > 2 or not 8 <= offset <= len(data) - 2:
            return
        count = struct.unpack_from(order + "H", data, offset)[0]
        for position in range(offset + 2, min(offset + 2 + count * 12, len(data) - 11), 12):
            tag, kind, amount, value = struct.unpack_from(order + "HHII", data, position)
            if tag == 0x8769:  # el bloque EXIF propiamente dicho
                walk(value, depth + 1)
                continue
            if tag not in names or kind not in (1, 2, 7) or amount > MAX_TEXT:
                continue
            raw = data[value:value + amount] if amount > 4 else data[position + 8:position + 8 + amount]
            text = _comment_text(raw, order) if tag == 0x9286 else (raw.decode("utf-16-le", "replace") if tag == 0x9C9C else
                                                                    raw.decode("utf-8", "replace"))
            text = text.rstrip("\0").strip()
            if text:
                texts[names[tag]] = text

    try:
        walk(struct.unpack_from(order + "I", data, 4)[0], 0)
    except struct.error:
        pass
    return texts


def _comment_text(raw: bytes, order: str) -> str:
    """El comentario EXIF empieza por su codificación. En UNICODE, A1111 lo escribe en UTF-16 big-endian aunque el EXIF sea little-endian."""
    code, body = raw[:8], raw[8:]
    if code.startswith(b"UNICODE"):
        if body[:2] in (b"\xfe\xff", b"\xff\xfe"):
            return body.decode("utf-16", "replace")
        big = body[:1] == b"\0" or (order == ">" and body[1:2] != b"\0")
        return body.decode("utf-16-be" if big else "utf-16-le", "replace")
    return body.decode("utf-8", "replace") if code.strip(b"\0 ") in (b"ASCII", b"") else raw.decode("utf-8", "replace")


def _jpeg_texts(handle) -> dict[str, str]:
    """Los segmentos del principio del JPEG, hasta que empieza la imagen: EXIF, XMP y comentarios."""
    texts: dict[str, str] = {}
    for _ in range(256):
        marker = handle.read(2)
        if len(marker) < 2 or marker[0] != 0xFF or marker[1] in (0xD9, 0xDA):
            break
        if marker[1] in (0x01, 0xFF) or 0xD0 <= marker[1] <= 0xD7:
            continue
        size = handle.read(2)
        if len(size) < 2:
            break
        length = struct.unpack(">H", size)[0] - 2
        data = handle.read(max(0, length))
        if marker[1] == 0xE1 and data.startswith(b"Exif\0\0"):
            texts.update(_exif_texts(data))
        elif marker[1] == 0xE1 and b"xmpmeta" in data[:400]:
            texts.update(_xmp_texts(data))
        elif marker[1] == 0xEB and (data.startswith(b"JP\0") or b"c2pa" in data[:100] or b"jumb" in data[:100]):
            texts["c2pa"] = texts.get("c2pa", "") + data.decode("latin-1", "replace")
        elif marker[1] == 0xFE:
            texts["comment"] = data.decode("utf-8", "replace")
    return texts


def _webp_texts(handle) -> dict[str, str]:
    """Los trozos del WebP: el EXIF y el XMP van al final, detrás de la imagen, que se salta."""
    header = handle.read(12)
    if len(header) < 12 or header[:4] != b"RIFF" or header[8:12] != b"WEBP":
        return {}
    texts: dict[str, str] = {}
    for _ in range(MAX_CHUNKS):
        chunk = handle.read(8)
        if len(chunk) < 8:
            break
        kind, length = chunk[:4], struct.unpack("<I", chunk[4:])[0]
        if kind in (b"EXIF", b"XMP ", b"c2pa", b"caBX") and length <= MAX_TEXT:
            data = handle.read(length)
            if kind == b"EXIF":
                texts.update(_exif_texts(data))
            elif kind == b"XMP ":
                texts.update(_xmp_texts(data))
            else:
                texts["c2pa"] = texts.get("c2pa", "") + data.decode("latin-1", "replace")
            handle.seek(length & 1, 1)
        else:
            handle.seek(length + (length & 1), 1)
    return texts


def _xmp_texts(data: bytes) -> dict[str, str]:
    text = data.decode("utf-8", "replace")
    texts: dict[str, str] = {"_xmp_raw": text}
    patterns = (
        ("description", r"<dc:description>.*?<rdf:li[^>]*>(.*?)</rdf:li>"),
        ("usercomment", r"<exif:UserComment>.*?<rdf:li[^>]*>(.*?)</rdf:li>"),
        ("parameters", r'sd-metadata:parameters="([^"]*)"'),
        ("creatortool", r"<xmp:CreatorTool>(.*?)</xmp:CreatorTool>"),
        ("creatortool", r'xmp:CreatorTool="([^"]*)"'),
        ("software", r"<tiff:Software>(.*?)</tiff:Software>"),
        ("software", r'tiff:Software="([^"]*)"'),
        ("credit", r"<photoshop:Credit>(.*?)</photoshop:Credit>"),
        ("credit", r'photoshop:Credit="([^"]*)"'),
        ("source", r"<photoshop:Source>(.*?)</photoshop:Source>"),
        ("source", r'photoshop:Source="([^"]*)"'),
        ("digitalsourcetype", r"<Iptc4xmpExt:digitalSourceType>.*?<rdf:li[^>]*>(.*?)</rdf:li>"),
        ("digitalsourcetype", r"<Iptc4xmpExt:digitalSourceType>(.*?)</Iptc4xmpExt:digitalSourceType>"),
        ("digitalsourcetype", r'digitalSourceType="([^"]*)"'),
        ("creator", r"<dc:creator>.*?<rdf:li[^>]*>(.*?)</rdf:li>"),
    )
    for key, pattern in patterns:
        if key not in texts:
            found = re.search(pattern, text, re.DOTALL | re.IGNORECASE)
            if found:
                texts[key] = _unescape_xml(found.group(1)).strip()
    return texts


def _unescape_xml(text: str) -> str:
    return text.replace("&lt;", "<").replace("&gt;", ">").replace("&quot;", '"').replace("&apos;", "'").replace("&#xA;", "\n").replace("&amp;", "&")


def _mp4_metadata(handle, size: int) -> bytes:
    """Las cajas de metadatos de un MP4 (`udta` y `meta` dentro de `moov`), sin leer las tablas de las pistas, que en un vídeo largo ocupan megas."""
    def boxes(start: int, end: int):
        position = start
        for _ in range(MAX_CHUNKS):
            if position + 8 > end:
                return
            handle.seek(position)
            header = handle.read(16)
            if len(header) < 8:
                return
            length, kind = struct.unpack(">I4s", header[:8])
            body = 8
            if length == 1 and len(header) == 16:
                length, body = struct.unpack(">Q", header[8:])[0], 16
            elif length == 0:
                length = end - position
            if length < body:
                return
            yield kind, position + body, min(position + length, end)
            position += length

    found = b""
    for kind, start, end in boxes(0, size):
        if kind != b"moov":
            continue
        for inner, inner_start, inner_end in boxes(start, end):
            if inner in (b"udta", b"meta") and inner_end - inner_start <= MAX_TEXT:
                handle.seek(inner_start)
                found += handle.read(inner_end - inner_start)
    return found


def _read_texts(path: str, extension: str) -> tuple[dict[str, str], bytes]:
    """Los textos con nombre y, de los formatos que no se recorren, los bytes donde buscar."""
    with open(path, "rb") as handle:
        handle.seek(0, 2)
        size = handle.tell()
        handle.seek(0)
        if extension in VIDEO_EXTENSIONS:
            texts: dict[str, str] = {}
            if extension in _MP4_EXTENSIONS:
                found = _mp4_metadata(handle, size)
                if found:
                    xmp_idx = found.find(b"<x:xmpmeta")
                    if xmp_idx != -1:
                        end_idx = found.find(b"</x:xmpmeta>", xmp_idx)
                        if end_idx != -1:
                            texts.update(_xmp_texts(found[xmp_idx:end_idx + 12]))
                    return texts, found
            handle.seek(0)
            head = handle.read(VIDEO_EDGE_BYTES)
            tail = b""
            if size > VIDEO_EDGE_BYTES:
                handle.seek(max(VIDEO_EDGE_BYTES, size - VIDEO_EDGE_BYTES))
                tail = handle.read()
            raw_vid = head + b"\n" + tail
            xmp_idx = raw_vid.find(b"<x:xmpmeta")
            if xmp_idx != -1:
                end_idx = raw_vid.find(b"</x:xmpmeta>", xmp_idx)
                if end_idx != -1:
                    texts.update(_xmp_texts(raw_vid[xmp_idx:end_idx + 12]))
            return texts, raw_vid
        start = handle.read(12)
        handle.seek(0)
        if start.startswith(PNG_SIGNATURE):
            handle.seek(len(PNG_SIGNATURE))
            return _png_texts(handle), b""
        if start[:2] == b"\xff\xd8":
            handle.seek(2)
            return _jpeg_texts(handle), b""
        if start[:4] == b"RIFF" and start[8:12] == b"WEBP":
            return _webp_texts(handle), b""
        return {}, handle.read(HEAD_BYTES)


# ---------------------------------------------------------------- interpretación
def _json(text) -> object:
    if not isinstance(text, (str, bytes)):
        return text
    try:
        return json.loads(text)
    except (ValueError, RecursionError):
        return None


def _json_at(data: bytes, start: int) -> object:
    """El objeto JSON que empieza en `start`, aunque detrás siga el resto del archivo."""
    try:
        value, _end = json.JSONDecoder().raw_decode(data[start:start + MAX_TEXT].decode("utf-8", "replace"))
    except (ValueError, RecursionError):
        return None
    return value


def _clip(text: str) -> str:
    text = text.strip()
    return text if len(text) <= MAX_PROMPT else text[:MAX_PROMPT] + "…"


def _plain(value) -> str:
    if isinstance(value, float):
        return f"{value:g}"
    return str(value) if isinstance(value, (str, int)) and not isinstance(value, bool) else ""


def _is_link(value) -> bool:
    return isinstance(value, list) and len(value) == 2 and isinstance(value[0], (str, int)) and isinstance(value[1], int)


_TEXT_INPUTS = ("text", "text_g", "text_l", "prompt", "positive", "string", "value", "populated_text", "wildcard_text", "text_positive")


def _comfy_text(nodes: dict, link, seen: set) -> str:
    """Sigue la conexión hacia atrás hasta el nodo que tiene el texto: el codificador del prompt, o el nodo de texto que le llega."""
    key = str(link[0])
    if key in seen or len(seen) > 64 or key not in nodes:
        return ""
    seen.add(key)
    inputs = nodes[key].get("inputs")
    if not isinstance(inputs, dict):
        return ""
    for name in _TEXT_INPUTS:
        value = inputs.get(name)
        if isinstance(value, str) and value.strip():
            return value
    for name in _TEXT_INPUTS + tuple(inputs):
        value = inputs.get(name)
        if _is_link(value):
            found = _comfy_text(nodes, value, seen)
            if found:
                return found
    return ""


_MODEL_FILES = ("ckpt_name", "unet_name", "model_name", "model_path")
_LOADERS = ("CheckpointLoader", "UNETLoader", "UnetLoader", "DiffusionModelLoader", "ImageOnlyCheckpointLoader")


def _comfy_model(nodes: dict, start: dict) -> tuple[str, list[str]]:
    """El checkpoint con que se generó: se sigue la entrada `model` del muestreador hacia atrás, pasando por los LoRA y los nodos que
    retocan el modelo, hasta el que lo carga. Así no se confunde con otros archivos del flujo: el escalador, el VAE, un detector…"""
    loras: list[str] = []
    node, seen = start, set()
    for _ in range(40):
        inputs = node.get("inputs") if isinstance(node.get("inputs"), dict) else {}
        kind = str(node.get("class_type", ""))
        if "lora" in kind.lower() and isinstance(inputs.get("lora_name"), str):
            loras.append(inputs["lora_name"])
        if node is not start and any(loader in kind for loader in _LOADERS):
            found = next((inputs[name] for name in _MODEL_FILES if isinstance(inputs.get(name), str)), "")
            if found:
                return found, loras
        link = inputs.get("model") if _is_link(inputs.get("model")) else inputs.get("guider") if _is_link(inputs.get("guider")) else None
        if link is None or str(link[0]) in seen or str(link[0]) not in nodes:
            break
        seen.add(str(link[0]))
        node = nodes[str(link[0])]
    for other in nodes.values():  # sin muestreador reconocible: el primer nodo que carga un checkpoint
        inputs = other.get("inputs") if isinstance(other.get("inputs"), dict) else {}
        if any(loader in str(other.get("class_type", "")) for loader in _LOADERS):
            found = next((inputs[name] for name in _MODEL_FILES if isinstance(inputs.get(name), str)), "")
            if found:
                return found, loras
    return "", loras


def _comfy(prompt_graph, workflow) -> Generation:
    nodes = {str(key): value for key, value in prompt_graph.items() if isinstance(value, dict)} if isinstance(prompt_graph, dict) else {}
    positive = negative = ""
    settings: dict[str, str] = {}
    samplers = [node for node in nodes.values() if isinstance(node.get("inputs"), dict) and _is_link(node["inputs"].get("positive"))]
    guiders = [node for node in nodes.values() if isinstance(node.get("inputs"), dict) and _is_link(node["inputs"].get("conditioning"))]
    guiders.sort(key=lambda node: "Guider" not in str(node.get("class_type", "")))  # el guía de Flux, antes que los nodos que solo retocan el texto
    model, loras = _comfy_model(nodes, (samplers or guiders or [{}])[0])
    if model:
        settings[N_("Modelo")] = model
    if loras:
        settings["LoRA"] = "\n".join(dict.fromkeys(loras))
    if samplers:
        inputs = samplers[0]["inputs"]
        positive = _comfy_text(nodes, inputs["positive"], set())
        if _is_link(inputs.get("negative")):
            negative = _comfy_text(nodes, inputs["negative"], set())
    elif guiders:
        positive = _comfy_text(nodes, guiders[0]["inputs"]["conditioning"], set())
    for node in nodes.values():
        inputs = node.get("inputs") if isinstance(node.get("inputs"), dict) else {}
        kind = str(node.get("class_type", ""))
        if not positive and "TextEncode" in kind:
            positive = next((inputs[name] for name in _TEXT_INPUTS if isinstance(inputs.get(name), str)), "")
        for name, label in (("seed", N_("Semilla")), ("noise_seed", N_("Semilla")), ("steps", N_("Pasos")),
                            ("cfg", "CFG"), ("sampler_name", N_("Muestreador")), ("scheduler", N_("Planificador")), ("denoise", N_("Reducción de ruido"))):
            value = _plain(inputs.get(name))
            if value and label not in settings and (name not in ("steps", "cfg", "denoise", "seed", "noise_seed") or "Sampler" in kind or "Noise" in kind
                                                    or "Scheduler" in kind or "Guider" in kind):
                settings[label] = value
        if "EmptyLatent" in kind and N_("Tamaño") not in settings and _plain(inputs.get("width")) and _plain(inputs.get("height")):
            settings[N_("Tamaño")] = f"{_plain(inputs['width'])}×{_plain(inputs['height'])}"
    count = 0
    if isinstance(workflow, dict):
        found = workflow.get("nodes")
        count = len(found) if isinstance(found, list) else 0
        if not positive and isinstance(found, list):  # solo el flujo: el texto está en los valores del primer codificador
            for node in found:
                values = node.get("widgets_values") if isinstance(node, dict) else None
                if "TextEncode" in str(node.get("type", "") if isinstance(node, dict) else "") and isinstance(values, list) and values and isinstance(values[0], str):
                    positive = values[0]
                    break
    wf_raw = ""
    if isinstance(workflow, dict) and workflow:
        try:
            wf_raw = json.dumps(workflow, indent=2, ensure_ascii=False)
        except Exception:
            wf_raw = ""
    elif isinstance(prompt_graph, dict) and prompt_graph:
        try:
            wf_raw = json.dumps(prompt_graph, indent=2, ensure_ascii=False)
        except Exception:
            wf_raw = ""
    return Generation("ComfyUI", _clip(positive), _clip(negative), bool(nodes) or isinstance(workflow, dict), count or len(nodes),
                      tuple((tr(label), value) for label, value in settings.items()), wf_raw)


def _a1111(text: str) -> Generation | None:
    """«prompt\\nNegative prompt: …\\nSteps: 20, Sampler: …»: el formato de Automatic1111, que copian otros."""
    lines = text.strip().split("\n")
    settings_line = ""
    if lines and re.match(r"\s*Steps:\s*\d", lines[-1]):
        settings_line = lines.pop()
    body = "\n".join(lines)
    prompt, _, negative = body.partition("Negative prompt:")
    if not prompt.strip() and not settings_line:
        return None
    settings = [(key.strip(), value.strip().strip('"')) for key, value in _SETTING.findall(settings_line)]
    version = dict(settings).get("Version", "")
    tool = ("Forge" if re.match(r"f\d", version) else "Fooocus" if "Fooocus" in body or "Fooocus" in version else
            "Automatic1111" if version.startswith("v") else tr("Automatic1111 o compatible"))
    return Generation(tool, _clip(prompt), _clip(negative), False, 0,
                      tuple((tr(_A1111_LABELS[key]) if key in _A1111_LABELS else key, value) for key, value in settings if value)[:24])


def _from_json_parameters(data: dict) -> Generation | None:
    """Fooocus y SwarmUI guardan un JSON en «parameters»."""
    if isinstance(data.get("sui_image_params"), dict):
        params = data["sui_image_params"]
        settings = [(tr(label), _plain(params.get(key))) for key, label in (("model", "Modelo"), ("seed", "Semilla"), ("steps", "Pasos"), ("cfgscale", "CFG"),
                                                                            ("sampler", "Muestreador"))]
        return Generation("SwarmUI", _clip(_plain(params.get("prompt"))), _clip(_plain(params.get("negativeprompt"))), False, 0,
                          tuple((label, value) for label, value in settings if value))
    if isinstance(data.get("prompt"), str):
        version = _plain(data.get("version"))
        settings = [(tr(label), _plain(data.get(key))) for key, label in (("base_model", "Modelo"), ("seed", "Semilla"), ("steps", "Pasos"),
                                                                          ("guidance_scale", "CFG"), ("sampler", "Muestreador"), ("resolution", "Tamaño"))]
        return Generation("Fooocus" if "Fooocus" in version or "fooocus" in json.dumps(data)[:4000].lower() else "", _clip(data["prompt"]),
                          _clip(_plain(data.get("negative_prompt"))), False, 0, tuple((label, value) for label, value in settings if value))
    return None


def _invoke(texts: dict[str, str]) -> Generation | None:
    wf_raw = texts.get("invokeai_workflow") or texts.get("invokeai_graph") or ""
    workflow = bool(wf_raw)
    data = _json(texts.get("invokeai_metadata"))
    if isinstance(data, dict):
        model = data.get("model")
        settings = [(tr("Modelo"), _plain(model.get("name") if isinstance(model, dict) else model)), (tr("Semilla"), _plain(data.get("seed"))),
                    (tr("Pasos"), _plain(data.get("steps"))), ("CFG", _plain(data.get("cfg_scale"))), (tr("Planificador"), _plain(data.get("scheduler")))]
        return Generation("InvokeAI", _clip(_plain(data.get("positive_prompt"))), _clip(_plain(data.get("negative_prompt"))), workflow, 0,
                          tuple((label, value) for label, value in settings if value), wf_raw)
    old = _json(texts.get("sd-metadata"))
    if isinstance(old, dict) and isinstance(old.get("image"), dict):
        prompt = old["image"].get("prompt")
        if isinstance(prompt, list):
            prompt = " ".join(_plain(part.get("prompt")) for part in prompt if isinstance(part, dict))
        return Generation("InvokeAI", _clip(_plain(prompt)), "", workflow, 0, (), wf_raw)
    if "dream" in texts:
        found = re.match(r'\s*"(.*?)"', texts["dream"], re.DOTALL)
        return Generation("InvokeAI", _clip(found.group(1) if found else texts["dream"]), "", workflow, 0, (), wf_raw)
    if workflow:
        return Generation("InvokeAI", "", "", True, 0, (), wf_raw)
    return None


def _novelai(texts: dict[str, str]) -> Generation | None:
    if "novelai" not in texts.get("software", "").lower():
        return None
    data = _json(texts.get("comment"))
    data = data if isinstance(data, dict) else {}
    settings = [(tr("Semilla"), _plain(data.get("seed"))), (tr("Pasos"), _plain(data.get("steps"))), ("CFG", _plain(data.get("scale"))),
                (tr("Muestreador"), _plain(data.get("sampler")))]
    return Generation("NovelAI", _clip(texts.get("description") or _plain(data.get("prompt"))), _clip(_plain(data.get("uc"))), False, 0,
                      tuple((label, value) for label, value in settings if value))


def _comfy_in_texts(texts: dict[str, str]) -> Generation | None:
    """Los textos «prompt» y «workflow» del PNG, o su versión en el EXIF de un WebP: «prompt:{…}» en el modelo y «workflow:{…}» en la marca."""
    graph, workflow = _json(texts.get("prompt")), _json(texts.get("workflow"))
    for value in (texts.get(key, "") for key in ("make", "model", "description", "usercomment")):
        found = _PREFIXED_JSON.match(value.encode("utf-8", "replace"))
        if found:
            parsed = _json(value[value.index("{"):])
            if found.group(1).lower() == b"prompt":
                graph = graph if isinstance(graph, dict) else parsed
            else:
                workflow = workflow if isinstance(workflow, dict) else parsed
    if isinstance(graph, dict) or isinstance(workflow, dict):
        return _comfy(graph, workflow)
    return None


def _in_bytes(data: bytes) -> Generation | None:
    """En un vídeo o un formato que no se recorre: el JSON que deja ComfyUI en el comentario («{"prompt": …, "workflow": …}»)
    o sus textos sueltos, «prompt:{…}» y «workflow:{…}»."""
    for found in _VIDEO_JSON.finditer(data):
        value = _json_at(data, found.start())
        if isinstance(value, dict) and ("prompt" in value or "workflow" in value):
            return _comfy(_json(value.get("prompt")), _json(value.get("workflow")))
    graph = workflow = None
    for found in _PREFIXED_JSON.finditer(data):
        value = _json_at(data, found.end())
        if found.group(1).lower() == b"prompt" and isinstance(value, dict):
            graph = graph or value
        elif isinstance(value, dict):
            workflow = workflow or value
    if graph or workflow:
        return _comfy(graph, workflow)
    lowered = data.lower()
    for encoding in ("utf-8", "utf-16-le", "utf-16-be"):
        mark = "negative prompt:".encode(encoding)
        if mark in lowered:
            start = max(0, lowered.index(mark) - 8000)
            text = data[start:start + 40_000].decode(encoding, "replace")
            text = text[max(text.rfind("\0", 0, text.lower().find("negative prompt:")) + 1, 0):].split("\0", 1)[0]
            return _a1111(text) or Generation(tr("Automatic1111 o compatible"))
    if any(mark in lowered for mark in (b'"prompt"', b'"workflow"')):
        return Generation("", "", "", b'"workflow"' in lowered)
    return None


def _detect_ai_service(texts: dict[str, str], raw_data: bytes = b"") -> Generation | None:
    """Detecta imágenes y vídeos creados con servicios de IA como Google Gemini, ChatGPT/DALL-E, Magnific (Freepik), Higgsfield, etc."""
    combined = [v for k, v in texts.items() if isinstance(v, str) and not k.startswith("_")]
    if "_xmp_raw" in texts:
        combined.append(texts["_xmp_raw"])
    if raw_data:
        combined.append(raw_data[:512 * 1024].decode("utf-8", "replace"))
    corpus = " ".join(combined).lower()
    if not corpus.strip():
        return None

    tool = ""
    is_google = any(k in corpus for k in ("google", "deepmind", "alphabet"))
    has_synthid = "synthid" in corpus
    has_algo = "trainedalgorithmicmedia" in corpus or "compositewithtrainedalgorithmicmedia" in corpus

    # 1. Google Gemini / Imagen / Google AI
    if "gemini" in corpus:
        tool = "Google Gemini"
    elif "imagen" in corpus and (is_google or has_algo or has_synthid):
        tool = "Google Imagen"
    elif "veo" in corpus and (is_google or has_algo):
        tool = "Google Veo"
    elif has_synthid or (is_google and has_algo):
        tool = "Google Gemini"
    elif any(phrase in corpus for phrase in ("made with google ai", "generated with google ai", "google generative ai", "google imagen", "google veo")):
        tool = "Google AI"

    # 2. ChatGPT / OpenAI / DALL-E / Sora
    elif any(k in corpus for k in ("dall-e", "dall·e", "dalle")):
        tool = "ChatGPT (DALL-E)"
    elif "chatgpt" in corpus or ("openai" in corpus and ("dall" in corpus or has_algo or "c2pa" in corpus)):
        tool = "ChatGPT (DALL-E)"
    elif "sora" in corpus and ("openai" in corpus or has_algo):
        tool = "OpenAI Sora"
    elif "openai" in corpus and (has_algo or "software" in corpus or "creatortool" in corpus):
        tool = "OpenAI"

    # 3. Magnific AI (Freepik)
    elif "magnific" in corpus:
        tool = "Magnific AI (Freepik)"
    elif "freepik" in corpus and any(k in corpus for k in ("pikaso", "generator", "trainedalgorithmicmedia", "freepik ai")):
        tool = "Freepik AI"

    # 4. Higgsfield AI
    elif "higgsfield" in corpus:
        tool = "Higgsfield AI"

    # 5. Midjourney
    elif "midjourney" in corpus:
        tool = "Midjourney"

    # 6. Adobe Firefly
    elif "firefly" in corpus or ("adobe" in corpus and has_algo):
        tool = "Adobe Firefly"

    # 7. Runway
    elif any(k in corpus for k in ("runwayml", "runway", "gen-2", "gen-3", "gen-4")):
        tool = "Runway"

    # 8. Luma AI
    elif "luma" in corpus or "dream machine" in corpus:
        tool = "Luma AI"

    # 9. Kling AI
    elif any(k in corpus for k in ("kling", "klingai", "kuaishou")):
        tool = "Kling AI"

    # 10. MiniMax / Hailuo
    elif "hailuo" in corpus or ("minimax" in corpus and any(k in corpus for k in ("ai", "video"))):
        tool = "Hailuo AI (MiniMax)"

    # 11. Pika
    elif "pika labs" in corpus or "pika.art" in corpus:
        tool = "Pika"

    # 12. Leonardo.Ai
    elif any(k in corpus for k in ("leonardo.ai", "leonardo ai", "leonardoai")):
        tool = "Leonardo.Ai"

    # 13. General IPTC / C2PA AI tag
    elif has_algo:
        tool = tr("IA generativa")

    if not tool:
        return None

    desc = texts.get("description", "").strip()
    ucomment = texts.get("usercomment", "").strip()
    prompt = ""
    if desc and not desc.startswith("<") and len(desc) > 3 and desc.lower() not in (tool.lower(), "google", "openai"):
        prompt = _clip(desc)
    elif ucomment and not ucomment.startswith("<") and len(ucomment) > 3 and ucomment.lower() not in (tool.lower(), "trainedalgorithmicmedia"):
        prompt = _clip(ucomment)

    settings = []
    if texts.get("model"):
        settings.append((tr("Modelo"), texts["model"]))
    if texts.get("software"):
        settings.append((tr("Software"), texts["software"]))
    if texts.get("credit"):
        settings.append((tr("Crédito"), texts["credit"]))

    return Generation(
        tool=tool,
        prompt=prompt,
        negative="",
        workflow=False,
        nodes=0,
        settings=tuple(settings),
        workflow_data=""
    )


def read_generation(path: str, extension: str) -> Generation | None:
    """Lo que dejó el programa con que se generó, o None si no hay nada o no se puede leer."""
    try:
        texts, data = _read_texts(path, extension.lower())
    except (OSError, struct.error, ValueError, OverflowError):
        return None
    texts = {key: value for key, value in texts.items() if value}
    if texts:
        for reader in (_comfy_in_texts, _invoke, _novelai):
            found = reader(texts)
            if found is not None:
                return found
        for key in ("parameters", "usercomment", "comment", "description"):
            value = texts.get(key, "")
            parsed = _json(value) if value.lstrip().startswith("{") else None
            found = _from_json_parameters(parsed) if isinstance(parsed, dict) else _a1111(value) if "Steps:" in value or "Negative prompt:" in value else None
            if found is not None:
                return found
        if "Job ID:" in texts.get("description", ""):
            return Generation("Midjourney", _clip(texts["description"].split("Job ID:", 1)[0].strip()))
        if "parameters" in texts:
            return Generation(tr("Automatic1111 o compatible"), _clip(texts["parameters"]))
        ai_serv = _detect_ai_service(texts, data)
        if ai_serv is not None:
            return ai_serv
        return None
    res = _in_bytes(data) if data else None
    if res is not None:
        return res
    if data:
        return _detect_ai_service({}, data)
    return None


def has_generation_data(path: str, extension: str) -> bool:
    found = read_generation(path, extension)
    return found is not None and (found.has_prompt or found.workflow or bool(found.tool))
