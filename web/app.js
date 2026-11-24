"use strict";
const $ = (id) => document.getElementById(id);

const GRANDEZAS = {
  temperatura: { nome: "Temperatura", un: "°C", icone: "termometro" },
  umidade_ar: { nome: "Umidade do ar", un: "%", icone: "nuvem" },
  umidade_solo: { nome: "Umidade do solo", un: "%", icone: "gota" },
  luminosidade: { nome: "Luminosidade", un: "%", icone: "sol" },
};
const ATUADORES = { bomba: "Bomba", led: "LED", buzzer: "Buzzer" };
const FEMININO = new Set(["bomba"]);
const DUR_MAX = { bomba: 15, led: 60, buzzer: 60 };
const TODOS = { sensores: Object.keys(GRANDEZAS), atuadores: Object.keys(ATUADORES) };
const CONDICOES = {
  adequado: { nome: "Condição adequada", classe: "c-adequado" },
  atencao: { nome: "Atenção", classe: "c-atencao" },
  inadequado: { nome: "Condição inadequada", classe: "c-inadequado" },
};
const MODOS = {
  automatico: { nome: "Automático", tipo: "acc", dica: "A estação e a regra do backend agem sozinhas." },
  manual: { nome: "Manual", tipo: "warn", dica: "Uma pessoa mandou comando há menos de 60 s: a irrigação automática está pausada." },
  seguro: { nome: "Modo seguro", tipo: "bad", dica: "Falha de sensor (ou acabou de ligar): a bomba fica bloqueada até as leituras voltarem." },
};
const SITUACOES = {
  enviado: ["Enviado", "warn"], recebido: ["Recebido", "warn"], executado: ["Executado", "warn"],
  confirmado: ["Confirmado", "ok"], rejeitado: ["Rejeitado", "bad"], falhou: ["Falhou", "bad"], expirado: ["Sem resposta", "bad"],
};
const FINAIS = new Set(["confirmado", "rejeitado", "falhou", "expirado"]);
const MOTIVOS = {
  intertravamento_intervalo: "A bomba ligou há pouco: aguarde o intervalo mínimo entre irrigações.",
  intertravamento_duracao: "Duração acima do máximo configurado na estação.",
  modo_seguro: "Estação em modo seguro ou sem leitura do solo: a bomba fica bloqueada.",
  comando_duplicado: "Comando repetido, ignorado pela estação.",
  alvo_invalido: "Esta estação não tem esse atuador.",
  valor_fora_da_faixa: "Valor fora da faixa permitida.",
  json_invalido: "Mensagem inválida.",
  versao_nao_suportada: "Versão do contrato não suportada pela estação.",
  bomba_bloqueada: "Bomba bloqueada: reservatório vazio? Reabasteça e clique em \"Reabasteci\" no alerta.",
  alerta_automatico: "Este alerta se resolve sozinho quando a causa some.",
  alerta_ja_resolvido: "Este alerta já foi resolvido.",
  broker_indisponivel: "Broker MQTT fora do ar: o comando não saiu.",
  dispositivo_offline: "A estação está offline.",
  dispositivo_desconhecido: "Estação desconhecida.",
  sem_permissao: "Seu perfil não tem permissão para isso.",
  credenciais_invalidas: "E-mail ou senha incorretos.",
};
const EVENTOS = {
  status: "Conexão", falha_sensor: "Falha de sensor", mensagem_invalida: "Mensagem inválida",
  firmware_atualizado: "Firmware atualizado", regra_alterada: "Regra alterada", comando_falhou: "Comando falhou",
  comando_expirado: "Comando sem resposta", bomba: "Bomba", recursos: "Equipamento da estação",
  modo: "Modo", condicao: "Condição", alarme: "Alarme", reservatorio: "Reservatório",
};
const ALERTAS = { dispositivo_offline: "Estação offline", solo_seco: "Solo seco", reservatorio_vazio: "Reservatório vazio?" };

const SEM_DADOS_S = 30;
const LIMITE_MS = 8000;
const SEM_CONEXAO = "Não foi possível conectar ao servidor";

let sessao = null, dispositivos = [], regras = [], charts = {}, timers = [];
let listasProntas = false, atualizando = false, ultimaOk = null;
let alertasVistos = null;
const assinaturas = {};
const ocupado = new Set();

const recursosDe = (id) => (dispositivos.find((d) => d.id === id) || {}).recursos || TODOS;
const nomeDe = (id) => (dispositivos.find((d) => d.id === id) || {}).nome || id;
const temBomba = (rec) => rec.atuadores.includes("bomba");
const admin = () => sessao && sessao.papel === "admin";

function guardar(k, v) { try { v == null ? sessionStorage.removeItem(k) : sessionStorage.setItem(k, v); } catch (e) {} }
function ler(k) { try { return sessionStorage.getItem(k); } catch (e) { return null; } }

async function buscar(url, opts = {}) {
  const ctrl = new AbortController();
  const t = setTimeout(() => ctrl.abort(), LIMITE_MS);
  try { return await fetch(url, { ...opts, signal: ctrl.signal }); }
  catch (e) { const erro = new Error(SEM_CONEXAO); erro.status = 0; throw erro; }
  finally { clearTimeout(t); }
}

