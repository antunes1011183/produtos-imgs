import requests
from flask import Flask, request, jsonify, url_for
from flask_sqlalchemy import SQLAlchemy
from flask_jwt_extended import JWTManager, create_access_token, jwt_required
import csv
from io import StringIO
from flasgger import Swagger, swag_from
import os
from werkzeug.utils import secure_filename
import logging
from logging.handlers import RotatingFileHandler
from datetime import timedelta, datetime
import openai

# Constants
ALLOWED_EXTENSIONS = {'png', 'jpg', 'jpeg', 'webp'}
LOG_DIRECTORY = 'logs'
IMAGES_FOLDER = 'static/imgs_produtos'
AUDIO_FOLDER = 'static/audios'
BING_API_KEY = os.getenv('BING_API_KEY', 'fd94e4427d7c4622919f8ac561818e94')
COSMOS_TOKEN = os.getenv('COSMOS_TOKEN', 'Cb35rSvWdEpyN8-Su3o3wg')
OPENAI_API_KEY = os.getenv('OPENAI_API_KEY', 'your-openai-api-key')
JWT_SECRET_KEY = os.getenv('JWT_SECRET_KEY', 'your-jwt-secret-key')
AZURE_SUBSCRIPTION_KEY = os.getenv('AZURE_SUBSCRIPTION_KEY', 'your-azure-subscription-key')
AZURE_REGION = os.getenv('AZURE_REGION', 'your-azure-region')

# Flask app setup
app = Flask(__name__)
app.config['SWAGGER'] = {
    'title': 'API de Produtos',
    'uiversion': 3,
    'version': '1.0.0',
    'description': 'API para gerenciar produtos e imagens de produtos'
}
app.config['SQLALCHEMY_DATABASE_URI'] = 'sqlite:///produtos.db'
app.config['JWT_SECRET_KEY'] = JWT_SECRET_KEY
app.config['JWT_ACCESS_TOKEN_EXPIRES'] = timedelta(hours=1)
swagger = Swagger(app)
db = SQLAlchemy(app)
jwt = JWTManager(app)

# OpenAI API setup
openai.api_key = OPENAI_API_KEY

# Create necessary directories
for folder in [LOG_DIRECTORY, IMAGES_FOLDER, AUDIO_FOLDER]:
    if not os.path.exists(folder):
        os.makedirs(folder)

# Logger setup
error_log_handler = RotatingFileHandler(os.path.join(LOG_DIRECTORY, 'errors.log'), maxBytes=10000, backupCount=5)
error_log_handler.setLevel(logging.WARNING)
formatter = logging.Formatter('%(asctime)s - %(levelname)s - %(message)s')
error_log_handler.setFormatter(formatter)
app.logger.addHandler(error_log_handler)

# Helper functions
def allowed_file(filename):
    return '.' in filename and filename.rsplit('.', 1)[1].lower() in ALLOWED_EXTENSIONS

def generate_product_suggestions(description):
    try:
        response = openai.ChatCompletion.create(
            model="gpt-4",
            messages=[
                {"role": "system", "content": "Você é uma IA treinada para sugerir produtos para venda cruzada com base na descrição de um produto em um supermercado. As sugestões devem ser em português do Brasil."},
                {"role": "user", "content": f"Com base na descrição do produto '{description}' em um supermercado, sugira dois produtos relacionados para venda cruzada."}
            ]
        )
        suggestions = response['choices'][0]['message']['content'].strip().split(", ")
        return suggestions
    except Exception as e:
        log_error('Erro ao gerar sugestões de produtos', '', str(e))
        return []

def log_non_200(response):
    if response.status_code != 200:
        app.logger.warning(f'Non-200 Response: {request.path} - Method: {request.method} - Status: {response.status_code} - IP: {request.remote_addr}')
    return response

app.after_request(log_non_200)

def log_error(erro, barcode, descricao_erro):
    new_error = ErroLog(erro=erro, barcode=barcode, descricao_erro=descricao_erro)
    db.session.add(new_error)
    db.session.commit()

# Database models
class Produto(db.Model):
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
    id = db.Column(db.Integer, primary_key=True)
    codbar = db.Column(db.String(255))
    sugestao = db.Column(db.String(255))
    audio_url = db.Column(db.String(255))

