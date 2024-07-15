import os
import csv
from io import StringIO
from datetime import datetime, timedelta
import pytz
import logging
from flask import Blueprint, request, jsonify, url_for, current_app
from flask_jwt_extended import create_access_token, jwt_required
from werkzeug.utils import secure_filename
from flasgger import swag_from
from .models import db, Produto, SugestaoProduto, ImagemProduto
from .utils import allowed_file, generate_product_suggestions, save_image_from_response, buscar_e_salvar_imagem_bing, buscar_e_salvar_imagem_google, serialize_produto_with_image, register_product_in_database, find_existing_image, fetch_product_from_google, text_to_speech

bp = Blueprint('routes', __name__)

@bp.route('/login', methods=['POST'])
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
        expires_time = datetime.now(pytz.timezone(current_app.config['TIMEZONE'])) + expires
        expires_timestamp = int(expires_time.timestamp() * 1000)
        return jsonify(access_token=access_token, expires_at=expires_timestamp), 200
    else:
        return jsonify({'error': 'Invalid credentials'}), 401

@bp.route('/upload-imagem-produto/<codbar>', methods=['POST'])
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
        file_path = os.path.join(current_app.config['UPLOAD_FOLDER'], filename)
        file.save(file_path)
        return jsonify({'message': 'Image successfully uploaded', 'path': file_path}), 200
    else:
        return jsonify({'message': 'File format not allowed'}), 400

@bp.route('/upload-multiplas-imagens', methods=['POST'])
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
            file_path = os.path.join(current_app.config['UPLOAD_FOLDER'], filename)
            file.save(file_path)

            nova_imagem = ImagemProduto(caminho=file_path)
            db.session.add(nova_imagem)
            db.session.commit()

            saved_files.append(file_path)
        else:
            return jsonify({'message': 'File format not allowed'}), 400

    return jsonify({'message': 'Images successfully uploaded', 'paths': saved_files}), 200

@bp.route('/importar-produtos', methods=['POST'])
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

@bp.route('/deletar-imagem-produto/<string:codbar>', methods=['DELETE'])
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
        for ext in current_app.config['ALLOWED_EXTENSIONS']:
            img_path = os.path.join(current_app.config['UPLOAD_FOLDER'], f'{codbar}.{ext}')
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

@bp.route('/produto-imagem/<codbar>', methods=['GET'])
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
    logging.info(f"Obtendo imagem para o produto com código de barras: {codbar}")

    produto = Produto.query.filter_by(codbar=codbar).first()
    if produto and produto.foto_png:
        img_url = url_for('static', filename=f'imgs_produtos/{codbar}.jpg', _external=True)
        logging.info(f"Imagem encontrada no banco de dados para o produto {codbar}: {img_url}")
        return jsonify({'imagem_url': img_url}), 200

    img_path = find_existing_image(codbar, 'static/imgs_produtos', ALLOWED_EXTENSIONS)
    if img_path:
        img_url = url_for('static', filename=f'imgs_produtos/{codbar}.jpg', _external=True)
        logging.info(f"Imagem encontrada localmente para o produto {codbar}: {img_url}")
        return jsonify({'imagem_url': img_url}), 200

    logging.info(f"Tentando buscar imagem do Bing para o produto {codbar}")
    bing_result = buscar_e_salvar_imagem_bing(codbar)
    if bing_result[1] == 200:
        if produto:
            produto.foto_png = f'imgs_produtos/{codbar}.jpg'
            db.session.commit()
        return bing_result

    logging.info(f"Tentando buscar imagem do Google para o produto {codbar}")
    google_result = buscar_e_salvar_imagem_google(codbar)
    if google_result[1] == 200:
        if produto:
            produto.foto_png = f'imgs_produtos/{codbar}.jpg'
            db.session.commit()
        return google_result

    logging.warning(f"Imagem não encontrada em nenhuma fonte para o produto {codbar}")
    return jsonify({'message': 'Imagem não encontrada em nenhuma fonte'}), 404