function textoErro(corpo, status) {
  if (corpo.codigo === "valor_fora_da_faixa" && corpo.mensagem) return `Valor inválido: ${corpo.mensagem}.`;
  if (corpo.codigo && MOTIVOS[corpo.codigo]) return MOTIVOS[corpo.codigo];
  if (corpo.codigo) return corpo.mensagem ? `${corpo.codigo}: ${corpo.mensagem}` : String(corpo.codigo);
  if (Array.isArray(corpo.detail)) return "Dados inválidos: " + corpo.detail.map((d) => (d && d.msg) || "valor inválido").join("; ");
  if (typeof corpo.detail === "string") return corpo.detail;
  return `Erro ${status} no servidor.`;
}

async function api(caminho, opts = {}) {
  const h = { "Content-Type": "application/json" };
  if (sessao) h.Authorization = "Bearer " + sessao.token;
  const r = await buscar("/api" + caminho, { ...opts, headers: h });
  if (r.status === 401 && sessao) { sair("Sua sessão expirou. Entre de novo."); const e = new Error("sessao expirada"); e.status = 401; throw e; }
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
function badge(texto, tipo, ponto) {
  const b = el("span", null, "badge" + (tipo ? " b-" + tipo : ""));
  if (ponto) b.appendChild(el("i", null, "ponto" + (ponto === "vivo" ? " vivo" : "")));
  b.appendChild(document.createTextNode(texto));
  return b;
}
function lerData(iso) { return new Date(String(iso).replace(/(\.\d{3})\d+/, "$1")); }
function hora(iso) { return iso ? lerData(iso).toLocaleTimeString("pt-BR") : "nunca"; }
function dataHora(iso) { return iso ? lerData(iso).toLocaleString("pt-BR") : ""; }
function atras(iso) {
  if (!iso) return "nunca";
  const s = Math.max(0, Math.round((Date.now() - lerData(iso).getTime()) / 1000));
  if (s < 5) return "agora";
  return s < 60 ? `há ${s} s` : s < 3600 ? `há ${Math.round(s / 60)} min` : s < 86400 ? `há ${Math.round(s / 3600)} h` : `há ${Math.round(s / 86400)} d`;
}
function tempo(iso, prefixo = "", classe = "tempo") {
  const s = el("span", prefixo + atras(iso), classe);
  if (iso) { s.dataset.ts = iso; s.dataset.prefixo = prefixo; s.title = dataHora(iso); }
  return s;
}
function relogio() {
  document.querySelectorAll("[data-ts]").forEach((n) => { n.textContent = (n.dataset.prefixo || "") + atras(n.dataset.ts); });
  if (ultimaOk) $("atualizado").textContent = "dados " + atras(new Date(ultimaOk).toISOString());
}
const fmt = (v, casas = 1) => (v == null ? "–" : Number(v).toFixed(casas).replace(".", ","));

const ICONES = {
  termometro: "M14 14.76V3.5a2.5 2.5 0 0 0-5 0v11.26a4.5 4.5 0 1 0 5 0z",
  nuvem: "M17.5 19H9a7 7 0 1 1 6.71-9h1.79a4.5 4.5 0 1 1 0 9z",
  gota: "M12 2.69l5.66 5.66a8 8 0 1 1-11.31 0z",
  sol: "M12 8a4 4 0 1 0 0 8a4 4 0 1 0 0-8z M12 2v2 M12 20v2 M4.93 4.93l1.41 1.41 M17.66 17.66l1.41 1.41 M2 12h2 M20 12h2 M6.34 17.66l-1.41 1.41 M19.07 4.93l-1.41 1.41",
};
function icone(nome) {
  const ns = "http://www.w3.org/2000/svg";
  const svg = document.createElementNS(ns, "svg");
  svg.setAttribute("viewBox", "0 0 24 24"); svg.setAttribute("fill", "none"); svg.setAttribute("stroke", "currentColor");
  svg.setAttribute("stroke-width", "2"); svg.setAttribute("stroke-linecap", "round"); svg.setAttribute("stroke-linejoin", "round");
  svg.setAttribute("aria-hidden", "true");
  const p = document.createElementNS(ns, "path"); p.setAttribute("d", ICONES[nome] || ""); svg.appendChild(p);
  return svg;
}

function toast(texto, tipo = "", ms = 5000) {
  const t = el("div", texto, "toast " + tipo);
  $("toasts").appendChild(t);
  setTimeout(() => t.remove(), ms);
}

$("ver-senha").addEventListener("click", () => {
  const i = $("login-senha"), mostrar = i.type === "password";
  i.type = mostrar ? "text" : "password";
  $("ver-senha").textContent = mostrar ? "ocultar" : "mostrar";
  $("ver-senha").setAttribute("aria-pressed", String(mostrar));
});
$("form-login").addEventListener("submit", async (ev) => {
  ev.preventDefault();
  const erro = $("login-erro"), botao = $("btn-entrar");
  erro.textContent = "";
  if (!$("login-email").value || !$("login-senha").value) { erro.textContent = "Preencha e-mail e senha."; return; }
  botao.disabled = true; botao.textContent = "Entrando…";
  try {
    sessao = await api("/login", { method: "POST",
      body: JSON.stringify({ email: $("login-email").value.trim(), senha: $("login-senha").value }) });
  } catch (e) {
    erro.textContent = e.message;
    return;
  } finally { botao.disabled = false; botao.textContent = "Entrar"; }
  $("login-senha").value = "";
  guardar("macp", JSON.stringify(sessao));
  iniciar();
});
$("btn-sair").addEventListener("click", () => sair());
function sair(aviso) {
  sessao = null; guardar("macp", null);
  timers.forEach(clearInterval); timers = [];
  listasProntas = false; alertasVistos = null; ultimaOk = null;
  Object.keys(assinaturas).forEach((k) => delete assinaturas[k]);
  $("rg-msg").textContent = ""; $("cmd-acomp").hidden = true;
  $("tela-app").hidden = true; $("tela-login").hidden = false;
  $("login-erro").textContent = aviso || "";
  $("login-email").focus();
}

async function iniciar() {
  $("tela-login").hidden = true; $("tela-app").hidden = false;
  const u = $("usuario"); u.textContent = "";
  u.appendChild(el("b", sessao.email)); u.appendChild(document.createTextNode(" · " + (admin() ? "administrador" : "operador")));
  $("aviso-papel").hidden = admin();
  $("form-comando").hidden = !admin();
  $("cmd-sem-permissao").hidden = admin();
  for (const id of ["rg-limiar", "rg-janela", "rg-intervalo", "rg-dur", "rg-ativa", "rg-salvar"]) $(id).disabled = !admin();
  limitarDuracao();
  timers.push(setInterval(atualizar, 3000), setInterval(atualizarPainel, 15000), setInterval(relogio, 1000));
  await atualizar();
}

async function carregarListas() {
  dispositivos = await api("/dispositivos");
  regras = await api("/regras");
  for (const id of ["cmd-disp", "pn-disp", "rg-disp"]) {
    const s = $(id), atual = s.value; s.textContent = "";
    dispositivos.forEach((d) => { const o = el("option", d.nome); o.value = d.id; s.appendChild(o); });
    if (atual) s.value = atual;
  }
  preencherRegra();
  listasProntas = true;
  atualizarPainel();
}

async function atualizar() {
  if (atualizando) return;
  atualizando = true;
  try { await Promise.all([atualizarSaude(), atualizarDados()]); }
  finally { atualizando = false; }
}

async function atualizarSaude() {
  const sb = $("saude"), txt = sb.querySelector("span"), ponto = sb.querySelector(".ponto");
  try {
    const r = await buscar("/api/saude");
    if (!r.ok) throw new Error(r.status);
    const s = await r.json();
    const ok = s.db && s.mqtt;
    txt.textContent = ok ? "Sistema operando" : `Atenção: ${[!s.db && "banco fora", !s.mqtt && "broker fora"].filter(Boolean).join(" e ")}`;
    sb.className = "pilula " + (ok ? "b-ok" : "b-bad");
    ponto.className = "ponto" + (ok ? " vivo" : "");
    $("faixa-offline").hidden = true;
  } catch (err) {
    txt.textContent = "Servidor fora do ar"; sb.className = "pilula b-bad"; ponto.className = "ponto";
    $("faixa-offline").hidden = false;
  }
}

async function atualizarDados() {
  try {
    if (!listasProntas) await carregarListas();
    const [d, c, a, e] = await Promise.all([api("/dispositivos"), api("/comandos?limite=15"),
      api("/alertas"), api("/eventos?limite=30")]);
    dispositivos = d;
    ultimaOk = Date.now();
    kpis(d, a, c); cartoes(d); tabelaComandos(c); listaAlertas(a); listaEventos(e);
    opcoesAlvo(); textosRegra();
    relogio();
  } catch (err) {
    if (dispositivos.length) cartoes(dispositivos);
  }
}

function kpis(lista, alertas, comandos) {
  const raiz = $("kpis"); raiz.textContent = "";
  const caixa = (rot, valor, extra, classe) => {
    const k = el("div", null, "kpi" + (classe ? " " + classe : ""));
    k.appendChild(el("span", rot)); const b = el("b", valor); k.appendChild(b);
    if (extra) b.appendChild(el("small", extra));
    raiz.appendChild(k);
  };
  const online = lista.filter((d) => d.online).length;
  caixa("Estações online", String(online), `de ${lista.length}`, online < lista.length ? "alerta" : "");
  caixa("Alertas ativos", String(alertas.length), "", alertas.length ? "alerta" : "");
  const seco = lista.filter((d) => avaliar(d).sensores.umidade_solo?.status === "bad").length;
  caixa("Precisam de água", String(seco), seco === 1 ? "estação" : "estações", seco ? "alerta" : "");
  const pend = comandos.filter((c) => !FINAIS.has(c.estado)).length;
  caixa("Comandos em andamento", String(pend));
}

function limiarDe(d) {
  const r = regras.find((x) => x.dispositivo_id === d.id);
  return Number(r?.limiar ?? d.ultimo_estado?.limiarSoloSecoPct ?? 30);
}

function avaliar(d) {
  const rec = d.recursos || TODOS, l = d.leituras || {}, limiar = limiarDe(d);
  const sensores = {}, motivos = [];
  const marcar = (g, status, motivo) => { sensores[g] = { status }; if (motivo) motivos.push(motivo); };
  for (const g of rec.sensores) {
    const v = l[g]?.valor;
    if (v == null) { sensores[g] = { status: "" }; continue; }
    if (g === "umidade_solo") {
      if (v < limiar) marcar(g, "bad", temBomba(rec) ? `Solo seco (${fmt(v, 0)}% < ${limiar}%): a irrigação automática cuida disso` : `Solo seco (${fmt(v, 0)}% < ${limiar}%): regue a planta`);
      else if (v < limiar + 10) marcar(g, "warn", "Solo secando");
      else marcar(g, "ok");
    } else if (g === "temperatura") {
      if (v > 32) marcar(g, "bad", `Calor demais (${fmt(v)} °C)`);
      else if (v < 10) marcar(g, "bad", `Frio demais (${fmt(v)} °C)`);
      else marcar(g, "ok");
    } else if (g === "luminosidade") {
      if (v < 20) marcar(g, "warn", "Pouca luz"); else marcar(g, "ok");
    } else if (g === "umidade_ar") {
      if (v < 30) marcar(g, "warn", "Ar seco"); else if (v > 85) marcar(g, "warn", "Ar muito úmido"); else marcar(g, "ok");
    }
  }
  return { sensores, motivos, limiar };
}

function situacaoConexao(d) {
  const idade = d.ultimo_contato ? (Date.now() - lerData(d.ultimo_contato).getTime()) / 1000 : Infinity;
  if (!d.online) return "offline";
  return idade > SEM_DADOS_S ? "sem_dados" : "online";
}

function cartoes(lista) {
  const raiz = $("cards");
  raiz.querySelectorAll(".esqueleto").forEach((n) => n.remove());
  if (!lista.length) { raiz.textContent = ""; raiz.appendChild(el("p", "Nenhuma estação cadastrada.", "mudo")); return; }
  for (const d of lista) {
    const assin = JSON.stringify([d.online, situacaoConexao(d), d.modo, d.condicao, d.fw, d.recursos, d.ultimo_estado,
      Object.fromEntries(Object.entries(d.leituras || {}).map(([g, x]) => [g, x.valor])), limiarDe(d), admin(),
      [...ocupado].filter((k) => k.startsWith(d.id + ":"))]);
    let card = raiz.querySelector(`[data-estacao="${CSS.escape(d.id)}"]`);
    if (card && assinaturas[d.id] === assin) continue;
    assinaturas[d.id] = assin;
    const novo = cartao(d);
    if (card) card.replaceWith(novo); else raiz.appendChild(novo);
  }
}

function cartao(d) {
  const rec = d.recursos || TODOS, est = d.ultimo_estado || {}, av = avaliar(d), conn = situacaoConexao(d);
  const card = el("article", null, "card estacao" + (conn === "offline" ? " off" : ""));
  card.dataset.estacao = d.id;

  const cab = el("div", null, "cab");
  const h = el("h3", d.nome); h.appendChild(el("small", d.id)); cab.appendChild(h);
  if (!temBomba(rec)) { const b = badge("sem bomba", ""); b.title = "Com o solo seco, esta estação avisa por LED e buzzer"; cab.appendChild(b); }
  if (temBomba(rec) && Number(est.bombaBloqueada) === 1) { const b = badge("bomba bloqueada", "bad"); b.title = "Reservatório vazio? Reabasteça e clique em \"Reabasteci\" no alerta"; cab.appendChild(b); }
  if (d.modo && MODOS[d.modo] && d.modo !== "automatico") { const m = MODOS[d.modo]; const b = badge(m.nome, m.tipo); b.title = m.dica; cab.appendChild(b); }
  cab.appendChild(conn === "online" ? badge("online", "ok", "vivo") : conn === "sem_dados" ? badge("sem dados", "warn", true) : badge("offline", "bad", true));
  card.appendChild(cab);

  const cond = el("div", null, "condicao");
  if (conn === "offline") {
    cond.appendChild(el("b", "Estação desconectada"));
    cond.appendChild(tempo(d.ultimo_contato, "Último contato ", ""));
  } else if (!d.condicao) {
    cond.appendChild(el("b", "Aguardando a primeira leitura…"));
  } else {
    const c = CONDICOES[d.condicao] || { nome: d.condicao, classe: "" };
    cond.classList.add(c.classe);
    cond.appendChild(el("b", c.nome));
    const motivos = av.motivos.length ? av.motivos : d.condicao === "adequado" ? ["Tudo certo para a planta"] : [];
    for (const m of motivos) cond.appendChild(el("span", m));
    if (d.modo === "seguro") cond.appendChild(el("span", MODOS.seguro.dica));
  }
  card.appendChild(cond);

  const grade = el("div", null, "leituras");
  for (const g of rec.sensores) {
    const info = GRANDEZAS[g], l = (d.leituras || {})[g], st = av.sensores[g]?.status;
    const box = el("div", null, "leit" + (st ? " s-" + st : ""));
    const rot = el("span", null, "rot"); rot.appendChild(icone(info.icone)); rot.appendChild(document.createTextNode(info.nome));
    box.appendChild(rot);
    const v = el("b", l ? fmt(l.valor) : "–"); if (l) v.appendChild(el("small", l.unidade || info.un)); box.appendChild(v);
    if (l && info.un === "%") { const bar = el("div", null, "barra"); const i = el("i"); i.style.width = Math.max(0, Math.min(100, l.valor)) + "%"; bar.appendChild(i); box.appendChild(bar); }
    box.appendChild(l ? tempo(l.medido_em, "", "quando") : el("span", "sem leitura", "quando"));
    if (g === "umidade_solo") box.title = `Abaixo de ${av.limiar}% o solo é considerado seco`;
    grade.appendChild(box);
  }
  card.appendChild(grade);

  const at = el("div", null, "atuadores");
  for (const n of rec.atuadores) {
    const ligado = est[n] === "on";
    const a = FEMININO.has(n) ? "a" : "o";
    at.appendChild(badge(`${ATUADORES[n]} ${ligado ? "ligad" + a : est[n] === "off" ? "desligad" + a : "–"}`, ligado ? "warn" : "", ligado ? "vivo" : null));
  }
  card.appendChild(at);

  if (admin()) {
    const acoes = el("div", null, "acoes");
    const podeAgir = d.online;
    const botao = (rotulo, chave, classe, fazer) => {
      const b = el("button", ocupado.has(`${d.id}:${chave}`) ? "Enviando…" : rotulo, classe);
      b.type = "button"; b.disabled = !podeAgir || ocupado.has(`${d.id}:${chave}`);
      if (!podeAgir) b.title = "Estação offline";
      b.addEventListener("click", () => acaoRapida(d, chave, fazer));
      acoes.appendChild(b);
    };
    if (temBomba(rec)) botao("Irrigar 5 s", "irrigar", "", [["bomba", "on", 5]]);
    const avisos = ["led", "buzzer"].filter((a) => rec.atuadores.includes(a));
    if (avisos.length) botao(avisos.length === 2 ? "Alertar (LED + buzzer)" : `Acionar ${ATUADORES[avisos[0]]}`, "alertar", "sec", avisos.map((a) => [a, "on", a === "buzzer" ? 3 : 8]));
    const ligados = rec.atuadores.filter((a) => est[a] === "on");
    if (ligados.length) botao("Desligar tudo", "desligar", "perigo", ligados.map((a) => [a, "off", null]));
    card.appendChild(acoes);
  }

  const rod = el("div", null, "rodape");
  rod.appendChild(tempo(d.ultimo_contato, "Último contato ", ""));
  if (d.fw) rod.appendChild(el("span", "firmware " + d.fw));
  if (d.recursos_declarados === false) rod.appendChild(el("span", "equipamento ainda não informado pela estação"));
  card.appendChild(rod);
  return card;
}

async function acaoRapida(d, chave, passos) {
  const k = `${d.id}:${chave}`;
  if (ocupado.has(k)) return;
  ocupado.add(k); cartoes(dispositivos);
  try {
    const enviados = [];
    for (const [alvo, acao, dur] of passos) {
      const corpo = { dispositivoId: d.id, alvo, acao };
      if (dur) corpo.duracaoS = dur;
      enviados.push(await api("/comandos", { method: "POST", body: JSON.stringify(corpo) }));
    }
    const finais = await Promise.all(enviados.map((c) => seguir(c.comando_id)));
    const ruim = finais.find((c) => c.estado !== "confirmado");
    if (ruim) toast(`${d.nome}: ${descricao(ruim)} — ${SITUACOES[ruim.estado]?.[0] || ruim.estado}. ${MOTIVOS[ruim.motivo] || ruim.motivo || ""}`, "bad", 8000);
    else toast(`${d.nome}: ${finais.map(descricao).join(" e ")} confirmado pela estação.`, "ok");
  } catch (e) {
    if (sessao) toast(`${d.nome}: ${e.message}`, "bad", 8000);
  } finally {
    ocupado.delete(k); atualizar();
  }
}

const pausa = (ms) => new Promise((r) => setTimeout(r, ms));
async function seguir(id, aoMudar) {
  let ultimo = null, c = null;
  for (let i = 0; i < 40 && sessao; i++) {
    try { c = await api(`/comandos/${encodeURIComponent(id)}`); } catch (e) { await pausa(1000); continue; }
    if (c.estado !== ultimo) { ultimo = c.estado; if (aoMudar) aoMudar(c); }
    if (FINAIS.has(c.estado)) return c;
    await pausa(1000);
  }
  return c || { comando_id: id, estado: "expirado" };
}

function descricao(c) {
  if (c.alvo === "config") return "Configuração";
  return `${ATUADORES[c.alvo] || c.alvo} ${c.acao === "on" ? "ligar" : "desligar"}${c.duracao_s ? ` por ${c.duracao_s} s` : ""}`;
}

function desenharEtapas(c) {
  const box = $("cmd-acomp"); box.hidden = false; box.textContent = "";
  const sit = SITUACOES[c.estado] || [c.estado, ""];
  const p = el("p"); p.appendChild(el("b", `${descricao(c)} · ${nomeDe(c.dispositivo_id)}`));
  p.appendChild(document.createTextNode(" — " + (c.estado === "confirmado" ? "a estação executou e confirmou." :
    FINAIS.has(c.estado) ? (MOTIVOS[c.motivo] || c.motivo || sit[0]) : "aguardando a estação…")));
  box.appendChild(p);
  const ol = el("ol", null, "etapas");
  const ordem = ["enviado", "recebido", "executado", "confirmado"];
  const idx = ordem.indexOf(c.estado);
  const erroEm = idx >= 0 ? -1 : c.estado === "falhou" ? (c.motivo === "broker_indisponivel" ? 0 : c.executado_em ? 3 : 2) : 1;
  ordem.forEach((e, i) => {
    let cls = "", rotulo = SITUACOES[e][0];
    if (erroEm >= 0) { if (i < erroEm) cls = "feita"; else if (i === erroEm) { cls = "erro"; rotulo = sit[0]; } }
    else if (i <= idx) cls = "feita";
    else if (i === idx + 1) cls = "atual";
    ol.appendChild(el("li", rotulo, cls));
  });
  box.appendChild(ol);
}

function listaAlertas(lista) {
  const ul = $("alertas"); ul.textContent = "";
  if (!lista.length) ul.appendChild(el("li", "Nenhum alerta ativo. Tudo em ordem.", "vazio"));
  for (const a of lista) {
    const li = el("li");
    li.appendChild(badge(ALERTAS[a.tipo] || a.tipo, "bad"));
    const msg = (a.mensagem || "").replace(a.dispositivo_id + " ", "").replace("umidade_solo", "umidade do solo");
    li.appendChild(el("span", `${nomeDe(a.dispositivo_id)}${msg ? " — " + msg : ""}`));
    li.appendChild(tempo(a.aberto_em, "aberto "));
    if (a.tipo === "reservatorio_vazio" && admin()) {
      const b = el("button", "Reabasteci", "sec");
      b.title = "Resolve o alerta e libera a bomba da estação";
      b.onclick = async () => {
        b.disabled = true;
        try {
          const r = await api(`/alertas/${a.id}/resolver`, { method: "POST" });
          toast(r.desbloqueioEnviado ? "Bomba liberada na estação." : "Alerta resolvido. A bomba será liberada quando a estação voltar.", "ok");
          atualizarDados();
        } catch (err) { toast(err.message, "bad"); b.disabled = false; }
      };
      li.appendChild(b);
    }
    ul.appendChild(li);
  }
  if (alertasVistos) for (const a of lista) if (!alertasVistos.has(a.id)) toast(`${ALERTAS[a.tipo] || a.tipo}: ${nomeDe(a.dispositivo_id)}`, "bad", 8000);
  alertasVistos = new Set(lista.map((a) => a.id));
}

function tabelaComandos(lista) {
  const tb = $("tab-comandos"); tb.textContent = "";
  for (const c of lista) {
    const tr = el("tr");
    const td0 = el("td"); td0.appendChild(tempo(c.enviado_em, "", "")); tr.appendChild(td0);
    tr.appendChild(el("td", nomeDe(c.dispositivo_id)));
    tr.appendChild(el("td", descricao(c)));
    tr.appendChild(el("td", c.origem === "regra" ? "Regra automática" : "Pessoa"));
    const sit = SITUACOES[c.estado] || [c.estado, ""];
    const td = el("td"); td.appendChild(badge(sit[0], sit[1], !FINAIS.has(c.estado) ? "vivo" : null)); tr.appendChild(td);
    tr.appendChild(el("td", c.motivo ? (MOTIVOS[c.motivo] || c.motivo) : "", "detalhe"));
    tb.appendChild(tr);
  }
  if (!lista.length) { const tr = el("tr"); const td = el("td", "Nenhum comando ainda.", "mudo"); td.colSpan = 6; tr.appendChild(td); tb.appendChild(tr); }
}

function textoEvento(e) {
  if (e.tipo === "status") return e.detalhe === "online" ? "conectou ao broker" : "desconectou (LWT)";
  const offline = e.detalhe?.includes("offline") ? " (sem conexão, enviado depois)" : "";
  if (e.tipo === "bomba") return (e.detalhe?.startsWith("on") ? "bomba ligou" : "bomba desligou") + offline;
  if (e.tipo === "modo") return `modo ${(MODOS[e.detalhe?.split(" ")[0]]?.nome || e.detalhe || "").toLowerCase()}${offline}`;
  if (e.tipo === "condicao") return `condição ${e.detalhe?.split(" ")[0] || ""}${offline}`;
  if (e.tipo === "alarme") return `alarme silenciado no botão da placa${offline}`;
  if (e.tipo === "reservatorio") return `reservatório ${e.detalhe || ""}: bomba liberada`;
  if (e.tipo === "recursos") {
    const lista = (e.detalhe || "").replace("atuadores: ", "").split(", ").map((a) => ATUADORES[a] || a);
    return `informou os atuadores: ${lista.join(", ")}${lista.includes("Bomba") ? "" : " (sem bomba)"}`;
  }
  if (e.tipo === "falha_sensor") {
    const g = (e.detalhe || "").split(" ")[0];
    return GRANDEZAS[g] ? `${GRANDEZAS[g].nome.toLowerCase()} sem leitura válida` : e.detalhe || "";
  }
  return e.detalhe || "";
}
function listaEventos(lista) {
  const ul = $("eventos"); ul.textContent = "";
  if (!lista.length) ul.appendChild(el("li", "Nenhum evento registrado.", "vazio"));
  const tipo = { status: "", falha_sensor: "bad", mensagem_invalida: "warn", comando_falhou: "bad", comando_expirado: "warn", bomba: "acc", modo: "warn", condicao: "", alarme: "", reservatorio: "ok" };
  for (const e of lista) {
    const li = el("li");
    li.appendChild(badge(EVENTOS[e.tipo] || e.tipo, tipo[e.tipo] ?? ""));
    li.appendChild(el("span", `${e.dispositivo_id ? nomeDe(e.dispositivo_id) + ": " : ""}${textoEvento(e)}`));
    li.appendChild(tempo(e.criado_em));
    ul.appendChild(li);
  }
}

const acaoEscolhida = () => (document.querySelector('input[name="cmd-acao"]:checked') || {}).value || "on";
function opcoesAlvo() {
  const s = $("cmd-alvo"), atual = s.value, lista = recursosDe($("cmd-disp").value).atuadores;
  if ([...s.options].map((o) => o.value).join() === lista.join()) return;
  s.textContent = "";
  for (const a of lista) { const o = el("option", ATUADORES[a]); o.value = a; s.appendChild(o); }
  if (lista.includes(atual)) s.value = atual;
  limitarDuracao();
}
function limitarDuracao() {
  const max = DUR_MAX[$("cmd-alvo").value] || 60, off = acaoEscolhida() === "off";
  $("cmd-dur").max = max; if (Number($("cmd-dur").value) > max) $("cmd-dur").value = max;
  $("cmd-dur").disabled = off;
  $("cmd-dur-faixa").textContent = off ? "(não se aplica)" : `(1 a ${max} s)`;
  $("cmd-enviar").textContent = off ? `Desligar ${ATUADORES[$("cmd-alvo").value] || ""}` : `Ligar ${ATUADORES[$("cmd-alvo").value] || ""}`;
}
$("cmd-disp").addEventListener("change", opcoesAlvo);
$("cmd-alvo").addEventListener("change", limitarDuracao);
document.querySelectorAll('input[name="cmd-acao"]').forEach((r) => r.addEventListener("change", limitarDuracao));
$("form-comando").addEventListener("submit", async (ev) => {
  ev.preventDefault();
  const botao = $("cmd-enviar"), acao = acaoEscolhida();
  const corpo = { dispositivoId: $("cmd-disp").value, alvo: $("cmd-alvo").value, acao };
  if (acao === "on") {
    const dur = Number($("cmd-dur").value), max = DUR_MAX[corpo.alvo];
    if (!Number.isInteger(dur) || dur < 1 || dur > max) { toast(`Duração deve ser de 1 a ${max} s.`, "warn"); $("cmd-dur").focus(); return; }
    corpo.duracaoS = dur;
  }
  botao.disabled = true;
  try {
    const c = await api("/comandos", { method: "POST", body: JSON.stringify(corpo) });
    desenharEtapas(c); atualizar();
    const fim = await seguir(c.comando_id, desenharEtapas);
    toast(`${descricao(fim)}: ${SITUACOES[fim.estado]?.[0] || fim.estado}`, fim.estado === "confirmado" ? "ok" : "bad");
  } catch (e) {
    if (!sessao) return;
    const c = e.corpo && e.corpo.comando;
    if (c) desenharEtapas(c); else toast(`Comando não enviado: ${e.message}`, "bad", 8000);
  } finally { botao.disabled = false; atualizar(); }
});

$("pn-disp").addEventListener("change", atualizarPainel);
$("pn-horas").addEventListener("change", atualizarPainel);
const cor = (v) => getComputedStyle(document.documentElement).getPropertyValue(v).trim();
async function atualizarPainel() {
  const disp = $("pn-disp").value, horas = $("pn-horas").value;
  if (!disp || typeof Chart === "undefined") return;
  let p;
  try { p = await api(`/painel?dispositivo_id=${encodeURIComponent(disp)}&horas=${horas}`); }
  catch (e) { return; }
  if (disp !== $("pn-disp").value || horas !== $("pn-horas").value) return;
  const rec = p.recursos || TODOS;
  const resumo = $("resumo"); resumo.textContent = "";
  const caixa = (t, v) => { const b = el("div", null, "leit"); b.appendChild(el("span", t, "rot")); b.appendChild(el("b", v)); resumo.appendChild(b); };
  if (temBomba(rec)) {
    caixa("Irrigações por comando", String(p.irrigacoes));
    if (p.acionamentos_bomba != null) caixa("Vezes que a bomba ligou", String(p.acionamentos_bomba));
  }
  caixa("Alertas no período", String(p.alertas));
  for (const g of rec.sensores) {
    const a = p.grandezas[g], info = GRANDEZAS[g];
    caixa(`${info.nome} · mín / méd / máx`, a.n ? `${fmt(a.minimo)} / ${fmt(a.media)} / ${fmt(a.maximo)} ${a.unidade || info.un}` : "sem dados");
  }

  Chart.defaults.color = cor("--mudo"); Chart.defaults.borderColor = cor("--grade");
  const raiz = $("graficos"), longo = Number(horas) > 24;
  const limiar = limiarDe(dispositivos.find((d) => d.id === disp) || { id: disp });
  for (const [g, info] of Object.entries(GRANDEZAS)) {
    if (!charts[g]) {
      const wrap = el("div", null, "g"); wrap.id = "g-" + g;
      const cv = document.createElement("canvas"); cv.setAttribute("role", "img"); cv.setAttribute("aria-label", `Gráfico de ${info.nome}`);
      wrap.appendChild(cv); const vazio = el("div", "Sem leituras neste período", "vazio"); wrap.appendChild(vazio); raiz.appendChild(wrap);
      const conjuntos = [
        { label: `${info.nome} (${info.un})`, data: [], borderColor: cor("--serie"), backgroundColor: cor("--serie"), pointRadius: 1.5, tension: 0.25 },
        { label: "média móvel", data: [], borderColor: cor("--serie2"), borderDash: [5, 4], pointRadius: 0, tension: 0.3 },
      ];
      if (g === "umidade_solo") conjuntos.push({ label: "limiar (seco abaixo)", data: [], borderColor: cor("--bad"), borderDash: [2, 3], borderWidth: 1.5, pointRadius: 0 });
      charts[g] = new Chart(cv, { type: "line", data: { labels: [], datasets: conjuntos },
        options: { responsive: true, maintainAspectRatio: false, animation: false, interaction: { mode: "index", intersect: false },
          plugins: { title: { display: true, text: info.nome, align: "start", font: { weight: "600" } }, legend: { labels: { boxWidth: 12 } } },
          scales: { y: { beginAtZero: false, ticks: { callback: (v) => `${Number(v).toLocaleString("pt-BR", { maximumFractionDigits: 1 })}${info.un === "%" ? "%" : ""}` } } } } });
    }
    const wrap = $("g-" + g);
    wrap.hidden = !rec.sensores.includes(g);
    const serie = p.grandezas[g].serie, movel = p.grandezas[g].media_movel, ch = charts[g];
    wrap.querySelector(".vazio").hidden = serie.length > 0;
    ch.data.labels = serie.map((s) => lerData(s.t).toLocaleString("pt-BR", longo ? { day: "2-digit", month: "2-digit", hour: "2-digit", minute: "2-digit" } : { hour: "2-digit", minute: "2-digit" }));
    ch.data.datasets[0].data = serie.map((s) => s.valor);
    ch.data.datasets[1].data = movel.map((m) => m.valor);
    if (g === "umidade_solo") ch.data.datasets[2].data = serie.map(() => limiar);
    ch.update();
  }
}

function preencherRegra() {
  const r = regras.find((x) => x.dispositivo_id === $("rg-disp").value) || {};
  $("rg-limiar").value = r.limiar ?? ""; $("rg-janela").value = r.janela_min ?? ""; $("rg-intervalo").value = r.intervalo_min_s ?? "";
  $("rg-dur").value = r.duracao_s ?? ""; $("rg-ativa").checked = !!r.ativa;
  $("rg-msg").textContent = r.atualizado_em ? `Última alteração em ${dataHora(r.atualizado_em)}.` : "";
  textosRegra();
}
function textosRegra() {
  const com = temBomba(recursosDe($("rg-disp").value));
  $("rg-desc").textContent = com
    ? "Quando a média da umidade do solo na janela fica abaixo do limiar, o backend liga a bomba pela duração definida — no máximo uma vez a cada intervalo mínimo."
    : "Esta estação não tem bomba: quando o solo fica abaixo do limiar, o backend acende o LED e toca o buzzer (repete a cada intervalo mínimo) até alguém regar.";
  $("rg-dur-rotulo").textContent = com ? "Duração da bomba (s)" : "Bipe do buzzer (s)";
}
$("rg-disp").addEventListener("change", preencherRegra);
$("form-regra").addEventListener("submit", async (ev) => {
  ev.preventDefault();
  const msg = $("rg-msg"), botao = $("rg-salvar");
  if (!$("form-regra").checkValidity()) { $("form-regra").reportValidity(); return; }
  botao.disabled = true; botao.textContent = "Salvando…"; msg.textContent = "";
  try {
    await api(`/regras/${encodeURIComponent($("rg-disp").value)}`, { method: "PUT", body: JSON.stringify({
      limiar: Number($("rg-limiar").value), janela_min: Number($("rg-janela").value),
      intervalo_min_s: Number($("rg-intervalo").value), duracao_s: Number($("rg-dur").value), ativa: $("rg-ativa").checked }) });
    toast(`Regra de ${nomeDe($("rg-disp").value)} salva.`, "ok");
    try { regras = await api("/regras"); } catch (e) {  }
    preencherRegra(); cartoes(dispositivos); atualizarPainel();
  } catch (e) {
    if (sessao) msg.textContent = "Não foi possível salvar: " + e.message;
  } finally { botao.disabled = !admin(); botao.textContent = "Salvar regra"; }
});

const salva = ler("macp");
if (salva) { try { sessao = JSON.parse(salva); iniciar(); } catch (e) { sair(); } }
