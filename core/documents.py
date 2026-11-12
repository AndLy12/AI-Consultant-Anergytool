"""Чтение закрытой базы без публикации файлов и разбиение с координатами источника."""

from dataclasses import dataclass
import csv
import hashlib
import io
import json
from pathlib import Path, PurePosixPath
import re
import stat
import time
import zipfile

from core_contract import CoreUnavailable

SUPPORTED = {'.txt', '.md', '.csv', '.json', '.pdf', '.docx'}
MAX_FILE_BYTES = 32 * 1024 * 1024
MAX_ARCHIVE_BYTES = 256 * 1024 * 1024
MAX_FILES = 2000
MAX_TEXT_CHARS = 12_000_000
MAX_TOTAL_TEXT_CHARS = 24_000_000
MAX_CHUNKS = 50000


@dataclass(frozen=True)
class Chunk:
    """Смещения относятся к извлечённому блоку, а не к байтам PDF или DOCX."""
    document_id: str
    chunk_id: str
    title: str
    text: str
    section: str | None
    page: int | None
    block: int
    start: int
    end: int


def clean_text(text: str) -> str:
    """Сохраняем переносы и таблицы; нормализуем только технические пробелы."""
    return text.replace('\r\n', '\n').replace('\r', '\n').replace('\x00', '').replace('\u00a0', ' ').strip()


