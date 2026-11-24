"use strict";
// Interface do MACP: HTML + JS puro, sem build. Todo dado externo entra com textContent (sem innerHTML).
const $ = (id) => document.getElementById(id);
const GRANDEZAS = {
  temperatura: { nome: "Temperatura", un: "°C" }, umidade_ar: { nome: "Umidade do ar", un: "%" },
  umidade_solo: { nome: "Umidade do solo", un: "%" }, luminosidade: { nome: "Luminosidade", un: "%" },
};
const ATUADORES = { bomba: "Bomba", led: "LED", buzzer: "Buzzer" };
const DUR_MAX = { bomba: 15, led: 60, buzzer: 60 };
// o que a estacao declarou ter (o backend completa com "tudo" se ela nunca declarou)
const TODOS = { sensores: Object.keys(GRANDEZAS), atuadores: Object.keys(ATUADORES) };
const recursosDe = (id) => (dispositivos.find((d) => d.id === id) || {}).recursos || TODOS;
const temBomba = (rec) => rec.atuadores.includes("bomba");
const SEM_DADOS_S = 30; // sem mensagem ha mais que isso: mostra aviso mesmo que o status diga online
const LIMITE_MS = 8000; // tempo maximo esperando o backend antes de considerar que ele nao respondeu
const SEM_CONEXAO = "Não foi possível conectar ao servidor";
let sessao = null, dispositivos = [], regras = [], charts = {}, timers = [];
let listasProntas = false; // selects de estacao e regras ja carregados nesta sessao
let atualizando = false;   // uma rodada de 3 s ainda em andamento

function guardar(k, v) { try { v == null ? sessionStorage.removeItem(k) : sessionStorage.setItem(k, v); } catch (e) {} }
function ler(k) { try { return sessionStorage.getItem(k); } catch (e) { return null; } }

// fetch com tempo limite: servidor fora ou travado vira o erro SEM_CONEXAO (status 0)
async function buscar(url, opts = {}) {
  const ctrl = new AbortController();
  const t = setTimeout(() => ctrl.abort(), LIMITE_MS);
  try { return await fetch(url, { ...opts, signal: ctrl.signal }); }
  catch (e) { const erro = new Error(SEM_CONEXAO); erro.status = 0; throw erro; }
  finally { clearTimeout(t); }
}

// erro da API vem como {codigo, mensagem}; validacao do proprio FastAPI (422) vem como {detail: [...]}
function textoErro(corpo, status) {
  if (corpo.codigo) return corpo.mensagem ? `${corpo.codigo}: ${corpo.mensagem}` : String(corpo.codigo);
  if (Array.isArray(corpo.detail)) return "dados_invalidos: " + corpo.detail.map((d) => (d && d.msg) || "valor inválido").join("; ");
  if (typeof corpo.detail === "string") return corpo.detail;
  return `erro ${status}`;
}

async function api(caminho, opts = {}) {
  const h = { "Content-Type": "application/json" };
  if (sessao) h.Authorization = "Bearer " + sessao.token;
  const r = await buscar("/api" + caminho, { ...opts, headers: h });
  if (r.status === 401 && sessao) { sair(); const e = new Error("sessao expirada"); e.status = 401; throw e; }
  const corpo = (await r.json().catch(() => null)) || {};
  if (!r.ok) { const e = new Error(textoErro(corpo, r.status)); e.status = r.status; e.codigo = corpo.codigo; e.corpo = corpo; throw e; }
  return corpo;
}

function el(tag, texto, classe) {
  const e = document.createElement(tag);
  if (texto != null) e.textContent = texto;
  if (classe) e.className = classe;
  return e;
}
function badge(texto, tipo) { return el("span", texto, "badge " + (tipo ? "b-" + tipo : "")); }
// o backend manda microssegundos (.123456); corta em milissegundos para qualquer navegador entender
function lerData(iso) { return new Date(String(iso).replace(/(\.\d{3})\d+/, "$1")); }
function hora(iso) { return iso ? lerData(iso).toLocaleTimeString("pt-BR") : "nunca"; }
function atras(iso) {
  if (!iso) return "nunca";
  const s = Math.max(0, Math.round((Date.now() - lerData(iso).getTime()) / 1000));
  return s < 60 ? `${s} s atrás` : s < 3600 ? `${Math.round(s / 60)} min atrás` : `${Math.round(s / 3600)} h atrás`;
}
const fmt = (v) => (v == null ? "-" : Number(v).toFixed(1));

