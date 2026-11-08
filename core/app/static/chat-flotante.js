/*
 * Chat flotante de Mortimer.
 *
 * Mortimer es uno solo: este widget es la misma conversación en la portada del
 * core y en el frontend de cualquier módulo. Se incluye con
 *
 *   <script src="<raiz-core>static/chat-flotante.js" data-core="<raiz-core>"></script>
 *
 * donde <raiz-core> es la ruta RELATIVA a la raíz del core: "" en la portada,
 * "../../" desde /m/calendario/ui, "../../../" desde /m/calendario/ui/mes.
 *
 * Contrato con las páginas:
 *   - POST <core>api/chat {texto, conversacion, canal:"web"}.
 *   - La conversación abierta se guarda en localStorage["mortimer.conversacion"],
 *     así que sigue al usuario al cambiar de página.
 *   - Tras cada respuesta se emite en document el evento "mortimer:respuesta"
 *     (detail = respuesta de /api/chat) para que la página refresque sus datos.
 *   - Al empezar una conversación nueva u olvidar la actual se emite
 *     "mortimer:conversacion-borrada" (detail = {conversacion}).
 *   - window.Mortimer = {abrir, cerrar, enviar, conversacion, olvidar, continuar}.
 *
 * Sin dependencias ni CDN (DA-04). Todo el texto se inserta con textContent.
 */
