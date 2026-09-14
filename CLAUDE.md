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

### REGRA FIXA: a arte sempre sai em 1280x800 (dispositivo na horizontal) — nunca mudar

`_normalizar_tamanho_arte` (chamada em `compor_texto_na_arte`, logo depois de abrir a imagem e ANTES da vinheta/texto) força a arte final a sair **sempre** em `ARTE_LARGURA_HORIZONTAL x ARTE_ALTURA_HORIZONTAL` = **1280x800**, redimensionando com preservação de proporção e cortando o excedente (mesma lógica de um `CENTER_CROP`, feita aqui no servidor). Isso é uma regra explícita pedida pelo usuário, não uma escolha arbitrária — **nunca remover essa normalização ou deixar o tamanho variar**.

Por quê isso importa tanto: antes dessa normalização, `_cortar_tarjas_pretas` cortava uma quantidade *variável* de pixels a cada geração (dependendo de quanto letterboxing o Gemini incluiu naquela chamada específica), então o arquivo final saía com um tamanho ligeiramente diferente toda vez. Isso causava dois problemas visuais concretos, ambos reportados pelo usuário e confirmados durante a investigação:
1. O app faz `CENTER_CROP`/`FIT_CENTER` (ver histórico abaixo) pra exibir a arte — uma fonte de tamanho variável fazia cada imagem ser escalada numa proporção diferente, resultado imprevisível de produto pra produto.
2. A vinheta (`_aplicar_vinheta`) é desenhada em % da altura da imagem *nesse ponto do pipeline* — se o corte de letterboxing reduzia a altura antes da vinheta ser aplicada, o app depois escalava essa imagem (já menor) de volta pro tamanho da tela, e esse reescalonamento fazia a faixa de vinheta ficar fina demais ou sumir visualmente — parecia que o degradê tinha sido removido, mas o código nunca deixou de chamar `_aplicar_vinheta`; o efeito só ficava inconsistente por causa do tamanho variável de entrada.

**Por que 1280x800 e não 1344x768 (valor original, um 16:9 genérico "arredondado")**: o usuário mediu a resolução real do terminal físico via `adb shell wm size` (`Physical size: 1280x800`) e pediu pra usar esse valor exato em vez de uma suposição de proporção. Antes da troca, a arte (1344x768, proporção 1.75:1) não batia com a tela real (1280x800, proporção 1.6:1) — o app usava `FIT_CENTER` (ver seção do app abaixo) pra nunca cortar a arte, mas isso deixava uma tarja (letterbox) visível no topo/base por causa do descompasso de proporção. Com a arte gerada EXATAMENTE na resolução real do terminal, `FIT_CENTER` escala 1:1 e cobre a tela inteira sem cortar nada E sem nenhuma tarja — as duas exigências (nunca cortar / preencher a tela) deixam de ser conflitantes.

Isso significa que, **se a loja/terminal físico tiver uma resolução de tela diferente de 1280x800, esse valor precisa ser atualizado** (não é mais um 16:9 universal, é a resolução exata de um hardware específico) — rodar `adb shell wm size` no terminal em questão antes de mudar. Se um dia existirem terminais com resoluções diferentes ao mesmo tempo, essa constante única deixa de fazer sentido — seria necessário um `ARTE_LARGURA_HORIZONTAL`/`ARTE_ALTURA_HORIZONTAL` por perfil de dispositivo, não implementado ainda porque não foi pedido.

Se um dia existir suporte a terminal em modo retrato, ele precisa de uma constante de tamanho fixa própria (resolução real do terminal em pé, medida do mesmo jeito) — mas seguindo a mesma regra: nunca variável, sempre a resolução física real.

Verificado após a correção: art regenerada via `/admin/gerar-arte/<codbar>` saiu em exatamente `(1280, 800)` (`PIL.Image.size`), e testada de ponta a ponta no terminal físico (`SK100_Mupa`, serial `4001442606003389`) — arte cobrindo a tela inteira, sem corte nas bordas e sem tarja.

### Painel gráfico de cor sólida com borda em curva (redesign do texto, referência: campanhas reais tipo Coca-Cola)

