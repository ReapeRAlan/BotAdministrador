# TheAdmingestor_bot/ollama_integration.py
import time
import json
import logging
from typing import List, Dict
import requests
import uuid
from config import OLLAMA_API_URL

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Configuración avanzada de contexto
MAX_CONTEXT_LENGTH = 2000  # Longitud máxima del contexto en caracteres
CONTEXT_TTL = 300  # Tiempo de vida del contexto en segundos

context_store = {}

class ContextManager:
    """Gestor avanzado de contexto de usuario, incluyendo IDs únicos."""
    
    @staticmethod
    def get_context(user_id: str) -> Dict:
        now = int(time.time())
        if user_id not in context_store:
            context_store[user_id] = {
                'messages': [],
                'last_active': now,
                'accion_ids': set()  # Conjunto de IDs únicos de acciones
            }
        elif (now - context_store[user_id]['last_active']) > CONTEXT_TTL:
            context_store[user_id] = {
                'messages': [],
                'last_active': now,
                'accion_ids': set()
            }
        return context_store[user_id]

    @staticmethod
    def add_message(user_id: str, role: str, content: str):
        context = ContextManager.get_context(user_id)
        context['messages'].append({'role': role, 'content': content})
        context['last_active'] = int(time.time())
        total_length = sum(len(m['content']) for m in context['messages'])
        while total_length > MAX_CONTEXT_LENGTH and len(context['messages']) > 1:
            removed = context['messages'].pop(0)
            total_length -= len(removed['content'])

    @staticmethod
    def add_action_id(user_id: str, accion_id: str) -> bool:
        """Registra un ID de acción en el contexto, evitando duplicados."""
        context = ContextManager.get_context(user_id)
        if accion_id in context['accion_ids']:
            logger.warning(f"⚠️ Intento de registrar acción duplicada con ID: {accion_id}")
            return False
        context['accion_ids'].add(accion_id)
        logger.info(f"✅ Acción registrada con ID: {accion_id}")
        return True

    @staticmethod
    def is_action_id_registered(user_id: str, accion_id: str) -> bool:
        """Verifica si una acción ya fue registrada en el contexto."""
        context = ContextManager.get_context(user_id)
        return accion_id in context['accion_ids']

# Actualizamos el prompt para incluir el campo "nota"
system_prompt = """Eres un asistente comercial. Tu única tarea es interpretar los mensajes del usuario para extraer **acciones** (ventas, compras, etc.) y responder **exclusivamente** con **un JSON** que contenga **exactamente** estos parámetros:

{
    "acciones": [
        {
            "tipo": "venta|compra|modificar|eliminar",
            "producto": "nombre_especifico",
            "cantidad": número,
            "precio": número,
            "unidad": "kg|unidades|litros|toneladas|cajas",
            "cliente": "nombre (opcional)",
            "nota": "texto (opcional)",
            "transaccion_id": "ID (solo para modificar/eliminar)"
        }
    ]
}

**REGLAS IMPORTANTES**:
1. **No incluyas texto extra fuera del JSON** (ni bloques de código, ni explicaciones, ni texto en Markdown).  
2. **Usa la estructura y los nombres exactamente como se muestran**. No cambies "acciones", ni los nombres de los campos.  
3. **No uses otras llaves ni campos** que no estén en la plantilla.  
4. **Si el usuario menciona varias acciones** (por ejemplo: “Vendí 10 kg de maíz… y compré 20 kg de frijol… además vendí 5 kg de arroz…”), **devuelve un arreglo con múltiples objetos** en la clave `"acciones"`.  
5. **Si el usuario menciona una sola acción**, devuelve un solo objeto en `"acciones"`.  
6. El campo `"transaccion_id"` se usa solo para modificar/eliminar. Si no se usa, puede ir vacío o en null.  
7. **No incluyas** ningún texto adicional fuera del JSON. **El JSON debe ser la única salida**.

**EJEMPLOS**:
1. **Mensaje múltiple**: “Vendí 10 kg de maíz a $5/kg a Juan y compré 20 kg de frijol a $3.5/kg a Pedro, además vendí 5 kg de arroz a $2/kg a María.”  
   - Tu salida (JSON) deberá contener 3 objetos en `"acciones"`: uno para la venta de maíz, uno para la compra de frijol y uno para la venta de arroz.

2. **Mensaje único**: “Vendí 2 toneladas de elote para Juan, le cobré 1900$ por tonelada nota: que sea de calidad.”  
   - Tu salida (JSON) deberá contener 1 objeto en `"acciones"` con `"tipo": "venta"`, `"producto": "elote"`, `"cantidad": 2`, `"precio": 1900`, `"unidad": "toneladas"`, `"cliente": "Juan"`, `"nota": "que sea de calidad"`, `"transaccion_id": ""` o `null`.

**Recuerda**: 
- El JSON es **estricto**.  
- **No** uses Markdown ni texto extra.  
- **No** incluyas tildes en los campos clave (por ejemplo, no cambies `"transaccion_id"` por `"transacción_id"`).  
- Si el usuario pide modificar/eliminar, incluye el `"transaccion_id"` apropiado.  

SIGUE ESTAS INSTRUCCIONES **AL PIE DE LA LETRA** Y **NO AÑADAS** MÁS DETALLES FUERA DEL JSON.

"""

