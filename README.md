# CaroM - Pre-entrega 3: Sistema de recuperación semántica local (RAG)
[![Python Tests](https://github.com/carolinamolfino-bsas/CaroM-RAG-Local/actions/workflows/tests.yml/badge.svg)](https://github.com/carolinamolfino-bsas/CaroM-RAG-Local/actions/workflows/tests.yml)

Sistema RAG End-to-End en Python 3.12. Recibe una pregunta, busca los fragmentos relevantes en una base vectorial **ChromaDB** local y genera una respuesta con **LangChain (LCEL)** usando **solo** esa información. Si la respuesta no está en los documentos, responde **"No lo sé"**.

Por defecto corre **100 % local y gratis** con [Ollama]: embeddings y LLM en tu computadora, sin API keys. También funciona con OpenAI o Anthropic cambiando el `.env`.

```
                         ┌──────────── ingest.py (una vez, o cuando cambian los documentos) ────────────┐
data/*.md ──► RecursiveCharacterTextSplitter (500 tokens, overlap 50) ──► embeddings ──► ChromaDB ./vectorstore
                                                                                              │
pregunta ──► embedding (mismo modelo) ──► búsqueda de similitud (top_k=4) ◄──────────────────┘
                                                │ fragmentos numerados [1]..[4]
                                                ▼
                         prompt "filtro de veracidad" ──► LLM (async) ──► PydanticOutputParser
                                                                                │
                                         validación de referencias ──► RAGResponse(respuesta, referencias)
```

## Estructura

```
CaroM-RAG-Local/
├── data/                    # Dataset de ejemplo: 4 manuales (.md) de una empresa ficticia
├── ingest.py                # 1. Ingesta: carga, chunking por tokens y persistencia en ChromaDB
├── rag.py                   # 2 y 3. Retriever + cadena LCEL + get_rag_response() asíncrona
├── main.py                  # Prueba End-to-End: pregunta con respuesta + pregunta trampa
├── ejemplos/ejecucion_ollama.json   # Registro de la corrida real con Ollama (bge-m3 + qwen2.5:7b)
├── config.py                # Configuración validada con Pydantic (.env)
├── llm.py                   # ChatOpenAI / ChatAnthropic / Ollama (reutiliza el Módulo 1)
├── tests/                   # 25 tests offline + servidor HTTP que imita a Ollama
├── .github/workflows/tests.yml   # CI: ruff + mypy + pytest en cada push
├── .env.example
└── requirements.txt / requirements-dev.txt
```

## El dataset

`data/` contiene 4 documentos internos de **Kaleido Pagos**, una empresa **ficticia** inventada para este ejercicio, sobre su plataforma de pagos "Orion":

| Archivo | Tema |
|---|---|
| `01_arquitectura_orion.md` | Componentes, infraestructura, observabilidad y seguridad |
| `02_gestion_de_incidentes.md` | Severidades SEV1 a SEV4, roles, flujo y postmortems |
| `03_politica_de_despliegues.md` | Ventanas, congelamiento, canary y criterios de reversión |
| `04_guardias.md` | Rotación on-call, escalamiento y compensación |

Que la empresa sea ficticia es a propósito: el LLM **no puede conocer estos datos de antemano**. Si responde bien, la información salió de los documentos; si inventa, se nota enseguida.

## Instalación

```bash
git clone https://github.com/carolinamolfino-bsas/CaroM-RAG-Local.git
cd CaroM-RAG-Local

python3.12 -m venv .venv
source .venv/bin/activate          # Windows: .venv\Scripts\activate
pip install -r requirements-dev.txt

cp .env.example .env               # Windows: copy .env.example .env
```

`requirements.txt` instala el cliente del Módulo 1 desde GitHub (`carom-llm-client @ git+https://...@v1.2.0`), así que hace falta tener `git`. LangChain se instala por módulos (`langchain-core`, `langchain-chroma`, `langchain-text-splitters`...): son las piezas del paquete `langchain` que usa el proyecto.

### Modelos locales (Ollama)

1. Instalá Ollama desde https://ollama.com/download.
2. Descargá los dos modelos (una sola vez):
   ```bash
   ollama pull bge-m3        # embeddings multilingües (~1,2 GB)
   ollama pull qwen2.5:7b    # LLM (~4,7 GB; conviene tener 8 GB de RAM libres)
   ```
3. El `.env.example` ya viene configurado para Ollama, así que no hay que tocar nada.

Para usar la API de OpenAI, en el `.env` borrá `OPENAI_BASE_URL`, cargá tu `OPENAI_API_KEY` y elegí `OPENAI_MODEL=gpt-4o-mini`. Si querés también embeddings de OpenAI, poné `EMBEDDINGS_PROVIDER=openai` y `EMBEDDINGS_MODEL=text-embedding-3-small`.

## Cómo ejecutarlo

### 1. Ingesta

```bash
python ingest.py               # indexa data/ en ./vectorstore
python ingest.py               # segunda vez: "Sin cambios", no recalcula embeddings
python ingest.py --rebuild     # fuerza la reindexación completa
python ingest.py --ver-chunks  # muestra los fragmentos (tokens y sección) sin indexar
```

### 2. Prueba End-to-End

```bash
python main.py
```

Corre la ingesta (o la reutiliza si nada cambió) y hace dos pruebas **verificadas automáticamente**:

| Prueba | Pregunta | Comportamiento esperado |
|---|---|---|
| Respuesta en los documentos | ¿Cuál es el tiempo máximo de respuesta de la guardia ante un incidente SEV1? | Responde "10 minutos" y cita `02_gestion_de_incidentes.md` |
| Pregunta trampa | ¿Cuál es el presupuesto anual de infraestructura de Kaleido Pagos? | Responde "No lo sé", sin referencias |

Termina con `Resumen: 2/2 pruebas con el comportamiento esperado` (código de salida 0).

Otras opciones:

```bash
python main.py --pregunta "¿Qué días se puede desplegar el Motor de cobros?"
python main.py --guardar ejemplos/ejecucion_ollama.json   # registro de la corrida real
```

### 3. Desde código

```python
import asyncio
from dotenv import load_dotenv
from rag import get_rag_response

load_dotenv()
r = asyncio.run(get_rag_response("¿Cuándo es el congelamiento de cambios?"))
print(r.respuesta)
for ref in r.referencias:
    print(ref.fuente, ref.seccion, ref.relevancia)
```

`get_rag_response` devuelve un `RAGResponse`:

```json
{
  "pregunta": "¿Cuál es el tiempo máximo de respuesta de la guardia ante un incidente SEV1?",
  "respuesta": "El tiempo máximo de respuesta de la guardia ante un incidente SEV1 es **10 minutos** desde la alerta.",
  "encontrada": true,
  "referencias": [
    {"fragmento": 1, "fuente": "02_gestion_de_incidentes.md", "seccion": "Niveles de severidad",
     "relevancia": 0.71, "extracto": "## Niveles de severidad ### SEV1: crítico Un SEV1 es..."}
  ],
  "avisos": []
}
```

> Respuesta y relevancia reales de la corrida con Ollama (`qwen2.5:7b` + `bge-m3`); el extracto está acortado. El registro completo está en [`ejemplos/ejecucion_ollama.json`](ejemplos/ejecucion_ollama.json).

### 4. Tests y controles de calidad

```bash
python -m pytest -q   # 25 tests, sin red, sin Ollama y sin API keys
ruff check .
mypy
```

Los tests usan ChromaDB real en una carpeta temporal, embeddings determinísticos (bolsa de palabras) y un LLM simulado. Cubren:

- el chunking y la persistencia: no reindexa si nada cambió, y sí cuando cambia un documento o el modelo de embeddings;
- la protección contra embeddings distintos entre indexar y consultar;
- la recuperación con `top_k`;
- la respuesta con referencias y la pregunta trampa;
- las referencias inventadas, el JSON inválido con reintento y los errores del proveedor;
- un **End-to-End de `main.py` por HTTP** contra un servidor local que imita a Ollama (`/api/embed` y `/v1/chat/completions`).

## Variables de entorno

| Variable | Default | Descripción |
|---|---|---|
| `LLM_PROVIDER` | `openai` | `openai` (incluye Ollama) o `anthropic` |
| `OPENAI_BASE_URL` | — | `http://localhost:11434/v1` para Ollama; vacío = API oficial de OpenAI |
| `OPENAI_API_KEY` | — | Con Ollama, cualquier texto (ej. `ollama`) |
| `OPENAI_MODEL` | `gpt-4o-mini` | El `.env.example` usa `qwen2.5:7b` |
| `LLM_TEMPERATURE` | `0.7` | El `.env.example` usa `0`, para respuestas más fieles al contexto |
| `LLM_OUTPUT_RETRIES` | `3` | Reintentos si el LLM devuelve un JSON inválido |
| `EMBEDDINGS_PROVIDER` | `ollama` | `ollama` u `openai` |
| `EMBEDDINGS_MODEL` | `bge-m3` | Con OpenAI: `text-embedding-3-small` |
| `RAG_CHUNK_SIZE` | `500` | Tokens por fragmento (mínimo 500) |
| `RAG_CHUNK_OVERLAP` | `50` | Tokens compartidos entre fragmentos |
| `RAG_TOP_K` | `4` | Fragmentos enviados al LLM (entre 3 y 5) |
| `RAG_PERSIST_DIR` | `vectorstore` | Carpeta de ChromaDB |
| `RAG_TOKENIZER` | `tiktoken` | `aproximado` sólo si no hay internet la primera vez |

## Decisiones de diseño

**Chunking por tokens (`ingest.py`).** `RecursiveCharacterTextSplitter` con `chunk_size=500` y `chunk_overlap=50`, medidos en **tokens** con `tiktoken` (`cl100k_base`), no en caracteres. Los separadores siguen la estructura del Markdown: primero corta antes de cada sección (`##`), después por párrafos, líneas y oraciones. Por eso algunos fragmentos quedan más cortos que 500: el corte respeta el final de la sección en lugar de partir una idea por la mitad. Cada fragmento guarda su archivo, su sección y un id estable (`archivo#n`).

**Persistencia inteligente.** La colección de ChromaDB guarda un **manifiesto**: hash SHA-256 de cada documento, modelo de embeddings y parámetros de chunking. Si al volver a ejecutar nada cambió, la ingesta se omite y no se recalcula ni un embedding. Si cambió un documento o la configuración, se reconstruye la colección.

**Embeddings coincidentes.** El modelo con el que se indexó queda registrado en la colección (`embedding_id`, ej. `ollama:bge-m3`). Si después se consulta con otro, el sistema lo detecta y se niega a responder con `EmbeddingMismatchError`, en vez de devolver resultados al azar. Ese es el error #1 de RAG que menciona la consigna. Se eligió `bge-m3` porque es multilingüe (los documentos están en español) y admite hasta 8192 tokens, así que un fragmento de 500 nunca se trunca.

**Contexto acotado.** `top_k` está validado entre 3 y 5 (default 4), para evitar el "contexto infinito" y el efecto *lost in the middle*. La búsqueda usa distancia coseno, y cada referencia informa su relevancia (de 0 a 1).

**Generación grounded (`rag.py`).** La cadena LCEL es explícita en `build_rag_chain()`:

```python
RunnableParallel(pregunta=RunnablePassthrough(), fragmentos=retriever)
| RunnablePassthrough.assign(contexto=formatear_contexto)                  # transformador de documentos
| RunnablePassthrough.assign(salida_llm=prompt | llm | PydanticOutputParser)
| armar_respuesta                                                          # validación de referencias
```

- **Prompt como "filtro de veracidad".** Ordena responder solo con el CONTEXTO, decir "No lo sé" si no alcanza y citar los números `[n]` de los fragmentos usados. El formato JSON lo inyecta `PydanticOutputParser.get_format_instructions()`.
- **Referencias verificadas, no confiadas.** Si el modelo cita un fragmento que no existe, se descarta la cita. Si afirma una respuesta sin citar ningún fragmento válido, **se descarta la respuesta entera** y se devuelve "No lo sé": una respuesta sin respaldo no es *grounded*.
- **Sin fragmentos, sin LLM.** Si la búsqueda no devuelve nada, `RunnableBranch` responde "No lo sé" sin llamar al modelo.
- **JSON inválido.** `.with_retry()` reintenta ante `OutputParserException`. Si se agotan los intentos, `get_rag_response` lanza un `RAGError` controlado (`tipo`: `salida_invalida`, `proveedor`, `indice`, `embeddings` o `entrada_invalida`). Nunca deja escapar una excepción cruda.

**Asincronía.** `get_rag_response` es `async`: el embedding de la pregunta, la búsqueda en ChromaDB y la llamada al LLM se hacen con `ainvoke`/`aembed_query`. `main.py` ejecuta las dos pruebas en paralelo con `asyncio.gather`.

**Reutilización.** La configuración del LLM (proveedor, key, modelo, `OPENAI_BASE_URL`) viene de `llm_client`, el cliente de la Pre-entrega 1, igual que en la Pre-entrega 2.

**Secretos.** Las keys sólo se leen del `.env`, que está en `.gitignore`. El repo solo incluye `.env.example`, sin claves reales. La carpeta `vectorstore/` tampoco se versiona, porque se genera con `python ingest.py`.

## Validación

**Ejecución real (20/11/2026).** Se corrió en Windows 11 con Python 3.12.10, Ollama 0.40.16, embeddings `bge-m3` y LLM `qwen2.5:7b`, todo local y sin API keys:

```
Índice: 13 fragmentos de 4 documentos (no existía el índice).
LLM: openai/qwen2.5:7b · embeddings: ollama:bge-m3 · top_k=4

Respuesta en los documentos
Pregunta: ¿Cuál es el tiempo máximo de respuesta de la guardia ante un incidente SEV1?
Respuesta: El tiempo máximo de respuesta de la guardia ante un incidente SEV1 es **10 minutos** desde la alerta.
  [1] 02_gestion_de_incidentes.md · Niveles de severidad · relevancia 0.71
✔ Respondió con el dato correcto y citó la fuente.

Pregunta trampa (no está en los documentos)
Pregunta: ¿Cuál es el presupuesto anual de infraestructura de Kaleido Pagos?
Respuesta: No lo sé. Esa información no está en los documentos disponibles.
✔ No alucinó: respondió 'No lo sé'.

Resumen: 2/2 pruebas con el comportamiento esperado.
```

- El fragmento correcto quedó **primero** en la búsqueda (relevancia 0,71), y el modelo citó solo esa fuente.
- Ante la pregunta trampa, el modelo no inventó un presupuesto: respondió "No lo sé" y no dio referencias.
- El registro completo, con las referencias y los extractos, está en [`ejemplos/ejecucion_ollama.json`](ejemplos/ejecucion_ollama.json).

**Tests automáticos.** Los 25 tests corren sin red ni modelos, así que también pasan en GitHub Actions. Incluyen ChromaDB real y un End-to-End de `main.py` con llamadas HTTP reales a un servidor que imita la API de Ollama, con embeddings determinísticos y respuestas simuladas.

**Alcance.** La corrida real valida las dos pruebas de la consigna con un modelo local de 7B parámetros. Un modelo más grande, como GPT-4o, puede dar respuestas mejor redactadas, pero el comportamiento que se verifica es el mismo: responder solo con el contexto y decir "No lo sé" cuando no está.

[Ollama]: https://ollama.com
