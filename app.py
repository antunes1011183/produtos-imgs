import os
import io
import sys
import csv
import sqlite3
import base64
import threading
import concurrent.futures
import time
import queue
import subprocess
import math
import colorsys
import requests
from io import StringIO, BytesIO
from io import StringIO
from datetime import datetime, timedelta
from flask import Flask, request, jsonify, url_for, render_template, redirect, send_file, Response
from flask_sqlalchemy import SQLAlchemy
from flask_jwt_extended import JWTManager, create_access_token, jwt_required, verify_jwt_in_request
from werkzeug.utils import secure_filename
from flasgger import Swagger, swag_from
import openai
from google import genai
from google.genai import types as genai_types
import pytz
import logging
try:
    from rembg import remove
    REMBG_ENABLED = True
except Exception:
    remove = None
    REMBG_ENABLED = False
    logging.warning("rembg não disponível - remoção de fundo desativada")
from PIL import Image, ImageDraw, ImageFont, ImageChops, ImageFilter
from flask_migrate import Migrate  # Adicionado
import re
import json
from io import BytesIO

# Quando compilado com PyInstaller (`sys.frozen`), __file__ e o cwd herdado do processo que
# lançou o .exe (ex.: o NSSM, ou um duplo-clique) não são confiáveis como base pra caminhos
# relativos (IMAGES_FOLDER, FONT_PATH etc. abaixo) nem pro banco SQLite — em modo onefile,
# __file__ aponta pra dentro da pasta temporária de extração (apagada a cada execução!), então
# usar isso pro banco faria o app "esquecer" tudo a cada reinício. BASE_DIR sempre aponta pra
# pasta real e persistente onde o .exe está, e o chdir garante que todo caminho relativo do
# resto do arquivo (nunca reescrito individualmente) resolva contra essa pasta.
if getattr(sys, 'frozen', False):
    BASE_DIR = os.path.dirname(sys.executable)
    os.chdir(BASE_DIR)
else:
    BASE_DIR = os.path.dirname(os.path.abspath(__file__))

# Constants
ALLOWED_EXTENSIONS = {'png', 'jpg', 'jpeg', 'webp'}
IMAGES_FOLDER = 'static/imgs_produtos'
PROCESSED_IMAGES_FOLDER = 'static/processed_images'
# Pasta de quarentena (imagens rejeitadas por _imagem_e_segura) — DELIBERADAMENTE fora de
# static/, que o Flask serve publicamente sem autenticação nenhuma. Uma imagem imprópria não
# pode acabar acessível via URL direta só porque foi "só" pra quarentena — o único jeito de ver
# o conteúdo é pela rota autenticada /admin/quarentena-imagem/<id> (ver seção de rotas).
QUARENTENA_FOLDER = 'quarentena_imagens'
AUDIO_FOLDER = 'static/audios'
ARTES_FOLDER = 'static/artes_geradas'
FONT_PATH = 'static/fonts/Montserrat-Variable.ttf'
GOOGLE_API_KEY = 'AIzaSyDcgpSF9cRmzLwGqIk44x-3_GZjTfUChtM'
GOOGLE_CX = '053e66708840f4936'
ZAFFARI_SEARCH_URL = 'https://zaffari.vtexcommercestable.com.br/api/catalog_system/pub/products/search'
RISSUL_SEARCH_URL = 'https://superrissul.vtexcommercestable.com.br/api/catalog_system/pub/products/search'

OPENAI_API_KEY = os.getenv('OPENAI_API_KEY', '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')
JWT_SECRET_KEY = os.getenv('JWT_SECRET_KEY', 'your-jwt-secret-key')
AZURE_SUBSCRIPTION_KEY = os.getenv('AZURE_SUBSCRIPTION_KEY', '9beaf866156a478a9bfac946c05cddde')
AZURE_REGION = os.getenv('AZURE_REGION', 'brazilsouth')
TIMEZONE = os.getenv('TIMEZONE', 'UTC')

# Flask app setup
# `instance_path`/`static_folder`/`template_folder` explícitos (em vez de deixar o Flask
# calcular a partir de __file__/root_path) pelo mesmo motivo do BASE_DIR acima: sob PyInstaller,
# root_path aponta pra dentro do bundle (_internal), não pra pasta persistente do .exe. Sem essa
# correção: (1) o Flask-SQLAlchemy resolveria 'sqlite:///produtos.db' relativo pra dentro do
# bundle; (2) a rota embutida `/static/<path>` (usada por toda URL de imagem/arte gerada por
# `url_for('static', ...)`) procuraria os arquivos dentro do bundle em vez da pasta `static/`
# real ao lado do .exe — mesmo com os arquivos sendo gravados no lugar certo (isso já funciona
# graças ao os.chdir(BASE_DIR) acima), a URL serviria 404 porque o Flask olharia no lugar errado.
app = Flask(
    __name__,
    instance_path=os.path.join(BASE_DIR, 'instance'),
    instance_relative_config=True,
    static_folder=os.path.join(BASE_DIR, 'static'),
    template_folder=os.path.join(BASE_DIR, 'templates'),
)
app.config['SWAGGER'] = {
    'title': 'API de Produtos',
    'uiversion': 3,
    'version': '1.0.0',
    'description': 'API para gerenciar produtos e imagens de produtos'
}
app.config['SQLALCHEMY_DATABASE_URI'] = 'sqlite:///produtos.db'
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False  # Adicionado para evitar warnings
app.config['JWT_SECRET_KEY'] = JWT_SECRET_KEY
app.config['JWT_ACCESS_TOKEN_EXPIRES'] = timedelta(hours=1)
app.config['PROCESSED_IMAGES_FOLDER'] = PROCESSED_IMAGES_FOLDER
swagger = Swagger(app)
db = SQLAlchemy(app)

migrate = Migrate(app, db)

# Caminho do banco para consultas diretas (Opções B e C)
DB_PATH = os.path.join(BASE_DIR, 'instance', 'produtos.db')  # Inicializado o Flask-Migrate

jwt = JWTManager(app)

# OpenAI API setup
openai.api_key = OPENAI_API_KEY

# Logger setup
logging.basicConfig(level=logging.INFO)

# Create necessary directories
os.makedirs(IMAGES_FOLDER, exist_ok=True)
os.makedirs(PROCESSED_IMAGES_FOLDER, exist_ok=True)
os.makedirs(AUDIO_FOLDER, exist_ok=True)
os.makedirs(ARTES_FOLDER, exist_ok=True)
os.makedirs(QUARENTENA_FOLDER, exist_ok=True)

# Helper functions
def allowed_file(filename):
    """Verifica se o arquivo tem uma extensão permitida"""
    return '.' in filename and filename.rsplit('.', 1)[1].lower() in ALLOWED_EXTENSIONS

def save_image_with_background_removal(image, file_path):
    """Remove o fundo da imagem e salva no caminho especificado.

    `alpha_matting=True` (pedido do usuário, depois de notar produto "cortado" na imagem
    processada): sem isso, o rembg usa só a máscara binária crua do modelo de segmentação
    (u2net, o padrão) — em bordas finas ou claras da embalagem (etiqueta translúcida, tampa
    branca contra fundo também claro, reflexo/brilho na superfície) o modelo às vezes classifica
    um pedaço do PRODUTO em si como fundo, "comendo" aquele pedaço na imagem final. Alpha
    matting (via `pymatting`, já instalado) refina a borda numa segunda passada — em vez de um
    corte binário direto, suaviza a transição real fundo/produto — reduz bastante esse efeito.
    Custa um pouco mais de tempo por imagem, aceitável (roda em background, não bloqueia
    resposta pro terminal).

    `optimize=True` no PNG final: compressão SEM PERDA mais agressiva (PNG já é lossless por
    natureza — isso só demora um pouco mais pra salvar, não muda um pixel sequer) — reduz o
    tamanho do arquivo final, pedido do usuário junto com a correção acima ("otimize e não
    perca qualidade").

    SEMPRE salva algo em `file_path`, mesmo com `REMBG_ENABLED=false` (nesse caso, sem remover
    fundo) — desde que a foto crua deixou de ser guardada em paralelo (ver save_image_from_response),
    esse é o único lugar onde a imagem é persistida; se essa função "desistisse" silenciosamente
    (comportamento antigo), a imagem simplesmente não seria salva em lugar nenhum."""
    # Converte para RGBA se necessário
    image = image.convert("RGBA") if image.mode != "RGBA" else image
    if REMBG_ENABLED:
        # Remove o fundo, com matting pra não cortar pedaço do produto nas bordas
        output_image = remove(image, alpha_matting=True)
    else:
        logging.warning(f"rembg desativado - salvando {file_path} sem remover o fundo")
        output_image = image
    # Salva a imagem processada (lossless, só mais compacta)
    output_image.save(file_path, optimize=True)

def fetch_product_from_google(ean):
    # safe=active: filtra conteúdo explícito no resultado — CRÍTICO aqui porque a busca é feita
    # só pelo EAN em dígitos (não pelo nome do produto, que ainda não temos nesse ponto), uma
    # query genérica demais pro Google Custom Search associar com confiança a um produto de
    # verdade; sem SafeSearch, um EAN pode acabar casando com qualquer página da internet que
    # contenha aquela sequência de números, inclusive conteúdo impróprio (incidente real: EAN
    # 7898909864181 retornou uma imagem pornográfica antes dessa correção).
    search_url = f"https://www.googleapis.com/customsearch/v1?q={ean}&cx={GOOGLE_CX}&searchType=image&num=2&safe=active&key={GOOGLE_API_KEY}"
    try:
        response = requests.get(search_url)
        response.raise_for_status()
        results = response.json()
        if 'items' in results:
            for item in results['items']:
                if 'product' in item:
                    return {
                        'gtin': ean,
                        'description': item['title'],
                        'ncm': 'NCM não disponível',
                        'marca': 'Marca não disponível',
                        'thumbnail': item['link'],
                        'cest_codigo': 'CEST não disponível',
                        'embalagem': 'Embalagem não disponível',
                        'preco_medio': 0.0,
                        'categoriaText': 'Categoria não disponível'
                    }
        return None
    except requests.RequestException as e:
        logging.error(f"Erro ao buscar produto no Google: {e}")
        return None

# Database models
class Produto(db.Model):
    """Modelo de Produto"""
    codbar = db.Column(db.String(255), primary_key=True)
    description = db.Column(db.String(255))
    ncm = db.Column(db.String(255))
    cest_codigo = db.Column(db.String(255))
    embalagem = db.Column(db.String(255))
    foto_png = db.Column(db.String(255))
    marca = db.Column(db.String(255))
    preco_medio = db.Column(db.Float)
    categoriaText = db.Column(db.String(255))
    # Indicador reliável de "tem foto crua salva localmente" (static/imgs_produtos/<codbar>.*) —
    # diferente de foto_png (campo legado, inconsistente: às vezes guarda uma URL externa crua
    # do cadastro automático, às vezes um path local, às vezes nunca foi atualizado depois de um
    # upload/busca de imagem). Mantido em sincronia por _marcar_tem_foto, chamado em todo ponto
    # que salva/remove a foto crua de um produto (ver função pra lista completa). Indexado
    # porque é usado pra ordenar "produtos com foto primeiro" na Consulta Rápida (pedido do
    # usuário) — sem índice, ordenar ~945 mil linhas por essa coluna seria lento.
    tem_foto = db.Column(db.Boolean, nullable=False, default=False, index=True)
    # Bloqueio permanente de busca automática de imagem pra ESSE EAN específico — pedido do
    # usuário depois de um incidente real (imagem pornográfica servida pro EAN 7898909864181,
    # vinda do PreçoMelhor — não do Google como se suspeitou inicialmente; ver CLAUDE.md).
    # Diferente do kill switch global (BUSCA_IMAGEM_ONLINE_ATIVA, Config), que desliga a busca
    # pra TODO produto: esse aqui é por produto, permanente, e sobrevive mesmo com o kill switch
    # global religado — sem isso, o próprio auto-heal do sistema iria buscar e salvar a MESMA
    # imagem ruim de novo na próxima consulta desse EAN, já que a fonte de dados (PreçoMelhor)
    # continua tendo o mesmo mapeamento ruim EAN->imagem independente de qualquer correção
    # nossa. Setado automaticamente pela ferramenta de exclusão de emergência em Configurações.
    busca_imagem_bloqueada = db.Column(db.Boolean, nullable=False, default=False)

class SugestaoProduto(db.Model):
    """Modelo de Sugestão de Produto"""
    id = db.Column(db.Integer, primary_key=True)
    codbar = db.Column(db.String(255))
    tipo_sugestao = db.Column(db.String(255))  # Novo campo para tipo de sugestão
    sugestao = db.Column(db.String(255))
    audio_url = db.Column(db.String(255))


class HistoricoBuscaImagem(db.Model):
    """Fila de pendências de imagens faltando — tanto de buscas do terminal
    (GET /produto-imagem/<codbar>, via='terminal') quanto de uma retentativa manual no painel
    (POST /admin/buscar-imagem/<codbar>, via='admin'). O resumo diário de e-mail/WhatsApp
    consulta as linhas com encontrado=False e notificado_em=NULL.

    Pedido do usuário: só registra quando (a) o EAN é um produto que realmente existe no nosso
    catálogo (`Produto`) e (b) a busca falhou — ver `_registrar_busca_imagem`. Antes disso, TODA
    consulta de imagem virava uma linha aqui (inclusive sucesso, e inclusive EANs que nem são
    produtos nossos, ex.: código escaneado errado ou produto de outra loja) — isso inflava a
    fila de "não encontrados" com entradas que ninguém tinha como resolver de verdade (não são
    produtos do catálogo) e obrigava a filtrar sucesso/falha manualmente na aba Histórico. A
    coluna `encontrado` continua existindo (linhas antigas, de antes dessa mudança, têm valores
    `True` reais) mas linhas novas são sempre `False` — o histórico virou só a fila de pendência,
    não mais um log geral de todo scan de terminal."""
    id = db.Column(db.Integer, primary_key=True)
    codbar = db.Column(db.String(64), nullable=False, index=True)
    encontrado = db.Column(db.Boolean, nullable=False, default=False)
    origem = db.Column(db.String(30), nullable=True)  # local, bing, google, zaffari
    via = db.Column(db.String(20), nullable=False, default='terminal')  # terminal, admin
    criado_em = db.Column(db.DateTime, nullable=False, default=datetime.utcnow, index=True)
    notificado_em = db.Column(db.DateTime, nullable=True)


class ImagemQuarentena(db.Model):
    """Fila de auditoria de imagens rejeitadas pelo filtro de conteúdo (`_imagem_e_segura`).

    Pedido do usuário depois do incidente do EAN 7898909864181: em vez de só descartar
    silenciosamente uma imagem classificada como imprópria (comportamento anterior), o sistema
    agora GUARDA o arquivo numa pasta separada (`QUARENTENA_FOLDER`, fora de `static/`, nunca
    servida sem autenticação) e cria uma linha aqui, pra um humano poder revisar depois — tanto
    pra confirmar casos reais (evidência, útil pra reportar a fonte externa) quanto pra pegar
    falsos positivos do classificador (a imagem pode ser liberada e volta a ser a foto oficial
    do produto).

    `decisao` começa `None` (pendente de revisão) e vira `'confirmada'` (era mesmo imprópria —
    arquivo apagado, só o registro fica) ou `'liberada'` (falso positivo — arquivo movido de
    volta pra `IMAGES_FOLDER`/`PROCESSED_IMAGES_FOLDER`, produto desbloqueado). `arquivo` vira
    `None` nos dois casos, já que o arquivo físico não fica mais na pasta de quarentena depois
    de uma decisão — só enquanto `decisao IS NULL` (pendente) o arquivo ainda existe lá."""
    id = db.Column(db.Integer, primary_key=True)
    codbar = db.Column(db.String(64), nullable=False, index=True)
    arquivo = db.Column(db.String(255), nullable=True)  # nome do arquivo em QUARENTENA_FOLDER; None depois de revisado
    origem = db.Column(db.String(30), nullable=True)  # de onde veio a imagem (bing/google/zaffari/precomelhor/rissul/ia/cosmos/...)
    criado_em = db.Column(db.DateTime, nullable=False, default=datetime.utcnow, index=True)
    decisao = db.Column(db.String(20), nullable=True, index=True)  # None=pendente, 'confirmada', 'liberada'
    revisado_em = db.Column(db.DateTime, nullable=True)


def _registrar_busca_imagem(codbar, encontrado, origem=None, via='terminal'):
    """Grava uma linha na fila de pendências (ver docstring de HistoricoBuscaImagem) — só quando
    as DUAS condições abaixo são verdadeiras:
    1. NÃO temos a imagem do produto na pasta: checado direto via `find_existing_image` em
       `PROCESSED_IMAGES_FOLDER` (não só confiando no parâmetro `encontrado` que o chamador
       passou) — garante que a condição real é sempre "o arquivo não existe fisicamente agora",
       não uma inferência sobre como a busca correu. `PROCESSED_IMAGES_FOLDER`, não
       `IMAGES_FOLDER`, porque a foto crua deixou de ser guardada em disco (ver
       save_image_from_response) — a processada é a única cópia que existe.
    2. `encontrado=False`: sucesso não vira pendência nenhuma (nada a resolver) — verificado
       primeiro, como atalho barato antes de tocar o banco/disco; os chamadores continuam
       passando `encontrado=True` nos casos de sucesso (não precisou mudar nenhum call site),
       vira um no-op silencioso aqui.

    Não exige mais que o EAN tenha `Produto` cadastrado (exigência removida a pedido do usuário,
    pra alimentar a página "Imagens Pendentes" — ver CLAUDE.md — que precisa listar QUALQUER EAN
    sem imagem, cadastrado ou não). A aba "Histórico → Não encontrados" continua mostrando só
    produtos cadastrados, mas o filtro agora é aplicado na hora da CONSULTA (`admin_historico_buscas`),
    não mais na hora de gravar — assim a mesma linha serve as duas telas.

    Nunca deixa uma falha de log quebrar o fluxo principal de consulta de imagem."""
    if encontrado:
        return
    try:
        if find_existing_image(codbar, PROCESSED_IMAGES_FOLDER, ALLOWED_EXTENSIONS):
            return
        db.session.add(HistoricoBuscaImagem(codbar=codbar, encontrado=encontrado, origem=origem, via=via))
        db.session.commit()
    except Exception as e:
        db.session.rollback()
        logging.error(f"Erro ao registrar histórico de busca de imagem para {codbar}: {e}")

@app.route('/')
def index():
    """A raiz não serve mais a ferramenta avulsa de remover fundo (era templates/index.html,
    sem login nenhum) — pedido do usuário pra sempre cair no login do painel. A ferramenta em
    si continua existindo, migrada pra dentro do painel autenticado (aba "Remover Fundo",
    ver /remove_background_upload e /remove_background_url)."""
    return redirect('/configuracoes')

@app.route('/remove_background_url', methods=['POST'])
@jwt_required()
def remove_background_url():
    if not REMBG_ENABLED:
        return jsonify({'message': 'rembg não disponível neste ambiente'}), 501
    data = request.json
    image_url = data.get('image_url')
    if not image_url:
        return jsonify({'message': 'No image URL provided'}), 400

    try:
        response = requests.get(image_url)
        response.raise_for_status()
        input_image = Image.open(BytesIO(response.content)).convert("RGBA")

        output_image = remove(input_image)

        filename = os.path.basename(image_url)
        safe_filename = secure_filename(filename).rsplit('.', 1)[0] + '.png'
        processed_image_path = os.path.join(app.config['PROCESSED_IMAGES_FOLDER'], safe_filename)

        output_image.save(processed_image_path, format="PNG")

        download_url = url_for('static', filename=f'processed_images/{safe_filename}', _external=True)

        return jsonify({'url': download_url}), 200

    except requests.RequestException as e:
        return jsonify({'message': f'Error downloading image: {str(e)}'}), 500
    except Exception as e:
        return jsonify({'message': f'Error processing image: {str(e)}'}), 500

@app.route('/remove_background_upload', methods=['POST'])
@jwt_required()
def remove_background_upload():
    if not REMBG_ENABLED:
        return jsonify({'message': 'rembg não disponível neste ambiente'}), 501
    if 'file' not in request.files:
        return jsonify({'message': 'No file uploaded'}), 400
    file = request.files['file']
    if file.filename == '':
        return jsonify({'message': 'No file selected'}), 400
    try:
        input_image = Image.open(file.stream).convert("RGBA")
        output_image = remove(input_image)

        safe_filename = secure_filename(file.filename).rsplit('.', 1)[0] + '.png'
        processed_image_path = os.path.join(app.config['PROCESSED_IMAGES_FOLDER'], safe_filename)

        output_image.save(processed_image_path, format="PNG")

        download_url = url_for('static', filename=f'processed_images/{safe_filename}', _external=True)

        return jsonify({'url': download_url}), 200
    except Exception as e:
        return jsonify({'message': f'Error processing image: {str(e)}'}), 500

# REMOVIDO: GET /painel/login emitia um JWT válido pra 'antunes@mupa.app' sem checar senha
# nenhuma — era a causa raiz de o painel "já entrar logado" sozinho (configuracoes.html chamava
# essa rota automaticamente no boot, antes de qualquer tela de login existir). Pedido do
# usuário pra corrigir isso de vez: agora só existe UM jeito de conseguir um token pro painel,
# POST /login com credenciais reais (ver CREDENCIAIS_PAINEL logo abaixo).

# Credenciais de acesso ao painel — usuário pediu explicitamente a adição de support@mupa.app.
CREDENCIAIS_PAINEL = {
    'antunes@mupa.app': '#Mupa04051623$',
    'support@mupa.app': '#CpvwPgu3233105$',
}


@app.route('/login', methods=['POST'])
@swag_from({
    'tags': ['Auth'],
    'parameters': [
        {
            'name': 'username',
            'in': 'formData',
            'type': 'string',
            'required': True
        },
        {
            'name': 'password',
            'in': 'formData',
            'type': 'string',
            'required': True
        }
    ],
    'responses': {
        '200': {
            'description': 'Login bem-sucedido',
            'examples': {
                'application/json': {
                    'access_token': 'string',
                    'expires_at': 'timestamp'
                }
            }
        },
        '401': {
            'description': 'Credenciais inválidas'
        }
    }
})
def login():
    """Realiza login"""
    username = request.form.get('username')
    password = request.form.get('password')
    if username in CREDENCIAIS_PAINEL and password == CREDENCIAIS_PAINEL[username]:
        expires = timedelta(hours=1)
        access_token = create_access_token(identity=username, expires_delta=expires)
        expires_time = datetime.now(pytz.timezone(TIMEZONE)) + expires
        expires_timestamp = int(expires_time.timestamp() * 1000)
        return jsonify(access_token=access_token, expires_at=expires_timestamp), 200
    else:
        return jsonify({'error': 'Invalid credentials'}), 401

@app.route('/upload-imagem-produto/<codbar>', methods=['POST'])
@jwt_required()
@swag_from({
    'tags': ['Produtos'],
    'parameters': [
        {
            'name': 'codbar',
            'in': 'path',
            'type': 'string',
            'required': True,
            'description': 'Código de barras do produto'
        },
        {
            'name': 'file',
            'in': 'formData',
            'type': 'file',
            'required': True
        }
    ],
    'responses': {
        '200': {
            'description': 'Imagem enviada com sucesso'
        },
        '400': {
            'description': 'Erro no upload da imagem'
        }
    }
})
def upload_imagem_produto(codbar):
    """Faz o upload da imagem de um produto"""
    if 'file' not in request.files:
        return jsonify({'message': 'No file part in the request'}), 400
    file = request.files['file']
    if file.filename == '':
        return jsonify({'message': 'No file selected'}), 400
    if file and allowed_file(file.filename):
        dados = file.read()
        if not _imagem_e_segura(dados):
            _quarentenar_imagem(dados, codbar, origem='upload_manual')
            return jsonify({'message': 'Imagem sinalizada pelo filtro de conteúdo e enviada para revisão humana (Configurações → Quarentena) em vez de ser salva'}), 422
        # Sem cópia crua (pedido do usuário) — processa (remove fundo) e salva só em
        # PROCESSED_IMAGES_FOLDER, mesmo padrão de save_image_from_response.
        processed_file_path = os.path.join(PROCESSED_IMAGES_FOLDER, f'{codbar}.png')
        input_image = Image.open(io.BytesIO(dados))
        save_image_with_background_removal(input_image, processed_file_path)
        try:
            _marcar_tem_foto(codbar, True)
            db.session.commit()
        except Exception as e:
            db.session.rollback()
            logging.warning(f"Não foi possível marcar tem_foto=True para {codbar}: {e}")
        return jsonify({'message': 'Image successfully uploaded', 'path': processed_file_path}), 200
    else:
        return jsonify({'message': 'File format not allowed'}), 400

@app.route('/upload-multiplas-imagens', methods=['POST'])
@jwt_required()
@swag_from({
    'tags': ['Produtos'],
    'parameters': [
        {
            'name': 'files',
            'in': 'formData',
            'type': 'array',
            'items': {'type': 'file'},
            'required': True,
            'description': 'Arquivos de imagem para upload'
        }
    ],
    'responses': {
        '200': {
            'description': 'Imagens enviadas com sucesso'
        },
        '400': {
            'description': 'Erro no upload das imagens'
        }
    }
})
def upload_multiplas_imagens():
    """Faz o upload de múltiplas imagens de produtos"""
    if 'files' not in request.files:
        return jsonify({'message': 'No files part in the request'}), 400

    files = request.files.getlist('files')
    if not files:
        return jsonify({'message': 'No files selected'}), 400

    saved_files = []
    for file in files:
        if file and allowed_file(file.filename):
            filename = secure_filename(file.filename)
            file_path = os.path.join(IMAGES_FOLDER, filename)
            file.save(file_path)

            # Salvar o caminho da imagem no banco de dados
            nova_imagem = ImagemProduto(caminho=file_path)
            db.session.add(nova_imagem)
            db.session.commit()

            saved_files.append(file_path)
        else:
            return jsonify({'message': 'File format not allowed'}), 400

    return jsonify({'message': 'Images successfully uploaded', 'paths': saved_files}), 200

# Modelo para Imagens de Produtos
class ImagemProduto(db.Model):
    """Modelo de Imagem de Produto"""
    id = db.Column(db.Integer, primary_key=True)
    caminho = db.Column(db.String(255), nullable=False)

@app.route('/importar-produtos', methods=['POST'])
@jwt_required()
@swag_from({
    'tags': ['Produtos'],
    'parameters': [
        {
            'name': 'file',
            'in': 'formData',
            'type': 'file',
            'required': True
        }
    ],
    'responses': {
        '201': {
            'description': 'Produtos importados com sucesso'
        },
        '400': {
            'description': 'Erro ao importar produtos'
        }
    }
})
def importar_produtos():
    """Importa produtos de um arquivo CSV"""
    if 'file' not in request.files:
        return jsonify({'error': 'Nenhum arquivo CSV enviado'}), 400

    file = request.files['file']
    if file.filename == '' or not file.filename.endswith('.csv'):
        return jsonify({'error': 'O arquivo enviado não é um CSV válido'}), 400

    try:
        # Tenta decodificar como UTF-8, mas cai para ISO-8859-1 se falhar
        try:
            csv_data = file.read().decode('utf-8-sig')
        except UnicodeDecodeError:
            csv_data = file.read().decode('ISO-8859-1')

        csv_file = StringIO(csv_data)
        
        # Define explicitamente o delimitador como ponto e vírgula (;)
        csv_reader = csv.DictReader(csv_file, delimiter=';')

        # Valida se as colunas esperadas estão presentes
        required_columns = {'codbar', 'description', 'ncm', 'cest_codigo', 'embalagem', 'foto_png', 'marca', 'preco_medio', 'categoriaText'}
        if not required_columns.issubset(csv_reader.fieldnames):
            return jsonify({'error': 'Colunas obrigatórias ausentes no arquivo CSV'}), 400

        for row in csv_reader:
            novo_produto = Produto(
            codbar=row['codbar'],
            description=row['description'],
            ncm=row['ncm'],
            cest_codigo=row['cest_codigo'],
            embalagem=row['embalagem'],
            foto_png=row['foto_png'],
            marca=row['marca'],
            preco_medio=float(row['preco_medio'].replace(',', '.')) if row['preco_medio'] else 0.0,
            categoriaText=row['categoriaText']
            )
            db.session.add(novo_produto)
        db.session.commit()

        return jsonify({'message': 'Produtos importados com sucesso'}), 201

    except KeyError as e:
        logging.error(f"Chave ausente no CSV: {e}")
        return jsonify({'error': f"Coluna ausente no CSV: {e}"}), 400
    except Exception as e:
        logging.error(f"Erro ao importar produtos: {e}")
        return jsonify({'error': 'Erro interno ao importar produtos'}), 500

@app.route('/deletar-imagem-produto/<string:codbar>', methods=['DELETE'])
@jwt_required()
@swag_from({
    'tags': ['Produtos'],
    'parameters': [
        {
            'name': 'codbar',
            'in': 'path',
            'type': 'string',
            'required': True
        }
    ],
    'responses': {
        '200': {
            'description': 'Imagem deletada com sucesso'
        },
        '404': {
            'description': 'Imagem não encontrada'
        },
        '422': {
            'description': 'Erro no processamento da solicitação'
        }
    }
})
def deletar_imagem_produto(codbar):
    """Deleta a imagem de um produto"""
    logging.info(f"Recebida requisição para deletar imagem do produto com código de barras: {codbar}")

    if not codbar:
        logging.error("Código de barras não fornecido")
        return jsonify({'message': 'Código de barras não fornecido'}), 400
    
    # Apaga dos DOIS diretórios (crua em IMAGES_FOLDER + com fundo removido em
    # PROCESSED_IMAGES_FOLDER) — antes só apagava a crua, deixando a versão processada
    # (que é a que normalmente fica servida, ver save_image_from_response) intacta no disco.
    # Bug real achado depois de um incidente com imagem imprópria: "excluir" não garantia de
    # verdade que a imagem parasse de ser servida. Confere as duas pastas mesmo se a extensão
    # divergir entre elas (a processada é sempre .png, mas não custa nada varrer as duas).
    # ?bloquear=true (usado pela ferramenta de exclusão de emergência em Configurações):
    # marca o produto pra nunca mais ter imagem buscada automaticamente (ver
    # Produto.busca_imagem_bloqueada) — independente de ter achado arquivo pra apagar ou não,
    # porque o objetivo é impedir uma busca FUTURA re-trazer a mesma imagem ruim (a fonte de
    # dados problemática continua tendo o mesmo mapeamento EAN->imagem ruim, então só apagar o
    # arquivo local não resolve — o auto-heal do sistema ia buscar nela de novo).
    bloquear = request.args.get('bloquear') == 'true'

    image_found = False
    try:
        for pasta in (IMAGES_FOLDER, PROCESSED_IMAGES_FOLDER):
            for ext in ALLOWED_EXTENSIONS:
                img_path = os.path.join(pasta, f'{codbar}.{ext}')
                if os.path.exists(img_path):
                    os.remove(img_path)
                    image_found = True
                    logging.info(f"Imagem deletada: {img_path}")

        if image_found or bloquear:
            try:
                if image_found:
                    _marcar_tem_foto(codbar, False)
                if bloquear:
                    produto = Produto.query.filter_by(codbar=codbar).first()
                    if not produto:
                        # `obter_imagem_produto` (terminal) busca imagem por qualquer EAN, sem
                        # exigir que exista um Produto cadastrado — foi exatamente esse o caso
                        # do incidente real que motivou esse bloqueio (EAN 7898909864181 nunca
                        # tinha sido cadastrado aqui). Sem criar a linha, não haveria onde
                        # gravar o bloqueio e ele nunca "pegaria" de verdade. Mesmo padrão do
                        # cadastro manual (só EAN obrigatório, resto fica vazio).
                        produto = Produto(codbar=codbar)
                        db.session.add(produto)
                    produto.busca_imagem_bloqueada = True
                db.session.commit()
            except Exception as e:
                db.session.rollback()
                logging.warning(f"Não foi possível atualizar tem_foto/busca_imagem_bloqueada para {codbar}: {e}")

        if image_found:
            msg = 'Imagem deletada com sucesso' + (' e busca automática bloqueada para este EAN' if bloquear else '')
            return jsonify({'message': msg}), 200
        elif bloquear:
            return jsonify({'message': 'Nenhuma imagem encontrada pra apagar, mas busca automática bloqueada para este EAN'}), 200
        else:
            logging.warning(f"Imagem não encontrada para o código de barras: {codbar}")
            return jsonify({'message': 'Imagem não encontrada'}), 404
    except Exception as e:
        logging.error(f"Erro ao deletar a imagem para o código de barras {codbar}: {e}")
        return jsonify({'message': f'Erro no processamento da solicitação: {e}'}), 422

