"""Bundle both applications with their sibling layout, excluding local state."""
from datetime import datetime, timezone
from pathlib import Path
import tarfile

staff = Path(__file__).resolve().parents[2]
student = staff.parent / 'UTB2'
if not (student / 'backend/app/main.py').is_file():
    raise SystemExit('Place UTB2 next to timetable-system before packaging.')
release_dir = staff / 'deploy/releases'
release_dir.mkdir(exist_ok=True)
archive = release_dir / ('okak-' + datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ') + '.tar.gz')
excluded = {'node_modules', '.git', '.venv', 'venv', '__pycache__', 'dist', 'backups', 'releases', '.DS_Store'}

def safe(path):
    return (not any(part in excluded for part in path.parts)
            and not (path.name.startswith('.env') and path.name != '.env.example')
            and path.suffix not in {'.pyc', '.db', '.sqlite', '.dump', '.log'})

def files(root, relative):
    path = root / relative
    if path.is_file():
        yield path
    elif path.is_dir():
        for item in sorted(path.rglob('*')):
            if item.is_file() and not item.is_symlink() and safe(item.relative_to(root)):
                yield item

with tarfile.open(archive, 'w:gz') as bundle:
    for root, name, selections in (
        (staff, 'timetable-system', ['README.md', 'deploy', 'backend/database', 'frontend/src', 'frontend/public']),
        (student, 'UTB2', ['README.md', 'backend/app', 'frontend/src', 'frontend/public']),
    ):
        candidates = set()
        for selection in selections:
            candidates.update(files(root, selection))
        for area in ('backend', 'frontend'):
            for item in (root / area).iterdir():
                if (item.is_file() and safe(item.relative_to(root))
                        and (item.suffix in {'.py', '.txt', '.json', '.js', '.html', '.conf'}
                             or item.name in {'Dockerfile', '.dockerignore', '.env.example'})):
                    candidates.add(item)
        for item in sorted(candidates):
            if safe(item.relative_to(root)):
                bundle.add(item, arcname=str(Path(name) / item.relative_to(root)), recursive=False)
print(archive)
