from flask import Flask, request, jsonify
from flask_limiter import Limiter
from flask_limiter.util import get_remote_address
from flask_cors import CORS
from functools import wraps
import logging
import logging.handlers
import sys
import os
import json
import time
from datetime import datetime
import hashlib
from typing import Dict, Any, Optional
import traceback
from werkzeug.security import generate_password_hash, check_password_hash
import secrets

def setup_logging():
    log_dir = './api/logs'
    os.makedirs(log_dir, exist_ok=True)
    
    formatter = logging.Formatter(
        '%(asctime)s - %(name)s - %(levelname)s - [%(filename)s:%(lineno)d] - %(message)s'
    )
    
    handler = logging.handlers.RotatingFileHandler(
        os.path.join(log_dir, 'api.log'),
        maxBytes=10*1024*1024,  # 10MB
        backupCount=30
    )
    handler.setFormatter(formatter)
    
    # handler para logs critios separado
    critical_handler = logging.handlers.RotatingFileHandler(
        os.path.join(log_dir, 'critical.log'),
        maxBytes=5*1024*1024,  # 5MB
        backupCount=10
    )
    critical_handler.setFormatter(formatter)
    critical_handler.setLevel(logging.CRITICAL)
    
    logger = logging.getLogger(__name__)
    logger.setLevel(logging.INFO)
    logger.addHandler(handler)
    logger.addHandler(critical_handler)
    
    return logger

logger = setup_logging()

# Config do caminho do sistema
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from func.imports.init import *

# Config da aplicação
app = Flask(__name__)

# Config CORS mais restritiva
CORS(app, resources={
    r"/*": {
        "origins": os.getenv('ALLOWED_ORIGINS', '*').split(','),
        "methods": ["GET", "POST"],
        "allow_headers": ["Content-Type", "X-API-Key"]
    }
})

# Config do rate limiting com armazenamento persistente
limiter = Limiter(
    app=app,
    key_func=get_remote_address,
    default_limits=["200 per day", "50 per hour"],
    storage_uri=os.getenv('REDIS_URL', 'memory://')  # Use Redis em produção
)

# config centralizadas com validação
class Config:
    def __init__(self):
        self.UPLOAD_FOLDER = os.getenv('UPLOAD_FOLDER', 'temp')
        self.MAX_CONTENT_LENGTH = int(os.getenv('MAX_CONTENT_LENGTH', 16 * 1024 * 1024))
        self.ALLOWED_EXTENSIONS = {'txt'}
        self.API_KEY = os.getenv('API_KEY')
        self.MAX_THREADS = int(os.getenv('MAX_THREADS', 50))
        self.MIN_THREADS = int(os.getenv('MIN_THREADS', 1))
        self.DEFAULT_FORMAT = 'json'
        self.ALLOWED_FORMATS = {'json', 'csv', 'txt'}
        
        if not self.API_KEY:
            self.API_KEY = secrets.token_urlsafe(32)
            logger.warning("API_KEY não configurada. Usando chave gerada: %s", self.API_KEY)
        
        self.validate()
    
    def validate(self):
        if not os.path.isabs(self.UPLOAD_FOLDER):
            self.UPLOAD_FOLDER = os.path.abspath(self.UPLOAD_FOLDER)
        
        if self.MAX_CONTENT_LENGTH <= 0:
            raise ValueError("MAX_CONTENT_LENGTH deve ser positivo")
        
        if self.MIN_THREADS <= 0 or self.MAX_THREADS < self.MIN_THREADS:
            raise ValueError("Configuração inválida de threads")

CONFIG = Config()

class APIError(Exception):
    def __init__(self, message: str, status_code: int, error_code: Optional[str] = None):
        super().__init__()
        self.message = message
        self.status_code = status_code
        self.error_code = error_code or 'GENERIC_ERROR'

def require_api_key(f):
    @wraps(f)
    def decorated_function(*args, **kwargs):
        api_key = request.headers.get('X-API-Key')
        if not api_key:
            raise APIError("API key não fornecida", 401, 'MISSING_API_KEY')
        
        if not secrets.compare_digest(api_key, CONFIG.API_KEY):
            logger.warning(f"Tentativa de acesso com API key inválida: {api_key[:10]}...")
            raise APIError("API key inválida", 401, 'INVALID_API_KEY')
        
        return f(*args, **kwargs)
    return decorated_function

def validate_file_content(content: str) -> bool:
    if not content or not isinstance(content, str):
        return False
    if len(content.strip()) == 0:
        return False
    return True

def sanitize_filename(filename: str) -> str:
    safe_chars = set('abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789-_.')
    sanitized = ''.join(c for c in filename if c in safe_chars)
    return sanitized[:255]  

def secure_temp_file(prefix: str = '') -> tuple[str, str]:
    temp_dir = os.path.abspath(CONFIG.UPLOAD_FOLDER)
    os.makedirs(temp_dir, exist_ok=True)
    
    filename = f"{prefix}_{secrets.token_hex(16)}.tmp"
    filepath = os.path.join(temp_dir, filename)
    
    return filename, filepath

