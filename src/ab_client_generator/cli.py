"""generate.py – build typed SDKs for every FastAPI service installed under
`ab_service.<service>.main`, using a YAML override.

Run:
    poetry run generate            # real generation
    poetry run generate --dry      # preview only (no SDKs written)

Dev requirements:
    poetry add --dev openapi-python-client typer pyyaml
"""

import importlib
import json
import pkgutil
import shutil
import subprocess
import sys
import tempfile
from collections.abc import Iterator
from pathlib import Path
from types import ModuleType

import typer
import yaml
from fastapi.openapi.utils import get_openapi

app = typer.Typer(add_completion=False)

# ------------------------------------------------------------------ #
# Paths                                                              #
# ------------------------------------------------------------------ #
RUN_DIR = Path.cwd()
ORG_DIR = RUN_DIR.parent  # org root (contains all packages)


# ------------------------------------------------------------------ #
# Helper: iterate over namespace services                            #
# ------------------------------------------------------------------ #
def iter_service_modules() -> Iterator[tuple[str, ModuleType]]:
    """Yield (service_name, module) for every `ab_service.<svc>.main` with app."""
    try:
        ns_pkg = importlib.import_module("ab_service")
    except ImportError:
        return  # namespace package not installed

    for info in pkgutil.walk_packages(ns_pkg.__path__, prefix="ab_service."):
        if not info.name.endswith(".main"):
            continue
        service = info.name.split(".")[1]
        try:
            mod = importlib.import_module(info.name)
            if hasattr(mod, "app"):
                yield service, mod
        except Exception as exc:  # noqa: BLE001
            typer.echo(f"⚠️  Skip {info.name}: {exc}")


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

    for package_name, mod in iter_service_modules():
        any_found = True
        fastapi_app = mod.app  # type: ignore[attr-defined]
        service_name = "service-" + package_name.replace("_", "-")
        client_folder_name = "client-" + service_name
        # TODO: use src/ab_client/{package}

        spec_path = ORG_DIR / f"{service_name}-openapi.json"
        sdk_dst = ORG_DIR / client_folder_name

        # 1. Dump OpenAPI spec
        spec = get_openapi(title=fastapi_app.title, version=fastapi_app.version, routes=fastapi_app.routes)
        spec_path.write_text(json.dumps(spec, indent=2))
        typer.echo(f"🔧  [{service_name}] openapi.json → {spec_path}")

        # 2. Clean/create SDK directory
        if sdk_dst.exists():
            if dry:
                typer.echo(f"[DRY] Would remove {sdk_dst}")
            else:
                shutil.rmtree(sdk_dst)
        if not dry:
            sdk_dst.mkdir(parents=True, exist_ok=True)

        # 3. Build YAML override (works on every generator version)
        cfg_data = {
            "package_name_override": package_name,
            "project_name_override": package_name,
        }
        with tempfile.NamedTemporaryFile("w", delete=False, suffix=".yml") as tmp:
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
            typer.echo("[DRY] Would run:\n " + " ".join(cmd) + "\n")
            continue

        # 5. Run generator
        typer.echo(f"🚀  [{service_name}] Generating SDK → {sdk_dst}")
        subprocess.run(cmd, check=True)
        typer.echo(f"✅  [{service_name}] SDK ready\n")

    if not any_found:
        typer.echo("❗  No FastAPI services with `app` found in the 'ab_service.' namespace")


def main() -> None:  # Poetry entry-point
    app()


if __name__ == "__main__":
    main()
