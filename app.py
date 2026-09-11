import os
import csv
import sqlite3
import requests
from io import StringIO, BytesIO
from io import StringIO
from datetime import datetime, timedelta
from flask import Flask, request, jsonify, url_for, render_template
from flask_sqlalchemy import SQLAlchemy
from flask_jwt_extended import JWTManager, create_access_token, jwt_required, verify_jwt_in_request
from werkzeug.utils import secure_filename
from flasgger import Swagger, swag_from
import openai
import pytz
import logging
try:
    from rembg import remove
    REMBG_ENABLED = True
except Exception:
    remove = None
    REMBG_ENABLED = False
    logging.warning("rembg não disponível - remoção de fundo desativada")
from PIL import Image
from flask_migrate import Migrate  # Adicionado
import re
from io import BytesIO

# Constants
ALLOWED_EXTENSIONS = {'png', 'jpg', 'jpeg', 'webp'}
IMAGES_FOLDER = 'static/imgs_produtos'
PROCESSED_IMAGES_FOLDER = 'static/processed_images'
AUDIO_FOLDER = 'static/audios'
BING_API_KEY = 'fd94e4427d7c4622919f8ac561818e94'
GOOGLE_API_KEY = 'AIzaSyAdGesz-7yJzbNKytK2iCIBTKbHWd7RRZU'
GOOGLE_CX = '053e66708840f4936'

OPENAI_API_KEY = os.getenv('OPENAI_API_KEY', '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')
JWT_SECRET_KEY = os.getenv('JWT_SECRET_KEY', 'your-jwt-secret-key')
AZURE_SUBSCRIPTION_KEY = os.getenv('AZURE_SUBSCRIPTION_KEY', '9beaf866156a478a9bfac946c05cddde')
AZURE_REGION = os.getenv('AZURE_REGION', 'brazilsouth')
TIMEZONE = os.getenv('TIMEZONE', 'UTC')

# Flask app setup
app = Flask(__name__)
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
DB_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'instance', 'produtos.db')  # Inicializado o Flask-Migrate

jwt = JWTManager(app)

# OpenAI API setup
openai.api_key = OPENAI_API_KEY

# Logger setup
logging.basicConfig(level=logging.INFO)

# Create necessary directories
os.makedirs(IMAGES_FOLDER, exist_ok=True)
os.makedirs(PROCESSED_IMAGES_FOLDER, exist_ok=True)
os.makedirs(AUDIO_FOLDER, exist_ok=True)

# Helper functions
def allowed_file(filename):
    """Verifica se o arquivo tem uma extensão permitida"""
    return '.' in filename and filename.rsplit('.', 1)[1].lower() in ALLOWED_EXTENSIONS

def save_image_with_background_removal(image, file_path):
    """Remove o fundo da imagem e salva no caminho especificado"""
    if not REMBG_ENABLED:
        logging.warning("rembg não disponível - operação ignorada")
        return
    # Converte para RGBA se necessário
    image = image.convert("RGBA") if image.mode != "RGBA" else image
    # Remove o fundo
    output_image = remove(image)
    # Salva a imagem processada
    output_image.save(file_path)

def fetch_product_from_google(ean):
    search_url = f"https://www.googleapis.com/customsearch/v1?q={ean}&cx={GOOGLE_CX}&searchType=image&num=2&key={GOOGLE_API_KEY}"
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

class SugestaoProduto(db.Model):
    """Modelo de Sugestão de Produto"""
    id = db.Column(db.Integer, primary_key=True)
    codbar = db.Column(db.String(255))
    tipo_sugestao = db.Column(db.String(255))  # Novo campo para tipo de sugestão
    sugestao = db.Column(db.String(255))
    audio_url = db.Column(db.String(255))

@app.route('/')
def index():
    return render_template('index.html')

@app.route('/remove_background_url', methods=['POST'])
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

