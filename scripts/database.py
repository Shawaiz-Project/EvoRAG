"""Create or restore a custom-format PostgreSQL archive through Docker Compose."""

import argparse
import re
import subprocess
from pathlib import Path
from tempfile import NamedTemporaryFile
from typing import BinaryIO

ROOT = Path(__file__).resolve().parents[1]
DATABASE_NAME = re.compile(r"[a-z][a-z0-9_]{0,62}")


def client(
    compose: list[str],
    command: str,
    *arguments: str,
    stdin: BinaryIO | None = None,
    stdout: BinaryIO | int | None = None,
) -> None:
    subprocess.run(
        [*compose, "exec", "-T", "postgres", "sh", "-ec", command, "sh", *arguments],
        cwd=ROOT,
        check=True,
        stdin=stdin,
        stdout=stdout,
    )


def backup(compose: list[str], archive: Path) -> None:
    if archive.exists():
        raise ValueError("Choose a new archive path")
    with NamedTemporaryFile(dir=archive.parent, delete=False) as output:
        temporary = Path(output.name)
        try:
            client(
                compose,
                'exec pg_dump -U "$POSTGRES_USER" -Fc -- "$POSTGRES_DB"',
                stdout=output,
            )
            output.close()
            archive.hardlink_to(temporary)
        finally:
            output.close()
            temporary.unlink(missing_ok=True)


def restore(compose: list[str], archive: Path, database: str) -> None:
    if not DATABASE_NAME.fullmatch(database):
        raise ValueError(
            "Use a database name with lowercase letters, digits, and underscores"
        )
    with archive.open("rb") as source:
        client(
            compose, "exec pg_restore --list", stdin=source, stdout=subprocess.DEVNULL
        )
        source.seek(0)
        client(
            compose, 'exec createdb -U "$POSTGRES_USER" -T template0 -- "$1"', database
        )
        client(
            compose,
            'exec pg_restore -U "$POSTGRES_USER" --exit-on-error --single-transaction -d "$1"',
            database,
            stdin=source,
        )


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--project-name", help="Use an alternate Compose project")
    parser.add_argument(
        "--wsl-distro", help="Run Compose through the named WSL distribution"
    )
    actions = parser.add_subparsers(dest="action", required=True)
    actions.add_parser("backup").add_argument("archive", type=Path)
    restore_args = actions.add_parser("restore")
    restore_args.add_argument("archive", type=Path)
    restore_args.add_argument(
        "--database", required=True, help="Name of a new database"
    )
    args = parser.parse_args()
    compose = ["docker", "compose"]
    if args.wsl_distro:
        compose = ["wsl.exe", "-d", args.wsl_distro, "--", *compose]
    if args.project_name:
        compose += ["--project-name", args.project_name]
    try:
        if args.action == "backup":
            backup(compose, args.archive.resolve())
        else:
            restore(compose, args.archive.resolve(), args.database)
    except (OSError, ValueError, subprocess.CalledProcessError) as error:
        parser.exit(1, f"Database {args.action} failed: {error}\n")
    print(f"Database {args.action} complete.")


if __name__ == "__main__":
    main()
