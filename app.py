import requests
from flask import Flask, request, jsonify
from flask_sqlalchemy import SQLAlchemy
from flask_jwt_extended import JWTManager, create_access_token, jwt_required
import csv
from io import StringIO
from flasgger import Swagger
import os
from PIL import Image
from flask import url_for
from io import BytesIO
from flask import send_from_directory
import os
from werkzeug.utils import secure_filename
import smtplib
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
import logging
from logging.handlers import RotatingFileHandler
from datetime import timedelta, datetime


ALLOWED_EXTENSIONS = {'png', 'jpg', 'jpeg', 'webp'}

def allowed_file(filename):
    return '.' in filename and filename.rsplit('.', 1)[1].lower() in ALLOWED_EXTENSIONS

app = Flask(__name__)
app.config['SWAGGER'] = {
    'title': 'API de Produtos',
    'uiversion': 3,
}
swagger = Swagger(app)
app.config['SQLALCHEMY_DATABASE_URI'] = 'sqlite:///produtos.db'
app.config['JWT_SECRET_KEY'] = 'secretpassword'  # Change as needed
app.config['JWT_ACCESS_TOKEN_EXPIRES'] = timedelta(hours=1)  # 1 hour token expiration
db = SQLAlchemy(app)
jwt = JWTManager(app)

images_folder = os.path.join(app.static_folder, 'imgs_produtos')
if not os.path.exists(images_folder):
    os.makedirs(images_folder)

# Define the Product model
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
    
# Setup Logger for non-200 status codes
log_directory = 'logs'
if not os.path.exists(log_directory):
    os.makedirs(log_directory)

error_log_handler = RotatingFileHandler(os.path.join(log_directory, 'errors.log'), maxBytes=10000, backupCount=5)
error_log_handler.setLevel(logging.WARNING)
formatter = logging.Formatter('%(asctime)s - %(levelname)s - %(message)s')
error_log_handler.setFormatter(formatter)

app.logger.addHandler(error_log_handler)

def log_non_200(response):
    if response.status_code != 200:
        app.logger.warning(f'Non-200 Response: {request.path} - Method: {request.method} - Status: {response.status_code} - IP: {request.remote_addr}')
    return response

app.after_request(log_non_200)

@app.route('/logs/errors')
@jwt_required()  # Uncomment to enable JWT authentication
def view_error_logs():
    log_path = os.path.join('logs', 'errors.log')
    if os.path.exists(log_path):
        with open(log_path, 'r') as file:
            return file.read(), 200
    else:
        return jsonify({'message': 'Log file does not exist'}), 404

# Authentication and JWT token endpoint
@app.route('/login', methods=['POST'])
def login():
    username = request.form.get('username')
    password = request.form.get('password')
    if username == 'antunes@mupa.app' and password == '#Mupa04051623$':
        # Define the duration for which the token should be valid
        expires = timedelta(hours=1)  # Set expiration time to 1 hour
        access_token = create_access_token(identity=username, expires_delta=expires)
        
        # Calculate the exact expiration time and convert to milliseconds
        expires_time = datetime.utcnow() + expires
        expires_timestamp = int(expires_time.timestamp() * 1000)  # Convert to milliseconds
        
        return jsonify(access_token=access_token, expires_at=expires_timestamp), 200
    else:
        return jsonify({'error': 'Invalid credentials'}), 401
    
@app.route('/upload-imagem-produto/<codbar>', methods=['POST'])
@jwt_required()
def upload_imagem_produto(codbar):
    if 'file' not in request.files:
        return jsonify({'message': 'No file part in the request'}), 400
    file = request.files['file']
    if file.filename == '':
        return jsonify({'message': 'No file selected'}), 400
    if file and allowed_file(file.filename):
        filename = secure_filename(f'{codbar}.{file.filename.rsplit(".", 1)[1].lower()}')
        file_path = os.path.join(app.static_folder, 'imgs_produtos', filename)
        file.save(file_path)
        return jsonify({'message': 'Image successfully uploaded', 'path': file_path}), 200
    else:
        return jsonify({'message': 'File format not allowed'}), 400

