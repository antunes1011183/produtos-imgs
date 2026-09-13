# produtos-imgs — Mupa Brain

API Flask de gestão de produtos/imagens para o app de consulta de preço da Mupa (`mplayer`). Roda na porta **5050**. Painel admin em `/configuracoes`.

## Arquitetura da arte publicitária (a parte mais delicada do projeto)

Fluxo: `GET /produto-imagem/<codbar>` → se já existe arte, retorna `imagem_url_arte`; se não existe mas há foto crua, dispara `gerar_arte_publicitaria` **em background** (thread separada, guardada por `_gerando_arte_em_andamento` pra não duplicar chamadas simultâneas) e retorna `null` nessa primeira resposta — a arte aparece só na consulta seguinte.

### Decisão arquitetural: "IA integra a cena, o app garante o texto"

Depois de iterar bastante (ver histórico de commits), a decisão final foi:
- **Gemini (`gemini-2.5-flash-image`) gera só a CENA/fotografia** com o produto integrado — nenhum texto escrito pela IA de imagem.
- **Nome do produto + headline são gerados por texto** (`gerar_textos_arte_ia`, modelo `gemini-2.5-flash-lite`) e **desenhados por PIL** (`compor_texto_na_arte`), com fonte real — isso garante ortografia 100% correta no card de texto, ao contrário de deixar o modelo de imagem "escrever" (ele erra letras).
- **Preço não faz parte da arte** — é sobreposto depois pelo app Android (`PlayerActivity.updatePriceBadge`), usando `product.priceSlots`. Por isso o `LAYOUT` do prompt reserva a metade esquerda inteira livre de qualquer coisa.

### `ARTE_PROMPT_TEMPLATE` — regras e por quê (não simplificar sem saber a razão)

Cada regra existe por causa de um bug real observado:

- **Categoria do produto no cenário** (comida vs. não-comida): sem essa regra, a IA interpretava nome/aroma do produto ao pé da letra — um desodorante "Dark Temptation" (aroma chocolate) virou uma cena de sobremesa com chocolate de verdade. Regra: só monta cenário de comida se o produto **for** alimento/bebida; caso contrário, cenário de uso real da categoria (banheiro, spa, rotina).
- **"Produto aparece uma única vez"**: sem essa regra, a IA às vezes duplicava o produto — uma cópia nítida na metade direita (correta) e uma segunda cópia borrada "vazando" pro fundo da metade esquerda, inclusive repetindo texto promocional impresso na embalagem (ex.: "Leve 5 Pague 4") duas vezes.
- **Vinheta lateral em todas as 4 bordas** (`_aplicar_vinheta`, função separada em Python, não faz parte do prompt): escurece sutilmente as bordas pra dar acabamento premium — implementada via gradiente de alpha (não numpy), combinando eixo horizontal + vertical com `ImageChops.lighter`.
- **Sem pessoas/rostos/mãos**: regra de compliance, não decoração.
- **Sem logo de terceiro duplicado**: um selo já impresso na embalagem (ex.: FIFA, campeonato licenciado) não pode ser recriado como elemento gráfico separado na composição.

### Risco residual conhecido (não totalmente resolvido)

O modelo de imagem às vezes **recria o texto já impresso na própria embalagem** com erro de grafia (ex.: "HARPIC" virou "HARIC", "PASTILHA" virou "PASTITILA") — isso não é a IA "escrevendo" texto novo (proibido e evitado via PIL), é ela **redesenhando** a embalagem de referência de forma levemente imperfeita. Diferente do nome/headline (que são 100% PIL), esse texto faz parte da foto do produto em si e não tem uma solução equivalente ainda. Se voltar a acontecer com frequência, considerar: reforçar ainda mais a instrução de fidelidade, ou testar `gemini-2.5-flash-image` com temperatura mais baixa.

### Corrupção de encoding no catálogo (mojibake, dados irreversíveis)

