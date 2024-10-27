# API de Verificação de Credenciais do WordPress

Esta é uma API para verificação de credenciais do WordPress, projetada para processar arquivos contendo URLs e credenciais, gerar relatórios e validar as entradas. A API é construída usando Flask e possui suporte para CORS, rate limiting e autenticação por chave de API.

## Índice

- [Recursos](#recursos)
- [Instalação](#instalação)
- [Configuração](#configuração)
- [Uso](#uso)
- [Endpoints](#endpoints)
- [Erros](#erros)
- [Logs](#logs)
- [Contribuição](#contribuição)
- [Licença](#licença)

## Recursos

- **Autenticação:** Autenticação baseada em chave de API.
- **CORS:** Suporte a CORS para facilitar o uso em diferentes domínios.
- **Rate Limiting:** Limitação de taxa configurável para proteger a API.
- **Relatórios:** Geração de relatórios em vários formatos (JSON, CSV, etc.).
- **Tratamento de Erros:** Tratamento de erros robusto com mensagens significativas.

## Instalação

### Pré-requisitos

- Python 3.7 ou superior
- Dependências do projeto, que podem ser instaladas usando o `requirements.txt`.

### Passos para instalação

1. Clone o repositório:

   ```bash
   git clone https://github.com/seuusuario/wp-checker-main.git
   cd wp-checker-main
   ```

2. Instale as dependências:

   ```bash
   pip install -r requirements.txt
   ```

## Configuração

Antes de iniciar a API, você pode precisar configurar algumas variáveis de ambiente. Aqui estão algumas opções disponíveis:

- `UPLOAD_FOLDER`: Diretório para armazenar arquivos temporários (padrão: `temp`).
- `MAX_CONTENT_LENGTH`: Tamanho máximo do payload em bytes (padrão: `16 * 1024 * 1024`).
- `ALLOWED_ORIGINS`: Origens permitidas para CORS (padrão: `*`).
- `API_KEY`: Chave de API para autenticação.
- `PORT`: Porta onde a API será executada (padrão: `5000`).
- `ENVIRONMENT`: Modo de execução da API (`development` ou `production`).

## Uso

Para iniciar a API, execute o seguinte comando:

```bash
python api/api.py
```

## Endpoints

### `GET /health`

Verifica se a API está ativa.

**Resposta:**

```json
{
    "status": "healthy",
    "timestamp": "2024-10-26T12:34:56.789Z",
    "version": "1.0.0"
}
```

### `POST /check`

Verifica as credenciais do WordPress.

**Requisitos:**

- Cabeçalho: `X-API-Key: <sua_chave_api>`
- Corpo da requisição (JSON):

```json
{
    "file": "conteúdo do arquivo com URLs e credenciais",
    "valid": "caminho/do/arquivo/para/validos.txt",
    "invalid": "caminho/do/arquivo/para/invalidos.txt",
    "offline": "caminho/do/arquivo/para/offline.txt",
    "check_wp_version": true,
    "skip_ping": false,
    "threads": 10,
    "output": "relatório",
    "format": "json",
    "delay": 0
}
```

**Resposta:**

```json
{
    "request_id": "1234567890_abcd1234efgh",
    "results": {
        "valid": [...],
        "invalid": [...],
        "offline": [...]
    },
    "report": "relatório.json",
    "processing_time": "2.34s"
}
```

## Erros

A API fornece respostas de erro significativas em caso de falhas. Os códigos de status HTTP correspondentes são retornados. Exemplos incluem:

- **400 Bad Request**: Para requisições malformadas.
- **401 Unauthorized**: Para falhas de autenticação (chave de API inválida).
- **500 Internal Server Error**: Para erros inesperados no servidor.

## Logs

Os logs da API são armazenados em arquivos localizados em `./api/logs/api.log` e `./api/logs/critical.log`. Verifique esses arquivos para monitorar a atividade e erros da API.