O cartão escuro semi-transparente original foi substituído por um **painel de cor sólida cobrindo a metade esquerda inteira**, com a borda direita em curva orgânica (uma onda suave, `_desenhar_painel_curvo`) em vez de uma linha reta — visual bem mais forte, de campanha publicitária de verdade, pedido explicitamente pelo usuário mostrando uma peça real da Coca-Cola como referência.

- `_cor_painel_vibrante(cor_acento)`: a cor extraída da embalagem (`_extrair_cor_acento`) às vezes sai clara/pálida demais pra um painel grande de cor sólida — essa função força saturação mínima e um brilho médio-escuro fixo (via HSV) pra sempre ter aquele efeito "cor de marca forte" (tipo o vermelho da Coca-Cola), sem depender de sorte na extração.
- `_desenhar_painel_curvo`: desenha o painel como um polígono cujo lado direito segue uma curva senoidal suave, não uma linha reta — e retorna o x mais à esquerda que a curva alcança, usado como limite seguro pro texto nunca colidir com a onda.
- O painel é **totalmente opaco** (alpha 255, não semi-transparente como o cartão antigo) — cobre completamente o cenário nessa metade, então o prompt (`LAYOUT`) foi ajustado pra deixar claro que a IA não precisa caprichar visualmente nessa área, só mantê-la limpa (sem texto/produto), porque o painel cobre tudo depois.
- Texto (nome + sublinhado branco + headline) fica na faixa superior do painel (começa ~13% da altura) — deixa espaço vazio na faixa inferior (~92% da altura) porque é ali que o app Android sobrepõe o preço depois (`PlayerActivity.updatePriceBadge`); o painel sendo full-height serve de fundo bonito pro preço também, não só pro texto.
- Prompt também ganhou ênfase em **gotas de condensação realistas** pra bebidas geladas (regra nova em CONCEITO VISUAL) e uma referência explícita de padrão de qualidade ("campanha real de grandes marcas como Coca-Cola/Nestlé/Ambev") em vez de só "premium genérico".

Testado gerando a arte do produto EXATO da referência do usuário (Coca-Cola Sabor Original Pet 600ml, EAN 7894900011609) — resultado visualmente muito próximo do exemplo mostrado: painel vermelho com curva, título em negrito com sublinhado, garrafa com condensação realista, cena de mesa/jardim apetitosa que a própria IA compôs dentro das regras.

### Ajustes seguintes (benefícios à direita, cor fiel ao produto, quantidade sempre visível)

Depois do primeiro resultado, mais 4 ajustes pedidos pelo usuário:

1. **Cor do painel sempre combinando com o produto**: `_extrair_cor_acento` tinha um fallback fixo vermelho (200,30,30) — funcionava bem pra produtos vermelhos por coincidência, mas ficava visualmente ERRADO pra qualquer outro produto quando a extração falhava (embalagem muito neutra/acromática). Trocado pra um cinza-chumbo neutro (45,48,56), que nunca destoa de nenhum produto. Também afrouxado o limiar de saturação (0.35→0.28) e aumentada a granularidade do quantize (8→12 cores) pra achar a cor dominante com mais precisão. Testado num produto verde (Dove Men+Care) — painel saiu verde, não mais vermelho por padrão.
2. **Produto sempre 100% visível**: reduzido o alcance máximo da curva do painel (base 0.47→0.44, amplitude 0.045→0.035 da LARGURA, não da altura — era um bug de unidade) pra nunca chegar perto dos 50% e arriscar cobrir o produto; e reforçada a regra no prompt (`ARTE_PROMPT_TEMPLATE`) proibindo qualquer corte do produto pelas bordas.
3. **Quantidade/tamanho da embalagem sempre visível** (ex.: 600ml, 1kg, 2L): a IA recebeu instrução explícita pra sempre incluir esse dado no nome gerado, mas ainda assim omitia às vezes — e mesmo quando incluía, o nome ficava tão longo que a linha com a quantidade era cortada pelo limite de linhas do título. Solução definitiva: `_extrair_quantidade_embalagem` (regex) SEMPRE remove a quantidade de dentro da string do nome (se a IA tiver incluído) e retorna ela separada; `compor_texto_na_arte` desenha a quantidade como uma linha PRÓPRIA e garantida logo após o nome (até 3 linhas) — nunca mais disputando espaço/sendo cortada junto com o resto do nome.
4. **Bullets de benefício movidos pro lado direito** (sobre a foto do produto, não mais no painel esquerdo) + **fonte da headline 40% menor**: o painel esquerdo agora só tem nome + quantidade + headline (bem mais compacto), deixando a maior parte da faixa inferior vazia pro card de preço que o app Android sobrepõe depois — era isso que "os benefícios no painel esquerdo" estava disputando espaço com. Do lado direito, os 3 bullets ficam encostados na borda direita, empilhados e centralizados verticalmente, cada um com um círculo na cor do painel + checkmark branco + texto com sombra escura (`_desenhar_texto_com_sombra`) — sombra necessária porque, ao contrário do painel de cor sólida, aqui o texto fica sobre a cena gerada pela IA, sem uma cor de fundo previsível atrás.