@app.route('/produto-imagem/<codbar>', methods=['GET'])
@swag_from({
    'tags': ['Produtos'],
    'parameters': [
        {
            'name': 'codbar',
            'in': 'path',
            'type': 'string',
            'required': True
        }
    ],
    'responses': {
        '200': {
            'description': 'URL da imagem do produto'
        },
        '404': {
            'description': 'Imagem não encontrada'
        }
    }
})
def obter_imagem_produto(codbar):
    """Obtém a imagem crua do produto (local -> Google -> Zaffari -> PrecoMelhor -> Rissul -> Sonda) e, se já
    existir, a URL da arte publicitária gerada para ele. Quando a foto existe mas a arte
    ainda não foi gerada, dispara a geração em background (a resposta desta chamada ainda
    sai sem 'imagem_url_arte'; uma consulta seguinte já encontra a arte pronta).

    Query param `orientacao` ('horizontal', default, ou 'vertical'): o app manda esse valor de
    acordo com a orientação física do terminal (ver PriceQueryEngine.reportarOrientacao no
    mplayer) — decide tanto qual arte é retornada em 'imagem_url_arte' quanto qual pipeline é
    disparado em background quando falta gerar. Só a orientação pedida é gerada (não as duas de
    uma vez) — cada chamada à IA custa dinheiro/tempo, não faz sentido gerar uma arte vertical
    pra uma loja que só tem terminais horizontais, e vice-versa."""
    orientacao = 'vertical' if request.args.get('orientacao') == 'vertical' else 'horizontal'
    # PROCESSED_IMAGES_FOLDER é a única cópia que existe (pedido do usuário: nunca duplicar
    # crua+processada em disco) — a checagem "já temos imagem" e a URL servida vêm de lá; a
    # geração de arte também passou a usar essa mesma imagem (sem fundo) como referência pra IA,
    # já que a crua não é mais salva em lugar nenhum.
    img_path = find_existing_image(codbar, PROCESSED_IMAGES_FOLDER, ALLOWED_EXTENSIONS)
    if img_path:
        img_url = _static_url(img_path)
        logging.info(f"Imagem encontrada localmente para o produto {codbar}: {img_url}")
        arte_url = _arte_url(codbar, orientacao)
        if not arte_url:
            _enfileirar_geracao_arte(codbar, img_path, orientacao=orientacao)
        _registrar_busca_imagem(codbar, True, origem='local', via='terminal')
        return jsonify({'imagem_url': img_url, 'imagem_url_arte': arte_url}), 200

    if _pode_buscar_imagem_online(codbar):
        cfg_fontes = _ler_todas_config()
        for fonte, buscar in (
            ('google', buscar_e_salvar_imagem_google),
            ('zaffari', buscar_e_salvar_imagem_zaffari),
            ('precomelhor', buscar_e_salvar_imagem_precomelhor),
            ('rissul', buscar_e_salvar_imagem_rissul),
            ('sonda', buscar_e_salvar_imagem_sonda),
            ('unidasul', buscar_e_salvar_imagem_unidasul),
            ('serper', buscar_e_salvar_imagem_serper),
        ):
            if not _fonte_imagem_ativa(fonte, cfg_fontes):
                continue
            resultado = buscar(codbar)
            if resultado[1] == 200:
                imagem_url = resultado[0].get_json().get('imagem_url')
                novo_img_path = find_existing_image(codbar, PROCESSED_IMAGES_FOLDER, ALLOWED_EXTENSIONS)
                if novo_img_path:
                    _enfileirar_geracao_arte(codbar, novo_img_path, orientacao=orientacao)
                _registrar_busca_imagem(codbar, True, origem=fonte, via='terminal')
                return jsonify({'imagem_url': imagem_url, 'imagem_url_arte': None}), 200

    _registrar_busca_imagem(codbar, False, via='terminal')
    return jsonify({'message': 'Imagem não encontrada em nenhuma fonte (local, Google, PrecoMelhor, Sonda, Unidasul, Serper)'}), 404


_fila_arte = queue.Queue()


class _JobArte:
    """Uma tarefa de geração de arte enfileirada. `evento` só é criado quando o chamador
    precisa aguardar o resultado (admin_gerar_arte, gerar_arte_publica) — o disparo em
    background feito por obter_imagem_produto não espera, só enfileira e segue servindo a
    foto crua normalmente.

    `orientacao` ('horizontal'/'vertical') decide qual pipeline o worker chama — mesma fila
    única pras duas orientações (continua garantindo no máximo uma chamada ao Gemini por vez,
    não importa se é arte horizontal ou vertical que está sendo gerada)."""
    __slots__ = ('codbar', 'img_path', 'evento', 'erro', 'orientacao')

    def __init__(self, codbar, img_path, aguardar, orientacao='horizontal'):
        self.codbar = codbar
        self.img_path = img_path
        self.evento = threading.Event() if aguardar else None
        self.erro = None
        self.orientacao = orientacao


def _chave_andamento(codbar, orientacao):
    """Chave usada em _gerando_arte_em_andamento. Horizontal usa o codbar puro (não muda o
    comportamento/formato já existente, usado por admin_gerar_arte/gerar_arte_publica); vertical
    usa uma chave composta, pra uma geração vertical em andamento nunca bloquear (nem ser
    bloqueada por) uma geração horizontal do mesmo produto, e vice-versa — são pipelines/
    arquivos independentes."""
    return codbar if orientacao == 'horizontal' else f'{codbar}:{orientacao}'


def _enfileirar_geracao_arte(codbar, img_path, aguardar=False, forcar=False, orientacao='horizontal'):
    """Coloca a geração de arte na fila em vez de disparar na hora. Com vários dispositivos em
    lojas/clientes diferentes consultando ao mesmo tempo, gerar tudo em paralelo estourava o
    rate limit do Gemini (429 RESOURCE_EXHAUSTED, já visto em produção) — um único worker
    (_worker_fila_arte) processa a fila em sequência, então nunca há mais de uma chamada ao
    Gemini em andamento por vez, custe o que custar em latência sob carga.

    `forcar=True` ignora a checagem de "arte já existe" (usado pelo botão de regenerar do
    admin). `aguardar=True` bloqueia até o job específico terminar e retorna o job para o
    chamador checar `job.erro` — usado pelas rotas que precisam responder com o resultado
    (admin_gerar_arte, gerar_arte_publica); sem isso, é fire-and-forget (retorna o job já
    enfileirado, mas ninguém espera por ele). `orientacao='vertical'` gera a arte pro terminal
    em pé (ver gerar_arte_publicitaria_vertical) em vez da horizontal de sempre.

    Retorna None quando não há nada a fazer (já em andamento, arte já existe e não é forçado,
    sem chave Gemini configurada, ou produto não cadastrado)."""
    chave = _chave_andamento(codbar, orientacao)
    if chave in _gerando_arte_em_andamento:
        return None
    if not forcar and _arte_url(codbar, orientacao):
        return None
    if not _ler_todas_config().get('GEMINI_API_KEY', '').strip():
        return None
    if not Produto.query.filter_by(codbar=codbar).first():
        return None

    _gerando_arte_em_andamento.add(chave)
    job = _JobArte(codbar, img_path, aguardar, orientacao)
    _fila_arte.put(job)
    return job


def _worker_fila_arte():
    """Processa a fila de geração de arte um item por vez, para sempre, numa única thread —
    é essa serialização que garante no máximo uma chamada ao Gemini em andamento simultânea,
    independente de quantos dispositivos estejam consultando produtos diferentes ao mesmo
    tempo (nem quantas orientações diferentes). Refaz a consulta do produto aqui dentro (em vez
    de receber o objeto já carregado) para não reaproveitar uma instância do SQLAlchemy entre
    threads/sessões diferentes."""
    while True:
        job = _fila_arte.get()
        try:
            with app.app_context():
                produto = Produto.query.filter_by(codbar=job.codbar).first()
                if produto:
                    if job.orientacao == 'vertical':
                        gerar_arte_publicitaria_vertical(produto, job.img_path)
                    else:
                        gerar_arte_publicitaria(produto, job.img_path)
                    logging.info(f"Arte publicitária ({job.orientacao}) gerada (fila) para {job.codbar}")
        except Exception as e:
            job.erro = str(e)
            logging.error(f"Erro ao gerar arte ({job.orientacao}) da fila para {job.codbar}: {e}")
        finally:
            _gerando_arte_em_andamento.discard(_chave_andamento(job.codbar, job.orientacao))
            if job.evento:
                job.evento.set()
            _fila_arte.task_done()


def _iniciar_worker_fila_arte():
    threading.Thread(target=_worker_fila_arte, daemon=True).start()

def find_existing_image(codbar, img_dir, image_extensions):
    """Procura a imagem existente em um diretório"""
    for ext in image_extensions:
        temp_path = os.path.join(img_dir, f'{codbar}.{ext}')
        if os.path.exists(temp_path):
            return temp_path
    return None

def _static_url(fs_path):
    """Converte um caminho de arquivo dentro de static/ (com separadores do SO, ex.:
    'static\\imgs_produtos\\x.png' no Windows) em uma URL http correta e absoluta."""
    rel_path = os.path.relpath(fs_path, 'static').replace(os.sep, '/')
    return url_for('static', filename=rel_path, _external=True)


def _arte_url(codbar, orientacao='horizontal'):
    """Retorna a URL da arte publicitária já gerada para o produto, ou None se ainda não existir.
    `orientacao='vertical'` olha pro arquivo <codbar>_vertical.webp (terminal em pé, ver
    gerar_arte_publicitaria_vertical) em vez do <codbar>.webp horizontal de sempre — os dois
    arquivos são independentes, um produto pode ter as duas artes ao mesmo tempo."""
    nome_arquivo = f'{codbar}_vertical.webp' if orientacao == 'vertical' else f'{codbar}.webp'
    if os.path.exists(os.path.join(ARTES_FOLDER, nome_arquivo)):
        return url_for('static', filename=f'artes_geradas/{nome_arquivo}', _external=True)
    return None

def _marcar_tem_foto(produto_ou_codbar, valor):
    """Mantém Produto.tem_foto em sincronia com a existência real da foto crua em disco — chamar
    em TODO ponto que salva ou remove static/imgs_produtos/<codbar>.*, ou a coluna volta a ficar
    tão inconfiável quanto o foto_png legado que ela substitui pra fins de ordenação/filtro.
    Aceita o objeto Produto já carregado (evita uma query extra quando o chamador já tem) ou o
    codbar puro (busca por conta própria). Não dá commit sozinho — o chamador decide quando
    (geralmente já está fazendo outro commit por perto)."""
    produto = produto_ou_codbar if isinstance(produto_ou_codbar, Produto) else Produto.query.filter_by(codbar=produto_ou_codbar).first()
    if produto:
        produto.tem_foto = bool(valor)


def _imagem_e_segura(image_data):
    """Classifica se uma imagem é apropriada pra exibição pública num terminal de preços de
    loja (sem nudez/pornografia/conteúdo sexual). Camada de segurança adicionada depois de um
    incidente real (ver CLAUDE.md): uma fonte externa "confiável" (PreçoMelhor, com foto
    hospedada no próprio CDN deles, não um resultado de busca genérico) tinha um mapeamento
    EAN→imagem pornográfica na própria base de dados — provou que NENHUMA fonte automática pode
    ser assumida como 100% segura só por ser "curada". A defesa de verdade é checar o CONTEÚDO
    da imagem antes de salvar, não confiar cegamente numa fonte específica.

    Usa o próprio Gemini (multimodal) como classificador — reaproveita a mesma infraestrutura já
    usada pra gerar arte (`Part.from_bytes`, mesmo client), sem precisar de chave/serviço novo
    (ex.: Cloud Vision SafeSearch, que exigiria configurar faturamento/API separada).

    Falha aberta deliberada: se a classificação der erro (sem chave configurada, rede, cota
    esgotada), retorna True (permite salvar) — uma falha aqui não pode travar toda a busca de
    imagem do sistema. Essa checagem é a PRIMEIRA linha de defesa (impede a imagem de ser salva
    em primeiro lugar); o bloqueio permanente por EAN (`Produto.busca_imagem_bloqueada`) continua
    sendo a segunda linha, pra quando algo passa despercebido por aqui mesmo assim."""
    api_key = _ler_todas_config().get('GEMINI_API_KEY', '').strip()
    if not api_key:
        return True
    try:
        client = genai.Client(vertexai=True, api_key=api_key)
        response = client.models.generate_content(
            model='gemini-2.5-flash-lite',
            contents=[
                genai_types.Part.from_bytes(data=image_data, mime_type='image/jpeg'),
                'Esta imagem vai ser exibida publicamente num terminal de preços de supermercado, '
                'como foto de um produto do catálogo. Ela contém nudez, conteúdo sexual/pornográfico, '
                'violência gráfica, ou é flagrantemente imprópria pra um ambiente familiar de varejo? '
                'Responda só com uma palavra, sem explicação: SEGURA ou IMPROPRIA.',
            ],
        )
        texto = (response.text or '').strip().upper()
        return 'IMPROPRIA' not in texto
    except Exception as e:
        logging.warning(f"Erro ao classificar segurança da imagem (permitindo por padrão): {e}")
        return True


def _quarentenar_imagem(image_data, codbar, origem=None):
    """Salva uma imagem rejeitada por `_imagem_e_segura` na pasta de quarentena + registra uma
    linha em `ImagemQuarentena` pra revisão humana depois (aba "Quarentena" do painel), em vez
    de só descartar silenciosamente como acontecia antes. Nome do arquivo leva timestamp pra não
    colidir se o mesmo EAN for rejeitado mais de uma vez (ex.: tentativas em fontes diferentes).

    Bloqueia a busca automática de imagem desse EAN na hora (mesma lógica de
    `DELETE /deletar-imagem-produto/<codbar>?bloquear=true`, inclusive criando um `Produto`
    mínimo se o EAN ainda não existir cadastrado) — erra pro lado de proteger o cliente por
    padrão; a revisão humana decide DEPOIS se era falso positivo (aí libera, ver rota
    `/admin/quarentena/<id>/liberar`), não antes."""
    nome_arquivo = f'{codbar}_{int(datetime.utcnow().timestamp())}.jpg'
    caminho = os.path.join(QUARENTENA_FOLDER, nome_arquivo)
    try:
        with open(caminho, 'wb') as f:
            f.write(image_data)
        db.session.add(ImagemQuarentena(codbar=codbar, arquivo=nome_arquivo, origem=origem))

        produto = Produto.query.filter_by(codbar=codbar).first()
        if not produto:
            produto = Produto(codbar=codbar)
            db.session.add(produto)
        produto.busca_imagem_bloqueada = True

        db.session.commit()
        logging.error(f"Imagem de {codbar} (origem={origem}) colocada em quarentena pra revisão: {caminho}")
    except Exception as e:
        db.session.rollback()
        logging.error(f"Erro ao colocar imagem de {codbar} em quarentena: {e}")


def save_image_from_response(image_data, codbar, origem=None):
    """Salva a imagem a partir da resposta de uma requisição.

    Pedido do usuário: nunca duplicar a mesma imagem em disco (crua + processada) — só
    `PROCESSED_IMAGES_FOLDER` existe como local de armazenamento daqui pra frente. A foto crua
    nunca é escrita em `IMAGES_FOLDER`; é aberta direto dos bytes em memória (`io.BytesIO`),
    processada (remoção de fundo) e só o resultado final vai pro disco. `save_image_with_background_removal`
    sempre salva algo em `file_path`, mesmo com `REMBG_ENABLED=false` (salva sem remover fundo
    nesse caso) — sem essa garantia, desativar o rembg faria a imagem não ser salva em lugar
    nenhum, já que não sobra mais uma cópia crua como rede de segurança."""
    processed_file_path = os.path.join(PROCESSED_IMAGES_FOLDER, f'{codbar}.png')

    reprovada = not _imagem_e_segura(image_data)
    revisao_obrigatoria = bool(origem) and _fonte_exige_revisao(origem)
    if reprovada or revisao_obrigatoria:
        if reprovada:
            logging.error(f"Imagem REJEITADA por conteúdo impróprio pro produto {codbar} (origem={origem}) — indo pra quarentena.")
            mensagem = 'Imagem rejeitada: conteúdo classificado como impróprio (em quarentena pra revisão)'
        else:
            logging.warning(f"Imagem de {codbar} (origem={origem}) indo pra quarentena: fonte configurada pra exigir revisão humana.")
            mensagem = f'Imagem enviada pra revisão humana (fonte "{origem}" configurada pra exigir revisão em Configurações → Fontes de Imagem)'
        _quarentenar_imagem(image_data, codbar, origem)
        return jsonify({'message': mensagem}), 422

    try:
        input_image = Image.open(io.BytesIO(image_data))
        save_image_with_background_removal(input_image, processed_file_path)

        img_url = _static_url(processed_file_path)
        logging.info(f"Imagem salva e processada para o produto {codbar}: {img_url}")

        try:
            _marcar_tem_foto(codbar, True)
            db.session.commit()
        except Exception as e:
            db.session.rollback()
            logging.warning(f"Não foi possível marcar tem_foto=True para {codbar}: {e}")

        return jsonify({'imagem_url': img_url}), 200
    except Exception as e:
        logging.error(f"Erro ao salvar a imagem do produto {codbar}: {e}")
        return jsonify({'message': 'Error saving image'}), 500

def buscar_e_salvar_imagem_google(codbar):
    # safe=active: ver comentário em fetch_product_from_google — mesma busca genérica só pelo
    # EAN em dígitos, mesmo risco de casar com conteúdo impróprio sem SafeSearch ativado.
    search_url = f"https://www.googleapis.com/customsearch/v1?q={codbar}&cx={GOOGLE_CX}&searchType=image&num=2&safe=active&key={GOOGLE_API_KEY}"
    try:
        response = requests.get(search_url)
        response.raise_for_status()
        results = response.json()
        if 'items' in results:
            for item in results['items']:
                image_url = item['link']
                if "https://cdn-cosmos.bluesoft.com.br/products/" in image_url:
                    continue
                try:
                    img_response = requests.get(image_url)
                    img_response.raise_for_status()
                    return save_image_from_response(img_response.content, codbar, origem='google')
                except requests.RequestException as e:
                    logging.warning(f"Erro ao baixar a imagem do URL {image_url}: {e}")
            return jsonify({'message': 'No valid image found from Google'}), 404
        else:
            logging.info(f"Nenhuma imagem encontrada no Google para o produto {codbar}")
            return jsonify({'message': 'No image found from Google'}), 404
    except requests.RequestException as e:
        logging.error(f"Erro ao buscar ou salvar imagem do Google para o produto {codbar}: {e}")
        return jsonify({'message': f'Error fetching or saving image from Google: {str(e)}'}), 500

def _buscar_imagem_real_precomelhor(ean):
    """Busca a URL da foto real do produto na página pública do PreçoMelhor
    (precomelhor.com.br/p/<ean>), via a tag <meta property="og:image">. Diferente do endpoint
    de dados nutricionais (fetch_product_from_precomelhor), que nunca traz imagem, essa página
    de produto às vezes tem uma foto real hospedada no CDN próprio deles (bucket Cloudflare R2,
    ex.: pub-251da151f43b454d9e192d38bf5e6d71.r2.dev/<ean>.webp) — descoberto inspecionando a
    página ao vivo depois que o usuário mandou um link de produto de exemplo. Quando o produto
    existe na base deles MAS não tem foto cadastrada, o og:image aponta em vez disso pro
    endpoint de imagem placeholder do próprio domínio (precomelhor.com.br/api/image/<ean>...),
    que é sempre o mesmo SVG genérico de "sem imagem" (confirmado ao vivo) — o sinal real de
    "tem foto de verdade" é o host do og:image ser o CDN r2.dev, não o domínio principal."""
    try:
        response = requests.get(
            f'https://www.precomelhor.com.br/p/{ean}',
            headers={'User-Agent': 'MupaBrain-ProdutosImgs/1.0'},
            timeout=15,
        )
        if response.status_code == 404:
            return None
        response.raise_for_status()
        match = re.search(r'<meta\s+property="og:image"\s+content="([^"]+)"', response.text)
        if not match:
            return None
        image_url = match.group(1)
        if 'r2.dev' not in image_url:
            return None
        return image_url
    except requests.RequestException as e:
        logging.error(f"Erro ao buscar página de produto no PreçoMelhor: {e}")
        return None


def buscar_e_salvar_imagem_precomelhor(codbar):
    """Busca e salva a imagem real do produto na página pública do PreçoMelhor (ver
    _buscar_imagem_real_precomelhor)."""
    image_url = _buscar_imagem_real_precomelhor(codbar)
    if not image_url:
        logging.info(f"Nenhuma imagem real encontrada no PreçoMelhor para o produto {codbar}")
        return jsonify({'message': 'No image found from PrecoMelhor'}), 404
    try:
        img_response = requests.get(image_url, timeout=15)
        img_response.raise_for_status()
        return save_image_from_response(img_response.content, codbar, origem='precomelhor')
    except requests.RequestException as e:
        logging.error(f"Erro ao buscar ou salvar imagem do PreçoMelhor para o produto {codbar}: {e}")
        return jsonify({'message': f'Error fetching or saving image from PrecoMelhor: {str(e)}'}), 500


def buscar_e_salvar_imagem_zaffari(codbar):
    """Busca e salva a imagem crua (fundo branco) do produto na API pública da Zaffari (VTEX)."""
    try:
        response = requests.get(
            ZAFFARI_SEARCH_URL,
            params={'fq': f'alternateIds_Ean:{codbar}'},
            timeout=15,
        )
        response.raise_for_status()
        resultados = response.json()

        if resultados:
            for produto_zaffari in resultados:
                for item in produto_zaffari.get('items', []):
                    for imagem in item.get('images', []):
                        image_url = imagem.get('imageUrl')
                        if not image_url:
                            continue
                        try:
                            img_response = requests.get(image_url, timeout=15)
                            img_response.raise_for_status()
                            return save_image_from_response(img_response.content, codbar, origem='zaffari')
                        except requests.RequestException as e:
                            logging.warning(f"Erro ao baixar a imagem do URL {image_url}: {e}")
            return jsonify({'message': 'No valid image found from Zaffari'}), 404
        else:
            logging.info(f"Nenhuma imagem encontrada na Zaffari para o produto {codbar}")
            return jsonify({'message': 'No image found from Zaffari'}), 404
    except requests.RequestException as e:
        logging.error(f"Erro ao buscar ou salvar imagem da Zaffari para o produto {codbar}: {e}")
        return jsonify({'message': f'Error fetching or saving image from Zaffari: {str(e)}'}), 500


def buscar_e_salvar_imagem_rissul(codbar):
    """Busca e salva a imagem crua (fundo branco) do produto na API pública do Rissul (VTEX,
    conta `superrissul`) — mesmo contrato da Zaffari (`fq=alternateIds_Ean:<ean>`, resposta
    é uma lista de produtos com `items[].images[].imageUrl`), confirmado testando ao vivo com
    um link de produto que o usuário mandou. EAN não encontrado retorna `200` com lista vazia
    (`[]`), não 404 — o `if resultados:` abaixo já trata isso corretamente sem checagem extra."""
    try:
        response = requests.get(
            RISSUL_SEARCH_URL,
            params={'fq': f'alternateIds_Ean:{codbar}'},
            timeout=15,
        )
        response.raise_for_status()
        resultados = response.json()

        if resultados:
            for produto_rissul in resultados:
                for item in produto_rissul.get('items', []):
                    for imagem in item.get('images', []):
                        image_url = imagem.get('imageUrl')
                        if not image_url:
                            continue
                        try:
                            img_response = requests.get(image_url, timeout=15)
                            img_response.raise_for_status()
                            return save_image_from_response(img_response.content, codbar, origem='rissul')
                        except requests.RequestException as e:
                            logging.warning(f"Erro ao baixar a imagem do URL {image_url}: {e}")
            return jsonify({'message': 'No valid image found from Rissul'}), 404
        else:
            logging.info(f"Nenhuma imagem encontrada no Rissul para o produto {codbar}")
            return jsonify({'message': 'No image found from Rissul'}), 404
    except requests.RequestException as e:
        logging.error(f"Erro ao buscar ou salvar imagem do Rissul para o produto {codbar}: {e}")
        return jsonify({'message': f'Error fetching or saving image from Rissul: {str(e)}'}), 500


SONDA_IMG_TAMANHO = 800  # px — largura pedida ao endpoint de redimensionamento, ver abaixo


def _buscar_imagem_real_sonda(ean):
    """Busca a URL da foto real do produto no Sonda Delivery (sondadelivery.com.br), a partir de
    um link de produto de exemplo mandado pelo usuário. Diferente da Zaffari/Rissul (API VTEX
    limpa) e do PreçoMelhor (og:image), o Sonda roda em ASP.NET WebForms com busca via widget
    SmartHint — sem API JSON pública conhecida — então a extração é via regex em cima do HTML da
    própria página de busca (`/delivery/busca/<ean>`), que embute o SKU do primeiro resultado num
    bloco de analytics (`gtag`) como `item_id: "<sku>"`. Funciona via requisição HTTP simples,
    sem precisar executar JS (confirmado ao vivo).

    O SKU é OBRIGATÓRIO, não um enfeite da URL: o endpoint de imagem real
    (`/img.aspx/sku/<sku>/<tamanho>/<ean>.<ext>`) não dá erro com um SKU errado/fake — devolve
    silenciosamente um placeholder genérico de tamanho FIXO (4434 bytes, sempre o mesmo,
    confirmado testando com EANs e SKUs diferentes), então pular essa etapa levaria a "imagens"
    que na verdade são todas a mesma imagem de "sem foto" disfarçada de foto real.

    Só pega o PRIMEIRO `item_id` da página (primeiro resultado da busca) — mesmo risco que já
    existe nas outras fontes raspadas (a busca pode não ser um match exato pro EAN pedido); a
    defesa contra conteúdo realmente impróprio continua sendo `_imagem_e_segura`, não esta
    função."""
    try:
        response = requests.get(
            f'https://www.sondadelivery.com.br/delivery/busca/{ean}',
            headers={'User-Agent': 'Mozilla/5.0'},
            timeout=15,
        )
        response.raise_for_status()
        match = re.search(r'item_id:\s*"(\d+)"', response.text)
        if not match:
            return None
        sku = match.group(1)
        return f'https://www.sondadelivery.com.br/img.aspx/sku/{sku}/{SONDA_IMG_TAMANHO}/{ean}.png'
    except requests.RequestException as e:
        logging.error(f"Erro ao buscar página de busca no Sonda Delivery: {e}")
        return None


def buscar_e_salvar_imagem_sonda(codbar):
    """Busca e salva a imagem real do produto no Sonda Delivery (ver _buscar_imagem_real_sonda)."""
    image_url = _buscar_imagem_real_sonda(codbar)
    if not image_url:
        logging.info(f"Nenhuma imagem real encontrada no Sonda Delivery para o produto {codbar}")
        return jsonify({'message': 'No image found from Sonda'}), 404
    try:
        img_response = requests.get(image_url, timeout=15)
        img_response.raise_for_status()
        return save_image_from_response(img_response.content, codbar, origem='sonda')
    except requests.RequestException as e:
        logging.error(f"Erro ao buscar ou salvar imagem do Sonda Delivery para o produto {codbar}: {e}")
        return jsonify({'message': f'Error fetching or saving image from Sonda: {str(e)}'}), 500


UNIDASUL_BLOB_BASE_URL = 'https://sabancoimagenspng.blob.core.windows.net/png1000x1000'


def _buscar_imagem_real_unidasul(ean):
    """Busca a URL da foto real do produto no Azure Blob Storage da Unidasul (container
    `png1000x1000` da storage account `sabancoimagenspng`) — pedido do usuário, mandando uma URL
    de container com SAS token de exemplo. Sem API/busca nenhuma: o nome do blob já é
    `<ean>_1.png` direto (confirmado listando o container com `restype=container&comp=list`,
    permitido pelo SAS token porque ele tem permissão `l`/list) — só monta a URL e confere se
    existe com HEAD.

    O SAS token (`UNIDASUL_SAS_TOKEN`, Config) é só a query string depois do `?` — igual ao
    resto do sistema, fica salvo no banco (não no código), editável em Configurações, porque um
    SAS token da Azure sempre tem validade (`se=` na query) e vai precisar ser trocado por um
    novo de tempos em tempos, sem precisar de deploy. Sem token configurado, retorna `None` sem
    tentar nada (`_pode_buscar_imagem_online`/kill switch continuam valendo por cima disso, como
    em qualquer outra fonte)."""
    token = _ler_todas_config().get('UNIDASUL_SAS_TOKEN', '').strip()
    if not token:
        return None
    url = f'{UNIDASUL_BLOB_BASE_URL}/{ean}_1.png?{token}'
    try:
        resp = requests.head(url, timeout=15)
        if resp.status_code == 200:
            return url
        return None
    except requests.RequestException as e:
        logging.error(f"Erro ao checar imagem da Unidasul: {e}")
        return None


def buscar_e_salvar_imagem_unidasul(codbar):
    """Busca e salva a imagem real do produto no Azure Blob da Unidasul (ver
    _buscar_imagem_real_unidasul)."""
    image_url = _buscar_imagem_real_unidasul(codbar)
    if not image_url:
        logging.info(f"Nenhuma imagem real encontrada na Unidasul para o produto {codbar}")
        return jsonify({'message': 'No image found from Unidasul'}), 404
    try:
        img_response = requests.get(image_url, timeout=15)
        img_response.raise_for_status()
        return save_image_from_response(img_response.content, codbar, origem='unidasul')
    except requests.RequestException as e:
        logging.error(f"Erro ao buscar ou salvar imagem da Unidasul para o produto {codbar}: {e}")
        return jsonify({'message': f'Error fetching or saving image from Unidasul: {str(e)}'}), 500


def buscar_e_salvar_imagem_serper(codbar):
    """Busca e salva a imagem do produto via Serper (serper.dev) — um proxy pago da busca de
    imagens do Google de verdade (`google.serper.dev/images`), pedido do usuário como fonte
    adicional. Útil em particular como alternativa quando a cota diária do Google Custom Search
    (100 buscas/dia, grátis) esgota — já aconteceu várias vezes nesta sessão e travava a cadeia
    até o dia seguinte.

    **`safe=active`, testado e confirmado que a API aceita** (o parâmetro volta ecoado em
    `searchParameters` da resposta) — mesma convenção do Google Custom Search, já que a Serper
    só repassa pro backend real do Google. Mesmo assim, `_imagem_e_segura` (dentro de
    `save_image_from_response`, chamada logo abaixo) continua sendo a checagem de verdade — o
    `safe=active` da fonte é defesa em profundidade, não motivo pra pular a checagem de
    conteúdo (mesma lição do incidente do PreçoMelhor: nenhuma fonte é 100% confiável sozinha).

    Token (`SERPER_API_KEY`, Config, header `X-API-KEY`) fica no banco, não no código — mesmo
    padrão do token Unidasul, editável em Configurações sem precisar de deploy."""
    api_key = _ler_todas_config().get('SERPER_API_KEY', '').strip()
    if not api_key:
        return jsonify({'message': 'Token Serper não configurado'}), 404
    try:
        response = requests.post(
            'https://google.serper.dev/images',
            headers={'X-API-KEY': api_key, 'Content-Type': 'application/json'},
            json={'q': codbar, 'safe': 'active', 'num': 5},
            timeout=15,
        )
        response.raise_for_status()
        resultados = response.json()
        for item in resultados.get('images', []):
            image_url = item.get('imageUrl')
            if not image_url or 'https://cdn-cosmos.bluesoft.com.br/products/' in image_url:
                continue
            try:
                img_response = requests.get(image_url, timeout=15)
                img_response.raise_for_status()
                return save_image_from_response(img_response.content, codbar, origem='serper')
            except requests.RequestException as e:
                logging.warning(f"Erro ao baixar a imagem do URL {image_url}: {e}")
        logging.info(f"Nenhuma imagem encontrada na Serper para o produto {codbar}")
        return jsonify({'message': 'No valid image found from Serper'}), 404
    except requests.RequestException as e:
        logging.error(f"Erro ao buscar ou salvar imagem da Serper para o produto {codbar}: {e}")
        return jsonify({'message': f'Error fetching or saving image from Serper: {str(e)}'}), 500


def _url_e_imagem_valida(url, timeout=5):
    """Confere rapidamente (HEAD, com fallback pra GET em streaming se o servidor não suportar
    HEAD direito) se uma URL aponta de verdade pra um arquivo de imagem (Content-Type image/*),
    não pra uma página HTML qualquer. Usado só pra filtrar as candidatas da busca com IA antes
    de mostrar pro usuário — o modelo às vezes devolve o link de uma página de resultado (ex.:
    toppng.com/png-...) em vez do arquivo de imagem direto, apesar do prompt pedir só links
    diretos; sem essa checagem, o picker mostrava "N imagens encontradas" com miniaturas quebradas
    (o <img> falha silenciosamente no navegador), um bug real pego testando ao vivo."""
    headers = {'User-Agent': 'Mozilla/5.0'}
    try:
        resp = requests.head(url, timeout=timeout, allow_redirects=True, headers=headers)
        content_type = resp.headers.get('Content-Type', '')
        if resp.status_code < 400 and 'image' in content_type.lower():
            return True
        if resp.status_code >= 400 or not content_type:
            resp = requests.get(url, timeout=timeout, stream=True, headers=headers)
            content_type = resp.headers.get('Content-Type', '')
            resp.close()
            return resp.status_code < 400 and 'image' in content_type.lower()
        return False
    except requests.RequestException:
        return False


