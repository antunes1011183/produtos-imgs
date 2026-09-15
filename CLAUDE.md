# produtos-imgs — Mupa Brain

API Flask de gestão de produtos/imagens para o app de consulta de preço da Mupa (`mplayer`). Roda na porta **5050**. Painel admin em `/configuracoes`.

## Tema escuro por padrão, com toggle pra claro

Pedido do usuário. `<html>` já nasce com `data-theme="dark"` (renderizado assim pelo servidor — evita flash de tema claro antes do JS rodar). Botão no topbar (`#btn-theme-toggle`, ícone lua/sol) alterna `data-theme` entre `"dark"`/`"light"` e persiste em `localStorage` (`mupa_painel_tema`); sem preferência salva, sempre abre escuro.

Implementado quase inteiramente via CSS custom properties: `html[data-theme="dark"] { --bg: ...; --card: ...; --border: ...; --text: ...; --muted: ...; --accent: ...; ... }` redefine os mesmos tokens que o `:root` já usa em claro — como o resto do CSS já era escrito em cima de `var(--bg)`/`var(--card)`/etc. (não cores literais), a troca de tema se propaga sozinha pro resto do painel sem precisar duplicar regras. Alguns lugares tinham cor **literal** hardcoded em vez de variável (`background:#fff` em inputs/botões/menu-toggle, `background:#f8fafc` no hover de botão secundário, `background:#fafbff` em 4 cards aninhados) — trocados por `var(--card)`/`var(--hover)`/`var(--bg)` pra também respeitarem o tema. A barra lateral (`--sidebar-*`) não precisou de bloco novo — já era escura nos dois temas.

**Não perseguido nesta leva** (aceito como inconsistência menor, não um bug): badges de status gerados via JS com cor pastel literal (ex. verde/vermelho claro dos badges "Encontrado"/"Não encontrado" no Histórico, âmbar do badge de tentativas) continuam com o mesmo hex em ambos os temas — ainda legíveis no escuro (fundo pastel + texto escuro), só não foram re-otimizados pixel a pixel pro tema escuro.

## Aba Histórico: painel de detalhes lateral (clicar no produto)

Pedido do usuário: clicar num produto da lista (qualquer filtro — agrupado "não encontrados" ou log bruto "todos"/"encontrados") abre um painel de detalhes na coluna direita, igual ao que já existia na Consulta Rápida. Em vez de generalizar o painel único da Consulta Rápida (`#detail-panel`/`openDetailModal`, fortemente acoplado a `loadConsulta`/`consultaPage`/`ultimoConsultaData`), criei um painel **paralelo e independente** pro Histórico (`#hist-detail-panel`/`openHistDetailPanel`, ids prefixados `hist-detail-*`) — mesmo HTML/CSS (`.consulta-layout`/`.consulta-detail-col`/`.detail-panel`/`.detail-modal-*`/`.detail-field`, todos genéricos o bastante pra reaproveitar), mesmas 3 rotas de backend (Buscar imagem, Gerar arte, Enviar/Trocar imagem), mas atualizando `loadHistorico(historicoPage)`/`removerLinhaHistorico` no lugar de `loadConsulta`. Evita acoplar os dois painéis e reduz risco de regressão na Consulta Rápida, ao custo de ~130 linhas de JS duplicadas (aceitável — mesmo padrão que `loadHistorico`/`loadConsulta` já coexistindo como implementações paralelas).

A aba Histórico ganhou o mesmo tratamento de layout largo que a Consulta Rápida (`$('#content')?.classList.toggle('wide', tab === 'consulta' || tab === 'historico')`).

`removerLinhaHistorico(codbar)` (já existia, das ideias 1-5) só encontra e remove `<tr data-codbar>` na vista **agrupada** — na vista de log bruto não há esse atributo no `<tr>` (só nos `<td>`, pra abrir o painel), então chamar a função ali é um no-op seguro. Corrigido um bug real encontrado ao ligar o painel de detalhes na vista de log: a função decrementava o contador "N registros" mesmo quando não achava/removia linha nenhuma — agora só decrementa se `row` existir.

