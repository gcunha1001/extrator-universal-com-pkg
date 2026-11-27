"""Motores de extracao: 7-Zip, unzip, unrar, bsdtar e extrator de PKG de PS4."""

from __future__ import annotations

import importlib.util
import os
import queue
import re
import select
import shutil
import signal
import subprocess
import sys
import tempfile
import threading
import time
from pathlib import Path

PKG_DIR = Path(
    os.environ.get("EXTRATOR_PKG_DIR")
    or Path.home() / ".local/share/extrator-pkg"
).expanduser()


class ExtractionError(RuntimeError):
    pass


class ExtractionCancelled(ExtractionError):
    pass


class UnsupportedEngine(ExtractionError):
    pass


SUPPORTED_EXTENSIONS = {
    ".zip": "ZIP",
    ".zipx": "ZIP",
    ".7z": "7Z",
    ".pkg": "PKG",
    ".iso": "ISO",
    ".img": "IMG",
    ".udf": "UDF",
    ".tar": "TAR",
    ".gz": "GZ",
    ".tgz": "TGZ",
    ".bz2": "BZ2",
    ".xz": "XZ",
    ".cab": "CAB",
    ".arj": "ARJ",
    ".lzh": "LZH",
    ".z": "Z",
}

ENGINE_INFO = {
    "auto": "Automatico (escolhe o mais rapido)",
    "7z": "7-Zip (ZIP/RAR/ISO/7z e mais)",
    "unzip": "unzip (ZIP)",
    "unrar": "unrar (RAR/RAR5)",
    "bsdtar": "bsdtar (ZIP/TAR/ISO)",
    "pkg": "Extrator PKG (PS4)",
}


def _which(name):
    return shutil.which(name)


def engine_binary(name):
    """Caminho do executavel de cada motor, ou None."""
    if name == "7z":
        for candidate in ("7z", "7zz", "7za"):
            found = _which(candidate)
            if found:
                return found
        return None
    if name == "pkg":
        binary = PKG_DIR / "ps4-pkg-extractor-ui/_internal/backend/pkg_extractor"
        return str(binary) if binary.is_file() else None
    return _which(name)


def available_engines():
    engines = ["auto"]
    for name in ("7z", "unzip", "unrar", "bsdtar", "pkg"):
        if engine_binary(name):
            engines.append(name)
    return engines


_pkg_core = None
_pkg_lock = threading.Lock()


def pkg_core():
    """Carrega o motor PKG existente sem modificar o aplicativo original."""
    global _pkg_core
    with _pkg_lock:
        if _pkg_core is None:
            if not (PKG_DIR / "core.py").is_file():
                raise UnsupportedEngine("Extrator PKG nao encontrado na instalacao.")
            if str(PKG_DIR) not in sys.path:
                sys.path.insert(0, str(PKG_DIR))
            spec = importlib.util.spec_from_file_location(
                "_extrator_pkg_core", PKG_DIR / "core.py")
            if spec is None or spec.loader is None:
                raise UnsupportedEngine("Nao foi possivel carregar o motor PKG.")
            module = importlib.util.module_from_spec(spec)
            sys.modules[spec.name] = module
            try:
                spec.loader.exec_module(module)
            except Exception:
                sys.modules.pop(spec.name, None)
                raise
            _pkg_core = module
        return _pkg_core


def kind_for(path):
    suffix = Path(path).suffix.lower()
    if re.fullmatch(r"\.r\d{2}", suffix):
        return "RAR"
    return SUPPORTED_EXTENSIONS.get(suffix, suffix.lstrip(".").upper() or "?")


def is_supported(path):
    suffix = Path(path).suffix.lower()
    return suffix in SUPPORTED_EXTENSIONS or bool(re.fullmatch(r"\.r\d{2}", suffix))


def free_space(path):
    path = Path(path)
    while not path.exists() and path != path.parent:
        path = path.parent
    return shutil.disk_usage(path).free


def ensure_writable(path):
    try:
        fd, name = tempfile.mkstemp(prefix=".extract-write-", dir=path)
        os.close(fd)
        Path(name).unlink()
    except OSError as error:
        raise ExtractionError(
            f"Nao e possivel gravar em {path}: {error}. "
            "Se for um disco externo, reconecte-o e escolha o caminho atual."
        ) from error


def _safe_name(name):
    safe = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", name).strip(" .")[:100]
    return safe or "arquivo"