// ---------------------------------------------------------------- login
$("form-login").addEventListener("submit", async (ev) => {
  ev.preventDefault();
  $("login-erro").textContent = "";
  try {
    sessao = await api("/login", { method: "POST",
      body: JSON.stringify({ email: $("login-email").value, senha: $("login-senha").value }) });
  } catch (e) {
    $("login-erro").textContent = e.status === 401 ? "E-mail ou senha incorretos" : e.message;
    return;
  }
  $("login-senha").value = "";
  guardar("macp", JSON.stringify(sessao));
  iniciar();
});
$("btn-sair").addEventListener("click", sair);
function sair() {
  sessao = null; guardar("macp", null);
  timers.forEach(clearInterval); timers = [];
  listasProntas = false;
  $("cmd-msg").textContent = ""; $("rg-msg").textContent = "";
  $("tela-app").hidden = true; $("tela-login").hidden = false;
}

// ---------------------------------------------------------------- inicio
async function iniciar() {
  $("tela-login").hidden = true; $("tela-app").hidden = false;
  $("usuario").textContent = `${sessao.email} (${sessao.papel})`;
  const admin = sessao.papel === "admin";
  $("aviso-papel").hidden = admin;
  $("form-comando").hidden = !admin;
  $("rg-salvar").disabled = !admin;
  limitarDuracao();
  timers.push(setInterval(atualizar, 3000), setInterval(atualizarPainel, 15000));
  await atualizar();
}

// estacoes e regras: carregadas uma vez por sessao; se falhar, a proxima rodada de 3 s tenta de novo
async function carregarListas() {
  dispositivos = await api("/dispositivos");
  regras = await api("/regras");
  for (const id of ["cmd-disp", "pn-disp", "rg-disp"]) {
    const s = $(id); s.textContent = "";
    dispositivos.forEach((d) => { const o = el("option", d.nome); o.value = d.id; s.appendChild(o); });
  }
  preencherRegra();
  opcoesAlvo();
  listasProntas = true;
  atualizarPainel();
}

// ---------------------------------------------------------------- cartoes, alertas, comandos
async function atualizar() {
  if (atualizando) return; // a rodada anterior ainda nao terminou: pula esta
  atualizando = true;
  try { await Promise.all([atualizarSaude(), atualizarDados()]); }
  finally { atualizando = false; }
}

async function atualizarSaude() {
  const sb = $("saude");
  try {
    const r = await buscar("/api/saude");
    if (!r.ok) throw new Error(r.status);
    const s = await r.json();
    sb.textContent = `banco ${s.db ? "ok" : "fora"} · broker ${s.mqtt ? "ok" : "fora"}`;
    sb.className = "badge " + (s.db && s.mqtt ? "b-ok" : "b-bad");
  } catch (err) {
    sb.textContent = "backend fora"; sb.className = "badge b-bad";
  }
}

async function atualizarDados() {
  try {
    if (!listasProntas) await carregarListas();
    const [d, c, a, e] = await Promise.all([api("/dispositivos"), api("/comandos?limite=15"),
      api("/alertas"), api("/eventos?limite=12")]);
    dispositivos = d;
    cartoes(d); tabelaComandos(c); listaAlertas(a); listaEventos(e);
    opcoesAlvo(); textosRegra(); // uma estacao pode ter acabado de declarar o que tem
  } catch (err) {
    cartoes(dispositivos); // sem resposta: redesenha o ultimo dado para o "último contato" continuar contando
  }
}