**Pegadinha de teste, não bug**: `GET /produto/<ean>` (usado tanto pela Consulta Rápida quanto por este painel novo) monta a resposta com uma sugestão via Gemini (`gemini-2.5-flash-lite`) **síncrona** — a chamada inteira leva uns 15-25s. Ao testar/depurar esse painel, esperar tempo suficiente antes de checar o resultado (já aconteceu de eu achar, num primeiro momento, que o clique não funcionava — só estava sendo verificado cedo demais).

## Login real do painel (substituiu um bypass sério que existia antes)

Até esta sessão o painel **não tinha login de verdade**: `configuracoes.html` se autenticava sozinho no carregamento via `GET /painel/login`, uma rota que emitia um JWT válido pra `antunes@mupa.app` **sem checar senha nenhuma** — e, se essa rota falhasse, caía num fallback com as credenciais reais **hardcoded no próprio JavaScript** (visível a qualquer um vendo o código-fonte da página). Ou seja: `/configuracoes` era, na prática, uma página pública. Pedido do usuário pra corrigir isso ("adiciona um login" + "altere o index pra carregar o login"):

- **`GET /painel/login` foi removido** — não existe mais nenhum jeito de conseguir um token sem mandar credenciais reais.
- **`POST /login`** continua sendo o único jeito de autenticar, contra `CREDENCIAIS_PAINEL` (dict em `app.py`, atualmente `antunes@mupa.app` e `support@mupa.app` — adicionar um novo acesso é só adicionar uma entrada nesse dict).
- **Tela de login de verdade** em `configuracoes.html` (`#login-screen`): e-mail + senha, `POST /login`, token + `expires_at` salvos em `localStorage` (`mupa_painel_token`/`mupa_painel_token_expira`). Uma sessão salva e ainda válida (mesma janela de 1h do JWT) pula a tela de login no próximo carregamento; expirada ou ausente, sempre mostra a tela de login. Botão "Sair" na barra lateral limpa a sessão.
- **`GET /` (index)** não serve mais a ferramenta avulsa de remover fundo — só redireciona (302) pra `/configuracoes`, que decide sozinho se mostra a tela de login ou o painel (ver acima). A ferramenta de remover fundo continua existindo, só que migrou pra dentro do painel autenticado (ver seção "Aba Remover Fundo" abaixo).

Testado de ponta a ponta num navegador real: `GET /painel/login` agora dá 404; `GET /` dá 302 pra `/configuracoes`; senha errada é rejeitada (401) e mantém a tela de login; a credencial nova (`support@mupa.app`) loga normalmente e mostra o painel completo; sessão sobrevive a um reload da página (localStorage); "Sair" limpa tudo e volta pra tela de login.

## Aba "Remover Fundo" (migrada de `templates/index.html`, agora autenticada)

Existia uma ferramenta solta de remover fundo de imagem (upload ou URL, via `rembg`) servida direto na raiz (`templates/index.html`, Bootstrap/jQuery antigo, sem login nenhum). Virou uma aba normal do painel (`#tab-removerfundo`, ícone de imagem na barra lateral) — mesmas duas rotas de backend de sempre (`POST /remove_background_upload`, `POST /remove_background_url`), só que agora atrás de `@jwt_required()` (antes eram públicas, sem proteção nenhuma, qualquer um na internet podia processar imagens à vontade nelas). `templates/index.html` ficou órfão (sem rota apontando pra ele) — não foi apagado, mas pode ser removido com segurança se um dia isso incomodar.

Fluxo: escolher arquivo OU colar URL → botão "Remover fundo" → chama a rota correspondente com `authHeaders()` → mostra o resultado inline (imagem com fundo xadrez pra evidenciar a transparência) + link "Abrir em nova aba". Testado de ponta a ponta com uma imagem real do catálogo (Coca-Cola, EAN 7894900011609) — recorte limpo, resultado renderizado corretamente na aba.

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

**Condensação suavizada depois** — a instrução original ("cubra a embalagem com GOTAS DE CONDENSAÇÃO realistas e **abundantes**... superfície molhada/brilhante") saía pesada demais em testes seguintes (garrafa toda molhada, filetes escorrendo, às vezes prejudicando a legibilidade do rótulo). Trocada por uma versão mais comedida: "leve toque de condensação, sutil e discreto (algumas gotículas pequenas e esparsas, sem filetes escorrendo nem superfície toda molhada)... a etiqueta e o design do produto continuam totalmente legíveis". Testado de novo na mesma Coca-Cola — poucas gotículas discretas, rótulo 100% legível, ainda comunica "gelado" sem dominar a cena.