def repair_json(response: str) -> List[Dict]:
    """Repara JSON fragmentado y extrae múltiples JSON en caso de respuesta incompleta."""
    try:
        response = response.replace("\n", "").replace(" ", "")
        json_start = response.find("{")
        json_end = response.rfind("}") + 1
        if json_start == -1 or json_end == 0:
            raise ValueError("No se encontró JSON válido en la respuesta")
        json_clean = response[json_start:json_end]
        try:
            json_data = json.loads(json_clean)
            return [json_data] if isinstance(json_data, dict) else json_data
        except json.JSONDecodeError as e:
            logger.error(f"Error reparando JSON: {e}")
            return [{"error": "No se pudo reconstruir JSON"}]
    except Exception as e:
        logger.error(f"Error al intentar reparar JSON: {str(e)}")
        return [{"error": "Respuesta no analizable"}]

def process_ollama_response(response: Dict) -> List[Dict]:
    """
    Procesa y normaliza la respuesta de Ollama, asegurando que cada acción tenga transaccion_id.
    Aquí se normaliza el campo 'tipo' y se espera que la respuesta incluya el campo 'nota'.
    """
    try:
        content_fragments = [
            msg["message"]["content"]
            for msg in response
            if "message" in msg and "content" in msg["message"]
        ]
        content = "".join(content_fragments).strip()
        logger.info(f"Respuesta combinada de Ollama: {content}")
        json_data = repair_json(content)
        if not json_data or json_data == {}:
            logger.error("⚠️ Respuesta JSON vacía")
            return [{"error": "Respuesta vacía desde Ollama"}]
        expected_keys = {"tipo", "producto", "cantidad", "precio", "unidad", "cliente", "nota", "transaccion_id"}
        
        def normalizar(item: Dict) -> Dict:
            # No asignamos transaccion_id aquí; se espera que se deje vacío para asignarlo después
            tipo = item.get("tipo", "").strip().lower()
            if tipo in ["comprar"]:
                item["tipo"] = "compra"
            elif tipo in ["vender"]:
                item["tipo"] = "venta"
            return item

        if isinstance(json_data, dict):
            if "acciones" in json_data and isinstance(json_data["acciones"], list):
                return [normalizar(a) for a in json_data["acciones"]]
            elif expected_keys.issubset(json_data.keys()):
                return [normalizar(json_data)]
            else:
                logger.error("⚠️ Estructura de 'acciones' no encontrada en JSON")
                return [{"error": "Formato de respuesta inválido"}]
        elif isinstance(json_data, list):
            valid_items = []
            for item in json_data:
                if isinstance(item, dict):
                    if "acciones" in item and isinstance(item["acciones"], list):
                        valid_items.extend([normalizar(a) for a in item["acciones"]])
                    elif expected_keys.issubset(item.keys()):
                        valid_items.append(normalizar(item))
            if valid_items:
                return valid_items
            else:
                logger.error("⚠️ Estructura de 'acciones' no encontrada en JSON")
                return [{"error": "Formato de respuesta inválido"}]
        else:
            logger.error("⚠️ Respuesta JSON inesperada")
            return [{"error": "Formato de respuesta inesperado"}]
    except Exception as e:
        logger.error(f"Error procesando respuesta: {str(e)}", exc_info=True)
        return [{"error": "Error interno procesando respuesta"}]

def analizar_mensaje(user_id: str, usuario: str, mensaje: str) -> List[Dict]:
    """
    Analiza mensajes usando Ollama con gestión de contexto.
    Se incluye automáticamente el nombre o número del usuario en el mensaje enviado a Ollama.
    Antes de procesar, se limpia el conjunto de IDs para que cada mensaje se procese de forma independiente.
    """
    try:
        context = ContextManager.get_context(user_id)
        # Limpiar los IDs para que cada mensaje se procese de forma independiente
        context['accion_ids'].clear()
        id_registrados = list(context['accion_ids'])
        historial_ids = ", ".join(id_registrados) if id_registrados else "ninguno"
        messages = [
            {"role": "system", "content": system_prompt},
            *context['messages'][-3:],
            {"role": "user", "content": f"Usuario: {usuario}. Transacciones previas registradas con ID: {historial_ids}. Ahora, procesa este mensaje: {mensaje}"}
        ]
        response = requests.post(
            f"{OLLAMA_API_URL}/api/chat",
            json={
                "model": "deepseek-r1",
                "messages": messages,
                "format": "json",
                "options": {
                    "temperature": 0.1,
                    "num_ctx": 4096
                }
            },
            timeout=20
        )
        raw_response = response.text
        logger.error(f"📝 Respuesta cruda completa:\n{raw_response}")
        if response.status_code != 200:
            logger.error(f"❌ Error Ollama: {response.status_code} - {raw_response}")
            return [{"error": "Error de conexión con el servicio de análisis"}]
        try:
            json_response = json.loads(f"[{raw_response.replace('}\n', '},')[:-1]}]")
        except json.JSONDecodeError as e:
            logger.error(f"Error decodificando JSON: {e}\nContenido: {raw_response}")
            return [{"error": "Formato de respuesta inválido desde Ollama"}]
        processed_actions = process_ollama_response(json_response)
        # En este punto, la respuesta de Deepseek incluye la información solicitada,
        # pero el campo transaccion_id se deja vacío para asignarlo más adelante.
        ContextManager.add_message(user_id, "assistant", json.dumps(processed_actions))
        ContextManager.add_message(user_id, "user", mensaje)
        return processed_actions
    except requests.exceptions.Timeout:
        logger.error("⚠️ Timeout al conectar con Ollama")
        return [{"error": "Tiempo de espera agotado"}]
    except Exception as e:
        logger.error(f"⚠️ Error crítico: {str(e)}", exc_info=True)
        return [{"error": "Error interno del sistema"}]