@app.route('/upload-imagem-url-produto/<codbar>', methods=['POST'])
@jwt_required()
def upload_imagem_url_produto(codbar):
    # Obter a URL da imagem do corpo da requisição
    image_url = request.json.get('url')
    if not image_url:
        return jsonify({'message': 'URL da imagem é obrigatória'}), 400

    # Tente baixar a imagem
    try:
        response = requests.get(image_url)
        response.raise_for_status()  # Garante que a requisição foi bem sucedida
    except requests.RequestException as e:
        return jsonify({'message': f'Erro ao baixar a imagem: {e}'}), 500

    # Definir o caminho do arquivo onde a imagem será salva
    filename = secure_filename(f'{codbar}.jpg')  # Você pode adicionar lógica para determinar a extensão correta
    file_path = os.path.join(app.static_folder, 'imgs_produtos', filename)

    # Salvar a imagem
    with open(file_path, 'wb') as f:
        f.write(response.content)

    return jsonify({'message': 'Imagem carregada com sucesso', 'path': file_path}), 200
    
# Import products from a CSV file
@app.route('/importar-produtos', methods=['POST'])
@jwt_required()
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

# Get products based on query parameters with pagination
@app.route('/produtos', methods=['GET'])
@jwt_required()
def get_produtos():
    codbar = request.args.get('codbar', default=None, type=str)
    descricao = request.args.get('descricao', default=None, type=str)
    categoria = request.args.get('categoria', default=None, type=str)
    page = request.args.get('page', default=1, type=int)
    per_page = request.args.get('per_page', default=100, type=int)

    produtos_query = Produto.query

    if codbar:
        produto = produtos_query.filter_by(codbar=codbar).first()
        if not produto:
            return jsonify({'message': 'Produto não encontrado'}), 404

        result = serialize_produto_with_image(produto)

        return jsonify({'produtos': [result], 'total_pages': 1, 'total_items': 1}), 200
    else:
        produtos_paginados = produtos_query.paginate(page=page, per_page=per_page, error_out=False)
        total_items = produtos_paginados.total
        total_pages = produtos_paginados.pages

        if not produtos_paginados.items:
            return jsonify({'message': 'Nenhum produto encontrado'}), 404

        result = [serialize_produto_with_image(produto) for produto in produtos_paginados.items]

        return jsonify({'produtos': result, 'total_pages': total_pages, 'total_items': total_items}), 200

def serialize_produto_with_image(produto):
    # Lista de possíveis extensões de imagem
    image_extensions = ['png', 'jpg', 'jpeg', 'webp']
    img_url = None
    # Encontrar o arquivo de imagem que corresponde a qualquer das extensões
    for ext in image_extensions:
        img_path = os.path.join(app.static_folder, 'imgs_produtos', f'{produto.codbar}.{ext}')
        if os.path.exists(img_path):
            img_url = request.host_url.rstrip('/') + url_for('static', filename=f'imgs_produtos/{produto.codbar}.{ext}').replace('\\', '/')
            break

    return {
        'codbar': produto.codbar,
        'description': produto.description,
        'ncm': produto.ncm,
        'cest_codigo': produto.cest_codigo,
        'embalagem': produto.embalagem,
        'foto_png': produto.foto_png,
        'marca': produto.marca,
        'preco_medio': produto.preco_medio,
        'categoriaText': produto.categoriaText,
        'img_produto': img_url or "Imagem não disponível"
    }
# Update product based on codbar
@app.route('/alterar-produto', methods=['PUT'])
@jwt_required()
def alterar_produto():
    codbar = request.form.get('codbar')
    descricao = request.form.get('descricao')
    preco_medio = request.form.get('preco_medio', type=float)

    if not codbar:
        return jsonify({'error': 'Código de barras não fornecido'}), 400

    produto = Produto.query.filter_by(codbar=codbar).first()

    if not produto:
        return jsonify({'message': 'Produto não encontrado'}), 404

    if descricao:
        produto.description = descricao

    if preco_medio is not None:
        produto.preco_medio = preco_medio

    db.session.commit()
    return jsonify({'message': 'Produto alterado com sucesso'}), 200