Retorno de `gerar_textos_arte_ia` mudou de `(nome, headline)` para `(nome, headline, beneficios, quantidade, marca)` — qualquer código futuro que chame essa função direto (fora de `gerar_arte_publicitaria`) precisa desempacotar os 5 valores.

### Prioridade da fonte da descrição: banco → Cosmos → Open Food Facts → Zaffari → Google (sempre, não só quando corrompido)

Cogitamos primeiro usar o nome que a API de preço PRÓPRIA de cada cliente/loja retorna (mais específico por estabelecimento, mas tipicamente abreviado — ex.: "REFRIG COCA COLA 600ML") como fonte pro nome da arte, e cheguei a implementar isso (mplayer enviando `nome_cliente` na query string pro `produto-imagem/<ean>/gerar-arte`). **O usuário pediu pra reverter** — decisão final foi manter a fonte só no lado do produtos-imgs, sem depender do app enviar nada extra:

1. `produto.description` (nosso banco) primeiro.
2. Se estiver **vazio OU corrompido** (antes só disparava por corrupção — `not descricao_fonte.strip()` foi adicionado como segundo gatilho), busca pelo EAN nessa ordem: Cosmos → Open Food Facts → Zaffari → **Google** (`fetch_product_from_google`, adicionado à lista — usa uma busca de imagem com Custom Search API que às vezes retorna um título de produto útil; nota: o dict que essa função retorna tem `marca` como string solta, não `brand: {name}` aninhado como as outras fontes — o código já trata os dois formatos).
3. Só DEPOIS de resolvida a melhor descrição bruta disponível é que a IA entra pra reconstruir/limpar o nome comercial final.

Nenhuma mudança foi mantida no mplayer por causa disso (a tentativa de passar `nome_cliente` foi revertida por completo).

### Hierarquia tipográfica: marca em destaque + resto do nome pequeno/leve

Pedido explícito do usuário com valores de referência (~80px peso 400 pra marca, ~40px peso 200 pro resto — escalados proporcionalmente à altura da imagem, não em pixels fixos, como todo o resto do texto). A fonte variável Montserrat já tem os pesos nomeados certos: `Regular` ≈ peso 400, `ExtraLight` ≈ peso 200 (confirmado via `font.get_variation_names()`).

- `_separar_marca_do_nome(nome, marca)`: separa a marca do resto do nome (remove o prefixo se o nome já começar pela marca, ex. `nome='Coca-Cola Sabor Original'` + `marca='Coca-Cola'` → `('Coca-Cola', 'Sabor Original')`); se não bater o prefixo, mantém o nome inteiro como "resto" mesmo assim (prefere uma pequena redundância a perder o destaque da marca).
- Quando há marca identificável: marca desenhada grande (peso Regular), resto do nome + quantidade juntos numa fonte ~metade do tamanho (peso ExtraLight) — a quantidade não precisa mais de uma linha garantida em fonte grande nesse tamanho reduzido, cabe tranquilamente junto com o resto.
- Sem marca identificável: cai pro estilo antigo (nome inteiro grande e em negrito, quantidade como linha garantida) — mais seguro que não destacar nada.
- Testado com marca de 1 palavra (Coca-Cola) e 3 palavras (Dove Men Care) — quebra de linha e proporção funcionam bem nos dois casos.

