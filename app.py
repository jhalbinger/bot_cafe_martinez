from flask import Flask, request, jsonify
import openai
import os
from dotenv import load_dotenv
import json
from collections import defaultdict, deque
import requests
import traceback

load_dotenv()

# === Credenciales de OpenAI ===
api_key = os.getenv("OPENAI_API_KEY")
project_id = os.getenv("OPENAI_PROJECT_ID")
organization_id = os.getenv("OPENAI_ORG_ID")

client = openai.OpenAI(
    api_key=api_key,
    project=project_id,
    organization=organization_id
)

app = Flask(__name__)

print("✅ Arrancó el app.py de Café Martínez 🧾")

# === CONTEXTO FIJO ===
txt_path = "cafe_martinez.txt"
if os.path.exists(txt_path):
    with open(txt_path, "r", encoding="utf-8") as f:
        CONTEXTO_COMPLETO = f.read()
else:
    print("⚠️ No se encontró el archivo cafe_martinez.txt")
    CONTEXTO_COMPLETO = ""

# === Memoria en RAM por usuario ===
historial_conversacion = defaultdict(lambda: deque(maxlen=4))
estado_usuario = {}
producto_usuario = {}

TRIGGER_DERIVACION = [
    "hablar con alguien", "pasar con", "asesor", "humano",
    "persona", "quiero hablar", "me pasas con alguien", "que me llamen",
    "quiero que me contacten", "contacto", "franquicia", "franquicias"
]

@app.route("/webhook", methods=["POST"])
def responder():
    try:
        try:
            datos = request.get_json(force=True)  # 🔧 fuerza el parseo del JSON aunque falte header
        except Exception as err:
            print("⚠️ Error al leer JSON:", err)
            return jsonify({"respuesta": "No pude leer tu consulta 🙈"}), 400

        if not datos:
            print("⚠️ No llegó ningún dato en el body")
            return jsonify({"respuesta": "No se recibió información válida 🙈"}), 400

        print("🔎 JSON recibido desde Watson:")
        print(json.dumps(datos, indent=2, ensure_ascii=False))

        mensaje_usuario = datos.get("consulta", "").lower().strip()
        numero_cliente = datos.get("numero", "").strip() or "anon"

        if not mensaje_usuario:
            return jsonify({"error": "No se recibió ninguna consulta"}), 400

        if estado_usuario.get(numero_cliente) == "derivado":
            return responder_normal(mensaje_usuario, numero_cliente)

        if any(trigger in mensaje_usuario for trigger in TRIGGER_DERIVACION):
            return derivar_asesor(numero_cliente)

        if estado_usuario.get(numero_cliente) == "esperando_confirmacion":
            positivos = ["sí", "si", "dale", "ok", "quiero", "confirmo"]
            negativos = ["no", "no quiero", "no gracias", "después", "mas tarde", "en otro momento"]

            if mensaje_usuario in positivos:
                return derivar_asesor(numero_cliente)
            elif mensaje_usuario in negativos:
                estado_usuario.pop(numero_cliente, None)
                return jsonify({"respuesta": "👌 Sin problema, cualquier cosa podés consultarme por acá cuando quieras."})
            else:
                return responder_normal(mensaje_usuario, numero_cliente)

        prod_detectado = detectar_producto_mencionado(mensaje_usuario)
        if prod_detectado:
            producto_usuario[numero_cliente] = prod_detectado

        consultas_previas = [msg for rol, msg in historial_conversacion[numero_cliente] if rol == "user"]
        cantidad_consultas_ahora = len(consultas_previas) + 1

        historial_conversacion[numero_cliente].append(("user", mensaje_usuario))
        respuesta_normal = responder_normal(mensaje_usuario, numero_cliente)

        if cantidad_consultas_ahora == 3 and estado_usuario.get(numero_cliente) != "derivado":
            estado_usuario[numero_cliente] = "esperando_confirmacion"
            extra = "\n\n✅ *Si querés, puedo pedir que un asesor te contacte para ayudarte. ¿Querés que te llamen?*"
            respuesta_data = json.loads(respuesta_normal.get_data())
            respuesta_data["respuesta"] += extra
            return jsonify(respuesta_data)

        return respuesta_normal

    except Exception as e:
        traceback.print_exc()
        print("💥 Error detectado:", e)
        return jsonify({"respuesta": "Estoy tardando en procesar tu consulta, intentá de nuevo en unos segundos 🙏"}), 200