import os
from flask import jsonify

@app.route('/deletar-imagem-produto/<codbar>', methods=['DELETE'])
@jwt_required()
def deletar_imagem_produto(codbar):
    img_dir = os.path.join(app.static_folder, 'imgs_produtos')
    image_extensions = ['png', 'jpg', 'jpeg', 'webp']
    image_found = False

    # Tentar encontrar e deletar a imagem
    for ext in image_extensions:
        img_path = os.path.join(img_dir, f'{codbar}.{ext}')
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
def obter_imagem_produto(codbar):
    img_dir = 'imgs_produtos'
    image_extensions = ['png', 'jpg', 'jpeg', 'webp']

    # Try to find an existing image
    img_path = find_existing_image(codbar, img_dir, image_extensions)
    if img_path:
        img_url = request.host_url.rstrip('/') + url_for('static', filename=os.path.join(img_dir, os.path.basename(img_path)))
        return jsonify({'imagem_url': img_url}), 200

    # If no image is found locally, try to fetch and save from external sources
    return buscar_e_salvar_imagem(codbar)

def find_existing_image(codbar, img_dir, image_extensions):
    for ext in image_extensions:
        temp_path = os.path.join(app.static_folder, img_dir, f'{codbar}.{ext}')
        if os.path.exists(temp_path):
            return temp_path
    return None

def buscar_e_salvar_imagem(codbar):
    primary_url = f"https://cdn-cosmos.bluesoft.com.br/products/{codbar}"
    try:
        response = requests.get(primary_url)
        app.logger.info(f"Response from primary URL for {codbar}: {response.status_code}, {response.text}")
        if response.status_code == 200:
            return save_image_from_response(response.content, codbar)
        else:
            app.logger.info(f"Primary URL responded with status {response.status_code} for barcode {codbar}")
    except Exception as e:
        app.logger.error(f"Error fetching from primary source: {str(e)}")

    # If primary source fails, try Bing API
    return buscar_e_salvar_imagem_bing(codbar)

def save_image_from_response(image_data, codbar):
    """ Saves the image data to static directory and returns the image URL """
    file_path = os.path.join(app.static_folder, 'imgs_produtos', f'{codbar}.jpg')
    with open(file_path, 'wb') as f:
        f.write(image_data)
    img_url = request.host_url.rstrip('/') + url_for('static', filename=f'imgs_produtos/{codbar}.jpg')
    return jsonify({'imagem_url': img_url}), 200

def buscar_e_salvar_imagem_bing(codbar):
    bing_api_key = os.getenv('BING_API_KEY', 'fd94e4427d7c4622919f8ac561818e94')
    search_url = f"https://api.bing.microsoft.com/v7.0/images/search?q={codbar}&count=1"
    headers = {'Ocp-Apim-Subscription-Key': bing_api_key}

    try:
        response = requests.get(search_url, headers=headers)
        response.raise_for_status()  # This will raise an error for non-200 responses
        results = response.json()
        if results.get('value'):
            image_url = results['value'][0]['contentUrl']
            response = requests.get(image_url)
            response.raise_for_status()
            return save_image_from_response(response.content, codbar)
        else:
            app.logger.warning(f"Bing found no images for barcode {codbar}: {response.json()}")
            return jsonify({'message': 'No image found from Bing'}), 404
    except requests.RequestException as e:
        app.logger.error(f"Error fetching or saving image from Bing for barcode {codbar}: {str(e)}")
        return jsonify({'message': f'Error fetching or saving image from Bing: {str(e)}'}), 500

if __name__ == '__main__':
    with app.app_context():
        db.create_all()

    app.run('0.0.0.0', port=5000, debug=True)
