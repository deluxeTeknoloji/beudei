import os

class Config:
    SECRET_KEY = os.environ.get('SECRET_KEY') or 'super-gizli-anahtar-2024'
    ALLOWED_IPS = ['127.0.0.1', '192.168.1.0/24']  # Güvenilir IP'ler
    REMOTE_ACCESS = os.environ.get('REMOTE_ACCESS', 'False').lower() == 'true'