function cartoes(lista) {
  const raiz = $("cards"); raiz.textContent = "";
  for (const d of lista) {
    const idade = d.ultimo_contato ? (Date.now() - lerData(d.ultimo_contato).getTime()) / 1000 : Infinity;
    const card = el("div", null, "card");
    const cab = el("div", null, "cab");
    cab.appendChild(el("h3", d.nome));
    if (!d.online) cab.appendChild(badge("offline", "bad"));
    else if (idade > SEM_DADOS_S) cab.appendChild(badge("sem dados", "warn"));
    else cab.appendChild(badge("online", "ok"));
    card.appendChild(cab);
    const rec = d.recursos || TODOS;
    if (!temBomba(rec)) cab.appendChild(badge("sem bomba", ""));
    card.appendChild(el("p", `Último contato: ${atras(d.ultimo_contato)} · modo ${d.modo || "-"} · condição ${d.condicao || "-"} · fw ${d.fw || "-"}`, "mudo"));
    const nomes = (lista, mapa) => lista.map((k) => mapa[k]).join(", ") || "nenhum";
    card.appendChild(el("p", `Sensores: ${nomes(rec.sensores, Object.fromEntries(Object.entries(GRANDEZAS).map(([k, v]) => [k, v.nome])))}`
      + ` · Atuadores: ${nomes(rec.atuadores, ATUADORES)}${d.recursos_declarados === false ? " (padrão: a estação ainda não informou)" : ""}`, "mudo pequena"));
    if (!temBomba(rec)) card.appendChild(el("p", "Sem bomba: com o solo seco, o sistema acende o LED e toca o buzzer para alguém regar.", "mudo pequena"));
    const grade = el("div", null, "leituras"), leituras = d.leituras || {};
    for (const [g, info] of Object.entries(GRANDEZAS)) {
      if (!rec.sensores.includes(g)) continue;
      const l = leituras[g];
      const box = el("div", null, "leit");
      box.appendChild(el("span", info.nome));
      box.appendChild(el("b", l ? `${fmt(l.valor)} ${l.unidade || info.un}` : "-"));
      if (l) box.appendChild(el("span", hora(l.medido_em)));
      grade.appendChild(box);
    }
    card.appendChild(grade);
    const at = el("div", null, "atuadores"), est = d.ultimo_estado || {};
    for (const n of rec.atuadores) at.appendChild(badge(`${ATUADORES[n]}: ${est[n] || "-"}`, est[n] === "on" ? "warn" : ""));
    card.appendChild(at);
    raiz.appendChild(card);
  }
}

const COR_ESTADO = { enviado: "warn", recebido: "warn", executado: "warn", confirmado: "ok", rejeitado: "bad", falhou: "bad", expirado: "bad" };
function tabelaComandos(lista) {
  const tb = $("tab-comandos"); tb.textContent = "";
  for (const c of lista) {
    const tr = el("tr");
    const desc = c.alvo === "config" ? "config" : `${c.alvo} ${c.acao}${c.duracao_s ? " " + c.duracao_s + " s" : ""}`;
    [hora(c.enviado_em), c.dispositivo_id, desc, c.origem].forEach((t) => tr.appendChild(el("td", t)));
    const td = el("td"); td.appendChild(badge(c.estado, COR_ESTADO[c.estado])); tr.appendChild(td);
    tr.appendChild(el("td", c.motivo || ""));
    tb.appendChild(tr);
  }
  if (!lista.length) { const tr = el("tr"); const td = el("td", "Nenhum comando ainda."); td.colSpan = 6; tr.appendChild(td); tb.appendChild(tr); }
}

function listaAlertas(lista) {
  const ul = $("alertas"); ul.textContent = "";
  if (!lista.length) ul.appendChild(el("li", "Nenhum alerta ativo.", "mudo"));
  for (const a of lista) {
    const li = el("li"); li.appendChild(badge(a.tipo, "bad"));
    li.appendChild(el("span", " " + [a.dispositivo_id, a.mensagem, atras(a.aberto_em)].filter(Boolean).join(" · ")));
    ul.appendChild(li);
  }
}
function listaEventos(lista) {
  const ul = $("eventos"); ul.textContent = "";
  for (const e of lista) ul.appendChild(el("li", `${hora(e.criado_em)} · ${e.dispositivo_id || "-"} · ${e.tipo}: ${e.detalhe || ""}`));
}