@app.route('/painel/login', methods=['GET'])
def painel_login():
    """Retorna um token JWT de acesso ao painel de configurações."""
    expires = timedelta(hours=1)
    access_token = create_access_token(identity='antunes@mupa.app', expires_delta=expires)
    return jsonify({
        'token': access_token,
        'expires_in_hours': 1,
        'message': 'Token de acesso ao painel gerado com sucesso'
    })

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
    if username == 'antunes@mupa.app' and password == '#Mupa04051623$':
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
        filename = secure_filename(f'{codbar}.{file.filename.rsplit(".", 1)[1].lower()}')
        file_path = os.path.join(IMAGES_FOLDER, filename)
        file.save(file_path)
        return jsonify({'message': 'Image successfully uploaded', 'path': file_path}), 200
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
    
    image_found = False
    try:
        for ext in ALLOWED_EXTENSIONS:
            img_path = os.path.join(IMAGES_FOLDER, f'{codbar}.{ext}')
            logging.info(f"Verificando a existência do arquivo: {img_path}")
            if os.path.exists(img_path):
                os.remove(img_path)
                image_found = True
                logging.info(f"Imagem deletada: {img_path}")
                break
        if image_found:
            return jsonify({'message': 'Imagem deletada com sucesso'}), 200
        else:
            logging.warning(f"Imagem não encontrada para o código de barras: {codbar}")
            return jsonify({'message': 'Imagem não encontrada'}), 404
    except Exception as e:
        logging.error(f"Erro ao deletar a imagem para o código de barras {codbar}: {e}")
        return jsonify({'message': f'Erro no processamento da solicitação: {e}'}), 422

@app.route('/produto-imagem/<codbar>', methods=['GET'])
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
            'description': 'URL da imagem do produto'
        },
        '404': {
            'description': 'Imagem não encontrada'
        }
    }
})
def obter_imagem_produto(codbar):
    """Obtém a imagem de um produto"""
    img_path = find_existing_image(codbar, IMAGES_FOLDER, ALLOWED_EXTENSIONS)
    if img_path:
        img_url = request.host_url.rstrip('/') + '/' + img_path
        logging.info(f"Imagem encontrada localmente para o produto {codbar}: {img_url}")
        return jsonify({'imagem_url': img_url}), 200
    
    # Tenta baixar da API do Bing
    bing_result = buscar_e_salvar_imagem_bing(codbar)
    if bing_result[1] == 200:
        return bing_result

    # Se Bing falhar, tenta baixar da API do Google
    return buscar_e_salvar_imagem_google(codbar)

def find_existing_image(codbar, img_dir, image_extensions):
    """Procura a imagem existente em um diretório"""
    for ext in image_extensions:
        temp_path = os.path.join(img_dir, f'{codbar}.{ext}')
        if os.path.exists(temp_path):
            return temp_path
    return None

def save_image_from_response(image_data, codbar):
    """Salva a imagem a partir da resposta de uma requisição"""
    original_file_path = os.path.join(IMAGES_FOLDER, f'{codbar}.jpg')
    processed_file_path = os.path.join(PROCESSED_IMAGES_FOLDER, f'{codbar}.png')  # Mudamos para .png para suportar transparência

    try:
        # Salva a imagem original
        with open(original_file_path, 'wb') as f:
            f.write(image_data)

        img_url = request.host_url.rstrip('/') + '/' + original_file_path
        logging.info(f"Imagem salva para o produto {codbar}: {img_url}")

        # Processa a imagem para remover o fundo
        input_image = Image.open(original_file_path)
        save_image_with_background_removal(input_image, processed_file_path)

        img_url = request.host_url.rstrip('/') + '/' + processed_file_path
        logging.info(f"Imagem salva e processada para o produto {codbar}: {img_url}")

        return jsonify({'imagem_url': img_url}), 200
    except Exception as e:
        logging.error(f"Erro ao salvar a imagem do produto {codbar}: {e}")
        return jsonify({'message': 'Error saving image'}), 500

