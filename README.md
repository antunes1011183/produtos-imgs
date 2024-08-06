<<<<<<< HEAD
# api-produto
=======
# API de Produtos com Flask

Este é um projeto de API para gerenciamento de produtos e imagens de produtos, incluindo funcionalidades de autenticação, upload de imagens, remoção de fundo de imagens, e geração de sugestões de produtos usando a API OpenAI GPT-4.

## Funcionalidades

- Autenticação de usuário com JWT
- Upload de imagens de produtos
- Remoção de fundo de imagens
- Geração de sugestões de produtos
- Integração com APIs do Google e Bing para busca de imagens de produtos
- API documentada com Swagger

## Requisitos

- Python 3.10+
- Flask
- Flask-JWT-Extended
- Flask-SQLAlchemy
- Flask-Migrate
- Flask-Swagger
- OpenAI API
- Azure Cognitive Services TTS
- rembg
- requests
- Pillow
- pytz

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

4. Configure as variáveis de ambiente no arquivo `.env`:
    ```bash
    OPENAI_API_KEY=your_openai_api_key
    JWT_SECRET_KEY=your_jwt_secret_key
    AZURE_SUBSCRIPTION_KEY=your_azure_subscription_key
    AZURE_REGION=your_azure_region
    TIMEZONE=your_timezone
    ```

5. Inicialize o banco de dados:
    ```bash
    flask db init
    flask db migrate -m "Initial migration."
    flask db upgrade
    ```

## Uso

1. Execute a aplicação:
    ```bash
    flask run
    ```

2. Acesse a documentação Swagger em:
    ```
    http://localhost:5000/apidocs
    ```

## Endpoints Principais

### Autenticação

- `POST /login`: Realiza login e retorna o token JWT.

### Upload de Imagens

- `POST /upload-imagem-produto/<codbar>`: Faz o upload da imagem de um produto.
- `POST /upload-multiplas-imagens`: Faz o upload de múltiplas imagens de produtos.

### Remoção de Fundo

- `POST /remove_background_url`: Remove o fundo de uma imagem a partir de uma URL.
- `POST /remove_background_upload`: Remove o fundo de uma imagem enviada pelo usuário.

### Sugestões de Produtos

- `GET /produto-sugestoes`: Obtém sugestões de produtos relacionados.

### Outras Funcionalidades

- `POST /importar-produtos`: Importa produtos de um arquivo CSV.
- `DELETE /deletar-imagem-produto/<string:codbar>`: Deleta a imagem de um produto.
- `GET /produto-imagem/<codbar>`: Obtém a imagem de um produto.
- `PUT /produto/preco/<string:codbar>`: Atualiza o preço médio de um produto.

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

Projeto Link: [https://github.com/antunes1011183/produtos-imgs](https://github.com/antunes1011183/produtos-imgs)
>>>>>>> ae52e37a50c623492cea475c86d2ba729f257ca7