def _target_folder(output, source):
    """Reserva a pasta de destino de forma atomica (seguro com abas em paralelo)."""
    base = _safe_name(Path(source).stem)
    for number in range(1, 100_001):
        candidate = output / (base if number == 1 else f"{base} ({number})")
        try:
            candidate.mkdir()
        except FileExistsError:
            continue
        except OSError as error:
            raise ExtractionError(f"Nao foi possivel criar {candidate}: {error}") from error
        return candidate
    raise ExtractionError("Nomes de pasta esgotados para este arquivo.")


def reserve_target(output, source):
    """Reserva (ou reutiliza) a pasta de destino; exposta para retomada."""
    return _target_folder(Path(output), Path(source))


def _resolve_engine(source, engine):
    suffix = Path(source).suffix.lower()
    available = available_engines()
    if engine == "auto":
        if suffix == ".pkg" and "pkg" in available:
            return "pkg"
        if "7z" in available:
            return "7z"
        if suffix in (".zip", ".zipx") and "unzip" in available:
            return "unzip"
        if (suffix == ".rar" or re.fullmatch(r"\.r\d{2}", suffix)) and "unrar" in available:
            return "unrar"
        if "bsdtar" in available:
            return "bsdtar"
        raise UnsupportedEngine("Nenhum motor de extracao disponivel.")
    if engine not in available:
        raise UnsupportedEngine(f"Motor '{engine}' nao disponivel neste sistema.")
    return engine


def _password_arg(engine, password):
    if not password:
        return []
    if engine == "7z":
        return [f"-p{password}"]
    if engine == "unzip":
        return [f"-P{password}"]
    if engine == "unrar":
        return [f"-p{password}"]
    return []


def _list_command(engine, source, password):
    binary = engine_binary(engine)
    if binary is None:
        raise UnsupportedEngine(f"Motor '{engine}' nao encontrado.")
    if engine == "7z":
        return [binary, "l", "-slt", *_password_arg(engine, password), str(source)]
    if engine == "unzip":
        return [binary, "-Z1", *_password_arg(engine, password), str(source)]
    if engine == "unrar":
        return [binary, "lb", *_password_arg(engine, password), str(source)]
    return [binary, "-tf", str(source)]


def _parse_listing_line(engine, line, state):
    """Conta arquivos e bytes linha a linha, sem guardar a saida inteira na memoria."""
    line = line.strip()
    if engine != "7z":
        if line:
            state["count"] += 1
        return
    if line.startswith("Path = "):
        state["folder"] = False
    elif line.startswith("Folder = "):
        state["folder"] = line.split("=", 1)[1].strip() == "+"
    elif line.startswith("Size = ") and not state["folder"]:
        try:
            state["total"] += int(line.split("=", 1)[1].strip())
        except ValueError:
            pass
        state["count"] += 1
        state["folder"] = False


def probe(source, engine="auto", password=None, fast=False):
    """Valida o arquivo e devolve {engine, kind, title, entries, bytes}.

    A listagem é lida aos poucos: mesmo um ISO enorme não estoura a memória.
    """
    source = Path(source).expanduser()
    if not source.is_file():
        raise ExtractionError(f"Arquivo de origem nao encontrado: {source}")
    resolved = _resolve_engine(source, engine)
    info = {
        "engine": resolved,
        "kind": kind_for(source),
        "title": source.stem,
        "entries": 0,
        "bytes": 0,
    }
    if resolved == "pkg":
        try:
            package = pkg_core().read_package(source)
            info["title"] = getattr(package, "title", "") or source.stem
            info["kind"] = getattr(package, "kind", "PKG") or "PKG"
        except Exception as error:
            raise ExtractionError(str(error)) from error
        return info

    try:
        process = subprocess.Popen(
            _list_command(resolved, source, password),
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
        )
    except OSError as error:
        raise ExtractionError(f"Falha ao consultar o motor: {error}") from error

    state = {"count": 0, "total": 0, "folder": False}
    details = []
    deadline = time.monotonic() + 30
    fd = process.stdout.fileno()
    pending = b""
    try:
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise ExtractionError("Tempo esgotado ao ler o cabecalho do arquivo.")
            if os.name == "posix":
                ready, _, _ = select.select([fd], [], [], min(remaining, 0.5))
                if not ready:
                    continue
            try:
                data = os.read(fd, 65536)
            except OSError:
                break
            if not data:
                break
            pending += data
            while b"\n" in pending:
                raw, pending = pending.split(b"\n", 1)
                line = raw[:8192].decode("utf-8", errors="replace").rstrip("\r")
                stripped = line.strip()
                if stripped.startswith(("Path = ", "Folder = ", "Size = ")):
                    _parse_listing_line(resolved, line, state)
                elif stripped and len(details) < 20:
                    details.append(stripped)
            if len(pending) > 8192:
                pending = pending[-8192:]
        if pending.strip():
            line = pending[:8192].decode("utf-8", errors="replace").strip()
            if line.startswith(("Path = ", "Folder = ", "Size = ")):
                _parse_listing_line(resolved, line, state)
            elif line and len(details) < 20:
                details.append(line)
        exit_code = process.wait(timeout=5)
    except subprocess.TimeoutExpired as error:
        raise ExtractionError("Tempo esgotado ao ler o cabecalho do arquivo.") from error
    finally:
        if process.poll() is None:
            process.kill()
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                pass
        process.stdout.close()

    if exit_code != 0:
        detail = details[-1] if details else f"codigo {exit_code}"
        raise ExtractionError(detail[:200])
    info["entries"], info["bytes"] = state["count"], state["total"]
    return info


