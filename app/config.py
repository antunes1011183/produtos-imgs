import os

class Config:
    BASE_DIR = os.path.abspath(os.path.dirname(__file__))
    SWAGGER = {
        'title': 'API de Produtos',
        'uiversion': 3,
        'version': '1.0.0',
        'description': 'API para gerenciar produtos e imagens de produtos'
    }
    SQLALCHEMY_DATABASE_URI = f"sqlite:///{os.path.join(BASE_DIR, '../instance/produtos.db')}"
    JWT_SECRET_KEY = os.getenv('JWT_SECRET_KEY', 'your-jwt-secret-key')
    JWT_ACCESS_TOKEN_EXPIRES = 3600  # 1 hour
    UPLOAD_FOLDER = os.path.join(BASE_DIR, '../static/imgs_produtos')
    AUDIO_FOLDER = os.path.join(BASE_DIR, '../static/audios')
    ALLOWED_EXTENSIONS = {'png', 'jpg', 'jpeg', 'webp'}
    BING_API_KEY = os.getenv('BING_API_KEY', 'fd94e4427d7c4622919f8ac561818e94')
    GOOGLE_API_KEY = 'AIzaSyC3W-xv3bUV9yO8nMx88ZOMdP6siy9ny3U'
    GOOGLE_CX = 'd1f975386e7b5450e'
    OPENAI_API_KEY = os.getenv('OPENAI_API_KEY', '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')
    AZURE_SUBSCRIPTION_KEY = os.getenv('AZURE_SUBSCRIPTION_KEY', '9beaf866156a478a9bfac946c05cddde')
    AZURE_REGION = os.getenv('AZURE_REGION', 'brazilsouth')
    TIMEZONE = os.getenv('TIMEZONE', 'UTC')
