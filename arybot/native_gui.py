"""Calibration and Groq suggestions for the safe-mode native automation pilot."""
from __future__ import annotations

import queue
import threading
import tkinter as tk
from tkinter import ttk, messagebox

from .dashboard import BLUE, MUTED
from . import native_controls as native
from .groq_service import ask, GroqError


class NativeTab(ttk.Frame):
    def __init__(self, parent, app):
        super().__init__(parent, padding=12)
        self.app = app
        self.labels = {v[2]: k for k, v in native.ROLES.items()}
        self.role = tk.StringVar(value="RFC de búsqueda")
        self.status = tk.StringVar(value="Piloto desactivado. No se modifican operaciones reales.")
        self.summary = tk.StringVar()
        self.pilot = tk.BooleanVar(value=False)
        self.rows = {}
        self.snapshot = None
        self.pending = False
        self.events = queue.Queue()

        ttk.Label(self, text="Motor de automatización", font=("Segoe UI", 19, "bold")).pack(anchor="w")
        ttk.Label(self, text="Polaris • Win32 / UI Automation • Groq", foreground=BLUE).pack(anchor="w", pady=(2, 8))
        ttk.Label(self, text="Calibra controles nativos en modo seguro. La IA solo sugiere candidatos; "
                            "el operador confirma. No se envían valores fiscales ni capturas de pantalla.",
                  foreground=MUTED, wraplength=720).pack(anchor="w", pady=(0, 8))
        bar = ttk.Frame(self)
        bar.pack(fill="x")
        ttk.Checkbutton(bar, text="Activar piloto nativo en MODO SEGURO", variable=self.pilot,
                        command=self._toggle).pack(side="left")
        ttk.Button(bar, text="Pausar cola", command=self._pause).pack(side="right")
        ttk.Label(self, textvariable=self.summary, foreground=BLUE, wraplength=730).pack(anchor="w", pady=6)
        line = ttk.Frame(self)
        line.pack(fill="x")
        select = ttk.Combobox(line, state="readonly", values=list(self.labels), textvariable=self.role, width=21)
        select.pack(side="left")
        select.bind("<<ComboboxSelected>>", self._role_changed)
        ttk.Button(line, text="Inspeccionar", command=self._inspect).pack(side="left", padx=4)
        ttk.Button(line, text="Capturar foco (5 s)", command=self._capture).pack(side="left", padx=4)
        self.groq_button = ttk.Button(line, text="Sugerir con Groq", command=self._groq)
        self.groq_button.pack(side="left", padx=4)

        table = ttk.Frame(self)
        table.pack(fill="both", expand=True, pady=8)
        cols = ("control", "clase", "etiqueta", "id", "estado")
        self.tree = ttk.Treeview(table, columns=cols, show="headings", height=6, selectmode="browse")
        for col, title, width in zip(cols, ("Control", "Clase Win32", "Etiqueta conocida", "ID", "Estado"),
                                     (64, 190, 146, 104, 90)):
            self.tree.heading(col, text=title)
            self.tree.column(col, width=width, minwidth=45)
        scroll = ttk.Scrollbar(table, orient="vertical", command=self.tree.yview)
        self.tree.configure(yscrollcommand=scroll.set)
        self.tree.pack(side="left", fill="both", expand=True)
        scroll.pack(side="right", fill="y")

        actions = ttk.Frame(self)
        actions.pack(fill="x")
        ttk.Button(actions, text="Confirmar control seleccionado", command=self._assign).pack(side="left")
        ttk.Button(actions, text="Quitar asignación", command=self._remove).pack(side="left", padx=6)
        ttk.Label(self, textvariable=self.status, foreground=BLUE, wraplength=740).pack(anchor="w", pady=8)
        ttk.Label(self, text="Para calibrar: pausa la recepción web y la cola, abre manualmente la ventana correspondiente "
                            "de Polaris y usa Capturar foco en un campo. Para botones, selecciona su etiqueta en la tabla. "
                            "RFC y Folio son obligatorios. Pago, CFDI y botones son opcionales. El modo se desactiva al reiniciar.",
                  wraplength=740, foreground=MUTED).pack(anchor="w")
        self._summary()
        self.after(180, self._poll)

    def _role(self):
        return self.labels[self.role.get()]

    def _bot(self):
        bot = self.app.polaris
        if bot is None:
            raise native.NativeError("Polaris no está disponible en este equipo.")
        if self.app.cfg.get("app", {}).get("modo_prueba", True) is not True:
            raise native.NativeError("Activa MODO SEGURO antes de usar el piloto.")
        return bot

    def _paused(self):
        bot = self._bot()
        state = self.app.cola.store.status()
        if state.get("activa") or not state.get("pausada") or not getattr(self.app.cola, "_manual_pause", False):
            raise native.NativeError("Pulsa Pausar cola y comprueba que no haya trabajos ejecutándose.")
        portal = getattr(self.app, "portal", None)
        if portal is not None and getattr(portal, "aceptando", False):
            raise native.NativeError("Pausa primero la recepción en Portal web.")
        return bot

    def _pause(self):
        self.app.cola.pausar()
        self.status.set("Cola pausada después de la operación actual. Pausa también la recepción web.")

    def _summary(self):
        try:
            bot = self.app.polaris
            maps = native.load_map(bot) if bot else {}
            names = ", ".join(native.ROLES[r][2] for r in native.ROLES if r in maps) or "ninguno"
            self.summary.set("Calibrados " + str(len(maps)) + "/6: " + names)
        except native.NativeError as exc:
            self.summary.set(str(exc))

    def _toggle(self):
        try:
            bot = self._paused()
            if self.pending:
                raise native.NativeError("Finaliza la calibración en curso.")
            if self.pilot.get():
                if not native.REQUIRED <= set(native.load_map(bot)):
                    raise native.NativeError("Calibra RFC de búsqueda y Folio antes de activar.")
                bot._native_pilot = True
                self.status.set("Piloto activado. Reanuda la cola solo para una prueba individual, sin timbrar.")
            else:
                bot._native_pilot = False
                self.status.set("Piloto desactivado. Se conserva el mapa local.")
        except native.NativeError as exc:
            self.pilot.set(bool(getattr(self.app.polaris, "_native_pilot", False)))
            self.status.set(str(exc))

    def _clear(self):
        self.tree.delete(*self.tree.get_children())
        self.rows.clear()
        self.snapshot = None

    def _role_changed(self, _event=None):
        self._clear()
        self.status.set("Selecciona Inspeccionar o Capturar foco.")

    def _show_rows(self, root, role, rows):
        self._clear()
        self.snapshot = (root, role)
        for i, row in enumerate(rows, 1):
            key = str(i)
            self.rows[key] = row
            self.tree.insert("", "end", iid=key,
                             values=("C"+key, row["class"], row["label"] or "—",
                                     row["signature"][-1][1], "Habilitado" if row["enabled"] else "Bloqueado"))
        self.status.set(f"{len(rows)} controles candidatos. No se leyó ningún valor de los campos.")

    def _inspect(self):
        try:
            if self.pending:
                raise native.NativeError("Ya hay una calibración en curso.")
            bot = self._paused()
            role = self._role()
            root = native.find_root(bot, role)
            # Enumeración Win32 local. Etiquetas de botones son de lista cerrada.
            self._show_rows(root, role, native.inventory(bot, root, role))
        except (native.NativeError, OSError) as exc:
            self._clear()
            self.status.set(str(exc))

    def _capture(self):
        try:
            if self.pending:
                raise native.NativeError("Ya hay una calibración en curso.")
            bot = self._paused()
            role = self._role()
            if native.ROLES[role][1] == "button":
                raise native.NativeError("Para botones, usa Inspeccionar y selecciona la etiqueta exacta.")
            root = native.find_root(bot, role)
        except (native.NativeError, OSError) as exc:
            self.status.set(str(exc))
            return
        self.pending = True
        self.status.set("Tienes 5 segundos: coloca el cursor dentro del campo correcto en Polaris sin escribir nada.")
        self.after(5000, lambda: self._finish_capture(root, role))

    def _finish_capture(self, root, role):
        self.pending = False
        try:
            bot = self._paused()
            if self._role() != role or native.find_root(bot, role) != root:
                raise native.NativeError("Cambió el formulario o la función; repite la calibración.")
            hwnd = native.focused_candidate(bot, root, role)
            rows = native.inventory(bot, root, role)
            self._show_rows(root, role, rows)
            matches = [k for k, row in self.rows.items() if row["handle"] == hwnd]
            if len(matches) != 1:
                raise native.NativeError("No se identificó un candidato único para el campo enfocado.")
            self.tree.selection_set(matches[0])
            self.tree.see(matches[0])
            self.status.set("Campo enfocado identificado. Regresa a ARY y pulsa Confirmar control seleccionado.")
        except (native.NativeError, OSError) as exc:
            self.status.set(str(exc))

    def _assign(self):
        try:
            bot = self._paused()
            if self.pending:
                raise native.NativeError("Finaliza la calibración en curso.")
            selected = self.tree.selection()
            if not selected or not self.snapshot:
                raise native.NativeError("Selecciona un control del inventario.")
            root, role = self.snapshot
            row = self.rows[selected[0]]
            if self._role() != role:
                raise native.NativeError("Cambió la función. Inspecciona otra vez.")
            # Never trust a Groq candidate or a stale handle without operator review.
            if not messagebox.askyesno("Confirmar identidad",
                "¿Verificaste visualmente que este control es " + native.ROLES[role][2] +
                "? La IA no garantiza la identidad. No se ejecutará ninguna acción ahora.",
                parent=self, default="no"):
                return
            bot = self._paused()
            if native.find_root(bot, role) != root:
                raise native.NativeError("Cambió la ventana. Repite la inspección.")
            fresh = native.unique_match(native.inventory(bot, root, role), row["signature"], enabled=False)
            if fresh["handle"] != row["handle"]:
                raise native.NativeError("Cambió el control. Repite la inspección.")
            native.calibrate(bot, root, role, fresh["handle"])
            self._summary()
            self.status.set("Identidad guardada localmente. No se almacenan HWND ni valores del cliente.")
        except (native.NativeError, OSError) as exc:
            self.status.set(str(exc))

    def _remove(self):
        try:
            bot = self._paused()
            if self.pending or native.requested(bot):
                raise native.NativeError("Desactiva el piloto y termina la calibración antes de quitar asignaciones.")
            native.clear_mapping(bot, self._role())
            self._summary()
            self.status.set("Asignación retirada.")
        except (native.NativeError, OSError) as exc:
            self.status.set(str(exc))

    def _groq(self):
        try:
            bot = self._paused()
            if self.pending:
                raise native.NativeError("Ya hay una consulta o calibración en curso.")
            if not self.snapshot:
                raise native.NativeError("Inspecciona primero el formulario.")
            root, role = self.snapshot
            if role != self._role() or native.find_root(bot, role) != root:
                raise native.NativeError("Cambió el formulario. Inspecciona otra vez.")
            payload, aliases = native.groq_candidates(role, list(self.rows.values()))
        except (native.NativeError, OSError) as exc:
            self.status.set(str(exc))
            return
        self.pending = True
        self.groq_button.configure(state="disabled")
        self.status.set("Groq analiza solo clases, etiquetas conocidas y estado de los controles.")
        snapshot = self.snapshot
        def work():
            try:
                reply = ask("control", payload)
                handle = native.validate_suggestion(reply, aliases)
                self.events.put((snapshot, handle, reply.data["motivo"], None))
            except (GroqError, native.NativeError) as exc:
                self.events.put((snapshot, None, "", str(exc)))
            except Exception:
                self.events.put((snapshot, None, "", "No se pudo consultar Groq."))
        threading.Thread(target=work, daemon=True, name="ARY-Groq-Control").start()

    def _poll(self):
        try:
            snapshot, hwnd, motive, error = self.events.get_nowait()
        except queue.Empty:
            pass
        else:
            self.pending = False
            self.groq_button.configure(state="normal")
            if error:
                self.status.set(error)
            elif snapshot != self.snapshot or snapshot[1] != self._role():
                self.status.set("La sugerencia corresponde a una inspección anterior; no se utiliza.")
            elif hwnd is None:
                self.status.set("Groq no identificó un control inequívoco. Usa Capturar foco.")
            else:
                matches = [k for k, row in self.rows.items() if row["handle"] == hwnd]
                if len(matches) == 1:
                    self.tree.selection_set(matches[0])
                    self.tree.see(matches[0])
                    self.status.set("Sugerencia de Groq: C" + matches[0] + ". Confirma manualmente antes de guardar. " + motive[:240])
                else:
                    self.status.set("El candidato ya no está en la vista. Inspecciona otra vez.")
        self.after(180, self._poll)