def responder_normal(mensaje_usuario, numero_cliente):
    system_prompt = (
        "Sos un asistente virtual de *Café Martínez* ☕.\n\n"
        "➡️ **Reglas de estilo (OBLIGATORIAS en TODAS las respuestas):**\n"
        "- Formato WhatsApp SIEMPRE: breve (máx 4-5 líneas).\n"
        "- Usá *un solo asterisco* para resaltar palabras clave (productos, enlaces, beneficios).\n"
        "- Usá ✅ para listas.\n"
        "- Agregá SALTOS DE LÍNEA para que quede ordenado.\n"
        "- Máximo 2 emojis por respuesta, nunca más.\n"
        "- Saludá SOLO si el usuario inicia con un saludo genérico.\n"
        "- Si el usuario hace una pregunta concreta (ej: 'horarios', 'qué cafés venden'), respondé DIRECTO sin saludo extra.\n"
        "- No inventes información: respondé solo usando el CONTEXTO.\n"
        "- Si algo no está en el CONTEXTO o varía por sucursal, indicá usar el buscador de *Sucursales* o proponé derivación a un asesor.\n"
        "- Para sucursales: compartí el link de *Sucursales* como texto plano.\n"
        "- Para tienda/productos: compartí el link de *Nuestro Café* como texto plano.\n"
        "- No uses links en formato [texto](url); siempre texto plano."
    )

    historial = list(historial_conversacion[numero_cliente])
    user_prompt = (
        f"CONTEXTO:\n{CONTEXTO_COMPLETO}\n\n"
        "Conversación previa:\n\n"
    )
    for rol, msg in historial:
        user_prompt += f"{rol.upper()}: {msg}\n"
    user_prompt += f"\nUSUARIO (nuevo): {mensaje_usuario}\n\n"
    user_prompt += (
        "⚠️ Instrucciones específicas:\n"
        "- Si piden *horarios/dirección*: referir a https://www.cafemartinez.com/sucursales/\n"
        "- Si piden *tienda/productos*: referir a https://www.cafemartinez.com/nuestro-cafe/\n"
        "- Si piden *delivery*: aclarar que depende de la zona y que pueden usar apps (ej. PedidosYa) cuando esté disponible.\n"
        "- Si piden *beneficios/app*: mencionar Club Café Martínez (app oficial).\n"
        "- Si piden *franquicias*: dar orientación general y ofrecer derivación.\n"
    )

    respuesta = client.chat.completions.create(
        model="gpt-4o",
        messages=[
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt}
        ]
    )

    respuesta_llm = respuesta.choices[0].message.content.strip()
    historial_conversacion[numero_cliente].append(("bot", respuesta_llm))

    return jsonify({"respuesta": respuesta_llm})

def derivar_asesor(numero_cliente):
    estado_usuario[numero_cliente] = "derivado"
    producto = producto_usuario.get(numero_cliente, "No especificado")
    mensaje_dueño = f"Consulta asociada: {producto}"
    return enviar_derivacion(numero_cliente, mensaje_dueño)

def enviar_derivacion(numero_cliente, mensaje_dueño):
    try:
        resp = requests.post(
            "https://derivacion-humano.onrender.com/derivar-humano",
            json={"numero": numero_cliente, "consulta": mensaje_dueño}
        )
        if resp.status_code == 200:
            return jsonify({"respuesta": "✅ Ya avisé a un asesor para que te contacte. Mientras tanto, sigo disponible 😉"})
        else:
            print("❌ Error derivando:", resp.text)
            return jsonify({"respuesta": "❌ Hubo un problema. Si querés, probamos de nuevo o te comparto el buscador de sucursales."})
    except Exception as e:
        traceback.print_exc()
        print("❌ Excepción derivando:", e)
        return jsonify({"respuesta": "❌ No pude avisar al asesor por ahora. Probemos más tarde."})

def detectar_producto_mencionado(texto):
    items = [
        "espresso", "ristretto", "americano", "latte", "capuchino", "mocca",
        "frappé", "cold brew", "café frío", "cafetería",
        "medialuna", "tostado", "sándwich", "sandwich", "waffle", "torta", "budín", "muffin",
        "cápsulas", "capsulas", "nespresso", "dolce gusto", "molido", "grano", "selecto", "origen",
        "franquicia", "franquicias", "club", "beneficios", "app"
    ]
    texto_lower = texto.lower()
    for p in items:
        if p in texto_lower:
            return p.title()
    return None

@app.route("/")
def index():
    return "✅ Webhook activo (Café Martínez)."

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000)
