"""generate.py - build typed SDKs for every FastAPI service installed under
`ab_service.<service>.main`, by dumping OpenAPI JSON in an isolated subprocess,
then generating a client module via:

    uv run ab-openapi-python-generator <openapi.json|url> <output_module_dir>

Output directory NOTE:
- This generator writes into the *python module directory itself*.

Example:
    ab_service.token_issuer  -> client-token-issuer/src/ab_client/token_issuer

Run:
    poetry run generate            # real generation
    poetry run generate --dry      # preview only (no files written)

"""

from __future__ import annotations

import importlib
import pkgutil
import shutil
import subprocess
import sys
from collections.abc import Iterator
from pathlib import Path

import typer

app = typer.Typer(add_completion=False)

# ------------------------------------------------------------------ #
# Paths                                                              #
# ------------------------------------------------------------------ #
RUN_DIR = Path.cwd()
ORG_DIR = RUN_DIR.parent  # org root (contains all packages)


# ------------------------------------------------------------------ #
# Helper: iterate over namespace services (WITHOUT importing them)    #
# ------------------------------------------------------------------ #
def iter_service_module_names() -> Iterator[tuple[str, str]]:
    """Yield (service_name, module_path) for every `ab_service.<svc>.main`.

    IMPORTANT: Do not import the service module here. Importing multiple FastAPI
    apps in the same interpreter can cause side-effect collisions (e.g. SQLAlchemy
    Table/Column re-registration).
    """
    try:
        ns_pkg = importlib.import_module("ab_service")
    except ImportError:
        return  # namespace package not installed

    for info in pkgutil.walk_packages(ns_pkg.__path__, prefix="ab_service."):
        if not info.name.endswith(".main"):
            continue

        # info.name looks like: ab_service.<svc>.main
        parts = info.name.split(".")
        if len(parts) < 3:
            continue

        service = parts[1]
        yield service, info.name


def _dump_openapi_in_subprocess(module_path: str, spec_path: Path, *, dry: bool) -> None:
    """Import the FastAPI service module in a fresh Python process and write OpenAPI JSON.

    This isolates import side-effects (SQLAlchemy metadata, global singletons, etc.)
    between services.
    """
    py = f"""
import importlib, json
from fastapi.openapi.utils import get_openapi

mod = importlib.import_module({module_path!r})
app = getattr(mod, "app", None)
if app is None:
    raise RuntimeError(f"{{{module_path!r}}} has no attribute 'app'")

spec = get_openapi(title=app.title, version=app.version, routes=app.routes)

with open({str(spec_path)!r}, "w", encoding="utf-8") as f:
    json.dump(spec, f, indent=2)

print("OK")
"""

    cmd = [sys.executable, "-c", py]

    if dry:
        typer.echo(f"[DRY] Would dump OpenAPI via subprocess:\n  {' '.join(cmd)}\n")
        return

    spec_path.parent.mkdir(parents=True, exist_ok=True)

    proc = subprocess.run(
        cmd,
        cwd=str(RUN_DIR),
        text=True,
        capture_output=True,
    )
    if proc.returncode != 0:
        raise RuntimeError(
            "Failed to dump OpenAPI.\n"
            f"Module: {module_path}\n"
            f"Spec:   {spec_path}\n\n"
            f"STDOUT:\n{proc.stdout}\n\n"
            f"STDERR:\n{proc.stderr}\n"
        )


# ------------------------------------------------------------------ #
# CLI command                                                        #
# ------------------------------------------------------------------ #
@app.command()
def generate(
    dry: bool = typer.Option(False, "--dry", "--dry-run", help="Preview only, don't write files"),
) -> None:
    """Generate (or preview) client SDK modules for each FastAPI service."""
    if dry:
        typer.echo("🌿  DRY-RUN – no files will be written.\n")

    any_found = False

    for service, module_path in iter_service_module_names():
        any_found = True

        package_name = service.replace("_", "-")
        service_name = "service-" + package_name
        client_name = "client-" + package_name

        # OpenAPI JSON is dumped at the org root
        spec_path = ORG_DIR / f"{service_name}-openapi.json"

        # Client repo root (already exists in your org layout)
        client_repo = ORG_DIR / client_name

        # IMPORTANT: generator output dir is the python module dir itself
        # e.g. client-token-issuer/src/ab_client/token_issuer
        out_module_dir = client_repo / "src" / "ab_client" / service

        # 1) Dump OpenAPI spec (isolated per service in a subprocess)
        try:
            _dump_openapi_in_subprocess(module_path, spec_path, dry=dry)
        except Exception as exc:  # noqa: BLE001
            typer.echo(f"⚠️  Skip {module_path}: {exc}")
            continue

        typer.echo(f"🔧  [{service_name}] openapi.json → {spec_path}")

        # 2) Ensure output module directory exists (and clear it to avoid stale files)
        if out_module_dir.exists():
            typer.echo(f"🧹  [{service_name}] Clearing existing module dir → {out_module_dir}")
            if not dry:
                shutil.rmtree(out_module_dir)

        if not dry:
            out_module_dir.mkdir(parents=True, exist_ok=True)

        # 3) Run the new generator
        cmd = [
            "uv",
            "run",
            "ab-openapi-python-generator",
            str(spec_path),  # you can swap to a URL if you prefer
            str(out_module_dir),  # module directory, not repo root
        ]

        if dry:
            typer.echo("[DRY] Would run:\n  " + " ".join(cmd) + "\n")
            continue

        typer.echo(f"🚀  [{service_name}] Generating SDK → {out_module_dir}")
        subprocess.run(cmd, check=True)
        typer.echo(f"✅  [{service_name}] SDK ready\n")

    if not any_found:
        typer.echo("❗  No FastAPI services found in the 'ab_service.' namespace")


def main() -> None:  # Poetry entry-point
    app()


if __name__ == "__main__":
    main()