### Ajustes seguintes (benefícios à direita, cor fiel ao produto, quantidade sempre visível)

Depois do primeiro resultado, mais 4 ajustes pedidos pelo usuário:

1. **Cor do painel sempre combinando com o produto**: `_extrair_cor_acento` tinha um fallback fixo vermelho (200,30,30) — funcionava bem pra produtos vermelhos por coincidência, mas ficava visualmente ERRADO pra qualquer outro produto quando a extração falhava (embalagem muito neutra/acromática). Trocado pra um cinza-chumbo neutro (45,48,56), que nunca destoa de nenhum produto. Também afrouxado o limiar de saturação (0.35→0.28) e aumentada a granularidade do quantize (8→12 cores) pra achar a cor dominante com mais precisão. Testado num produto verde (Dove Men+Care) — painel saiu verde, não mais vermelho por padrão.
2. **Produto sempre 100% visível**: reduzido o alcance máximo da curva do painel (base 0.47→0.44, amplitude 0.045→0.035 da LARGURA, não da altura — era um bug de unidade) pra nunca chegar perto dos 50% e arriscar cobrir o produto; e reforçada a regra no prompt (`ARTE_PROMPT_TEMPLATE`) proibindo qualquer corte do produto pelas bordas.
3. **Quantidade/tamanho da embalagem sempre visível** (ex.: 600ml, 1kg, 2L): a IA recebeu instrução explícita pra sempre incluir esse dado no nome gerado, mas ainda assim omitia às vezes — e mesmo quando incluía, o nome ficava tão longo que a linha com a quantidade era cortada pelo limite de linhas do título. Solução definitiva: `_extrair_quantidade_embalagem` (regex) SEMPRE remove a quantidade de dentro da string do nome (se a IA tiver incluído) e retorna ela separada; `compor_texto_na_arte` desenha a quantidade como uma linha PRÓPRIA e garantida logo após o nome (até 3 linhas) — nunca mais disputando espaço/sendo cortada junto com o resto do nome.
4. **Bullets de benefício movidos pro lado direito** (sobre a foto do produto, não mais no painel esquerdo) + **fonte da headline 40% menor**: o painel esquerdo agora só tem nome + quantidade + headline (bem mais compacto), deixando a maior parte da faixa inferior vazia pro card de preço que o app Android sobrepõe depois — era isso que "os benefícios no painel esquerdo" estava disputando espaço com. Do lado direito, os 3 bullets ficam encostados na borda direita, empilhados e centralizados verticalmente, cada um com um círculo na cor do painel + checkmark branco + texto com sombra escura (`_desenhar_texto_com_sombra`) — sombra necessária porque, ao contrário do painel de cor sólida, aqui o texto fica sobre a cena gerada pela IA, sem uma cor de fundo previsível atrás.

Retorno de `gerar_textos_arte_ia` mudou de `(nome, headline)` para `(nome, headline, beneficios, quantidade, marca)` — qualquer código futuro que chame essa função direto (fora de `gerar_arte_publicitaria`) precisa desempacotar os 5 valores.

### Prioridade da fonte da descrição: banco → Cosmos → Open Food Facts → Zaffari → Google → PreçoMelhor (sempre, não só quando corrompido)

Cogitamos primeiro usar o nome que a API de preço PRÓPRIA de cada cliente/loja retorna (mais específico por estabelecimento, mas tipicamente abreviado — ex.: "REFRIG COCA COLA 600ML") como fonte pro nome da arte, e cheguei a implementar isso (mplayer enviando `nome_cliente` na query string pro `produto-imagem/<ean>/gerar-arte`). **O usuário pediu pra reverter** — decisão final foi manter a fonte só no lado do produtos-imgs, sem depender do app enviar nada extra:

