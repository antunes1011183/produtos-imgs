from . import db

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
    """Modelo de Sugestão de Produto"""
    id = db.Column(db.Integer, primary_key=True)
    codbar = db.Column(db.String(255))
    tipo_sugestao = db.Column(db.String(50), nullable=False)
    sugestao = db.Column(db.String(255))
    audio_url = db.Column(db.String(255))
    
class ImagemProduto(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    caminho = db.Column(db.String(255), nullable=False)
