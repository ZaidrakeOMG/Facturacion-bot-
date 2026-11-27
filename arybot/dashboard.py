"""Panel de control ARY: estadísticas de solo lectura de la cola local."""
from __future__ import annotations

import json
import sqlite3
import tkinter as tk
from collections import Counter, defaultdict
from datetime import datetime, timedelta
from .cola_modelo import resultado_facturacion
from tkinter import ttk

NAVY = "#10243B"
BLUE = "#164E91"
RED = "#C62836"
BG = "#F0F4F8"
WHITE = "#FFFFFF"
INK = "#172B40"
MUTED = "#52677D"
GREEN = "#08764D"
AMBER = "#9A5808"

PERIODS = ("Hoy", "Últimos 7 días", "Este mes", "Todo el historial")
STATES = {
    "ENVIO_SOLICITADO": "Procesada / envío solicitado",
    "YA_FACTURADO": "Ya facturado",
    "EN_COLA": "En cola", "EJECUTANDO": "En ejecución",
    "REVISION_REQUERIDA": "Revisión requerida",
    "REVISION_ENTRADA": "Revisión de entrada",
    "ESPERANDO_DATOS": "Faltan datos", "DUPLICADA": "Duplicada",
    "PREPARADA_FACTURA": "Preparada sin timbrar",
    "PREPARADA_ALTA": "Alta preparada",
    "ALTA_CONFIRMADA": "Alta confirmada", "CANCELADA": "Cancelada",
    "PRUEBA_CERRADA": "Prueba cerrada", "REVISION_CERRADA": "Revisión cerrada",
    "FINALIZADA": "Finalizada (sin detalle)", "PRUEBA_PENDIENTE": "Prueba pendiente",
    "ACEPTAR_ALTA": "Alta por aceptar",
}
REVIEW = {"REVISION_REQUERIDA", "REVISION_ENTRADA", "ESPERANDO_DATOS",
          "PREPARADA_FACTURA", "PREPARADA_ALTA", "PRUEBA_PENDIENTE", "ACEPTAR_ALTA"}


def period_start(period, now=None):
    now = now or datetime.now()
    today = now.replace(hour=0, minute=0, second=0, microsecond=0)
    if period == "Hoy":
        return today.timestamp()
    if period == "Últimos 7 días":
        return (today - timedelta(days=6)).timestamp()
    if period == "Este mes":
        return today.replace(day=1).timestamp()
    return None


def read_jobs(path, period, now=None):
    """Read existing SQLite without creating, migrating or writing anything."""
    start = period_start(period, now)
    uri = path.resolve().as_uri() + "?mode=ro"
    with sqlite3.connect(uri, uri=True, timeout=.25) as conn:
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA query_only=ON")
        sql = """SELECT id,tipo,origen,seguro,estado,datos,creado,iniciado,terminado,motivo
                 FROM trabajos WHERE tipo IN ('FACTURA','ALTA')"""
        params = ()
        if start is not None:
            sql += " AND creado >= ?"
            params = (start,)
        return [dict(row) for row in conn.execute(sql + " ORDER BY creado DESC, id", params)]


def summarize(rows):
    """Only real ENVIO_SOLICITADO is a successful invoice workflow, not PAC verification."""
    totals = Counter()
    stations = defaultdict(Counter)
    sources = Counter()
    states = Counter()
    payments = Counter()
    daily = Counter()
    details = []
    durations = []
    for row in rows:
        try:
            data = json.loads(row["datos"])
            if not isinstance(data, dict):
                data = {}
        except (ValueError, TypeError):
            data = {}
        state = row["estado"]
        kind = row["tipo"]
        safe = bool(row["seguro"])
        answer, detail = resultado_facturacion({"tipo":kind, "estado":state, "seguro":safe,
                                                "motivo":row.get("motivo", "")})
        station = str(data.get("estacion") or "Sin estación")
        stamp = datetime.fromtimestamp(row["creado"])
        real_invoice = kind == "FACTURA" and not safe
        successful = real_invoice and state == "ENVIO_SOLICITADO"
        # Los dos KPI miden únicamente resultados de facturación del BOT:
        # - SÍ: Polaris completó el flujo y solicitó el envío.
        # - NO: operación real fallida antes de confirmar timbrado.
        # No contar altas, pruebas, pendientes, folios ya facturados antes,
        # cancelaciones ni posibles CFDI con resultado incierto.
        if successful:
            totals["kpi_si"] += 1
        elif real_invoice and state in {
                "REVISION_REQUERIDA", "REVISION_CERRADA",
                "ESPERANDO_DATOS", "REVISION_ENTRADA"
        } and answer == "NO":
            totals["kpi_no"] += 1
        totals["solicitudes"] += 1
        if kind == "FACTURA":
            if answer == "NO":
                totals["no"] += 1
                stations[station]["no"] += 1
            elif answer == "SIN CONFIRMAR":
                totals["inciertas"] += 1
                stations[station]["inciertas"] += 1
        states[state] += 1
        sources[row["origen"]] += 1
        stations[station]["solicitudes"] += 1
        if kind == "FACTURA":
            totals["facturas"] += 1
        if safe:
            totals["pruebas"] += 1
        if successful:
            totals["procesadas"] += 1
            stations[station]["procesadas"] += 1
            payments[str(data.get("forma_pago") or "No indicada")] += 1
            daily[stamp.strftime("%Y-%m-%d")] += 1
            if row["iniciado"] is not None and row["terminado"] is not None:
                seconds = row["terminado"] - row["iniciado"]
                if seconds >= 0:
                    durations.append(seconds)
        if kind == "FACTURA" and state == "YA_FACTURADO":
            totals["ya_facturado"] += 1
            stations[station]["ya_facturado"] += 1
        if state == "DUPLICADA":
            totals["duplicadas"] += 1
        if state in REVIEW:
            totals["revision"] += 1
            stations[station]["revision"] += 1
        if state in {"EN_COLA", "EJECUTANDO"}:
            totals["pendientes"] += 1
            stations[station]["pendientes"] += 1
        if kind == "ALTA" and not safe and state == "ALTA_CONFIRMADA":
            totals["altas"] += 1
        details.append((stamp.strftime("%d/%m/%Y %H:%M"), station, kind,
                        str(data.get("ticket") or "—"), answer,
                        " ".join(detail.split())[:180], str(row["origen"])))
    return dict(totals=totals, stations=stations, sources=sources, states=states,
                payments=payments, daily=daily, details=details,
                average=sum(durations)/len(durations) if durations else None)