def _buscar_imagens_gemini_web(nome_produto, marca, codbar):
    """Usa o Gemini com a ferramenta de busca do Google (grounding) pra sugerir candidatas a
    foto do produto na internet. Trocado de OpenAI pra Gemini nesta mesma leva — a chave da
    OpenAI configurada nesta máquina estava sem crédito (HTTP 429, 'insufficient_quota',
    confirmado testando ao vivo), e o Gemini já é o provedor de IA principal do sistema (arte
    publicitária + sugestão de nome), com uma chave já configurada e ativa — mesmo padrão de
    client usado em gerar_termos_sugestao_ia/gerar_textos_arte_ia (`genai.Client(vertexai=True,
    api_key=...)`), só que com a tool `google_search` habilitada pra busca em tempo real.

    Só devolve URLs — não baixa nem salva nada aqui (ver admin_definir_imagem_url pra isso).
    Retorna uma tupla (candidatos, erro): candidatos é uma lista de dicts {url, titulo} (pode vir
    vazia quando a IA genuinamente não acha nada confiável); erro só vem preenchido quando a
    própria chamada ao Gemini falhou (chave inválida, rede, etc.) — nesse caso o chamador deve
    mostrar esse erro pro usuário, em vez de tratar como "nenhuma imagem encontrada"."""
    api_key = _ler_todas_config().get('GEMINI_API_KEY', '').strip()
    if not api_key:
        return [], 'Nenhuma chave do Gemini configurada (Configurações → Token Gemini).'

    termo_busca = ' '.join(filter(None, [marca, nome_produto])).strip() or codbar
    prompt = (
        f"Procure na internet (Google) fotos reais e atuais do produto de supermercado a "
        f"seguir, preferencialmente em fundo branco/neutro, sem marca d'água grande. "
        f"Produto: \"{termo_busca}\" (código de barras EAN: {codbar}).\n\n"
        f"Responda APENAS com um JSON (sem markdown, sem texto antes ou depois), no formato "
        f'exato: {{"imagens": [{{"url": "...", "titulo": "..."}}]}}. Inclua até 6 URLs diretas '
        f"de arquivo de imagem (terminando em .jpg, .jpeg, .png ou .webp) que você encontrou de "
        f'verdade na busca, das mais confiáveis pras menos. Se não encontrar nenhuma foto real '
        f'desse produto específico, responda {{"imagens": []}}.'
    )

    try:
        client = genai.Client(vertexai=True, api_key=api_key)
        response = client.models.generate_content(
            model='gemini-2.5-flash',
            contents=prompt,
            config=genai_types.GenerateContentConfig(
                tools=[genai_types.Tool(google_search=genai_types.GoogleSearch())],
            ),
        )
        texto = (response.text or '').strip()
    except Exception as e:
        logging.error(f"Erro ao chamar a busca web do Gemini para {codbar}: {e}")
        return [], f'Erro ao buscar no Gemini: {e}'

    match = re.search(r'\{.*\}', texto, re.DOTALL)
    if not match:
        logging.warning(f"Busca web do Gemini para {codbar} não retornou JSON reconhecível: {texto[:300]}")
        return [], None

    try:
        parsed = json.loads(match.group(0))
    except (json.JSONDecodeError, TypeError):
        logging.warning(f"Busca web do Gemini para {codbar} retornou JSON inválido: {texto[:300]}")
        return [], None

    candidatos_brutos = []
    for item in (parsed.get('imagens') or [])[:6]:
        if not isinstance(item, dict):
            continue
        url = (item.get('url') or '').strip()
        if url.startswith('http'):
            candidatos_brutos.append({'url': url, 'titulo': (item.get('titulo') or '').strip()})

    # Validação em paralelo (não em série) — até 6 candidatas, cada uma com timeout de alguns
    # segundos; em série isso podia empilhar até ~30s no pior caso (todas lentas/travando).
    if candidatos_brutos:
        with concurrent.futures.ThreadPoolExecutor(max_workers=len(candidatos_brutos)) as executor:
            validas = list(executor.map(lambda c: _url_e_imagem_valida(c['url']), candidatos_brutos))
        candidatos = [c for c, valida in zip(candidatos_brutos, validas) if valida]
    else:
        candidatos = []
    return candidatos, None


def serialize_produto_with_image(produto):
    """Serializa um produto com a imagem"""
    img_url = None
    # PROCESSED_IMAGES_FOLDER é a única cópia que existe (nunca duplicamos crua+processada).
    img_path = find_existing_image(produto.codbar, PROCESSED_IMAGES_FOLDER, ALLOWED_EXTENSIONS)
    if img_path:
        img_url = _static_url(img_path)
    elif _busca_imagem_online_ativa() and not produto.busca_imagem_bloqueada:
        # Mesmo padrão em loop de obter_imagem_produto/admin_buscar_imagem (antes era um
        # if/elif aninhado repetindo a mesma lógica 7 vezes) — refatorado ao adicionar o toggle
        # individual por fonte, já que checar `_fonte_imagem_ativa` dentro de um loop é bem mais
        # simples do que dentro de um if/elif aninhado.
        cfg_fontes = _ler_todas_config()
        for fonte, buscar in (
            ('google', buscar_e_salvar_imagem_google),
            ('zaffari', buscar_e_salvar_imagem_zaffari),
            ('precomelhor', buscar_e_salvar_imagem_precomelhor),
            ('rissul', buscar_e_salvar_imagem_rissul),
            ('sonda', buscar_e_salvar_imagem_sonda),
            ('unidasul', buscar_e_salvar_imagem_unidasul),
            ('serper', buscar_e_salvar_imagem_serper),
        ):
            if not _fonte_imagem_ativa(fonte, cfg_fontes):
                continue
            resultado = buscar(produto.codbar)
            if resultado[1] == 200:
                img_url = resultado[0].get_json().get('imagem_url')
                break

    return {
        'codbar': produto.codbar,
        'description': produto.description,
        'ncm': produto.ncm,
        'cest_codigo': produto.cest_codigo,
        'embalagem': produto.embalagem,
        'foto_png': img_url or "No image available",
        'marca': produto.marca,
        'preco_medio': produto.preco_medio,
        'categoriaText': produto.categoriaText
    }

@app.route('/produtos', methods=['GET'])
@jwt_required()
@swag_from({
    'tags': ['Produtos'],
    'parameters': [
        {
            'name': 'codbar',
            'in': 'query',
            'type': 'string',
            'required': False,
            'description': 'Código de barras do produto'
        },
        {
            'name': 'descricao',
            'in': 'query',
            'type': 'string',
            'required': False,
            'description': 'Descrição do produto'
        },
        {
            'name': 'categoria',
            'in': 'query',
            'type': 'string',
            'required': False,
            'description': 'Categoria do produto'
        },
        {
            'name': 'page',
            'in': 'query',
            'type': 'integer',
            'required': False,
            'description': 'Número da página'
        },
        {
            'name': 'per_page',
            'in': 'query',
            'type': 'integer',
            'required': False,
            'description': 'Número de itens por página'
        }
    ],
    'responses': {
        '200': {
            'description': 'Lista de produtos'
        },
        '404': {
            'description': 'Nenhum produto encontrado'
        }
    }
})
def get_produtos():
    """Obtém a lista de produtos"""
    codbar = request.args.get('codbar', default=None, type=str)
    descricao = request.args.get('descricao', default=None, type=str)
    categoria = request.args.get('categoria', default=None, type=str)
    page = request.args.get('page', default=1, type=int)
    per_page = request.args.get('per_page', default=100, type=int)
    
    produtos_query = Produto.query
    
    if codbar:
        produtos_query = produtos_query.filter_by(codbar=codbar)
    if descricao:
        produtos_query = produtos_query.filter(Produto.description.like(f'%{descricao}%'))
    if categoria:
        produtos_query = produtos_query.filter(Produto.categoriaText.like(f'%{categoria}%'))
    
    produtos_paginados = produtos_query.paginate(page=page, per_page=per_page, error_out=False)
    produtos = produtos_paginados.items
    total_items = produtos_paginados.total
    total_pages = produtos_paginados.pages

    produtos_com_imagem = []
    produtos_sem_imagem = []

    for produto in produtos:
        img_path = find_existing_image(produto.codbar, PROCESSED_IMAGES_FOLDER, ALLOWED_EXTENSIONS)
        if img_path:
            produto.foto_png = img_path
            produtos_com_imagem.append(produto)
        else:
            produtos_sem_imagem.append(produto)

    produtos_ordenados = produtos_com_imagem + produtos_sem_imagem
    result = [serialize_produto_with_image(produto) for produto in produtos_ordenados]

    if not result:
        google_data = fetch_product_from_google(codbar)
        if google_data:
            registered_product = register_product_in_database(google_data)
            result = [serialize_produto_with_image(registered_product)]
            return jsonify({'produtos': result, 'total_pages': 1, 'total_items': 1}), 201
        else:
            return jsonify({'message': 'No products found'}), 404

    return jsonify({'produtos': result, 'total_pages': total_pages, 'total_items': total_items}), 200

def register_product_in_database(product_data):
    """Registra um produto na base de dados"""
    try:
        ncm_description = product_data.get('ncm', {}).get('description', 'Não disponível') if isinstance(product_data.get('ncm'), dict) else 'Não disponível'
        brand_name = product_data.get('brand', {}).get('name', 'Marca não disponível') if isinstance(product_data.get('brand'), dict) else 'Marca não disponível'
        thumbnail = product_data.get('thumbnail', 'Imagem não disponível')
        cest_code = product_data.get('cest', {}).get('code', 'Não disponível') if isinstance(product_data.get('cest'), dict) else 'Não disponível'
        package_type = product_data.get('package', {}).get('type', 'Não disponível') if isinstance(product_data.get('package'), dict) else 'Não disponível'
        category_name = product_data.get('category', {}).get('name', 'Não disponível') if isinstance(product_data.get('category'), dict) else 'Não disponível'
        price_value = 0.0
        if isinstance(product_data.get('price'), dict):
            price_value = product_data.get('price', {}).get('value', 0.0)
        elif isinstance(product_data.get('price'), str) and product_data['price'].replace('.', '', 1).isdigit():
            price_value = float(product_data['price'])
        new_product = Produto(
            codbar=product_data['gtin'],
            description=product_data['description'],
            ncm=ncm_description,
            marca=brand_name,
            foto_png=thumbnail,
            cest_codigo=cest_code,
            embalagem=package_type,
            preco_medio=price_value,
            categoriaText=category_name
        )
        db.session.add(new_product)
        db.session.commit()
        _incrementar_contador('STATS_CADASTROS_AUTOMATICOS')
        return new_product
    except Exception as e:
        return None

@app.route('/produto-sugestoes', methods=['GET'])
@jwt_required()
@swag_from({
    'tags': ['Produtos'],
    'parameters': [
        {
            'name': 'ean',
            'in': 'query',
            'type': 'string',
            'required': True,
            'description': 'Código de barras do produto'
        },
        {
            'name': 'tipo_sugestao',
            'in': 'query',
            'type': 'string',
            'required': True,
            'description': 'Tipo de sugestão: por_marca, combinar'
        }
    ],
    'responses': {
        '200': {
            'description': 'Sugestões de produtos relacionados'
        },
        '400': {
            'description': 'EAN é obrigatório'
        },
        '404': {
            'description': 'Produto não encontrado'
        },
        '500': {
            'description': 'Erro interno do servidor'
        }
    }
})
def produto_sugestoes():
    """Obtém sugestões de produtos relacionados"""
    try:
        ean = request.args.get('ean')
        tipo_sugestao = request.args.get('tipo_sugestao')
        if not ean:
            return jsonify({'error': 'EAN is required'}), 400
        if not tipo_sugestao:
            return jsonify({'error': 'Tipo de sugestão é obrigatório'}), 400

        produto = Produto.query.filter_by(codbar=ean).first()
        if not produto:
            # Tenta buscar o produto na API do Google
            google_data = fetch_product_from_google(ean)
            if google_data:
                produto = register_product_in_database(google_data)
            if not produto:
                # Tenta buscar o produto no Cosmos
                cosmos_data = fetch_product_from_cosmos(ean)
                if cosmos_data:
                    produto = register_product_in_database(cosmos_data)
                else:
                    return jsonify({'message': 'Product not found'}), 404

        suggestion, eans_sugeridos = obter_sugestao(produto, tipo_sugestao)

        audio_file_path = text_to_speech(suggestion, f"{ean}_{tipo_sugestao}.wav")
        if not audio_file_path:
            return jsonify({'message': 'Failed to generate audio'}), 500
        audio_url = url_for('static', filename='audios/' + f"{ean}_{tipo_sugestao}.wav", _external=True)

        # Salva a sugestão no banco de dados
        new_suggestion = SugestaoProduto(codbar=ean, tipo_sugestao=tipo_sugestao, sugestao=suggestion, audio_url=audio_url)
        db.session.add(new_suggestion)
        db.session.commit()

        return jsonify({'suggestion': suggestion, 'audio_url': audio_url, 'eans_sugeridos': eans_sugeridos}), 200
    except Exception as e:
        logging.error(f"Erro interno do servidor: {e}")
        return jsonify({'message': 'Internal server error'}), 500

def generate_product_suggestions(produto, tipo_sugestao):
    """Gera sugestões de produtos usando OpenAI GPT-4 e retorna os EANs dos produtos sugeridos"""
    try:
        description = produto.description
        marca = produto.marca
        query = ""
        
        if tipo_sugestao == 'por_marca' and marca:
            query = f"Produtos da marca {marca} que combinam com '{description}'"
        elif tipo_sugestao == 'combinar':
            query = f"O que eu posso combinar com '{description}' e que eu possa comprar"
        else:
            return "Tipo de sugestão inválido ou informações insuficientes.", []

        api_key = _ler_todas_config().get('OPENAI_API_KEY', '').strip()
        if not api_key or not api_key.startswith('sk-'):
            return "Nenhuma chave OpenAI configurada no painel de Configurações.", []
        openai.api_key = api_key

        response = openai.ChatCompletion.create(
            model="gpt-4",
            messages=[
                {"role": "system", "content": "Você é uma inteligência artificial desenvolvida para fornecer uma única resposta resumida e conversacional, indicando até dois produtos relacionados com base na descrição de um produto, em português do Brasil"},
                {"role": "user", "content": query}
            ]
        )
        suggestion = response.choices[0].message['content'].strip()

        # Vamos simular a recuperação de EANs para produtos sugeridos
        # Em um cenário real, você precisaria de uma lógica para mapear esses nomes para EANs reais
        suggested_eans = find_ean_from_text(suggestion)

        return suggestion, suggested_eans
    except Exception as e:
        logging.error(f"Erro ao gerar sugestões de produtos: {e}")
        return "Desculpe, não consegui encontrar uma sugestão adequada.", []

def find_ean_from_text(text):
    """Extrai EANs sugeridos do texto usando regex"""
    eans = re.findall(r'\d{12,13}', text)
    if not eans:
        # Se nenhum EAN for encontrado, adicione EANs simulados para teste
        eans = ['7896504300646', '7896026305133']
    return eans

def fetch_related_products(description, max_results=2):
    """Busca produtos relacionados no banco de dados"""
    related_products = Produto.query.filter(Produto.description.like(f'%{description}%')).limit(max_results).all()
    return related_products


def generate_local_suggestion(produto, tipo_sugestao):
    """Gera uma sugestão de produtos relacionados sem usar IA, buscando direto no
    catálogo (marca ou categoria). Gratuito e retorna EANs reais do banco."""
    if tipo_sugestao == 'por_marca' and produto.marca:
        relacionados = (
            Produto.query
            .filter(Produto.marca == produto.marca, Produto.codbar != produto.codbar)
            .filter(Produto.description.isnot(None), Produto.description != '')
            .order_by(db.func.random())
            .limit(2)
            .all()
        )
        intro = f"Quem leva {produto.description} também costuma gostar de"
    elif tipo_sugestao == 'combinar' and produto.categoriaText:
        relacionados = (
            Produto.query
            .filter(Produto.categoriaText == produto.categoriaText, Produto.codbar != produto.codbar)
            .filter(Produto.description.isnot(None), Produto.description != '')
            .order_by(db.func.random())
            .limit(2)
            .all()
        )
        intro = "Combina muito bem com"
    else:
        return "Tipo de sugestão inválido ou informações insuficientes.", []

    if not relacionados:
        return "Não encontramos sugestões relacionadas no catálogo para este produto.", []

    nomes = [p.description.title() for p in relacionados]
    texto_produtos = nomes[0] if len(nomes) == 1 else f"{nomes[0]} e {nomes[1]}"

    suggestion = f"{intro} {texto_produtos}."
    eans_sugeridos = [p.codbar for p in relacionados]
    return suggestion, eans_sugeridos


def gerar_termos_sugestao_ia(produto, tipo_sugestao):
    """Usa Gemini (texto, modelo leve e barato) para sugerir termos de produtos
    genuinamente relevantes/complementares. Retorna None se a IA não estiver disponível."""
    api_key = _ler_todas_config().get('GEMINI_API_KEY', '').strip()
    if not api_key:
        return None

    if tipo_sugestao == 'por_marca' and produto.marca:
        instrucao = (
            f"Liste 3 tipos de produtos da marca '{produto.marca}' que combinam com "
            f"'{produto.description}'."
        )
    else:
        instrucao = (
            f"Liste 3 tipos de produtos complementares que uma pessoa compraria junto "
            f"com '{produto.description}' em um supermercado."
        )
    instrucao += (
        " Responda APENAS com uma lista de termos de busca curtos (1-3 palavras cada), "
        "separados por vírgula, sem explicações, sem numeração."
    )

    try:
        client = genai.Client(vertexai=True, api_key=api_key)
        response = client.models.generate_content(
            model='gemini-2.5-flash-lite',
            contents=instrucao,
        )
        termos = [t.strip() for t in (response.text or '').split(',') if t.strip()]
        return termos[:3] or None
    except Exception as e:
        logging.error(f"Erro ao gerar termos de sugestão via IA: {e}")
        return None


def buscar_produtos_por_termos(termos, excluir_codbar, max_results=2):
    """Busca produtos reais no catálogo cujo nome combine com algum dos termos dados."""
    encontrados = []
    encontrados_codbars = set()
    for termo in termos:
        if len(encontrados) >= max_results:
            break
        candidatos = (
            Produto.query
            .filter(db.func.upper(Produto.description).like(f'%{termo.upper()}%'))
            .filter(Produto.codbar != excluir_codbar)
            .filter(Produto.description.isnot(None), Produto.description != '')
            .limit(max_results)
            .all()
        )
        for c in candidatos:
            if c.codbar not in encontrados_codbars and len(encontrados) < max_results:
                encontrados.append(c)
                encontrados_codbars.add(c.codbar)
    return encontrados


def generate_smart_suggestion(produto, tipo_sugestao):
    """Sugestão híbrida: a IA (Gemini) indica termos relevantes, e a busca é feita no
    próprio catálogo para garantir produtos e EANs reais. Cai para o método local
    (marca/categoria) se a IA estiver indisponível ou não achar nada no catálogo."""
    termos = gerar_termos_sugestao_ia(produto, tipo_sugestao)
    if termos:
        relacionados = buscar_produtos_por_termos(termos, produto.codbar, max_results=2)
        if relacionados:
            nomes = [p.description.title() for p in relacionados]
            texto_produtos = nomes[0] if len(nomes) == 1 else f"{nomes[0]} e {nomes[1]}"
            intro = (
                f"Combina com outros itens da marca {produto.marca}:"
                if tipo_sugestao == 'por_marca' and produto.marca
                else "Quem leva este produto também costuma comprar"
            )
            suggestion = f"{intro} {texto_produtos}."
            return suggestion, [p.codbar for p in relacionados]

    return generate_local_suggestion(produto, tipo_sugestao)


def obter_sugestao(produto, tipo_sugestao):
    """Escolhe a melhor fonte disponível para a sugestão: Gemini (híbrido, mais
    inteligente) -> OpenAI (se habilitado e com chave) -> busca local gratuita."""
    cfg = _ler_todas_config()
    if cfg.get('GEMINI_API_KEY', '').strip():
        return generate_smart_suggestion(produto, tipo_sugestao)

    if cfg.get('USE_OPENAI_SUGESTIONS', 'true') == 'true':
        suggestion, eans_sugeridos = generate_product_suggestions(produto, tipo_sugestao)
        if not suggestion.startswith('Desculpe, não consegui') and not suggestion.startswith('Nenhuma chave OpenAI'):
            return suggestion, eans_sugeridos

    return generate_local_suggestion(produto, tipo_sugestao)


def get_azure_tts_token(subscription_key):
    """Obtém o token para Azure TTS"""
    fetch_token_url = f"https://{AZURE_REGION}.api.cognitive.microsoft.com/sts/v1.0/issuetoken"
    headers = {'Ocp-Apim-Subscription-Key': subscription_key}
    response = requests.post(fetch_token_url, headers=headers)
    return response.text

def text_to_speech(text, filename):
    """Converte texto em fala usando Azure TTS"""
    token = get_azure_tts_token(AZURE_SUBSCRIPTION_KEY)
    tts_url = f"https://{AZURE_REGION}.tts.speech.microsoft.com/cognitiveservices/v1"
    headers = {
        'Authorization': f'Bearer {token}',
        'Content-Type': 'application/ssml+xml',
        'X-Microsoft-OutputFormat': 'riff-24khz-16bit-mono-pcm'
    }
    ssml = f"""
    <speak version='1.0' xml:lang='pt-BR'>
        <voice xml:lang='pt-BR' xml:gender='Female' name='pt-BR-FranciscaNeural'>
            {text}
        </voice>
    </speak>
    """
    response = requests.post(tts_url, headers=headers, data=ssml.encode('utf-8'))
    if response.status_code == 200:
        file_path = os.path.join(AUDIO_FOLDER, filename)
        with open(file_path, 'wb') as audio_file:
            audio_file.write(response.content)
        return file_path
    else:
        return None

def fetch_description_from_ean(ean):
    """Busca a descrição do produto a partir do EAN"""
    try:
        produto = Produto.query.filter_by(codbar=ean).first()
        if produto:
            return produto.description
        else:
            google_data = fetch_product_from_google(ean)
            if google_data:
                description = google_data.get('description')
                if description:
                    register_product_in_database(google_data)
                    return description
        return None
    except Exception as e:
        logging.error(f"Erro ao buscar descrição do produto: {e}")
        return None

# Rota para Listar Imagens
@app.route('/listar-imagens', methods=['GET'])
@jwt_required()
def listar_imagens():
    try:
        files = os.listdir(PROCESSED_IMAGES_FOLDER)
        images = [f for f in files if allowed_file(f)]
        image_urls = [url_for('static', filename='processed_images/' + image, _external=True) for image in images]
        return jsonify({'images': image_urls}), 200
    except Exception as e:
        return jsonify({'message': 'Error listing images', 'error': str(e)}), 500

# Route to update the average price of a product
@app.route('/produto/preco/<string:codbar>', methods=['PUT'])
@jwt_required()
@swag_from({
    'tags': ['Produtos'],
    'parameters': [
        {
            'name': 'codbar',
            'in': 'path',
            'type': 'string',
            'required': True,
            'description': 'Código de barras do produto'
        },
        {
            'name': 'preco_medio',
            'in': 'formData',
            'type': 'float',
            'required': True,
            'description': 'Novo preço médio do produto'
        }
    ],
    'responses': {
        '200': {
            'description': 'Preço médio atualizado com sucesso'
        },
        '404': {
            'description': 'Produto não encontrado'
        }
    }
})
def update_preco_medio(codbar):
    """Atualiza o preço médio de um produto"""
    data = request.get_json()
    new_preco_medio = data.get('preco_medio')
    
    if new_preco_medio is None:
        return jsonify({'message': 'Preço médio é obrigatório'}), 400

    produto = Produto.query.filter_by(codbar=codbar).first()
    if produto:
        produto.preco_medio = new_preco_medio
        db.session.commit()
        return jsonify({'message': 'Preço médio atualizado com sucesso'}), 200
    else:
        return jsonify({'message': 'Produto não encontrado'}), 404

def _incrementar_contador(chave, delta=1):
    """Incrementa um contador simples guardado na tabela Config (usado pelas estatísticas do
    resumo diário). Não é atômico entre processos concorrentes, mas o volume de escrita aqui é
    baixo o bastante (cadastros/consultas ao Cosmos) pra isso não ser um problema na prática."""
    try:
        atual = int(_ler_todas_config().get(chave, '0') or '0')
    except ValueError:
        atual = 0
    set_config(chave, str(atual + delta))


def _registrar_uso_gemini(categoria, sucesso, rate_limited=False):
    """Contabiliza uma chamada ao Gemini (modelo de imagem ou de texto) pra dar visibilidade de
    consumo dentro do próprio painel, já que checar o console do Google não é prático pro
    dia a dia. `categoria` é 'imagem' (gerar_arte_publicitaria) ou 'texto'
    (gerar_textos_arte_ia). Contadores resetados a cada resumo diário enviado com sucesso (ver
    enviar_resumo_diario_sistema) — reportam consumo 'desde o último resumo', não histórico
    acumulado."""
    prefixo = f'STATS_GEMINI_{categoria.upper()}'
    if sucesso:
        _incrementar_contador(f'{prefixo}_SUCESSOS')
    elif rate_limited:
        _incrementar_contador(f'{prefixo}_RATE_LIMIT')
    else:
        _incrementar_contador(f'{prefixo}_ERROS')


def _status_gemini():
    """Consumo do Gemini desde o último resumo diário enviado."""
    cfg = _ler_todas_config()
    def _n(chave):
        try:
            return int(cfg.get(chave, '0') or '0')
        except ValueError:
            return 0
    return {
        'imagem_sucessos': _n('STATS_GEMINI_IMAGEM_SUCESSOS'),
        'imagem_rate_limit': _n('STATS_GEMINI_IMAGEM_RATE_LIMIT'),
        'imagem_erros': _n('STATS_GEMINI_IMAGEM_ERROS'),
        'texto_sucessos': _n('STATS_GEMINI_TEXTO_SUCESSOS'),
        'texto_rate_limit': _n('STATS_GEMINI_TEXTO_RATE_LIMIT'),
        'texto_erros': _n('STATS_GEMINI_TEXTO_ERROS'),
    }


def _lista_tokens_cosmos():
    """Lê a lista de tokens do Cosmos configurada no painel (um por linha, em
    COSMOS_API_TOKENS). Mantém compatibilidade com a chave antiga COSMOS_API_TOKEN (token
    único) enquanto ela não for migrada."""
    cfg = _ler_todas_config()
    bruto = cfg.get('COSMOS_API_TOKENS', '').strip()
    if bruto:
        tokens = [t.strip() for t in bruto.splitlines() if t.strip()]
        if tokens:
            return tokens
    antigo = cfg.get('COSMOS_API_TOKEN', '').strip()
    return [antigo] if antigo else []


# Códigos HTTP que indicam problema com o TOKEN em si (cota do plano free esgotada ou token
# inválido/revogado) — nesses casos faz sentido girar pro próximo token da lista. Um 404
# significa apenas que o EAN não existe no Cosmos (não é problema de token, não gira).
_CODIGOS_HTTP_TOKEN_ESGOTADO = {401, 402, 403, 429}


def fetch_product_from_cosmos(ean):
    """Busca o produto na API do Cosmos usando o código de barras (EAN).

    O plano free do Cosmos tem cota mensal por token, então o sistema mantém uma LISTA de
    tokens (COSMOS_API_TOKENS, um por linha, configurável no painel) e um índice do 'token
    atual' (COSMOS_TOKEN_INDEX_ATUAL). Começa pelo token atual; se a resposta indicar cota
    esgotada ou token inválido (401/402/403/429), avança pro próximo token da lista, tenta de
    novo, e persiste o novo índice — assim a próxima chamada já começa de onde parou, sem
    precisar regirar todos os tokens já esgotados a cada consulta. Um 404 (produto não existe
    no Cosmos) retorna None imediatamente, sem trocar de token."""
    tokens = _lista_tokens_cosmos()
    if not tokens:
        return None

    cfg = _ler_todas_config()
    try:
        indice = int(cfg.get('COSMOS_TOKEN_INDEX_ATUAL', '0') or '0') % len(tokens)
    except ValueError:
        indice = 0

    cosmos_url = f"https://cosmos.bluesoft.com.br/api/gtins/{ean}.json"
    indice_inicial = indice

    for tentativa in range(len(tokens)):
        token = tokens[indice]
        try:
            response = requests.get(
                cosmos_url,
                headers={
                    'X-Cosmos-Token': token,
                    'Content-Type': 'application/json',
                    'User-Agent': 'Cosmos-API-Request',
                },
                timeout=15,
            )
        except requests.RequestException as e:
            logging.error(f"Erro de rede ao buscar produto no Cosmos (token #{indice + 1}/{len(tokens)}): {e}")
            return None

        if response.status_code == 200:
            if indice != indice_inicial:
                set_config('COSMOS_TOKEN_INDEX_ATUAL', str(indice))
            _incrementar_contador('STATS_COSMOS_SUCESSOS')
            return response.json()

        if response.status_code == 404:
            return None

        if response.status_code in _CODIGOS_HTTP_TOKEN_ESGOTADO:
            logging.warning(f"Token Cosmos #{indice + 1}/{len(tokens)} sem cota ou inválido (HTTP {response.status_code}), tentando o próximo.")
            _incrementar_contador('STATS_COSMOS_ROTACOES')
            indice = (indice + 1) % len(tokens)
            continue

        logging.error(f"Erro ao buscar produto no Cosmos: HTTP {response.status_code}")
        return None

    set_config('COSMOS_TOKEN_INDEX_ATUAL', str(indice))
    logging.error(f"Todos os {len(tokens)} token(ns) do Cosmos estão sem cota ou inválidos.")
    return None


def fetch_product_from_openfoodfacts(ean):
    """Busca o produto na API pública e gratuita do Open Food Facts (sem necessidade de chave)."""
    url = f"https://world.openfoodfacts.org/api/v2/product/{ean}.json"
    try:
        response = requests.get(url, headers={'User-Agent': 'MupaBrain-ProdutosImgs/1.0'}, timeout=15)
        response.raise_for_status()
        data = response.json()
        if data.get('status') != 1 or not data.get('product'):
            return None

        p = data['product']
        description = p.get('product_name') or p.get('product_name_pt') or p.get('generic_name')
        if not description:
            return None

        return {
            'gtin': ean,
            'description': description,
            'ncm': {'description': 'Não disponível'},
            'brand': {'name': p.get('brands') or 'Marca não disponível'},
            'thumbnail': p.get('image_url') or 'Imagem não disponível',
            'cest': {'code': 'Não disponível'},
            'package': {'type': p.get('quantity') or 'Não disponível'},
            'price': {},
            'category': {'name': p.get('categories') or 'Não disponível'},
        }
    except requests.RequestException as e:
        logging.error(f"Erro ao buscar produto no Open Food Facts: {e}")
        return None


def fetch_product_from_zaffari(ean):
    """Busca dados estruturados do produto na API pública da Zaffari (VTEX), gratuita."""
    try:
        response = requests.get(
            ZAFFARI_SEARCH_URL,
            params={'fq': f'alternateIds_Ean:{ean}'},
            timeout=15,
        )
        response.raise_for_status()
        resultados = response.json()
        if not resultados:
            return None

        produto_zaffari = resultados[0]
        description = produto_zaffari.get('productName') or produto_zaffari.get('productTitle')
        if not description:
            return None

        item = (produto_zaffari.get('items') or [{}])[0]
        imagens = item.get('images') or []
        thumbnail = imagens[0].get('imageUrl') if imagens else 'Imagem não disponível'
        categorias = produto_zaffari.get('categories') or []
        categoria_nome = categorias[0].strip('/').split('/')[-1] if categorias else 'Não disponível'

        return {
            'gtin': ean,
            'description': description,
            'ncm': {'description': 'Não disponível'},
            'brand': {'name': produto_zaffari.get('brand') or 'Marca não disponível'},
            'thumbnail': thumbnail,
            'cest': {'code': 'Não disponível'},
            'package': {'type': 'Não disponível'},
            'price': {},
            'category': {'name': categoria_nome},
        }
    except requests.RequestException as e:
        logging.error(f"Erro ao buscar produto na Zaffari: {e}")
        return None


def fetch_product_from_rissul(ean):
    """Busca dados estruturados do produto na API pública do Rissul (VTEX, conta `superrissul`),
    gratuita — mesmo contrato da Zaffari (ver RISSUL_SEARCH_URL/buscar_e_salvar_imagem_rissul),
    só que aqui também aproveitando nome/marca/categoria, não só a imagem.

    Pedido do usuário pra também usar como fonte de cadastro/descrição (antes só entrava na
    busca de foto crua) — mas sem o nome "Rissul" aparecer em nenhum lugar do painel: a
    supressão é feita do mesmo jeito que já existe pra Zaffari (`rotuloFonte` no frontend,
    ver seção "Nome 'zaffari' suprimido dos rótulos do painel"), só adicionando `'rissul'` à
    mesma lista — a fonte continua sendo usada normalmente, só o rótulo some da UI."""
    try:
        response = requests.get(
            RISSUL_SEARCH_URL,
            params={'fq': f'alternateIds_Ean:{ean}'},
            timeout=15,
        )
        response.raise_for_status()
        resultados = response.json()
        if not resultados:
            return None

        produto_rissul = resultados[0]
        description = produto_rissul.get('productName') or produto_rissul.get('productTitle')
        if not description:
            return None

        item = (produto_rissul.get('items') or [{}])[0]
        imagens = item.get('images') or []
        thumbnail = imagens[0].get('imageUrl') if imagens else 'Imagem não disponível'
        categorias = produto_rissul.get('categories') or []
        categoria_nome = categorias[0].strip('/').split('/')[-1] if categorias else 'Não disponível'

        return {
            'gtin': ean,
            'description': description,
            'ncm': {'description': 'Não disponível'},
            'brand': {'name': produto_rissul.get('brand') or 'Marca não disponível'},
            'thumbnail': thumbnail,
            'cest': {'code': 'Não disponível'},
            'package': {'type': 'Não disponível'},
            'price': {},
            'category': {'name': categoria_nome},
        }
    except requests.RequestException as e:
        logging.error(f"Erro ao buscar produto no Rissul: {e}")
        return None


