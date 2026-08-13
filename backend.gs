/**
 * SSP30 Loss Prevention — Google Apps Script Backend
 *
 * Deploy: Extensions → Apps Script → Deploy → New Deployment
 *   Type: Web App
 *   Execute as: Me
 *   Who has access: Anyone within Mercado Livre (ou "Anyone" se necessário)
 *
 * Após deploy, copie a URL e cole em config.js (AS_URL).
 */

// ─── Constantes ────────────────────────────────────────────────────────────────
const PLANILHA_ID       = '1rFcUXxl53WVQf_ASRx3mhlEvFoJevcaiwjMZY1vso5Y';
const SINISTRO_SHEET_ID = '12-JUN1u4UfXBv0Mkeq9D3lsosG3e6OjbysMt4h7cpII';
const SINISTRO_ABA      = 'Eventos SVC';
const ABA_ON_WAY        = 'Tratativas Risco On Way (HV) - Lucas';
const ABA_ON_ROUTE      = 'Tratativas Risco On Route (HV) - Lucas';
const ABA_HISTORICO     = 'Histórico';
const ABA_DIARIO        = 'Diário de Bordo';
const TZ                = 'America/Sao_Paulo';

// Colunas 1-based (índice para getRange / getValue)
// Na planilha: coluna A=1, B=2, ..., W=23, X=24...
const COLS = {
  wy: { acao: 23, link: 24, status: 29, final: 30, gmv: 22, label: 'ON WAY',   aba: ABA_ON_WAY },
  rt: { acao: 24,           status: 29, final: 30, gmv: 23, label: 'ON ROUTE', aba: ABA_ON_ROUTE },
};

const FINAL_MAP = {
  reversao:   'Retornou ao fluxo',
  bpp:        'Perdido',
  recuperado: 'Recuperado',
};

const SCHEDULE = {
  1: [['08:00','09:30','Investigação CFTV','Análise'],['09:30','10:30','Gemba APP / MLB Megas','Gemba'],['10:30','11:00','DDS / Touch Point','Reunião'],['14:00','14:30','Aduana Faltante','Análise'],['14:30','15:00','Ronda Virtual','Análise'],['15:00','15:30','Drivers Ofensores','Análise'],['15:30','17:00','Risco On Way / Route','Análise']],
  2: [['08:00','09:30','Investigação CFTV','Análise'],['09:30','10:30','Gemba APP / MLB Megas','Gemba'],['13:00','13:30','Weekly Stolen','Reunião'],['14:00','14:30','Aduana Faltante','Análise'],['14:30','15:00','Ronda Virtual','Análise'],['15:30','17:00','Risco On Way / Route','Análise']],
  3: [['08:00','09:30','Investigação CFTV','Análise'],['09:30','10:30','Gemba APP / MLB Megas','Gemba'],['14:00','14:30','Aduana Faltante','Análise'],['14:30','15:00','Ronda Virtual','Análise'],['15:00','15:30','Drivers Ofensores','Análise'],['15:30','17:00','Risco On Way / Route','Análise']],
  4: [['08:00','09:30','Investigação CFTV','Análise'],['09:30','10:30','Gemba APP / MLB Megas','Gemba'],['14:00','14:30','Aduana Faltante','Análise'],['14:30','15:00','Ronda Virtual','Análise'],['15:00','15:30','Drivers Ofensores','Análise'],['15:30','17:00','Risco On Way / Route','Análise']],
  5: [['08:00','09:30','Investigação CFTV','Análise'],['09:30','10:30','Gemba APP / MLB Megas','Gemba'],['14:00','14:30','Aduana Faltante','Análise'],['14:30','15:00','Ronda Virtual','Análise'],['15:30','17:00','Risco On Way / Route','Análise']],
};

// ─── Roteador principal ────────────────────────────────────────────────────────
function doGet(e)  { return route(e); }
function doPost(e) { return route(e); }

