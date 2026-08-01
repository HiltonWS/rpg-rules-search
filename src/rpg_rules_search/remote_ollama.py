from __future__ import annotations

import argparse
import os
import platform
import shutil
import subprocess
import tempfile
import urllib.request
from collections.abc import Callable, Sequence
from pathlib import Path

from rpg_rules_search.config import OllamaSettings, load_ollama_settings, save_ollama_settings

DEFAULT_DATA_DIR = Path.home() / ".local" / "share" / "rpg-rules-search"
LINUX_INSTALL_URL = "https://ollama.com/install.sh"


class RemoteOllamaSetupError(RuntimeError):
    pass


Runner = Callable[..., subprocess.CompletedProcess[object]]
Downloader = Callable[[str, str], tuple[str, object]]


def install_ollama(
    *,
    system: str | None = None,
    runner: Runner = subprocess.run,
    downloader: Downloader = urllib.request.urlretrieve,
) -> None:
    if shutil.which("ollama") is not None:
        return
    operating_system = system or platform.system()
    if operating_system == "Linux":
        with tempfile.TemporaryDirectory() as temporary_dir:
            installer_path = Path(temporary_dir) / "install-ollama.sh"
            downloader(LINUX_INSTALL_URL, str(installer_path))
            runner(["/bin/sh", str(installer_path)], check=True)
        return
    if operating_system == "Darwin":
        if shutil.which("brew") is None:
            raise RemoteOllamaSetupError("Homebrew não está instalado")
        runner(["brew", "install", "ollama"], check=True)
        return
    if operating_system == "Windows":
        if shutil.which("winget") is None:
            raise RemoteOllamaSetupError("winget não está instalado")
        runner(["winget", "install", "--id", "Ollama.Ollama", "--exact"], check=True)
        return
    raise RemoteOllamaSetupError(f"Sistema não suportado: {operating_system}")


def serve_ollama(host: str, port: int, *, runner: Runner = subprocess.run) -> None:
    executable = shutil.which("ollama")
    if executable is None:
        raise RemoteOllamaSetupError("Ollama não está instalado; execute o comando install")
    environment = os.environ.copy()
    environment["OLLAMA_HOST"] = f"{host}:{port}"
    runner([executable, "serve"], check=True, env=environment)


def configure_client(base_url: str, data_dir: Path = DEFAULT_DATA_DIR) -> OllamaSettings:
    settings_path = data_dir / "ollama.json"
    current = load_ollama_settings(settings_path) or OllamaSettings()
    settings = current.model_copy(update={"base_url": base_url})
    settings = OllamaSettings.model_validate(settings.model_dump())
    save_ollama_settings(settings_path, settings)
    return settings


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m rpg_rules_search.remote_ollama",
        description="Instala e conecta um servidor Ollama para o Arquivo Arcano.",
    )
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("install", help="Instala o Ollama neste computador")
    serve = commands.add_parser("serve", help="Inicia o Ollama para acesso pela rede local")
    serve.add_argument("--host", default="127.0.0.1")
    serve.add_argument("--port", type=int, default=11434)
    configure = commands.add_parser(
        "configure-client",
        help="Configura este Arquivo Arcano para usar outro computador",
    )
    configure.add_argument("url", help="Ex.: http://192.168.1.50:11434")
    configure.add_argument("--data-dir", type=Path, default=DEFAULT_DATA_DIR)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    arguments = build_parser().parse_args(argv)
    try:
        if arguments.command == "install":
            install_ollama()
            print("Ollama instalado.")
        elif arguments.command == "serve":
            if arguments.host == "0.0.0.0":
                print("O Ollama ficará acessível na rede. Restrinja a porta 11434 no firewall.")
            serve_ollama(arguments.host, arguments.port)
        else:
            settings = configure_client(arguments.url, arguments.data_dir)
            print(f"Arquivo Arcano configurado para {settings.base_url}")
    except (OSError, subprocess.CalledProcessError, RemoteOllamaSetupError, ValueError) as error:
        print(f"Erro: {error}")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())