def fetch_product_from_precomelhor(ean):
    """Busca o produto na API pública e gratuita do PreçoMelhor (precomelhor.com.br) —
    endpoint pensado pra dados nutricionais, mas retorna nome + marca mesmo quando os campos
    nutricionais vêm vazios, então serve como mais uma fonte de nome/marca na mesma cadeia de
    fallback do Cosmos/Open Food Facts/Zaffari/Google.

    Particularidade dessa API: `success` vem `true` mesmo quando o EAN não existe na base deles
    — o sinal real de "não encontrado" é `product_name` vindo vazio, não o campo `success`
    (confirmado testando com um EAN inexistente antes de integrar). Esse endpoint em si nunca
    traz imagem (o `/api/image/<ean>` dele é sempre um SVG placeholder genérico) — o thumbnail
    vem, quando existe, de `_buscar_imagem_real_precomelhor` (página pública do produto, que às
    vezes tem uma foto real hospedada no CDN próprio deles, ver docstring dessa função)."""
    try:
        response = requests.get(
            'https://www.precomelhor.com.br/api/nutrition-lookup',
            params={'ean': ean},
            headers={'User-Agent': 'MupaBrain-ProdutosImgs/1.0'},
            timeout=15,
        )
        response.raise_for_status()
        data = response.json()
        description = (data.get('product_name') or '').strip()
        if not description:
            return None

        thumbnail = _buscar_imagem_real_precomelhor(ean) or 'Imagem não disponível'
        return {
            'gtin': ean,
            'description': description,
            'ncm': {'description': 'Não disponível'},
            'brand': {'name': data.get('brand') or 'Marca não disponível'},
            'thumbnail': thumbnail,
            'cest': {'code': 'Não disponível'},
            'package': {'type': 'Não disponível'},
            'price': {},
            'category': {'name': 'Não disponível'},
        }
    except requests.RequestException as e:
        logging.error(f"Erro ao buscar produto no PreçoMelhor: {e}")
        return None


@app.route('/produto/<string:codbar>', methods=['GET'])
@jwt_required()
@swag_from({
    'tags': ['Produtos'],
    'parameters': [
        {
            'name': 'codbar',
            'in': 'path',
            'type': 'string',
            'required': True,
            'description': 'Código de barras do produto'
        }
    ],
    'responses': {
        '200': {
            'description': 'Produto encontrado',
            'schema': {
                'type': 'object',
                'properties': {
                    'codbar': {'type': 'string'},
                    'description': {'type': 'string'},
                    'ncm': {'type': 'string'},
                    'marca': {'type': 'string'},
                    'preco_medio': {'type': 'float'},
                    'categoriaText': {'type': 'string'}
                }
            }
        },
        '201': {
            'description': 'Produto cadastrado com sucesso'
        },
        '404': {
            'description': 'Produto não encontrado'
        }
    }
})
def consultar_ou_cadastrar_produto(codbar):
    """Consulta o produto no banco de dados e, se não encontrado, busca e cadastra via
    fontes externas gratuitas. Inclui uma sugestão de produtos relacionados na resposta."""
    tipo_sugestao = request.args.get('tipo_sugestao', 'combinar')

    def _sugestao_segura(produto):
        try:
            texto, eans = obter_sugestao(produto, tipo_sugestao)
            return {'texto': texto, 'eans_sugeridos': eans}
        except Exception as e:
            logging.error(f"Erro ao gerar sugestão para {produto.codbar}: {e}")
            return None

    # Busca o produto no banco de dados
    produto = Produto.query.filter_by(codbar=codbar).first()

    if produto:
        # Retorna o produto encontrado no banco de dados
        return jsonify({
            'codbar': produto.codbar,
            'description': produto.description,
            'ncm': produto.ncm,
            'marca': produto.marca,
            'preco_medio': produto.preco_medio,
            'categoriaText': produto.categoriaText,
            'sugestao': _sugestao_segura(produto),
        }), 200

    # Se o produto não for encontrado localmente, tenta cadastrar a partir de fontes externas
    # gratuitas, em ordem de qualidade dos dados: Cosmos -> Open Food Facts -> Zaffari -> PreçoMelhor.
    for fonte, buscar in (
        ('cosmos', fetch_product_from_cosmos),
        ('open_food_facts', fetch_product_from_openfoodfacts),
        ('zaffari', fetch_product_from_zaffari),
        ('precomelhor', fetch_product_from_precomelhor),
        ('rissul', fetch_product_from_rissul),
    ):
        dados = buscar(codbar)
        if not dados:
            continue

        novo_produto = register_product_in_database(dados)
        if not novo_produto:
            continue

        # Aproveita a imagem da própria fonte para já salvar a foto crua do produto
        thumbnail = dados.get('thumbnail')
        if thumbnail and thumbnail.startswith('http') and not find_existing_image(codbar, PROCESSED_IMAGES_FOLDER, ALLOWED_EXTENSIONS):
            try:
                img_response = requests.get(thumbnail, timeout=15)
                img_response.raise_for_status()
                save_image_from_response(img_response.content, codbar, origem=fonte)
            except requests.RequestException as e:
                logging.warning(f"Não foi possível salvar a imagem de {fonte} para {codbar}: {e}")

        return jsonify({
            'codbar': novo_produto.codbar,
            'description': novo_produto.description,
            'ncm': novo_produto.ncm,
            'marca': novo_produto.marca,
            'preco_medio': novo_produto.preco_medio,
            'categoriaText': novo_produto.categoriaText,
            'fonte': fonte,
            'sugestao': _sugestao_segura(novo_produto),
        }), 201

    return jsonify({'message': 'Produto não encontrado em nenhuma fonte (Cosmos, Open Food Facts, PreçoMelhor)'}), 404


# =====================================================================
# Modelo de Configurações (definido aqui para evitar circular import)
# =====================================================================

class Config(db.Model):
    """Pares chave-valor de configuração do sistema."""
    id = db.Column(db.Integer, primary_key=True)
    key = db.Column(db.String(100), unique=True, nullable=False)
    value = db.Column(db.Text, nullable=True)
    updated_at = db.Column(db.DateTime, nullable=True)

    def __repr__(self):
        return f"<Config {self.key}={self.value[:30] if self.value else ''}>"


def _ler_todas_config():
    """Retorna todas as configurações como dict {key: valor}."""
    rows = Config.query.all()
    return {r.key: (r.value or '') for r in rows}


def _busca_imagem_online_ativa():
    """Kill switch de emergência (Configurações → Flags de Funcionamento): quando desativado, a
    busca automática de foto crua em fontes externas (Google/Zaffari/PreçoMelhor/Rissul/Sonda/Unidasul) é
    pulada inteiramente — só a imagem já salva localmente continua funcionando. Não afeta
    cadastro de nome/marca (fetch_product_from_*) nem o botão "Buscar com IA" (fluxos
    separados). Pedido do usuário depois de um incidente real com imagem imprópria vinda de uma
    fonte externa — dá pra pausar a busca automática na hora, sem precisar de deploy."""
    return _ler_todas_config().get('BUSCA_IMAGEM_ONLINE_ATIVA', 'true') == 'true'


# Toda fonte externa de imagem que passa pelas 3 cadeias de busca (obter_imagem_produto,
# admin_buscar_imagem, serialize_produto_with_image) — usada tanto pro toggle individual
# (`_fonte_imagem_ativa`) quanto pra montar a UI de Configurações e o JSON de status.
FONTES_IMAGEM_DISPONIVEIS = ['google', 'zaffari', 'precomelhor', 'rissul', 'sonda', 'unidasul', 'serper']


def _fonte_imagem_ativa(fonte, cfg=None):
    """Toggle individual por fonte (Configurações → Fontes de Imagem), complementar ao kill
    switch geral (`_busca_imagem_online_ativa`, que desliga TODAS de uma vez). Pedido do
    usuário: poder desligar só uma fonte específica (ex.: uma que esteja trazendo imagem errada
    de novo) sem precisar pausar a busca inteira. Config key = `FONTE_<NOME>_ATIVA`
    (`FONTE_GOOGLE_ATIVA`, `FONTE_SONDA_ATIVA`, etc.), default `'true'` — uma fonte nova só fica
    "desligada por padrão" se alguém desligar explicitamente no painel. `cfg` opcional evita
    reler `_ler_todas_config()` a cada fonte dentro do mesmo loop de busca."""
    cfg = cfg if cfg is not None else _ler_todas_config()
    return cfg.get(f'FONTE_{fonte.upper()}_ATIVA', 'true') == 'true'


def _fonte_exige_revisao(fonte, cfg=None):
    """Segunda flag por fonte (Configurações → Fontes de Imagem), independente do toggle
    liga/desliga acima: quando marcada, a fonte continua buscando normalmente, mas TODA imagem
    que vier dela cai em quarentena pra revisão humana antes de virar a foto do produto — mesmo
    que `_imagem_e_segura` classifique como segura. Pedido do usuário depois do incidente com o
    PreçoMelhor: poder exigir revisão manual de uma fonte específica sem precisar desligá-la por
    completo (ela pode continuar trazendo imagens boas na maioria das vezes). Checada dentro de
    `save_image_from_response`, junto da checagem de conteúdo impróprio — default `'false'`
    (nenhuma fonte exige revisão até alguém ligar essa flag explicitamente)."""
    cfg = cfg if cfg is not None else _ler_todas_config()
    return cfg.get(f'FONTE_{fonte.upper()}_REVISAO_OBRIGATORIA', 'false') == 'true'


@app.before_request
def _proxy_imagens_para_vps():
    """Migração pra VPS (Hostinger): quando ativo (Configurações → "Proxy de imagens pra VPS"),
    TODA requisição em `/produto-imagem/...` é encaminhada pra VPS nova em vez de processada
    aqui — os terminais em campo continuam apontando pro mesmo host/porta de sempre
    (`srv-mupa.ddns.net:5050`, configurado em cada aparelho), sem precisar reconfigurar nenhum
    dispositivo; o corte de verdade pra VPS vira só ligar este toggle. Path checado por prefixo
    exato (`/produto-imagem/`), não um proxy genérico pra tudo — as outras rotas (painel admin,
    cadastro, etc.) continuam sendo atendidas aqui normalmente enquanto a migração não terminar.

    `before_request` (não um decorator por rota) pra não precisar tocar nas duas view functions
    (`obter_imagem_produto`/`gerar_arte_publica`) nem arriscar esquecer uma futura — qualquer
    rota nova sob esse prefixo já cai no proxy automaticamente.

    Repassa método, corpo (bytes crus, sem reinterpretar — `gerar_arte_publica` recebe a foto
    como corpo binário) e query string; NÃO repassa o header `Host` nem `Content-Length` (o
    `requests` recalcula os dois sozinho pro destino certo). Timeout de 30s: geração de arte na
    VPS pode legitimamente demorar (fila do Gemini), mas não pode travar pra sempre se a VPS cair
    — nesse caso responde 502 pro terminal em vez de pendurar a requisição."""
    if not request.path.startswith('/produto-imagem/'):
        return None
    if _ler_todas_config().get('PROXY_IMAGENS_VPS_ATIVO', 'false') != 'true':
        return None
    destino = _ler_todas_config().get('PROXY_IMAGENS_VPS_URL', '').strip().rstrip('/')
    if not destino:
        return None

    qs = request.query_string.decode()
    url = f'{destino}{request.path}' + (f'?{qs}' if qs else '')
    try:
        resp = requests.request(
            method=request.method,
            url=url,
            headers={k: v for k, v in request.headers if k.lower() not in ('host', 'content-length')},
            data=request.get_data(),
            timeout=30,
        )
        return Response(resp.content, status=resp.status_code, content_type=resp.headers.get('Content-Type'))
    except requests.RequestException as e:
        logging.error(f"Erro ao encaminhar {request.path} pra VPS ({destino}): {e}")
        return jsonify({'message': 'Erro ao encaminhar requisição para a VPS'}), 502


def _pode_buscar_imagem_online(codbar):
    """Combina as duas travas de segurança da busca automática de imagem: o kill switch
    global (`_busca_imagem_online_ativa`, afeta todo produto) e o bloqueio permanente por
    produto (`Produto.busca_imagem_bloqueada`, ver coluna). Só retorna True quando NENHUMA das
    duas está acionada."""
    if not _busca_imagem_online_ativa():
        return False
    produto = Produto.query.filter_by(codbar=codbar).first()
    return not (produto and produto.busca_imagem_bloqueada)


def set_config(key, value):
    """Cria ou atualiza uma configuração pelo par chave-valor."""
    cfg = Config.query.filter_by(key=key).first()
    if cfg:
        cfg.value = value
        cfg.updated_at = datetime.utcnow()
    else:
        cfg = Config(key=key, value=value, updated_at=datetime.utcnow())
        db.session.add(cfg)
    db.session.commit()



# =====================================================================
# Painel de Configurações e Gestão
# =====================================================================

def _git(*args, timeout=15):
    """Roda um comando git no diretório do projeto (BASE_DIR) e retorna o CompletedProcess.
    `encoding='utf-8'` explícito é o que importa aqui: sem isso, `subprocess.run(text=True)`
    usa o encoding padrão do locale do Windows (geralmente cp1252, não UTF-8) pra decodificar
    a saída — mensagens de commit com acento (ex.: "árvore") saíam como mojibake ("Ã¡rvore").
    `errors='replace'` evita quebrar tudo por causa de 1 byte estranho isolado."""
    return subprocess.run(
        ['git', *args],
        capture_output=True, text=True, encoding='utf-8', errors='replace',
        cwd=BASE_DIR, timeout=timeout,
    )


def _info_versao_git():
    """Commit atual (hash curto + data + mensagem) pra mostrar no cabeçalho do painel — dá pra
    conferir num relance se/quando a atualização automática (ver _iniciar_agendador_atualizacao)
    aplicou algo, sem precisar abrir o log do servidor. Retorna None se não for um repositório
    git (ex.: rodando a partir de um pacote/zip sem pasta .git — ver seção de packaging no
    CLAUDE.md) ou qualquer outra falha; o header simplesmente omite o bloco de versão nesse caso."""
    try:
        res = _git('log', '-1', '--format=%h|%cI|%s', timeout=10)
        if res.returncode != 0 or not res.stdout.strip():
            return None
        hash_curto, data_iso, mensagem = res.stdout.strip().split('|', 2)
        return {'hash': hash_curto, 'data_iso': data_iso, 'mensagem': mensagem}
    except Exception:
        return None


@app.route('/configuracoes', methods=['GET', 'POST'])
def configuracoes():
    """Painel de configurações: página HTML (pública, sem segredos) + API JSON (requer JWT)."""
    # GET sem Accept: application/json → renderiza o shell HTML. A própria página mostra a tela
    # de login (POST /login) e busca os dados reais por fetch autenticado depois, então nenhum
    # segredo é embutido neste HTML público.
    if request.method == 'GET' and 'application/json' not in request.headers.get('Accept', ''):
        try:
            cfg = _ler_todas_config()
            openai_key_full = cfg.get('OPENAI_API_KEY', '') or ''
            gemini_key_full = cfg.get('GEMINI_API_KEY', '') or ''
            use_openai = cfg.get('USE_OPENAI_SUGESTIONS', 'true') == 'true'
            rembg_enabled = cfg.get('REMBG_ENABLED', 'false') == 'true'
            preview = openai_key_full[:8] + '...' if openai_key_full and len(openai_key_full) > 8 else openai_key_full or '(não configurado)'
            return render_template(
                'configuracoes.html',
                openai_key_preview=preview,
                use_openai=use_openai,
                rembg_enabled=rembg_enabled,
                openai_key_full='',
                gemini_key_full='',
                versao=_info_versao_git(),
            )
        except Exception as e:
            logging.error(f"Erro ao renderizar painel: {e}")
            return jsonify({'error': 'Não foi possível carregar o painel'}), 500

    # A partir daqui (POST e GET JSON) exige JWT válido.
    verify_jwt_in_request()

    # POST: ações
    if request.method == 'POST':
        data = request.form or request.json or {}
        action = data.get('action', '')

        if action == 'save_openai_key':
            new_key = (data.get('openai_key') or '').strip()
            if new_key:
                set_config('OPENAI_API_KEY', new_key)
                return jsonify({'message': 'Token OpenAI salvo com sucesso', 'saved': True})
            else:
                return jsonify({'message': 'Chave inválida', 'saved': False}), 400

        elif action == 'save_gemini_key':
            new_key = (data.get('gemini_key') or '').strip()
            if new_key:
                set_config('GEMINI_API_KEY', new_key)
                return jsonify({'message': 'Token Gemini salvo com sucesso', 'saved': True})
            else:
                return jsonify({'message': 'Chave inválida', 'saved': False}), 400

        elif action == 'save_serper_key':
            # Token da Serper (serper.dev, X-API-KEY) — ver buscar_e_salvar_imagem_serper. Mesmo
            # padrão do token Unidasul: fica no banco, editável sem deploy.
            new_key = (data.get('serper_key') or '').strip()
            if new_key:
                set_config('SERPER_API_KEY', new_key)
                return jsonify({'message': 'Token Serper salvo com sucesso', 'saved': True})
            else:
                return jsonify({'message': 'Token inválido', 'saved': False}), 400

        elif action == 'save_unidasul_token':
            # Só a query string do SAS token (tudo depois do "?" na URL que a Unidasul manda) —
            # ver _buscar_imagem_real_unidasul. Sempre tem validade (parâmetro "se="), então fica
            # no banco em vez de no código pra dar pra trocar sem precisar de deploy.
            new_token = (data.get('unidasul_token') or '').strip().lstrip('?')
            if new_token:
                set_config('UNIDASUL_SAS_TOKEN', new_token)
                return jsonify({'message': 'Token Unidasul salvo com sucesso', 'saved': True})
            else:
                return jsonify({'message': 'Token inválido', 'saved': False}), 400

        elif action == 'toggle_use_openai':
            # Lia 'use_openai' aqui, mas o frontend sempre mandou o campo com o mesmo nome do
            # data-key do toggle ('USE_OPENAI_SUGESTIONS') — bug real, achado de passagem
            # implementando o toggle de busca de imagem online (mesmo padrão de código): o
            # valor lido nunca batia, então esse toggle sempre salvava False, mesmo clicando
            # pra ativar. Corrigido pra ler a chave que o frontend realmente manda.
            val = data.get('USE_OPENAI_SUGESTIONS')
            use_flag = (val == 'true' or val is True)
            set_config('USE_OPENAI_SUGESTIONS', str(use_flag).lower())
            return jsonify({'message': f'Flag USE_OPENAI_SUGESTIONS = {use_flag}', 'saved': True})

        elif action == 'toggle_rembg':
            # Mesmo bug do toggle acima, mesma correção.
            val = data.get('REMBG_ENABLED')
            enabled = (val == 'true' or val is True)
            set_config('REMBG_ENABLED', str(enabled).lower())
            return jsonify({'message': f'REMBG_ENABLED = {enabled}', 'saved': True})

        elif action == 'toggle_busca_imagem_online':
            val = data.get('BUSCA_IMAGEM_ONLINE_ATIVA')
            ativa = (val == 'true' or val is True)
            set_config('BUSCA_IMAGEM_ONLINE_ATIVA', str(ativa).lower())
            return jsonify({'message': f'BUSCA_IMAGEM_ONLINE_ATIVA = {ativa}', 'saved': True})

        elif action.startswith('toggle_fonte_'):
            # Dois toggles independentes por fonte, mesma action genérica pros dois (o nome da
            # fonte + qual dos dois vem do próprio nome da action, que já bate com o padrão que
            # o fallback genérico do toggle no frontend gera sozinho a partir do data-key):
            # - toggle_fonte_<x>_ativa (ver _fonte_imagem_ativa): liga/desliga a fonte de vez.
            # - toggle_fonte_<x>_revisao_obrigatoria (ver _fonte_exige_revisao): fonte continua
            #   ativa, mas TODA imagem que vier dela cai em quarentena pra revisão humana, mesmo
            #   que o classificador aprove — pedido do usuário pra poder exigir revisão manual
            #   de uma fonte específica (ex.: PreçoMelhor, depois do incidente) sem precisar
            #   desligá-la por completo.
            resto = action[len('toggle_fonte_'):]
            if resto.endswith('_ativa'):
                fonte = resto[:-len('_ativa')]
                config_key = f'FONTE_{fonte.upper()}_ATIVA'
            elif resto.endswith('_revisao_obrigatoria'):
                fonte = resto[:-len('_revisao_obrigatoria')]
                config_key = f'FONTE_{fonte.upper()}_REVISAO_OBRIGATORIA'
            else:
                return jsonify({'error': 'Ação desconhecida'}), 400
            if fonte not in FONTES_IMAGEM_DISPONIVEIS:
                return jsonify({'error': 'Fonte desconhecida'}), 400
            val = data.get(config_key)
            ativa = (val == 'true' or val is True)
            set_config(config_key, str(ativa).lower())
            return jsonify({'message': f'{config_key} = {ativa}', 'saved': True})

        elif action == 'save_proxy_imagens_vps':
            # Migração pra VPS (ver _proxy_imagens_para_vps) — salva a URL de destino e o toggle
            # juntos na mesma ação, pra nunca dar pra ativar o proxy sem uma URL configurada.
            url_destino = (data.get('PROXY_IMAGENS_VPS_URL') or '').strip().rstrip('/')
            val = data.get('PROXY_IMAGENS_VPS_ATIVO')
            ativo = (val == 'true' or val is True)
            if ativo and not url_destino:
                return jsonify({'message': 'Informe a URL da VPS antes de ativar o proxy', 'saved': False}), 400
            set_config('PROXY_IMAGENS_VPS_URL', url_destino)
            set_config('PROXY_IMAGENS_VPS_ATIVO', str(ativo).lower())
            return jsonify({'message': f'Proxy de imagens pra VPS: {"ATIVO" if ativo else "inativo"} (destino: {url_destino or "—"})', 'saved': True})

        elif action == 'toggle_resumo_ativo':
            val = data.get('RESUMO_ATIVO')
            enabled = (val == 'true' or val is True)
            set_config('RESUMO_ATIVO', str(enabled).lower())
            return jsonify({'message': f'RESUMO_ATIVO = {enabled}', 'saved': True})

        elif action == 'toggle_whatsapp_ativo':
            val = data.get('WHATSAPP_ATIVO')
            enabled = (val == 'true' or val is True)
            set_config('WHATSAPP_ATIVO', str(enabled).lower())
            return jsonify({'message': f'WHATSAPP_ATIVO = {enabled}', 'saved': True})

        elif action == 'save_cosmos_tokens':
            bruto = (data.get('cosmos_tokens') or '').strip()
            tokens = [t.strip() for t in bruto.splitlines() if t.strip()]
            set_config('COSMOS_API_TOKENS', '\n'.join(tokens))
            set_config('COSMOS_TOKEN_INDEX_ATUAL', '0')
            return jsonify({'message': f'{len(tokens)} token(ns) do Cosmos salvo(s) com sucesso', 'saved': True, 'total_tokens': len(tokens)})

        elif action == 'save_notificacoes':
            for chave in NOTIFICACAO_CONFIG_KEYS:
                if chave in data:
                    valor = data.get(chave)
                    if chave in ('RESUMO_ATIVO', 'WHATSAPP_ATIVO', 'STATUS_HORARIO_ATIVO'):
                        valor = str(valor == 'true' or valor is True).lower()
                    set_config(chave, (valor or '').strip() if isinstance(valor, str) else valor)
            return jsonify({'message': 'Configurações de notificação salvas com sucesso', 'saved': True})

        return jsonify({'error': 'Ação desconhecida'}), 400

    # GET com Accept: application/json → JSON
    cfg = _ler_todas_config()
    openai_key_full = cfg.get('OPENAI_API_KEY', '') or ''
    gemini_key_full = cfg.get('GEMINI_API_KEY', '') or ''
    unidasul_token_full = cfg.get('UNIDASUL_SAS_TOKEN', '') or ''
    serper_key_full = cfg.get('SERPER_API_KEY', '') or ''
    cosmos_status = _status_tokens_cosmos()
    return jsonify({
        'openai_key': openai_key_full[:8] + '...' if openai_key_full and len(openai_key_full) > 8 else openai_key_full or '(não configurado)',
        'openai_key_full': openai_key_full,
        'gemini_key': gemini_key_full[:8] + '...' if gemini_key_full and len(gemini_key_full) > 8 else gemini_key_full or '(não configurado)',
        'gemini_key_full': gemini_key_full,
        'unidasul_token_full': unidasul_token_full,
        'serper_key_full': serper_key_full,
        'cosmos_tokens_full': '\n'.join(_lista_tokens_cosmos()),
        'cosmos_status': cosmos_status,
        'notificacoes': {chave: cfg.get(chave, '') for chave in NOTIFICACAO_CONFIG_KEYS},
        'use_openai': cfg.get('USE_OPENAI_SUGESTIONS', 'true') == 'true',
        'rembg_enabled': cfg.get('REMBG_ENABLED', 'false') == 'true',
        'busca_imagem_online_ativa': cfg.get('BUSCA_IMAGEM_ONLINE_ATIVA', 'true') == 'true',
        'fontes_imagem_ativas': {fonte: _fonte_imagem_ativa(fonte, cfg) for fonte in FONTES_IMAGEM_DISPONIVEIS},
        'fontes_imagem_revisao': {fonte: _fonte_exige_revisao(fonte, cfg) for fonte in FONTES_IMAGEM_DISPONIVEIS},
        'proxy_imagens_vps_ativo': cfg.get('PROXY_IMAGENS_VPS_ATIVO', 'false') == 'true',
        'proxy_imagens_vps_url': cfg.get('PROXY_IMAGENS_VPS_URL', '') or '',
    })


@app.route('/api/config', methods=['GET'])
@jwt_required()
def api_config():
    """Retorna todas as configurações (para o painel frontend)."""
    cfg = _ler_todas_config()
    openai_key_full = cfg.get('OPENAI_API_KEY', '') or ''
    gemini_key_full = cfg.get('GEMINI_API_KEY', '') or ''
    unidasul_token_full = cfg.get('UNIDASUL_SAS_TOKEN', '') or ''
    serper_key_full = cfg.get('SERPER_API_KEY', '') or ''
    return jsonify({
        'openai_key': openai_key_full[:8] + '...' if openai_key_full and len(openai_key_full) > 8 else openai_key_full or '(não configurado)',
        'openai_key_full': openai_key_full,
        'gemini_key': gemini_key_full[:8] + '...' if gemini_key_full and len(gemini_key_full) > 8 else gemini_key_full or '(não configurado)',
        'gemini_key_full': gemini_key_full,
        'unidasul_token_full': unidasul_token_full,
        'serper_key_full': serper_key_full,
        'use_openai': cfg.get('USE_OPENAI_SUGESTIONS', 'true') == 'true',
        'rembg_enabled': cfg.get('REMBG_ENABLED', 'false') == 'true',
        'busca_imagem_online_ativa': cfg.get('BUSCA_IMAGEM_ONLINE_ATIVA', 'true') == 'true',
        'fontes_imagem_ativas': {fonte: _fonte_imagem_ativa(fonte, cfg) for fonte in FONTES_IMAGEM_DISPONIVEIS},
        'fontes_imagem_revisao': {fonte: _fonte_exige_revisao(fonte, cfg) for fonte in FONTES_IMAGEM_DISPONIVEIS},
        'proxy_imagens_vps_ativo': cfg.get('PROXY_IMAGENS_VPS_ATIVO', 'false') == 'true',
        'proxy_imagens_vps_url': cfg.get('PROXY_IMAGENS_VPS_URL', '') or '',
    })


@app.route('/api/teste-openai', methods=['GET'])
@jwt_required()
def api_teste_openai():
    """Testa se a chave da OpenAI está válida."""
    key = _ler_todas_config().get('OPENAI_API_KEY', '')
    if not key or not key.startswith('sk-'):
        return jsonify({'ok': False, 'message': 'Nenhuma chave OpenAI configurada no sistema.'}), 200
    try:
        openai.api_key = key
        models = openai.Model.list()
        model_names = [m.get('id', '') for m in (models.get('data') or [])]
        gpt4_available = any('gpt-4' in m for m in model_names)
        return jsonify({
            'ok': True,
            'message': 'Chave válida e com acesso à API.',
            'has_gpt4': gpt4_available,
            'modelos_disponiveis': model_names[:5],
        })
    except openai.error.AuthenticationError:
        return jsonify({'ok': False, 'message': 'Erro de autenticação. A chave está inválida ou expirada.'}), 200
    except openai.error.RateLimitError:
        return jsonify({'ok': False, 'message': 'Limite de taxa (rate limit) excedido. Tente novamente mais tarde.'}), 200
    except Exception as e:
        logging.error(f"Erro ao testar OpenAI: {e}")
        return jsonify({'ok': False, 'message': f'Erro ao conectar: {str(e)}'}), 200


@app.route('/api/teste-gemini', methods=['GET'])
@jwt_required()
def api_teste_gemini():
    """Testa se a chave do Gemini (Google Cloud / Vertex AI Modo Expresso) está válida."""
    key = _ler_todas_config().get('GEMINI_API_KEY', '')
    if not key:
        return jsonify({'ok': False, 'message': 'Nenhuma chave Gemini configurada no sistema.'}), 200
    try:
        client = genai.Client(vertexai=True, api_key=key)
        response = client.models.generate_content(
            model='gemini-2.5-flash-lite',
            contents='responda apenas: ok',
        )
        return jsonify({
            'ok': True,
            'message': 'Chave válida e com acesso à API (Vertex AI Modo Expresso).',
            'has_image_model': True,
        })
    except Exception as e:
        logging.error(f"Erro ao testar Gemini: {e}")
        return jsonify({'ok': False, 'message': f'Erro ao conectar: {str(e)}'}), 200


# =====================================================================
# Geração de Arte Publicitária (imagem crua -> peça de campanha)
# =====================================================================