def _extract_command(engine, source, target, password, fast, resume=False):
    binary = engine_binary(engine)
    if binary is None:
        raise UnsupportedEngine(f"Motor '{engine}' nao encontrado.")
    args = _password_arg(engine, password)
    if engine == "7z":
        overwrite = "-aos" if resume else "-y"
        command = [binary, "x", "-bso0", "-bsp1", overwrite, "-mmt=on", f"-o{target}"]
        if not fast:
            command.append("-bb3")  # lista cada arquivo para contar os processados
        return [*command, *args, str(source)]
    if engine == "unzip":
        command = [binary, "-n" if resume else "-o"]
        if fast:
            command.append("-q")
        return [*command, *args, str(source), "-d", str(target)]
    if engine == "unrar":
        overwrite = "-o-" if resume else "-o+"
        flags = "-idq" if fast else "-idp"
        return [binary, "x", overwrite, flags, *args, str(source), f"{target}/"]
    command = [binary, "-xf", str(source), "-C", str(target)]
    if resume:
        command.insert(2, "-k")
    return command


def _line_is_item(engine, line):
    if engine == "unzip":
        return line.startswith(("inflating:", "extracting:", "creating:"))
    if engine == "unrar":
        return bool(re.match(r"^\s*[0-9]+%", line))
    return False


def _verify_target(target, cancel=None):
    checked = 0
    for folder, directories, files in os.walk(target, followlinks=False):
        for name in directories + files:
            if cancel is not None and cancel.is_set():
                raise ExtractionCancelled("Extracao cancelada.")
            checked += 1
            path = Path(folder) / name
            if path.is_symlink() and not path.resolve().is_relative_to(target.resolve()):
                raise ExtractionError(
                    f"O arquivo continha um link simbolico para fora do destino: {path.name}"
                )
            if checked % 1000 == 0:
                time.sleep(0)  # ponto de espera cooperativo em pastas muito grandes


def _remove_tree(path, cancel=None):
    """Remove a pasta incompleta sem travar a interface e respeitando cancelamento."""
    try:
        for folder, directories, files in os.walk(path, topdown=False, followlinks=False):
            if cancel is not None and cancel.is_set():
                return
            for name in files:
                if cancel is not None and cancel.is_set():
                    return
                try:
                    (Path(folder) / name).unlink()
                except OSError:
                    pass
            for name in directories:
                if cancel is not None and cancel.is_set():
                    return
                try:
                    (Path(folder) / name).rmdir()
                except OSError:
                    pass
        if not (cancel is not None and cancel.is_set()):
            Path(path).rmdir()
    except OSError:
        pass