def make_chunks(name: str, blocks: list[tuple[str, str | None, int | None]], size: int) -> list[Chunk]:
    """Начинаем с абзацев и разделов; крупный абзац делим по ближайшему пробелу."""
    digest = hashlib.sha256((name + '\n' + '\n'.join(b[0] for b in blocks)).encode()).hexdigest()[:20]
    doc_id = 'doc-' + digest
    title = Path(name).stem[:500] or 'Документ'
    chunks = []
    for block_index, (raw, initial_section, page) in enumerate(blocks):
        text = clean_text(raw)
        section = initial_section
        for paragraph in re.finditer(r'\S[\s\S]*?(?=\n\s*\n|\Z)', text):
            paragraph_text = paragraph.group().strip()
            if paragraph_text.startswith('#'):
                heading, _, rest = paragraph_text.partition('\n')
                section = heading.lstrip('#').strip()[:500]
                if not rest.strip():
                    continue
            start = paragraph.start()
            end = paragraph.end()
            while start < end:
                stop = min(start + size, end)
                if stop < end:
                    boundary = text.rfind(' ', start + size // 2, stop)
                    if boundary > start:
                        stop = boundary
                fragment = text[start:stop].strip()
                if fragment:
                    leading = len(text[start:stop]) - len(text[start:stop].lstrip())
                    left = start + leading
                    chunk_id = f'{doc_id}:b{block_index}:s{left}:e{left + len(fragment)}'
                    chunks.append(Chunk(doc_id, chunk_id, title, fragment, section, page,
                                        block_index, left, left + len(fragment)))
                start = stop
                while start < end and text[start].isspace():
                    start += 1
    return chunks


def _flatten_json(value):
    """Названия полей остаются рядом со значениями, чтобы не потерять условия."""
    if isinstance(value, dict):
        return '\n'.join(f'{key}: {_flatten_json(item)}' for key, item in value.items())
    if isinstance(value, list):
        return '\n\n'.join(_flatten_json(item) for item in value)
    return str(value) if value is not None else 'не указано'


def read_blocks(name: str, content: bytes):
    """Не исполняем вложения. Скан без текста считается ошибкой, а не пустой базой."""
    suffix = Path(name).suffix.lower()
    try:
        if suffix in {'.txt', '.md', '.json', '.csv'}:
            text = content.decode('utf-8-sig')
            if suffix == '.json':
                text = _flatten_json(json.loads(text))
            elif suffix == '.csv':
                rows = list(csv.reader(io.StringIO(text)))
                if not rows:
                    raise ValueError
                header = rows[0]
                text = '\n\n'.join('\n'.join(f'{header[i] if i < len(header) else i}: {cell}'
                                                   for i, cell in enumerate(row)) for row in rows[1:])
            blocks = [(text, None, None)]
        elif suffix == '.docx':
            from docx import Document
            from docx.table import Table
            from docx.text.paragraph import Paragraph
            _validate_zip(content)
            doc = Document(io.BytesIO(content))
            section = None
            blocks = []
            # iter_inner_content сохраняет взаимное положение абзацев и таблиц.
            for item in doc.iter_inner_content():
                if isinstance(item, Paragraph):
                    if item.style and item.style.name.startswith('Heading'):
                        section = item.text.strip()[:500] or section
                    blocks.append((item.text, section, None))
                elif isinstance(item, Table):
                    rows = [[cell.text for cell in row.cells] for row in item.rows]
                    header = rows[0] if rows else []
                    # Повторяем заголовки в каждой строке: число не отрывается от смысла колонки.
                    for row in rows[1:]:
                        blocks.append(('\n'.join(f'{header[i] if i < len(header) else i}: {cell}'
                                                 for i, cell in enumerate(row)), section, None))
                    if len(rows) == 1:
                        blocks.append((' | '.join(header), section, None))
        elif suffix == '.pdf':
            from pypdf import PdfReader
            reader = PdfReader(io.BytesIO(content))
            if reader.is_encrypted:
                raise ValueError
            blocks = []
            for page_number, page in enumerate(reader.pages, 1):
                stream = page.get_contents()
                if stream and len(stream.get_data()) > MAX_FILE_BYTES:
                    raise ValueError
                blocks.append((page.extract_text(extraction_mode='layout') or '', None, page_number))
        else:
            raise ValueError
        if not any(text.strip() for text, _, _ in blocks):
            raise ValueError
        if sum(len(b[0]) for b in blocks) > MAX_TEXT_CHARS:
            raise ValueError
        return blocks
    except Exception:
        # В исключении парсера могут быть фрагменты закрытого документа.
        raise CoreUnavailable('Документ не прочитан: проверьте формат, кодировку, таблицы и наличие текстового слоя') from None


def _validate_zip(content: bytes):
    """Проверяем и внутренний DOCX-архив; распаковка на диск не требуется."""
    with zipfile.ZipFile(io.BytesIO(content)) as archive:
        infos = archive.infolist()
        if len(infos) > MAX_FILES or sum(item.file_size for item in infos) > MAX_ARCHIVE_BYTES:
            raise CoreUnavailable('Архив превышает лимиты безопасного чтения')
        seen = set()
        for item in infos:
            path = PurePosixPath(item.filename.replace('\\', '/'))
            mode = item.external_attr >> 16
            if (path.is_absolute() or '..' in path.parts or ':' in item.filename
                    or stat.S_ISLNK(mode) or item.flag_bits & 1 or item.filename in seen
                    or item.file_size > MAX_FILE_BYTES):
                raise CoreUnavailable('Недопустимая запись в архиве')
            seen.add(item.filename)


def load_chunks(settings, deadline=None) -> tuple[Chunk, ...]:
    """По умолчанию читаем только knowledge-base.zip или каталог knowledge-base.

    Не сканируем весь DATA_DIR: там находятся вопросы, чужие ответы и служебные файлы.
    Для собственного формата базы путь задаётся явно через CORE_KB_PATH.
    """
    path = settings.kb_path
    if path is None:
        zipped = settings.data_dir / 'knowledge-base.zip'
        path = zipped if zipped.is_file() else settings.data_dir / 'knowledge-base'
    if not path.exists():
        raise CoreUnavailable('База знаний отсутствует; задайте DATA_DIR или CORE_KB_PATH')
    files = []
    try:
        if path.is_file() and path.suffix.lower() == '.zip':
            if path.stat().st_size > MAX_ARCHIVE_BYTES:
                raise CoreUnavailable('Архив базы слишком большой')
            content = path.read_bytes()
            _validate_zip(content)
            with zipfile.ZipFile(io.BytesIO(content)) as archive:
                for info in sorted(archive.infolist(), key=lambda x: x.filename):
                    if not info.is_dir():
                        if Path(info.filename).suffix.lower() not in SUPPORTED:
                            raise CoreUnavailable('В базе есть неподдерживаемый формат; требуется разбор состава')
                        files.append((info.filename, archive.read(info)))
        elif path.is_dir():
            total_bytes = 0
            for file in sorted(path.rglob('*')):
                if file.is_symlink():
                    raise CoreUnavailable('Символьные ссылки в базе не поддерживаются')
                if file.is_file():
                    if file.suffix.lower() not in SUPPORTED or file.stat().st_size > MAX_FILE_BYTES:
                        raise CoreUnavailable('Недопустимый формат или размер файла базы')
                    total_bytes += file.stat().st_size
                    if total_bytes > MAX_ARCHIVE_BYTES or len(files) >= MAX_FILES:
                        raise CoreUnavailable('База превышает лимиты безопасного чтения')
                    files.append((file.relative_to(path).as_posix(), file.read_bytes()))
        elif path.suffix.lower() in SUPPORTED and path.stat().st_size <= MAX_FILE_BYTES:
            files = [(path.name, path.read_bytes())]
        else:
            raise CoreUnavailable('Недопустимый путь базы знаний')
    except (OSError, zipfile.BadZipFile, RuntimeError):
        raise CoreUnavailable('Не удалось прочитать базу знаний') from None
    if len(files) > MAX_FILES or sum(len(data) for _, data in files) > MAX_ARCHIVE_BYTES:
        raise CoreUnavailable('База превышает лимиты безопасного чтения')
    chunks = []
    total_text = 0
    for name, content in files:
        if deadline is not None and time.monotonic() >= deadline:
            raise CoreUnavailable('Истекло время подготовки базы')
        blocks = read_blocks(name, content)
        total_text += sum(len(block[0]) for block in blocks)
        if total_text > MAX_TOTAL_TEXT_CHARS:
            raise CoreUnavailable('Объём извлечённого текста превышает лимит baseline')
        chunks.extend(make_chunks(name, blocks, settings.chunk_chars))
        if len(chunks) > MAX_CHUNKS:
            raise CoreUnavailable('Количество фрагментов превышает лимит baseline')
    if not chunks:
        raise CoreUnavailable('В базе нет пригодного текста')
    return tuple(chunks)