// ---------------------------------------------------------------- enviar comando
// duracao maxima do contrato: bomba 15 s, LED e buzzer 60 s
// Alvo: so os atuadores que a estacao escolhida tem (a estacao sem bomba nao oferece "Bomba")
function opcoesAlvo() {
  const s = $("cmd-alvo"), atual = s.value, lista = recursosDe($("cmd-disp").value).atuadores;
  if ([...s.options].map((o) => o.value).join() === lista.join()) return; // nada mudou: nao mexe na escolha
  s.textContent = "";
  for (const a of lista) { const o = el("option", `${ATUADORES[a]} (1 a ${DUR_MAX[a]} s)`); o.value = a; s.appendChild(o); }
  if (lista.includes(atual)) s.value = atual;
  limitarDuracao();
}
$("cmd-disp").addEventListener("change", opcoesAlvo);

// "Desligar" nao leva duracao: o campo fica desabilitado (e fora da validacao do formulario)
function limitarDuracao() {
  const max = DUR_MAX[$("cmd-alvo").value] || 60;
  $("cmd-dur").max = max; if (Number($("cmd-dur").value) > max) $("cmd-dur").value = max;
  $("cmd-dur").disabled = $("cmd-acao").value === "off";
}
$("cmd-alvo").addEventListener("change", limitarDuracao);
$("cmd-acao").addEventListener("change", limitarDuracao);
$("form-comando").addEventListener("submit", async (ev) => {
  ev.preventDefault();
  const msg = $("cmd-msg"); msg.textContent = "Enviando...";
  try {
    const acao = $("cmd-acao").value;
    const corpo = { dispositivoId: $("cmd-disp").value, alvo: $("cmd-alvo").value, acao };
    if (acao === "on") corpo.duracaoS = Number($("cmd-dur").value);
    const c = await api("/comandos", { method: "POST", body: JSON.stringify(corpo) });
    msg.textContent = `Comando ${c.comando_id} registrado como "${c.estado}". Acompanhe o estado na tabela abaixo.`;
  } catch (e) {
    if (!sessao) return; // 401: ja voltou para o login
    const c = e.corpo && e.corpo.comando; // 503 broker_indisponivel: o comando foi gravado como falhou
    msg.textContent = c ? `Comando ${c.comando_id} registrado como "${c.estado}" (${e.codigo}).`
      : `Comando não enviado: ${e.message}`;
  }
  atualizar();
});

