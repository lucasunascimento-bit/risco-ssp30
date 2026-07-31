/**
 * SSP30 Loss Prevention — Configuração central
 * Edite AS_URL com a URL do seu Google Apps Script após o deploy.
 */

// URL do Google Apps Script (Web App) — preencher após deploy
const AS_URL = 'https://script.google.com/a/macros/mercadolivre.com/s/AKfycbxe6V2PY_5_9xkc503kky20X9BJExE1RbDZGdW0UrkOzJbG-SqphAQIfiMwFJUxXyk/exec';

// Função helper para chamar o backend
async function api(action, body, method) {
  let url = AS_URL + '?action=' + action;
  if (method !== 'POST' && body) {
    url += '&' + new URLSearchParams(body).toString();
  }
  const opts = method === 'POST'
    ? { method: 'POST', body: JSON.stringify(Object.assign({ action }, body)) }
    : {};
  const res = await fetch(url, opts);
  return res.json();
}
