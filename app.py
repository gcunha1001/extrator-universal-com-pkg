#!/usr/bin/env python3
"""Interface única em tema claro e sóbrio: arquivos (ZIP/RAR/ISO) e PKG."""

import json
import os
import re
import shutil
import subprocess
import sys
import threading
import time
import traceback
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent
LOG_FILE = (
    Path(os.environ.get("XDG_STATE_HOME", "") or Path.home() / ".local/state")
    / "extrair-tudo" / "erros.log"
)
CHECKPOINT_FILE = (
    Path(os.environ.get("XDG_STATE_HOME", "") or Path.home() / ".local/state")
    / "extrair-tudo" / "checkpoint.json"
)
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "lib"))

import core
from core import ExtractionCancelled, ExtractionError

try:
    import tkinter as tk
    from tkinter import filedialog, messagebox, ttk
except ImportError as error:
    raise SystemExit(
        "Tkinter não encontrado: instale python3-tk/tk."
    ) from error

try:
    from tkinterdnd2 import DND_FILES, TkinterDnD
except ImportError:
    legacy = core.PKG_DIR / "lib"
    if legacy.is_dir():
        sys.path.insert(0, str(legacy))
    try:
        from tkinterdnd2 import DND_FILES, TkinterDnD
    except ImportError:
        DND_FILES, TkinterDnD = None, None

# Paleta clara e sóbria: azul-violeta discreto sobre neutros.
BG = "#f4f6f9"
HEADER = "#ffffff"
CARD = "#ffffff"
CARD2 = "#f7f8fb"
BORDER = "#dfe3ea"
ACCENT = "#5668d6"
ACCENT_HOVER = "#6a7ae0"
ACCENT_SOFT = "#eef0fb"
TEXT = "#1c1f2a"
MUTED = "#667085"
SUCCESS = "#11864a"
ERROR = "#d1434b"
WARN = "#9a6700"
FONT = "Segoe UI"
LOG_BG = "#ffffff"


def human_size(number):
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if number < 1024 or unit == "TB":
            if unit == "B":
                return f"{number:.0f} B"
            return f"{number:.0f} {unit}" if number >= 10 else f"{number:.1f} {unit}"
        number /= 1024


def default_output_dir():
    """Pasta de destino: XDG_DOCUMENTS_DIR, Documentos ou Documents."""
    configured = os.environ.get("XDG_DOCUMENTS_DIR")
    if configured:
        documents = Path(configured).expanduser()
    else:
        documents = Path.home() / "Documentos"
        if not documents.is_dir():
            documents = Path.home() / "Documents"
    return documents / "Arquivos Extraídos"


class Tooltip:
    """Dica curta que se oculta sozinha após alguns segundos."""

    def __init__(self, widget, text, delay=500, visible_time=4000):
        self.widget = widget
        self.text = text
        self.delay = delay
        self.visible_time = visible_time
        self.tip = None
        self.after_id = None
        self.hide_id = None
        widget.bind("<Enter>", self._on_enter)
        widget.bind("<Leave>", self._on_leave)
        widget.bind("<Destroy>", self._on_destroy)

    def _on_enter(self, event=None):
        self._cancel()
        self.after_id = self.widget.after(self.delay, self._show)

    def _cancel(self):
        if self.after_id is not None:
            try:
                self.widget.after_cancel(self.after_id)
            except (tk.TclError, ValueError):
                pass
            self.after_id = None

    def _cancel_hide(self):
        if self.hide_id is not None:
            try:
                self.widget.after_cancel(self.hide_id)
            except (tk.TclError, ValueError):
                pass
            self.hide_id = None

    def _show(self):
        self.after_id = None
        if self.tip is not None:
            return
        x = self.widget.winfo_rootx() + 12
        y = self.widget.winfo_rooty() + self.widget.winfo_height() + 6
        self.tip = tk.Toplevel(self.widget)
        self.tip.wm_overrideredirect(True)
        self.tip.wm_geometry(f"+{x}+{y}")
        label = tk.Label(
            self.tip, text=self.text, justify="left", wraplength=300,
            background=CARD, foreground=TEXT, relief="flat", borderwidth=0,
            highlightthickness=1, highlightbackground=BORDER,
            font=(FONT, 9), padx=9, pady=6,
        )
        label.pack()
        self.hide_id = self.widget.after(self.visible_time, self._hide)

    def _on_leave(self, event=None):
        self._cancel()
        self._hide()

    def _hide(self):
        self._cancel_hide()
        if self.tip is not None:
            try:
                self.tip.destroy()
            except (tk.TclError, AttributeError):
                pass
            self.tip = None

    def _on_destroy(self, event=None):
        self._cancel()
        self._hide()


def card(parent, padding=(10, 8)):
    """Cartao branco com borda sutil."""
    outer = tk.Frame(parent, bg=CARD, highlightthickness=1,
                     highlightbackground=BORDER, highlightcolor=BORDER)
    inner = ttk.Frame(outer, padding=padding, style="Card.TFrame")
    inner.pack(fill="both", expand=True)
    return outer, inner