Parte do catálogo importado tem `description`/`marca` corrompidos — **dois tipos diferentes**, ambos precisam ser checados (`_texto_corrompido`):
1. Caractere de substituição `U+FFFD` (`�`) — corrupção "limpa", já virou perda de dado.
2. Bytes de controle C1 soltos (`U+0080`–`U+009F`, ex.: `\x94`) — mojibake de UTF-8 lido como Latin-1/Windows-1252, sem virar `U+FFFD`. Foi a causa raiz de "SOCOCO" virar "SOC�CO" no banco e a IA "adivinhar" nomes errados tipo "Socaneco" ou até usar `marca='KELLOGG S'` (dado de importação errado, não alucinação).

Quando `_texto_corrompido` detecta corrupção em `gerar_textos_arte_ia`, o fluxo busca uma descrição/marca **íntegras pelo EAN** nas mesmas fontes do cadastro automático (Cosmos → Open Food Facts → Zaffari, nessa ordem) **antes** de deixar a IA reconstruir. Se achar fonte confiável, o **nome fica travado** nela (`fonte_confiavel = True`) — a IA só gera a headline, nunca reescreve o nome de novo (ela já errou mesmo com entrada limpa numa geração).

Nota: o token do Cosmos (`KZSEuMgGjPb8d9gFztQHiw`, hardcoded em `fetch_product_from_cosmos`) está retornando `401 Unauthorized` — Open Food Facts e Zaffari continuam funcionando e cobrem a maioria dos casos.

### Formato de arquivo: WEBP, não PNG

`compor_texto_na_arte` salva em WEBP (`quality=88`), não PNG — para uma foto (não um gráfico com poucas cores), isso dá ~90% de redução de tamanho com qualidade visualmente idêntica, e o app Android já reconverte tudo pra webp no cache local mesmo (evitava um retrabalho). `_arte_url` só reconhece `.webp` — artes antigas em `.png` viram órfãs automaticamente e regeneram sozinhas na próxima consulta (comportamento esperado, não é bug).

**Resolução**: a foto crua (produto pequeno, lado a lado com texto) é cacheada a até 512px — ok pra thumbnail. A ARTE (vira fundo de tela cheia no app) precisa de resolução bem maior; isso é responsabilidade do **app Android** (`PriceQueryEngine.downloadProductImageIfNeeded` usa o maior lado da tela do próprio aparelho como teto, não 512px — ver CLAUDE.md do mplayer).

**Cuidado ao mexer em `downloadProductImageIfNeeded` do lado do app**: a foto crua e a arte do mesmo EAN não podem dividir o mesmo nome de cache (`{ean}.webp`) — já causou um bug onde a arte "roubava" o arquivo da foto crua já baixada. A arte usa a chave `{ean}_arte`.

## Ambiente de desenvolvimento (Windows, sem reloader confiável)

- Rodar com `./venv/Scripts/python.exe app.py` (não `flask run`, não `start_flask.py` — o wrapper usa `psutil`/`taskkill` e `subprocess.Popen` com `DETACHED_PROCESS`, que trava nesta máquina/sessão).
- **`debug=True` (auto-reload) não é confiável nesta máquina** — depois de editar `app.py`, sempre `taskkill //F //IM python.exe` e reiniciar manualmente antes de testar, ou vai testar código antigo silenciosamente (já aconteceu mais de uma vez nesta sessão).
- Teste rápido de uma regeneração de arte, sem precisar do app:
  ```bash
  rm -f static/artes_geradas/<codbar>.webp
  curl -X POST "http://localhost:5050/produto-imagem/<codbar>/gerar-arte" \
    -H "Content-Type: image/jpeg" --data-binary @static/imgs_produtos/<codbar>.jpg
  ```
- Rate limit do Gemini (`429 RESOURCE_EXHAUSTED`) acontece com uso pesado (vários testes seguidos, ou o app sendo testado ao vivo ao mesmo tempo) — só esperar ~30s e tentar de novo.

## srv-mupa (produção) é uma máquina DIFERENTE desta