(function () {
  "use strict";
  if (window.Mortimer) return;  // incluido dos veces

  var script = document.currentScript;
  var CORE = (script && script.dataset.core) || "";
  var CLAVE = "mortimer.conversacion";
  var CLAVE_ABIERTO = "mortimer.chat.abierto";

  // ── Almacenamiento (puede fallar en navegación privada) ───────────────────
  function leer(k) { try { return localStorage.getItem(k); } catch (e) { return null; } }
  function guardar(k, v) {
    try { v == null ? localStorage.removeItem(k) : localStorage.setItem(k, v); } catch (e) {}
  }

  // ── Estilos ───────────────────────────────────────────────────────────────
  var css = [
    "#mrt{--f:#f7f6f3;--t:#1f1f1c;--te:#6b6a64;--b:#dedcd5;--a:#2f5d50;--s:#fff;--k:#b5483b;",
    " position:fixed;right:20px;bottom:20px;z-index:2147483000;font:15px/1.5 system-ui,sans-serif;color:var(--t)}",
    "@media (prefers-color-scheme:dark){#mrt{--f:#1b1b19;--t:#ecebe6;--te:#9b9a93;--b:#33332f;--a:#7fb8a6;--s:#252522;--k:#e08a7e}}",
    "#mrt *{box-sizing:border-box}",
    "#mrt .mrt-boton{display:flex;align-items:center;gap:8px;border:0;border-radius:999px;padding:12px 18px;",
    " background:var(--a);color:var(--f);font:inherit;font-weight:600;cursor:pointer;box-shadow:0 4px 16px rgba(0,0,0,.18)}",
    "#mrt .mrt-boton svg{width:18px;height:18px}",
    "#mrt.abierto .mrt-boton{display:none}",
    "#mrt .mrt-panel{display:none;flex-direction:column;width:min(400px,calc(100vw - 32px));height:min(600px,calc(100vh - 40px));",
    " background:var(--f);border:1px solid var(--b);border-radius:14px;overflow:hidden;box-shadow:0 12px 40px rgba(0,0,0,.25)}",
    "#mrt.abierto .mrt-panel{display:flex}",
    "#mrt .mrt-cab{display:flex;align-items:center;gap:6px;padding:10px 12px;border-bottom:1px solid var(--b);background:var(--s)}",
    "#mrt .mrt-cab b{flex:1;font-size:15px}",
    "#mrt .mrt-cab button,#mrt .mrt-cab a{font:inherit;font-size:13px;color:var(--te);background:none;border:0;",
    " padding:4px 8px;border-radius:6px;cursor:pointer;text-decoration:none}",
    "#mrt .mrt-cab button:hover,#mrt .mrt-cab a:hover{background:var(--f);color:var(--t)}",
    "#mrt .mrt-turnos{flex:1;overflow-y:auto;padding:14px 12px;display:flex;flex-direction:column;gap:10px}",
    "#mrt .mrt-u,#mrt .mrt-r{padding:8px 12px;border-radius:12px;max-width:88%;white-space:pre-wrap;word-wrap:break-word}",
    "#mrt .mrt-u{align-self:flex-end;background:var(--a);color:var(--f)}",
    "#mrt .mrt-r{align-self:flex-start;background:var(--s);border:1px solid var(--b)}",
    "#mrt .mrt-r.mrt-error{border-color:var(--k);color:var(--k)}",
    "#mrt .mrt-traza{margin-top:6px;padding-top:4px;border-top:1px solid var(--b);font:12px/1.4 ui-monospace,Menlo,monospace;color:var(--te);white-space:normal}",
    "#mrt .mrt-traza summary{cursor:pointer}",
    "#mrt .mrt-traza .ko{color:var(--k)}",
    "#mrt .mrt-pensando{align-self:flex-start;color:var(--te);font-size:13px}",
    "#mrt .mrt-hola{color:var(--te);font-size:14px;text-align:center;margin:auto 12px}",
    "#mrt form{display:flex;gap:8px;padding:10px;border-top:1px solid var(--b);background:var(--s)}",
    "#mrt textarea{flex:1;resize:none;font:inherit;padding:8px 10px;border:1px solid var(--b);border-radius:10px;",
    " background:var(--f);color:var(--t);max-height:120px}",
    "#mrt form button{font:inherit;border:0;border-radius:10px;padding:0 14px;background:var(--a);color:var(--f);cursor:pointer}",
    "#mrt form button:disabled{opacity:.5;cursor:default}",
    "@media (max-width:520px){#mrt{right:12px;bottom:12px}#mrt.abierto{left:0;right:0;top:0;bottom:0}",
    " #mrt.abierto .mrt-panel{width:100%;height:100%;border-radius:0;border:0}}",
  ].join("\n");
  var estilo = document.createElement("style");
  estilo.textContent = css;
  document.head.appendChild(estilo);

  // ── DOM ───────────────────────────────────────────────────────────────────
  function el(tag, attrs, texto) {
    var n = document.createElement(tag);
    for (var k in attrs || {}) n.setAttribute(k, attrs[k]);
    if (texto != null) n.textContent = texto;
    return n;
  }

  var raiz = el("div", { id: "mrt" });
  var boton = el("button", { class: "mrt-boton", type: "button", "aria-label": "Hablar con Mortimer" });
  boton.innerHTML = '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" aria-hidden="true">' +
    '<path d="M21 12a8 8 0 0 1-11.6 7.1L4 20l1-4.6A8 8 0 1 1 21 12z"/></svg>';
  boton.appendChild(document.createTextNode("Mortimer"));

  var panel = el("section", { class: "mrt-panel", role: "dialog", "aria-label": "Chat con Mortimer" });
  var cab = el("div", { class: "mrt-cab" });
  cab.appendChild(el("b", null, "Mortimer"));
  var bNueva = el("button", { type: "button", title: "Empezar una conversación nueva" }, "Nueva");
  var aHistorial = el("a", { href: CORE + "conversaciones", title: "Ver y borrar conversaciones" }, "Historial");
  var bCerrar = el("button", { type: "button", "aria-label": "Cerrar el chat", title: "Cerrar (Esc)" }, "✕");
  cab.appendChild(bNueva); cab.appendChild(aHistorial); cab.appendChild(bCerrar);

  var turnos = el("div", { class: "mrt-turnos", "aria-live": "polite" });
  var form = el("form");
  var entrada = el("textarea", { rows: "1", placeholder: "Escribe a Mortimer…", "aria-label": "Mensaje para Mortimer" });
  var enviarB = el("button", { type: "submit" }, "Enviar");
  form.appendChild(entrada); form.appendChild(enviarB);
  panel.appendChild(cab); panel.appendChild(turnos); panel.appendChild(form);
  raiz.appendChild(boton); raiz.appendChild(panel);

  // ── Estado ────────────────────────────────────────────────────────────────
  var conversacion = leer(CLAVE);
  var cargada = false;      // ya se pidió el historial de la conversación guardada
  var ocupado = false;

  function saludo() {
    turnos.textContent = "";
    turnos.appendChild(el("p", { class: "mrt-hola" },
      "Pregúntame lo que quieras. Si es algo que alguno de mis módulos sabe hacer, lo uso; si no, te lo digo."));
  }

  function alFinal() { turnos.scrollTop = turnos.scrollHeight; }

  function pintarTurno(peticion, r) {
    var hola = turnos.querySelector(".mrt-hola");
    if (hola) hola.remove();
    if (peticion != null) turnos.appendChild(el("div", { class: "mrt-u" }, peticion));
    if (!r) return;
    var burbuja = el("div", { class: "mrt-r" + (r.error ? " mrt-error" : "") }, r.texto);
    var hs = r.herramientas || [];
    if (hs.length) {
      var d = el("details", { class: "mrt-traza" });
      d.appendChild(el("summary", null, hs.map(function (h) { return (h.modulo || "?") + "." + h.herramienta; }).join(", ")));
      hs.forEach(function (h) {
        var linea = el("div", null, "→ " + (h.modulo || "?") + "." + h.herramienta + "(" + JSON.stringify(h.argumentos || {}) + ") ");
        linea.appendChild(el("span", { class: h.ok ? "" : "ko" }, h.ok ? "ok" : "error"));
        linea.appendChild(document.createTextNode(" · " + h.ms + " ms"));
        d.appendChild(linea);
      });
      burbuja.appendChild(d);
    }
    turnos.appendChild(burbuja);
  }

  function emitir(nombre, detalle) {
    document.dispatchEvent(new CustomEvent(nombre, { detail: detalle }));
  }

  function fijarConversacion(id) {
    conversacion = id || null;
    guardar(CLAVE, conversacion);
  }

  function cargarHistorial() {
    if (cargada) return Promise.resolve();
    cargada = true;
    if (!conversacion) { saludo(); return Promise.resolve(); }
    var id = conversacion;
    return fetch(CORE + "api/conversaciones/" + encodeURIComponent(id), { headers: { Accept: "application/json" } })
      .then(function (r) {
        if (r.status === 404) { if (conversacion === id) fijarConversacion(null); saludo(); return; }
        if (!r.ok) throw new Error("HTTP " + r.status);
        return r.json().then(function (datos) {
          if (conversacion !== id) return;  // el usuario empezó otra mientras tanto
          turnos.textContent = "";
          datos.turnos.forEach(function (t) {
            pintarTurno(t.peticion, { texto: t.respuesta, herramientas: t.herramientas, error: t.error });
          });
          alFinal();
        });
      })
      .catch(function () { saludo(); });
  }

  function abrir() {
    raiz.classList.add("abierto");
    guardar(CLAVE_ABIERTO, "1");
    cargarHistorial().then(alFinal);
    setTimeout(function () { entrada.focus(); }, 0);
  }

  function cerrar() {
    raiz.classList.remove("abierto");
    guardar(CLAVE_ABIERTO, null);
    boton.focus();
  }

  function olvidar() {
    var antes = conversacion;
    fijarConversacion(null);
    cargada = true;
    saludo();
    if (antes) emitir("mortimer:conversacion-borrada", { conversacion: antes });
  }

  function continuar(id) {
    fijarConversacion(id);
    cargada = false;
    abrir();
  }

  function enviar(texto) {
    texto = (texto || "").trim();
    if (!texto || ocupado) return Promise.resolve(null);
    if (!raiz.classList.contains("abierto")) abrir();
    ocupado = true;
    enviarB.disabled = true;
    pintarTurno(texto, null);
    var pensando = el("div", { class: "mrt-pensando" }, "Mortimer está pensando…");
    turnos.appendChild(pensando);
    alFinal();
    // En el servidor de referencia (CPU) una respuesta puede tardar decenas de
    // segundos: se enseña el tiempo para que no parezca colgado.
    var t0 = Date.now();
    var reloj = setInterval(function () {
      pensando.textContent = "Mortimer está pensando… " + Math.round((Date.now() - t0) / 1000) + " s";
    }, 1000);

    return fetch(CORE + "api/chat", {
      method: "POST",
      headers: { "Content-Type": "application/json", Accept: "application/json" },
      body: JSON.stringify({ texto: texto, conversacion: conversacion, canal: "web" }),
    })
      .then(function (r) {
        if (!r.ok) throw new Error("HTTP " + r.status);
        return r.json();
      })
      .then(function (r) {
        fijarConversacion(r.conversacion);
        pintarTurno(null, r);
        emitir("mortimer:respuesta", r);
        return r;
      })
      .catch(function (e) {
        pintarTurno(null, { texto: "No he podido hablar con Mortimer (" + e.message + "). Inténtalo de nuevo.", error: true });
        return null;
      })
      .finally(function () {
        clearInterval(reloj);
        pensando.remove();
        ocupado = false;
        enviarB.disabled = false;
        alFinal();
        entrada.focus();
      });
  }

  // ── Eventos ───────────────────────────────────────────────────────────────
  boton.addEventListener("click", abrir);
  bCerrar.addEventListener("click", cerrar);
  bNueva.addEventListener("click", function () { olvidar(); entrada.focus(); });
  form.addEventListener("submit", function (ev) {
    ev.preventDefault();
    var t = entrada.value;
    if (!t.trim() || ocupado) return;
    entrada.value = "";
    entrada.style.height = "";
    enviar(t);
  });
  entrada.addEventListener("keydown", function (ev) {
    if (ev.key === "Enter" && !ev.shiftKey && !ev.isComposing) {
      ev.preventDefault();
      form.requestSubmit ? form.requestSubmit() : form.dispatchEvent(new Event("submit", { cancelable: true }));
    }
  });
  entrada.addEventListener("input", function () {
    entrada.style.height = "";
    entrada.style.height = Math.min(entrada.scrollHeight, 120) + "px";
  });
  panel.addEventListener("keydown", function (ev) { if (ev.key === "Escape") cerrar(); });

  // Otra pestaña cambió de conversación: esta la sigue.
  window.addEventListener("storage", function (ev) {
    if (ev.key !== CLAVE || ev.newValue === conversacion) return;
    conversacion = ev.newValue;
    cargada = false;
    if (raiz.classList.contains("abierto")) cargarHistorial();
  });

  function montar() {
    document.body.appendChild(raiz);
    if (leer(CLAVE_ABIERTO)) abrir();
  }
  if (document.body) montar(); else document.addEventListener("DOMContentLoaded", montar);

  window.Mortimer = {
    abrir: abrir,
    cerrar: cerrar,
    enviar: enviar,
    conversacion: function () { return conversacion; },
    olvidar: olvidar,
    continuar: continuar,
  };
})();