Também reduzida a fonte da headline em 40% (pedido do usuário) — o painel esquerdo ficou visivelmente mais enxuto (marca + resto + headline, tudo mais compacto), sobrando bem mais espaço vazio na faixa inferior pro card de preço.

### Bullets de benefício: cápsula translúcida + ancorados na BASE (não mais centralizados)

Pedido do usuário pra "baixar um pouco mais" o bloco de bullets expôs um problema que antes passava despercebido: o bloco de bullets é sempre encostado na borda direita (`icone_x = int(width * 0.965) - diametro_icone`), então o texto (que cresce pra ESQUERDA do ícone, `alinhar_direita=True`) cai onde quer que a garrafa/produto esteja naquela altura — quando centralizado verticalmente, o texto caía direto em cima do rótulo do produto, ilegível mesmo com a sombra (`_desenhar_texto_com_sombra` sozinha não é suficiente contra um fundo com textura/contraste, só contra fundo liso).

Duas correções, na ordem em que foram pedidas:
1. **Cápsula translúcida** em `_desenhar_bullet_beneficio`: quando `alinhar_direita=True` (sempre o caso do lado direito, sobre a foto), desenha uma cápsula semitransparente (`fill=(20,20,24,140)`, `rounded_rectangle` com raio = metade da altura) atrás do grupo ícone+texto, ANTES do ícone e do texto. Garante legibilidade em qualquer parte da cena, inclusive sobre o rótulo do produto — mesmo recurso usado em peças publicitárias reais (badge/tarja atrás de texto sobre foto), não é só um workaround. Não usar essa cápsula do lado não-`alinhar_direita` (painel esquerdo) — lá o fundo já é sólido, cápsula seria redundante.
2. **Ancorado na base em vez de centralizado**: usuário pediu explicitamente ("os benefícios fiquem na base, com uma margem pra não encostar na base") — `bloco_y` deixou de ser `(height - altura_bloco) / 2 + offset` (cálculo a partir do centro) e passou a ser `height - margem_inferior - altura_bloco` (`margem_inferior = int(height * 0.08)`), ancorado a partir da borda inferior. Fica na mesma faixa vertical inferior onde a cena costuma ter menos elementos importantes (chão/mesa/gelo), e não compete mais com a parte superior do produto.

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

### Fila única de geração de arte (protege contra rate limit do Gemini)

Com vários terminais em lojas/clientes diferentes, gerar arte em paralelo (uma thread por request, como era antes) estourava o rate limit do Gemini (429 RESOURCE_EXHAUSTED). Agora existe **uma fila única processada por um único worker** (`_fila_arte`, `_worker_fila_arte`, `_enfileirar_geracao_arte`) — nunca há mais de uma chamada ao Gemini em andamento ao mesmo tempo, custe o que custar em latência sob carga.

- `_enfileirar_geracao_arte(codbar, img_path, aguardar=False, forcar=False)` é o único ponto de entrada. `aguardar=True` bloqueia até o job terminar (usado onde a rota precisa devolver a `arte_url` na resposta); sem isso é fire-and-forget. `forcar=True` ignora a checagem de "arte já existe" (usado pelo botão de regenerar).
- Os 3 pontos que geram arte no sistema passam todos pela mesma fila: o disparo automático em `GET /produto-imagem/<codbar>` (fire-and-forget), o botão "Gerar/Regenerar arte" do admin (`aguardar=True, forcar=True`) e a rota pública `POST /produto-imagem/<codbar>/gerar-arte` usada pelo app (`aguardar=True`). O contrato HTTP de cada uma **não mudou** — só a execução interna passou a ser serializada.
- O worker refaz a consulta do `Produto` por conta própria dentro do seu próprio `app_context()`, em vez de reaproveitar o objeto SQLAlchemy que o request original carregou — evita problemas de sessão entre threads.
- Sem persistência: se o Flask reiniciar com jobs na fila, eles se perdem, mas isso é inofensivo — a próxima consulta em `GET /produto-imagem/<codbar>` detecta que a arte ainda não existe e reenfileira sozinha (mesmo padrão de auto-recuperação que já existia antes da fila).
- Testado com concorrência real (3 gerações disparadas juntas com `gerar_arte_publicitaria` mockado por um `time.sleep`) confirmando que a concorrência máxima observada é sempre 1, e com uma geração real via Gemini (`POST /admin/gerar-arte/<codbar>`) confirmando que o contrato da rota não mudou.
- Visibilidade: `GET /admin/status-sistema` retorna `fila_arte.pendentes` e `fila_arte.em_processamento`, mostrado ao vivo (poll a cada 5s) num pequeno painel no topo da aba Consulta Rápida.

