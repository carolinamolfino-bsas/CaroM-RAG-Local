"""Servidor HTTP que imita a Ollama: /api/embed (embeddings) y /v1/chat/completions (LLM).

Permite correr main.py de punta a punta, con llamadas HTTP reales, sin instalar
Ollama. El "LLM" falso responde como lo haría un modelo obediente: si el
contexto contiene el dato pedido, lo cita; si no, dice "No lo sé".
"""

from __future__ import annotations

import json
import re
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any

from tests.fakes import HashingEmbeddings

_EMB = HashingEmbeddings()


def _respuesta_llm(prompt_usuario: str) -> dict[str, Any]:
    pregunta = prompt_usuario.split("PREGUNTA:")[-1]
    if "SEV1" in pregunta:
        for numero, bloque in re.findall(r"\[(\d+)\] fuente:(.*?)(?=\n\n---\n\n|\Z)",
                                         prompt_usuario, flags=re.S):
            if "SEV1" in bloque and "10 minutos" in bloque:
                return {"respuesta": "La guardia tiene 10 minutos desde la alerta.",
                        "encontrada": True, "fragmentos_usados": [int(numero)]}
    return {"respuesta": "No lo sé", "encontrada": False, "fragmentos_usados": []}


class FakeOllamaServer:
    def __init__(self) -> None:
        self.paths: list[str] = []
        self.chat_requests: list[dict[str, Any]] = []
        servidor = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args: Any) -> None:
                pass

            def _json(self, data: dict) -> None:
                body = json.dumps(data).encode()
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def do_POST(self) -> None:
                body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
                servidor.paths.append(self.path)
                if self.path == "/api/embed":
                    textos = body["input"] if isinstance(body["input"], list) else [body["input"]]
                    self._json({"model": body["model"],
                                "embeddings": [_EMB.embed_query(t) for t in textos]})
                elif self.path == "/v1/chat/completions":
                    servidor.chat_requests.append(body)
                    contenido = json.dumps(_respuesta_llm(body["messages"][-1]["content"]),
                                           ensure_ascii=False)
                    self._json({
                        "id": "c", "object": "chat.completion", "created": 0,
                        "model": body["model"],
                        "choices": [{"index": 0, "finish_reason": "stop",
                                     "message": {"role": "assistant", "content": contenido}}],
                        "usage": {"prompt_tokens": 10, "completion_tokens": 10,
                                  "total_tokens": 20},
                    })
                else:
                    self.send_response(404)
                    self.end_headers()

        self._httpd = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.url = f"http://127.0.0.1:{self._httpd.server_address[1]}"
        self._thread = threading.Thread(target=self._httpd.serve_forever, daemon=True)

    def __enter__(self) -> FakeOllamaServer:
        self._thread.start()
        return self

    def __exit__(self, *exc: object) -> None:
        self._httpd.shutdown()
        self._httpd.server_close()