`srv-mupa.ddns.net` **não é** esta estação de trabalho — é o servidor real usado pelos terminais de loja, em outra rede (IP público diferente, confirmado via `netstat`/`ipconfig`). Rodar/testar aqui (`localhost:5050`) não afeta produção. O app Android aponta pra um host configurável em Configurações → "Servidor de Imagens" (ver CLAUDE.md do mplayer) — o IP local desta máquina muda entre sessões (Wi-Fi DHCP), sempre conferir com `Get-NetIPAddress` antes de testar no aparelho físico.

`instance/produtos.db` e `static/` (fotos, artes) **não estão no git** (fora do repositório) — um clone novo não traz banco nem imagens. Ver conversa/commits sobre deploy pra detalhes.

## Painel admin (`/configuracoes`)

Aba "Consulta Rápida" unifica busca + catálogo + geração de arte + detalhes num painel lateral (não modal) — layout com `.content.wide` pra ocupar a tela toda nessa aba especificamente.

## Débito técnico conhecido (não mexido, fora de escopo até ser pedido)

- `venv/` está parcialmente rastreado no git (deveria estar 100% no `.gitignore`) — **nunca usar `git add -A` ou `git commit -a`** neste repo, sempre `git add <arquivo>` explícito.
- A API do Bing Image Search (`buscar_e_salvar_imagem_bing`) está retornando `410 Gone` — a Microsoft descontinuou essa API. Na prática a cadeia de busca de foto crua (local → Bing → Google → Zaffari) pula direto pro Google na maioria dos casos. Não corrigido ainda; considerar remover o passo do Bing ou trocar por outra fonte se isso virar um problema real.

## Histórico de buscas e resumo diário do sistema

Tabela `HistoricoBuscaImagem` registra toda consulta de imagem — sucesso ou falha — tanto do terminal (`GET /produto-imagem/<codbar>`, `via='terminal'`) quanto de uma retentativa manual no painel (`POST /admin/buscar-imagem/<codbar>`, `via='admin'`). Serve dois propósitos com a mesma tabela: histórico de uso (aba "Histórico" do painel) e fila de pendências para notificação (linhas com `encontrado=False` e `notificado_em=NULL`).

**Resumo diário automático**: `_iniciar_agendador_resumo()` roda uma thread em background (mesmo padrão de `_disparar_geracao_arte_em_background`, sem dependência nova tipo APScheduler) que dispara `enviar_resumo_diario_sistema()` uma vez por dia no horário configurado (`RESUMO_HORARIO`, painel → Configurações → Notificações). Ao contrário de uma versão anterior mais restrita, esse resumo cobre **o sistema inteiro**, não só imagens não encontradas:
- Estatísticas gerais do catálogo (`_estatisticas_gerais`): total de produtos, com/sem foto, com arte publicitária — reaproveita a mesma técnica leve de `/admin/estatisticas` (conta arquivo em pasta, não escaneia produto por produto; o catálogo tem ~945 mil linhas).
- Status da rotação de tokens do Cosmos (`_status_tokens_cosmos`): qual token está em uso, quantos sucessos e quantas rotações por cota esgotada desde o último resumo.
- Cadastros automáticos via fontes externas desde o último resumo (`STATS_CADASTROS_AUTOMATICOS`, incrementado em `register_product_in_database`).
- Imagens de produto ainda não encontradas / não notificadas (o que já existia).

Envia pelos canais ativos:
- **E-mail** (`RESUMO_ATIVO=true` + `RESEND_API_KEY`/`RESUMO_DESTINATARIOS` preenchidos) — via **Resend** (`https://api.resend.com/emails`, chamada HTTP direta com `requests`, sem SDK nem SMTP). `RESEND_REMETENTE` precisa ser de um domínio verificado no Resend; sem configurar, cai no remetente de testes `onboarding@resend.dev` (que só entrega pro próprio e-mail cadastrado na conta Resend — configurar um domínio verificado antes de usar em produção). Testado de ponta a ponta com `requests.post` mockado (monkeypatch) — payload (from/to/subject/html/text), header de autenticação e o tratamento de erro HTTP (ex.: API key inválida) verificados manualmente, sem erros.
- **WhatsApp** (`WHATSAPP_ATIVO=true` + campos preenchidos) — via Z-API ou Evolution API (gateways self-hosted comuns no Brasil), mensagem mais curta (só números-chave, sem a tabela detalhada do e-mail). **Formato do payload ainda não testado contra uma conta real** (`_enviar_whatsapp_resumo_diario`) — se o provedor específico usar um contrato diferente do genérico implementado, ajustar ali.

