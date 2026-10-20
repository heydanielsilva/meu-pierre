// Service worker do Meu Pierre: abre rápido e funciona sem internet com os últimos dados vistos.
// - Página e /api/painel: rede primeiro; sem rede, usa a última cópia guardada.
// - Bibliotecas e fontes (CDN): usa a cópia guardada e atualiza em segundo plano.
// - Login, logout, atualização, exportação e qualquer POST passam direto, sem cache.
const VERSAO = "meu-pierre-v1";
const ESTATICOS = ["/pwa/icone-192.png", "/pwa/icone-512.png", "/pwa/manifest.webmanifest"];

self.addEventListener("install", (e) => {
  e.waitUntil(caches.open(VERSAO).then((c) => c.addAll(ESTATICOS)).then(() => self.skipWaiting()));
});

self.addEventListener("activate", (e) => {
  e.waitUntil(caches.keys().then((ks) => Promise.all(ks.filter((k) => k !== VERSAO).map((k) => caches.delete(k))))
    .then(() => self.clients.claim()));
});

self.addEventListener("message", (e) => {
  if (e.data === "limpar") e.waitUntil(caches.delete(VERSAO));  // ao sair da conta
});

const PULAR = ["/entrar", "/sair", "/exportar.xlsx", "/api/sincronizar", "/api/cron", "/api/orcamento", "/api/regra", "/sw.js"];

self.addEventListener("fetch", (e) => {
  const req = e.request;
  if (req.method !== "GET") return;
  const url = new URL(req.url);
  if (url.origin === location.origin && PULAR.some((p) => url.pathname.startsWith(p))) return;

  if (url.origin === location.origin && (req.mode === "navigate" || url.pathname === "/api/painel")) {
    e.respondWith(redePrimeiro(req, req.mode === "navigate" ? "/" : null));
    return;
  }
  if (url.origin === location.origin && url.pathname.startsWith("/pwa/")) {
    e.respondWith(caches.match(req).then((r) => r || fetch(req)));
    return;
  }
  if (/(cdnjs\.cloudflare\.com|fonts\.(googleapis|gstatic)\.com)$/.test(url.hostname)) {
    e.respondWith(guardadoEAtualiza(req));
  }
});

async function redePrimeiro(req, chave) {
  const cache = await caches.open(VERSAO);
  try {
    const r = await fetch(req);
    // só guarda páginas e dados de quem está logado (o painel), nunca a tela de login nem erros
    if (r.ok && !r.redirected) {
      const tipo = r.headers.get("content-type") || "";
      const ehPainel = chave ? (await r.clone().text()).includes('id="conteudo"') : tipo.includes("json");
      if (ehPainel) cache.put(chave || req, r.clone());
    }
    return r;
  } catch (err) {
    const guardada = await cache.match(chave || req);
    if (guardada) {
      if (chave) return guardada;
      const h = new Headers(guardada.headers);
      h.set("x-offline", "1");
      return new Response(await guardada.blob(), { status: 200, headers: h });
    }
    if (chave) return new Response(PAGINA_OFFLINE, { headers: { "content-type": "text/html; charset=utf-8" } });
    return new Response(JSON.stringify({ erro: "Sem internet e sem dados guardados ainda." }), { status: 503, headers: { "content-type": "application/json" } });
  }
}

async function guardadoEAtualiza(req) {
  const cache = await caches.open(VERSAO);
  const guardada = await cache.match(req);
  const rede = fetch(req).then((r) => { if (r.ok || r.type === "opaque") cache.put(req, r.clone()); return r; }).catch(() => guardada);
  return guardada || rede;
}

const PAGINA_OFFLINE = `<!doctype html><html lang="pt-BR"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>Meu Pierre · sem internet</title><style>body{margin:0;min-height:100vh;display:grid;place-items:center;background:#071817;color:#eaf3f2;
font:16px/1.5 system-ui,sans-serif;text-align:center;padding:24px}img{width:72px;height:72px;border-radius:18px}button{margin-top:18px;height:46px;
padding:0 22px;border:0;border-radius:999px;background:#f28c3b;color:#fff;font:inherit;font-weight:700}</style></head><body><div>
<img src="/pwa/icone-192.png" alt=""><h1 style="font-size:22px">Sem internet</h1><p style="color:#8fb0ac">Abra o painel uma vez com internet para ele guardar seus dados neste aparelho.</p>
<button onclick="location.reload()">Tentar de novo</button></div></body></html>`;
