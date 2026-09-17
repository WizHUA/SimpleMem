"""Package only allowlisted source/build artifacts; never runtime data or .env."""

import argparse
import hashlib
import json
import zipfile
from pathlib import Path

from agent_memory import __version__


def package(output: Path):
    root = Path(__file__).resolve().parents[2]
    repo = root.parent
    if not list((root / "dist").glob(f"*-{__version__}-*.whl")):
        raise SystemExit("Build the current backend wheel before packaging")
    if not (repo / "memory-ui/dist/index.html").is_file():
        raise SystemExit("Build memory-ui before packaging")
    paths = [repo / "DELIVERY.md", repo / "AGENTS.md", repo / "LICENSE"]
    for base, patterns in (
        (
            root,
            [
                "*.md",
                "pyproject.toml",
                "requirements.lock",
                ".env.example",
                "src/**/*.py",
                "tests/**/*.py",
                "scripts/*.py",
                "docs/*.md",
                "docs/*.json",
                f"dist/*-{__version__}-*.whl",
            ],
        ),
        (
            repo / "memory-ui",
            [
                "README.md",
                "package.json",
                "pnpm-lock.yaml",
                "tsconfig.json",
                "vite.config.ts",
                "index.html",
                ".env.example",
                "playwright*.ts",
                "src/**/*",
                "tests/*.ts",
                "tests/*.py",
                "dist/**/*",
            ],
        ),
    ):
        for pattern in patterns:
            paths.extend(base.glob(pattern))
    files = sorted({path for path in paths if path.is_file() and "__pycache__" not in path.parts})
    manifest = {
        str(path.relative_to(repo)).replace("\\", "/"): hashlib.sha256(path.read_bytes()).hexdigest()
        for path in files
    }
    output.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(output, "w", zipfile.ZIP_DEFLATED) as archive:
        for path in files:
            archive.write(path, path.relative_to(repo))
        archive.writestr("MANIFEST.sha256.json", json.dumps(manifest, ensure_ascii=False, indent=2))
    print(f"Packaged {len(files)} files: {output}")
    print(f"SHA256: {hashlib.sha256(output.read_bytes()).hexdigest()}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=Path("dist/full-forward-delivery.zip"))
    package(parser.parse_args().output)