def apply_style(root):
    root.configure(bg=BG)
    style = ttk.Style(root)
    try:
        style.theme_use("clam")
    except tk.TclError:
        pass
    style.configure(".", font=("Segoe UI", 10))
    style.configure("TFrame", background=BG)
    style.configure("TLabel", background=BG, foreground=INK)
    style.configure("TLabelframe", background=BG, bordercolor="#D6E0EA")
    style.configure("TLabelframe.Label", background=BG, foreground=BLUE,
                    font=("Segoe UI", 10, "bold"))
    style.configure("TButton", padding=(10, 6))
    style.configure("TCheckbutton", background=BG, foreground=INK)
    style.configure("TNotebook", background=BG, borderwidth=0)
    style.configure("TNotebook.Tab", padding=(12, 8))
    style.map("TNotebook.Tab", background=[("selected", WHITE)], foreground=[("selected", BLUE)])
    style.configure("Treeview", rowheight=27, background=WHITE,
                    fieldbackground=WHITE, foreground=INK, font=("Segoe UI", 9))
    style.configure("Treeview.Heading", background="#DFE8F2", foreground=NAVY,
                    font=("Segoe UI", 9, "bold"), padding=5)
    style.map("Treeview", background=[("selected", BLUE)], foreground=[("selected", WHITE)])
    style.layout("ARY.TNotebook.Tab", [])
    style.configure("ARY.TNotebook", background=BG, borderwidth=0)
    return style


def attach_navigation(app, sidebar):
    """Notebook remains the owner of the existing pages and callbacks."""
    buttons = {}
    def sync(_event=None):
        try:
            selected = app.nb.select()
            tabs = app.nb.tabs()
            for tab in tuple(buttons):
                if tab not in tabs:
                    buttons.pop(tab).destroy()
            for tab in tabs:
                label = app.nb.tab(tab, "text").strip()
                if tab not in buttons:
                    button = tk.Button(sidebar, text=label, anchor="w",
                                       relief="flat", bd=0, padx=13, pady=9,
                                       font=("Segoe UI", 10), cursor="hand2",
                                       command=lambda t=tab: app.nb.select(t))
                    buttons[tab] = button
                button = buttons[tab]
                button.pack_forget()
                button.pack(fill="x", padx=9, pady=2)
                active = tab == selected
                button.configure(bg=BLUE if active else NAVY,
                                 fg=WHITE if active else "#D5E3F1",
                                 activebackground=BLUE, activeforeground=WHITE)
        except tk.TclError:
            pass
    app.nb.bind("<<NotebookTabChanged>>", sync, add="+")
    app.root.after_idle(sync)
    app.root.after(800, sync)
    return sync