1. `produto.description` (nosso banco) primeiro.
2. Se estiver **vazio OU corrompido** (antes só disparava por corrupção — `not descricao_fonte.strip()` foi adicionado como segundo gatilho), busca pelo EAN nessa ordem: Cosmos → Open Food Facts → Zaffari → Google (`fetch_product_from_google`, usa uma busca de imagem com Custom Search API que às vezes retorna um título de produto útil; nota: o dict que essa função retorna tem `marca` como string solta, não `brand: {name}` aninhado como as outras fontes — o código já trata os dois formatos) → **PreçoMelhor** (`fetch_product_from_precomelhor`, adicionado por último na cadeia, ver seção própria abaixo).
3. Só DEPOIS de resolvida a melhor descrição bruta disponível é que a IA entra pra reconstruir/limpar o nome comercial final.

### Nova fonte: PreçoMelhor (precomelhor.com.br) — só nome/marca, sem imagem

`fetch_product_from_precomelhor(ean)` consulta a API pública e gratuita do PreçoMelhor: `GET https://www.precomelhor.com.br/api/nutrition-lookup?ean=<ean>`. Documentada abertamente no próprio site deles (`precomelhor.com.br` → "Ferramentas & APIs Gratuitas" → seção "Desenvolvedores"), sem necessidade de chave/token.

**Particularidade importante, só descoberta testando na prática** (o `curl` direto tomou `403` — proteção anti-bot da Cloudflare; testei via browser real pra confirmar o contrato): o campo `success` do JSON vem **sempre `true`**, mesmo pra um EAN que não existe na base deles — o sinal real de "não encontrado" é `product_name` vindo como string vazia (`""`), não o campo `success`. `fetch_product_from_precomelhor` checa isso corretamente (`if not description: return None`), mas qualquer código novo que chamar essa API direto precisa saber disso — confiar só em `success` faria o app achar que TODO EAN foi encontrado.

Também tem um endpoint de imagem (`/api/image/<ean>?w=200`), mas ele só devolve um SVG placeholder genérico ("Sem Imagem") pra qualquer EAN testado, nunca uma foto real — por isso essa fonte não é usada pra buscar foto crua (`buscar_e_salvar_imagem_bing/google/zaffari`), só pra descrição/marca, igual Open Food Facts e Zaffari.

Adicionado nas 3 cadeias de fallback (fim da lista, prioridade mais baixa por ser a fonte mais nova/menos testada em produção): a rota `GET /produto/<codbar>` (cadastro automático genérico), o loop de correção de texto corrompido em `gerar_textos_arte_ia`, e o auto-cadastro dentro de `gerar_arte_publica` (`POST /produto-imagem/<ean>/gerar-arte`). Testado de ponta a ponta com o EAN de exemplo do usuário (`7896577211627`, Pepinos em Conserva Agridoce Fatiado Petry 440g) — `register_product_in_database` cadastrou corretamente `description`/`marca`, registro removido depois só por ser dado de teste.

Nenhuma mudança foi mantida no mplayer por causa disso (a tentativa de passar `nome_cliente` foi revertida por completo).

### Hierarquia tipográfica: marca em destaque + resto do nome pequeno/leve

Pedido explícito do usuário com valores de referência (~80px peso 400 pra marca, ~40px peso 200 pro resto — escalados proporcionalmente à altura da imagem, não em pixels fixos, como todo o resto do texto). A fonte variável Montserrat já tem os pesos nomeados certos: `Regular` ≈ peso 400, `ExtraLight` ≈ peso 200 (confirmado via `font.get_variation_names()`).

- `_separar_marca_do_nome(nome, marca)`: separa a marca do resto do nome (remove o prefixo se o nome já começar pela marca, ex. `nome='Coca-Cola Sabor Original'` + `marca='Coca-Cola'` → `('Coca-Cola', 'Sabor Original')`); se não bater o prefixo, mantém o nome inteiro como "resto" mesmo assim (prefere uma pequena redundância a perder o destaque da marca).
- Quando há marca identificável: marca desenhada grande (peso Regular), resto do nome + quantidade juntos numa fonte ~metade do tamanho (peso ExtraLight) — a quantidade não precisa mais de uma linha garantida em fonte grande nesse tamanho reduzido, cabe tranquilamente junto com o resto.
- Sem marca identificável: usa a **1ª palavra do nome** como destaque (mesma regra de peso/tamanho da marca de verdade) em vez de cair pro estilo antigo (nome inteiro grande e uniforme, sem hierarquia). Mudado depois que um requeijão Vigor saiu com "Requeijão cremoso tradicional 400G" tudo do mesmo tamanho porque nenhuma fonte (banco/Cosmos/OFF/Zaffari/Google/AI) resolveu a marca daquela vez — não é a marca de verdade, mas garante que SEMPRE haja hierarquia tipográfica, nunca um bloco de texto uniforme. Só cai pro `None` (nome inteiro, sem split) quando o nome é uma única palavra sem "resto" possível.
- Testado com marca de 1 palavra (Coca-Cola), 3 palavras (Dove Men Care) e sem marca identificável (Requeijão Vigor, usando "Requeijão" como palavra de destaque) — quebra de linha e proporção funcionam bem nos três casos.

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