ARTE_PROMPT_TEMPLATE = """Crie uma peça publicitária premium para a tela de um terminal de consulta de preços dentro de um supermercado — a partir da imagem do produto fornecida.

OBJETIVO PRINCIPAL:
Isto NÃO é uma tela informativa de consulta. É uma MICROEXPERIÊNCIA DE VENDA: o shopper já está com o produto em mãos e acabou de consultar o preço porque tem interesse nele — a cena deve aumentar esse desejo, provocando de cara "isso parece ótimo, eu quero levar" antes mesmo de ler qualquer texto.

CONCEITO VISUAL:
- Transforme o produto no protagonista de uma cena de consumo/uso aspiracional que conte uma pequena história visual — o shopper deve pensar "eu posso usar/fazer isso em casa", "isso vai ficar muito bom", "vale a pena levar".
- Se o produto É alimento ou bebida: monte uma cena gastronômica extremamente apetitosa — ingredientes frescos, textura real (vapor, cremosidade, crocância, brilho, gotas), como se estivesse pronto para ser consumido agora mesmo. Se for uma bebida gelada (refrigerante, cerveja, água, suco): adicione um leve toque de condensação, sutil e discreto (algumas gotículas pequenas e esparsas, sem filetes escorrendo nem superfície toda molhada), só o suficiente para sugerir "gelado e refrescante" sem dominar a embalagem — a etiqueta e o design do produto continuam totalmente legíveis, sem áreas embaçadas ou cobertas de água.
- Se o produto NÃO é alimento (higiene pessoal, perfumaria, cosmético, limpeza, eletrônico etc.): crie a mesma sensação de desejo e aspiração através do contexto de USO REAL da categoria (ex.: banheiro/spa moderno, rotina de cuidado pessoal, ambiente doméstico impecável) — nunca insira comida, ingredientes crus ou sobremesa só porque o nome do produto menciona um sabor/fragrância como "chocolate", "menta", "coco" etc.; isso descreve o AROMA do produto, não um alimento real a ser retratado.
- Na dúvida sobre a categoria, prefira um cenário neutro e elegante (superfície premium, iluminação de estúdio) a arriscar um contexto tematicamente errado.
- Padrão de qualidade: fotografia de still-life comercial de nível internacional, como uma campanha real de uma grande marca de bebidas/alimentos/consumo (ex.: o padrão visual usado por Coca-Cola, Nestlé, Ambev em suas peças de merchandising) — iluminação cinematográfica de estúdio, profundidade de campo bem controlada, texturas extremamente realistas (brilho, umidade, nitidez de superfície), composição sofisticada, cores vibrantes e convidativas, fundo levemente desfocado, detalhes nítidos e "apetitosos" no produto.

REGRAS PRINCIPAIS:
- Use o produto da imagem original como elemento principal.
- Preserve fielmente a embalagem, formato, proporções, cores, logotipo, textos e características visuais do produto exatamente como estão na foto de referência.
- NÃO invente informações sobre o produto.
- NÃO altere a identidade visual da embalagem.
- NÃO adicione preço ou promoção. Se a embalagem original já tiver algum texto promocional impresso nela (ex.: "Leve 5 Pague 4", "20% a mais"), preserve-o normalmente, mas só ali, uma única vez, como parte da própria embalagem — nunca o repita, amplie ou destaque em outro lugar da composição.
- O PRODUTO APARECE UMA ÚNICA VEZ em toda a composição. NÃO duplique, reflita, "ecoe" ou repita o produto (inteiro ou em parte) em nenhum outro lugar da cena — nem borrado ao fundo, nem cortado nas bordas, nem como reflexo em vidro/superfície, nem uma segunda embalagem menor ou fora de foco. Isso vale mesmo que pareça decorativo: uma segunda cópia do produto é sempre um defeito grave desta arte.
- NÃO adicione, replique ou destaque como selo/elemento gráfico separado nenhuma marca, logotipo ou selo de terceiros — nem da Mupa, nem de qualquer empresa, evento, campeonato ou promoção licenciada além do fabricante do produto. Isso vale mesmo que a embalagem original já tenha algum selo de terceiro impresso nela: preserve-o apenas ali, como parte da embalagem, e NÃO o recrie como um elemento gráfico isolado em outra parte da composição.
- NÃO adicione QR Code.
- NÃO adicione informações nutricionais ou benefícios que não estejam claramente presentes na embalagem.
- NÃO inclua pessoas, rostos, mãos ou silhuetas humanas em nenhuma parte da composição — nem em primeiro plano, nem desfocadas no fundo.

INTEGRAÇÃO DO PRODUTO (regra crítica):
- A foto de referência do produto tem fundo branco/liso de estúdio — REMOVA COMPLETAMENTE esse fundo. É proibido deixar qualquer resquício de fundo branco, área lisa ou "recorte colado" ao redor do produto.
- Extraia o produto e reintegre-o de forma perfeitamente realista dentro do novo cenário: iluminação, sombras projetadas, reflexos, gotas de condensação (quando fizer sentido para o produto) e perspectiva devem ser coerentes com o ambiente ao redor.
- O resultado deve parecer que o produto foi fisicamente fotografado ali, na mesma sessão de fotos do cenário — nunca uma montagem ou colagem.

COMPOSIÇÃO:
- Crie uma arte horizontal, moderna e sofisticada, própria para Digital Signage em supermercado.
- Preencha toda a composição com o cenário (sem áreas de fundo branco isoladas).
- Use profundidade de campo e fundo suavemente desfocado.
- Iluminação profissional de fotografia publicitária, com sombras e reflexos realistas integrando totalmente o produto ao cenário.
- Aparência de fotografia comercial de alto nível, como uma campanha publicitária real de uma grande marca — nunca aparência de panfleto promocional, ficha técnica ou tela informativa.

LAYOUT (siga exatamente esta divisão, é uma regra rígida de posicionamento):
- METADE DIREITA da imagem: o produto, grande, centralizado nessa metade, perfeitamente legível e totalmente integrado ao cenário (sem fundo branco/liso visível ao redor dele). Esta é a ÚNICA área da composição inteira onde o produto (ou qualquer parte reconhecível dele — embalagem, rótulo, tampa etc.) pode aparecer. REGRA CRÍTICA: o produto PRECISA aparecer sempre 100% visível e sem nenhum corte — nenhuma parte dele (rótulo, tampa, base) pode ficar cortada pela borda da imagem nem invadir a metade esquerda. Se necessário, afaste um pouco o produto ou ajuste seu tamanho para garantir que ele caiba inteiro, nítido e totalmente dentro da metade direita.
- METADE ESQUERDA inteira (quarto superior e quarto inferior): mantenha essa área com composição visual simples — cenário, elementos decorativos leves suavemente desfocados — SEM nenhum texto, letra, número ou tipografia adicional, E SEM nenhuma parte do produto (nem borrada, nem cortada, nem ao fundo, nem em segundo plano). Um cenário genérico (parede, superfície, ambiente) preenche essa área; o produto nunca "vaza" pra esse lado. Um sistema separado cobre depois essa metade inteira com um painel gráfico de cor sólida (a cor de identidade do produto) com o nome, uma frase curta de benefício e o preço em fonte real — então essa área da FOTO gerada por você não precisa ficar "bonita" por si só, só limpa e sem elementos que atrapalhem (o painel vai cobrir completamente o que estiver aqui).
- Use as cores da própria embalagem como referência para a identidade visual da arte.

TEXTOS:
- NÃO escreva NENHUM texto adicional na imagem — nem nome do produto, nem frases, nem números, nem preço, em nenhuma parte da composição. A única exceção é o texto que já vem impresso na embalagem original do produto (parte da foto de referência), que deve ser preservado normalmente.
- A frase de benefício, o nome do produto e o preço são adicionados depois por um sistema separado, com fonte real (garante ortografia correta); a imagem gerada deve ficar totalmente livre de tipografia adicional — sua responsabilidade aqui é só a cena/fotografia.

ESTILO: premium, comercial, moderno, clean, cinematográfico, aspiracional, apetitoso e "molhado/fresco" quando o produto for alimento ou bebida gelada, supermercado, digital signage, fotografia publicitária realista de grande marca (padrão Coca-Cola/Nestlé/Ambev), alta qualidade, visual impactante, sem pessoas.

RESULTADO ESPERADO: a cena deve parecer uma campanha de merchandising digital criada por uma grande marca — nunca uma tela informativa de consulta de preço — vendendo o produto visualmente antes mesmo que o shopper leia qualquer texto. Produto fisicamente integrado ao cenário (nunca um recorte colado sobre fundo branco), sem nenhuma arte gráfica ou texto sobreposto.

Produto de referência: {descricao}."""


def _montar_prompt_arte(produto):
    """Monta o prompt de geração de arte a partir das regras fixas + dados do produto."""
    descricao = produto.description or 'produto embalado'
    if produto.marca:
        descricao = f"{descricao} (marca {produto.marca})"
    return ARTE_PROMPT_TEMPLATE.format(descricao=descricao)


_REGEX_QUANTIDADE_EMBALAGEM = re.compile(
    r'\b\d+[.,]?\d*\s?(?:ML|L|KG|G|MG|UN|UNID\w*|CX|PCT|PACOTE\w*)\b',
    re.IGNORECASE,
)


def _extrair_quantidade_embalagem(descricao_original, nome_atual):
    """Rede de segurança: extrai a quantidade/tamanho da embalagem (ex.: '600ML', '2L', '1KG')
    da descrição original via regex — o usuário quer a quantidade SEMPRE visível na arte, mas
    a IA às vezes omite esse dado do nome (mesmo instruída a incluir) e, quando inclui, o nome
    resultante às vezes quebra em mais linhas do que o limite desenhado permite, cortando
    justamente essa parte. Por isso a quantidade nunca fica misturada dentro da string do nome:
    esta função REMOVE a quantidade de dentro de `nome_atual` se a IA já tiver incluído ali, e
    retorna o nome limpo + a quantidade separada — quem desenha (compor_texto_na_arte) sempre
    põe a quantidade como uma linha PRÓPRIA e garantida, nunca disputando espaço com o resto do
    nome dentro do limite de linhas do título.

    Retorna (nome_sem_quantidade, quantidade_ou_None)."""
    match = _REGEX_QUANTIDADE_EMBALAGEM.search(descricao_original or '')
    if not match:
        return nome_atual, None
    quantidade = match.group(0).strip()
    nome_limpo = re.sub(re.escape(quantidade), '', nome_atual or '', flags=re.IGNORECASE).strip()
    nome_limpo = re.sub(r'\s{2,}', ' ', nome_limpo).rstrip(',- ')
    return (nome_limpo or nome_atual), quantidade


def _texto_corrompido(texto):
    """Detecta indícios de corrupção de encoding num texto: o caractere de substituição
    (U+FFFD, '�') OU bytes de controle C1 (U+0080–U+009F) — estes últimos sobram quando um
    arquivo UTF-8 foi lido como Latin-1/Windows-1252 na importação (mojibake), sem virar U+FFFD
    (ex.: 'SOCOCO' virou 'SOC\\xc3\\x94CO', onde \\x94 é um controle C1 solto no meio da
    palavra). Qualquer um dos dois não tem motivo pra aparecer numa descrição de produto real."""
    return any(ch == '�' or 0x80 <= ord(ch) <= 0x9F for ch in texto)


def gerar_textos_arte_ia(produto):
    """Usa Gemini (texto, modelo leve e barato) para produzir um nome de produto limpo e uma
    headline comercial curta. Gera o nome do zero a partir da descrição bruta em vez de usar
    produto.description diretamente porque parte do catálogo tem caracteres corrompidos de uma
    importação antiga (ex.: 'AÇÚCAR' virou 'A��CAR', perda de dados irreversível) —
    a IA reconstrói o nome comercial correto a partir do contexto. Esse texto é sempre desenhado
    depois com fonte real (nunca pela IA de imagem), então não corre risco de erro de ortografia.

    Prioridade da fonte da descrição: nosso banco (produto.description) primeiro; se estiver
    VAZIO ou CORROMPIDO (caractere de substituição U+FFFD, '�'), busca uma descrição/marca
    ÍNTEGRAS pelo EAN em fontes externas, nessa ordem: Cosmos -> Open Food Facts -> Zaffari ->
    Google — a primeira que responder com uma descrição válida (não vazia, não corrompida)
    vence. só DEPOIS de resolvida a melhor fonte disponível é que a IA entra, pra reconstruir/
    limpar o nome comercial final (ela às vezes "adivinha" errado a partir de texto corrompido —
    ex.: 'SOC�CO' virou 'Socaneco' numa geração, quando o certo era 'Sococo' — ou usa uma marca
    cadastrada errada, tipo marca='KELLOGG S' nesse mesmo produto, dado de importação errado —
    por isso vale a pena buscar uma fonte íntegra ANTES de deixar a IA adivinhar).

    Retorna (nome, headline, beneficios, quantidade, marca); nome cai para produto.description
    em caixa normal se a IA falhar, beneficios é uma lista de até 3 bullets curtos (pode vir
    vazia), quantidade é a unidade/tamanho da embalagem extraída por regex (ex.: '600ML') só
    quando o nome gerado não já incluir esse dado (ou None), marca é a marca resolvida (produto
    ou fonte externa) já em Title Case, usada pra destacar tipograficamente o nome da marca
    separado do resto da descrição — ou None se não houver marca conhecida."""
    descricao_fonte = produto.description or ''
    marca_fonte = produto.marca or ''
    fallback_nome = (descricao_fonte or 'Produto').title()
    fonte_confiavel = False

    if not descricao_fonte.strip() or _texto_corrompido(descricao_fonte) or _texto_corrompido(marca_fonte):
        for buscar in (fetch_product_from_cosmos, fetch_product_from_openfoodfacts, fetch_product_from_zaffari, fetch_product_from_google, fetch_product_from_precomelhor, fetch_product_from_rissul):
            dados = buscar(produto.codbar)
            if not dados:
                continue
            desc_externa = (dados.get('description') or '').strip()
            if not desc_externa or _texto_corrompido(desc_externa):
                continue
            descricao_fonte = desc_externa
            fallback_nome = desc_externa.title()
            fonte_confiavel = True
            marca_externa = dados.get('brand', {}).get('name', '') if isinstance(dados.get('brand'), dict) else (dados.get('marca') or '')
            if marca_externa and marca_externa != 'Marca não disponível':
                marca_fonte = marca_externa
            break

    api_key = _ler_todas_config().get('GEMINI_API_KEY', '').strip()
    if not api_key:
        return fallback_nome, None, [], None, (marca_fonte.title() if marca_fonte else None)
    try:
        client = genai.Client(vertexai=True, api_key=api_key)
        instrucao_beneficios = (
            "Gere também 3 BULLETS curtos de benefício/motivo de compra (máximo 3 palavras cada, "
            "bem diretos, tipo legenda de ícone — ex.: 'Combina com seus momentos', 'Vai bem com "
            "toda refeição', 'Mais sabor pra compartilhar'). Não repita a mesma ideia da HEADLINE, "
            "cada bullet deve trazer um motivo diferente."
        )
        if fonte_confiavel:
            # Descrição já vem íntegra de uma fonte externa (Cosmos/Open Food Facts/Zaffari) —
            # não vale a pena deixar a IA "reescrever" o nome de novo aqui: às vezes ela troca
            # uma letra ou erra a marca mesmo com um texto de entrada perfeito. Usa o nome exato
            # dessa fonte (fallback_nome) e pede só a frase de benefício + os bullets.
            instrucao = (
                f"Este produto de supermercado é: \"{descricao_fonte}\""
                + (f" (marca {marca_fonte})" if marca_fonte else "")
                + ". Gere uma frase curta de BENEFÍCIO/motivo pra levar o produto (máximo "
                "5 palavras), tom emocional e comercial — não descreva o produto, diga o que ele "
                "proporciona. Exemplos de tom (adapte pro produto, não copie): 'Mais sabor pro "
                "seu dia', 'Leve para casa', 'Vale a pena experimentar', 'Um toque especial'.\n\n"
                f"{instrucao_beneficios}\n\n"
                "Responda EXATAMENTE neste formato, uma linha para cada, sem mais nada:\n"
                "HEADLINE: <frase>\n"
                "BULLET1: <bullet 1>\n"
                "BULLET2: <bullet 2>\n"
                "BULLET3: <bullet 3>"
            )
        else:
            instrucao = (
                f"A descrição bruta deste produto num sistema de catálogo é: \"{descricao_fonte}\""
                + (f" (marca {marca_fonte})" if marca_fonte else "")
                + ". Essa descrição pode estar em caixa alta, abreviada, ou conter caracteres "
                "corrompidos/símbolos estranhos (ex.: �) — ignore os símbolos quebrados e "
                "reconstrua o nome comercial correto a partir do contexto. Se não tiver certeza "
                "absoluta de qual palavra/marca um trecho corrompido deveria formar, mantenha só "
                "a parte legível em vez de inventar uma palavra parecida.\n\n"
                "Gere:\n"
                "1. Um nome de produto limpo, comercial e curto, em capitalização normal (não "
                "tudo maiúsculo), ex.: 'Coca-Cola Sem Açúcar 600ml'. IMPORTANTE: se a descrição "
                "original tiver uma quantidade/tamanho de embalagem (ex.: 600ml, 2L, 1kg, 500g, "
                "12 unidades, pacote com 3), SEMPRE inclua esse dado no nome, exatamente como "
                "está na origem — nunca omita a quantidade só pra deixar o nome mais curto.\n"
                "2. Uma frase curta de BENEFÍCIO/motivo pra levar o produto (máximo 5 palavras), "
                "tom emocional e comercial — não descreva o produto, diga o que ele proporciona. "
                "Exemplos de tom (adapte pro produto, não copie): 'Mais sabor pro seu dia', 'Leve "
                "para casa', 'Vale a pena experimentar', 'Um toque especial'.\n"
                f"3. {instrucao_beneficios}\n\n"
                "Responda EXATAMENTE neste formato, uma linha para cada, sem mais nada:\n"
                "NOME: <nome do produto>\n"
                "HEADLINE: <frase>\n"
                "BULLET1: <bullet 1>\n"
                "BULLET2: <bullet 2>\n"
                "BULLET3: <bullet 3>"
            )
        response = client.models.generate_content(
            model='gemini-2.5-flash-lite',
            contents=instrucao,
        )
        _registrar_uso_gemini('texto', sucesso=True)
        texto = (response.text or '').strip()
        nome, headline = None, None
        beneficios = []
        for linha in texto.splitlines():
            linha = linha.strip()
            if linha.upper().startswith('NOME:'):
                nome = linha.split(':', 1)[1].strip()
            elif linha.upper().startswith('HEADLINE:'):
                headline = linha.split(':', 1)[1].strip()
            elif linha.upper().startswith('BULLET'):
                valor = linha.split(':', 1)[1].strip() if ':' in linha else ''
                if valor:
                    beneficios.append(valor)
        nome_final = fallback_nome if fonte_confiavel else (nome or fallback_nome)
        nome_final, quantidade = _extrair_quantidade_embalagem(descricao_fonte, nome_final)
        marca_exibicao = marca_fonte.title() if marca_fonte and marca_fonte != 'Marca não disponível' else None
        return nome_final, headline, beneficios[:3], quantidade, marca_exibicao
    except Exception as e:
        rate_limited = '429' in str(e) or 'RESOURCE_EXHAUSTED' in str(e)
        _registrar_uso_gemini('texto', sucesso=False, rate_limited=rate_limited)
        logging.error(f"Erro ao gerar textos da arte via IA: {e}")
        return fallback_nome, None, [], None, (marca_fonte.title() if marca_fonte else None)


def _quebrar_texto(texto, font, max_width, draw):
    """Quebra um texto em linhas que cabem em max_width pixels, com a fonte dada."""
    palavras = texto.split()
    linhas = []
    linha_atual = ''
    for palavra in palavras:
        tentativa = f'{linha_atual} {palavra}'.strip()
        largura = draw.textbbox((0, 0), tentativa, font=font)[2]
        if largura <= max_width or not linha_atual:
            linha_atual = tentativa
        else:
            linhas.append(linha_atual)
            linha_atual = palavra
    if linha_atual:
        linhas.append(linha_atual)
    return linhas