class ErroLog(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    erro = db.Column(db.String(255))
    barcode = db.Column(db.String(255))
    descricao_erro = db.Column(db.String(255))
    data_hora = db.Column(db.DateTime, default=datetime.utcnow)

# Routes
@app.route('/logs/errors', methods=['GET'])
@jwt_required()
@swag_from({
    'tags': ['Logs'],
    'parameters': [
        {
            'name': 'data_inicio',
            'in': 'query',
            'type': 'string',
            'required': False,
            'description': 'Data de início para filtrar os logs (YYYY-MM-DD)'
        },
        {
            'name': 'data_fim',
            'in': 'query',
            'type': 'string',
            'required': False,
            'description': 'Data de fim para filtrar os logs (YYYY-MM-DD)'
        }
    ],
    'responses': {
        '200': {
            'description': 'Lista de erros',
            'examples': {
                'application/json': [
                    {
                        'id': 1,
                        'erro': 'Erro exemplo',
                        'barcode': '123456789',
                        'descricao_erro': 'Descrição do erro',
                        'data_hora': '2024-01-01 12:00:00'
                    }
                ]
            }
        }
    }
})
def view_error_logs():
    data_inicio = request.args.get('data_inicio')
    data_fim = request.args.get('data_fim')
    
    query = ErroLog.query
    
    if data_inicio:
        query = query.filter(ErroLog.data_hora >= datetime.strptime(data_inicio, '%Y-%m-%d'))
    if data_fim:
        query = query.filter(ErroLog.data_hora <= datetime.strptime(data_fim, '%Y-%m-%d'))

    erros = query.all()
    erros_json = [
        {
            'id': erro.id,
            'erro': erro.erro,
            'barcode': erro.barcode,
            'descricao_erro': erro.descricao_erro,
            'data_hora': erro.data_hora.strftime('%Y-%m-%d %H:%M:%S')
        }
        for erro in erros
    ]
    return jsonify(erros_json), 200

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
    username = request.form.get('username')
    password = request.form.get('password')
    if username == 'antunes@mupa.app' and password == '#Mupa04051623$':
        expires = timedelta(hours=1)
        access_token = create_access_token(identity=username, expires_delta=expires)
        expires_time = datetime.utcnow() + expires
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
    if 'file' not in request.files:
        return jsonify({'error': 'Nenhum arquivo CSV enviado'}), 400
    file = request.files['file']
    if file.filename == '' or not file.filename.endswith('.csv'):
        return jsonify({'error': 'O arquivo enviado não é um CSV válido'}), 400
    csv_data = file.read().decode('utf-8')
    csv_file = StringIO(csv_data)
    csv_reader = csv.DictReader(csv_file)
    for row in csv_reader:
        novo_produto = Produto(
            codbar=row['codbar'],
            description=row['description'],
            ncm=row['ncm'],
            cest_codigo=row['cest_codigo'],
            embalagem=row['embalagem'],
            foto_png=row['foto_png'],
            marca=row['marca'],
            preco_medio=float(row['preco_medio']),
            categoriaText=row['categoriaText']
        )
        db.session.add(novo_produto)
    db.session.commit()
    return jsonify({'message': 'Produtos importados com sucesso'}), 201

@app.route('/deletar-imagem-produto/<codbar>', methods=['DELETE'])
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
        }
    }
})
def deletar_imagem_produto(codbar):
    image_found = False
    for ext in ALLOWED_EXTENSIONS:
        img_path = os.path.join(IMAGES_FOLDER, f'{codbar}.{ext}')
        if os.path.exists(img_path):
            os.remove(img_path)
            image_found = True
            break
    if image_found:
        return jsonify({'message': 'Imagem deletada com sucesso'}), 200
    else:
        return jsonify({'message': 'Imagem não encontrada'}), 404

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
    img_path = find_existing_image(codbar, IMAGES_FOLDER, ALLOWED_EXTENSIONS)
    if img_path:
        img_url = request.host_url.rstrip('/') + '/' + img_path
        return jsonify({'imagem_url': img_url}), 200
    
    cosmos_image_url = f"https://cdn-cosmos.bluesoft.com.br/products/{codbar}.jpg"
    response = requests.get(cosmos_image_url)
    
    if response.status_code == 200:
        return save_image_from_response(response.content, codbar)
    
    return buscar_e_salvar_imagem_bing(codbar)

def find_existing_image(codbar, img_dir, image_extensions):
    for ext in image_extensions:
        temp_path = os.path.join(img_dir, f'{codbar}.{ext}')
        if os.path.exists(temp_path):
            return temp_path
    return None

def save_image_from_response(image_data, codbar):
    file_path = os.path.join(IMAGES_FOLDER, f'{codbar}.jpg')
    try:
        with open(file_path, 'wb') as f:
            f.write(image_data)
        img_url = request.host_url.rstrip('/') + '/' + file_path
        return jsonify({'imagem_url': img_url}), 200
    except Exception as e:
        log_error('Erro ao salvar imagem', codbar, str(e))
        return jsonify({'message': 'Error saving image'}), 500