## Atualização automática via GitHub (polling, não webhook)

Pedido do usuário: "quero que o sistema se atualize quando o github receber um push/commit". `_iniciar_agendador_atualizacao()` (thread daemon, mesmo padrão dos outros agendadores) confere a cada 5 minutos se há commit novo em `origin/<branch atual>` e, se houver, aplica sozinho: `git pull` + reinício do processo via `os.execv(sys.executable, [sys.executable] + sys.argv)`.

**Por que polling, não webhook**: essa máquina (e qualquer outra rodando isso numa loja/rede local) não tem garantia de estar acessível publicamente pra receber a chamada HTTP que o GitHub faria num push — então em vez de "o GitHub me avisa", o sistema "pergunta sozinho de tempos em tempos". Teto de 5 minutos entre um push e o sistema notar, não é instantâneo.

**`_git(*args)`**: helper que centraliza toda chamada ao git (`subprocess.run(['git', *args], cwd=BASE_DIR, encoding='utf-8', errors='replace', ...)`) — usado por `_info_versao_git` e `_verificar_e_aplicar_atualizacao_github`. O `encoding='utf-8'` explícito importa de verdade: sem ele, `text=True` sozinho usa o locale padrão do Windows (não é UTF-8) pra decodificar a saída do git, e uma mensagem de commit com acento virava mojibake (`"árvore"` → `"Ã¡rvore"`) — bug real, pego testando o badge de versão no header (próxima seção).

**Proteção principal contra sobrescrever trabalho local**: só faz `git pull` se não houver mudança em arquivo **rastreado** (`git status --porcelain`, ignorando linhas `??` de arquivo nunca versionado). Passou por uma correção real: a primeira versão tratava QUALQUER linha do `--porcelain` como "sujo", inclusive arquivos soltos nunca rastreados — como esta máquina tem ~12 scripts/screenshots avulsos na raiz (débito antigo, sem relação com o código de verdade), isso deixava o auto-update permanentemente bloqueado aqui, mesmo depois de consertar o `.gitignore`. Corrigido pra só considerar "sujo" mudança em arquivo já rastreado (modificado/deletado/staged) — um arquivo nunca versionado não é risco pra um `git pull` na prática.

**Detecção de "tem atualização" também corrigida**: a primeira versão comparava só `local == remoto` (hash do HEAD local vs. `origin/<branch>`) — bug real pego em teste: quando o LOCAL está à frente do remoto (commit feito aqui, ainda não enviado ao GitHub), os hashes são diferentes mas não há nada pra puxar; a função tentava um pull desnecessário mesmo assim. Corrigido com `git rev-list --count HEAD..origin/<branch>` — só considera "atualização disponível" quando esse número é `> 0` (origin tem commit(s) que local realmente não possui).

**`os.execv` pra reiniciar, testado isoladamente**: confirmado funcionando no Windows com um script de teste que se reinicia sozinho 2x em sequência via `os.execv` antes de terminar — a abordagem funciona tanto rodando `python app.py` quanto no `.exe` compilado pelo PyInstaller (`sys.executable` aponta pro lugar certo nos dois casos, ver seção de packaging), sem precisar de nenhum supervisor externo (NSSM, serviço do Windows) pra "trazer de volta" o processo depois do restart — ele se relança sozinho.