function route(e) {
  const action = (e.parameter && e.parameter.action) || '';
  let body = {};
  if (e.postData && e.postData.contents) {
    try { body = JSON.parse(e.postData.contents); } catch(_) {}
  }
  // Mescla parâmetros GET no body para conveniência
  if (e.parameter) Object.assign(body, e.parameter);

  try {
    let r;
    switch (action) {
      case 'ping':               r = { ok: true }; break;
      case 'ow_values':          r = owValues(body.tab || 'wy'); break;
      case 'update':             r = cellUpdate(body); break;
      case 'mover_historico':    r = moverHistorico(body); break;
      case 'diario':             r = diarioGet(); break;
      case 'diario_toggle':      r = diarioToggle(body); break;
      case 'diario_obs':         r = diarioObs(body); break;
      case 'diario_extra':       r = diarioExtra(body); break;
      case 'diario_delete_extra':r = diarioDeleteExtra(body); break;
      case 'save_rt':            r = saveRt(body); break;
      case 'sinistros_rota_info':r = sinistrosRotaInfo(body); break;
      case 'sinistros_add':      r = sinistrosAdd(body); break;
      default: r = { ok: false, error: 'Ação desconhecida: ' + action };
    }
    return json(r);
  } catch (err) {
    return json({ ok: false, error: err.message || err.toString() });
  }
}

function json(data) {
  return ContentService
    .createTextOutput(JSON.stringify(data))
    .setMimeType(ContentService.MimeType.JSON);
}

function norm(s) {
  return String(s || '').trim().toLowerCase()
    .normalize('NFD').replace(/[̀-ͯ]/g, '');
}

function planilha() {
  return SpreadsheetApp.openById(PLANILHA_ID);
}

// ─── ON WAY / ON ROUTE ────────────────────────────────────────────────────────
function owValues(tab) {
  const c = COLS[tab];
  if (!c) return { error: 'tab inválido' };

  const ws   = planilha().getSheetByName(c.aba);
  const rows = ws.getDataRange().getValues();
  const result = {};

  for (let i = 1; i < rows.length; i++) {
    const row = rows[i];
    const shp = String(row[2] || '').trim();
    if (!shp) continue;
    const entry = {
      acao:   String(row[c.acao   - 1] || ''),
      status: String(row[c.status - 1] || ''),
      final:  String(row[c.final  - 1] || ''),
    };
    if (tab === 'wy') entry.link = String(row[c.link - 1] || '');
    result[shp] = entry;
  }
  return result;
}

function findRow(ws, shpId) {
  const vals = ws.getDataRange().getValues();
  for (let i = 1; i < vals.length; i++) {
    if (String(vals[i][2] || '').trim() === shpId) return { rowIdx: i + 1, row: vals[i] };
  }
  return null;
}

function cellUpdate(body) {
  const tab   = String(body.tab   || 'wy');
  const shpId = String(body.shp_id || '').trim();
  const col   = parseInt(body.col);
  const val   = String(body.value || '');

  const c = COLS[tab];
  if (!c) return { ok: false, error: 'tab inválido' };

  const ws  = planilha().getSheetByName(c.aba);
  const hit = findRow(ws, shpId);
  if (!hit) return { ok: false, error: `SHP ${shpId} não encontrado` };

  ws.getRange(hit.rowIdx, col).setValue(val);
  return { ok: true };
}

function moverHistorico(body) {
  const tab   = String(body.tab    || 'wy');
  const shpId = String(body.shp_id || '').trim();
  const hoje  = String(body.hoje   || '');

  const c = COLS[tab];
  if (!c) return { ok: false, error: 'tab inválido' };

  const pl = planilha();
  const ws = pl.getSheetByName(c.aba);
  const hit = findRow(ws, shpId);
  if (!hit) return { ok: false, error: `SHP ${shpId} não encontrado` };

  const r = hit.row;
  while (r.length < 35) r.push('');

  if (norm(r[28]) !== 'concluido' || !String(r[29] || '').trim()) {
    return { ok: false, error: 'Condições não atendidas (status≠Concluído ou finalização vazia)' };
  }

  const finalNorm = norm(String(r[29] || '').trim());
  const finalHist = FINAL_MAP[finalNorm] || String(r[29] || '').trim();

  pl.getSheetByName(ABA_HISTORICO).appendRow([
    hoje, c.label,
    r[2]  || '',
    r[1]  || '',
    r[c.gmv - 1] || '',
    r[0]  || '',
    r[28] || '',
    finalHist,
  ]);

  ws.deleteRow(hit.rowIdx);
  return { ok: true };
}

function saveRt(body) {
  const tab   = String(body.tab || 'rt');
  const shpId = String(body.shp_id || '').trim();
  const c     = COLS[tab];
  if (!c) return { ok: false, error: 'tab inválido' };

  const ws  = planilha().getSheetByName(c.aba);
  const hit = findRow(ws, shpId);
  if (!hit) return { ok: false, error: `SHP ${shpId} não encontrado` };

  if (body.acao_lp   !== undefined) ws.getRange(hit.rowIdx, c.acao).setValue(body.acao_lp   || '');
  if (body.status    !== undefined) ws.getRange(hit.rowIdx, c.status).setValue(body.status   || '');
  if (body.conclusao !== undefined) ws.getRange(hit.rowIdx, c.final).setValue(body.conclusao || '');
  if (body.nota      !== undefined) ws.getRange(hit.rowIdx, 31).setValue(body.nota           || '');

  return { ok: true };
}