class QueueTab:
    """Uma aba com fila, progresso detalhado e log proprios."""

    def __init__(self, app, parent, mode):
        self.app = app
        self.mode = mode
        self.frame = ttk.Frame(parent, padding=(0, 0), style="Card.TFrame")
        self.jobs = []
        self.counter = 0
        self.running = False
        self.probing = 0
        self.cancel_event = threading.Event()
        self.pause_event = threading.Event()
        self.pause_event.set()
        self.current_job = None
        self.job_started = 0.0
        self._eta_after = None
        self.engine_var = tk.StringVar(value="auto" if mode == "files" else "pkg")
        self._build()
        self._refresh_tree()

    # ---------- interface ----------

    def _build(self):
        is_files = self.mode == "files"
        # Reserva o rodape primeiro: os cartoes expansivos nao apertam os botoes.
        actions = ttk.Frame(self.frame)
        actions.pack(side="bottom", fill="x", pady=(2, 0))
        drop_text = (
            "Arraste ZIP, RAR, ISO, 7z, TAR...\n\nou clique para escolher arquivos"
            if is_files else
            "Arraste arquivos PKG\n\nou clique para escolher arquivos"
        )

        drop_card, drop_inner = card(self.frame, padding=(2, 2))
        drop_card.pack(fill="x")
        self.drop = tk.Frame(drop_inner, bg=CARD2, highlightthickness=1,
                             highlightbackground=BORDER, height=54)
        self.drop.pack(fill="x")
        self.drop.pack_propagate(False)
        self.drop_label = tk.Label(self.drop, bg=CARD2, fg=TEXT, cursor="hand2",
                                   text=drop_text, font=(FONT, 10), justify="center")
        self.drop_label.pack(expand=True, fill="both")
        self.drop_label.bind("<Button-1>", lambda event: self._choose_files())
        if self.app.dnd:
            for widget in (self.drop, self.drop_label):
                widget.drop_target_register(DND_FILES)
                widget.dnd_bind("<<Drop>>", self._on_drop)
                widget.dnd_bind("<<DropEnter>>", lambda event: self._highlight(True))
                widget.dnd_bind("<<DropLeave>>", lambda event: self._highlight(False))

        tools = ttk.Frame(self.frame)
        tools.pack(fill="x", pady=(2, 2))
        ttk.Button(tools, text="Escolher arquivos", style="Flat.TButton",
                   command=self._choose_files).pack(side="left")
        self.remove_button = ttk.Button(tools, text="Remover", style="Flat.TButton",
                                        command=self._remove_selected)
        self.remove_button.pack(side="left", padx=6)
        self.clear_button = ttk.Button(tools, text="Limpar fila", style="Flat.TButton",
                                       command=self._clear)
        self.clear_button.pack(side="left")
        if is_files:
            ttk.Label(tools, text="Motor", style="Muted.TLabel").pack(
                side="right", padx=(6, 6))
            engines = [item for item in core.available_engines() if item != "pkg"]
            self.engine_box = ttk.Combobox(tools, textvariable=self.engine_var,
                                           values=engines, state="readonly", width=24)
            self.engine_box.pack(side="right")
            Tooltip(self.engine_box,
                    "Auto escolhe o motor ideal. Você também pode forçar 7-Zip, "
                    "unzip, unrar ou bsdtar.")
        else:
            ttk.Label(tools, text="Motor: PKG nativo",
                      style="Muted.TLabel").pack(side="right")

        list_card, list_inner = card(self.frame, padding=(6, 4))
        list_card.pack(fill="both", expand=True)
        ttk.Label(list_inner, text="Fila de extração",
                  style="CardTitle.TLabel").pack(anchor="w", pady=(0, 2))

        tree_frame = ttk.Frame(list_inner)
        tree_frame.pack(fill="both", expand=True)
        self.tree = ttk.Treeview(tree_frame, columns=(
            "status", "name", "kind", "size", "progress"), show="headings", height=2)
        for key, heading, width in (
            ("status", "Status", 100), ("name", "Arquivo", 300),
            ("kind", "Tipo", 65), ("size", "Tamanho", 90),
            ("progress", "Progresso", 80),
        ):
            self.tree.heading(key, text=heading)
            self.tree.column(key, width=width, anchor="w")
        self.scrollbar = ttk.Scrollbar(tree_frame, orient="vertical",
                                       command=self.tree.yview)
        self.tree.configure(yscrollcommand=self.scrollbar.set)
        self.tree.tag_configure("ok", foreground=SUCCESS)
        self.tree.tag_configure("erro", foreground=ERROR)
        self.tree.tag_configure("ativo", foreground=WARN)
        self.tree.tag_configure("invalido", foreground=MUTED)

        self.empty_label = tk.Label(
            tree_frame,
            text="A fila está vazia\nArraste arquivos para cá ou clique em Escolher arquivos",
            bg=CARD, fg=MUTED, font=(FONT, 10), justify="center")
        self.empty_label.bind("<Button-1>", lambda event: self._choose_files())

        progress_frame = ttk.Frame(list_inner)
        progress_frame.pack(fill="x", pady=(2, 0))
        self.progress = ttk.Progressbar(progress_frame, mode="determinate", maximum=100)
        self.progress.pack(side="left", fill="x", expand=True)
        self.progress_label = ttk.Label(progress_frame, text="0%", width=6, anchor="e")
        self.progress_label.pack(side="left", padx=(10, 0))
        self.summary_label = ttk.Label(list_inner, text="Pronto para extrair.",
                                       style="Muted.TLabel")
        self.summary_label.pack(anchor="w", pady=(2, 0))

        log_card, log_inner = card(self.frame, padding=(6, 4))
        log_card.pack(fill="both", expand=True, pady=(2, 0))
        ttk.Label(log_inner, text="Log", style="CardTitle.TLabel").pack(
            anchor="w", pady=(0, 2))
        log_frame = ttk.Frame(log_inner)
        log_frame.pack(fill="both", expand=True)
        self.log_text = tk.Text(log_frame, height=3, bg=LOG_BG, fg=TEXT,
                                font=("Monospace", 9), wrap="word", relief="flat",
                                highlightthickness=1, highlightbackground=BORDER)
        log_scroll = ttk.Scrollbar(log_frame, orient="vertical",
                                   command=self.log_text.yview)
        self.log_text.configure(yscrollcommand=log_scroll.set, state="disabled")
        self.log_text.pack(side="left", fill="both", expand=True)
        log_scroll.pack(side="right", fill="y")

        self.cancel_button = ttk.Button(actions, text="Cancelar",
                                        command=self._cancel, state="disabled")
        self.cancel_button.pack(side="right", padx=6)
        self.pause_button = ttk.Button(actions, text="Pausar",
                                       command=self._pause, state="disabled")
        self.pause_button.pack(side="right", padx=6)
        self.extract_button = ttk.Button(actions, text="Extrair",
                                         style="Accent.TButton",
                                         command=self._start_extraction)
        self.extract_button.pack(side="right")

    def ui(self, function):
        self.app.ui(function)

    def _highlight(self, active):
        self.drop.configure(highlightbackground=ACCENT if active else BORDER)
        self.drop_label.configure(bg=ACCENT if active else CARD2,
                                  fg="#ffffff" if active else TEXT)

    def _on_drop(self, event):
        paths = self.app.root.tk.splitlist(event.data)
        self._highlight(False)
        self._add_paths(paths)

    def _choose_files(self):
        initial = str(Path(self.app.output_var.get()).expanduser())
        if not Path(initial).is_dir():
            initial = str(Path.home())
        if shutil.which("kdialog"):
            if self.mode == "files":
                filter_text = (
                    "*.zip *.zipx *.rar *.7z *.iso *.img *.tar *.gz *.tgz "
                    "*.bz2 *.xz *.cab|Arquivos suportados\n*.*|Todos os arquivos")
            else:
                filter_text = "*.pkg|Arquivos PKG\n*.*|Todos os arquivos"
            try:
                result = subprocess.run(
                    ["kdialog", "--multiple", "--separate-output",
                     "--getopenfilename", initial, filter_text],
                    capture_output=True, text=True)
            except OSError:
                result = None
            if result is not None and result.returncode == 0:
                paths = [line.strip() for line in result.stdout.splitlines()
                         if line.strip()]
                if paths:
                    self._add_paths(paths)
                return
        if self.mode == "files":
            filetypes = [
                ("Arquivos e imagens",
                 "*.zip *.zipx *.rar *.7z *.iso *.img *.tar *.gz *.tgz *.bz2 *.xz *.cab"),
                ("Todos os arquivos", "*.*"),
            ]
            title = "Escolha os arquivos para extrair"
        else:
            filetypes = [("Arquivos PKG", "*.pkg"), ("Todos os arquivos", "*.*")]
            title = "Escolha os arquivos PKG"
        paths = filedialog.askopenfilenames(title=title, filetypes=filetypes)
        if paths:
            self._add_paths(paths)

    def _add_paths(self, paths):
        jobs = []
        for raw in paths:
            path = Path(str(raw).strip("{}"))
            if path.is_dir():
                if self.mode == "files":
                    candidates = sorted(p for p in path.rglob("*")
                                        if core.is_supported(p))
                else:
                    candidates = sorted(p for p in path.rglob("*")
                                        if p.suffix.lower() == ".pkg")
                if not candidates:
                    self._log(f"Pasta sem arquivos aceitos: {path}")
            elif path.is_file():
                candidates = [path]
            else:
                self._log(f"Caminho inexistente: {path}")
                continue
            # Evita duplicar arquivos com o mesmo nome sem perguntar ao usuario.
            to_add = []
            duplicates = []
            for candidate in candidates:
                if any(job["path"] == candidate
                       or job["path"].name == candidate.name
                       for job in self.jobs):
                    duplicates.append(candidate)
                else:
                    to_add.append(candidate)
            if duplicates:
                names = sorted({candidate.name for candidate in duplicates})
                sample = ", ".join(names[:5])
                extra = f" e mais {len(names) - 5}" if len(names) > 5 else ""
                if messagebox.askyesno(
                        "Arquivo duplicado",
                        f"Já existe na fila arquivo com o mesmo nome: "
                        f"{sample}{extra}.\nAdicionar mesmo assim?"):
                    to_add.extend(duplicates)
            for candidate in to_add:
                jobs.append(self._add_job(candidate))
        if not jobs:
            return
        # Le as opcoes na thread da interface; a verificacao roda fora dela.
        settings = (
            self.engine_var.get(),
            self.app.password_var.get() if self.mode == "files" else None,
            self.app.fast_var.get(),
        )
        self.probing += 1
        self._sync_buttons()
        self._refresh_tree()
        threading.Thread(target=self._probe_jobs, args=(jobs, settings),
                         daemon=True).start()

    def _add_job(self, path):
        self.counter += 1
        job = {
            "id": str(self.counter), "path": path, "kind": core.kind_for(path),
            "title": "", "size": path.stat().st_size, "status": "Verificando...",
            "current": 0, "percent": 0, "files_done": 0, "files_total": 0,
            "bytes_done": 0, "bytes_total": 0, "bytes": 0,
            "entries": 0, "engine": "", "result": None, "error": None,
        }
        self.jobs.append(job)
        return job

    def _probe_jobs(self, jobs, settings):
        engine, password, fast = settings
        for job in jobs:
            try:
                info = core.probe(job["path"], engine=engine,
                                  password=password, fast=fast)
            except (ExtractionError, OSError) as error:
                message = str(error)

                def invalid(j=job, m=message):
                    if j not in self.jobs:
                        return
                    j["status"] = "Inválido"
                    j["error"] = m
                    self._set_status(j, "Inválido")
                    self._log(f"{j['path'].name}: {m}")
                self.ui(invalid)
            except Exception as error:  # verificação inesperada nao mata a fila
                self.app.log_error(
                    f"Falha ao verificar {job['path']}:\n{traceback.format_exc()}")
                message = str(error)

                def invalid(j=job, m=message):
                    if j not in self.jobs:
                        return
                    j["status"] = "Inválido"
                    j["error"] = m
                    self._set_status(j, "Inválido")
                    self._log(f"{j['path'].name}: erro ao verificar: {m}")
                self.ui(invalid)
            else:
                def apply(j=job, i=info):
                    if j not in self.jobs:
                        return
                    total_bytes = i.get("bytes", 0)
                    j.update({
                        "kind": i["kind"], "title": i["title"],
                        "entries": i["entries"], "engine": i["engine"],
                        "bytes": total_bytes, "bytes_total": total_bytes,
                        "status": "Aguardando",
                    })
                    self._update_row(j)
                self.ui(apply)
        self.ui(self._probe_done)

    def _probe_done(self):
        self.probing = max(0, self.probing - 1)
        self._sync_buttons()

    def _sync_buttons(self):
        state = "disabled" if (self.running or self.probing > 0) else "normal"
        self.extract_button.configure(state=state)
        self.remove_button.configure(state=state)
        self.clear_button.configure(state=state)
        if hasattr(self, "pause_button"):
            self.pause_button.configure(
                state="normal" if self.running else "disabled")

    def _refresh_tree(self):
        self.tree.delete(*self.tree.get_children())
        if not self.jobs:
            self.tree.pack_forget()
            self.scrollbar.pack_forget()
            self.empty_label.pack(expand=True, fill="both")
            return
        self.empty_label.pack_forget()
        self.tree.pack(side="left", fill="both", expand=True)
        self.scrollbar.pack(side="right", fill="y")
        for job in self.jobs:
            self.tree.insert("", "end", iid=job["id"], values=self._values(job),
                             tags=(self._tag(job["status"]),))

    @staticmethod
    def _values(job):
        return (job["status"], job["title"] or job["path"].name, job["kind"],
                human_size(job["size"]), f"{min(100, job['percent'])}%")

    @staticmethod
    def _tag(status):
        if status == "Concluído":
            return "ok"
        if status in ("Erro", "Inválido"):
            return "erro" if status == "Erro" else "invalido"
        if status in ("Extraindo", "Verificando..."):
            return "ativo"
        return ()

    def _update_row(self, job):
        try:
            self.tree.item(job["id"], values=self._values(job),
                           tags=(self._tag(job["status"]),))
        except tk.TclError:
            pass

    def _set_status(self, job, status):
        job["status"] = status
        self._update_row(job)

    def _log(self, message):
        try:
            self.log_text.configure(state="normal")
            self.log_text.insert("end", f"{message}\n")
            lines = int(self.log_text.index("end-1l").split(".")[0])
            if lines > 600:
                self.log_text.delete("1.0", f"{lines - 300}.0")
            self.log_text.see("end")
            self.log_text.configure(state="disabled")
        except tk.TclError:
            pass

    # ---------- progresso detalhado ----------

    @staticmethod
    def _duration(seconds):
        seconds = max(0, int(seconds))
        hours, rest = divmod(seconds, 3600)
        minutes, secs = divmod(rest, 60)
        if hours:
            return f"{hours:02d}:{minutes:02d}:{secs:02d}"
        return f"{minutes:02d}:{secs:02d}"

    def _cancel_eta(self):
        if self._eta_after is not None:
            try:
                self.app.root.after_cancel(self._eta_after)
            except (tk.TclError, ValueError):
                pass
            self._eta_after = None

    def _stop_eta(self):
        self._cancel_eta()
        self.current_job = None

    def _begin_tracking(self, job):
        self._cancel_eta()
        self.current_job = job
        self.job_started = time.monotonic()
        self._eta_after = self.app.root.after(1000, self._tick_eta)

    def _tick_eta(self):
        self._eta_after = None
        if self.running and self.current_job is not None:
            self._update_summary(self.current_job)
            self._eta_after = self.app.root.after(1000, self._tick_eta)

    def _update_summary(self, job):
        percent = max(0, min(100, int(job.get("percent", 0) or 0)))
        files_done = int(job.get("files_done", 0) or 0)
        files_total = int(job.get("files_total", 0) or job.get("entries", 0) or 0)
        bytes_done = int(job.get("bytes_done", 0) or 0)
        bytes_total = int(job.get("bytes_total", 0) or job.get("bytes", 0) or 0)

        files_text = f"{files_done}/{files_total} arquivos" if files_total else "arquivos —"
        size_text = (f"{human_size(bytes_done)} / {human_size(bytes_total)}"
                     if bytes_total else "tamanho —")
        status = job.get("status", "")
        if status == "Concluído":
            eta_text = "concluído"
        elif status == "Cancelado":
            eta_text = "cancelado"
        elif status in ("Erro", "Inválido"):
            eta_text = "interrompido"
        elif percent <= 0:
            eta_text = "calculando..."
        else:
            elapsed = max(0.0, time.monotonic() - self.job_started)
            remaining = elapsed * (100 - percent) / max(percent, 1)
            eta_text = f"restam {self._duration(remaining)}"
        self.summary_label.configure(text=f"{files_text} · {size_text} · {eta_text}")

    def _clear(self):
        if self.running or self.probing:
            return
        self._stop_eta()
        self.jobs.clear()
        self._refresh_tree()
        self.progress.configure(value=0)
        self.progress_label.configure(text="0%")
        self.summary_label.configure(text="Pronto para extrair.")

    def _remove_selected(self):
        if self.running or self.probing:
            return
        selected = set(self.tree.selection())
        if not selected:
            return
        remaining = [job for job in self.jobs if job["id"] not in selected]
        if len(remaining) != len(self.jobs):
            self.jobs = remaining
            self._refresh_tree()

    # ---------- extração ----------

    def _start_extraction(self):
        if self.running:
            return
        if self.probing:
            self._log("Aguarde a verificação dos arquivos.")
            return
        pending = [job for job in self.jobs if job["status"] == "Aguardando"]
        if not pending:
            self._log("Adicione pelo menos um arquivo pendente na fila.")
            return
        output = Path(self.app.output_var.get()).expanduser()
        engine = self.engine_var.get()
        if engine not in core.available_engines():
            self._log(f"Motor indisponível: {engine}")
            return
        try:
            output.mkdir(parents=True, exist_ok=True)
            core.ensure_writable(output)
        except (OSError, ExtractionError) as error:
            self._log(f"Destino inválido: {error}")
            return
        self.running = True
        self.cancel_event = threading.Event()
        self.pause_event = threading.Event()
        self.pause_event.set()
        self._sync_buttons()
        self.cancel_button.configure(state="normal")
        self.pause_button.configure(state="normal", text="Pausar")
        args = (output, engine, self.app.password_var.get(), self.app.fast_var.get(),
                self.app.verify_var.get())
        threading.Thread(target=self._worker, args=args, daemon=True).start()

    def _worker(self, output, engine, password, fast, verify=False):
        if self.mode == "pkg":
            def _tracer(frame, event, arg):
                while not self.pause_event.is_set():
                    time.sleep(0.1)
                    if self.cancel_event.is_set():
                        raise ExtractionCancelled("Extracao cancelada.")
                return _tracer
            sys.settrace(_tracer)
        for job in list(self.jobs):
            if self.cancel_event.is_set():
                break
            if job["status"] != "Aguardando":
                continue
            job["percent"] = 0
            job["current"] = 0
            job["files_done"] = 0
            job["files_total"] = int(job.get("entries", 0) or 0)
            job["bytes_done"] = 0
            job["bytes_total"] = int(job.get("bytes", 0) or 0)

            def start(j=job):
                self._begin_tracking(j)
                self._set_status(j, "Extraindo")
                self.progress.configure(value=0)
                self.progress_label.configure(text="0%")
                self._update_summary(j)
                self._log(f"Extraindo: {j['path'].name}")

            def progress(info=None, total=None, j=job):
                if isinstance(info, dict):
                    data = info
                else:
                    current = info or 0
                    denom = total or 100
                    data = {"percent": int(current * 100 / denom)}

                def update():
                    try:
                        percent = int(data.get("percent", 0) or 0)
                    except (TypeError, ValueError):
                        percent = 0
                    percent = max(0, min(100, percent))
                    j["percent"] = percent
                    j["current"] = percent
                    for key in ("files_done", "files_total", "bytes_done", "bytes_total"):
                        try:
                            j[key] = int(data.get(key, 0) or 0)
                        except (TypeError, ValueError):
                            j[key] = 0
                    self._update_row(j)
                    self.progress.configure(value=percent)
                    self.progress_label.configure(text=f"{percent}%")
                    self._update_summary(j)

                self.ui(update)

            def log(message):
                self.ui(lambda: self._log(message))

            self.ui(start)
            resume = bool(job.get("resume"))
            resume_target = job.get("resume_target")
            target = Path(resume_target) if resume_target else None
            if not resume:
                try:
                    target = core.reserve_target(output, job["path"])
                except Exception as error:
                    target = None
            if target is not None:
                job["resume_target"] = str(target)
            self._save_checkpoint(job, output, target, engine,
                                  password if self.mode == "files" else None,
                                  fast, verify)
            if verify:
                self.ui(lambda: self._log(
                    f"Verificando integridade: {job['path'].name}"))
                self.ui(lambda: self._set_status(job, "Verificando"))
                try:
                    core.verify(
                        job["path"], engine=engine,
                        password=password if self.mode == "files" else None)
                except Exception as error:
                    self._clear_checkpoint()
                    message = str(error)

                    def failed_verify(j=job, m=message):
                        j["error"] = m
                        self._set_status(j, "Erro")
                        self._update_summary(j)
                        self._stop_eta()
                        self._log(f"ERRO: {m}")
                    self.ui(failed_verify)
                    continue
            try:
                result = core.extract(
                    job["path"], output,
                    engine=engine,
                    password=password if self.mode == "files" else None,
                    fast=fast, entries=job["entries"],
                    bytes_total=job["bytes_total"],
                    progress=progress, log=log, cancel=self.cancel_event,
                    resume=resume, target=target, pause=self.pause_event,
                )

                def done(j=job, r=result):
                    j["result"] = r
                    j["percent"] = 100
                    self._clear_checkpoint()
                    j["current"] = 100
                    j["files_done"] = j["files_done"] or j["files_total"] or j["entries"]
                    j["bytes_done"] = j["bytes_done"] or j["bytes_total"] or j["bytes"]
                    self.progress.configure(value=100)
                    self.progress_label.configure(text="100%")
                    self._set_status(j, "Concluído")
                    self._update_summary(j)
                    self._stop_eta()
                    self._log(f"Concluído: {r}")
                self.ui(done)
            except ExtractionCancelled:
                def cancelled(j=job):
                    self._set_status(j, "Cancelado")
                    self._update_summary(j)
                    self._stop_eta()
                    self._log("Extração cancelada.")
                self.ui(cancelled)
                break
            except ExtractionError as error:
                self._clear_checkpoint()
                message = str(error)

                def failed(j=job, m=message):
                    j["error"] = m
                    self._set_status(j, "Erro")
                    self._update_summary(j)
                    self._stop_eta()
                    self._log(f"ERRO: {m}")
                    if any(word in m.lower() for word in
                           ("password", "encrypted", "wrong", "senha")):
                        self._log("Dica: informe a senha em Opções e tente novamente.")
                self.ui(failed)
            except Exception as error:
                self._clear_checkpoint()
                self.app.log_error(
                    f"Erro inesperado ao extrair {job['path']}:\n{traceback.format_exc()}")
                def failed_unexpected(j=job, m=str(error)):
                    j["error"] = m
                    self._set_status(j, "Erro")
                    self._update_summary(j)
                    self._stop_eta()
                    self._log(f"ERRO inesperado: {m}")
                self.ui(failed_unexpected)

        if self.mode == "pkg":
            sys.settrace(None)
        def finish():
            self.running = False
            self._stop_eta()
            self._sync_buttons()
            self.cancel_button.configure(state="disabled")
            if hasattr(self, "pause_button"):
                self.pause_button.configure(state="disabled")
            self._log("Fila finalizada.")
        self.ui(finish)

    def _pause(self):
        if not self.running:
            return
        if self.pause_event.is_set():
            self.pause_event.clear()
            if hasattr(self, "pause_button"):
                self.pause_button.configure(text="Continuar")
            self._log("Pausando...")
            if self.current_job is not None:
                self.summary_label.configure(text="Pausado")
        else:
            self.pause_event.set()
            if hasattr(self, "pause_button"):
                self.pause_button.configure(text="Pausar")
            self._log("Retomando...")
            if self.current_job is not None:
                self._update_summary(self.current_job)

    def _save_checkpoint(self, job, output, target, engine, password, fast,
                         verify=False):
        try:
            CHECKPOINT_FILE.parent.mkdir(parents=True, exist_ok=True)
            data = [{
                "source": str(job["path"]),
                "output": str(output),
                "target": str(target) if target is not None else None,
                "engine": engine,
                "password": password,
                "fast": bool(fast),
                "verify": bool(verify),
                "mode": self.mode,
                "entries": int(job.get("entries", 0) or 0),
                "bytes": int(job.get("bytes", 0) or 0),
            }]
            with CHECKPOINT_FILE.open("w", encoding="utf-8") as handle:
                json.dump(data, handle)
        except OSError:
            pass

    def _clear_checkpoint(self):
        try:
            CHECKPOINT_FILE.unlink()
        except FileNotFoundError:
            pass
        except OSError:
            pass

    def _cancel(self):
        if self.running:
            self.cancel_event.set()
            self.cancel_button.configure(state="disabled")
            self._log("Cancelando...")
            if self.current_job is not None:
                self.summary_label.configure(text="Cancelando...")