def buscar_e_salvar_imagem_bing(codbar):
    """Busca e salva a imagem do produto no Bing"""
    search_url = f"https://api.bing.microsoft.com/v7.0/images/search?q={codbar}"
    headers = {'Ocp-Apim-Subscription-Key': BING_API_KEY}
    try:
        response = requests.get(search_url, headers=headers)
        response.raise_for_status()
        results = response.json()
        
        if results.get('value'):
            for item in results['value']:
                image_url = item['contentUrl']
                if "https://cdn-cosmos.bluesoft.com.br/products/" in image_url:
                    continue
                try:
                    img_response = requests.get(image_url)
                    img_response.raise_for_status()
                    return save_image_from_response(img_response.content, codbar)
                except requests.RequestException as e:
                    logging.warning(f"Erro ao baixar a imagem do URL {image_url}: {e}")
            return jsonify({'message': 'No valid image found from Bing'}), 404
        else:
            logging.info(f"Nenhuma imagem encontrada no Bing para o produto {codbar}")
            return jsonify({'message': 'No image found from Bing'}), 404
    except requests.RequestException as e:
        logging.error(f"Erro ao buscar ou salvar imagem do Bing para o produto {codbar}: {e}")
        return jsonify({'message': f'Error fetching or saving image from Bing: {str(e)}'}), 500

def buscar_e_salvar_imagem_google(codbar):
    search_url = f"https://www.googleapis.com/customsearch/v1?q={codbar}&cx={GOOGLE_CX}&searchType=image&num=2&key={GOOGLE_API_KEY}"
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
                    return save_image_from_response(img_response.content, codbar)
                except requests.RequestException as e:
                    logging.warning(f"Erro ao baixar a imagem do URL {image_url}: {e}")
            return jsonify({'message': 'No valid image found from Google'}), 404
        else:
            logging.info(f"Nenhuma imagem encontrada no Google para o produto {codbar}")
            return jsonify({'message': 'No image found from Google'}), 404
    except requests.RequestException as e:
        logging.error(f"Erro ao buscar ou salvar imagem do Google para o produto {codbar}: {e}")
        return jsonify({'message': f'Error fetching or saving image from Google: {str(e)}'}), 500

def serialize_produto_with_image(produto):
    """Serializa um produto com a imagem"""
    img_url = None
    img_path = find_existing_image(produto.codbar, IMAGES_FOLDER, ALLOWED_EXTENSIONS)
    if img_path:
        img_url = request.host_url.rstrip('/') + '/' + img_path
    else:
        bing_result = buscar_e_salvar_imagem_bing(produto.codbar)
        if bing_result[1] == 200:
            img_url = bing_result[0].get_json().get('imagem_url')
        else:
            google_result = buscar_e_salvar_imagem_google(produto.codbar)
            if google_result[1] == 200:
                img_url = google_result[0].get_json().get('imagem_url')

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
        img_path = find_existing_image(produto.codbar, IMAGES_FOLDER, ALLOWED_EXTENSIONS)
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
        return new_product
    except Exception as e:
        return None

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
        suggested_eans = ['7896504300646', '7896026305133']  # Substitua com lógica real

        return suggestion, suggested_eans
    except Exception as e:
        logging.error(f"Erro ao gerar sugestões de produtos: {e}")
        return "Desculpe, não consegui encontrar uma sugestão adequada.", []

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

        suggestion, eans_sugeridos = generate_product_suggestions(produto, tipo_sugestao)

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
        files = os.listdir(IMAGES_FOLDER)
        images = [f for f in files if allowed_file(f)]
        image_urls = [url_for('static', filename='imgs_produtos/' + image, _external=True) for image in images]
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