Diferença importante de comportamento: o resumo agora **sempre envia** quando algum canal está ativo, mesmo sem nenhuma imagem pendente — porque as estatísticas gerais por si só já são um "heartbeat" diário útil. Os contadores (`STATS_COSMOS_SUCESSOS`, `STATS_COSMOS_ROTACOES`, `STATS_CADASTROS_AUTOMATICOS`) são zerados só depois de um envio **bem-sucedido** em pelo menos um canal — cada resumo reporta "desde o último resumo", não o total histórico acumulado. Mesma lógica pras linhas de `HistoricoBuscaImagem` (`notificado_em` só é preenchido em caso de sucesso). Botão "Enviar resumo agora (teste)" no painel dispara manualmente, sem esperar o horário agendado.

## Cadastro manual de produto via fontes externas

A aba "Consulta Rápida" já buscava produtos só no catálogo local. Quando a busca por um EAN (8 a 14 dígitos) não encontra nada localmente, aparece um botão "Cadastrar produto" que chama `GET /produto/<codbar>` — a mesma rota que já fazia esse cadastro automático em outros pontos do sistema (Cosmos → Open Food Facts → Zaffari, nessa ordem). Nenhuma rota nova foi criada para isso, só a exposição na UI. A imagem (thumbnail) que a fonte externa retorna já é baixada e salva automaticamente nesse fluxo (`register_product_in_database` + o bloco que salva `dados.get('thumbnail')` em `consultar_ou_cadastrar_produto`) — não precisou de nada novo pra isso.

### Rotação de tokens do Cosmos (plano free, cota por token)

O Cosmos free tem cota mensal por token. Em vez de um único `COSMOS_API_TOKEN`, o painel agora guarda uma **lista** (`COSMOS_API_TOKENS`, um por linha, textarea em Configurações → "Tokens Cosmos"). `fetch_product_from_cosmos`:
- Começa pelo índice guardado em `COSMOS_TOKEN_INDEX_ATUAL` (não sempre do token #1 — evita re-testar tokens já sabidamente esgotados a cada chamada).
- Se a resposta for **401/402/403/429** (token inválido ou cota esgotada), avança pro próximo token da lista e tenta de novo, até esgotar a lista inteira.
- Se a resposta for **404** (produto não existe no Cosmos — não é problema de token), retorna `None` na hora, sem rotacionar.
- Ao ter sucesso ou esgotar todos os tokens, persiste o índice final em `COSMOS_TOKEN_INDEX_ATUAL` pra próxima chamada já começar dali.
- Contadores `STATS_COSMOS_SUCESSOS` e `STATS_COSMOS_ROTACOES` (Config) acumulam desde o último resumo diário enviado (zerados lá, não aqui) — usados no e-mail/WhatsApp de resumo.

Testado com tokens falsos direto contra a API real do Cosmos (confirma 401 em cada um → rotaciona → esgota → `None`) e com `smtplib`/`requests` mockados via monkeypatch pra validar o caminho de sucesso (token 2 funciona, índice persiste, chamada seguinte já pula direto pro token 2). Não testado ainda com tokens reais válidos — fazer isso assim que os tokens do usuário forem colados no painel.

## Gestão de imagem por produto (painel)

O painel de detalhes (Consulta Rápida) ganhou "Enviar/Trocar imagem" (upload via `POST /upload-imagem-produto/<codbar>`, sobrescreve) e "Excluir imagem" (via `DELETE /deletar-imagem-produto/<codbar>`, com confirmação). Ambas as rotas já existiam — só não estavam expostas na UI. **Excluir a foto crua não apaga a arte publicitária já gerada** (são arquivos independentes, ver seção de arte acima) — intencional, mencionado no próprio diálogo de confirmação.