def _cortar_tarjas_pretas(image_bytes, limiar=12):
    """Remove tarjas pretas sólidas no topo/base da imagem, caso o modelo tenha gerado
    letterboxing em vez de uma imagem genuinamente widescreen (16:9)."""
    image = Image.open(BytesIO(image_bytes)).convert('RGB')
    width, height = image.size
    grayscale = image.convert('L')
    pixels = grayscale.load()
    amostras_x = list(range(0, width, max(1, width // 50)))

    def linha_e_preta(y):
        media = sum(pixels[x, y] for x in amostras_x) / len(amostras_x)
        return media <= limiar

    topo = 0
    while topo < height // 3 and linha_e_preta(topo):
        topo += 1
    base = height - 1
    while base > height * 2 // 3 and linha_e_preta(base):
        base -= 1

    if topo == 0 and base == height - 1:
        return image_bytes

    cropped = image.crop((0, topo, width, base + 1))
    output = BytesIO()
    cropped.save(output, format='PNG')
    return output.getvalue()


def _extrair_cor_acento(image_path, fallback=(45, 48, 56)):
    """Extrai a cor DOMINANTE e representativa da foto crua do produto (a cor primária da
    embalagem/marca — ex.: vermelho da Coca-Cola, azul de outra marca), pra usar como base do
    painel gráfico da arte — sem precisar de um mapeamento manual por marca. Ordena por
    contagem de pixels (mais frequente primeiro), então a primeira cor que passar no filtro de
    'cor de verdade' (não cinza/branco/preto de fundo ou embalagem neutra) já é a mais
    dominante entre as candidatas válidas — não só 'uma cor saturada qualquer'.

    O fallback (quando a embalagem é genuinamente acromática — branco, preto, prata, sem
    nenhuma cor viva) é um cinza-chumbo neutro, NÃO mais um vermelho fixo: um painel vermelho
    'por padrão' ficava visivelmente errado/deslocado em produtos de outras cores quando a
    extração falhava — um neutro escuro nunca destoa da imagem, seja qual for o produto."""
    try:
        img = Image.open(image_path).convert('RGB')
        img.thumbnail((150, 150))
        paleta = img.quantize(colors=12, method=Image.MEDIANCUT).convert('RGB')
        cores = paleta.getcolors(img.width * img.height) or []
        cores.sort(key=lambda c: c[0], reverse=True)
        for _contagem, (r, g, b) in cores:
            maximo, minimo = max(r, g, b), min(r, g, b)
            saturacao = (maximo - minimo) / maximo if maximo else 0
            brilho = maximo / 255
            # Ignora tons quase brancos/pretos/cinzas (fundo/embalagem neutra) — fica só
            # com cores vivas o suficiente pra funcionar como a cor primária da marca.
            if saturacao > 0.28 and 0.15 < brilho < 0.95:
                return (r, g, b)
    except Exception:
        pass
    return fallback


def _gradiente_borda_1d(tamanho, largura_pct, alpha_max):
    """Lista de alphas 0..tamanho-1: alto nas duas pontas, caindo a 0 até a borda da faixa
    central — usado como LUT pra montar a vinheta (1 eixo por vez, depois combinados)."""
    largura_borda = max(1, int(tamanho * largura_pct))
    valores = []
    for i in range(tamanho):
        if i < largura_borda:
            alpha = int(alpha_max * (1 - i / largura_borda))
        elif i >= tamanho - largura_borda:
            alpha = int(alpha_max * (1 - (tamanho - 1 - i) / largura_borda))
        else:
            alpha = 0
        valores.append(alpha)
    return valores


ARTE_LARGURA_HORIZONTAL = 1280
ARTE_ALTURA_HORIZONTAL = 800

# Resolução real do terminal ET45 em pé (retrato) — mesma lógica/regra do par horizontal acima:
# nunca variável, sempre a resolução física do device. Medida a partir da especificação do
# painel 10.1" do ET45 (WUXGA 1920x1200, girado pra retrato = 1200x1920). Se a rede de lojas
# vier a ter terminais verticais com resolução física diferente, atualizar aqui do mesmo jeito
# que ARTE_LARGURA_HORIZONTAL/ARTE_ALTURA_HORIZONTAL — nunca assumir, sempre medir o aparelho
# real (ver seção "Atualização automática..."/regra fixa no CLAUDE.md pro par horizontal).
ARTE_LARGURA_VERTICAL = 1200
ARTE_ALTURA_VERTICAL = 1920


def _normalizar_tamanho_arte(image, largura=ARTE_LARGURA_HORIZONTAL, altura=ARTE_ALTURA_HORIZONTAL):
    """REGRA: a arte publicitária NUNCA pode sair do sistema em tamanho diferente de
    1280x800 (dispositivo na horizontal — resolução real do terminal físico, medida via
    `adb shell wm size`, não mais um valor 16:9 assumido) — sem essa normalização, o tamanho final variava a
    cada geração porque _cortar_tarjas_pretas corta uma quantidade diferente de pixels
    dependendo de quanto letterboxing o Gemini incluiu naquela chamada específica. Duas
    consequências visuais ruins vinham disso: (1) o app faz CENTER_CROP pra cobrir a tela, e
    uma fonte de tamanho variável fazia cada imagem ser escalada numa proporção diferente,
    resultado imprevisível de dispositivo pra dispositivo e de produto pra produto; (2) a
    vinheta (função abaixo) é desenhada em % da altura da imagem NESTE ponto do pipeline — se
    o corte de letterboxing reduzisse a altura antes da vinheta ser aplicada, o app then escalava
    essa imagem já menor de volta pro tamanho da tela, e esse reescalonamento fazia a faixa de
    vinheta (proporcionalmente correta na arte, mas originada de uma imagem menor) ficar fina
    demais ou sumir depois de esticada — parecia que o degradê tinha sido removido.

    Normalizando aqui, ANTES da vinheta e do texto, os dois problemas desaparecem: a arte
    sempre sai em 1344x768 exatos, então a vinheta (aplicada depois, sempre sobre essa mesma
    resolução fixa) sempre resulta no mesmo efeito visual, e o CENTER_CROP do app sempre
    escala a partir da mesma origem. Redimensiona preservando a proporção (sem esticar/
    distorcer o produto) e corta o excedente pra cobrir exatamente largura x altura — o
    equivalente a um CENTER_CROP feito aqui no servidor, em vez de depender de cada
    dispositivo fazer isso de um jeito consistente."""
    origem_largura, origem_altura = image.size
    if origem_largura == largura and origem_altura == altura:
        return image
    escala = max(largura / origem_largura, altura / origem_altura)
    nova_largura = max(largura, round(origem_largura * escala))
    nova_altura = max(altura, round(origem_altura * escala))
    image = image.resize((nova_largura, nova_altura), Image.LANCZOS)
    esquerda = (nova_largura - largura) // 2
    topo = (nova_altura - altura) // 2
    return image.crop((esquerda, topo, esquerda + largura, topo + altura))


def _aplicar_vinheta(image, largura_pct=0.14, altura_pct=0.14, alpha_max=110):
    """Escurece sutilmente as quatro bordas da imagem (efeito vinheta/gradiente preto),
    sem afetar a área central — dá um acabamento mais premium/cinematográfico à arte."""
    width, height = image.size

    linha_h = Image.new('L', (width, 1))
    linha_h.putdata(_gradiente_borda_1d(width, largura_pct, alpha_max))
    mascara_h = linha_h.resize((width, height))

    linha_v = Image.new('L', (1, height))
    linha_v.putdata(_gradiente_borda_1d(height, altura_pct, alpha_max))
    mascara_v = linha_v.resize((width, height))

    # "lighter" pega o maior alpha entre os dois eixos por pixel — os cantos (perto de ambas
    # as bordas) ficam no mesmo tom máximo da vinheta, em vez de somar e escurecer demais.
    mascara = ImageChops.lighter(mascara_h, mascara_v)

    preto = Image.new('RGBA', image.size, (0, 0, 0, 255))
    preto.putalpha(mascara)
    return Image.alpha_composite(image.convert('RGBA'), preto)


def _cor_painel_vibrante(cor_rgb, boost_saturacao=1.2, brilho_alvo=0.42, saturacao_minima=0.55):
    """Deriva do 'cor_acento' extraído da foto do produto uma cor mais rica/saturada e num
    brilho médio-escuro fixo — o acento puro (extraído pra combinar com quase qualquer produto)
    às vezes sai claro/pálido demais pra funcionar como fundo sólido de um painel gráfico
    grande; sem esse ajuste o painel perderia o efeito 'cor de marca forte' que peças
    publicitárias reais (ex.: vermelho da Coca-Cola) sempre têm."""
    r, g, b = (c / 255 for c in cor_rgb)
    h, s, _v = colorsys.rgb_to_hsv(r, g, b)
    s = min(1.0, max(s, saturacao_minima) * boost_saturacao)
    r2, g2, b2 = colorsys.hsv_to_rgb(h, s, brilho_alvo)
    return (round(r2 * 255), round(g2 * 255), round(b2 * 255))


def _desenhar_painel_curvo(draw, width, height, cor_rgba, largura_base_pct=0.44, amplitude_pct=0.035):
    """Desenha um painel de cor sólida cobrindo a metade esquerda, com a borda direita em uma
    curva orgânica (uma onda suave) em vez de uma linha reta — o acabamento gráfico de
    campanhas publicitárias reais (ex.: peças da Coca-Cola), bem mais bonito que um retângulo
    simples. Retorna o x mais à esquerda que a curva alcança, usado como limite seguro pro
    texto nunca colidir com a onda."""
    largura_base = width * largura_base_pct
    amplitude = width * amplitude_pct
    passos = 48
    pontos = [(0, 0)]
    for i in range(passos + 1):
        y = height * i / passos
        x = largura_base + amplitude * math.sin((y / height) * math.pi * 1.4)
        pontos.append((x, y))
    pontos.append((0, height))
    draw.polygon(pontos, fill=cor_rgba)
    return largura_base - amplitude


def _desenhar_texto_com_sombra(draw, pos, texto, font, fill=(255, 255, 255, 255), sombra=(0, 0, 0, 170), deslocamento=2):
    """Desenha texto com uma sombra escura sutil por baixo — garante legibilidade em cima de
    uma foto/cenário com brilho e cor variáveis (ao contrário do painel de cor sólida, essa
    área não tem um fundo previsível para calcular contraste)."""
    x, y = pos
    draw.text((x + deslocamento, y + deslocamento), texto, font=font, fill=sombra)
    draw.text((x, y), texto, font=font, fill=fill)


def _desenhar_bullet_beneficio(draw, x, y, diametro, cor_marca, texto, font, largura_max_texto, alinhar_direita=False):
    """Desenha um bullet de benefício: círculo na cor da marca com checkmark branco + texto ao
    lado (referência: peças publicitárias reais com 2-3 ícones de benefício ao lado do
    produto). Usa um checkmark genérico em vez de um ícone temático por benefício (copo,
    talher, pessoas etc.) — evita ter que decidir programaticamente qual ícone combina com
    qual frase gerada pela IA; o checkmark funciona pra qualquer benefício.

    `alinhar_direita=True` desenha o texto à ESQUERDA do ícone em vez de à direita (usado do
    lado direito da arte, sobre a foto do produto, pra manter o grupo ícone+texto encostado na
    borda direita — mesmo layout da referência)."""
    raio = diametro / 2
    cx, cy = x + raio, y + raio
    linha = _quebrar_texto(texto, font, largura_max_texto, draw)[0] if texto else ''
    altura_texto = draw.textbbox((0, 0), linha, font=font)[3]
    texto_y = y + (diametro - altura_texto) / 2
    if alinhar_direita:
        largura_linha = draw.textbbox((0, 0), linha, font=font)[2]
        texto_x = x - int(diametro * 0.35) - largura_linha
    else:
        texto_x = x + diametro * 1.35

    if alinhar_direita:
        # Sobre a foto do produto (fundo imprevisível) — cápsula semitransparente atrás do
        # grupo ícone+texto pra garantir legibilidade em qualquer parte da cena, mesmo quando
        # o bullet cai em cima do próprio produto (mesmo recurso visual usado em peças
        # publicitárias reais, não é só um workaround).
        pad_v = int(diametro * 0.22)
        pad_h = int(diametro * 0.35)
        capsula = [texto_x - pad_h, y - pad_v, x + diametro + pad_h, y + diametro + pad_v]
        raio_capsula = (capsula[3] - capsula[1]) / 2
        draw.rounded_rectangle(capsula, radius=raio_capsula, fill=(20, 20, 24, 140))

    draw.ellipse([x, y, x + diametro, y + diametro], fill=(*cor_marca, 255))
    espessura = max(2, int(diametro * 0.14))
    draw.line(
        [(cx - raio * 0.45, cy), (cx - raio * 0.1, cy + raio * 0.35), (cx + raio * 0.5, cy - raio * 0.35)],
        fill=(255, 255, 255, 255),
        width=espessura,
        joint='curve',
    )
    _desenhar_texto_com_sombra(draw, (texto_x, texto_y), linha, font)


def _separar_marca_do_nome(nome, marca):
    """Separa a marca do resto do nome pra hierarquia tipográfica em duas camadas (marca em
    destaque, resto menor/mais leve — ex.: 'Coca-Cola' grande + 'Sabor Original' pequeno).
    Quando o nome começa pela marca (caso comum, já que fallback_nome/nome gerado costuma
    incluir a marca no início), remove a marca do resto pra não repetir; quando não bate
    (fontes diferentes, formatação diferente), mantém o nome inteiro como 'resto' mesmo assim —
    prefere uma pequena redundância a perder o destaque da marca.

    Quando NÃO há marca identificada (nenhuma fonte — banco/Cosmos/OFF/Zaffari/Google/AI —
    conseguiu resolver uma), usa a 1ª palavra do nome como destaque em vez de cair pro estilo
    antigo (nome inteiro grande e uniforme, sem hierarquia nenhuma) — pedido do usuário depois
    de ver um requeijão Vigor sair com "Requeijão cremoso tradicional 400G" tudo do mesmo
    tamanho porque a marca não veio identificada daquela vez. Não é a marca de verdade, mas
    aplica a mesma regra visual (1ª palavra grande/Regular, resto pequeno/ExtraLight) — melhor
    que nome inteiro sem hierarquia."""
    marca = (marca or '').strip()
    nome = (nome or '').strip()
    if not marca:
        partes = nome.split(' ', 1)
        if len(partes) == 2 and partes[0]:
            return partes[0], partes[1]
        return None, nome
    if nome.upper().startswith(marca.upper()):
        resto = nome[len(marca):].strip(' -,')
    else:
        resto = nome
    return marca, resto


def compor_texto_na_arte(image_bytes, nome_produto, headline, cor_acento=(45, 48, 56), beneficios=None, quantidade=None, marca=None):
    """Desenha o nome do produto + headline sobre a imagem (sem texto) gerada pela IA, usando
    fonte real — garante ortografia 100% correta, ao contrário de texto renderizado diretamente
    pelo modelo de imagem. Painel de cor sólida (identidade do produto) com borda em curva
    orgânica cobrindo a metade esquerda + texto branco por cima — visual inspirado em campanhas
    publicitárias reais (ex.: Coca-Cola). Hierarquia tipográfica em duas camadas: a MARCA em
    destaque (fonte grande, peso Regular) e o RESTO do nome + quantidade numa fonte bem menor e
    mais leve (peso ExtraLight) — pedido explícito do usuário, com tamanhos/pesos calibrados
    (~80px/peso 400 pra marca, ~40px/peso 200 pro resto, escalados proporcionalmente à altura
    da imagem do mesmo jeito que todo o resto do texto). Os bullets de benefício ficam do lado
    DIREITO, sobre a foto do produto (não no painel esquerdo) — o painel esquerdo fica só com
    marca/nome/quantidade/headline, deixando a faixa inferior livre para o card de preço que o
    app Android sobrepõe depois."""
    image = Image.open(BytesIO(image_bytes)).convert('RGBA')
    image = _normalizar_tamanho_arte(image)
    image = _aplicar_vinheta(image)
    width, height = image.size

    overlay = Image.new('RGBA', image.size, (0, 0, 0, 0))
    draw = ImageDraw.Draw(overlay)

    cor_painel = _cor_painel_vibrante(cor_acento)
    borda_segura = _desenhar_painel_curvo(draw, width, height, (*cor_painel, 255))

    margin = int(width * 0.06)
    max_text_width = int(borda_segura - margin - width * 0.04)

    # Proporções calibradas (e testadas de verdade) num tablet 10" 1280x800 na horizontal — o
    # dispositivo real usado nos testes de consulta de preço. Continuam relativas à altura da
    # própria imagem (não um valor fixo em pixels) porque a IA às vezes devolve uma resolução
    # um pouco diferente; a proporção é o que garante que o texto sempre saia no mesmo tamanho
    # visual nessa tela, independente disso. Texto começa numa faixa alta do painel (não colado
    # no topo) — o painel é full-height pra cobrir toda a metade esquerda, mas o preço
    # (sobreposto depois pelo app Android) ocupa a faixa perto da base, então o conteúdo do
    # painel (nome + quantidade + headline, sem mais os bullets) precisa parar bem antes disso.
    marca_destaque, resto_nome = _separar_marca_do_nome(nome_produto, marca)

    # Marca em destaque: fonte grande, peso Regular (não Bold — pedido explícito do usuário,
    # ~80px de referência). O resto do nome (+ quantidade) usa uma fonte bem menor e mais leve
    # (~40px, peso ExtraLight) — metade do tamanho da marca, é a hierarquia "marca chama a
    # atenção, o resto é só complemento". Quando não há marca identificável, cai pro estilo
    # antigo (nome inteiro grande e em negrito) — mais seguro que não destacar nada.
    font_marca = ImageFont.truetype(FONT_PATH, int(height * 0.095))
    font_marca.set_variation_by_name('Regular')
    font_resto = ImageFont.truetype(FONT_PATH, max(10, int(height * 0.048)))
    font_resto.set_variation_by_name('ExtraLight')
    font_nome = ImageFont.truetype(FONT_PATH, int(height * 0.09))
    font_nome.set_variation_by_name('Bold')
    # Fonte da headline 40% menor que antes (pedido do usuário) — o painel ficou mais discreto
    # abaixo do nome, dando mais destaque relativo ao título e sobrando espaço pro preço.
    font_headline = ImageFont.truetype(FONT_PATH, max(10, int(height * 0.03 * 0.6)))
    font_headline.set_variation_by_name('Medium')

    text_x = margin
    text_y = int(height * 0.13)
    text_color = (255, 255, 255, 255)
    muted_color = (255, 255, 255, 215)
    resto_color = (255, 255, 255, 225)
    line_height_nome = int(height * 0.095)
    line_height_marca = int(height * 0.1)
    line_height_resto = int(height * 0.052)
    line_height_headline = int(height * 0.026)
    accent_height = max(4, int(height * 0.01))

    if marca_destaque:
        for linha in _quebrar_texto(marca_destaque, font_marca, max_text_width, draw)[:2]:
            draw.text((text_x, text_y), linha, font=font_marca, fill=text_color)
            text_y += line_height_marca
        text_y += int(height * 0.008)
        # Resto do nome + quantidade juntos na fonte pequena/leve — a quantidade não precisa
        # mais de uma linha garantida em fonte grande aqui, ela já cabe tranquilamente junto
        # com o resto do nome nesse tamanho reduzido.
        texto_resto = f"{resto_nome} {quantidade}".strip() if quantidade else resto_nome
        for linha in _quebrar_texto(texto_resto, font_resto, max_text_width, draw)[:2]:
            draw.text((text_x, text_y), linha, font=font_resto, fill=resto_color)
            text_y += line_height_resto
    else:
        # Sem marca identificável: mantém o nome inteiro grande e em negrito (comportamento
        # anterior), com a quantidade como linha garantida à parte.
        for linha in _quebrar_texto(nome_produto, font_nome, max_text_width, draw)[:3]:
            draw.text((text_x, text_y), linha, font=font_nome, fill=text_color)
            text_y += line_height_nome
        if quantidade:
            draw.text((text_x, text_y), quantidade, font=font_nome, fill=text_color)
            text_y += line_height_nome

    text_y += int(height * 0.015)
    linha_underline_largura = min(max_text_width, int(width * 0.2))
    draw.rounded_rectangle(
        [text_x, text_y, text_x + linha_underline_largura, text_y + accent_height],
        radius=accent_height // 2,
        fill=(255, 255, 255, 230),
    )
    text_y += accent_height + int(height * 0.025)

    if headline:
        for linha in _quebrar_texto(headline, font_headline, max_text_width, draw)[:2]:
            draw.text((text_x, text_y), linha, font=font_headline, fill=muted_color)
            text_y += line_height_headline

    if beneficios:
        # Bullets do lado DIREITO, sobre a foto do produto — encostados na borda direita, uma
        # embaixo da outra, ancorados na BASE da imagem (não mais centralizados verticalmente,
        # pedido do usuário) com uma margem de segurança pra não encostar na borda inferior.
        # Ícone com fundo sólido + texto com sombra (em vez do texto branco liso usado no
        # painel) porque aqui não há uma cor de fundo previsível atrás do texto, só a cena
        # gerada pela IA.
        font_beneficio = ImageFont.truetype(FONT_PATH, int(height * 0.026))
        font_beneficio.set_variation_by_name('Medium')
        diametro_icone = int(height * 0.05)
        espaco_linha = int(height * 0.075)
        beneficios_usados = beneficios[:3]
        altura_bloco = espaco_linha * (len(beneficios_usados) - 1) + diametro_icone
        icone_x = int(width * 0.965) - diametro_icone
        margem_inferior = int(height * 0.08)
        bloco_y = height - margem_inferior - altura_bloco
        largura_max_beneficio = int(width * 0.22)
        for i, texto_beneficio in enumerate(beneficios_usados):
            _desenhar_bullet_beneficio(
                draw, icone_x, bloco_y + i * espaco_linha, diametro_icone, cor_painel,
                texto_beneficio, font_beneficio, largura_max_beneficio, alinhar_direita=True,
            )

    final_image = Image.alpha_composite(image, overlay).convert('RGB')
    output = BytesIO()
    # WEBP em vez de PNG: pra uma foto (não um gráfico com poucas cores), fica 60-70% menor com
    # qualidade visualmente idêntica — e o app já reconverte pra webp no cache local mesmo, então
    # deixar de gerar em PNG evita um retrabalho.
    final_image.save(output, format='WEBP', quality=88)
    return output.getvalue()


def gerar_arte_publicitaria(produto, image_path):
    """Gera uma peça publicitária a partir da foto crua do produto: a IA (Gemini 2.5 Flash
    Image, via Vertex AI Modo Expresso) cria a cena/cenário com o produto totalmente
    integrado (sem fundo branco) e sem nenhum texto, e o nome do produto + headline (gerada
    separadamente por texto) são desenhados por cima com fonte real, garantindo ortografia
    correta. Salva o resultado em ARTES_FOLDER e retorna o caminho do arquivo gerado."""
    api_key = _ler_todas_config().get('GEMINI_API_KEY', '').strip()
    if not api_key:
        raise ValueError('Nenhuma chave Gemini configurada. Configure em Configurações antes de gerar artes.')

    prompt = _montar_prompt_arte(produto)

    ext = image_path.rsplit('.', 1)[-1].lower()
    mime_type = 'image/png' if ext == 'png' else 'image/jpeg' if ext in ('jpg', 'jpeg') else 'image/webp'
    with open(image_path, 'rb') as image_file:
        image_bytes = image_file.read()

    try:
        client = genai.Client(vertexai=True, api_key=api_key)
        response = client.models.generate_content(
            model='gemini-2.5-flash-image',
            contents=[
                genai_types.Part.from_bytes(data=image_bytes, mime_type=mime_type),
                prompt,
            ],
            config=genai_types.GenerateContentConfig(
                image_config=genai_types.ImageConfig(aspect_ratio='16:9'),
            ),
        )
        _registrar_uso_gemini('imagem', sucesso=True)
    except Exception as e:
        rate_limited = '429' in str(e) or 'RESOURCE_EXHAUSTED' in str(e)
        _registrar_uso_gemini('imagem', sucesso=False, rate_limited=rate_limited)
        raise RuntimeError(f'Erro da API Gemini: {e}')

    parts = response.candidates[0].content.parts if response.candidates else []
    output_bytes = None
    for part in parts:
        if part.inline_data and part.inline_data.data:
            output_bytes = part.inline_data.data
            break

    if not output_bytes:
        raise RuntimeError('A API Gemini não retornou uma imagem gerada (verifique se o modelo de imagem está disponível para sua chave).')

    output_bytes = _cortar_tarjas_pretas(output_bytes)
    nome_produto, headline, beneficios, quantidade, marca = gerar_textos_arte_ia(produto)
    cor_acento = _extrair_cor_acento(image_path)
    output_bytes = compor_texto_na_arte(output_bytes, nome_produto, headline, cor_acento, beneficios, quantidade, marca)

    output_path = os.path.join(ARTES_FOLDER, f'{produto.codbar}.webp')
    with open(output_path, 'wb') as out_file:
        out_file.write(output_bytes)

    return output_path


def _desenhar_painel_curvo_topo(draw, width, y_topo_painel, altura_painel, cor_rgba, amplitude_pct=0.035):
    """Equivalente vertical de _desenhar_painel_curvo: em vez de um painel lateral com borda
    direita em curva, desenha um painel cobrindo a faixa de BAIXO inteira da arte vertical, com
    a borda de CIMA em curva (mesma sanoide, só que percorrendo a largura em vez da altura).
    Retorna o y mais alto que a curva alcança, útil pra nunca colidir texto com ela (mesmo papel
    de 'borda_segura' em _desenhar_painel_curvo, só que no eixo vertical)."""
    amplitude = width * amplitude_pct
    passos = 60
    pontos = [(0, y_topo_painel + altura_painel), (0, y_topo_painel)]
    y_min = y_topo_painel
    for i in range(passos + 1):
        x = width * i / passos
        y = y_topo_painel + amplitude * math.sin((x / width) * math.pi * 1.4)
        pontos.append((x, y))
        y_min = min(y_min, y)
    pontos.append((width, y_topo_painel + altura_painel))
    draw.polygon(pontos, fill=cor_rgba)
    return y_min


def compor_arte_vertical(cena_bytes, produto, cor_acento, altura_foto=None):
    """Compõe a arte vertical (retrato, ver ARTE_LARGURA_VERTICAL/ARTE_ALTURA_VERTICAL): a cena
    gerada pela IA ocupa só a faixa de CIMA (45% da altura) e um painel de cor sólida (dominante
    extraída da própria foto do produto, ver _cor_painel_vibrante) cobre a faixa de BAIXO (55%),
    com o nome do produto desenhado por PIL (mesma garantia de ortografia 100% correta do
    pipeline horizontal) e uma caixa branca com sombra reservando o espaço onde o preço real vai
    entrar depois.

    Diferença deliberada em relação à arte horizontal: o PREÇO não é desenhado aqui. Pedido
    explícito do usuário ("os preços vai retornar do cliente") — cada loja/integração tem seu
    próprio preço em tempo real, então gravar um valor fixo na arte não faz sentido; a arte só
    reserva visualmente o espaço (caixa com sombra), e quem desenha o preço de verdade por cima
    é o app (nativo, dinâmico), igual já faz hoje pro badge de preço da arte horizontal."""
    largura = ARTE_LARGURA_VERTICAL
    altura_total = ARTE_ALTURA_VERTICAL
    altura_foto = altura_foto or round(altura_total * 0.45)
    altura_painel = altura_total - altura_foto

    cena = Image.open(BytesIO(cena_bytes)).convert('RGBA')
    cena = _normalizar_tamanho_arte(cena, largura=largura, altura=altura_foto)

    canvas = Image.new('RGBA', (largura, altura_total), (0, 0, 0, 255))
    canvas.paste(cena.convert('RGB'), (0, 0))

    overlay = Image.new('RGBA', (largura, altura_total), (0, 0, 0, 0))
    draw = ImageDraw.Draw(overlay)

    cor_painel = _cor_painel_vibrante(cor_acento)
    y_min_curva = _desenhar_painel_curvo_topo(draw, largura, altura_foto, altura_painel, (*cor_painel, 255))

    marca_destaque, resto_nome = _separar_marca_do_nome(produto.description, produto.marca)
    margin = int(largura * 0.07)
    y_texto = int(y_min_curva + altura_total * 0.045)

    font_marca = ImageFont.truetype(FONT_PATH, int(altura_total * 0.052))
    font_marca.set_variation_by_name('Bold')
    font_resto = ImageFont.truetype(FONT_PATH, int(altura_total * 0.028))
    font_resto.set_variation_by_name('Regular')

    if marca_destaque:
        _desenhar_texto_com_sombra(draw, (margin, y_texto), marca_destaque.upper(), font_marca, fill=(255, 255, 255, 255))
        y_texto += int(altura_total * 0.058)
        if resto_nome:
            _desenhar_texto_com_sombra(draw, (margin, y_texto), resto_nome, font_resto, fill=(255, 255, 255, 230))
            y_texto += int(altura_total * 0.045)
    else:
        _desenhar_texto_com_sombra(draw, (margin, y_texto), (produto.description or '').upper(), font_marca, fill=(255, 255, 255, 255))
        y_texto += int(altura_total * 0.058)

    # Caixa vazia (com sombra) reservando o espaço do preço real — ver docstring da função.
    # Posição FIXA (% de altura_foto/altura_total), não derivada de y_texto/quebra de linha do
    # nome — de propósito: o app (Kotlin) precisa desenhar o preço de verdade exatamente nesse
    # mesmo lugar, sem ter como saber quantas linhas o nome ocupou aqui no servidor. Com uma
    # posição fixa, as constantes abaixo (CAIXA_PRECO_*) podem ser espelhadas do lado do app
    # como estão, garantindo que os dois lados sempre concordem — ver
    # updatePriceBadgeVertical/CAIXA_PRECO_* no PlayerActivity.kt.
    CAIXA_PRECO_Y0_PCT = 0.16
    CAIXA_PRECO_ALTURA_PCT = 0.14
    caixa_x0 = margin
    caixa_y0 = altura_foto + int(altura_total * CAIXA_PRECO_Y0_PCT)
    caixa_x1 = largura - margin
    caixa_y1 = caixa_y0 + int(altura_total * CAIXA_PRECO_ALTURA_PCT)

    sombra_layer = Image.new('RGBA', (largura, altura_total), (0, 0, 0, 0))
    sombra_draw = ImageDraw.Draw(sombra_layer)
    deslocamento_sombra = int(altura_total * 0.006)
    sombra_draw.rounded_rectangle(
        (caixa_x0, caixa_y0 + deslocamento_sombra, caixa_x1, caixa_y1 + deslocamento_sombra),
        radius=int(altura_total * 0.018), fill=(0, 0, 0, 130),
    )
    sombra_layer = sombra_layer.filter(ImageFilter.GaussianBlur(int(altura_total * 0.012)))

    caixa_layer = Image.new('RGBA', (largura, altura_total), (0, 0, 0, 0))
    caixa_draw = ImageDraw.Draw(caixa_layer)
    caixa_draw.rounded_rectangle(
        (caixa_x0, caixa_y0, caixa_x1, caixa_y1),
        radius=int(altura_total * 0.018), fill=(255, 255, 255, 235),
    )

    final_image = Image.alpha_composite(canvas, overlay)
    final_image = Image.alpha_composite(final_image, sombra_layer)
    final_image = Image.alpha_composite(final_image, caixa_layer)

    output = BytesIO()
    final_image.convert('RGB').save(output, format='WEBP', quality=88)
    return output.getvalue()


def gerar_arte_publicitaria_vertical(produto, image_path):
    """Equivalente vertical de gerar_arte_publicitaria: gera só a CENA (Gemini, mesmas regras
    de ARTE_PROMPT_TEMPLATE, só que enquadrada em 4:3 pra caber na faixa de cima da arte
    vertical) e delega a composição do painel+nome+caixa de preço pra compor_arte_vertical.
    Salva em ARTES_FOLDER/<codbar>_vertical.webp — nome de arquivo distinto da arte horizontal
    (<codbar>.webp), pra nunca colidir/sobrescrever uma com a outra; os dois podem coexistir
    pro mesmo produto (um terminal horizontal e um vertical na mesma loja, por exemplo)."""
    api_key = _ler_todas_config().get('GEMINI_API_KEY', '').strip()
    if not api_key:
        raise ValueError('Nenhuma chave Gemini configurada. Configure em Configurações antes de gerar artes.')

    prompt = _montar_prompt_arte(produto)

    ext = image_path.rsplit('.', 1)[-1].lower()
    mime_type = 'image/png' if ext == 'png' else 'image/jpeg' if ext in ('jpg', 'jpeg') else 'image/webp'
    with open(image_path, 'rb') as image_file:
        image_bytes = image_file.read()

    try:
        client = genai.Client(vertexai=True, api_key=api_key)
        response = client.models.generate_content(
            model='gemini-2.5-flash-image',
            contents=[
                genai_types.Part.from_bytes(data=image_bytes, mime_type=mime_type),
                prompt,
            ],
            config=genai_types.GenerateContentConfig(
                image_config=genai_types.ImageConfig(aspect_ratio='4:3'),
            ),
        )
        _registrar_uso_gemini('imagem', sucesso=True)
    except Exception as e:
        rate_limited = '429' in str(e) or 'RESOURCE_EXHAUSTED' in str(e)
        _registrar_uso_gemini('imagem', sucesso=False, rate_limited=rate_limited)
        raise RuntimeError(f'Erro da API Gemini: {e}')

    parts = response.candidates[0].content.parts if response.candidates else []
    cena_bytes = None
    for part in parts:
        if part.inline_data and part.inline_data.data:
            cena_bytes = part.inline_data.data
            break

    if not cena_bytes:
        raise RuntimeError('A API Gemini não retornou uma imagem gerada (verifique se o modelo de imagem está disponível para sua chave).')

    cena_bytes = _cortar_tarjas_pretas(cena_bytes)
    cor_acento = _extrair_cor_acento(image_path)
    output_bytes = compor_arte_vertical(cena_bytes, produto, cor_acento)

    output_path = os.path.join(ARTES_FOLDER, f'{produto.codbar}_vertical.webp')
    with open(output_path, 'wb') as out_file:
        out_file.write(output_bytes)

    return output_path


@app.route('/admin/cadastrar-produto-cosmos/<string:codbar>', methods=['POST'])
@jwt_required()
def admin_cadastrar_produto_cosmos(codbar):
    """Cadastra um produto buscando especificamente no Cosmos pelo EAN (com rotação de tokens,
    ver fetch_product_from_cosmos). É o fluxo explícito do formulário 'Cadastrar produto' do
    painel — não cai para Open Food Facts/Zaffari aqui; essa busca multi-fonte continua
    acontecendo automaticamente em GET /produto/<codbar> e no botão de cadastro que aparece
    quando a busca do catálogo não encontra nada localmente."""
    produto_existente = Produto.query.filter_by(codbar=codbar).first()
    if produto_existente:
        return jsonify({
            'message': f'Produto já cadastrado no catálogo: {produto_existente.description}',
            'ja_existia': True,
            'codbar': produto_existente.codbar,
            'description': produto_existente.description,
        }), 200

    dados = fetch_product_from_cosmos(codbar)
    if not dados:
        return jsonify({'message': 'Produto não encontrado no Cosmos para esse EAN (ou todos os tokens configurados estão sem cota/inválidos).'}), 404

    novo_produto = register_product_in_database(dados)
    if not novo_produto:
        return jsonify({'message': 'O Cosmos retornou dados para esse EAN, mas houve um erro ao salvar o produto no banco.'}), 500

    thumbnail = dados.get('thumbnail')
    if thumbnail and isinstance(thumbnail, str) and thumbnail.startswith('http') and not find_existing_image(codbar, PROCESSED_IMAGES_FOLDER, ALLOWED_EXTENSIONS):
        try:
            img_response = requests.get(thumbnail, timeout=15)
            img_response.raise_for_status()
            save_image_from_response(img_response.content, codbar, origem='cosmos')
        except requests.RequestException as e:
            logging.warning(f"Não foi possível salvar a imagem do Cosmos para {codbar}: {e}")

    return jsonify({
        'message': f'Produto cadastrado com sucesso via Cosmos: {novo_produto.description}',
        'ja_existia': False,
        'codbar': novo_produto.codbar,
        'description': novo_produto.description,
        'marca': novo_produto.marca,
    }), 201


@app.route('/admin/cadastrar-produto-manual/<string:codbar>', methods=['POST'])
@jwt_required()
def admin_cadastrar_produto_manual(codbar):
    """Cadastra um produto no catálogo local só com o EAN, sem consultar NENHUMA fonte externa
    (Cosmos/Open Food Facts/Zaffari/Google/PreçoMelhor). Pensado pro caso em que as buscas
    automáticas já tentaram e nenhuma fonte tem esse produto (ex.: item novo/exclusivo da loja,
    sem GTIN público cadastrado em lugar nenhum).

    `description` (form-data, opcional) e um arquivo de imagem (form-data, campo `file`,
    opcional) podem vir junto nessa mesma requisição — pedido do usuário pra não precisar de um
    passo separado depois. Sem eles, cai no comportamento antigo (produto só com o EAN; hoje o
    painel não tem tela de edição de nome/marca, só o upload de imagem no painel de detalhes
    pra completar depois)."""
    codbar = (codbar or '').strip()
    if not re.fullmatch(r'\d{8,14}', codbar):
        return jsonify({'message': 'EAN inválido (precisa ter só números, 8 a 14 dígitos)'}), 400

    description = (request.form.get('description') or '').strip() or None

    produto_existente = Produto.query.filter_by(codbar=codbar).first()
    if produto_existente:
        return jsonify({
            'message': f'Produto já cadastrado no catálogo: {produto_existente.description or "(sem descrição ainda)"}',
            'ja_existia': True,
            'codbar': produto_existente.codbar,
            'description': produto_existente.description,
        }), 200

    novo_produto = Produto(codbar=codbar, description=description)
    _marcar_tem_foto(novo_produto, False)
    db.session.add(novo_produto)
    db.session.commit()

    imagem_salva = False
    imagem_quarentenada = False
    file = request.files.get('file')
    if file and file.filename and allowed_file(file.filename):
        dados = file.read()
        if not _imagem_e_segura(dados):
            _quarentenar_imagem(dados, codbar, origem='upload_manual')
            imagem_quarentenada = True
        else:
            # Sem cópia crua (pedido do usuário) — processa e salva só em PROCESSED_IMAGES_FOLDER.
            processed_file_path = os.path.join(PROCESSED_IMAGES_FOLDER, f'{codbar}.png')
            input_image = Image.open(io.BytesIO(dados))
            save_image_with_background_removal(input_image, processed_file_path)
            _marcar_tem_foto(novo_produto, True)
            db.session.commit()
            imagem_salva = True

    partes_msg = [f'Produto {codbar} cadastrado manualmente']
    partes_msg.append('com descrição' if description else 'sem descrição')
    if imagem_quarentenada:
        partes_msg.append('- imagem enviada foi sinalizada pelo filtro de conteúdo e ficou em quarentena para revisão humana (não foi salva como foto do produto)')
    else:
        partes_msg.append('e com imagem' if imagem_salva else 'sem imagem')
    partes_msg.append('(nenhuma fonte externa foi consultada).')

    return jsonify({
        'message': ' '.join(partes_msg),
        'ja_existia': False,
        'codbar': novo_produto.codbar,
        'description': novo_produto.description,
        'marca': novo_produto.marca,
        'imagem_salva': imagem_salva,
        'imagem_quarentenada': imagem_quarentenada,
    }), 201


@app.route('/admin/buscar-imagem/<string:codbar>', methods=['POST'])
@jwt_required()
def admin_buscar_imagem(codbar):
    """Busca a imagem do produto: local -> Google -> Zaffari -> PrecoMelhor -> Rissul -> Sonda ->
    Unidasul -> Serper, salvando o resultado (já processado, sem fundo) em PROCESSED_IMAGES_FOLDER.

    Cria um `Produto` mínimo (só codbar) se o EAN ainda não tiver cadastro, em vez de 404 —
    mesmo padrão já usado em `deletar_imagem_produto`/`_quarentenar_imagem`. Necessário pra ação
    "Buscar" funcionar também na página "Imagens Pendentes" (que lista EAN sem cadastro também,
    ver `_registrar_busca_imagem`/`admin_historico_buscas`)."""
    produto = Produto.query.filter_by(codbar=codbar).first()
    if not produto:
        produto = Produto(codbar=codbar)
        db.session.add(produto)
        db.session.commit()

    img_path = find_existing_image(codbar, PROCESSED_IMAGES_FOLDER, ALLOWED_EXTENSIONS)
    if img_path:
        img_url = url_for('static', filename=f'processed_images/{os.path.basename(img_path)}', _external=True)
        return jsonify({'message': 'Produto já possui foto', 'imagem_url': img_url, 'fonte': 'local'}), 200

    if not _busca_imagem_online_ativa():
        _registrar_busca_imagem(codbar, False, via='admin')
        return jsonify({'message': 'Busca de imagem online está desativada em Configurações → Flags de Funcionamento'}), 404

    if produto.busca_imagem_bloqueada:
        _registrar_busca_imagem(codbar, False, via='admin')
        return jsonify({'message': 'Busca automática de imagem bloqueada permanentemente para este EAN (imagem imprópria já foi encontrada aqui antes)'}), 404

    cfg_fontes = _ler_todas_config()
    for fonte, buscar in (
        ('google', buscar_e_salvar_imagem_google),
        ('zaffari', buscar_e_salvar_imagem_zaffari),
        ('precomelhor', buscar_e_salvar_imagem_precomelhor),
        ('rissul', buscar_e_salvar_imagem_rissul),
        ('sonda', buscar_e_salvar_imagem_sonda),
        ('unidasul', buscar_e_salvar_imagem_unidasul),
        ('serper', buscar_e_salvar_imagem_serper),
    ):
        if not _fonte_imagem_ativa(fonte, cfg_fontes):
            continue
        resultado = buscar(codbar)
        if resultado[1] == 200:
            imagem_url = resultado[0].get_json().get('imagem_url')
            _registrar_busca_imagem(codbar, True, origem=fonte, via='admin')
            return jsonify({'message': f'Imagem encontrada via {fonte}', 'imagem_url': imagem_url, 'fonte': fonte}), 200

    _registrar_busca_imagem(codbar, False, via='admin')
    return jsonify({'message': 'Nenhuma imagem encontrada em nenhuma das fontes (Google, PrecoMelhor, Sonda, Unidasul, Serper)'}), 404


@app.route('/admin/buscar-imagem-ia/<string:codbar>', methods=['POST'])
@jwt_required()
def admin_buscar_imagem_ia(codbar):
    """Usa o Gemini (busca na internet via grounding do Google, ver _buscar_imagens_gemini_web)
    pra sugerir candidatas a foto do produto. Não salva nada sozinha — só devolve as opções pro
    painel mostrar numa lista; o usuário escolhe uma (ver admin_definir_imagem_url) ou descarta."""
    produto = Produto.query.filter_by(codbar=codbar).first()
    if not produto:
        return jsonify({'message': 'Produto não encontrado'}), 404

    candidatos, erro = _buscar_imagens_gemini_web(produto.description, produto.marca, codbar)
    if erro:
        return jsonify({'message': erro}), 502
    return jsonify({'opcoes': candidatos}), 200


@app.route('/admin/definir-imagem-url/<string:codbar>', methods=['POST'])
@jwt_required()
def admin_definir_imagem_url(codbar):
    """Baixa uma imagem a partir de uma URL (ex.: escolhida no picker da busca com IA) e salva
    como a foto do produto — mesmo caminho de sempre (save_image_from_response), então também
    passa pela remoção de fundo já existente pras outras fontes."""
    produto = Produto.query.filter_by(codbar=codbar).first()
    if not produto:
        return jsonify({'message': 'Produto não encontrado'}), 404

    data = request.get_json(silent=True) or {}
    image_url = (data.get('url') or '').strip()
    if not image_url.startswith('http'):
        return jsonify({'message': 'URL de imagem inválida'}), 400

    try:
        img_response = requests.get(image_url, timeout=20, headers={'User-Agent': 'Mozilla/5.0'})
        img_response.raise_for_status()
        content_type = img_response.headers.get('Content-Type', '')
        if 'image' not in content_type.lower():
            return jsonify({'message': 'O link escolhido não retornou uma imagem válida'}), 400
    except requests.RequestException as e:
        logging.warning(f"Erro ao baixar imagem escolhida na busca com IA para {codbar}: {e}")
        return jsonify({'message': 'Não foi possível baixar essa imagem'}), 400

    resultado = save_image_from_response(img_response.content, codbar, origem='ia')
    if resultado[1] == 200:
        _registrar_busca_imagem(codbar, True, origem='ia', via='admin')
    return resultado


@app.route('/admin/gerar-arte/<string:codbar>', methods=['POST'])
@jwt_required()
def admin_gerar_arte(codbar):
    """Gera (ou regenera) a arte publicitária de um produto a partir da sua foto (processada,
    sem fundo — única cópia que existe). A geração em si acontece na fila única (ver
    _enfileirar_geracao_arte) — essa rota aguarda o próprio job terminar antes de responder,
    então o contrato não muda (ainda retorna a URL pronta), só passa a esperar a vez se houver
    outras gerações em andamento na fila."""
    produto = Produto.query.filter_by(codbar=codbar).first()
    if not produto:
        return jsonify({'message': 'Produto não encontrado'}), 404

    img_path = find_existing_image(codbar, PROCESSED_IMAGES_FOLDER, ALLOWED_EXTENSIONS)
    if not img_path:
        return jsonify({'message': 'Produto não possui foto cadastrada para servir de base'}), 400

    if codbar in _gerando_arte_em_andamento:
        return jsonify({'message': 'Já existe uma geração em andamento para este produto'}), 409

    job = _enfileirar_geracao_arte(codbar, img_path, aguardar=True, forcar=True)
    if job is None:
        return jsonify({'message': 'Não foi possível enfileirar a geração (verifique se a chave do Gemini está configurada)'}), 400

    job.evento.wait()
    if job.erro:
        return jsonify({'message': f'Erro ao gerar arte: {job.erro}'}), 500

    return jsonify({'message': 'Arte gerada com sucesso', 'arte_url': _arte_url(codbar)}), 200


_gerando_arte_em_andamento = set()


@app.route('/produto-imagem/<string:codbar>/gerar-arte', methods=['POST'])
@swag_from({
    'tags': ['Produtos'],
    'parameters': [
        {
            'name': 'codbar',
            'in': 'path',
            'type': 'string',
            'required': True
        }
    ],
    'consumes': ['image/jpeg', 'image/png', 'image/webp'],
    'responses': {
        '200': {
            'description': 'URL da arte publicitária (já existente ou recém-gerada)'
        },
        '404': {
            'description': 'Produto não encontrado em nenhuma fonte'
        }
    }
})
def gerar_arte_publica(codbar):
    """Gera a arte publicitária a partir de uma foto crua enviada no corpo da requisição
    (usado pelo app de consulta de preço quando a foto vem de uma fonte externa própria da
    integração, ex.: API do Komprão, que o srv-mupa ainda não conhece). Idempotente: se a
    arte já existe, apenas retorna a URL existente sem gerar de novo.

    Mesmo query param `orientacao` de obter_imagem_produto (ver lá) — o app pede a orientação
    que precisa pro terminal físico dele."""
    orientacao = 'vertical' if request.args.get('orientacao') == 'vertical' else 'horizontal'
    arte_existente = _arte_url(codbar, orientacao)
    if arte_existente:
        return jsonify({'imagem_url_arte': arte_existente}), 200

    image_data = request.get_data()
    if not image_data:
        return jsonify({'message': 'Corpo da requisição vazio (esperada a foto crua do produto)'}), 400

    # Evita duas gerações concorrentes do mesmo EAN+orientação (ex.: dois terminais consultando
    # o mesmo produto ao mesmo tempo). A geração em si acontece na fila única (ver
    # _enfileirar_geracao_arte) — essa rota aguarda o próprio job terminar antes de responder,
    # então o contrato não muda (ainda retorna a URL pronta no corpo da resposta).
    if _chave_andamento(codbar, orientacao) in _gerando_arte_em_andamento:
        return jsonify({'message': 'Geração de arte já em andamento para este produto'}), 409

    try:
        produto = Produto.query.filter_by(codbar=codbar).first()
        if not produto:
            for fonte, buscar in (
                ('cosmos', fetch_product_from_cosmos),
                ('open_food_facts', fetch_product_from_openfoodfacts),
                ('zaffari', fetch_product_from_zaffari),
                ('precomelhor', fetch_product_from_precomelhor),
                ('rissul', fetch_product_from_rissul),
            ):
                dados = buscar(codbar)
                if dados:
                    produto = register_product_in_database(dados)
                    if produto:
                        break
            if not produto:
                return jsonify({'message': 'Produto não encontrado em nenhuma fonte (Cosmos, Open Food Facts, PreçoMelhor)'}), 404

        # Idempotente: só salva a foto enviada nesta requisição se AINDA não tivermos uma —
        # se o produto já tem imagem, usa a que já existe em vez de sobrescrever. A gravação em
        # si passa por save_image_from_response (mesmo chokepoint de todas as outras fontes) —
        # antes essa rota escrevia os bytes crus direto em disco, sem passar pelo filtro de
        # conteúdo impróprio nem pela remoção de fundo; um gap real, corrigido de passagem ao
        # consolidar tudo pra salvar só em PROCESSED_IMAGES_FOLDER (nunca mais duplicar
        # crua+processada em disco).
        img_path = find_existing_image(codbar, PROCESSED_IMAGES_FOLDER, ALLOWED_EXTENSIONS)
        if not img_path:
            resultado_save = save_image_from_response(image_data, codbar, origem='app_gerar_arte')
            if resultado_save[1] != 200:
                return resultado_save
            img_path = find_existing_image(codbar, PROCESSED_IMAGES_FOLDER, ALLOWED_EXTENSIONS)

        job = _enfileirar_geracao_arte(codbar, img_path, aguardar=True, orientacao=orientacao)
        if job is not None:
            job.evento.wait()
            if job.erro:
                return jsonify({'message': f'Erro ao gerar arte: {job.erro}'}), 500
    except Exception as e:
        logging.error(f"Erro ao gerar arte publicitária (via upload) para {codbar}: {e}")
        return jsonify({'message': f'Erro ao gerar arte: {e}'}), 500

    return jsonify({'imagem_url_arte': _arte_url(codbar, orientacao)}), 200


@app.route('/admin/estatisticas', methods=['GET'])
@jwt_required()
def admin_estatisticas():
    """Dashboard com estatísticas do catálogo."""
    conn = sqlite3.connect(DB_PATH)
    try:
        total = conn.execute("SELECT COUNT(*) FROM produto").fetchone()[0]
        with_photo = len([
            f for f in os.listdir(PROCESSED_IMAGES_FOLDER)
            if os.path.splitext(f)[1].lstrip('.').lower() in ALLOWED_EXTENSIONS
        ])
        brands = conn.execute(
            "SELECT marca, COUNT(*) as cnt FROM produto WHERE marca IS NOT NULL AND marca != '' GROUP BY marca ORDER BY cnt DESC LIMIT 10"
        ).fetchall()
        categories = conn.execute(
            "SELECT categoriaText, COUNT(*) as cnt FROM produto WHERE categoriaText IS NOT NULL AND categoriaText != '' GROUP BY categoriaText ORDER BY cnt DESC LIMIT 10"
        ).fetchall()
        return jsonify({
            'total_produtos': total,
            'com_foto': with_photo,
            'sem_foto': total - with_photo,
            'top_marcas': [{'marca': b[0], 'count': b[1]} for b in brands],
            'top_categorias': [{'categoria': c[0], 'count': c[1]} for c in categories],
        })
    finally:
        conn.close()


@app.route('/admin/produtos-com-foto', methods=['GET'])
@jwt_required()
def admin_produtos_com_foto():
    """Lista produtos que possuem foto salva localmente (paginação).

    A existência de foto é determinada pelos arquivos em PROCESSED_IMAGES_FOLDER (única cópia
    que existe — nunca duplicamos crua+processada em disco), não pela coluna foto_png do banco
    (que fica vazia na importação em massa do CSV).
    """
    page = request.args.get('page', 1, type=int)
    per_page = request.args.get('per_page', 15, type=int)
    search = request.args.get('search', '').strip()

    arquivo_por_codbar = {}
    for filename in os.listdir(PROCESSED_IMAGES_FOLDER):
        codbar, ext = os.path.splitext(filename)
        if ext.lstrip('.').lower() in ALLOWED_EXTENSIONS:
            arquivo_por_codbar[codbar] = filename

    if not arquivo_por_codbar:
        return jsonify({'produtos': [], 'total': 0, 'page': page, 'per_page': per_page, 'pages': 1})

    conn = sqlite3.connect(DB_PATH)
    try:
        placeholders = ','.join('?' for _ in arquivo_por_codbar)
        query = f"""
            SELECT codbar, description, marca, categoriaText, preco_medio
            FROM produto
            WHERE codbar IN ({placeholders})
        """
        params = list(arquivo_por_codbar.keys())

        if search:
            query += " AND (UPPER(description) LIKE ? OR UPPER(marca) LIKE ? OR UPPER(codbar) LIKE ?)"
            like_search = f"%{search.upper()}%"
            params += [like_search, like_search, like_search]

        all_rows = conn.execute(query + " ORDER BY description", params).fetchall()
    finally:
        conn.close()

    count = len(all_rows)
    offset = (page - 1) * per_page
    page_rows = all_rows[offset:offset + per_page]

    produtos = []
    for codbar, desc, marca, cat, preco in page_rows:
        produtos.append({
            'ean': codbar,
            'descricao': desc,
            'marca': marca,
            'categoria': cat,
            'foto_png': url_for('static', filename=f'processed_images/{arquivo_por_codbar[codbar]}', _external=True),
            'preco_medio': preco,
            'arte_url': _arte_url(codbar),
        })

    return jsonify({
        'produtos': produtos,
        'total': count,
        'page': page,
        'per_page': per_page,
        'pages': (count + per_page - 1) // per_page if count > 0 else 1,
    })


def _listar_imagens_orfas(search=''):
    """Lista arquivos já salvos (em `PROCESSED_IMAGES_FOLDER`, a única cópia que existe daqui pra
    frente — e ainda em `IMAGES_FOLDER` também, por compatibilidade com arquivos crus que
    sobraram de antes dessa mudança) cujo EAN (nome do arquivo) NÃO tem `Produto` correspondente
    — pedido do usuário: essas imagens "órfãs" ficavam completamente invisíveis na Consulta
    Rápida, já que a rota sempre partiu de `SELECT ... FROM produto` (não tem como aparecer algo
    que não é uma linha dessa tabela). Existem de verdade — ex.: uma busca automática que criou
    um `Produto` mínimo só pra bloquear/registrar (ver `_quarentenar_imagem`/`deletar_imagem_produto`)
    e depois esse `Produto` foi apagado por algum motivo, ou um upload manual apontando um EAN
    que nunca virou cadastro completo.

    Varrer as PASTAS (não a tabela) é barato aqui porque o volume de arquivos é sempre muito
    menor que o catálogo inteiro (~945 mil produtos) — o oposto (escanear todo o catálogo
    procurando quem não tem arquivo) seria caro demais pra fazer a cada consulta, por isso nunca
    foi feito assim. `search`, quando preenchido, filtra pelo EAN em si (substring) — órfão não
    tem descrição/marca pra buscar por outros campos."""
    termo = search.upper() if search else None
    candidatos_processada = {}
    candidatos_crua = {}
    for pasta, destino in ((PROCESSED_IMAGES_FOLDER, candidatos_processada), (IMAGES_FOLDER, candidatos_crua)):
        if not os.path.isdir(pasta):
            continue
        try:
            arquivos = os.listdir(pasta)
        except OSError:
            continue
        for nome in arquivos:
            codbar, ext = os.path.splitext(nome)
            if ext.lstrip('.').lower() not in ALLOWED_EXTENSIONS:
                continue
            if termo and termo not in codbar.upper():
                continue
            destino[codbar] = nome

    todos_codbars = set(candidatos_processada.keys()) | set(candidatos_crua.keys())
    if not todos_codbars:
        return []

    cadastrados = {p.codbar for p in Produto.query.filter(Produto.codbar.in_(todos_codbars)).all()}
    orfaos = sorted(todos_codbars - cadastrados)
    return [{
        'ean': codbar,
        'descricao': None,
        'marca': None,
        'categoria': None,
        'preco_medio': None,
        # Processada primeiro (o normal daqui pra frente); crua só sobra de arquivos antigos.
        'foto_png': _static_url(
            os.path.join(PROCESSED_IMAGES_FOLDER, candidatos_processada[codbar]) if codbar in candidatos_processada
            else os.path.join(IMAGES_FOLDER, candidatos_crua[codbar])
        ),
        'arte_url': None,
        'orfao': True,
    } for codbar in orfaos]


@app.route('/admin/consulta-produtos', methods=['GET'])
@jwt_required()
def admin_consulta_produtos():
    """Lista/busca produtos em todo o catálogo (com ou sem foto), paginado. Usado pela aba
    unificada 'Consulta Rápida' do painel (busca + navegação + geração de arte)."""
    page = request.args.get('page', 1, type=int)
    per_page = request.args.get('per_page', 15, type=int)
    search = request.args.get('q', '').strip()

    # Produtos com foto sempre primeiro (pedido do usuário) — tem_foto é mantida em sincronia
    # com static/processed_images/ (única cópia que existe) por _marcar_tem_foto em todo ponto
    # que salva/remove a foto do produto (ver Produto.tem_foto). Na busca, o EAN exato
    # pesquisado continua tendo prioridade máxima
    # (achar o produto que a pessoa procurou é mais importante que a ordem geral), mas entre os
    # resultados o critério "com foto primeiro" também se aplica.
    if search:
        like_search = f"%{search.upper()}%"
        where_clause = " WHERE codbar = ? OR UPPER(description) LIKE ? OR UPPER(marca) LIKE ?"
        where_params = [search, like_search, like_search]
        order_clause = " ORDER BY (codbar = ?) DESC, tem_foto DESC, description"
        order_params = [search]
    else:
        where_clause = ""
        where_params = []
        order_clause = " ORDER BY tem_foto DESC, description"
        order_params = []

    # Imagens órfãs (arquivo sem Produto) só entram na página 1 — "tem imagem" as coloca junto
    # do grupo prioritário, mas não faz sentido replicar essa varredura em toda página seguinte
    # (o conjunto é sempre pequeno, cabe inteiro ali). Reduz o LIMIT da consulta SQL da página 1
    # na mesma proporção, pra página continuar com ~per_page itens no total, não per_page + N.
    orfaos = _listar_imagens_orfas(search) if page == 1 else []
    limit_sql = max(per_page - len(orfaos), 0) if page == 1 else per_page

    conn = sqlite3.connect(DB_PATH)
    try:
        total = conn.execute("SELECT COUNT(*) FROM produto" + where_clause, where_params).fetchone()[0]

        offset = 0 if page == 1 else (page - 1) * per_page - len(orfaos)
        offset = max(offset, 0)
        rows = conn.execute(
            "SELECT codbar, description, marca, categoriaText, preco_medio FROM produto"
            + where_clause + order_clause + " LIMIT ? OFFSET ?",
            where_params + order_params + [limit_sql, offset],
        ).fetchall()
    finally:
        conn.close()

    produtos = list(orfaos)
    for codbar, desc, marca, cat, preco in rows:
        # Mesma prioridade de obter_imagem_produto/serialize_produto_with_image: processada
        # (sem fundo) primeiro, crua só como fallback se a processada não existir.
        img_path = find_existing_image(codbar, IMAGES_FOLDER, ALLOWED_EXTENSIONS)
        processed_path = find_existing_image(codbar, PROCESSED_IMAGES_FOLDER, ALLOWED_EXTENSIONS)
        foto_url = _static_url(processed_path) if processed_path else (_static_url(img_path) if img_path else None)
        produtos.append({
            'ean': codbar,
            'descricao': desc,
            'marca': marca,
            'categoria': cat,
            'preco_medio': preco,
            'foto_png': foto_url,
            'arte_url': _arte_url(codbar),
        })

    return jsonify({
        'produtos': produtos,
        'total': total,
        'page': page,
        'per_page': per_page,
        'pages': (total + per_page - 1) // per_page if total > 0 else 1,
    })


@app.route('/admin/consulta-simples', methods=['GET'])
@jwt_required()
def admin_consulta_simples():
    """Consulta rápida por EAN, descrição ou marca."""
    q = request.args.get('q', '').strip()
    if not q:
        return jsonify({'error': 'Parâmetro "q" é obrigatório'}), 400

    conn = sqlite3.connect(DB_PATH)
    try:
        # Tenta por EAN exato primeiro
        row = conn.execute(
            "SELECT codbar, description, marca, foto_png, categoriaText, preco_medio FROM produto WHERE codbar = ?",
            (q,)
        ).fetchone()
        if row:
            codbar, desc, marca, foto, cat, preco = row
            img_path = find_existing_image(codbar, PROCESSED_IMAGES_FOLDER, ALLOWED_EXTENSIONS)
            resultado = {
                'ean': codbar,
                'descricao': desc,
                'marca': marca,
                'categoria': cat,
                'foto_png': url_for('static', filename=f'processed_images/{os.path.basename(img_path)}', _external=True) if img_path else 'No image available',
                'preco_medio': preco,
            }
            return jsonify({'results': [resultado], 'tipo_busca': 'ean_exact', 'count': 1})

        # Tenta por descrição (LIKE)
        like_q = f"%{q.upper()}%"
        rows = conn.execute(
            "SELECT codbar, description, marca, foto_png, categoriaText, preco_medio FROM produto WHERE UPPER(description) LIKE ? LIMIT 10",
            (like_q,)
        ).fetchall()
        if rows:
            resultados = []
            for r in rows:
                img_path = find_existing_image(r[0], PROCESSED_IMAGES_FOLDER, ALLOWED_EXTENSIONS)
                resultados.append({
                    'ean': r[0],
                    'descricao': r[1],
                    'marca': r[2],
                    'categoria': r[4],
                    'foto_png': url_for('static', filename=f'processed_images/{os.path.basename(img_path)}', _external=True) if img_path else 'No image available',
                    'preco_medio': r[5],
                })
            return jsonify({'results': resultados, 'tipo_busca': 'descricao_like', 'count': len(resultados)})

        # Tenta por marca
        rows = conn.execute(
            "SELECT codbar, description, marca, foto_png, categoriaText, preco_medio FROM produto WHERE UPPER(marca) LIKE ? LIMIT 10",
            (like_q,)
        ).fetchall()
        if rows:
            resultados = []
            for r in rows:
                img_path = find_existing_image(r[0], PROCESSED_IMAGES_FOLDER, ALLOWED_EXTENSIONS)
                resultados.append({
                    'ean': r[0],
                    'descricao': r[1],
                    'marca': r[2],
                    'categoria': r[4],
                    'foto_png': url_for('static', filename=f'processed_images/{os.path.basename(img_path)}', _external=True) if img_path else 'No image available',
                    'preco_medio': r[5],
                })
            return jsonify({'results': resultados, 'tipo_busca': 'marca_like', 'count': len(resultados)})

        return jsonify({'message': 'Nenhum produto encontrado', 'results': [], 'tipo_busca': 'none'}), 404
    finally:
        conn.close()


@app.route('/admin/historico-buscas', methods=['GET'])
@jwt_required()
def admin_historico_buscas():
    """Histórico de buscas de imagem. `status` filtra por 'encontrado', 'nao_encontrado' ou
    'todos' (padrão).

    'nao_encontrado' e 'sem_imagem' têm um formato DIFERENTE do log bruto: em vez de uma linha
    por tentativa, retornam **agrupado por EAN** — um produto consultado sem sucesso em 12
    lojas diferentes antes virava 12 linhas idênticas na lista, obrigando quem for resolver a
    escanear/pular duplicatas manualmente. Agrupado, cada produto aparece uma vez com o total
    de tentativas (`tentativas`), ordenado do mais tentado pro menos tentado — prioriza o
    produto com mais impacto (mais consultas perdidas) primeiro. 'encontrado'/'todos' continuam
    como log bruto (útil pra auditoria/histórico), sem essa agregação.

    Diferença entre os dois modos agrupados (mesma fonte de dados, `HistoricoBuscaImagem`, já
    que `_registrar_busca_imagem` não exige mais cadastro pra gravar — ver docstring de lá):
    - 'nao_encontrado' (aba Histórico): só produtos com `Produto` cadastrado — filtro aplicado
      AQUI, na consulta, pra manter o comportamento de sempre (evitar "poluir" essa lista com
      EAN que nem é produto nosso, ex. código escaneado errado no terminal).
    - 'sem_imagem' (página "Imagens Pendentes"): TODOS os EAN sem imagem, cadastrados ou não —
      pedido explícito do usuário ("não precisa ter cadastro do produto"), pra dar visibilidade
      de qualquer barcode que o terminal tentou e não achou foto nenhuma, mesmo os que ainda nem
      viraram produto no catálogo.

    Também filtra fora, na hora, qualquer EAN que já tenha uma foto crua salva agora (upload
    manual feito por fora do fluxo de busca não gera uma linha 'encontrado' no histórico, então
    sem esse filtro o produto continuaria aparecendo como pendente mesmo já resolvido)."""
    page = request.args.get('page', 1, type=int)
    per_page = request.args.get('per_page', 20, type=int)
    status = request.args.get('status', 'todos')
    codbar_filtro = request.args.get('codbar', '').strip()

    if status in ('nao_encontrado', 'sem_imagem'):
        grupo = db.session.query(
            HistoricoBuscaImagem.codbar,
            db.func.count(HistoricoBuscaImagem.id).label('tentativas'),
            db.func.max(HistoricoBuscaImagem.criado_em).label('ultima_tentativa'),
        ).filter(HistoricoBuscaImagem.encontrado == False)
        if status == 'nao_encontrado':
            grupo = grupo.filter(HistoricoBuscaImagem.codbar.in_(db.session.query(Produto.codbar)))
        if codbar_filtro:
            grupo = grupo.filter(HistoricoBuscaImagem.codbar.like(f'%{codbar_filtro}%'))
        grupo = grupo.group_by(HistoricoBuscaImagem.codbar).order_by(db.desc('tentativas'))

        # Tira quem já tem foto agora (resolvido por upload manual, sem passar pela busca).
        pendentes = [
            g for g in grupo.all()
            if not find_existing_image(g.codbar, PROCESSED_IMAGES_FOLDER, ALLOWED_EXTENSIONS)
        ]
        total = len(pendentes)
        pagina = pendentes[(page - 1) * per_page: (page - 1) * per_page + per_page]

        codbars = {g.codbar for g in pagina}
        descricoes = {}
        cadastrados = set()
        if codbars:
            for p in Produto.query.filter(Produto.codbar.in_(codbars)).all():
                descricoes[p.codbar] = p.description
                cadastrados.add(p.codbar)

        return jsonify({
            'agrupado': True,
            'registros': [{
                'codbar': g.codbar,
                'descricao': descricoes.get(g.codbar),
                'tentativas': g.tentativas,
                'ultima_tentativa': g.ultima_tentativa.isoformat() + 'Z',
                'cadastrado': g.codbar in cadastrados,
            } for g in pagina],
            'total': total,
            'page': page,
            'per_page': per_page,
            'pages': (total + per_page - 1) // per_page if total > 0 else 1,
        })

    query = HistoricoBuscaImagem.query
    if status == 'encontrado':
        query = query.filter_by(encontrado=True)
    if codbar_filtro:
        query = query.filter(HistoricoBuscaImagem.codbar.like(f'%{codbar_filtro}%'))

    query = query.order_by(HistoricoBuscaImagem.criado_em.desc())
    total = query.count()
    registros = query.offset((page - 1) * per_page).limit(per_page).all()

    codbars = {r.codbar for r in registros}
    descricoes = {}
    if codbars:
        for p in Produto.query.filter(Produto.codbar.in_(codbars)).all():
            descricoes[p.codbar] = p.description

    return jsonify({
        'agrupado': False,
        'registros': [{
            'id': r.id,
            'codbar': r.codbar,
            'descricao': descricoes.get(r.codbar),
            'encontrado': r.encontrado,
            'origem': r.origem,
            'via': r.via,
            'criado_em': r.criado_em.isoformat() + 'Z',
            'notificado': r.notificado_em is not None,
        } for r in registros],
        'total': total,
        'page': page,
        'per_page': per_page,
        'pages': (total + per_page - 1) // per_page if total > 0 else 1,
    })


@app.route('/admin/imagens-pendentes/zerar', methods=['POST'])
@jwt_required()
def zerar_imagens_pendentes():
    """Botão "Zerar registros" da página Imagens Pendentes — apaga TODAS as linhas de pendência
    (`HistoricoBuscaImagem` com `encontrado=False`), cadastradas ou não. Como as duas telas
    agrupadas (`status=nao_encontrado` do Histórico e `status=sem_imagem` das Imagens Pendentes)
    são views diferentes sobre a MESMA tabela (ver `admin_historico_buscas`), zerar aqui também
    esvazia a aba Histórico → Não encontrados — efeito colateral esperado de um "reset" completo
    da fila de pendências, não um bug. Não mexe em nada além dessa tabela: nenhum arquivo, nenhum
    `Produto`, nenhum bloqueio por EAN é tocado — só o histórico de tentativas registradas."""
    try:
        apagadas = HistoricoBuscaImagem.query.filter_by(encontrado=False).delete()
        db.session.commit()
        return jsonify({'message': f'{apagadas} registro(s) de pendência apagado(s)', 'apagadas': apagadas}), 200
    except Exception as e:
        db.session.rollback()
        logging.error(f"Erro ao zerar registros de imagens pendentes: {e}")
        return jsonify({'message': 'Erro ao zerar registros'}), 500


@app.route('/admin/quarentena', methods=['GET'])
@jwt_required()
def admin_quarentena():
    """Lista as imagens em quarentena (rejeitadas por `_imagem_e_segura`, ver docstring de
    `ImagemQuarentena`) pra revisão humana. `status` filtra por 'pendente' (padrão — ainda sem
    decisão), 'confirmada', 'liberada' ou 'todos'."""
    page = request.args.get('page', 1, type=int)
    per_page = request.args.get('per_page', 20, type=int)
    status = request.args.get('status', 'pendente')

    query = ImagemQuarentena.query
    if status == 'pendente':
        query = query.filter(ImagemQuarentena.decisao.is_(None))
    elif status in ('confirmada', 'liberada'):
        query = query.filter_by(decisao=status)

    query = query.order_by(ImagemQuarentena.criado_em.desc())
    total = query.count()
    registros = query.offset((page - 1) * per_page).limit(per_page).all()

    codbars = {r.codbar for r in registros}
    descricoes = {}
    if codbars:
        for p in Produto.query.filter(Produto.codbar.in_(codbars)).all():
            descricoes[p.codbar] = p.description

    return jsonify({
        'registros': [{
            'id': r.id,
            'codbar': r.codbar,
            'descricao': descricoes.get(r.codbar),
            'origem': r.origem,
            'criado_em': r.criado_em.isoformat() + 'Z',
            'decisao': r.decisao,
            'revisado_em': r.revisado_em.isoformat() + 'Z' if r.revisado_em else None,
            'tem_arquivo': r.arquivo is not None,
        } for r in registros],
        'total': total,
        'page': page,
        'per_page': per_page,
        'pages': (total + per_page - 1) // per_page if total > 0 else 1,
    })


@app.route('/admin/quarentena-imagem/<int:quarentena_id>', methods=['GET'])
@jwt_required()
def admin_quarentena_imagem(quarentena_id):
    """Serve o ARQUIVO de uma entrada em quarentena — rota autenticada de propósito
    (`QUARENTENA_FOLDER` fica fora de `static/`, nunca acessível sem passar por aqui e sem JWT
    válido). Só existe enquanto a entrada estiver pendente ou tiver sido 'confirmada' recém — o
    arquivo físico é apagado assim que uma decisão é tomada (ver rotas de confirmar/liberar),
    então uma entrada antiga sem `arquivo` retorna 404."""
    entrada = ImagemQuarentena.query.get_or_404(quarentena_id)
    if not entrada.arquivo:
        return jsonify({'message': 'Arquivo não existe mais (já revisado)'}), 404
    caminho = os.path.join(QUARENTENA_FOLDER, entrada.arquivo)
    if not os.path.exists(caminho):
        return jsonify({'message': 'Arquivo não encontrado no disco'}), 404
    # Lê pra memória em vez de `send_file(caminho, ...)` direto — no Windows, send_file num
    # caminho de arquivo pode deixar um handle aberto por um instante além do fim da resposta,
    # e como essa mesma imagem costuma ser apagada logo em seguida (rota de confirmar/liberar,
    # chamada pelo usuário assim que termina de revisar), isso causava um WinError 32 real
    # ("arquivo já está sendo usado por outro processo") pego testando o fluxo completo ao vivo.
    # Servindo de um BytesIO, o handle do arquivo em disco é fechado assim que a leitura termina.
    with open(caminho, 'rb') as f:
        dados = f.read()
    return send_file(io.BytesIO(dados), mimetype='image/jpeg')


@app.route('/admin/quarentena/<int:quarentena_id>/confirmar', methods=['POST'])
@jwt_required()
def admin_quarentena_confirmar(quarentena_id):
    """Humano revisou e confirma que a imagem é mesmo imprópria: apaga o arquivo físico da
    quarentena (não faz sentido guardar o conteúdo indefinidamente só pelo registro existir) e
    marca a decisão. O bloqueio permanente do EAN (`Produto.busca_imagem_bloqueada`) já foi
    aplicado no momento da rejeição (ver `_quarentenar_imagem`) — continua valendo, essa rota só
    fecha o ciclo de revisão."""
    entrada = ImagemQuarentena.query.get_or_404(quarentena_id)
    if entrada.arquivo:
        caminho = os.path.join(QUARENTENA_FOLDER, entrada.arquivo)
        if os.path.exists(caminho):
            os.remove(caminho)
        entrada.arquivo = None
    entrada.decisao = 'confirmada'
    entrada.revisado_em = datetime.utcnow()
    db.session.commit()
    return jsonify({'message': 'Confirmado como imagem imprópria — arquivo removido, EAN continua bloqueado.'}), 200


@app.route('/admin/quarentena/<int:quarentena_id>/liberar', methods=['POST'])
@jwt_required()
def admin_quarentena_liberar(quarentena_id):
    """Humano revisou e considera FALSO POSITIVO do classificador: reprocessa o arquivo
    (remoção de fundo, igual `save_image_from_response`) e salva SÓ em `PROCESSED_IMAGES_FOLDER`
    (nunca uma cópia crua — mesma regra do resto do sistema), desbloqueia o EAN e marca
    `tem_foto=True`. Não passa pelo classificador de novo — a decisão humana aqui é definitiva,
    não faz sentido rejeitar de novo automaticamente algo que um humano acabou de revisar e
    aprovar."""
    entrada = ImagemQuarentena.query.get_or_404(quarentena_id)
    if not entrada.arquivo:
        return jsonify({'message': 'Arquivo não existe mais — não é possível liberar (já revisado antes ou arquivo perdido).'}), 400

    caminho_quarentena = os.path.join(QUARENTENA_FOLDER, entrada.arquivo)
    if not os.path.exists(caminho_quarentena):
        return jsonify({'message': 'Arquivo não encontrado no disco'}), 404

    try:
        processed_file_path = os.path.join(PROCESSED_IMAGES_FOLDER, f'{entrada.codbar}.png')
        with open(caminho_quarentena, 'rb') as f:
            image_data = f.read()
        input_image = Image.open(io.BytesIO(image_data))
        save_image_with_background_removal(input_image, processed_file_path)

        os.remove(caminho_quarentena)
        entrada.arquivo = None
        entrada.decisao = 'liberada'
        entrada.revisado_em = datetime.utcnow()

        produto = Produto.query.filter_by(codbar=entrada.codbar).first()
        if produto:
            produto.busca_imagem_bloqueada = False
        _marcar_tem_foto(entrada.codbar, True)

        db.session.commit()
        return jsonify({'message': 'Liberado como falso positivo — imagem restaurada como foto do produto, EAN desbloqueado.'}), 200
    except Exception as e:
        db.session.rollback()
        logging.error(f"Erro ao liberar imagem em quarentena {quarentena_id}: {e}")
        return jsonify({'message': f'Erro ao liberar: {e}'}), 500


@app.route('/admin/status-sistema', methods=['GET'])
@jwt_required()
def admin_status_sistema():
    """Status operacional em tempo real: fila de geração de arte (o quanto está pendente e
    quais EANs estão em processamento agora) e consumo do Gemini/Cosmos desde o último resumo
    diário enviado — os mesmos contadores usados no e-mail, mas consultáveis aqui sem esperar
    o horário agendado."""
    return jsonify({
        'fila_arte': {
            'pendentes': _fila_arte.qsize(),
            'em_processamento': sorted(_gerando_arte_em_andamento),
        },
        'gemini': _status_gemini(),
        'cosmos': _status_tokens_cosmos(),
    })


# =====================================================================
# Notificações de imagens não encontradas (resumo por e-mail / WhatsApp)
# =====================================================================

NOTIFICACAO_CONFIG_KEYS = [
    'RESEND_API_KEY', 'RESEND_REMETENTE', 'RESUMO_DESTINATARIOS', 'RESUMO_HORARIO', 'RESUMO_ATIVO',
    'WHATSAPP_ATIVO', 'WHATSAPP_BASE_URL', 'WHATSAPP_INSTANCE',
    'WHATSAPP_TOKEN', 'WHATSAPP_NUMERO_DESTINO', 'STATUS_HORARIO_ATIVO',
]


def _estatisticas_gerais():
    """Estatísticas rápidas do catálogo, no mesmo estilo (leve) de /admin/estatisticas — conta
    arquivos nas pastas em vez de checar produto por produto (o catálogo tem ~945 mil linhas,
    então checar arquivo por arquivo pra cada uma seria caro demais pra rodar num resumo diário)."""
    total = Produto.query.count()
    com_foto = len([f for f in os.listdir(PROCESSED_IMAGES_FOLDER) if os.path.splitext(f)[1].lstrip('.').lower() in ALLOWED_EXTENSIONS])
    com_arte = len([f for f in os.listdir(ARTES_FOLDER) if f.lower().endswith('.webp')])
    return {'total_produtos': total, 'com_foto': com_foto, 'sem_foto': total - com_foto, 'com_arte': com_arte}


def _status_tokens_cosmos():
    """Estado atual da rotação de tokens do Cosmos + contadores acumulados desde o último
    resumo enviado (zerados em enviar_resumo_diario_sistema após um envio bem-sucedido)."""
    tokens = _lista_tokens_cosmos()
    cfg = _ler_todas_config()
    try:
        indice_atual = int(cfg.get('COSMOS_TOKEN_INDEX_ATUAL', '0') or '0') % max(len(tokens), 1)
    except ValueError:
        indice_atual = 0
    return {
        'total_tokens': len(tokens),
        'token_atual': (indice_atual + 1) if tokens else 0,
        'sucessos': int(cfg.get('STATS_COSMOS_SUCESSOS', '0') or '0'),
        'rotacoes': int(cfg.get('STATS_COSMOS_ROTACOES', '0') or '0'),
    }


def _enviar_email_resumo_diario(cfg, pendentes, stats, cosmos_status, cadastros_automaticos, gemini_status):
    """Envia o resumo diário completo por e-mail via Resend (API HTTP, sem SMTP). Levanta
    exceção em caso de falha (o chamador decide como registrar/logar)."""
    api_key = cfg.get('RESEND_API_KEY', '').strip()
    destinatarios = [d.strip() for d in cfg.get('RESUMO_DESTINATARIOS', '').split(',') if d.strip()]
    remetente = cfg.get('RESEND_REMETENTE', '').strip() or 'Mupa Brain <onboarding@resend.dev>'
    if not api_key or not destinatarios:
        raise ValueError('Resend não configurado (API key ou destinatários ausentes)')

    linhas_texto_pendentes = [
        f"- {p['codbar']} | {p['descricao'] or '(produto não cadastrado)'} | {p['ocorrencias']}x | última tentativa: {p['ultima_ocorrencia']}"
        for p in pendentes
    ] or ['(nenhuma pendência)']
    linhas_html_pendentes = "".join(
        f"<tr><td style='padding:4px 10px;'>{p['codbar']}</td>"
        f"<td style='padding:4px 10px;'>{p['descricao'] or '(produto não cadastrado)'}</td>"
        f"<td style='padding:4px 10px; text-align:center;'>{p['ocorrencias']}</td>"
        f"<td style='padding:4px 10px;'>{p['ultima_ocorrencia']}</td></tr>"
        for p in pendentes
    ) or "<tr><td colspan='4' style='padding:8px 10px; color:#666;'>Nenhuma pendência</td></tr>"

    assunto = f"Mupa Brain - Resumo diário do sistema ({len(pendentes)} imagem(ns) pendente(s))"
    corpo_texto = (
        "Resumo diário do sistema Mupa Brain\n\n"
        f"Catálogo: {stats['total_produtos']} produtos | {stats['com_foto']} com foto | {stats['sem_foto']} sem foto | {stats['com_arte']} com arte publicitária\n"
        f"Cadastros automáticos desde o último resumo: {cadastros_automaticos}\n"
        f"Cosmos: token #{cosmos_status['token_atual']} de {cosmos_status['total_tokens']} em uso | "
        f"{cosmos_status['sucessos']} sucesso(s) e {cosmos_status['rotacoes']} rotação(ões) por cota esgotada desde o último resumo\n"
        f"Gemini (imagem): {gemini_status['imagem_sucessos']} gerada(s) | {gemini_status['imagem_rate_limit']} bloqueada(s) por limite de taxa | {gemini_status['imagem_erros']} erro(s) outro\n"
        f"Gemini (texto/headline): {gemini_status['texto_sucessos']} gerado(s) | {gemini_status['texto_rate_limit']} bloqueado(s) por limite de taxa | {gemini_status['texto_erros']} erro(s) outro\n\n"
        "Imagens de produto não encontradas (ainda não notificadas):\n" + "\n".join(linhas_texto_pendentes)
    )
    corpo_html = f"""
    <h2 style="font-family:sans-serif;">Resumo diário do sistema — Mupa Brain</h2>
    <table style="border-collapse:collapse; font-family:sans-serif; font-size:13px; margin-bottom:16px;">
        <tr><td style="padding:4px 10px; color:#666;">Produtos no catálogo</td><td style="padding:4px 10px; font-weight:bold;">{stats['total_produtos']}</td></tr>
        <tr><td style="padding:4px 10px; color:#666;">Com foto</td><td style="padding:4px 10px;">{stats['com_foto']}</td></tr>
        <tr><td style="padding:4px 10px; color:#666;">Sem foto</td><td style="padding:4px 10px;">{stats['sem_foto']}</td></tr>
        <tr><td style="padding:4px 10px; color:#666;">Com arte publicitária</td><td style="padding:4px 10px;">{stats['com_arte']}</td></tr>
        <tr><td style="padding:4px 10px; color:#666;">Cadastros automáticos (desde o último resumo)</td><td style="padding:4px 10px;">{cadastros_automaticos}</td></tr>
        <tr><td style="padding:4px 10px; color:#666;">Cosmos — token em uso</td><td style="padding:4px 10px;">#{cosmos_status['token_atual']} de {cosmos_status['total_tokens']}</td></tr>
        <tr><td style="padding:4px 10px; color:#666;">Cosmos — sucessos / rotações por cota (desde o último resumo)</td><td style="padding:4px 10px;">{cosmos_status['sucessos']} / {cosmos_status['rotacoes']}</td></tr>
        <tr><td style="padding:4px 10px; color:#666;">Gemini imagem — geradas / limite de taxa / outro erro</td><td style="padding:4px 10px;">{gemini_status['imagem_sucessos']} / {gemini_status['imagem_rate_limit']} / {gemini_status['imagem_erros']}</td></tr>
        <tr><td style="padding:4px 10px; color:#666;">Gemini texto — gerados / limite de taxa / outro erro</td><td style="padding:4px 10px;">{gemini_status['texto_sucessos']} / {gemini_status['texto_rate_limit']} / {gemini_status['texto_erros']}</td></tr>
    </table>
    <h3 style="font-family:sans-serif;">Imagens de produto não encontradas ({len(pendentes)})</h3>
    <table style="border-collapse:collapse; font-family:sans-serif; font-size:13px;">
        <tr style="background:#f1f1f1;"><th style="padding:4px 10px; text-align:left;">EAN</th>
        <th style="padding:4px 10px; text-align:left;">Descrição</th>
        <th style="padding:4px 10px;">Ocorrências</th>
        <th style="padding:4px 10px; text-align:left;">Última tentativa (UTC)</th></tr>
        {linhas_html_pendentes}
    </table>
    """

    resposta = requests.post(
        'https://api.resend.com/emails',
        headers={'Authorization': f'Bearer {api_key}', 'Content-Type': 'application/json'},
        json={
            'from': remetente,
            'to': destinatarios,
            'subject': assunto,
            'html': corpo_html,
            'text': corpo_texto,
        },
        timeout=20,
    )
    if resposta.status_code >= 400:
        raise ValueError(f"Resend retornou HTTP {resposta.status_code}: {resposta.text[:300]}")


def _enviar_whatsapp_resumo_diario(cfg, pendentes, stats, cosmos_status, cadastros_automaticos, gemini_status):
    """Envia um resumo diário curto por WhatsApp via Evolution API (self-hosted) — mesma
    instância criada/gerenciada na aba WhatsApp do painel. Levanta exceção em caso de falha."""
    base_url = cfg.get('WHATSAPP_BASE_URL', '').strip().rstrip('/')
    instancia = cfg.get('WHATSAPP_INSTANCE', '').strip()
    apikey = cfg.get('WHATSAPP_TOKEN', '').strip()
    numero = cfg.get('WHATSAPP_NUMERO_DESTINO', '').strip()
    if not base_url or not apikey or not instancia or not numero:
        raise ValueError('WhatsApp não configurado (servidor, instância ou número destino ausentes — ver aba WhatsApp)')

    mensagem = (
        "*Mupa Brain* - Resumo diário\n\n"
        f"Catálogo: {stats['total_produtos']} produtos ({stats['com_foto']} com foto, {stats['com_arte']} com arte)\n"
        f"Cadastros automáticos: {cadastros_automaticos}\n"
        f"Cosmos: token #{cosmos_status['token_atual']}/{cosmos_status['total_tokens']} — {cosmos_status['sucessos']} sucesso(s), {cosmos_status['rotacoes']} rotação(ões)\n"
        f"Gemini: {gemini_status['imagem_sucessos']} arte(s) geradas, {gemini_status['imagem_rate_limit']} bloqueada(s) por limite\n"
        f"Imagens não encontradas pendentes: {len(pendentes)}"
    )

    url = f"{base_url}/message/sendText/{instancia}"
    headers = {'apikey': apikey, 'Content-Type': 'application/json'}
    payload = {'number': numero, 'textMessage': {'text': mensagem}}

    resposta = requests.post(url, json=payload, headers=headers, timeout=20)
    resposta.raise_for_status()


def enviar_resumo_diario_sistema():
    """Monta e envia (por e-mail e/ou WhatsApp, conforme os canais ativos) o resumo diário do
    sistema: estatísticas gerais do catálogo, status da rotação de tokens do Cosmos, cadastros
    automáticos via fontes externas e as imagens de produto ainda não encontradas / não
    notificadas. Ao contrário da versão anterior (só "imagens não encontradas"), agora sempre
    envia quando algum canal está ativo — mesmo sem pendências de imagem — porque o resumo
    também carrega as estatísticas gerais, que são úteis como um "heartbeat" diário do sistema.
    Os contadores (Cosmos, cadastros automáticos) são zerados após um envio bem-sucedido: cada
    resumo reporta o que aconteceu desde o resumo anterior, não o total histórico acumulado.
    Sempre roda dentro de um app_context (chamada tanto pelo agendador em thread quanto pela
    rota de teste manual)."""
    cfg = _ler_todas_config()

    if cfg.get('RESUMO_ATIVO', 'false') != 'true' and cfg.get('WHATSAPP_ATIVO', 'false') != 'true':
        return {'enviado': False, 'motivo': 'nenhum canal ativo', 'total_nao_encontradas': 0, 'erros': []}

    pendentes_query = (
        db.session.query(
            HistoricoBuscaImagem.codbar,
            db.func.count(HistoricoBuscaImagem.id),
            db.func.max(HistoricoBuscaImagem.criado_em),
        )
        .filter(HistoricoBuscaImagem.encontrado == False, HistoricoBuscaImagem.notificado_em.is_(None))
        .group_by(HistoricoBuscaImagem.codbar)
        .all()
    )
    codbars = [codbar for codbar, _, _ in pendentes_query]
    descricoes = {p.codbar: p.description for p in Produto.query.filter(Produto.codbar.in_(codbars)).all()} if codbars else {}
    pendentes = [{
        'codbar': codbar,
        'descricao': descricoes.get(codbar),
        'ocorrencias': total,
        'ultima_ocorrencia': ultima.strftime('%d/%m/%Y %H:%M'),
    } for codbar, total, ultima in pendentes_query]

    stats = _estatisticas_gerais()
    cosmos_status = _status_tokens_cosmos()
    gemini_status = _status_gemini()
    cadastros_automaticos = int(cfg.get('STATS_CADASTROS_AUTOMATICOS', '0') or '0')

    algum_sucesso = False
    erros = []

    if cfg.get('RESUMO_ATIVO', 'false') == 'true':
        try:
            _enviar_email_resumo_diario(cfg, pendentes, stats, cosmos_status, cadastros_automaticos, gemini_status)
            algum_sucesso = True
            logging.info("Resumo diário do sistema enviado por e-mail.")
        except Exception as e:
            erros.append(f'email: {e}')
            logging.error(f"Falha ao enviar resumo diário por e-mail: {e}")

    if cfg.get('WHATSAPP_ATIVO', 'false') == 'true':
        try:
            _enviar_whatsapp_resumo_diario(cfg, pendentes, stats, cosmos_status, cadastros_automaticos, gemini_status)
            algum_sucesso = True
            logging.info("Resumo diário do sistema enviado por WhatsApp.")
        except Exception as e:
            erros.append(f'whatsapp: {e}')
            logging.error(f"Falha ao enviar resumo diário por WhatsApp: {e}")

    if algum_sucesso:
        agora = datetime.utcnow()
        if codbars:
            db.session.query(HistoricoBuscaImagem).filter(
                HistoricoBuscaImagem.codbar.in_(codbars),
                HistoricoBuscaImagem.encontrado == False,
                HistoricoBuscaImagem.notificado_em.is_(None),
            ).update({HistoricoBuscaImagem.notificado_em: agora}, synchronize_session=False)
        set_config('STATS_COSMOS_SUCESSOS', '0')
        set_config('STATS_COSMOS_ROTACOES', '0')
        set_config('STATS_CADASTROS_AUTOMATICOS', '0')
        for chave in ('STATS_GEMINI_IMAGEM_SUCESSOS', 'STATS_GEMINI_IMAGEM_RATE_LIMIT', 'STATS_GEMINI_IMAGEM_ERROS',
                      'STATS_GEMINI_TEXTO_SUCESSOS', 'STATS_GEMINI_TEXTO_RATE_LIMIT', 'STATS_GEMINI_TEXTO_ERROS'):
            set_config(chave, '0')
        db.session.commit()

    return {'enviado': algum_sucesso, 'total_nao_encontradas': len(pendentes), 'erros': erros}


@app.route('/admin/enviar-resumo-diario', methods=['POST'])
@jwt_required()
def admin_enviar_resumo_diario():
    """Dispara manualmente o envio do resumo diário do sistema — útil para testar a
    configuração de SMTP/WhatsApp sem esperar o horário agendado."""
    try:
        resultado = enviar_resumo_diario_sistema()
    except Exception as e:
        logging.error(f"Erro ao enviar resumo diário manual: {e}")
        return jsonify({'message': f'Erro ao enviar resumo: {e}'}), 500

    if resultado.get('motivo') == 'nenhum canal ativo':
        return jsonify({'message': 'Nenhum canal de notificação está ativo (e-mail ou WhatsApp).', **resultado}), 400
    if not resultado['enviado']:
        return jsonify({'message': 'Falha ao enviar em todos os canais ativos.', **resultado}), 500
    return jsonify({'message': f"Resumo enviado ({resultado['total_nao_encontradas']} imagem(ns) pendente(s)).", **resultado}), 200


# =====================================================================
# WhatsApp — gestão de instância via Evolution API (self-hosted)
# =====================================================================
# Documentação: https://docs.evolutionfoundation.com.br/evolution-api/installation
# Reaproveita as mesmas chaves de Config já usadas pelo resumo diário
# (WHATSAPP_BASE_URL, WHATSAPP_TOKEN, WHATSAPP_INSTANCE, WHATSAPP_NUMERO_DESTINO) — criar uma
# instância aqui é o que torna esses campos funcionais para o envio do resumo.


def _evolution_config():
    """Lê a config da Evolution API; lança ValueError com mensagem amigável se faltar algo
    essencial (URL do servidor ou apikey)."""
    cfg = _ler_todas_config()
    base_url = cfg.get('WHATSAPP_BASE_URL', '').strip().rstrip('/')
    apikey = cfg.get('WHATSAPP_TOKEN', '').strip()
    instancia = cfg.get('WHATSAPP_INSTANCE', '').strip()
    if not base_url or not apikey:
        raise ValueError('Configure a URL do servidor e a API Key da Evolution API antes de continuar.')
    return base_url, apikey, instancia


@app.route('/admin/whatsapp/criar-instancia', methods=['POST'])
@jwt_required()
def admin_whatsapp_criar_instancia():
    """Cria uma nova instância na Evolution API e retorna o QR code (base64) pra escanear no
    WhatsApp. A instância criada vira a 'ativa' (salva em WHATSAPP_INSTANCE) — é ela que o
    resumo diário usa pra enviar mensagem."""
    try:
        base_url, apikey, _ = _evolution_config()
    except ValueError as e:
        return jsonify({'message': str(e)}), 400

    data = request.form or request.json or {}
    nome_instancia = (data.get('instance_name') or '').strip()
    if not nome_instancia:
        return jsonify({'message': 'Informe um nome para a instância.'}), 400
    numero = (data.get('number') or '').strip()

    payload = {
        'instanceName': nome_instancia,
        'qrcode': True,
        'integration': 'WHATSAPP-BAILEYS',
    }
    if numero:
        payload['number'] = numero

    try:
        resposta = requests.post(
            f'{base_url}/instance/create',
            headers={'apikey': apikey, 'Content-Type': 'application/json'},
            json=payload,
            timeout=30,
        )
    except requests.RequestException as e:
        return jsonify({'message': f'Erro de rede ao falar com a Evolution API: {e}'}), 502

    if resposta.status_code >= 400:
        return jsonify({'message': f'Evolution API retornou HTTP {resposta.status_code}: {resposta.text[:300]}'}), 502

    corpo = resposta.json()
    set_config('WHATSAPP_INSTANCE', nome_instancia)
    if numero:
        set_config('WHATSAPP_NUMERO_DESTINO', numero)

    qrcode = corpo.get('qrcode') or {}
    return jsonify({
        'message': f'Instância "{nome_instancia}" criada. Escaneie o QR code com o WhatsApp.',
        'instance_name': nome_instancia,
        'status': (corpo.get('instance') or {}).get('status'),
        'qrcode_base64': qrcode.get('base64'),
        'pairing_code': qrcode.get('pairingCode'),
    }), 201


@app.route('/admin/whatsapp/qrcode', methods=['GET'])
@jwt_required()
def admin_whatsapp_qrcode():
    """Busca um QR code novo pra instância já configurada (ex.: o código anterior expirou
    antes de escanear, ou a instância desconectou e precisa reconectar)."""
    try:
        base_url, apikey, instancia = _evolution_config()
    except ValueError as e:
        return jsonify({'message': str(e)}), 400
    if not instancia:
        return jsonify({'message': 'Nenhuma instância configurada ainda — crie uma primeiro.'}), 400

    try:
        resposta = requests.get(
            f'{base_url}/instance/connect/{instancia}',
            headers={'apikey': apikey},
            timeout=20,
        )
    except requests.RequestException as e:
        return jsonify({'message': f'Erro de rede ao falar com a Evolution API: {e}'}), 502

    if resposta.status_code >= 400:
        return jsonify({'message': f'Evolution API retornou HTTP {resposta.status_code}: {resposta.text[:300]}'}), 502

    corpo = resposta.json()
    return jsonify({
        'qrcode_base64': corpo.get('base64'),
        'pairing_code': corpo.get('pairingCode'),
    }), 200


@app.route('/admin/whatsapp/status', methods=['GET'])
@jwt_required()
def admin_whatsapp_status():
    """Estado atual da conexão da instância configurada: 'open' (conectado), 'close'
    (desconectado) ou 'connecting'. Usado pelo painel pra saber quando parar de mostrar o QR
    code (assim que o usuário escaneia, o estado vira 'open')."""
    try:
        base_url, apikey, instancia = _evolution_config()
    except ValueError as e:
        return jsonify({'message': str(e), 'state': 'unconfigured'}), 200
    if not instancia:
        return jsonify({'state': 'no_instance'}), 200

    try:
        resposta = requests.get(
            f'{base_url}/instance/connectionState/{instancia}',
            headers={'apikey': apikey},
            timeout=15,
        )
    except requests.RequestException as e:
        return jsonify({'state': 'error', 'message': str(e)}), 200

    if resposta.status_code == 404:
        return jsonify({'state': 'not_found'}), 200
    if resposta.status_code >= 400:
        return jsonify({'state': 'error', 'message': f'HTTP {resposta.status_code}'}), 200

    corpo = resposta.json()
    estado = (corpo.get('instance') or {}).get('state', 'unknown')
    return jsonify({'state': estado, 'instance_name': instancia}), 200


@app.route('/admin/whatsapp/instancias', methods=['GET'])
@jwt_required()
def admin_whatsapp_instancias():
    """Lista todas as instâncias cadastradas no servidor Evolution API (não só a configurada
    aqui) — útil pra ver o que já existe no servidor antes de criar uma nova."""
    try:
        base_url, apikey, _ = _evolution_config()
    except ValueError as e:
        return jsonify({'message': str(e)}), 400

    try:
        resposta = requests.get(f'{base_url}/instance/fetchInstances', headers={'apikey': apikey}, timeout=20)
    except requests.RequestException as e:
        return jsonify({'message': f'Erro de rede ao falar com a Evolution API: {e}'}), 502

    if resposta.status_code >= 400:
        return jsonify({'message': f'Evolution API retornou HTTP {resposta.status_code}: {resposta.text[:300]}'}), 502

    bruto = resposta.json()
    instancias = [{
        'instance_name': (item.get('instance') or {}).get('instanceName'),
        'status': (item.get('instance') or {}).get('status'),
        'state': ((item.get('instance') or {}).get('connectionStatus') or {}).get('state'),
    } for item in bruto] if isinstance(bruto, list) else []
    return jsonify({'instancias': instancias}), 200


@app.route('/admin/whatsapp/desconectar', methods=['POST'])
@jwt_required()
def admin_whatsapp_desconectar():
    """Desconecta (logout) a instância configurada sem apagá-la — o número sai do WhatsApp Web
    vinculado, mas a instância continua existindo no servidor pra reconectar depois."""
    try:
        base_url, apikey, instancia = _evolution_config()
    except ValueError as e:
        return jsonify({'message': str(e)}), 400
    if not instancia:
        return jsonify({'message': 'Nenhuma instância configurada.'}), 400

    try:
        resposta = requests.delete(f'{base_url}/instance/logout/{instancia}', headers={'apikey': apikey}, timeout=20)
    except requests.RequestException as e:
        return jsonify({'message': f'Erro de rede ao falar com a Evolution API: {e}'}), 502

    if resposta.status_code >= 400:
        return jsonify({'message': f'Evolution API retornou HTTP {resposta.status_code}: {resposta.text[:300]}'}), 502
    return jsonify({'message': f'Instância "{instancia}" desconectada.'}), 200


@app.route('/admin/whatsapp/excluir-instancia', methods=['POST'])
@jwt_required()
def admin_whatsapp_excluir_instancia():
    """Apaga a instância configurada no servidor Evolution API e limpa WHATSAPP_INSTANCE."""
    try:
        base_url, apikey, instancia = _evolution_config()
    except ValueError as e:
        return jsonify({'message': str(e)}), 400
    if not instancia:
        return jsonify({'message': 'Nenhuma instância configurada.'}), 400

    try:
        resposta = requests.delete(f'{base_url}/instance/delete/{instancia}', headers={'apikey': apikey}, timeout=20)
    except requests.RequestException as e:
        return jsonify({'message': f'Erro de rede ao falar com a Evolution API: {e}'}), 502

    if resposta.status_code >= 400:
        return jsonify({'message': f'Evolution API retornou HTTP {resposta.status_code}: {resposta.text[:300]}'}), 502

    set_config('WHATSAPP_INSTANCE', '')
    return jsonify({'message': f'Instância "{instancia}" excluída.'}), 200


@app.route('/admin/whatsapp/testar-envio', methods=['POST'])
@jwt_required()
def admin_whatsapp_testar_envio():
    """Envia uma mensagem de teste pro número configurado (WHATSAPP_NUMERO_DESTINO), pra
    validar que a instância está mesmo conectada e funcionando de ponta a ponta."""
    try:
        base_url, apikey, instancia = _evolution_config()
    except ValueError as e:
        return jsonify({'message': str(e)}), 400
    if not instancia:
        return jsonify({'message': 'Nenhuma instância configurada.'}), 400

    numero = _ler_todas_config().get('WHATSAPP_NUMERO_DESTINO', '').strip()
    if not numero:
        return jsonify({'message': 'Configure um número de destino primeiro.'}), 400

    try:
        resposta = requests.post(
            f'{base_url}/message/sendText/{instancia}',
            headers={'apikey': apikey, 'Content-Type': 'application/json'},
            json={'number': numero, 'textMessage': {'text': 'Mupa Brain: mensagem de teste — conexão OK!'}},
            timeout=20,
        )
    except requests.RequestException as e:
        return jsonify({'message': f'Erro de rede ao falar com a Evolution API: {e}'}), 502

    if resposta.status_code >= 400:
        return jsonify({'message': f'Evolution API retornou HTTP {resposta.status_code}: {resposta.text[:300]}'}), 502
    return jsonify({'message': f'Mensagem de teste enviada para {numero}.'}), 200


def _iniciar_agendador_resumo():
    """Thread em segundo plano que dispara enviar_resumo_diario_sistema() uma vez por dia, no
    horário configurado em RESUMO_HORARIO (formato 'HH:MM', padrão 08:00). Roda indefinidamente;
    erros de uma execução não impedem a próxima (loop nunca morre por exceção)."""
    def _loop():
        while True:
            try:
                with app.app_context():
                    horario = _ler_todas_config().get('RESUMO_HORARIO', '08:00').strip() or '08:00'
                    hora, minuto = (int(x) for x in horario.split(':'))
                agora = datetime.now()
                proxima = agora.replace(hour=hora, minute=minuto, second=0, microsecond=0)
                if proxima <= agora:
                    proxima += timedelta(days=1)
                time.sleep((proxima - agora).total_seconds())
                with app.app_context():
                    enviar_resumo_diario_sistema()
            except Exception as e:
                logging.error(f"Erro no loop do agendador de resumo: {e}")
                time.sleep(300)

    threading.Thread(target=_loop, daemon=True).start()


def _montar_mensagem_status_horario(motivo='heartbeat horário'):
    """Mensagem curta pro heartbeat horário via WhatsApp — bem mais enxuta que o resumo diário
    (só o essencial pra confirmar rapidamente, num relance, que o sistema está de pé e
    funcionando: fila de arte, uso do Gemini, status do Cosmos)."""
    fila_pendentes = _fila_arte.qsize()
    fila_processando = len(_gerando_arte_em_andamento)
    gemini_status = _status_gemini()
    cosmos_status = _status_tokens_cosmos()
    agora = datetime.now().strftime('%d/%m/%Y %H:%M')
    titulo = 'Sistema iniciado' if motivo == 'sistema iniciado' else 'Status horário'
    emoji = '🟢' if motivo == 'sistema iniciado' else '🕐'
    return (
        f"{emoji} *Mupa Brain* - {titulo}\n"
        f"{agora}\n\n"
        f"Fila de arte: {fila_pendentes} pendente(s), {fila_processando} em processamento\n"
        f"Gemini: {gemini_status['imagem_sucessos']} arte(s) geradas, {gemini_status['imagem_rate_limit']} bloqueada(s) por limite\n"
        f"Cosmos: token #{cosmos_status['token_atual']}/{cosmos_status['total_tokens']}"
    )


def _enviar_texto_whatsapp(mensagem):
    """Envia uma mensagem de texto livre via Evolution API, usando a mesma instância/número já
    configurados na aba WhatsApp (WHATSAPP_BASE_URL/WHATSAPP_TOKEN/WHATSAPP_INSTANCE/
    WHATSAPP_NUMERO_DESTINO). Levanta exceção em caso de falha ou config ausente — quem chama
    decide o que fazer. Ponto único de envio, reaproveitado pelo heartbeat horário e pela
    notificação de atualização automática (ver _notificar_atualizacao_whatsapp)."""
    cfg = _ler_todas_config()
    base_url = cfg.get('WHATSAPP_BASE_URL', '').strip().rstrip('/')
    instancia = cfg.get('WHATSAPP_INSTANCE', '').strip()
    apikey = cfg.get('WHATSAPP_TOKEN', '').strip()
    numero = cfg.get('WHATSAPP_NUMERO_DESTINO', '').strip()
    if not base_url or not apikey or not instancia or not numero:
        raise ValueError('WhatsApp não configurado (servidor, instância ou número destino ausentes — ver aba WhatsApp)')

    url = f"{base_url}/message/sendText/{instancia}"
    headers = {'apikey': apikey, 'Content-Type': 'application/json'}
    payload = {'number': numero, 'textMessage': {'text': mensagem}}

    resposta = requests.post(url, json=payload, headers=headers, timeout=20)
    resposta.raise_for_status()


def _enviar_status_horario_whatsapp(motivo='heartbeat horário'):
    """Envia o heartbeat horário via Evolution API. Levanta exceção em caso de falha; quem
    chama decide o que fazer (o agendador só loga e tenta de novo na próxima hora, nunca deixa
    a falta de 1 envio derrubar o loop)."""
    _enviar_texto_whatsapp(_montar_mensagem_status_horario(motivo))


def _iniciar_agendador_status_horario():
    """Thread em segundo plano que envia um heartbeat de status por WhatsApp uma vez por hora,
    das 07:00 às 22:00 (horário comercial) — pedido explícito do usuário: "o sistema nunca deve
    parar" e ele quer um jeito de perceber se parou. Um processo que já morreu não consegue
    avisar sobre si mesmo, então a estratégia é a mesma usada em monitoramento de verdade
    (heartbeat/dead man's switch): mensagens regulares e previsíveis (sempre na hora cheia,
    XX:00) fazem da AUSÊNCIA de uma mensagem esperada o próprio aviso — se não chegou a
    mensagem das 14h, alguma coisa parou entre as 13h e as 14h.

    Duas robustezes deliberadas:
    1. O `try/except` fica DENTRO do loop (por iteração), não ao redor da thread inteira — uma
       falha de rede na Evolution API derruba só aquele envio específico, nunca o agendador; a
       próxima hora tenta de novo normalmente. "Sistema nunca deve parar" vale pro agendador
       também.
    2. Na primeira execução (processo acabou de subir), se estiver dentro do horário comercial,
       envia IMEDIATAMENTE com um texto diferenciado ("Sistema iniciado") em vez de esperar a
       próxima hora cheia — isso funciona como um segundo sinal complementar ao heartbeat: se o
       processo cair e for reiniciado (manualmente ou por um supervisor), essa mensagem fora do
       padrão avisa que houve um restart, além do heartbeat regular continuar depois.

    Fora da janela 07-22h, dorme direto até o início do expediente do dia seguinte (não acorda
    de hora em hora à toa de madrugada). Controlado por STATUS_HORARIO_ATIVO (Config, default
    'true' — ativo assim que configurado, sem precisar de opt-in extra)."""
    HORA_INICIO = 7
    HORA_FIM = 22

    def _tentar_enviar(motivo):
        try:
            with app.app_context():
                _enviar_status_horario_whatsapp(motivo)
            logging.info(f"Status horário enviado por WhatsApp ({motivo}).")
        except Exception as e:
            logging.error(f"Falha ao enviar status horário por WhatsApp ({motivo}): {e}")

    def _loop():
        primeira_execucao = True
        while True:
            try:
                agora = datetime.now()
                with app.app_context():
                    ativo = _ler_todas_config().get('STATUS_HORARIO_ATIVO', 'true') == 'true'
                dentro_da_janela = HORA_INICIO <= agora.hour < HORA_FIM

                if ativo and dentro_da_janela:
                    _tentar_enviar('sistema iniciado' if primeira_execucao else 'heartbeat horário')
                primeira_execucao = False

                proxima = (agora + timedelta(hours=1)).replace(minute=0, second=0, microsecond=0)
                if not (HORA_INICIO <= proxima.hour < HORA_FIM):
                    proxima = proxima.replace(hour=HORA_INICIO)
                    if proxima <= agora:
                        proxima += timedelta(days=1)
                time.sleep(max(1.0, (proxima - agora).total_seconds()))
            except Exception as e:
                logging.error(f"Erro no loop do agendador de status horário: {e}")
                time.sleep(300)

    threading.Thread(target=_loop, daemon=True).start()


def _notificar_atualizacao_whatsapp(commit_antigo, commit_novo):
    """Avisa por WhatsApp que uma atualização automática via GitHub foi aplicada e o processo
    vai reiniciar — mesmo espírito do heartbeat horário (visibilidade sem precisar checar log
    manualmente), mas disparado pelo evento, não por horário."""
    mensagem = (
        "🔄 *Mupa Brain* - Atualização aplicada\n"
        f"{datetime.now().strftime('%d/%m/%Y %H:%M')}\n\n"
        f"{commit_antigo[:8]} → {commit_novo[:8]}\n"
        "Reiniciando o processo com o código novo..."
    )
    _enviar_texto_whatsapp(mensagem)


def _verificar_e_aplicar_atualizacao_github():
    """Confere se há commits novos no GitHub (origin/<branch atual>) e, se houver E a working
    tree estiver limpa, aplica sozinha: `git pull` seguido de reinício do processo via
    `os.execv` (recarrega o mesmo interpretador com os mesmos argumentos — funciona tanto
    rodando `python app.py` quanto no .exe compilado pelo PyInstaller, sem precisar de nenhum
    supervisor externo tipo NSSM/serviço do Windows).

    Nunca faz `git pull` se `git status --porcelain` não vier vazio — proteção deliberada:
    puxar por cima de mudanças locais não commitadas podia gerar conflito de merge ou, pior,
    sobrescrever trabalho em andamento (esse repositório já tem histórico de arquivos
    modificados fora de commit, ver seção de débito técnico no CLAUDE.md). Nesse caso só loga
    um aviso e sai — precisa de intervenção manual (commitar, descartar ou stash).

    Roda inteiramente via subprocess chamando o `git` do PATH — testado que o Python nativo do
    venv (não só o Git Bash) enxerga o `git` normalmente nesta máquina. Não faz nada (retorna
    cedo, em silêncio) se não for um repositório git — cobre o caso de alguém rodar a partir de
    um pacote zipado sem pasta .git (ver seção de empacotamento no CLAUDE.md)."""
    try:
        branch_res = _git('rev-parse', '--abbrev-ref', 'HEAD')
        if branch_res.returncode != 0:
            return
        branch = branch_res.stdout.strip()

        # Só considera "suja" a árvore se houver mudança em arquivo RASTREADO (modificado,
        # deletado, staged etc.) — um arquivo solto e nunca versionado (`??` no --porcelain,
        # ex.: um script de teste esquecido na raiz) não é risco nenhum pra um `git pull`
        # (só bloquearia de verdade se o commit remoto tentasse criar um arquivo exatamente
        # nesse mesmo caminho, caso raro). A primeira versão bloqueava em QUALQUER `??`
        # também — na prática isso deixava o auto-update permanentemente travado nesta
        # máquina por causa de ~12 arquivos avulsos pré-existentes, sem relação com o
        # trabalho de verdade sendo versionado.
        status_res = _git('status', '--porcelain')
        tem_mudanca_rastreada = any(
            linha.strip() and not linha.startswith('??')
            for linha in status_res.stdout.splitlines()
        )
        if tem_mudanca_rastreada:
            logging.warning("Atualização automática pulada: há mudanças locais não commitadas em arquivo rastreado.")
            return

        fetch_res = _git('fetch', 'origin', branch, timeout=60)
        if fetch_res.returncode != 0:
            logging.error(f"Falha ao buscar atualizações do GitHub: {fetch_res.stderr.strip()}")
            return

        local = _git('rev-parse', 'HEAD').stdout.strip()
        remoto = _git('rev-parse', f'origin/{branch}').stdout.strip()
        if not remoto or local == remoto:
            return  # já está na última versão

        # Não basta "diferente de local" — precisa ser efetivamente uma atualização (origin
        # contém commit(s) que local não tem). Sem essa checagem, um caso real aconteceu no
        # teste: local estava À FRENTE do remoto (commit feito aqui mas ainda não enviado ao
        # GitHub) e a função tratou isso como "atualização disponível", tentando um pull
        # desnecessário. `--count` = 0 quando origin não tem nada de novo (local igual ou à
        # frente); > 0 só quando origin realmente tem commit(s) que local ainda não possui.
        atras_res = _git('rev-list', '--count', f'HEAD..origin/{branch}')
        commits_atras = int(atras_res.stdout.strip() or '0') if atras_res.returncode == 0 else 0
        if commits_atras == 0:
            return  # local já tem tudo que o remoto tem (igual ou à frente) — nada a puxar

        logging.info(f"Nova versão detectada no GitHub ({local[:8]} -> {remoto[:8]}, {commits_atras} commit(s) novo(s)) — aplicando...")
        pull_res = _git('pull', 'origin', branch, timeout=120)
        if pull_res.returncode != 0:
            logging.error(f"Falha ao aplicar atualização (git pull): {pull_res.stderr.strip()}")
            return

        logging.info("Atualização aplicada com sucesso — reiniciando o processo...")
        try:
            _notificar_atualizacao_whatsapp(local, remoto)
        except Exception as e:
            logging.warning(f"Não foi possível notificar a atualização por WhatsApp: {e}")

        os.execv(sys.executable, [sys.executable] + sys.argv)
    except Exception as e:
        logging.error(f"Erro no verificador de atualização automática: {e}")


def _iniciar_agendador_atualizacao():
    """Thread em segundo plano que confere a cada 5 minutos se há commits novos no GitHub e
    aplica sozinha (ver _verificar_e_aplicar_atualizacao_github). Pedido do usuário: "quero que
    o sistema se atualize quando o github receber um push/commit".

    Por POLLING, não webhook: esta máquina não tem garantia de estar acessível publicamente pra
    receber uma chamada do GitHub no momento do push, então a checagem é sempre "puxar" (fetch
    periódico), nunca "receber um aviso" — o intervalo de 5 minutos é o teto de quanto tempo
    leva até o sistema notar um push novo, não é instantâneo. Controlado por
    AUTO_UPDATE_ATIVO (Config, default 'true' — ativo sem precisar de opt-in, mas pode ser
    desligado setando essa chave como 'false' direto no banco se um dia for preciso).

    Nunca morre por exceção — mesmo padrão dos outros agendadores: erro numa iteração não
    impede a próxima."""
    INTERVALO_SEGUNDOS = 300

    def _loop():
        while True:
            try:
                with app.app_context():
                    ativo = _ler_todas_config().get('AUTO_UPDATE_ATIVO', 'true') == 'true'
                    if ativo:
                        _verificar_e_aplicar_atualizacao_github()
            except Exception as e:
                logging.error(f"Erro no loop do agendador de atualização automática: {e}")
            time.sleep(INTERVALO_SEGUNDOS)

    threading.Thread(target=_loop, daemon=True).start()


if __name__ == '__main__':
    with app.app_context():
        db.create_all()
    _iniciar_agendador_resumo()
    _iniciar_agendador_status_horario()
    _iniciar_agendador_atualizacao()
    _iniciar_worker_fila_arte()
    # debug=True usa o reloader do Werkzeug, que re-executa o processo (sys.executable + argv)
    # pra vigiar mudança de arquivo — dentro de um .exe compilado (PyInstaller) isso reabre o
    # próprio .exe recursivamente, quebra. Roda com debug/reloader só fora do modo congelado
    # (ambiente de desenvolvimento, onde o reloader nunca foi confiável nesta máquina mesmo,
    # já documentado no CLAUDE.md — então desligar aqui não muda o fluxo de trabalho de dev).
    congelado = getattr(sys, 'frozen', False)
    app.run('0.0.0.0', port=5050, debug=not congelado, use_reloader=False, threaded=True)
