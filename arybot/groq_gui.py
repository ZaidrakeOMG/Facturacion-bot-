"""Pestaña de pruebas Groq. No interactúa con Polaris, la cola ni las impresoras."""
from __future__ import annotations

import queue
import threading
import tkinter as tk
from tkinter import ttk

from .dashboard import BLUE, MUTED, GREEN, RED
from .groq_service import MODEL, GroqError, ask, get_key, save_key

EXAMPLES = {
    "diagnostico": "Polaris: no se encontró el campo Folio en la ventana de facturación. El botón Aceptar aún no se ha presionado.",
    "ticket": "TICKET FICTICIO | FOLIO: 00012345 | REIMPRESIÓN | EJEMPLO SIN VALIDEZ",
}


class GroqTab(ttk.Frame):
    def __init__(self, parent, app):
        super().__init__(parent, padding=14)
        self.app = app
        self.events = queue.Queue()
        self.busy = False
        self.calls = self.success = self.errors = self.tokens = 0
        self.session_key = None
        self.state = tk.StringVar(value="Clave disponible; falta probar conexión." if get_key() else "Sin clave configurada.")
        self.metrics = tk.StringVar(value="Solicitudes: 0  |  Correctas: 0  |  Errores: 0  |  Tokens: 0")
        self.kind = tk.StringVar(value="Diagnóstico de Polaris")
        self.key = tk.StringVar()

        ttk.Label(self, text="ARY Intelligence", font=("Segoe UI", 20, "bold")).pack(anchor="w")
        ttk.Label(self, text="Groq Cloud • Laboratorio de asistencia inteligente", foreground=MUTED).pack(anchor="w", pady=(2, 12))
        config = ttk.LabelFrame(self, text="Conexión segura", padding=10)
        config.pack(fill="x")
        ttk.Label(config, text="Modelo: " + MODEL).pack(anchor="w")
        self.connection_result = ttk.Label(config, textvariable=self.state, foreground=BLUE,
                                           font=("Segoe UI", 11, "bold"), wraplength=620)
        self.connection_result.pack(fill="x", anchor="w", pady=(5, 8))
        row = ttk.Frame(config); row.pack(fill="x")
        ttk.Entry(row, textvariable=self.key, show="*", width=38).pack(side="left", fill="x", expand=True)
        self.save_btn = ttk.Button(row, text="Guardar clave en Windows", command=self._save)
        self.save_btn.pack(side="left", padx=6)
        self.test_btn = ttk.Button(row, text="Probar conexión", command=lambda: self._run("conexion"))
        self.test_btn.pack(side="left")
        ttk.Label(config, text="La clave se oculta al probar. Guarda primero para reutilizarla.",
                  foreground=MUTED, wraplength=620).pack(anchor="w", pady=(5, 0))
        ttk.Label(self, textvariable=self.metrics, foreground=MUTED).pack(anchor="w", pady=8)

        lab = ttk.LabelFrame(self, text="Pruebas manuales — sin timbrado ni impresión", padding=10)
        lab.pack(fill="both", expand=True)
        head = ttk.Frame(lab); head.pack(fill="x")
        cb = ttk.Combobox(head, textvariable=self.kind, values=("Diagnóstico de Polaris", "Lectura de ticket ficticio"), state="readonly", width=25)
        cb.pack(side="left")
        ttk.Button(head, text="Cargar ejemplo ficticio", command=self._example).pack(side="left", padx=6)
        self.run_btn = ttk.Button(head, text="Analizar con Groq", command=self._analyze)
        self.run_btn.pack(side="right")
        ttk.Label(lab, text="Usa únicamente ejemplos ficticios. No incluyas constancias, RFC, nombres, correos, claves ni datos de clientes.",
                  foreground=MUTED, wraplength=730).pack(anchor="w", pady=(7, 4))
        self.input = tk.Text(lab, height=4, wrap="word", font=("Segoe UI", 10))
        self.input.pack(fill="x")
        ttk.Label(lab, text="Resultado de asistencia (no autoriza acciones)", foreground=BLUE).pack(anchor="w", pady=(7, 2))
        self.output = tk.Text(lab, height=7, wrap="word", state="disabled", font=("Segoe UI", 10))
        self.output.pack(fill="both", expand=True)
        self._example()
        self.after(150, self._poll)

    def _report(self, message, *, ok=None, record=True):
        prefix = "CONECTADO: " if ok is True else "ERROR: " if ok is False else ""
        self.state.set(prefix + message)
        self.connection_result.configure(foreground=GREEN if ok is True else RED if ok is False else BLUE)
        if record:
            self.app.log("GROQ | " + prefix + message)

    def _selected(self):
        return "ticket" if self.kind.get() == "Lectura de ticket ficticio" else "diagnostico"

    def _example(self):
        self.input.delete("1.0", "end")
        self.input.insert("1.0", EXAMPLES[self._selected()])

    def _save(self):
        value = self.key.get().strip()
        try:
            save_key(value)
        except GroqError as exc:
            self._report(str(exc), ok=False)
            return
        self.session_key = value
        self.key.set("")
        self._report("Clave guardada en Windows. Pulsa Probar conexión.")

    def _analyze(self):
        self._run(self._selected(), self.input.get("1.0", "end-1c"))

    def _run(self, kind, text=""):
        if self.busy:
            return
        if self.app.cola.ocupada:
            self._report("Polaris está trabajando. Realiza las pruebas cuando termine.", ok=False)
            return
        key = self.key.get().strip() or self.session_key or get_key()
        if not key:
            self._report("Introduce una clave o configura GROQ_API_KEY en Windows.", ok=False)
            return
        self.key.set("")
        self.busy = True
        self.calls += 1
        self._report("Consultando Groq…")
        self.test_btn.configure(text="Probando…")
        for button in (self.run_btn, self.test_btn, self.save_btn):
            button.configure(state="disabled")
        def work():
            try:
                self.events.put((kind, ask(kind, text, api_key=key), None))
            except GroqError as exc:
                self.events.put((kind, None, str(exc)))
            except Exception:
                self.events.put((kind, None, "Error inesperado en la conexión."))
        threading.Thread(target=work, daemon=True, name="ARY-Groq-Prueba").start()

    def _poll(self):
        try:
            kind, reply, error = self.events.get_nowait()
        except queue.Empty:
            pass
        else:
            self.busy = False
            self.test_btn.configure(text="Probar conexión")
            for button in (self.run_btn, self.test_btn, self.save_btn):
                button.configure(state="normal")
            if error:
                self.errors += 1
                self._report(error, ok=False)
            else:
                self.success += 1
                self.tokens += reply.prompt_tokens + reply.completion_tokens
                self._report(f"Groq respondió correctamente • {reply.elapsed_ms} ms", ok=True)
            self.metrics.set(f"Solicitudes: {self.calls}  |  Correctas: {self.success}  |  Errores: {self.errors}  |  Tokens: {self.tokens} (esta sesión)")
            if error:
                result = error
            elif kind == "conexion":
                result = reply.data["estado"]
            elif kind == "ticket":
                result = ("Folio propuesto: " + (reply.data["folio"] or "No identificado") +
                          "\nReimpresión: " + reply.data["reimpresion"] +
                          "\nObservación: " + reply.data["observacion"] +
                          "\nResultado orientativo; no bloquea ni autoriza una impresión.")
            else:
                result = ("Posible causa: " + reply.data["resumen"] +
                          "\nSiguiente comprobación: " + reply.data["siguiente_paso"] +
                          "\nRevisión humana: " + ("Sí" if reply.data["requiere_revision"] else "Recomendada"))
            self.output.configure(state="normal")
            self.output.delete("1.0", "end")
            self.output.insert("1.0", result)
            self.output.configure(state="disabled")
        self.after(150, self._poll)