@bp.route('/produtos', methods=['GET'])
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
    per_page = request.args.get('per_page', default=10, type=int)
    
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

    result = [serialize_produto_with_image(produto) for produto in produtos]

    if not result:
        return jsonify({'message': 'No products found'}), 404

    return jsonify({
        'produtos': result,
        'total_pages': total_pages,
        'total_items': total_items
    }), 200

@bp.route('/produto-sugestoes', methods=['GET'])
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
            'name': 'file_path',
            'in': 'query',
            'type': 'string',
            'required': False,
            'description': 'Caminho do arquivo com lista de produtos'
        },
        {
            'name': 'tipo_sugestao',
            'in': 'query',
            'type': 'string',
            'required': True,
            'description': 'Tipo de sugestão: por_marca, combinar, aleatorio'
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
        tipo_sugestao = request.args.get('tipo_sugestao')
        file_path = request.args.get('file_path')

        if not ean:
            return jsonify({'error': 'EAN is required'}), 400
        if not tipo_sugestao:
            return jsonify({'error': 'Tipo de sugestão é obrigatório'}), 400

        produto = Produto.query.filter_by(codbar=ean).first()
        if not produto:
            google_data = fetch_product_from_google(ean)
            if google_data:
                produto = register_product_in_database(google_data)
                if not produto:
                    return jsonify({'message': 'Failed to register product in database'}), 500
            else:
                return jsonify({'message': 'Product not found locally or in Google API'}), 404

        suggestions = {}
        for tipo in ['por_marca', 'combinar', 'aleatorio']:
            suggestion_record = SugestaoProduto.query.filter_by(codbar=ean, tipo_sugestao=tipo).first()
            if suggestion_record:
                suggestions[tipo] = {
                    'suggestion': suggestion_record.sugestao,
                    'audio_url': suggestion_record.audio_url
                }
            else:
                if tipo == 'aleatorio' and file_path:
                    with open(file_path, 'r', encoding='utf-8') as f:
                        product_list = f.read().splitlines()
                else:
                    product_list = None

                suggestion = generate_product_suggestions(produto.description, tipo, marca=produto.marca, product_list=product_list)
                audio_file_path = text_to_speech(suggestion, f"{ean}_{tipo}.wav")
                if not audio_file_path:
                    return jsonify({'message': 'Failed to generate audio'}), 500
                audio_url = url_for('static', filename='audios/' + f"{ean}_{tipo}.wav", _external=True)
                new_suggestion = SugestaoProduto(codbar=ean, tipo_sugestao=tipo, sugestao=suggestion, audio_url=audio_url)
                db.session.add(new_suggestion)
                db.session.commit()
                suggestions[tipo] = {
                    'suggestion': suggestion,
                    'audio_url': audio_url
                }

        return jsonify(suggestions), 200

    except Exception as e:
        return jsonify({'message': 'Internal server error'}), 500

def get_azure_tts_token(subscription_key):
    """Obtém o token para Azure TTS"""
    fetch_token_url = f"https://{current_app.config['AZURE_REGION']}.api.cognitive.microsoft.com/sts/v1.0/issuetoken"
    headers = {'Ocp-Apim-Subscription-Key': subscription_key}
    response = requests.post(fetch_token_url, headers=headers)
    return response.text

def text_to_speech(text, filename):
    """Converte texto em fala usando Azure TTS"""
    token = get_azure_tts_token(current_app.config['AZURE_SUBSCRIPTION_KEY'])
    tts_url = f"https://{current_app.config['AZURE_REGION']}.tts.speech.microsoft.com/cognitiveservices/v1"
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
        file_path = os.path.join(current_app.config['AUDIO_FOLDER'], filename)
        with open(file_path, 'wb') as audio_file:
            audio_file.write(response.content)
        return file_path
    else:
        return None

@bp.route('/produto/preco/<string:codbar>', methods=['PUT'])
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