// ─── DIÁRIO DE BORDO ──────────────────────────────────────────────────────────
function getDiarioWs() {
  const pl = planilha();
  let ws = pl.getSheetByName(ABA_DIARIO);
  if (!ws) {
    ws = pl.insertSheet(ABA_DIARIO);
    ws.getRange('A1:H1').setValues([['Data','Hora_Inicio','Hora_Fim','Atividade','Tipo','Feito','Observacao','Extra']]);
  }
  return ws;
}

function diarioGet() {
  const now      = new Date();
  const hojeStr  = Utilities.formatDate(now, TZ, 'yyyy-MM-dd');
  const isoDay   = parseInt(Utilities.formatDate(now, TZ, 'u')); // 1=Seg...7=Dom

  const ws   = getDiarioWs();
  const rows = ws.getDataRange().getValues();
  const log  = {};
  const extras = [];

  for (let i = 1; i < rows.length; i++) {
    const row = rows[i];
    if (String(row[0] || '').trim() !== hojeStr) continue;
    const atv = String(row[3] || '').trim();
    if (!atv) continue;
    const entry = {
      hora_ini:  row[1], hora_fim: row[2],
      atividade: atv,    tipo:     row[4],
      feito:     row[5] == '1' || row[5] === true,
      obs:       String(row[6] || ''),
      extra:     row[7] == '1' || row[7] === true,
    };
    if (entry.extra) extras.push(entry);
    else log[atv] = entry;
  }

  const schedule = SCHEDULE[isoDay] || [];
  const items = schedule.map(([hi, hf, atv, tipo]) => {
    const lg = log[atv] || {};
    return { hora_ini: hi, hora_fim: hf, atividade: atv, tipo,
             feito: !!lg.feito, obs: lg.obs || '', extra: false };
  });

  return { data: hojeStr, dia_semana: isoDay, items, extras };
}

function diarioFindOrCreate(ws, hoje, atv, hi, hf, tipo) {
  const vals = ws.getDataRange().getValues();
  for (let i = 1; i < vals.length; i++) {
    if (String(vals[i][0]).trim() === hoje && String(vals[i][3]).trim() === atv) {
      return i + 1; // rowIdx 1-based
    }
  }
  ws.appendRow([hoje, hi, hf, atv, tipo, '', '', '']);
  return ws.getLastRow();
}

function diarioToggle(body) {
  const ws     = getDiarioWs();
  const hoje   = String(body.data       || '');
  const atv    = String(body.atividade  || '').trim();
  const feito  = body.feito !== false && body.feito !== 'false';
  const rowIdx = diarioFindOrCreate(ws, hoje, atv, body.hora_ini || '', body.hora_fim || '', body.tipo || '');
  ws.getRange(rowIdx, 6).setValue(feito ? '1' : '');
  return { ok: true };
}

function diarioObs(body) {
  const ws     = getDiarioWs();
  const hoje   = String(body.data       || '');
  const atv    = String(body.atividade  || '').trim();
  const rowIdx = diarioFindOrCreate(ws, hoje, atv, body.hora_ini || '', body.hora_fim || '', body.tipo || '');
  ws.getRange(rowIdx, 7).setValue(String(body.obs || ''));
  return { ok: true };
}

function diarioExtra(body) {
  const ws = getDiarioWs();
  ws.appendRow([
    body.data || '', body.hora_ini || '', body.hora_fim || '',
    body.atividade || '', 'Extra',
    body.feito ? '1' : '', body.obs || '', '1',
  ]);
  return { ok: true };
}

function diarioDeleteExtra(body) {
  const ws   = getDiarioWs();
  const hoje = String(body.data       || '');
  const atv  = String(body.atividade  || '').trim();
  const vals = ws.getDataRange().getValues();
  for (let i = vals.length - 1; i >= 1; i--) {
    if (String(vals[i][0]).trim() === hoje && String(vals[i][3]).trim() === atv && (vals[i][7] == '1' || vals[i][7] === true)) {
      ws.deleteRow(i + 1);
      return { ok: true };
    }
  }
  return { ok: true };
}