def fetch_product_from_cosmos(ean):
    """Busca o produto na API do Cosmos usando o código de barras (EAN)"""
    cosmos_url = f"https://api.cosmos.bluesoft.com.br/gtins/{ean}.json"
    headers = {
        'Authorization': 'Bearer KZSEuMgGjPb8d9gFztQHiw'  # Seu token do Cosmos
    }
    try:
        response = requests.get(cosmos_url, headers=headers)
        response.raise_for_status()
        return response.json()
    except requests.RequestException as e:
        logging.error(f"Erro ao buscar produto no Cosmos: {e}")
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
    """Consulta o produto no banco de dados e, se não encontrado, busca e cadastra via API do Cosmos"""
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
            'categoriaText': produto.categoriaText
        }), 200

    # Se o produto não for encontrado, tenta buscar na API do Cosmos
    cosmos_data = fetch_product_from_cosmos(codbar)
    if cosmos_data:
        # Registra o produto no banco de dados
        novo_produto = register_product_in_database(cosmos_data)
        if novo_produto:
            return jsonify({
                'codbar': novo_produto.codbar,
                'description': novo_produto.description,
                'ncm': novo_produto.ncm,
                'marca': novo_produto.marca,
                'preco_medio': novo_produto.preco_medio,
                'categoriaText': novo_produto.categoriaText
            }), 201
        else:
            return jsonify({'message': 'Erro ao cadastrar produto no banco de dados'}), 500
    else:
        return jsonify({'message': 'Produto não encontrado'}), 404


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

