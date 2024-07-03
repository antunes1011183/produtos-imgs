from flask import Flask, request, send_file, jsonify
import os
import requests
from PIL import Image
from io import BytesIO

app = Flask(__name__)

# Pasta onde as imagens serão salvas
IMG_DIR = 'imgs_produto'
os.makedirs(IMG_DIR, exist_ok=True)

# Função para obter a imagem a partir do código de barras
def fetch_image(codigobarras):
    url = f'https://cdn-cosmos.bluesoft.com.br/products/{codigobarras}'
    response = requests.get(url)
    response.raise_for_status()  # Verificar se a solicitação foi bem-sucedida
    return Image.open(BytesIO(response.content))

# Função para salvar a imagem
def save_image(image, filename):
    image.save(filename)

@app.route('/download_image', methods=['GET'])
def download_image():
    codigobarras = request.args.get('codigobarras')
    if not codigobarras:
        return jsonify({'error': 'Código de barras é obrigatório'}), 400

    try:
        image = fetch_image(codigobarras)
        filename = os.path.join(IMG_DIR, f'{codigobarras}.png')
        save_image(image, filename)
        
        return send_file(filename, mimetype='image/png')

    except requests.HTTPError as e:
        return jsonify({'error': 'Falha ao baixar a imagem', 'details': str(e)}), 500

if __name__ == '__main__':
    app.run(debug=True)
