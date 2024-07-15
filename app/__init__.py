import os
import requests
import logging
from flask import Flask
from flask_sqlalchemy import SQLAlchemy
from flask_jwt_extended import JWTManager
from flasgger import Swagger

# Configuração da aplicação
db = SQLAlchemy()

def create_app():
    app = Flask(__name__)
    app.config.from_object('app.config.Config')

    # Inicializa extensões
    db.init_app(app)
    JWTManager(app)
    Swagger(app)

    # Registra blueprints
    from .routes import bp as routes_bp
    app.register_blueprint(routes_bp)

    # Criação de diretórios necessários
    os.makedirs(app.config['UPLOAD_FOLDER'], exist_ok=True)
    os.makedirs(app.config['AUDIO_FOLDER'], exist_ok=True)

    # Criação das tabelas do banco de dados
    with app.app_context():
        db.create_all()

    return app
