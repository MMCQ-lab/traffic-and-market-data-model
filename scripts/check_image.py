"""Run inside a newly built image. Checks paths only; never opens secrets."""
import os
from pathlib import Path


def main() -> int:
    forbidden = []
    # Ignore kernel virtual filesystems; inspect the image-backed filesystem.
    for directory, subdirs, files in os.walk("/", followlinks=False):
        if directory == "/":
            subdirs[:] = [name for name in subdirs if name not in {"proc", "sys", "dev"}]
        for name in subdirs + files:
            if name == ".env" or name.startswith(".env."):
                forbidden.append(str(Path(directory) / name))
    for name in (".git", ".venv", ".pytest_cache", "data", "backups", "work", "outputs", ".vscode", ".idea"):
        if (Path("/app") / name).exists():
            forbidden.append(f"/app/{name}")
    if forbidden:
        raise RuntimeError("Forbidden image paths: " + ", ".join(sorted(set(forbidden))))
    for name in ("src/alt_data/ingestion/base.py", "alembic.ini", "requirements.txt", "alembic/versions/0007_camera_created_at.py"):
        if not (Path("/app") / name).is_file():
            raise RuntimeError(f"Missing runtime file: {name}")
    print("Image exclusions and required runtime files verified")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