def buscar_e_salvar_imagem_bing(codbar):
    search_url = f"https://api.bing.microsoft.com/v7.0/images/search?q={codbar}&count=1"
    headers = {'Ocp-Apim-Subscription-Key': BING_API_KEY}
    try:
        response = requests.get(search_url, headers=headers)
        response.raise_for_status()
        results = response.json()
        if results.get('value'):
            image_url = results['value'][0]['contentUrl']
            response = requests.get(image_url)
            response.raise_for_status()
            return save_image_from_response(response.content, codbar)
        else:
            app.logger.warning(f"Bing found no images for barcode {codbar}: {response.json()}")
            log_error('Imagem não encontrada no Bing', codbar, 'Nenhuma imagem encontrada')
            return jsonify({'message': 'No image found from Bing'}), 404
    except requests.RequestException as e:
        app.logger.error(f"Error fetching or saving image from Bing for barcode {codbar}: {str(e)}")
        log_error('Erro ao buscar ou salvar imagem no Bing', codbar, str(e))
        return jsonify({'message': f'Error fetching or saving image from Bing: {str(e)}'}), 500

def fetch_product_from_cosmos(ean):
    url = f"https://api.cosmos.bluesoft.com.br/gtins/{ean}"
    headers = {'X-Cosmos-Token': COSMOS_TOKEN}
    response = requests.get(url, headers=headers)
    if response.status_code == 200:
        return response.json()
    else:
        return None

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
    total_items = produtos_paginados.total
    total_pages = produtos_paginados.pages
    if not produtos_paginados.items:
        cosmos_data = fetch_product_from_cosmos(codbar)
        if cosmos_data:
            registered_product = register_product_in_database(cosmos_data)
            result = [serialize_produto_with_image(registered_product)]
            return jsonify({'produtos': result, 'total_pages': 1, 'total_items': 1}), 201
        else:
            return jsonify({'message': 'No products found'}), 404
    result = [serialize_produto_with_image(produto) for produto in produtos_paginados.items]
    return jsonify({'produtos': result, 'total_pages': total_pages, 'total_items': total_items}), 200

def serialize_produto_with_image(produto):
    img_url = None
    if produto.foto_png:
        img_url = url_for('static', filename='imgs_produtos/' + produto.foto_png, _external=True)

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

def register_product_in_database(product_data):
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
        app.logger.error(f"Error registering product in database: {str(e)}")
        log_error('Erro ao registrar produto no banco de dados', product_data.get('gtin', ''), str(e))
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
    try:
        ean = request.args.get('ean')
        if not ean:
            return jsonify({'error': 'EAN is required'}), 400
        produto = Produto.query.filter_by(codbar=ean).first()
        if not produto:
            cosmos_data = fetch_product_from_cosmos(ean)
            if cosmos_data:
                produto = register_product_in_database(cosmos_data)
                if not produto:
                    return jsonify({'message': 'Failed to register product in database'}), 500
            else:
                return jsonify({'message': 'Product not found locally or in Cosmos API'}), 404
        suggestion_record = SugestaoProduto.query.filter_by(codbar=ean).first()
        if suggestion_record:
            audio_url = suggestion_record.audio_url
            return jsonify({'suggestion': suggestion_record.sugestao, 'audio_url': audio_url}), 200
        suggestion = generate_product_suggestions(produto.description)
        audio_file_path = text_to_speech(suggestion, f"{ean}.wav")
        if not audio_file_path:
            return jsonify({'message': 'Failed to generate audio'}), 500
        audio_url = url_for('static', filename='audios/' + f"{ean}.wav", _external=True)
        new_suggestion = SugestaoProduto(codbar=ean, sugestao=suggestion, audio_url=audio_url)
        db.session.add(new_suggestion)
        db.session.commit()
        return jsonify({'suggestion': suggestion, 'audio_url': audio_url}), 200
    except Exception as e:
        app.logger.error(f"Error in /produto-sugestoes route: {str(e)}")
        log_error('Erro na rota /produto-sugestoes', ean, str(e))
        return jsonify({'message': 'Internal server error'}), 500

def get_azure_tts_token(subscription_key):
    fetch_token_url = f"https://{AZURE_REGION}.api.cognitive.microsoft.com/sts/v1.0/issuetoken"
    headers = {'Ocp-Apim-Subscription-Key': subscription_key}
    response = requests.post(fetch_token_url, headers=headers)
    return response.text

def text_to_speech(text, filename):
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
        app.logger.error(f"Error generating audio: {response.status_code}, {response.text}")
        log_error('Erro ao gerar áudio', '', f"Status: {response.status_code}, Texto: {response.text}")
        return None

if __name__ == '__main__':
    with app.app_context():
        db.create_all()
    app.run('0.0.0.0', port=5000, debug=True)