### Consumo do Gemini (visibilidade, já que o console do Google não é prático pro dia a dia)

Cada chamada real ao Gemini (`gerar_arte_publicitaria` — modelo de imagem — e `gerar_textos_arte_ia` — modelo de texto) é contabilizada via `_registrar_uso_gemini(categoria, sucesso, rate_limited)`, separando sucesso / bloqueio por limite de taxa (429/RESOURCE_EXHAUSTED, detectado pela string do erro) / outro erro, para cada categoria ('imagem'/'texto'). `_status_gemini()` lê esses contadores; aparecem tanto em `GET /admin/status-sistema` (ao vivo) quanto no resumo diário por e-mail/WhatsApp — resetados a cada resumo enviado com sucesso, igual aos contadores do Cosmos (reportam "desde o último resumo", não total histórico).

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

Duas formas de cadastrar um produto que não está no catálogo local, ambas na aba "Consulta Rápida":

1. **Botão "+ Cadastrar produto"** (sempre visível, ao lado da busca do catálogo) — abre um formulário dedicado que pede só o EAN e busca **exclusivamente no Cosmos** (`POST /admin/cadastrar-produto-cosmos/<codbar>`, com a rotação de tokens de `fetch_product_from_cosmos`). Se o EAN já existir localmente, avisa e não duplica. Se não achar no Cosmos, retorna 404 sem tentar outras fontes — é um fluxo deliberadamente restrito ao Cosmos, pedido explicitamente pelo usuário.
2. **Botão no estado vazio da busca** — quando a busca por um EAN (8 a 14 dígitos) no catálogo local não encontra nada, aparece um botão que chama `GET /produto/<codbar>` — a mesma rota que já fazia cadastro automático multi-fonte em outros pontos do sistema (Cosmos → Open Food Facts → Zaffari, nessa ordem). Esse é o caminho "genérico", mantido como estava.

A imagem (thumbnail) que a fonte externa retorna já é baixada e salva automaticamente em ambos os fluxos (`register_product_in_database` + o bloco que salva `dados.get('thumbnail')`) — não precisou de nada novo pra isso.

### Rotação de tokens do Cosmos (plano free, cota por token)