@app.errorhandler(APIError)
def handle_api_error(error):
    response = jsonify({
        'error': error.message,
        'status_code': error.status_code,
        'error_code': error.error_code
    })
    response.status_code = error.status_code
    return response

@app.errorhandler(Exception)
def handle_generic_error(error):
    logger.error(f"Erro não tratado: {str(error)}\n{traceback.format_exc()}")
    return jsonify({
        'error': 'Erro interno do servidor',
        'status_code': 500,
        'error_code': 'INTERNAL_ERROR'
    }), 500

@app.before_request
def before_request():
    if request.method == 'POST':
        if not request.is_json:
            raise APIError("Content-Type deve ser application/json", 400, 'INVALID_CONTENT_TYPE')
        
        content_length = request.content_length
        if content_length and content_length > CONFIG.MAX_CONTENT_LENGTH:
            raise APIError("Payload muito grande", 413, 'PAYLOAD_TOO_LARGE')

@app.route('/health', methods=['GET'])
@limiter.limit("60 per minute")
def health_check():
    return jsonify({
        'status': 'healthy',
        'timestamp': datetime.now().isoformat(),
        'version': os.getenv('APP_VERSION', '1.0.0')
    })

@app.route('/check', methods=['POST'])
@require_api_key
@limiter.limit("10 per minute")
def check_credentials():
    start_time = time.time()
    request_id = generate_request_id()
    logger.info(f"Iniciando requisição {request_id}")
    
    temp_files = []
    
    try:
        data = request.get_json()
        if not data:
            raise APIError("Dados JSON inválidos", 400, 'INVALID_JSON')
        
        if 'file' not in data:
            raise APIError("Campo 'file' é obrigatório", 400, 'MISSING_FILE')
        
        file_content = data['file']
        if not validate_file_content(file_content):
            raise APIError("Conteúdo do arquivo inválido", 400, 'INVALID_FILE_CONTENT')
        
        args = validate_and_sanitize_params(data, request_id)
        
        temp_filename, temp_file_path = secure_temp_file(f'credentials_{request_id}')
        temp_files.append(temp_file_path)
        
        with open(temp_file_path, 'w', encoding='utf-8') as temp_file:
            temp_file.write(file_content)
        
        try:
            results = process_file(args)
            
            output_file = f"{args['output']}.{args['format']}"
            generate_report(results, output_file, args['format'])
            
            response_data = {
                'request_id': request_id,
                'results': results,
                'report': output_file,
                'processing_time': f"{time.time() - start_time:.2f}s"
            }
            
            logger.info(f"Requisição {request_id} processada com sucesso")
            return jsonify(response_data), 200
            
        except Exception as e:
            logger.error(f"Erro no processamento da requisição {request_id}: {str(e)}")
            raise APIError(f"Erro no processamento: {str(e)}", 500, 'PROCESSING_ERROR')
    
    except APIError:
        raise
    
    except Exception as e:
        logger.error(f"Erro não esperado na requisição {request_id}: {str(e)}")
        raise APIError("Erro interno do servidor", 500, 'INTERNAL_ERROR')
    
    finally:
        for temp_file in temp_files:
            try:
                if os.path.exists(temp_file):
                    os.remove(temp_file)
            except Exception as e:
                logger.error(f"Erro ao remover arquivo temporário {temp_file}: {str(e)}")

def validate_and_sanitize_params(data: Dict[str, Any], request_id: str) -> Dict[str, Any]:
    args = {
        'valid': data.get('valid'),
        'invalid': data.get('invalid'),
        'offline': data.get('offline'),
        'check_wp_version': bool(data.get('check_wp_version', False)),
        'skip_ping': bool(data.get('skip_ping', False)),
        'threads': min(max(int(data.get('threads', 10)), CONFIG.MIN_THREADS), CONFIG.MAX_THREADS),
        'output': sanitize_filename(data.get('output', f'report_{request_id}')),
        'format': data.get('format', CONFIG.DEFAULT_FORMAT).lower(),
        'delay': max(int(data.get('delay', 0)), 0)
    }
    
    if args['format'] not in CONFIG.ALLOWED_FORMATS:
        raise APIError(
            f"Formato inválido. Use: {', '.join(CONFIG.ALLOWED_FORMATS)}", 
            400, 
            'INVALID_FORMAT'
        )
    
    return args

def generate_request_id() -> str:
    timestamp = int(time.time())
    random_bytes = secrets.token_bytes(8)
    request_hash = hashlib.sha256(random_bytes).hexdigest()[:12]
    return f"{timestamp}_{request_hash}"

if __name__ == '__main__':
    from waitress import serve
    
    # Config do ambiente
    port = int(os.getenv('PORT', 5000))
    environment = os.getenv('ENVIRONMENT', 'production')
    
    logger.info(f"Iniciando servidor na porta {port} em modo {environment}")
    
    if environment == 'development':
        app.run(debug=True, port=port)
    else:
        # config recomendadas para produção
        serve(
            app,
            host='0.0.0.0',
            port=port,
            threads=int(os.getenv('WAITRESS_THREADS', 4)),
            url_scheme='https'
        )