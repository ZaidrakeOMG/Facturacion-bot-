/* ARY | Centro de Control - visualizacion segura, sin dependencias ni operaciones fiscales */
(() => {
  "use strict";
  const $ = id => document.getElementById(id);
  const q = s => document.querySelectorAll(s);
  const ns = "http://www.w3.org/2000/svg";
  const fmt = n => (Number(n) || 0).toLocaleString("es-MX");
  const numero = n => Math.max(0, Number(n) || 0);
  const reducedMotion = window.matchMedia("(prefers-reduced-motion: reduce)").matches;
  const state = { data: null, busy: false, filter: "todos", search: "", charts: "", currentView: "resumen" };
  const titleByView = {
    resumen: ["Dashboard", "Panel de resultados.", "Una lectura clara de las solicitudes que atiende ARY Facturación Bot."],
    solicitudes: ["Solicitudes", "Historial de solicitudes.", "Consulta el estado de cada trámite sin exponer los datos fiscales del cliente."],
    incidencias: ["Incidencias", "Análisis de motivos.", "Identifica por qué no se completaron trámites y cuáles requieren revisión."],
    operacion: ["Cola y sistema", "Operación en tiempo real.", "Cola, avisos y actividad persistida en la VM, sin controlar Polaris."]
  };

  function fecha(epoch, full=false) {
    const n = Number(epoch);
    if (!n || !Number.isFinite(n)) return "—";
    return new Date(n * 1000).toLocaleString("es-MX", {
      day: "2-digit", month: "short", year: full ? "numeric" : undefined,
      hour: "2-digit", minute: "2-digit", hour12: false
    });
  }
  function set(id, text) {
    const el = $(id);
    if (el) el.textContent = String(text ?? "—");
  }
  function error(text) {
    $("alerta").hidden = !text;
    $("alerta").textContent = text || "";
  }
  function animateNumber(id, target) {
    const el = $(id);
    if (!el) return;
    target = Math.max(0, Number(target) || 0);
    const prior = Number(el.dataset.current);
    if (Number.isFinite(prior) && prior === target) return;
    el.dataset.current = String(target);
    const start = Number.isFinite(prior) ? prior : 0;
    if (reducedMotion || Math.abs(target - start) > 10000) {
      el.textContent = fmt(target);
      return;
    }
    const started = performance.now();
    const duration = 700;
    const tick = now => {
      if (Number(el.dataset.current) !== target) return;
      const p = Math.min(1, (now - started) / duration);
      const ease = 1 - Math.pow(1 - p, 3);
      el.textContent = fmt(Math.round(start + (target - start) * ease));
      if (p < 1) requestAnimationFrame(tick);
    };
    requestAnimationFrame(tick);
  }
  function openView(name) {
    if (!titleByView[name]) return;
    state.currentView = name;
    for (const section of q(".view")) {
      const active = section.id === "vista-" + name;
      section.hidden = !active;
      section.classList.toggle("active", active);
    }
    for (const button of q(".nav-link[data-view]")) {
      const active = button.dataset.view === name;
      button.classList.toggle("active", active);
      if (active) button.setAttribute("aria-current", "page");
      else button.removeAttribute("aria-current");
    }
    const [crumb, title, sub] = titleByView[name];
    set("breadcrumb", crumb);
    const parts = title.endsWith(".") ? title.slice(0, -1).split(" ") : title.split(" ");
    const end = parts.pop();
    const h = $("vista-titulo");
    h.replaceChildren(document.createTextNode(parts.join(" ") + " "));
    const em = document.createElement("em");
    em.textContent = end + ".";
    h.appendChild(em);
    set("vista-subtitulo", sub);
    $("shell").classList.remove("menu-open");
    $("btn-menu").setAttribute("aria-expanded", "false");
    if (state.data && name === "solicitudes") renderTable();
    window.scrollTo({top: 0, behavior: reducedMotion ? "auto" : "smooth"});
  }
  for (const button of q("[data-view]")) button.addEventListener("click", () => openView(button.dataset.view));
  for (const button of q("[data-goto]")) button.addEventListener("click", () => openView(button.dataset.goto));
  $("btn-collapse").addEventListener("click", () => {
    const compact = $("shell").classList.toggle("compact");
    $("btn-collapse").setAttribute("aria-label", compact ? "Expandir menú" : "Contraer menú");
    $("btn-collapse").title = compact ? "Expandir menú" : "Contraer menú";
  });
  $("btn-menu").addEventListener("click", () => {
    const open = $("shell").classList.toggle("menu-open");
    $("btn-menu").setAttribute("aria-expanded", String(open));
  });
  $("backdrop").addEventListener("click", () => {
    $("shell").classList.remove("menu-open");
    $("btn-menu").setAttribute("aria-expanded", "false");
  });
  document.addEventListener("keydown", e => {
    if (e.key === "Escape") {
      $("shell").classList.remove("menu-open");
      $("btn-menu").setAttribute("aria-expanded", "false");
    }
  });

  function drawDonut(stats) {
    const ids = [
      ["si", "donut-si"], ["no", "donut-no"],
      ["pendiente", "donut-pend"], ["revision", "donut-revision"]
    ];
    const total = ids.reduce((s, [key]) => s + numero(stats[key]), 0);
    set("donut-total", fmt(total));
    set("legend-si", fmt(stats.si)); set("legend-no", fmt(stats.no));
    set("legend-pend", fmt(stats.pendiente)); set("legend-revision", fmt(stats.revision));
    let offset = 0;
    for (const [key, id] of ids) {
      const arc = $(id);
      const fraction = total ? numero(stats[key]) / total * 100 : 0;
      arc.setAttribute("stroke-dasharray", fraction.toFixed(4) + " " + (100 - fraction).toFixed(4));
      arc.setAttribute("stroke-dashoffset", (-offset).toFixed(4));
      offset += fraction;
    }
    $("donut-access").setAttribute("aria-label",
      "Facturas: " + fmt(stats.si) + " con factura, " + fmt(stats.no) +
      " no completadas, " + fmt(stats.pendiente) + " pendientes y " +
      fmt(stats.revision) + " sin confirmar.");
  }

  function svgElement(name, attrs={}, contents="") {
    const el = document.createElementNS(ns, name);
    for (const [key, value] of Object.entries(attrs)) el.setAttribute(key, String(value));
    if (contents) el.textContent = String(contents);
    return el;
  }
  function drawTrend(days) {
    const svg = $("grafica-tendencia");
    svg.replaceChildren();
    const data = Array.isArray(days) ? days.slice(-7) : [];
    if (!data.length) {
      svg.appendChild(svgElement("text", {x:310, y:95, "text-anchor":"middle", class:"chart-empty"}, "Sin datos"));
      return;
    }
    const top = 16, bottom = 159, left = 42, right = 601, max = Math.max(
      1, ...data.map(d => Math.max(numero(d.si),numero(d.no)))
    );
    const defs = svgElement("defs");
    const grad = svgElement("linearGradient", {id:"chart-fill",x1:"0",x2:"0",y1:"0",y2:"1"});
    grad.appendChild(svgElement("stop",{offset:"0%","stop-color":"#3fd2a4","stop-opacity":".23"}));
    grad.appendChild(svgElement("stop",{offset:"100%","stop-color":"#3fd2a4","stop-opacity":"0"}));
    defs.appendChild(grad);svg.appendChild(defs);
    for (let i=0;i<4;i++) {
      const y=top + (bottom-top)*i/3;
      svg.appendChild(svgElement("line",{x1:left,y1:y,x2:right,y2:y,class:"chart-grid"}));
      svg.appendChild(svgElement("text",{x:30,y:y+4,"text-anchor":"end",class:"chart-tick"},Math.round(max*(1-i/3))));
    }
    const xFor=i => left+(right-left)*i/Math.max(1,data.length-1);
    const yFor=v => bottom - numero(v)/max*(bottom-top);
    const coords=key=>data.map((d,i)=>[xFor(i),yFor(d[key])]);
    const poly = pairs => pairs.map(([x,y],i)=>(i?"L":"M")+x.toFixed(2)+","+y.toFixed(2)).join(" ");
    const yes = coords("si"), no=coords("no");
    if (yes.length) {
      const area=poly(yes)+" L"+yes[yes.length-1][0].toFixed(2)+","+bottom+
        " L"+yes[0][0].toFixed(2)+","+bottom+" Z";
      svg.appendChild(svgElement("path",{d:area,class:"chart-area"}));
      svg.appendChild(svgElement("path",{d:poly(yes),class:"chart-line yes"}));
      svg.appendChild(svgElement("path",{d:poly(no),class:"chart-line no"}));
      for(const [key,points] of [["yes",yes],["no",no]]) {
        for(const [x,y] of points) svg.appendChild(svgElement("circle",
          {cx:x.toFixed(2),cy:y.toFixed(2),r:3.6,class:"chart-point "+key}));
      }
    }
    data.forEach((d,i)=>{
      const raw=String(d.fecha||"");
      const label=/^\d{4}-\d{2}-\d{2}$/.test(raw)?raw.slice(8)+"/"+raw.slice(5,7):"—";
      svg.appendChild(svgElement("text",{x:xFor(i),y:185,"text-anchor":"middle",class:"chart-tick"},label));
    });
    svg.setAttribute("aria-label", "Resultados de los últimos siete días. "+
      data.map(d => d.fecha+": "+numero(d.si)+" sí, "+numero(d.no)+" no.").join(" "));
  }

  function drawReasons(target, reasons, type) {
    const root=$(target);
    root.replaceChildren();
    const rows=Array.isArray(reasons)?reasons:[];
    if (!rows.length) {
      const empty=document.createElement("div");empty.className="empty";
      empty.textContent=type==="review"?"Sin solicitudes que requieran revisión en este periodo.":"No se registran facturas no completadas en este periodo.";
      root.appendChild(empty);return;
    }
    const biggest=Math.max(1,...rows.map(row=>numero(row.cantidad)));
    for(const row of rows) {
      const wrap=document.createElement("div");wrap.className="bar-item";
      const head=document.createElement("div");head.className="bar-head";
      const title=document.createElement("span");title.textContent=String(row.motivo||"Sin categoría");
      const count=document.createElement("strong");count.textContent=fmt(row.cantidad);
      head.append(title,count);
      const track=document.createElement("div");track.className="bar-track";
      const fill=document.createElement("div");fill.className="bar-fill"+(type==="review"?" review":"");
      track.appendChild(fill);wrap.append(head,track);root.appendChild(wrap);
      requestAnimationFrame(()=>{fill.style.width=(numero(row.cantidad)/biggest*100).toFixed(2)+"%";});
    }
  }
  function drawStations(stations) {
    const root=$("grafica-estaciones");root.replaceChildren();
    const rows=Array.isArray(stations)?stations.slice(0,8):[];
    if(!rows.length){
      const empty=document.createElement("div");empty.className="empty";
      empty.textContent="Todavía no hay resultados reales de facturación por estación.";
      root.appendChild(empty);return;
    }
    const total = s => numero(s.si)+numero(s.no)+numero(s.pendiente)+numero(s.revision);
    const highest=Math.max(1,...rows.map(total));
    for(const item of rows){
      const wrap=document.createElement("div");wrap.className="station-line";
      const name=document.createElement("span");name.className="station-label";name.textContent=item.nombre;
      name.title=item.nombre;
      const track=document.createElement("div");track.className="station-track";
      for(const [key,cls] of [["si","yes"],["no","no"],["pendiente","pending"],["revision","review"]]){
        const segment=document.createElement("div");segment.className="station-segment "+cls;
        segment.title=key+": "+fmt(item[key]);
        track.appendChild(segment);
        requestAnimationFrame(()=>{segment.style.width=(numero(item[key])/highest*100).toFixed(2)+"%";});
      }
      const n=document.createElement("span");n.className="station-total";n.textContent=fmt(total(item));
      wrap.append(name,track,n);root.appendChild(wrap);
    }
    const legend=document.createElement("div");legend.className="station-legend";
    for(const [cls,label] of [["green","Con factura"],["red","No"],["yellow","Pendiente"],["lilac","Revisión"]]){
      const item=document.createElement("span"),dot=document.createElement("i");
      dot.className="legend-dot "+cls;item.append(dot,document.createTextNode(label));legend.appendChild(item);
    }
    root.appendChild(legend);
  }

  function cell(tr,val,cls=""){
    const td=document.createElement("td");
    if(cls) td.className=cls;
    td.textContent=String(val ?? "—");
    tr.appendChild(td);return td;
  }
  function tableMatches(row){
    const f=$("filtro-resultado").value;
    const grupo=String(row.grupo||"");
    if(f!=="todos" && (f==="alta"?!grupo.startsWith("alta"):f!==grupo)) return false;
    const search=state.search.trim().toLocaleLowerCase("es-MX");
    if(!search)return true;
    return [row.id,row.estacion,row.tipo,row.motivo,row.estado,row.resultado]
      .some(s=>String(s||"").toLocaleLowerCase("es-MX").includes(search));
  }
  function renderTable() {
    const body=$("filas");body.replaceChildren();
    const r=state.data?.reporte;
    const all=Array.isArray(r?.trabajos)?r.trabajos:[];
    const rows=all.filter(tableMatches);
    set("total-tabla",fmt(rows.length)+" visibles");
    if(!rows.length){
      const tr=document.createElement("tr");const td=cell(tr,"No hay solicitudes con estos filtros.","empty");
      td.colSpan=7;body.appendChild(tr);
    }
    for(const row of rows){
      const tr=document.createElement("tr");
      cell(tr,row.id);cell(tr,row.estacion);
      cell(tr,row.tipo==="FACTURA"?"Factura":row.tipo==="ALTA"?"Alta":row.tipo||"Otro");
      cell(tr,row.modo);
      const td=cell(tr,"");
      const badge=document.createElement("span");badge.className="badge "+(
        ["si","no","pendiente","revision","prueba"].includes(row.grupo)?row.grupo:
          row.grupo?.startsWith("alta_si")?"si":row.grupo?.startsWith("alta_no")?"no":
          row.grupo?.startsWith("alta_pendiente")?"pendiente":"revision");
      badge.textContent=row.resultado;td.appendChild(badge);
      cell(tr,row.motivo==="—"?"Sin incidencia registrada":row.motivo);
      cell(tr,fecha(row.actualizado,true));
      body.appendChild(tr);
    }
    const total=numero(r?.total_registros);
    set("tabla-nota",
      "De "+fmt(total)+" registros en el periodo; se muestran hasta 150."+
      " Los archivados quedan fuera de este monitor.");
  }
  $("busqueda").addEventListener("input",event=>{
    state.search=event.target.value; if(state.data)renderTable();
  });
  $("filtro-resultado").addEventListener("change",()=>{if(state.data)renderTable();});

  function render(data) {
    state.data=data;
    if(!data.disponible){
      error(data.mensaje||"La base de datos de la cola no está disponible.");return;
    }
    error("");
    const r=data.reporte||{}, facts=r.facturas||{}, altas=r.altas||{};
    const total = numero(facts.si)+numero(facts.no)+numero(facts.pendiente)+numero(facts.revision);
    animateNumber("k-total",total);
    animateNumber("k-si",facts.si);
    animateNumber("k-no",facts.no);
    animateNumber("k-pend",facts.pendiente);
    animateNumber("k-revision",facts.revision);
    set("menu-incidencias",fmt(numero(facts.no)+numero(facts.revision)));
    set("fecha-resumen", $("selector-periodo").selectedOptions[0]?.textContent||"");
    drawDonut(facts);
    set("cola-global",fmt(data.conteos?.EN_COLA));
    set("ejecutando-global",fmt(data.conteos?.EJECUTANDO));
    set("altas-ok",fmt(altas.alta_si));
    set("pruebas-total",fmt(facts.prueba));
    set("estado-cola",data.pausada===null?"Sin información":data.pausada?"Pausada":"Habilitada");
    set("estado-bloqueo",data.bloqueada===null?"Sin información":data.bloqueada?"Con bloqueo":"Sin bloqueo");
    set("estado-activa",data.activa?String(data.activa).toUpperCase():"Ninguno registrado");
    set("estado-actividad",fecha(data.ultima_actividad,true));
    const avisos=data.avisos||{};
    set("aviso-pendiente",fmt(avisos.PENDIENTE));
    set("aviso-enviando",fmt(avisos.ENVIANDO));
    set("aviso-enviado",fmt(avisos.ENVIADO));
    set("aviso-error",fmt(numero(avisos.ERROR_ENVIO)+numero(avisos.ENVIO_INCIERTO)));
    set("ultima-lectura",fecha(data.actualizado,true));
    $("conexion").className="connection ok";
    set("conexion-texto","SQLite disponible");
    $("side-dot").className="status-light ok";
    const chartsKey=JSON.stringify([r.motivos_no,r.motivos_revision,r.estaciones,r.tendencia]);
    if(chartsKey!==state.charts){
      state.charts=chartsKey;
      drawReasons("motivos-no",r.motivos_no,"no");
      drawReasons("motivos-no-detalle",r.motivos_no,"no");
      drawReasons("motivos-revision",r.motivos_revision,"review");
      drawStations(r.estaciones);
      drawTrend(r.tendencia);
    }
    if(state.currentView==="solicitudes")renderTable();
  }

  async function actualizar(){
    if(state.busy)return;
    state.busy=true;$("btn-refresh").disabled=true;
    try{
      const periodo=$("selector-periodo").value;
      const response=await fetch("/api/estado?periodo="+encodeURIComponent(periodo),
        {cache:"no-store",credentials:"same-origin",headers:{"Accept":"application/json"}});
      if(!response.ok)throw new Error("Monitor no disponible (HTTP "+response.status+").");
      const data=await response.json();
      render(data);
    }catch(e){
      error("No se pudo actualizar el monitor. Revisa la tarea de Windows y la conexión con 10.20.20.182:8787.");
      $("conexion").className="connection off";
      set("conexion-texto","Sin conexión");
      $("side-dot").className="status-light off";
    }finally{
      state.busy=false;$("btn-refresh").disabled=false;
    }
  }
  $("selector-periodo").addEventListener("change",()=>{state.charts="";actualizar();});
  $("btn-refresh").addEventListener("click",actualizar);
  openView("resumen");
  actualizar();
  window.setInterval(()=>{if(!document.hidden)actualizar();},10000);
})();