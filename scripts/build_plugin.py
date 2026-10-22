"""Build a marketplace archive containing the server and its skills.

Usage: python scripts/build_plugin.py --output dist/plugins
The server is built from bundled source; only third-party dependencies need an index.
"""

from __future__ import annotations

import argparse
import hashlib
import shutil
import tempfile
from pathlib import Path
from zipfile import ZIP_DEFLATED, ZipFile

from gen_plugin import ROOT, __version__, build, build_marketplaces


def build_release(output: Path) -> tuple[Path, Path]:
    output = output.resolve()
    destination = output / f"mlspace-plugin-{__version__}"
    archive = output / f"{destination.name}.zip"
    if destination.exists() or archive.exists():
        raise FileExistsError(f"Release already exists: {destination}")
    output.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix=".mlspace-build-", dir=output) as stage:
        root = Path(stage) / destination.name
        plugin = root
        for name, content in build().items():
            target = plugin / name
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(content, encoding="utf-8")
        # Explicit allowlist: a source checkout may contain credentials and caches.
        sources = [ROOT / name for name in (
            'pyproject.toml', 'uv.lock', 'LICENSE', 'README.md', 'PLUGIN.md',
            'INSTALL.md', 'INSTALL_AGENT.md', 'REFERENCE.md', 'AGENTS.md', 'CONTRIBUTING.md', 'RELEASE.md',
        )]
        sources += list((ROOT / 'src/mlspace_mcp').rglob('*.py'))
        sources += [ROOT / 'src/mlspace_mcp/spec' / name for name in ('openapi.json', 'body_schemas.json')]
        sources += list((ROOT / 'skills').glob('*/SKILL.md'))
        for source in sorted(sources):
            relative = source.relative_to(ROOT)
            if any(part.startswith('.') or part == '__pycache__' for part in relative.parts):
                continue
            if source.is_symlink() or any(parent.is_symlink() for parent in source.parents if parent != ROOT and parent.is_relative_to(ROOT)):
                raise ValueError(f'Symlinks are not allowed in release files: {relative}')
            target = root / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(source, target)
        for name, content in build_marketplaces().items():
            target = root / name
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(content, encoding="utf-8")
        hashes = []
        for path in sorted(root.rglob("*")):
            if path.is_file():
                hashes.append(f"{hashlib.sha256(path.read_bytes()).hexdigest()}  {path.relative_to(root).as_posix()}")
        (root / "SHA256SUMS").write_text("\n".join(hashes) + "\n", encoding="utf-8")
        staged_archive = Path(stage) / archive.name
        with ZipFile(staged_archive, "w", compression=ZIP_DEFLATED) as zipped:
            for path in sorted(root.rglob("*")):
                if path.is_file():
                    zipped.write(path, f"{root.name}/{path.relative_to(root).as_posix()}")
        root.rename(destination)
        staged_archive.rename(archive)
    return destination, archive


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=ROOT / "dist/plugins")
    args = parser.parse_args()
    root, archive = build_release(args.output)
    print(f"Plugin marketplace: {root}\nArchive: {archive}")


if __name__ == "__main__":
    main()