class App:
    def __init__(self):
        base = TkinterDnD.Tk if TkinterDnD is not None else tk.Tk
        # Tk nao expoe wm_class(): WM_CLASS e definido ao criar a janela.
        self.root = base(className="extrair-tudo")
        self.root.title("Extrair Tudo")
        self.root.geometry("880x700")
        self.root.minsize(760, 620)
        self.root.configure(bg=BG)
        self.root.protocol("WM_DELETE_WINDOW", self._on_close)

        self.dnd = DND_FILES is not None
        self.output_var = tk.StringVar(value=str(default_output_dir()))
        self.password_var = tk.StringVar()
        self.fast_var = tk.BooleanVar(value=False)
        self.verify_var = tk.BooleanVar(value=False)

        self._style()
        self._build()
        self.root.after(50, self._safe_free_space)
        self.root.after(300, self._check_checkpoint)

    def _safe_free_space(self):
        """Protege a consulta de disco se a janela já tiver sido fechada."""
        try:
            self._update_free_space()
        except (tk.TclError, RuntimeError, OSError):
            pass

    def _clear_checkpoint_global(self):
        try:
            CHECKPOINT_FILE.unlink()
        except FileNotFoundError:
            pass
        except OSError:
            pass

    def _check_checkpoint(self):
        if not CHECKPOINT_FILE.exists():
            return
        try:
            with CHECKPOINT_FILE.open(encoding="utf-8") as handle:
                data = json.load(handle)
        except (OSError, json.JSONDecodeError):
            self._clear_checkpoint_global()
            return
        if not isinstance(data, list) or not data:
            self._clear_checkpoint_global()
            return
        item = data[0]
        source = Path(item.get("source", ""))
        target_raw = item.get("target")
        target = Path(target_raw) if target_raw else None
        output = Path(item.get("output", ""))
        if not source.is_file() or target is None or not target.exists():
            self._clear_checkpoint_global()
            return
        if not messagebox.askyesno(
                "Retomar extração",
                f"Extração interrompida encontrada:\n{source.name}\n"
                "Retomar de onde parou?"):
            self._clear_checkpoint_global()
            return
        mode = item.get("mode", "files")
        tab = self.tabs[0] if mode == "files" else self.tabs[1]
        job = tab._add_job(source)
        job["resume"] = True
        job["resume_target"] = str(target)
        job["engine"] = item.get("engine", tab.engine_var.get())
        job["entries"] = int(item.get("entries", 0) or 0)
        job["bytes"] = int(item.get("bytes", 0) or 0)
        job["bytes_total"] = job["bytes"]
        job["status"] = "Aguardando"
        tab._update_row(job)
        self.output_var.set(str(output))
        tab.engine_var.set(item.get("engine", tab.engine_var.get()))
        password = item.get("password")
        if password:
            self.password_var.set(password)
        self.fast_var.set(bool(item.get("fast", False)))
        self.verify_var.set(bool(item.get("verify", False)))
        tab.running = True
        tab.cancel_event = threading.Event()
        tab.pause_event = threading.Event()
        tab.pause_event.set()
        tab._sync_buttons()
        tab.cancel_button.configure(state="normal")
        if hasattr(tab, "pause_button"):
            tab.pause_button.configure(state="normal", text="Pausar")
        args = (output, tab.engine_var.get(), self.password_var.get(),
                self.fast_var.get(), self.verify_var.get())
        threading.Thread(target=tab._worker, args=args, daemon=True).start()

    # ---------- interface ----------

    def _style(self):
        style = ttk.Style()
        style.theme_use("clam")
        style.configure(".", background=BG, foreground=TEXT, fieldbackground=CARD,
                        font=(FONT, 10))
        style.configure("TFrame", background=BG)
        style.configure("Card.TFrame", background=CARD)
        style.configure("TLabel", background=BG, foreground=TEXT)
        style.configure("Muted.TLabel", background=BG, foreground=MUTED,
                        font=(FONT, 9))
        style.configure("CardTitle.TLabel", background=CARD, foreground=TEXT,
                        font=(FONT, 10, "bold"))
        style.configure("HeaderTitle.TLabel", background=HEADER, foreground=TEXT,
                        font=(FONT, 14, "bold"))
        style.configure("HeaderSub.TLabel", background=HEADER, foreground=MUTED,
                        font=(FONT, 9))
        for name, pad in (("TButton", (10, 6)), ("Flat.TButton", (9, 5)),
                          ("Header.TButton", (9, 5))):
            style.configure(name, background=CARD, foreground=TEXT,
                            bordercolor=BORDER, lightcolor=BORDER,
                            darkcolor=BORDER, padding=pad, borderwidth=1,
                            font=(FONT, 10), relief="flat")
            style.map(name, background=[("active", ACCENT_SOFT),
                                        ("disabled", CARD2)],
                      foreground=[("disabled", MUTED)])
        style.configure("Accent.TButton", background=ACCENT, foreground="#ffffff",
                        bordercolor=ACCENT, lightcolor=ACCENT, darkcolor=ACCENT,
                        padding=(14, 7), borderwidth=0, font=(FONT, 10),
                        relief="flat")
        style.map("Accent.TButton", background=[("active", ACCENT_HOVER),
                                                ("disabled", "#c8cde0")],
                  foreground=[("disabled", "#f7f9fc")])
        style.configure("TEntry", fieldbackground=CARD, foreground=TEXT,
                        insertcolor=TEXT, bordercolor=BORDER, padding=6,
                        borderwidth=1)
        style.configure("TCombobox", fieldbackground=CARD, foreground=TEXT,
                        bordercolor=BORDER, insertcolor=TEXT, padding=4)
        style.configure("TCheckbutton", background=BG, foreground=TEXT,
                        selectcolor=ACCENT, font=(FONT, 10))
        style.map("TCheckbutton", background=[("active", BG)])
        style.configure("Treeview", background=CARD, fieldbackground=CARD,
                        foreground=TEXT, rowheight=26, borderwidth=0)
        style.configure("Treeview.Heading", background=CARD2, foreground=MUTED,
                        font=(FONT, 9), padding=(6, 4), borderwidth=0)
        style.map("Treeview", background=[("selected", ACCENT_SOFT)],
                  foreground=[("selected", TEXT)])
        style.configure("TProgressbar", background=ACCENT, troughcolor="#e6e9f0",
                        bordercolor=BORDER, borderwidth=0, thickness=6)
        style.configure("TNotebook", background=BG, borderwidth=0,
                        tabmargins=(0, 4, 0, 0))
        style.configure("TNotebook.Tab", background=CARD2, foreground=MUTED,
                        bordercolor=BORDER, padding=(12, 6), borderwidth=1,
                        font=(FONT, 10))
        style.map("TNotebook.Tab", background=[("selected", ACCENT_SOFT),
                                               ("active", ACCENT_SOFT)],
                  foreground=[("selected", ACCENT), ("active", ACCENT)])
        style.configure("TScrollbar", background="#d3d8e2", troughcolor=CARD2,
                        bordercolor=BORDER, arrowcolor=MUTED, borderwidth=0)

    def _build(self):
        main = ttk.Frame(self.root)
        main.pack(fill="both", expand=True)

        header = tk.Frame(main, bg=HEADER, height=52)
        header.pack(fill="x")
        header.pack_propagate(False)
        tk.Frame(main, bg=ACCENT, height=2).pack(fill="x")
        left = tk.Frame(header, bg=HEADER)
        left.pack(side="left", padx=(16, 0))
        tk.Label(left, text="Extrair Tudo", bg=HEADER, fg=TEXT,
                 font=(FONT, 14, "bold")).pack(anchor="w")
        tk.Label(left, text="Arquivos, imagens e jogos PKG",
                 bg=HEADER, fg=MUTED, font=(FONT, 9)).pack(anchor="w")
        ttk.Button(header, text="Abrir destino", style="Header.TButton",
                   command=self._open_output).pack(side="right", padx=(6, 16))

        content = ttk.Frame(main, padding=(16, 10, 16, 10))
        content.pack(fill="both", expand=True)

        dest_card, dest_inner = card(content)
        dest_card.pack(fill="x")
        dest_row = ttk.Frame(dest_inner, style="Card.TFrame")
        dest_row.pack(fill="x")
        ttk.Label(dest_row, text="Salvar em",
                  style="CardTitle.TLabel").pack(side="left", padx=(0, 8))
        self.output_entry = ttk.Entry(dest_row, textvariable=self.output_var)
        self.output_entry.pack(side="left", fill="x", expand=True)
        self.output_entry.bind("<FocusOut>", lambda event: self._update_free_space())
        self.output_entry.bind("<Return>", lambda event: self._update_free_space())
        choose = ttk.Button(dest_row, text="Escolher", command=self._choose_output)
        choose.pack(side="left", padx=(6, 0))
        Tooltip(choose, "Selecionar outra pasta de destino.")

        folder_row = ttk.Frame(dest_inner, style="Card.TFrame")
        folder_row.pack(fill="x", pady=(4, 0))
        ttk.Label(folder_row, text="Nova pasta:", style="Muted.TLabel").pack(
            side="left")
        self.new_folder_var = tk.StringVar()
        folder_entry = ttk.Entry(folder_row, textvariable=self.new_folder_var,
                                 width=22)
        folder_entry.pack(side="left", padx=(6, 0))
        folder_entry.bind("<Return>", lambda event: self._create_folder())
        ttk.Button(folder_row, text="Criar", style="Flat.TButton",
                   command=self._create_folder).pack(side="left", padx=(6, 0))
        self.free_label = ttk.Label(folder_row, style="Muted.TLabel", text="")
        self.free_label.pack(side="right")

        options_card, options_inner = card(content)
        options_card.pack(fill="x", pady=(4, 0))
        options = ttk.Frame(options_inner, style="Card.TFrame")
        options.pack(fill="x")
        ttk.Label(options, text="Opções", style="CardTitle.TLabel").pack(
            side="left", padx=(0, 10))
        ttk.Label(options, text="Senha", style="Muted.TLabel").pack(side="left")
        self.password_entry = ttk.Entry(options, textvariable=self.password_var,
                                        show="*", width=20)
        self.password_entry.pack(side="left", padx=(8, 12))
        Tooltip(self.password_entry,
                "Preencha apenas se o RAR/ZIP estiver protegido por senha.")
        ttk.Checkbutton(options, text="Ultra rápida",
                        variable=self.fast_var).pack(side="left")
        ttk.Label(options, text="sem log detalhado e com multithread",
                  style="Muted.TLabel").pack(side="left", padx=(8, 0))
        ttk.Checkbutton(options, text="Verificar integridade",
                        variable=self.verify_var).pack(side="left", padx=(12, 0))
        ttk.Label(options, text="testa antes de extrair",
                  style="Muted.TLabel").pack(side="left", padx=(8, 0))

        notebook = ttk.Notebook(content)
        notebook.pack(fill="both", expand=True, pady=(4, 0))
        self.tabs = [
            QueueTab(self, notebook, "files"),
            QueueTab(self, notebook, "pkg"),
        ]
        notebook.add(self.tabs[0].frame, text="  Arquivos  ")
        notebook.add(self.tabs[1].frame, text="  Jogos PKG  ")

        motors = ", ".join(core.available_engines())
        ttk.Label(content, text=f"Motores disponíveis: {motors}",
                  style="Muted.TLabel").pack(anchor="w", pady=(2, 0))

    def ui(self, function):
        try:
            self.root.after(0, function)
        except (tk.TclError, RuntimeError):
            pass

    def log_error(self, mensagem):
        """Registra falhas inesperadas em erros.log, com timestamp."""
        try:
            stamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            LOG_FILE.parent.mkdir(parents=True, exist_ok=True)
            with LOG_FILE.open("a", encoding="utf-8") as handle:
                handle.write(f"[{stamp}] {mensagem}\n")
        except OSError:
            pass

    def _choose_output(self):
        initial = str(Path(self.output_var.get()).expanduser())
        if not Path(initial).is_dir():
            initial = str(Path.home())
        if shutil.which("kdialog"):
            try:
                result = subprocess.run(
                    ["kdialog", "--getexistingdirectory", initial],
                    capture_output=True, text=True)
            except OSError:
                result = None
            if result is not None and result.returncode == 0:
                chosen = result.stdout.strip()
                if chosen:
                    self.output_var.set(chosen)
                    self._update_free_space()
                    return
        chosen = filedialog.askdirectory(
            title="Escolha a pasta de destino",
            initialdir=initial,
            mustexist=False,
        )
        if chosen:
            self.output_var.set(chosen)
            self._update_free_space()

    def _create_folder(self):
        name = self.new_folder_var.get().strip()
        if not name:
            for tab in self.tabs:
                tab._log("Digite o nome da nova pasta em 'Nova pasta'.")
            return
        safe = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", name).strip(" .")[:100]
        if not safe:
            for tab in self.tabs:
                tab._log(f"Nome de pasta inválido: {name!r}")
            return
        output = Path(self.output_var.get()).expanduser()
        target = output / safe
        if target.exists():
            for tab in self.tabs:
                tab._log(f"A pasta já existe: {target}")
        else:
            try:
                target.mkdir(parents=True)
            except OSError as error:
                for tab in self.tabs:
                    tab._log(f"Não foi possível criar a pasta: {error}")
                return
        self.new_folder_var.set("")
        self.output_var.set(str(target))
        self._update_free_space()
        for tab in self.tabs:
            tab._log(f"Destino alterado para: {target}")

    def _open_output(self):
        output = Path(self.output_var.get()).expanduser()
        try:
            output.mkdir(parents=True, exist_ok=True)
            if sys.platform == "darwin":
                opener = ["open", str(output)]
            elif sys.platform == "win32":
                opener = ["explorer", str(output)]
            else:
                opener = ["xdg-open", str(output)]
            subprocess.Popen(opener)
        except OSError as error:
            for tab in self.tabs:
                tab._log(f"Não foi possível abrir a pasta: {error}")

    def _update_free_space(self):
        try:
            path = Path(self.output_var.get()).expanduser()
            self.free_label.configure(
                text=f"Espaço livre: {human_size(core.free_space(path))}")
        except OSError as error:
            self.free_label.configure(text=f"Destino inválido: {error}")

    def _on_close(self):
        if any(tab.running for tab in self.tabs):
            for tab in self.tabs:
                tab.cancel_event.set()
        for tab in self.tabs:
            tab._stop_eta()
        self.root.destroy()

    def run(self):
        self.root.mainloop()


if __name__ == "__main__":
    try:
        App().run()
    except Exception:
        stamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        try:
            LOG_FILE.parent.mkdir(parents=True, exist_ok=True)
            with LOG_FILE.open("a", encoding="utf-8") as handle:
                handle.write(
                    f"[{stamp}] Falha na inicializacao:\n{traceback.format_exc()}\n")
        except OSError:
            pass
        raise