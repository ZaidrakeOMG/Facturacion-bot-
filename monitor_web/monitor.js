(function(){
"use strict";
const $ = id => document.getElementById(id);
const STATE_DONE = new Set(["ALTA_CONFIRMADA","ENVIO_SOLICITADO","YA_FACTURADO","PRUEBA_CERRADA","REVISION_CERRADA","FINALIZADA","CANCELADA"]);
const STATE_REVIEW = new Set(["REVISION_REQUERIDA","PREPARADA_ALTA","PREPARADA_FACTURA","PRUEBA_PENDIENTE","ACEPTAR_ALTA","ESPERANDO_DATOS","REVISION_ENTRADA"]);
const STATE_PENDING = new Set(["EN_COLA","EJECUTANDO"]);
let filtro = "todas";
let ultima = null;
let consultando = false;
function fecha(ts) {
  if (!ts || !Number.isFinite(Number(ts))) return "—";
  return new Date(Number(ts)*1000).toLocaleString("es-MX",{day:"2-digit",month:"short",hour:"2-digit",minute:"2-digit"});
}
function contar(counts,estados) {return [...estados].reduce((n,k)=>n+(Number(counts[k])||0),0);}
function establecer(id,val){$(id).textContent=String(val);}
function categoria(estado){
  if(STATE_REVIEW.has(estado)) return "warning";
  if(STATE_DONE.has(estado)) return "done";
  if(estado==="EJECUTANDO")return "running";
  return "";
}
function coincide(row){
  if(filtro==="todas") return true;
  if(filtro==="pendientes") return STATE_PENDING.has(row.estado);
  if(filtro==="revisar") return STATE_REVIEW.has(row.estado);
  if(filtro==="terminadas") return STATE_DONE.has(row.estado);
  return true;
}
function cell(tr,value,extraClass){
  const td=document.createElement("td");
  if(extraClass)td.className=extraClass;
  td.textContent=String(value ?? "—");
  tr.appendChild(td);
  return td;
}
function renderTabla(rows){
  const data=rows.filter(coincide);
  const body=$("filas");
  body.replaceChildren();
  establecer("total-tabla",data.length+" registros");
  if(!data.length){
    const tr=document.createElement("tr");
    const td=cell(tr,"No hay solicitudes para este filtro.","empty");
    td.colSpan=6;body.appendChild(tr);return;
  }
  for(const item of data){
    const tr=document.createElement("tr");
    cell(tr,item.id);
    cell(tr,item.estacion);
    cell(tr,item.tipo==="FACTURA" ? "Factura" : item.tipo==="ALTA" ? "Alta de cliente" : item.tipo);
    cell(tr,item.modo,"mode");
    const status=cell(tr,"");
    const badge=document.createElement("span");
    badge.className="state "+categoria(item.estado);
    badge.textContent=item.estado.replaceAll("_"," ");
    status.appendChild(badge);
    cell(tr,fecha(item.actualizado));
    body.appendChild(tr);
  }
}
function render(data){
  ultima=data;
  const counts=data.conteos||{},avisos=data.avisos||{};
  establecer("cuenta-cola",counts.EN_COLA||0);
  establecer("cuenta-ejecutando",counts.EJECUTANDO||0);
  establecer("cuenta-finalizadas",contar(counts,STATE_DONE));
  establecer("cuenta-revision",contar(counts,STATE_REVIEW));
  establecer("pausada",data.pausada===null?"Sin datos":data.pausada?"Pausada":"Habilitada");
  establecer("bloqueada",data.bloqueada===null?"Sin datos":data.bloqueada?"Bloqueada":"Sin bloqueo");
  establecer("activa",data.activa?data.activa.toUpperCase():"Ninguno registrado");
  establecer("actividad",fecha(data.ultima_actividad));
  establecer("aviso-pendiente",avisos.PENDIENTE||0);
  establecer("aviso-enviando",avisos.ENVIANDO||0);
  establecer("aviso-error",(avisos.ERROR_ENVIO||0)+(avisos.ENVIO_INCIERTO||0));
  renderTabla(data.trabajos||[]);
  const con=$("conexion");
  con.className="connection "+(data.disponible?"ok":"error");
  establecer("conexion-texto",data.disponible?"Base de datos disponible":"Sin datos de cola");
  establecer("actualizado","Consultado: "+fecha(data.actualizado));
  const alert=$("alerta");
  alert.hidden=data.disponible;
  if(!data.disponible)alert.textContent=data.mensaje||"No se puede consultar la cola.";
}
async function actualizar(){
  if(consultando)return;
  consultando=true;
  $("actualizar").disabled=true;
  try{
    const response=await fetch("/api/estado",{cache:"no-store",credentials:"same-origin"});
    if(!response.ok)throw new Error("No se pudo consultar la cola (HTTP "+response.status+").");
    render(await response.json());
  }catch(e){
    $("conexion").className="connection error";
    establecer("conexion-texto","Conexión interrumpida");
    $("alerta").hidden=false;
    $("alerta").textContent=e.message||"No disponible.";
  }finally{consultando=false;$("actualizar").disabled=false;}
}
document.querySelectorAll("[data-filter]").forEach(button=>button.addEventListener("click",()=>{
  filtro=button.dataset.filter;
  document.querySelectorAll("[data-filter]").forEach(el=>{
    const active=el===button;el.classList.toggle("active",active);el.setAttribute("aria-pressed",String(active));
  });
  if(ultima)renderTabla(ultima.trabajos||[]);
}));
$("actualizar").addEventListener("click",actualizar);
actualizar();
setInterval(actualizar,10000);
})();