O Cosmos free tem cota mensal por token. Em vez de um único `COSMOS_API_TOKEN`, o painel agora guarda uma **lista** (`COSMOS_API_TOKENS`, um por linha, textarea em Configurações → "Tokens Cosmos"). `fetch_product_from_cosmos`:
- Começa pelo índice guardado em `COSMOS_TOKEN_INDEX_ATUAL` (não sempre do token #1 — evita re-testar tokens já sabidamente esgotados a cada chamada).
- Se a resposta for **401/402/403/429** (token inválido ou cota esgotada), avança pro próximo token da lista e tenta de novo, até esgotar a lista inteira.
- Se a resposta for **404** (produto não existe no Cosmos — não é problema de token), retorna `None` na hora, sem rotacionar.
- Ao ter sucesso ou esgotar todos os tokens, persiste o índice final em `COSMOS_TOKEN_INDEX_ATUAL` pra próxima chamada já começar dali.
- Contadores `STATS_COSMOS_SUCESSOS` e `STATS_COSMOS_ROTACOES` (Config) acumulam desde o último resumo diário enviado (zerados lá, não aqui) — usados no e-mail/WhatsApp de resumo.

Testado com tokens falsos direto contra a API real do Cosmos (confirma 401 em cada um → rotaciona → esgota → `None`) e com `smtplib`/`requests` mockados via monkeypatch pra validar o caminho de sucesso (token 2 funciona, índice persiste, chamada seguinte já pula direto pro token 2).

**Cuidado, API do Cosmos real é DIFERENTE da assumida inicialmente** — a primeira versão usava `https://api.cosmos.bluesoft.com.br/gtins/<ean>.json` com `Authorization: Bearer <token>` (formato copiado do código antigo/hardcoded já existente no projeto antes desta sessão). Com tokens reais do usuário, isso retornava 401 "Token Inválido" em TODOS os tokens — confirmado com `curl` direto que a causa não era token inválido de verdade, mas **endpoint e header errados**. O formato correto (confirmado pelo usuário rodando um `curl` que funcionou, e replicado com sucesso):
```
GET https://cosmos.bluesoft.com.br/api/gtins/<ean>.json
Header: X-Cosmos-Token: <token>   (não é Authorization: Bearer!)
```
`fetch_product_from_cosmos` já foi corrigido pra usar essa URL/header. Testado com tokens reais após a correção — cadastro via `/admin/cadastrar-produto-cosmos/<ean>` funcionou (retornou produto real, HTTP 201).

## WhatsApp — gestão de instância via Evolution API (aba dedicada)

Nova aba "WhatsApp" no painel faz a gestão completa de uma instância na [Evolution API](https://docs.evolutionfoundation.com.br/evolution-api/installation) (servidor self-hosted) — criar instância, mostrar QR code pra escanear, checar status de conexão, listar todas as instâncias do servidor, desconectar, excluir e testar envio. Reaproveita as mesmas chaves de Config já usadas pelo resumo diário (`WHATSAPP_BASE_URL`, `WHATSAPP_TOKEN`, `WHATSAPP_INSTANCE`, `WHATSAPP_NUMERO_DESTINO`) — criar/conectar uma instância aqui é o que torna o canal WhatsApp do resumo diário funcional.

**Contrato real da Evolution API** (confirmado contra um servidor real do usuário, não documentação genérica assumida):
- `POST /instance/create` — header `apikey`, body `{instanceName, qrcode: true, integration: "WHATSAPP-BAILEYS", number?}`. **Não mandar `token` no body** — se for igual ao apikey global (ou de outra instância já existente), a API rejeita com `"Token already exists"`; deixar a Evolution gerar o hash da instância sozinha. Resposta traz `qrcode.base64` (data URI, pronta pra `<img src>`) e `qrcode.pairingCode`.
- `GET /instance/connect/{instanceName}` — regenera o QR (útil quando o anterior expira antes de escanear). Resposta: `{base64, pairingCode, code}` (sem o wrapper `qrcode.`, direto na raiz — diferente do `/create`).
- `GET /instance/connectionState/{instanceName}` — resposta `{instance: {state}}`, `state` ∈ `open` (conectado) / `close` (desconectado) / `connecting`.
- `GET /instance/fetchInstances` — lista todas as instâncias do servidor (array de `{instance: {instanceName, status, connectionStatus: {state}}}`).
- `DELETE /instance/logout/{instanceName}` — desconecta sem apagar a instância.
- `DELETE /instance/delete/{instanceName}` — apaga a instância.
- `POST /message/sendText/{instanceName}` — body `{number, textMessage: {text}}` — **atenção**: o campo é `textMessage.text` (aninhado), não `text` solto na raiz. Esse era um bug real no código de envio do resumo diário (`_enviar_whatsapp_resumo_diario`) escrito antes de eu confirmar o contrato oficial — já corrigido.

O painel faz polling de `GET /admin/whatsapp/status` a cada 4s enquanto uma instância está configurada, escondendo o QR automaticamente assim que o estado vira `open`. A opção de provedor "Z-API" (genérica, nunca testada) foi removida da UI — o sistema hoje só suporta Evolution API de verdade, com essa página dedicada.

## Gestão de imagem por produto (painel)

O painel de detalhes (Consulta Rápida) ganhou "Enviar/Trocar imagem" (upload via `POST /upload-imagem-produto/<codbar>`, sobrescreve) e "Excluir imagem" (via `DELETE /deletar-imagem-produto/<codbar>`, com confirmação). Ambas as rotas já existiam — só não estavam expostas na UI. **Excluir a foto crua não apaga a arte publicitária já gerada** (são arquivos independentes, ver seção de arte acima) — intencional, mencionado no próprio diálogo de confirmação.