Se o pull for aplicado com sucesso, dispara um aviso por WhatsApp antes de reiniciar (`_notificar_atualizacao_whatsapp`, reaproveita `_enviar_texto_whatsapp` — o mesmo helper genérico que o heartbeat horário usa, extraído dele nesta mesma leva). Controlado por `AUTO_UPDATE_ATIVO` (Config, default `'true'`, sem toggle na UI ainda — só setável direto no banco se precisar desligar).

### Badge de versão no header do painel (commit atual)

Pedido do usuário, pra conferir num relance (sem abrir log de servidor) se/quando o auto-update aplicou algo: o topbar do painel (`/configuracoes`) mostra um badge com a data do último commit + hash curto (tooltip com a mensagem completa). `_info_versao_git()` roda `git log -1 --format=%h|%cI|%s` e passa `versao={hash, data_iso, mensagem}` pro template (`None` se não for um repositório git — o bloco some do header nesse caso, `{% if versao %}`). Data formatada no cliente via `toLocaleString('pt-BR')` a partir do ISO, mesmo padrão usado no resto do painel.

**Não testado de ponta a ponta**: o caminho completo "árvore limpa + commit novo remoto → pull → restart" (tentei clonar uma cópia limpa do repo pra testar isolado, mas o Windows recusou por causa de caminhos longos demais dentro do `venv` — só resolvido DEPOIS que o `.gitignore` foi corrigido, então o teste isolado já não fazia mais sentido refazer). O que FOI verificado individualmente, com testes reais nesta máquina: (1) a guarda de árvore suja distingue corretamente rastreado vs. não-rastreado, (2) a detecção "tem atualização" distingue corretamente à-frente vs. atrás, (3) `git fetch`/`rev-parse`/`rev-list`/`pull` funcionam via subprocess a partir do Python nativo do venv, com encoding correto, (4) `os.execv` reinicia o processo corretamente, (5) o badge de versão no header renderiza certo. A composição completa desses pedaços — um push real chegando e sendo aplicado sozinho — ainda não foi vista rodando de ponta a ponta; deve acontecer naturalmente no próximo push depois desta sessão.

- A API do Bing Image Search (`buscar_e_salvar_imagem_bing`) está retornando `410 Gone` — a Microsoft descontinuou essa API. Na prática a cadeia de busca de foto crua (local → Bing → Google → Zaffari) pula direto pro Google na maioria dos casos. Não corrigido ainda; considerar remover o passo do Bing ou trocar por outra fonte se isso virar um problema real.

### RESOLVIDO: `.gitignore` corrompido causava `venv/` (16.6k arquivos!) rastreado por engano

Ficava documentado aqui como "`venv/` parcialmente rastreado, nunca usar `git add -A`" — investigando por causa do auto-update automático (seção "Atualização automática via GitHub" abaixo), achei a causa raiz: o `.gitignore` tinha as regras certas (`venv/`, `static/`, `instance/`) mas **metade do arquivo estava em UTF-16**, escrita por cima de um arquivo que começou em UTF-8 (bytes confirmados com `xxd`: cada caractere virava "letra + `0x00`" a partir de certo ponto) — git nunca conseguiu interpretar essas linhas como padrões válidos, então elas nunca funcionaram.

Corrigido: `.gitignore` reescrito do zero em UTF-8 puro (`venv/`, `__pycache__/`, `*.pyc`, `static/`, `instance/`, `*.log`, `build/`/`dist/`/`*.spec` do PyInstaller) + `git rm -r --cached` nos ~16.8k arquivos afetados (venv + static + pycache) — **só tira do índice do git, não apaga nada do disco**, os arquivos continuam exatamente onde estavam. A regra "nunca usar `git add -A`/`git commit -a`" neste repo pode ser reavaliada agora que o `.gitignore` funciona de verdade, mas por precaução (não testado a fundo ainda com um `git add -A` real) continue preferindo `git add <arquivo>` explícito por enquanto.

## Histórico de buscas e resumo diário do sistema

Tabela `HistoricoBuscaImagem` registra toda consulta de imagem — sucesso ou falha — tanto do terminal (`GET /produto-imagem/<codbar>`, `via='terminal'`) quanto de uma retentativa manual no painel (`POST /admin/buscar-imagem/<codbar>`, `via='admin'`). Serve dois propósitos com a mesma tabela: histórico de uso (aba "Histórico" do painel) e fila de pendências para notificação (linhas com `encontrado=False` e `notificado_em=NULL`).