class Dashboard(ttk.Frame):
    """Estadísticas simplificadas: dos conteos y una gráfica de barras.

    Consulta la misma base SQLite de solicitudes solo en modo lectura.
    No contabiliza como fracaso un CFDI de resultado desconocido, ni
    hace pasar por facturación nueva un folio ya facturado.
    """
    def __init__(self, parent, app):
        super().__init__(parent, padding=16)
        self.app = app
        self.period = tk.StringVar(value="Hoy")
        self.status = tk.StringVar(value="Consultando resultados de facturación…")
        self.values = {
            "kpi_si": tk.StringVar(value="0"),
            "kpi_no": tk.StringVar(value="0"),
        }
        self.chart_values = (0, 0)
        self.cached = None

        head = ttk.Frame(self)
        head.pack(fill="x", pady=(0, 10))
        ttk.Label(head, text="Estadísticas de facturación",
                  font=("Segoe UI", 19, "bold")).pack(side="left")
        ttk.Button(head, text="Actualizar", command=self.refresh).pack(side="right")
        period = ttk.Combobox(head, state="readonly", values=PERIODS,
                              textvariable=self.period, width=18)
        period.pack(side="right", padx=8)
        period.bind("<<ComboboxSelected>>", lambda _e: self.refresh())
        ttk.Label(self, text="Facturas del bot con resultado definitivo, según la fecha de recepción.",
                  foreground=MUTED).pack(anchor="w", pady=(0, 12))

        cards = ttk.Frame(self)
        cards.pack(fill="x", pady=(0, 12))
        for i, (key, label, color) in enumerate((
                ("kpi_si", "SÍ SE FACTURÓ", GREEN),
                ("kpi_no", "NO SE FACTURÓ", RED))):
            cards.columnconfigure(i, weight=1, uniform="resultados")
            card = tk.Frame(cards, bg=WHITE, highlightbackground="#DBE5EF",
                            highlightthickness=1)
            card.grid(row=0, column=i, sticky="nsew", padx=5, pady=5)
            tk.Frame(card, bg=color, height=4).pack(fill="x")
            tk.Label(card, text=label, bg=WHITE, fg=MUTED,
                     font=("Segoe UI", 11, "bold"), anchor="w").pack(
                         fill="x", padx=18, pady=(12, 2))
            tk.Label(card, textvariable=self.values[key], bg=WHITE, fg=color,
                     font=("Segoe UI", 36, "bold"), anchor="w").pack(
                         fill="x", padx=18, pady=(0, 12))

        ttk.Label(self, text="Gráfica de resultados",
                  font=("Segoe UI", 13, "bold")).pack(anchor="w", pady=(2, 5))
        self.chart = tk.Canvas(self, height=280, bg=WHITE,
                               highlightbackground="#DBE5EF", highlightthickness=1)
        self.chart.pack(fill="both", expand=True, pady=(0, 12))
        self.chart.bind("<Configure>", lambda _event: self._draw_chart())

        ttk.Label(
            self,
            text="SÍ: Polaris completó el flujo y solicitó el envío del CFDI. "
                 "NO: intento real terminado sin confirmar una factura. "
                 "Las pruebas, altas, solicitudes pendientes, folios previamente facturados "
                 "y posibles timbrados inciertos no se cuentan en ninguna barra. "
                 "La confirmación del UUID y la entrega del correo son independientes.",
            foreground=MUTED, wraplength=900,
        ).pack(anchor="w", pady=(0, 8))
        ttk.Label(self, textvariable=self.status, foreground=MUTED,
                  wraplength=900).pack(anchor="w")
        self.after(50, self.refresh)
        self.after(15000, self._tick)

    def _draw_chart(self):
        canvas = self.chart
        canvas.delete("all")
        width = max(canvas.winfo_width(), 350)
        height = max(canvas.winfo_height(), 220)
        yes, no = self.chart_values
        maximum = max(yes, no, 1)
        label_x = 24
        bar_x = min(170, max(108, width // 3))
        available = max(60, width - bar_x - 85)
        for label, value, color, fraction in (
                ("SÍ", yes, GREEN, .34),
                ("NO", no, RED, .70)):
            y = int(height * fraction)
            canvas.create_text(label_x, y, text=label, anchor="w",
                               font=("Segoe UI", 13, "bold"), fill=INK)
            canvas.create_rectangle(bar_x, y - 22, bar_x + available, y + 22,
                                    fill="#E8EEF4", outline="")
            if value:
                canvas.create_rectangle(bar_x, y - 22,
                                        bar_x + max(3, round(available * value / maximum)),
                                        y + 22, fill=color, outline="")
            canvas.create_text(bar_x + available + 15, y, text=str(value),
                               anchor="w", fill=color,
                               font=("Segoe UI", 15, "bold"))

    def refresh(self):
        try:
            summary = summarize(read_jobs(self.app.cola.store.path, self.period.get()))
        except (sqlite3.Error, OSError, ValueError) as exc:
            self.status.set("No se pudo leer el historial guardado: " + str(exc))
            return
        if summary != self.cached:
            self.cached = summary
            totals = summary["totals"]
            yes = totals["kpi_si"]
            no = totals["kpi_no"]
            self.values["kpi_si"].set(str(yes))
            self.values["kpi_no"].set(str(no))
            self.chart_values = (yes, no)
            self._draw_chart()
        self.status.set("Datos guardados en SQLite local • Actualizado "
                        + datetime.now().strftime("%H:%M:%S")
                        + " • Refresco automático cada 15 segundos.")

    def _tick(self):
        if self.app.nb.select() == str(self):
            self.refresh()
        self.after(15000, self._tick)
