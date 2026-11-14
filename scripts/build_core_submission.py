"""Плоский ZIP из core/ без данных, настроек, frontend и зависимостей веб-БД."""

import argparse
from pathlib import Path
import re
import zipfile

MODULES = ('config', 'documents', 'retrieval', 'dialogue', 'prompts', 'llm', 'validation', 'demo', 'engine')
ROOT_MODULES = ('solution.py', 'core_contract.py', 'consultant_core.py')


def flatten_imports(text: str) -> str:
    """Исходники остаются в core/; корневые импорты создаются только при сборке."""
    return re.sub(r'\bfrom core\.(' + '|'.join(MODULES) + r') import\b', r'from energytool_\1 import', text)


def build(destination: Path, root: Path | None = None):
    """Белый список не позволяет случайно упаковать закрытый DATA_DIR или .env."""
    root = root or Path(__file__).resolve().parents[1]
    destination.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(destination, 'w', compression=zipfile.ZIP_DEFLATED) as archive:
        for name in ROOT_MODULES:
            archive.writestr(name, flatten_imports((root / name).read_text(encoding='utf-8')))
        for module in MODULES:
            content = flatten_imports((root / 'core' / (module + '.py')).read_text(encoding='utf-8'))
            archive.writestr('energytool_' + module + '.py', content)
        archive.writestr('requirements.txt', (root / 'requirements.txt').read_text(encoding='utf-8'))
    if destination.stat().st_size > 128 * 1024 * 1024:
        raise ValueError('ZIP больше 128 МБ')
    with zipfile.ZipFile(destination) as archive:
        names = archive.namelist()
        assert len(names) == len(set(names))
        assert all('/' not in name and '\\' not in name for name in names)
        assert all(name.endswith('.py') or name == 'requirements.txt' for name in names)
    return names


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description='Подготовить кандидат платформенного ZIP')
    parser.add_argument('--output', type=Path, default=Path('work/core-submission.zip'))
    args = parser.parse_args()
    print('Файлов в ZIP:', len(build(args.output)))
