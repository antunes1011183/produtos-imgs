// Service worker do painel Mupa Brain — pedido do usuário ("um pwa top"), servido na raiz
// (GET /sw.js, ver rota em app.py) pra ganhar escopo de site inteiro por padrão, em vez do
// escopo restrito a /static/ que teria se ficasse só dentro da pasta static/.
//
// DELIBERADAMENTE conservador: cacheia só os ícones + manifest (ativos estáticos, sem dado
// nenhum sensível ou que mude). NUNCA cacheia o HTML do painel nem qualquer chamada de API
// (/admin/..., /configuracoes, /produtos, /login, etc.) — duas razões:
// 1. É um painel administrativo com dado em tempo real (estoque, preço, fila de quarentena);
//    servir uma resposta cacheada e desatualizada escondida atrás de "funciona offline"
//    enganaria quem usa o painel sem avisar que a informação pode estar velha.
// 2. O HTML de /configuracoes é renderizado no servidor com a chave OpenAI/Gemini JÁ
//    preenchida no próprio markup (`value="{{ openai_key_full }}"`) — guardar esse HTML no
//    Cache Storage do navegador manteria uma cópia da chave em disco fora do banco de dados,
//    inclusive depois de trocada/revogada. Melhor nunca cachear essa página.
//
// O único motivo de ter um service worker aqui é técnico: o Chrome exige um com um handler
// de 'fetch' funcional como critério de instalabilidade (o botão "Instalar app") — sem
// nenhuma necessidade real de funcionar off-line pra esse tipo de ferramenta.

const CACHE_NAME = 'mupa-brain-shell-v1';
const PRECACHE_URLS = [
    '/static/icons/icon-192.png',
    '/static/icons/icon-512.png',
    '/static/icons/icon-maskable-512.png',
    '/static/manifest.json',
];

self.addEventListener('install', (event) => {
    event.waitUntil(
        caches.open(CACHE_NAME)
            .then((cache) => cache.addAll(PRECACHE_URLS))
            .catch(() => {}) // rede fora na hora de instalar não pode quebrar o SW inteiro
    );
    self.skipWaiting();
});

self.addEventListener('activate', (event) => {
    event.waitUntil(
        caches.keys().then((chaves) =>
            Promise.all(chaves.filter((k) => k !== CACHE_NAME).map((k) => caches.delete(k)))
        )
    );
    self.clients.claim();
});

self.addEventListener('fetch', (event) => {
    if (event.request.method !== 'GET') return;
    const url = new URL(event.request.url);
    if (!PRECACHE_URLS.includes(url.pathname)) return; // deixa passar direto pra rede

    event.respondWith(
        caches.match(event.request).then((cached) => cached || fetch(event.request))
    );
});