### Aba "Histórico" → filtro "Não encontrados": agrupado por EAN + ações inline (resolução rápida)

Problema real identificado numa sessão de UX com o usuário: a aba só listava, sem nenhuma ação — pra resolver uma imagem faltante, o colaborador precisava sair da aba, ir em Consulta Rápida, redigitar o EAN, abrir o painel de detalhes, clicar "Buscar imagem". E a lista não agrupava: um produto consultado sem sucesso em N terminais virava N linhas idênticas, sem noção de prioridade.

`GET /admin/historico-buscas?status=nao_encontrado` agora retorna um formato **diferente** dos outros dois filtros (`agrupado: true` no JSON, o frontend usa isso pra saber qual template de tabela renderizar):
- **Agrupado por `codbar`** (`GROUP BY` + `COUNT(*)` como `tentativas`, `MAX(criado_em)` como `ultima_tentativa`) — uma linha por produto, não por tentativa.
- **Ordenado por `tentativas` DESC** — o produto mais pedido e ainda sem imagem aparece primeiro (maior impacto/prioridade).
- **Filtra fora quem já tem foto agora** (`find_existing_image` checado na hora, por cima do resultado agrupado): um upload manual feito fora do fluxo de busca (aba Consulta Rápida → "Enviar/Trocar imagem") não gera uma linha `encontrado=True` no histórico — sem esse filtro extra, o produto continuaria aparecendo como "pendente" pra sempre mesmo já resolvido. Os filtros `'encontrado'`/`'todos'` continuam como log bruto de sempre (auditoria), sem agrupar nem esse filtro extra.

Três ações por linha, todas reaproveitando rotas/fluxos que já existiam (nada novo no backend além do agrupamento acima):
1. **Buscar** → `POST /admin/buscar-imagem/<codbar>` (rota já existente, local→Bing→Google→Zaffari) — na resposta 200, remove a linha da lista na hora (`removerLinhaHistorico`, decrementa o contador também) sem precisar recarregar a página; em 404, só reabilita o botão (a linha fica, `tentativas` sobe 1 na próxima consulta).
2. **Google Imagens** → só frontend, sem rota nova: abre `https://www.google.com/search?tbm=isch&q=<descrição + EAN>` numa aba nova. Existe porque a busca da Bing Image Search está morta (410 Gone, ver seção de débito técnico) e a automação (Google/Zaffari) às vezes não acha — dá pro colaborador fazer a mesma busca manual que faria de qualquer jeito, sem digitar nada.
3. **Enviar imagem** → `POST /upload-imagem-produto/<codbar>` (rota já existente) via um `<input type="file" accept="image/*">` oculto por linha, disparado pelo clique do botão — mesmo padrão de resolução instantânea da linha no sucesso.

Testado de ponta a ponta num navegador real: os 178 registros brutos do histórico colapsaram pra 3 produtos únicos pendentes, ordenados corretamente (3x/2x/2x); clique real em "Buscar" gerou o `POST` esperado (confirmado por `read_network_requests`) e tratou a resposta 404 corretamente (botão reabilita, linha permanece); os `<input>` ocultos de upload existem um por linha, com `codbar` certo em cada.

### Nome "zaffari" suprimido dos rótulos do painel (fonte continua ativa normalmente)

Pedido do usuário, só depois de duas rodadas de esclarecimento (pediu "deletar qualquer descrição Zaffari" — mas `Produto` não tem coluna de fonte, então não tem como saber quais descrições já cadastradas vieram de lá; a intenção real era só cosmética): a palavra "zaffari" não deve aparecer em nenhum rótulo/toast do painel admin, mas a Zaffari **continua sendo usada normalmente** como fonte de imagem e descrição em todos os fluxos — nada foi desativado, nenhum dado foi tocado.