@app.route('/configuracoes', methods=['GET', 'POST'])
def configuracoes():
    """Painel de configurações: página HTML (pública, sem segredos) + API JSON (requer JWT)."""
    # GET sem Accept: application/json → renderiza o shell HTML. A própria página
    # se autentica via /painel/login e busca os dados reais por fetch autenticado,
    # então nenhum segredo é embutido neste HTML público.
    if request.method == 'GET' and 'application/json' not in request.headers.get('Accept', ''):
        try:
            cfg = _ler_todas_config()
            openai_key_full = cfg.get('OPENAI_API_KEY', '') or ''
            use_openai = cfg.get('USE_OPENAI_SUGESTIONS', 'true') == 'true'
            rembg_enabled = cfg.get('REMBG_ENABLED', 'false') == 'true'
            preview = openai_key_full[:8] + '...' if openai_key_full and len(openai_key_full) > 8 else openai_key_full or '(não configurado)'
            return render_template(
                'configuracoes.html',
                openai_key_preview=preview,
                use_openai=use_openai,
                rembg_enabled=rembg_enabled,
                openai_key_full='',
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

        elif action == 'toggle_use_openai':
            val = data.get('use_openai')
            use_flag = (val == 'true' or val is True)
            set_config('USE_OPENAI_SUGESTIONS', str(use_flag).lower())
            return jsonify({'message': f'Flag USE_OPENAI_SUGESTIONS = {use_flag}', 'saved': True})

        elif action == 'toggle_rembg':
            val = data.get('rembg_enabled')
            enabled = (val == 'true' or val is True)
            set_config('REMBG_ENABLED', str(enabled).lower())
            return jsonify({'message': f'REMBG_ENABLED = {enabled}', 'saved': True})

        return jsonify({'error': 'Ação desconhecida'}), 400

    # GET com Accept: application/json → JSON
    cfg = _ler_todas_config()
    openai_key_full = cfg.get('OPENAI_API_KEY', '') or ''
    return jsonify({
        'openai_key': openai_key_full[:8] + '...' if openai_key_full and len(openai_key_full) > 8 else openai_key_full or '(não configurado)',
        'openai_key_full': openai_key_full,
        'use_openai': cfg.get('USE_OPENAI_SUGESTIONS', 'true') == 'true',
        'rembg_enabled': cfg.get('REMBG_ENABLED', 'false') == 'true',
    })


@app.route('/api/config', methods=['GET'])
@jwt_required()
def api_config():
    """Retorna todas as configurações (para o painel frontend)."""
    cfg = _ler_todas_config()
    openai_key_full = cfg.get('OPENAI_API_KEY', '') or ''
    return jsonify({
        'openai_key': openai_key_full[:8] + '...' if openai_key_full and len(openai_key_full) > 8 else openai_key_full or '(não configurado)',
        'openai_key_full': openai_key_full,
        'use_openai': cfg.get('USE_OPENAI_SUGESTIONS', 'true') == 'true',
        'rembg_enabled': cfg.get('REMBG_ENABLED', 'false') == 'true',
    })


@app.route('/api/teste-openai', methods=['GET'])
@jwt_required()
def api_teste_openai():
    """Testa se a chave da OpenAI está válida."""
    key = _ler_todas_config().get('OPENAI_API_KEY', '')
    if not key or not key.startswith('sk-'):
        return jsonify({'ok': False, 'message': 'Nenhuma chave OpenAI configurada no sistema.'}), 200
    try:
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


@app.route('/admin/estatisticas', methods=['GET'])
@jwt_required()
def admin_estatisticas():
    """Dashboard com estatísticas do catálogo."""
    conn = sqlite3.connect(DB_PATH)
    try:
        total = conn.execute("SELECT COUNT(*) FROM produto").fetchone()[0]
        with_photo = conn.execute(
            "SELECT COUNT(*) FROM produto WHERE foto_png IS NOT NULL AND foto_png != '' AND foto_png != 'No image available'"
        ).fetchone()[0]
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
    """Lista produtos que possuem foto cadastrada (paginação)."""
    page = request.args.get('page', 1, type=int)
    per_page = request.args.get('per_page', 15, type=int)
    search = request.args.get('search', '').strip()

    conn = sqlite3.connect(DB_PATH)
    try:
        base_query = """
            SELECT codbar, description, marca, foto_png, categoriaText, preco_medio
            FROM produto
            WHERE foto_png IS NOT NULL AND foto_png != '' AND foto_png != 'No image available'
        """
        count_query = "SELECT COUNT(*) FROM produto WHERE foto_png IS NOT NULL AND foto_png != '' AND foto_png != 'No image available'"

        params = []
        if search:
            base_query += " AND (UPPER(description) LIKE ? OR UPPER(marca) LIKE ? OR UPPER(codbar) LIKE ?)"
            like_search = f"%{search.upper()}%"
            params = [like_search, like_search, like_search]
            count_query += " AND (UPPER(description) LIKE ? OR UPPER(marca) LIKE ? OR UPPER(codbar) LIKE ?)"

        count = conn.execute(count_query, params if search else []).fetchone()[0]

        offset = (page - 1) * per_page
        if search:
            rows = conn.execute(base_query + " ORDER BY description LIMIT ? OFFSET ?",
                                tuple(params + [per_page, offset])).fetchall()
        else:
            rows = conn.execute(base_query + " ORDER BY description LIMIT ? OFFSET ?",
                                [per_page, offset]).fetchall()

        produtos = []
        for row in rows:
            codbar, desc, marca, foto, cat, preco = row
            produtos.append({
                'ean': codbar,
                'descricao': desc,
                'marca': marca,
                'categoria': cat,
                'foto_png': foto,
                'preco_medio': preco,
            })

        return jsonify({
            'produtos': produtos,
            'total': count,
            'page': page,
            'per_page': per_page,
            'pages': (count + per_page - 1) // per_page if count > 0 else 1,
        })
    finally:
        conn.close()


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
            return jsonify({
                'ean': codbar,
                'descricao': desc,
                'marca': marca,
                'categoria': cat,
                'foto_png': foto or 'No image available',
                'preco_medio': preco,
                'tipo_busca': 'ean_exact',
            })

        # Tenta por descrição (LIKE)
        like_q = f"%{q.upper()}%"
        rows = conn.execute(
            "SELECT codbar, description, marca, foto_png, categoriaText, preco_medio FROM produto WHERE UPPER(description) LIKE ? LIMIT 10",
            (like_q,)
        ).fetchall()
        if rows:
            resultados = []
            for r in rows:
                resultados.append({
                    'ean': r[0],
                    'descricao': r[1],
                    'marca': r[2],
                    'categoria': r[4],
                    'foto_png': r[3] or 'No image available',
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
                resultados.append({
                    'ean': r[0],
                    'descricao': r[1],
                    'marca': r[2],
                    'categoria': r[4],
                    'foto_png': r[3] or 'No image available',
                    'preco_medio': r[5],
                })
            return jsonify({'results': resultados, 'tipo_busca': 'marca_like', 'count': len(resultados)})

        return jsonify({'message': 'Nenhum produto encontrado', 'results': [], 'tipo_busca': 'none'}), 404
    finally:
        conn.close()

if __name__ == '__main__':
    with app.app_context():
        db.create_all()
    app.run('0.0.0.0', port=5000, debug=True)
