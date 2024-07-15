import os
import requests
from flask import request, jsonify
import openai
from .models import db, Produto

def allowed_file(filename):
    ALLOWED_EXTENSIONS = {'png', 'jpg', 'jpeg', 'webp'}
    return '.' in filename and filename.rsplit('.', 1)[1].lower() in ALLOWED_EXTENSIONS

def generate_product_suggestions(description, tipo_sugestao, marca=None, product_list=None):
    try:
        if tipo_sugestao == 'por_marca' and marca:
            query = f"Produtos da marca {marca} que combinam com '{description}'"
        elif tipo_sugestao == 'combinar':
            query = f"O que eu posso combinar com '{description}' e que eu possa comprar"
        elif tipo_sugestao == 'aleatorio' and product_list:
            query = f"Escolha dois produtos aleatórios desta lista que combinam com '{description}': {product_list}"
        else:
            return "Tipo de sugestão inválido ou informações insuficientes."

        response = openai.ChatCompletion.create(
            model="gpt-4",
            messages=[
                {"role": "system", "content": "Você é uma inteligência artificial desenvolvida para fornecer uma única resposta resumida e conversacional, indicando até dois produtos relacionados com base na descrição de um produto, em português do Brasil"},
                {"role": "user", "content": query}
            ]
        )
        suggestion = response.choices[0].message['content'].strip()
        return suggestion
    except Exception as e:
        return "Desculpe, não consegui encontrar uma sugestão adequada."
    
def find_existing_image(codbar, img_dir, image_extensions):
    """Procura a imagem existente em um diretório"""
    for ext in image_extensions:
        temp_path = os.path.join(img_dir, f'{codbar}.{ext}')
        if os.path.exists(temp_path):
            return temp_path
    return None

def save_image_from_response(image_data, codbar):
    """Salva a imagem a partir da resposta de uma requisição"""
    file_path = os.path.join('static', 'imgs_produtos', f'{codbar}.jpg')
    try:
        with open(file_path, 'wb') as f:
            f.write(image_data)
        img_url = url_for('static', filename=f'imgs_produtos/{codbar}.jpg', _external=True)
        logging.info(f"Imagem salva para o produto {codbar}: {img_url}")
        return jsonify({'imagem_url': img_url}), 200
    except Exception as e:
        logging.error(f"Erro ao salvar a imagem do produto {codbar}: {e}")
        return jsonify({'message': 'Error saving image'}), 500
    
def buscar_e_salvar_imagem_bing(codbar):
    """Busca e salva a imagem do produto no Bing"""
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
            logging.info(f"Nenhuma imagem encontrada no Bing para o produto {codbar}")
            return jsonify({'message': 'No image found from Bing'}), 404
    except requests.RequestException as e:
        logging.error(f"Erro ao buscar ou salvar imagem do Bing para o produto {codbar}: {e}")
        return jsonify({'message': f'Error fetching or saving image from Bing: {str(e)}'}), 500

def buscar_e_salvar_imagem_google(codbar):
    """Busca e salva a imagem do produto no Google Images"""
    search_url = f"https://www.googleapis.com/customsearch/v1?q={codbar}&cx={GOOGLE_CX}&searchType=image&num=1&key={GOOGLE_API_KEY}"
    try:
        response = requests.get(search_url)
        response.raise_for_status()
        results = response.json()
        if 'items' in results:
            image_url = results['items'][0]['link']
            response = requests.get(image_url)
            response.raise_for_status()
            return save_image_from_response(response.content, codbar)
        else:
            logging.info(f"Nenhuma imagem encontrada no Google para o produto {codbar}")
            return jsonify({'message': 'No image found from Google'}), 404
    except requests.RequestException as e:
        logging.error(f"Erro ao buscar ou salvar imagem do Google para o produto {codbar}: {e}")
        return jsonify({'message': f'Error fetching or saving image from Google: {str(e)}'}), 500


def serialize_produto_with_image(produto):
    img_url = None
    img_path = find_existing_image(produto.codbar, 'static/imgs_produtos', ['png', 'jpg', 'jpeg', 'webp'])
    if img_path:
        img_url = img_path
    else:
        bing_result = buscar_e_salvar_imagem_bing(produto.codbar)
        if bing_result[1] == 200:
            img_url = bing_result[0]['imagem_url']
        else:
            google_result = buscar_e_salvar_imagem_google(produto.codbar)
            if google_result[1] == 200:
                img_url = google_result[0]['imagem_url']

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
        return None

def fetch_product_from_google(codbar):
    search_url = f"https://www.googleapis.com/customsearch/v1?q={codbar}&cx={os.getenv('GOOGLE_CX')}&key={os.getenv('GOOGLE_API_KEY')}"
    try:
        response = requests.get(search_url)
        response.raise_for_status()
        results = response.json()
        if 'items' in results:
            product_info = results['items'][0]
            return {
                'gtin': codbar,
                'description': product_info.get('title', 'Descrição não disponível'),
                'ncm': {'description': 'NCM não disponível'},
                'brand': {'name': 'Marca não disponível'},
                'thumbnail': product_info['link'],
                'cest': {'code': 'CEST não disponível'},
                'package': {'type': 'Embalagem não disponível'},
                'category': {'name': 'Categoria não disponível'},
                'price': {'value': 0.0}
            }
        else:
            return None
    except requests.RequestException as e:
        return None

def text_to_speech(text, filename):
    token = get_azure_tts_token(os.getenv('AZURE_SUBSCRIPTION_KEY'))
    tts_url = f"https://{os.getenv('AZURE_REGION')}.tts.speech.microsoft.com/cognitiveservices/v1"
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

def get_azure_tts_token(subscription_key):
    fetch_token_url = f"https://{os.getenv('AZURE_REGION')}.api.cognitive.microsoft.com/sts/v1.0/issuetoken"
    headers = {'Ocp-Apim-Subscription-Key': subscription_key}
    response = requests.post(fetch_token_url, headers=headers)
    return response.text
