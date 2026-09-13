# produtos-imgs (Mupa Brain)

API Flask para gerenciamento de produtos e imagens usada pelo app de consulta de preço da Mupa (`mplayer`). Inclui cadastro/consulta de produtos, upload e remoção de fundo de imagens, e geração automática de "arte publicitária" (foto do produto integrada a uma cena gerada por IA + nome/preço sobrepostos) para exibição em tela cheia nos terminais de loja.

Para decisões de arquitetura, regras do prompt de geração de arte e por que cada uma existe, ver [CLAUDE.md](CLAUDE.md).

## Funcionalidades

- Autenticação de usuário com JWT
- Cadastro, consulta e importação de produtos (CSV e busca automática por EAN em fontes externas)
- Upload de imagens de produtos e remoção de fundo
- Geração automática de arte publicitária com IA (Google Gemini) quando o produto ainda não tem uma
- Painel administrativo (`/configuracoes`) com consulta rápida, catálogo e geração de arte

## Requisitos

- Python 3.10+
- Flask, Flask-JWT-Extended, Flask-SQLAlchemy, Flask-Migrate
- google-genai (Gemini/Vertex AI)
- rembg, Pillow, requests, pytz

## Instalação

1. Clone o repositório:
    ```bash
    git clone https://github.com/antunes1011183/produtos-imgs.git
    cd produtos-imgs
    ```

2. Crie um ambiente virtual e ative-o:
    ```bash
    python -m venv venv
    source venv/bin/activate  # Linux/MacOS
    venv\Scripts\activate  # Windows
    ```

3. Instale as dependências:
    ```bash
    pip install -r requirements.txt
    ```

4. Configure as variáveis de ambiente no arquivo `.env` (ver `app.py` para a lista completa de chaves usadas, incluindo credenciais do Gemini/Vertex AI).

5. Inicialize o banco de dados:
    ```bash
    flask db init
    flask db migrate -m "Initial migration."
    flask db upgrade
    ```

## Uso

Execute a aplicação (ver [CLAUDE.md](CLAUDE.md) para observações sobre o reloader no Windows):
```bash
python app.py
```

A API sobe em `http://localhost:5050`. O painel administrativo fica em `http://localhost:5050/configuracoes`.

## Endpoints principais

### Autenticação

- `POST /login`: realiza login e retorna o token JWT.

### Produtos

- `GET /produtos`: lista produtos.
- `GET /produto/<codbar>`: detalhes de um produto.
- `PUT /produto/preco/<codbar>`: atualiza o preço médio de um produto.
- `GET /produto-sugestoes`: sugestões de produtos relacionados.
- `POST /importar-produtos`: importa produtos de um arquivo CSV.

### Imagens e arte publicitária

- `GET /produto-imagem/<codbar>`: retorna a imagem/arte de um produto; se a arte ainda não existir, dispara a geração automaticamente em segundo plano.
- `POST /produto-imagem/<codbar>/gerar-arte`: gera (ou regenera) a arte publicitária de um produto a partir da foto enviada.
- `POST /upload-imagem-produto/<codbar>`: faz upload da foto de um produto.
- `POST /upload-multiplas-imagens`: upload de múltiplas imagens de produtos.
- `DELETE /deletar-imagem-produto/<codbar>`: remove a imagem de um produto.
- `POST /remove_background_url` / `POST /remove_background_upload`: remoção de fundo de imagem, por URL ou upload.

### Administração (`/admin/...`)

- `GET /admin/estatisticas`, `GET /admin/produtos-com-foto`, `GET /admin/consulta-produtos`, `GET /admin/consulta-simples`: usados pelo painel `/configuracoes`.
- `POST /admin/buscar-imagem/<codbar>`, `POST /admin/gerar-arte/<codbar>`: ações administrativas de imagem/arte.

## Contribuição

1. Fork o projeto
2. Crie sua feature branch (`git checkout -b feature/fooBar`)
3. Commit suas mudanças (`git commit -am 'Add some fooBar'`)
4. Faça o push para a branch (`git push origin feature/fooBar`)
5. Crie um novo Pull Request

## Licença

Este projeto está licenciado sob a Licença MIT - veja o arquivo [LICENSE](LICENSE) para mais detalhes.

## Contato

Adriano Antunes - antunes@mupa.app

Projeto: [https://github.com/antunes1011183/produtos-imgs](https://github.com/antunes1011183/produtos-imgs)