// ─── SINISTROS ────────────────────────────────────────────────────────────────
function getBppCache() {
  const cache = CacheService.getScriptCache();
  const cached = cache.get('bpp');
  if (cached) {
    try { return JSON.parse(cached); } catch(_) {}
  }
  const ws   = SpreadsheetApp.openById(SINISTRO_SHEET_ID).getSheetByName('BPP');
  const vals = ws.getDataRange().getValues();
  if (!vals.length) return { header: {}, rows: [] };
  const header = {};
  vals[0].forEach((h, i) => { header[String(h).trim()] = i; });
  const data = { header, rows: vals.slice(1) };
  try { cache.put('bpp', JSON.stringify(data), 300); } catch(_) {}
  return data;
}

function parseUsd(val) {
  const v = String(val || '').replace('$', '').replace(/,/g, '');
  return parseFloat(v) || 0;
}

function sinistrosRotaInfo(body) {
  const rotaId = String(body.rota || '').trim();
  if (!rotaId) return { ok: false, error: 'rota obrigatório' };

  const { header, rows } = getBppCache();
  if (!rows.length) return { ok: false, error: 'BPP vazio' };

  const ri = header['Rota']           ?? -1;
  const bi = header['Bpp Cashout Usd'] ?? -1;
  const ci = header['Causa BPP']      ?? -1;
  const mi = header['MLP']            ?? -1;

  if (ri < 0 || bi < 0) return { ok: false, error: 'Colunas Rota/BPP não encontradas' };

  const matching = rows.filter(r => r.length > ri && String(r[ri]).trim() === rotaId);
  if (!matching.length) return { ok: true, found: 0, qtd_shp: 0, bpp_valor: 0, rota_total: 0, mlp: '' };

  const stolen    = matching.filter(r => ci >= 0 && r.length > ci && String(r[ci]).toLowerCase().includes('stolen'));
  const qtdShp    = stolen.length;
  const bppValor  = stolen.reduce((s, r) => s + (r.length > bi ? parseUsd(r[bi]) : 0), 0);
  const rotaTotal = matching.reduce((s, r) => s + (r.length > bi ? parseUsd(r[bi]) : 0), 0);
  const mlp       = mi >= 0 && matching[0].length > mi ? String(matching[0][mi] || '') : '';

  return {
    ok: true, found: matching.length,
    qtd_shp: qtdShp,
    bpp_valor:  Math.round(bppValor  * 100) / 100,
    rota_total: Math.round(rotaTotal * 100) / 100,
    mlp,
  };
}

function sinistrosAdd(payload) {
  const ws      = SpreadsheetApp.openById(SINISTRO_SHEET_ID).getSheetByName(SINISTRO_ABA);
  const headers = ws.getRange(1, 1, 1, ws.getLastColumn()).getValues()[0];
  const row     = new Array(headers.length).fill('');

  function setCol(name, val) {
    const idx = headers.findIndex(h => String(h).trim() === name);
    if (idx >= 0) row[idx] = val || '';
  }

  setCol('Data',                  payload.data         || '');
  setCol('Horario',               payload.horario       || '');
  setCol('Rota',                  payload.rota          || '');
  setCol('Drive',                 payload.id_driver     || '');
  setCol('Nome Drive',            payload.nome          || '');
  setCol('Placa',                 payload.placa         || '');
  setCol('TIPO 2',                payload.tipo2         || '');
  setCol('Qtde Shp',              payload.qtd_total     || '');
  setCol('Bpp Cashout Usd',       payload.valor         || '');
  setCol('$Rota',                 payload.rota_total    || '');
  setCol('Recup. da Carga?',      payload.recup_carga   || '');
  setCol('Recup. Shp',            payload.qtd_rec       || '');
  setCol('Recup. Cashout Usd',    payload.recup_bpp     || '');
  setCol('Cidade',                payload.cidade        || '');
  setCol('Distrito',              payload.distrito      || '');
  setCol('Bairro',                payload.bairro        || '');
  setCol('CEP',                   payload.cep           || '');
  setCol('CLUSTER',               payload.cluster       || '');
  setCol('Rua',                   payload.local         || '');
  setCol('MLP',                   payload.transp        || '');
  setCol('Veículo',               payload.veiculo       || '');
  setCol('Natureza do evento',    payload.natureza      || '');
  setCol('MODUS OPERANDI',        payload.modus         || '');
  setCol('Boletim de ocorrência', payload.boletim       || '');
  setCol('Link boletim',          payload.link_bo       || '');
  setCol('Relato',                payload.relato        || '');

  ws.appendRow(row);
  return { ok: true };
}
