"""Create an allowlisted source archive without local configs, weights, scans or build outputs."""
import argparse
import os
from pathlib import Path
import zipfile

ROOT = Path(__file__).resolve().parents[1]
TOP = {'README.md', 'LICENSE', 'CONTRIBUTING.md', 'SECURITY.md', 'THIRD_PARTY_NOTICES.md',
       '.gitignore', 'pyproject.toml', 'setup.sh', 'start-server.sh'}
DIRECTORIES = {'desktop', 'web', 'mobile', 'training', 'scripts', '.github', 'docs'}
EXCLUDE = {'build', '.gradle', '.kotlin', '__pycache__', 'node_modules', '.venv', '.venvs', '.idea', '.cxx'}
SUFFIXES = {'.py', '.md', '.txt', '.toml', '.html', '.css', '.js', '.mjs', '.json', '.xml', '.kt',
            '.kts', '.sh', '.bat', '.ps1', '.properties', '.yml', '.yaml'}


def sources(root):
    for name in sorted(TOP):
        path = root / name
        if path.is_file() and not path.is_symlink():
            yield path
    for name in sorted(DIRECTORIES):
        directory = root / name
        if directory.is_symlink():
            continue
        for current, directories, files in os.walk(directory, followlinks=False):
            directories[:] = sorted(d for d in directories if d not in EXCLUDE and not (Path(current) / d).is_symlink())
            for name in sorted(files):
                path = Path(current) / name
                if path.is_symlink() or name in {'config.local.toml', 'local.properties'} or name.startswith('.env'):
                    continue
                relative = path.relative_to(root).as_posix()
                binary = relative in {'mobile/android/gradle/wrapper/gradle-wrapper.jar',
                                      'mobile/android/app/src/main/res/raw/scan_ding.wav'}
                if path.suffix in SUFFIXES or name in {'gradlew', '.gitignore'} or binary:
                    yield path


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, default=ROOT / 'dist/pokemon-tcg-scanner-source.zip')
    args = parser.parse_args(argv)
    paths = list(sources(ROOT))
    args.output.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(args.output, 'x', compression=zipfile.ZIP_DEFLATED) as archive:
        for path in paths:
            archive.write(path, 'pokemon-tcg-scanner/' + path.relative_to(ROOT).as_posix())
    print(f'{len(paths)} source files: {args.output}')
    print('Review THIRD_PARTY_NOTICES.md and docs/RELEASING.md before publication.')


if __name__ == '__main__':
    main()
