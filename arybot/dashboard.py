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
    def __init__(self, parent, app):
        super().__init__(parent, padding=14)
        self.app = app
        self.period = tk.StringVar(value="Hoy")
        self.status = tk.StringVar(value="Consultando historial local…")
        self.values = {}
        self.cached = None
        heading = ttk.Frame(self)
        heading.pack(fill="x")
        ttk.Label(heading, text="Centro de estadísticas", font=("Segoe UI", 19, "bold")).pack(side="left")
        ttk.Button(heading, text="Actualizar", command=self.refresh).pack(side="right")
        select = ttk.Combobox(heading, state="readonly", values=PERIODS,
                              textvariable=self.period, width=18)
        select.pack(side="right", padx=8)
        select.bind("<<ComboboxSelected>>", lambda _e: self.refresh())
        ttk.Label(self, text="Por fecha de recepción de la solicitud • Incluye su estado actual",
                  foreground=MUTED).pack(anchor="w", pady=(3, 12))

        cards = ttk.Frame(self)
        cards.pack(fill="x")
        for col in range(3):
            cards.columnconfigure(col, weight=1, uniform="kpi")
        definitions = [
            ("solicitudes", "Solicitudes recibidas", BLUE),
            ("procesadas", "SÍ · envío solicitado", GREEN),
            ("ya_facturado", "SÍ · ya facturada", GREEN),
            ("no", "NO · no completada", RED),
            ("inciertas", "Sin confirmación", AMBER),
            ("pendientes", "Por atender / procesando", BLUE),
        ]
        for i, (key, title, color) in enumerate(definitions):
            card = tk.Frame(cards, bg=WHITE, highlightbackground="#DCE5ED", highlightthickness=1)
            card.grid(row=i//3, column=i%3, sticky="nsew", padx=4, pady=4)
            tk.Frame(card, bg=color, height=3).pack(fill="x")
            tk.Label(card, text=title, bg=WHITE, fg=MUTED, anchor="w",
                     font=("Segoe UI", 10)).pack(fill="x", padx=12, pady=(7, 0))
            var = self.values[key] = tk.StringVar(value="—")
            tk.Label(card, textvariable=var, bg=WHITE, fg=color, anchor="w",
                     font=("Segoe UI", 24, "bold")).pack(fill="x", padx=12, pady=(0, 5))
        self.extra = tk.StringVar(value="")
        ttk.Label(self, textvariable=self.extra, foreground=MUTED, wraplength=760).pack(anchor="w", pady=6)

        sub = ttk.Notebook(self)
        sub.pack(fill="both", expand=True)
        self.station_tree = self._table(sub, "Por estación",
            ("Estación", "Solicitudes", "SÍ nuevas", "SÍ previas", "NO", "Sin confirmar", "Por atender"),
            (130, 85, 80, 80, 65, 115, 90))
        self.detail_tree = self._table(sub, "Resultados por solicitud",
            ("Recepción", "Estación", "Trámite", "Folio", "¿Se facturó?", "Detalle", "Origen"),
            (120, 85, 75, 95, 110, 290, 85))
        self.breakdown_tree = self._table(sub, "Estados / pagos / origen",
            ("Grupo", "Detalle", "Cantidad"), (130, 350, 90))
        self.daily_tree = self._table(sub, "Facturas por día",
            ("Fecha recepción", "Envíos solicitados"), (230, 180))
        ttk.Label(self, textvariable=self.status, foreground=MUTED, wraplength=790).pack(anchor="w", pady=(7, 0))
        ttk.Label(self, text="SÍ = Polaris terminó el flujo de envío o detectó un folio ya facturado. "
                  "No verifica UUID del PAC ni recepción del correo. Un timbrado incierto se indica como SIN CONFIRMAR para impedir duplicados.",
                  foreground=MUTED, wraplength=790).pack(anchor="w", pady=(4, 0))
        self.after(50, self.refresh)
        self.after(15000, self._tick)

    def _table(self, notebook, title, columns, widths):
        panel = ttk.Frame(notebook, padding=6)
        notebook.add(panel, text=title)
        tree = ttk.Treeview(panel, columns=columns, show="headings", height=7)
        scroll = ttk.Scrollbar(panel, orient="vertical", command=tree.yview)
        horizontal = ttk.Scrollbar(panel, orient="horizontal", command=tree.xview)
        tree.configure(yscrollcommand=scroll.set, xscrollcommand=horizontal.set)
        tree.grid(row=0, column=0, sticky="nsew")
        scroll.grid(row=0, column=1, sticky="ns")
        horizontal.grid(row=1, column=0, sticky="ew")
        panel.rowconfigure(0, weight=1)
        panel.columnconfigure(0, weight=1)
        for name, width in zip(columns, widths):
            tree.heading(name, text=name)
            tree.column(name, width=width, minwidth=60, anchor="w")
        return tree

    @staticmethod
    def _replace(tree, rows):
        tree.delete(*tree.get_children())
        for row in rows:
            tree.insert("", "end", values=row)

    def refresh(self):
        try:
            summary = summarize(read_jobs(self.app.cola.store.path, self.period.get()))
        except (sqlite3.Error, OSError, ValueError) as exc:
            self.status.set("No se pudo actualizar; se conserva la última vista válida. " + str(exc))
            return
        if summary != self.cached:
            self.cached = summary
            t = summary["totals"]
            for key, var in self.values.items():
                var.set(str(t[key]))
            avg = summary["average"]
            avg_label = "—" if avg is None else f"{avg/60:.1f} min"
            self.extra.set(f"Solicitudes de factura: {t['facturas']}   |   Altas confirmadas: {t['altas']}   |   "
                           f"Duplicadas: {t['duplicadas']}   |   Tiempo medio de envío: {avg_label}")
            self._replace(self.station_tree, [
                (s, c["solicitudes"], c["procesadas"], c["ya_facturado"], c["no"], c["inciertas"], c["pendientes"])
                for s, c in sorted(summary["stations"].items())])
            self._replace(self.detail_tree, summary["details"][:200])
            rows = [("Facturación", "SÍ: envío solicitado", t["procesadas"]),
                    ("Facturación", "SÍ: folio ya facturado", t["ya_facturado"]),
                    ("Facturación", "NO: no completada", t["no"]),
                    ("Facturación", "Sin confirmar", t["inciertas"])]
            for group, key in (("Origen", "sources"), ("Forma de pago", "payments")):
                rows.extend((group, v, n) for v, n in summary[key].most_common())
            self._replace(self.breakdown_tree, rows)
            self._replace(self.daily_tree, sorted(summary["daily"].items(), reverse=True))
        self.status.set("Actualizado " + datetime.now().strftime("%H:%M:%S") +
                        " • Refresco automático cada 15 s al abrir Estadísticas • Últimas 200 solicitudes en actividad.")

    def _tick(self):
        if self.app.nb.select() == str(self):
            self.refresh()
        self.after(15000, self._tick)