def _extract_archive(source, output, engine, password, fast, entries,
                     bytes_total, progress, log, cancel, resume=False,
                     target=None, pause=None):
    try:
        output.mkdir(parents=True, exist_ok=True)
    except OSError as error:
        raise ExtractionError(f"Nao foi possivel criar/acessar {output}: {error}") from error
    ensure_writable(output)

    if target is None:
        target = _target_folder(output, source)
    else:
        target = Path(target)
        target.mkdir(parents=True, exist_ok=True)

    command = _extract_command(engine, source, target, password, fast, resume)
    log(f"Motor: {ENGINE_INFO.get(engine, engine)}")
    log(f"Origem: {source}")
    log(f"Destino: {target}")

    process = None
    reader_out = None
    reader_err = None
    messages = queue.Queue(maxsize=128)
    last_errors = []
    reader_errors = []
    files_done = [0]
    stop_readers = threading.Event()
    last_progress = [0.0, -1]
    success = False

    def report(percent, done, force=False):
        """Limita atualizações a ~5 por segundo; a barra continua precisa no fim."""
        percent = max(0, min(100, int(percent)))
        now = time.monotonic()
        if not force:
            if now - last_progress[0] < 0.2 and percent != 100:
                return
            if percent == last_progress[1] and now - last_progress[0] < 1.0:
                return
        last_progress[0], last_progress[1] = now, percent
        files_total = max(entries, done)
        if files_total and not done:
            done = percent * files_total // 100
        done = min(done, files_total) if files_total else 0
        bytes_done = percent * bytes_total // 100 if bytes_total else 0
        bytes_done = min(bytes_done, bytes_total) if bytes_total else 0
        progress({
            "percent": percent,
            "files_done": done,
            "files_total": files_total,
            "bytes_done": bytes_done,
            "bytes_total": bytes_total,
        })

    try:
        process = subprocess.Popen(
            command,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )

        def read_stdout():
            percent = -1
            buffer = ""
            fd = process.stdout.fileno()
            try:
                while not stop_readers.is_set():
                    try:
                        chunk = os.read(fd, 65536)
                    except OSError:
                        break
                    if not chunk:
                        break
                    text = chunk.decode("utf-8", errors="replace")
                    for match in re.finditer(r"(\d{1,3})%", text):
                        percent = max(percent, int(match.group(1)))
                    clean = text.replace("\x08", "").replace("\r", "\n")
                    buffer += clean
                    parts = buffer.split("\n")
                    buffer = parts.pop() if parts and not clean.endswith("\n") else ""
                    for raw in parts:
                        line = raw.strip()
                        if not line:
                            continue
                        if line.startswith("- ") and not line.startswith("--"):
                            files_done[0] += 1
                        elif _line_is_item(engine, line):
                            files_done[0] += 1
                        elif not fast and not re.match(r"^\d+%", line):
                            try:
                                messages.put(line, timeout=0.1)
                            except queue.Full:
                                pass
                    estimated = percent if percent >= 0 else (
                        files_done[0] * 100 // entries if entries else 0)
                    try:
                        report(estimated, files_done[0])
                    except Exception as error:
                        reader_errors.append(error)
                        return
            except Exception as error:
                reader_errors.append(error)
            finally:
                process.stdout.close()

        def read_stderr():
            pending = ""
            fd = process.stderr.fileno()
            try:
                while not stop_readers.is_set():
                    try:
                        chunk = os.read(fd, 65536)
                    except OSError:
                        break
                    if not chunk:
                        break
                    pending += chunk.decode("utf-8", errors="replace")
                    parts = pending.split("\n")
                    pending = parts.pop() if parts else ""
                    for raw in parts:
                        line = raw.rstrip("\r")
                        if not line.strip():
                            continue
                        if len(last_errors) < 20:
                            last_errors.append(line)
                        while not stop_readers.is_set():
                            try:
                                messages.put(line, timeout=0.1)
                                break
                            except queue.Full:
                                if cancel.is_set():
                                    return
                if pending.strip():
                    line = pending.strip()
                    if len(last_errors) < 20:
                        last_errors.append(line)
                    try:
                        messages.put(line, timeout=0.1)
                    except queue.Full:
                        pass
            except Exception as error:
                reader_errors.append(error)
            finally:
                process.stderr.close()

        reader_out = threading.Thread(target=read_stdout, daemon=True)
        reader_err = threading.Thread(target=read_stderr, daemon=True)
        reader_out.start()
        reader_err.start()

        if pause is None:
            pause = threading.Event()
            pause.set()
        while (reader_out.is_alive() or reader_err.is_alive()
               or not messages.empty() or process.poll() is None):
            if cancel.is_set():
                raise ExtractionCancelled("Extracao cancelada.")
            if not pause.is_set():
                try:
                    process.send_signal(signal.SIGSTOP)
                except (OSError, ProcessLookupError):
                    pass
                while not pause.is_set():
                    if cancel.is_set():
                        try:
                            process.send_signal(signal.SIGCONT)
                        except (OSError, ProcessLookupError):
                            pass
                        raise ExtractionCancelled("Extracao cancelada.")
                    pause.wait(0.2)
                try:
                    process.send_signal(signal.SIGCONT)
                except (OSError, ProcessLookupError):
                    pass
            if reader_errors:
                raise ExtractionError(f"Falha ao acompanhar a extracao: {reader_errors[0]}")
            try:
                line = messages.get(timeout=0.05)
            except queue.Empty:
                continue
            log(line)

        try:
            exit_code = process.wait(timeout=5)
        except subprocess.TimeoutExpired as error:
            raise ExtractionError("O motor demorou para encerrar; extracao interrompida.") from error
        if reader_errors:
            raise ExtractionError(f"Falha ao acompanhar a extracao: {reader_errors[0]}")
        if cancel.is_set():
            raise ExtractionCancelled("Extracao cancelada.")
        if exit_code != 0:
            detail = last_errors[-1] if last_errors else f"codigo {exit_code}"
            hint = ""
            if any(word in detail.lower() for word in ("password", "encrypted", "wrong")):
                hint = " Se estiver protegido, informe a senha e tente novamente."
            raise ExtractionError(f"Falha na extracao: {detail}{hint}")

        if not fast:
            _verify_target(target, cancel)
        report(100, max(entries, files_done[0]), force=True)
        success = True
        return target
    finally:
        stop_readers.set()
        if process is not None and process.poll() is None:
            process.terminate()
            try:
                process.wait(timeout=3)
            except subprocess.TimeoutExpired:
                process.kill()
                try:
                    process.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    pass
        for reader in (reader_out, reader_err):
            if reader is not None:
                reader.join(timeout=3)
        if not success:
            _remove_tree(target, cancel)


def verify(source, engine="auto", password=None):
    """Testa a integridade do arquivo sem extraí-lo."""
    source = Path(source).expanduser()
    if not source.is_file():
        raise ExtractionError(f"Arquivo de origem nao encontrado: {source}")
    resolved = _resolve_engine(source, engine)
    if resolved == "pkg":
        try:
            pkg_core().read_package(source)
        except Exception as error:
            raise ExtractionError(str(error)) from error
        return
    binary = engine_binary(resolved)
    if binary is None:
        raise UnsupportedEngine(f"Motor '{resolved}' nao encontrado.")
    args = _password_arg(resolved, password)
    if resolved == "7z":
        command = [binary, "t", "-bso0", "-bsp0", *args, str(source)]
    elif resolved == "unzip":
        command = [binary, "-t", *args, str(source)]
    elif resolved == "unrar":
        command = [binary, "t", *args, str(source)]
    elif resolved == "bsdtar":
        command = [binary, "-tf", str(source)]
    else:
        return
    try:
        process = subprocess.run(
            command, stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
    except OSError as error:
        raise ExtractionError(f"Falha ao testar o arquivo: {error}") from error
    if process.returncode != 0:
        detail = process.stdout.decode("utf-8", errors="replace").strip().splitlines()
        detail = detail[-1] if detail else f"codigo {process.returncode}"
        hint = ""
        if any(word in detail.lower() for word in ("password", "encrypted", "wrong", "senha")):
            hint = " Se estiver protegido, informe a senha e tente novamente."
        raise ExtractionError(f"Falha na verificação: {detail}{hint}")


def extract(source, output, engine="auto", password=None, fast=False,
            entries=0, bytes_total=0, progress=None, log=None, cancel=None,
            resume=False, target=None, pause=None):
    """Extrai um arquivo para output/<nome> e retorna a pasta criada."""
    source = Path(source).expanduser()
    output = Path(output).expanduser().resolve()
    progress = progress or (lambda info: None)
    log = log or (lambda message: None)
    cancel = cancel or threading.Event()

    resolved = _resolve_engine(source, engine)
    if resolved == "pkg":
        last = [0.0, -1]

        def pkg_progress(current, total):
            percent = min(100, current * 100 // total) if total else 0
            now = time.monotonic()
            if now - last[0] < 0.2 and percent != 100 and percent == last[1]:
                return
            last[0], last[1] = now, percent
            progress({
                "percent": percent,
                "files_done": current,
                "files_total": total,
                "bytes_done": 0,
                "bytes_total": 0,
            })
        return pkg_core().extract(
            source, output, progress=pkg_progress, log=log, cancel=cancel)
    return _extract_archive(
        source, output, resolved, password, fast, entries,
        bytes_total, progress, log, cancel, resume=resume, target=target,
        pause=pause)