`rotuloFonte(fonte)` (helper no `<script>` de `configuracoes.html`, perto de `escHtml`): retorna a fonte normalmente, exceto quando é `'zaffari'` — aí retorna `null`, e cada chamador trata isso mostrando o texto sem o nome da fonte (ex.: badge "Encontrado" sem o "· zaffari"; toast "Imagem encontrada para X" sem o "via zaffari"). Aplicado nos 3 pontos onde a origem aparece pro usuário: badge da aba Histórico (filtro 'todos'/'encontrado'), toast do botão "Buscar" (filtro 'não encontrados', ver seção acima) e toast de "Cadastrar produto" (Consulta Rápida). Se um dia aparecer um 4º lugar mostrando `origem`/`fonte` de busca de imagem, passar por `rotuloFonte` também, pelo mesmo motivo.

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

### Heartbeat horário por WhatsApp (07h-22h) — "avisar se o sistema parou"

Pedido explícito do usuário: "o sistema nunca deve parar" + um jeito de perceber quando parar. Um processo que já morreu não consegue avisar sobre si mesmo (não existe hook de "estou caindo" confiável em qualquer falha — crash da JVM/CPython, máquina desligada, processo morto pelo SO), então a estratégia é a mesma de qualquer monitoramento por heartbeat/dead man's switch: mandar uma mensagem previsível a cada hora cheia (XX:00), das 07:00 às 22:00, e deixar a **ausência** de uma mensagem esperada ser o próprio aviso — se não chegou a mensagem das 14h, algo parou entre 13h e 14h.

`_iniciar_agendador_status_horario()` (thread daemon, mesmo padrão de `_iniciar_agendador_resumo`):
- Fora da janela 07-22h, dorme direto até o início do próximo expediente (não acorda de hora em hora à toa de madrugada) — testado com simulação dos horários de borda (00:10, 06:55, 21:45, 22:05) antes de rodar de verdade.
- `try/except` **dentro** do loop, por iteração — uma falha de rede na Evolution API (ou qualquer outro erro) derruba só aquele envio específico, nunca o agendador; "nunca deve parar" vale pro próprio mecanismo de aviso também, não só pro resto do sistema.
- Na primeira execução após o processo subir, se estiver dentro do horário comercial, envia IMEDIATAMENTE com texto diferenciado ("🟢 Sistema iniciado") em vez de esperar a próxima hora cheia — sinal extra e complementar ao heartbeat regular: se o processo cair e alguém (ou um supervisor) reiniciar, essa mensagem fora do padrão avisa que houve um restart.
- Controlado por `STATUS_HORARIO_ATIVO` (Config, default `'true'` — ativo assim que o WhatsApp em si estiver configurado, sem precisar de opt-in extra; adicionado à mesma lista `NOTIFICACAO_CONFIG_KEYS`/rota `save_notificacoes` já usada pelos outros toggles de notificação).
- Mensagem (`_montar_mensagem_status_horario`) é bem mais enxuta que o resumo diário — só fila de arte (pendentes/em processamento), uso do Gemini e status do token Cosmos atual, o suficiente pra confirmar num relance que o sistema está de pé.

Testado enviando de verdade pro número configurado (`_enviar_status_horario_whatsapp` chamada manualmente) — mensagem chegou via Evolution API sem erro. Reaproveita a mesma instância (`bot_imgs`) e número (`WHATSAPP_NUMERO_DESTINO`) já usados pelo resumo diário — não é um canal novo, é o mesmo WhatsApp com uma cadência diferente.

**Limite conhecido, deliberadamente fora de escopo por ora**: isso detecta parada por AUSÊNCIA de mensagem (o usuário percebe, mas só na próxima hora cheia esperada) — não é um alerta ativo e imediato de queda. Um alerta imediato de verdade exigiria um processo INDEPENDENTE (fora deste Flask) fazendo ping periódico e alertando via Evolution API diretamente se o servidor não responder — não implementado ainda porque não foi pedido explicitamente; se o heartbeat horário se mostrar insuficiente na prática, esse é o próximo passo natural.

## Gestão de imagem por produto (painel)

O painel de detalhes (Consulta Rápida) ganhou "Enviar/Trocar imagem" (upload via `POST /upload-imagem-produto/<codbar>`, sobrescreve) e "Excluir imagem" (via `DELETE /deletar-imagem-produto/<codbar>`, com confirmação). Ambas as rotas já existiam — só não estavam expostas na UI. **Excluir a foto crua não apaga a arte publicitária já gerada** (são arquivos independentes, ver seção de arte acima) — intencional, mencionado no próprio diálogo de confirmação.
