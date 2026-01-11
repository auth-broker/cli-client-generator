"""
generate.py – build typed SDKs for every FastAPI service installed under
`ab_service.<service>.main`, using a YAML override.

Run:
    poetry run generate            # real generation
    poetry run generate --dry      # preview only (no SDKs written)

Dev requirements:
    poetry add --dev openapi-python-client typer pyyaml
"""

from __future__ import annotations

import importlib
import json
import pkgutil
import shutil
import subprocess
import sys
import tempfile
from collections.abc import Iterator
from pathlib import Path

import typer
import yaml

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
    """
    Yield (service_name, module_path) for every `ab_service.<svc>.main`.

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
    """
    Import the FastAPI service module in a fresh Python process and write OpenAPI JSON.

    This isolates import side-effects (SQLAlchemy metadata, global singletons, etc.)
    between services.
    """
    # Note: keep this snippet self-contained; it runs in a new interpreter.
    py = f"""
import importlib, json, sys
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

    # Inherit env so Poetry/venv imports resolve; set cwd to RUN_DIR for consistency.
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
    dry: bool = typer.Option(False, "--dry", "--dry-run", help="Preview only, don't write SDKs"),
) -> None:
    """Generate (or preview) client SDKs for each FastAPI service."""
    if dry:
        typer.echo("🌿  DRY-RUN – no SDKs will be written.\n")

    any_found = False

    for service, module_path in iter_service_module_names():
        any_found = True

        package_name = service.replace("_", "-")
        service_name = "service-" + package_name
        client_name = "client-" + package_name
        # TODO: use src/ab_client/{package}

        spec_path = ORG_DIR / f"{service_name}-openapi.json"
        sdk_dst = ORG_DIR / client_name

        # 1. Dump OpenAPI spec (isolated per service in a subprocess)
        try:
            _dump_openapi_in_subprocess(module_path, spec_path, dry=dry)
        except Exception as exc:  # noqa: BLE001
            typer.echo(f"⚠️  Skip {module_path}: {exc}")
            continue

        typer.echo(f"🔧  [{service_name}] openapi.json → {spec_path}")

        # 2. Clean/create SDK directory
        if sdk_dst.exists():
            # Remove any existing `uv.lock` file in the SDK directory
            uv_lock_path = sdk_dst / "uv.lock"
            if uv_lock_path.exists():
                typer.echo(f"⚠️  Found existing uv.lock file, removing it: {uv_lock_path}")
                if not dry:
                    uv_lock_path.unlink()

            typer.echo(f"Directory {sdk_dst} already exists, will overwrite.")
        if not dry:
            sdk_dst.mkdir(parents=True, exist_ok=True)

        # 3. Build YAML override (works on every generator version)
        cfg_data = {
            "package_name_override": f"ab_client_{service}",
            "project_name_override": f"ab-{client_name}",
        }

        cfg_path: str | None = None
        try:
            with tempfile.NamedTemporaryFile("w", delete=False, suffix=".yml", encoding="utf-8") as tmp:
                yaml.safe_dump(cfg_data, tmp)
                cfg_path = tmp.name

            # 4. Build command
            cmd = [
                sys.executable,
                "-m",
                "openapi_python_client",
                "generate",
                "--path",
                str(spec_path),
                "--output-path",
                str(sdk_dst),
                "--config",
                cfg_path,
                "--overwrite",
            ]

            if dry:
                typer.echo("[DRY] Would run:\n  " + " ".join(cmd) + "\n")
                continue

            # 5. Run generator
            typer.echo(f"🚀  [{service_name}] Generating SDK → {sdk_dst}")
            subprocess.run(cmd, check=True)
            typer.echo(f"✅  [{service_name}] SDK ready\n")

        finally:
            # Always clean up the temp config file
            if cfg_path and Path(cfg_path).exists() and not dry:
                try:
                    Path(cfg_path).unlink()
                except OSError:
                    pass

        # 6. Migrate the generated project to uv (if available)
        uvx_path = shutil.which("uvx") or shutil.which("uvx.exe")
        uv_path = shutil.which("uv") or shutil.which("uv.exe")

        if uvx_path:
            try:
                typer.echo(f"🔧  [{service_name}] Running 'uvx migrate-to-uv' in {sdk_dst}")
                subprocess.run([uvx_path, "migrate-to-uv"], cwd=sdk_dst, check=True)
                typer.echo(f"✅  [{service_name}] uv migration complete\n")
            except subprocess.CalledProcessError as e:
                typer.echo(f"⚠️  [{service_name}] uv migration failed: {e}\n")
        elif uv_path:
            # If your setup prefers `uv migrate-to-uv` instead of uvx, you can keep this.
            # If `uv migrate-to-uv` doesn't exist in your version, remove this branch.
            try:
                typer.echo(f"🔧  [{service_name}] Running 'uv migrate-to-uv' in {sdk_dst}")
                subprocess.run([uv_path, "migrate-to-uv"], cwd=sdk_dst, check=True)
                typer.echo(f"✅  [{service_name}] uv migration complete\n")
            except subprocess.CalledProcessError as e:
                typer.echo(f"⚠️  [{service_name}] uv migration failed: {e}\n")
        else:
            typer.echo(f"⚠️  [{service_name}] 'uvx'/'uv' not found on PATH; skipping uv migration\n")

    if not any_found:
        typer.echo("❗  No FastAPI services found in the 'ab_service.' namespace")


def main() -> None:  # Poetry entry-point
    app()


if __name__ == "__main__":
    main()