// ---------------------------------------------------------------- painel analitico (R14)
$("pn-disp").addEventListener("change", atualizarPainel);
$("pn-horas").addEventListener("change", atualizarPainel);
async function atualizarPainel() {
  const disp = $("pn-disp").value, horas = $("pn-horas").value;
  if (!disp) return; // estacoes ainda nao carregadas
  let p;
  try { p = await api(`/painel?dispositivo_id=${encodeURIComponent(disp)}&horas=${horas}`); }
  catch (e) { return; }
  if (disp !== $("pn-disp").value || horas !== $("pn-horas").value) return; // a selecao mudou durante a espera
  const resumo = $("resumo"); resumo.textContent = "";
  const caixa = (t, v) => { const b = el("div", null, "leit"); b.appendChild(el("span", t)); b.appendChild(el("b", v)); resumo.appendChild(b); };
  const rec = p.recursos || TODOS;
  if (temBomba(rec)) {
    caixa("Irrigações confirmadas (comandos)", String(p.irrigacoes));
    if (p.acionamentos_bomba != null) caixa("Bomba ligou (inclui regra local)", String(p.acionamentos_bomba));
  }
  caixa("Alertas no período", String(p.alertas));
  for (const [g, info] of Object.entries(GRANDEZAS)) {
    if (!rec.sensores.includes(g)) continue;
    const a = p.grandezas[g];
    caixa(`${info.nome} (mín / média / máx)`, a.n ? `${fmt(a.minimo)} / ${fmt(a.media)} / ${fmt(a.maximo)} ${a.unidade || info.un}` : "sem dados");
  }
  const raiz = $("graficos");
  for (const [g, info] of Object.entries(GRANDEZAS)) {
    if (!charts[g]) {
      const wrap = el("div", null, "g"); wrap.id = "g-" + g; const cv = document.createElement("canvas"); wrap.appendChild(cv); raiz.appendChild(wrap);
      charts[g] = new Chart(cv, { type: "line", data: { labels: [], datasets: [
        { label: `${info.nome} (${info.un})`, data: [], borderColor: "#1f6feb", pointRadius: 1.5, tension: 0.2 },
        { label: "média móvel", data: [], borderColor: "#e08a00", borderDash: [5, 4], pointRadius: 0, tension: 0.3 }] },
        options: { responsive: true, maintainAspectRatio: false, animation: false,
          plugins: { title: { display: true, text: info.nome } }, scales: { y: { beginAtZero: false } } } });
    }
    $("g-" + g).hidden = !rec.sensores.includes(g); // grafico so dos sensores que a estacao tem
    const serie = p.grandezas[g].serie, movel = p.grandezas[g].media_movel, ch = charts[g];
    ch.data.labels = serie.map((s) => lerData(s.t).toLocaleTimeString("pt-BR", { hour: "2-digit", minute: "2-digit" }));
    ch.data.datasets[0].data = serie.map((s) => s.valor);
    ch.data.datasets[1].data = movel.map((m) => m.valor); // mesma ordem e tamanho da serie
    ch.update();
  }
}

// ---------------------------------------------------------------- regra remota
function preencherRegra() {
  const r = regras.find((x) => x.dispositivo_id === $("rg-disp").value) || {}; // sem regra: campos vazios
  $("rg-limiar").value = r.limiar ?? ""; $("rg-janela").value = r.janela_min ?? ""; $("rg-intervalo").value = r.intervalo_min_s ?? "";
  $("rg-dur").value = r.duracao_s ?? ""; $("rg-ativa").checked = !!r.ativa;
  textosRegra();
}
// a mesma regra age diferente na estacao sem bomba: explica isso no formulario
function textosRegra() {
  const com = temBomba(recursosDe($("rg-disp").value));
  $("rg-desc").textContent = com
    ? "Se a umidade do solo ficar abaixo do limiar durante a janela, o backend envia um comando de bomba."
    : "Esta estação não tem bomba: com o solo abaixo do limiar, o backend acende o LED e toca o buzzer (repete a cada intervalo mínimo) até alguém regar.";
  $("rg-dur-rotulo").textContent = com ? "Duração da bomba (s)" : "Duração do bipe do buzzer (s)";
}
$("rg-disp").addEventListener("change", preencherRegra);
$("form-regra").addEventListener("submit", async (ev) => {
  ev.preventDefault();
  const msg = $("rg-msg"); msg.textContent = "Salvando...";
  try {
    await api(`/regras/${encodeURIComponent($("rg-disp").value)}`, { method: "PUT", body: JSON.stringify({
      limiar: Number($("rg-limiar").value), janela_min: Number($("rg-janela").value),
      intervalo_min_s: Number($("rg-intervalo").value), duracao_s: Number($("rg-dur").value), ativa: $("rg-ativa").checked }) });
  } catch (e) {
    if (!sessao) return; // 401: ja voltou para o login
    msg.textContent = e.status === 404 || e.status === 405
      ? "Não foi possível salvar: o backend ainda não tem o endpoint de regras."
      : "Não foi possível salvar: " + e.message;
    return;
  }
  msg.textContent = "Regra salva.";
  try { regras = await api("/regras"); } catch (e) { /* o formulario ja mostra o que foi salvo */ }
});

// ---------------------------------------------------------------- partida
const salva = ler("macp");
if (salva) { try { sessao = JSON.parse(salva); iniciar(); } catch (e) { sair(); } }
