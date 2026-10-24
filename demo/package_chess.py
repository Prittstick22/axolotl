"""Build the UI upload ZIP from the current chess source, excluding caches."""

from pathlib import Path
from zipfile import ZIP_DEFLATED, ZipFile

def build_archive(source: Path, output: Path) -> Path:
    """Include visible case inputs, while hidden suites remain outside this tree."""
    with ZipFile(output, "w", compression=ZIP_DEFLATED) as archive:
        for path in sorted(source.rglob("*")):
            relative = path.relative_to(source).as_posix()
            if (
                path.is_file()
                and not path.is_symlink()
                and (path.suffix in {".py", ".md"} or relative == "tests/cases.json")
                and "__pycache__" not in path.parts
            ):
                archive.write(path, relative)
    return output


if __name__ == "__main__":
    root = Path(__file__).resolve().parent
    print(build_archive(root / "chess", root / "chess-input.zip"))
