# Llama Desktop

Aplicativo desktop em Python (Tkinter) para conversar com modelos locais do Ollama com resposta em streaming.

## Recursos

- Chat local com modelos do Ollama (`/api/chat`)
- Streaming com atualização contínua de status (`Pensando...` / `Gerando...`)
- Perfis com prompts de sistema independentes
- Histórico por perfil
- Renderização Markdown no resultado final
- Interrupção de geração em andamento
- Busca web opcional (DuckDuckGo)

## Requisitos

- Python 3.11+
- Ollama instalado e rodando localmente
- Pelo menos um modelo baixado no Ollama

## Instalação

```bash
python -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt
```

## Execução

1. Inicie o servidor do Ollama:

```bash
ollama serve
```

2. Em outro terminal, rode a aplicação:

```bash
python app.py
```

## Uso

1. Clique em Atualizar modelos
2. Selecione um modelo
3. Selecione um perfil
4. Digite a mensagem e clique em Enviar

## Perfis e histórico

Os perfis são armazenados em `data/profiles.json` com estrutura simples:

```json
{
  "geral": {
    "name": "Conversa Geral",
    "system_prompt": "Você é um assistente útil, claro e conciso.",
    "history": []
  }
}
```

Para iniciar com um template genérico versionado no repositório:

```powershell
New-Item -ItemType Directory -Force data | Out-Null
Copy-Item profiles.example.json data/profiles.json -Force
```

Observações:

- O diretório `data/` está no `.gitignore` para evitar publicar histórico pessoal.
- Se `data/profiles.json` não existir, o app cria perfis padrão automaticamente.

## GPU AMD (opcional)

Em alguns ambientes Windows com AMD, pode ser útil iniciar o Ollama com Vulkan:

```powershell
$env:OLLAMA_VULKAN=1
ollama serve
```

## Estrutura do projeto

- `app.py`: interface Tkinter e fluxo de chat
- `ollama_client.py`: cliente HTTP streaming via httpx
- `profiles_manager.py`: persistência de perfis e histórico
- `web_search.py`: integração opcional com DuckDuckGo
- `requirements.txt`: dependências Python

## Licença

MIT
