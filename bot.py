import logging
import shlex
import uuid
import os
import tempfile
import pandas as pd
import sqlite3
from datetime import datetime, date
from telegram import (
    Update,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    ReplyKeyboardMarkup,
    ReplyKeyboardRemove
)
from telegram.ext import (
    ApplicationBuilder,
    CommandHandler,
    MessageHandler,
    ContextTypes,
    filters,
    ConversationHandler,
    CallbackQueryHandler
)
from config import TOKEN, DATABASE_PATH
from database import (
    connect_db,
    agregar_transaccion,
    modificar_transaccion,
    eliminar_transaccion,
    obtener_historial_df,
    obtener_inventario_df
)
from ollama_integration import analizar_mensaje, ContextManager

logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO
)
logger = logging.getLogger(__name__)

# Inicializar base de datos
connect_db()

# -----------------------
# Registro de usuario por chat
# -----------------------
def registrar_usuario(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Registra el usuario en función del chat en el que se encuentre.
    Se guarda en context.bot_data un diccionario con la relación chat_id -> datos del usuario.
    """
    chat = update.effective_chat
    user = update.effective_user
    if "usuarios" not in context.bot_data:
        context.bot_data["usuarios"] = {}
    context.bot_data["usuarios"][chat.id] = {
        "user_id": user.id,
        "username": user.username,
        "full_name": user.full_name,
        "chat_type": chat.type,
    }
    logger.info("Registrado usuario %s en el chat %s", user.full_name, chat.id)

# -----------------------
# Funciones utilitarias
# -----------------------
def parse_command_arguments(text: str, command: str) -> list:
    return shlex.split(text[len(command):].strip())

def obtener_historial_text(user_id: str) -> str:
    df = obtener_historial_df(user_id)
    if df.empty:
        return "📭 No hay registros en el historial"
    df['cantidad'] = df['cantidad'].apply(lambda x: f"{x}")
    df['precio'] = df['precio'].apply(lambda x: f"${x:,.2f}")
    return pd.DataFrame.to_string(
        df[['tipo', 'producto', 'cantidad', 'unidad', 'precio', 'cliente', 'notas', 'fecha']],
        index=False
    )

def obtener_ultimo_transaccion(user_id: str) -> dict:
    df = obtener_historial_df(user_id)
    if df.empty:
        return {}
    return df.iloc[0].to_dict()

def calcular_ganancias(user_id: str) -> dict:
    with sqlite3.connect(DATABASE_PATH) as conn:
        cursor = conn.cursor()
        cursor.execute(
            """
            SELECT tipo, SUM(cantidad * precio) FROM transacciones
            WHERE usuario = ? AND tipo IN ('venta', 'compra')
            GROUP BY tipo
            """, (user_id,)
        )
        rows = cursor.fetchall()
    ventas = 0.0
    compras = 0.0
    for tipo, total in rows:
        if tipo.lower() == 'venta':
            ventas = total if total is not None else 0.0
        elif tipo.lower() == 'compra':
            compras = total if total is not None else 0.0
    return {'ventas': ventas, 'compras': compras, 'neto': ventas - compras}

# -----------------------
# Comandos básicos
# -----------------------
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    registrar_usuario(update, context)
    await update.message.reply_text(
        "🚀 Bienvenido al Gestor Comercial Inteligente\n\n"
        "Registra ventas, compras, modificaciones o eliminaciones mediante comandos o flujo interactivo.\n"
        "Ejemplo de texto libre: 'Vendí 150kg de maíz a $5.2/kg a Juan'\n\n"
        "Usa /help para ver todos los comandos."
    )

async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    help_text = (
        "📘 Guía de Comandos:\n\n"
        "▫️ /venta - Inicia un asistente interactivo para registrar una venta.\n"
        "▫️ /compra - Inicia un asistente interactivo para registrar una compra.\n"
        "▫️ /modificar - Inicia un asistente para modificar una transacción.\n"
        "▫️ /eliminar - Elimina una transacción (se confirma con botones).\n"
        "▫️ /ganancias - Muestra el balance financiero.\n"
        "▫️ /historial - Muestra las transacciones recientes.\n"
        "▫️ /inventario - Muestra todo el inventario.\n"
        "▫️ /exportar_historial - Exporta el historial a Excel.\n"
        "▫️ /filtrar_historial <dia|mes> <valor> - Filtra el historial.\n"
        "▫️ /ultimo_pedido - Muestra la última transacción.\n"
        "▫️ /corte - Genera un informe de las transacciones del día.\n\n"
        "También puedes enviar mensajes en texto libre para que la IA los procese."
    )
    await update.message.reply_text(help_text)

async def ganancias(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    try:
        user_id = str(update.message.from_user.id)
        ganancia = calcular_ganancias(user_id)
        respuesta = (
            f"📊 Balance Financiero:\n"
            f"Total Ventas: ${ganancia['ventas']:.2f}\n"
            f"Total Compras: ${ganancia['compras']:.2f}\n"
            f"Ganancias Netas: ${ganancia['neto']:.2f}"
        )
        await update.message.reply_text(respuesta)
    except Exception as e:
        logger.error(f"Error en ganancias: {str(e)}", exc_info=True)
        await update.message.reply_text("❌ Error obteniendo balance financiero")

# -----------------------
# Función para ver el inventario
# -----------------------
async def inventario(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    try:
        df = obtener_inventario_df()
        if df.empty:
            mensaje = "📭 No hay inventario registrado."
        else:
            tabla = df.to_string(index=False)
            mensaje = f"<pre>{tabla}</pre>"
        keyboard = [[InlineKeyboardButton("Actualizar", callback_data="refrescar_inventario")]]
        reply_markup = InlineKeyboardMarkup(keyboard)
        await update.message.reply_text(mensaje, parse_mode='HTML', reply_markup=reply_markup)
    except Exception as e:
        logger.error(f"Error mostrando inventario: {str(e)}", exc_info=True)
        await update.message.reply_text("❌ Error al mostrar el inventario.")

async def refrescar_inventario(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    await query.answer()
    try:
        df = obtener_inventario_df()
        if df.empty:
            mensaje = "📭 No hay inventario registrado."
        else:
            tabla = df.to_string(index=False)
            mensaje = f"<pre>{tabla}</pre>"
        await query.edit_message_text(text=mensaje, parse_mode='HTML')
    except Exception as e:
        logger.error(f"Error refrescando inventario: {str(e)}", exc_info=True)
        await query.edit_message_text(text="❌ Error al refrescar el inventario.")

# -----------------------
# Flujo de conversación para VENTA
# Nuevo orden: Producto → Cantidad → Unidad → Precio → Cliente → Nota → Confirmar
# -----------------------
V_PRODUCTO, V_CANTIDAD, V_UNIDAD, V_PRECIO, V_CLIENTE, V_NOTA, V_CONFIRM = range(7)

async def start_venta(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    registrar_usuario(update, context)
    await update.message.reply_text("🚀 Iniciando registro de venta.\n\nPor favor, ingresa el nombre del producto:")
    return V_PRODUCTO

async def venta_producto(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    producto = update.message.text.strip()
    if len(producto) < 3:
        await update.message.reply_text("❌ El nombre del producto debe tener al menos 3 caracteres. Intenta de nuevo:")
        return V_PRODUCTO
    context.user_data['producto'] = producto
    await update.message.reply_text("Ingresa la cantidad:")
    return V_CANTIDAD

async def venta_cantidad(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    try:
        cantidad = float(update.message.text.strip())
        if cantidad <= 0:
            raise ValueError
    except ValueError:
        await update.message.reply_text("❌ Cantidad inválida. Ingresa un número mayor a 0:")
        return V_CANTIDAD
    context.user_data['cantidad'] = cantidad
    reply_keyboard = [["kilos", "toneladas", "cajas"]]
    await update.message.reply_text(
        "Selecciona la unidad:",
        reply_markup=ReplyKeyboardMarkup(reply_keyboard, one_time_keyboard=True)
    )
    return V_UNIDAD

async def venta_unidad(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    unidad = update.message.text.strip().lower()
    context.user_data['unidad'] = unidad
    await update.message.reply_text("Ingresa el precio unitario:", reply_markup=ReplyKeyboardRemove())
    return V_PRECIO

async def venta_precio(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    try:
        precio = float(update.message.text.strip())
        if precio <= 0:
            raise ValueError
    except ValueError:
        await update.message.reply_text("❌ Precio inválido. Ingresa un número mayor a 0:")
        return V_PRECIO
    context.user_data['precio'] = precio
    await update.message.reply_text("Ingresa el nombre del cliente (o escribe 'omitir'):")
    return V_CLIENTE

async def venta_cliente(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    cliente = update.message.text.strip()
    context.user_data['cliente'] = "" if cliente.lower() == "omitir" else cliente
    await update.message.reply_text("Ingresa alguna nota adicional (o escribe 'omitir'):")
    return V_NOTA

async def venta_nota(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    nota = update.message.text.strip()
    context.user_data['nota'] = "" if nota.lower() == "omitir" else nota
    resumen = (
        f"Por favor, confirma la siguiente información:\n\n"
        f"Tipo: Venta\n"
        f"Producto: {context.user_data['producto']}\n"
        f"Cantidad: {context.user_data['cantidad']} {context.user_data['unidad']}\n"
        f"Precio: ${context.user_data['precio']:.2f}\n"
        f"Cliente: {context.user_data['cliente'] or 'No especificado'}\n"
        f"Nota: {context.user_data['nota'] or 'Ninguna'}\n\n"
        "¿Confirmas la transacción?"
    )
    keyboard = [
        [InlineKeyboardButton("Confirmar", callback_data="confirmar_venta"),
         InlineKeyboardButton("Cancelar", callback_data="cancelar_venta")]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    await update.message.reply_text(resumen, reply_markup=reply_markup)
    return V_CONFIRM

async def venta_confirmar(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    await query.answer()
    try:
        user_id = str(query.from_user.id)
        registrado_por = query.from_user.username or user_id
        transaccion_id = agregar_transaccion(
            registrado_por,
            "venta",
            context.user_data['producto'],
            context.user_data['cantidad'],
            context.user_data['unidad'],
            context.user_data['precio'],
            context.user_data['cliente'],
            context.user_data['nota']
        )
    except ValueError as ve:
        await query.edit_message_text(text=f"❌ Error registrando la venta: {ve}")
        context.user_data.clear()
        return ConversationHandler.END
    respuesta = (
        f"✅ Venta registrada:\n"
        f"Producto: {context.user_data['producto']}\n"
        f"Cantidad: {context.user_data['cantidad']} {context.user_data['unidad']}\n"
        f"Precio: ${context.user_data['precio']:.2f}\n"
        f"ID: {transaccion_id}"
    )
    await query.edit_message_text(respuesta)
    await enviar_informe_inventario(query, context.user_data['producto'])
    context.user_data.clear()
    return ConversationHandler.END

async def venta_cancel(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    await update.message.reply_text("❌ Operación cancelada", reply_markup=ReplyKeyboardRemove())
    context.user_data.clear()
    return ConversationHandler.END

# -----------------------
# Flujo de conversación para COMPRA
# Nuevo orden: Producto → Cantidad → Unidad → Precio → Proveedor → Nota → Confirmar
# -----------------------
C_PRODUCTO, C_CANTIDAD, C_UNIDAD, C_PRECIO, C_CLIENTE, C_NOTA, C_CONFIRM = range(7)

async def start_compra(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    registrar_usuario(update, context)
    await update.message.reply_text("🚀 Iniciando registro de compra.\n\nPor favor, ingresa el nombre del producto:")
    return C_PRODUCTO

async def compra_producto(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    producto = update.message.text.strip()
    if len(producto) < 3:
        await update.message.reply_text("❌ El nombre del producto debe tener al menos 3 caracteres. Intenta de nuevo:")
        return C_PRODUCTO
    context.user_data['producto'] = producto
    await update.message.reply_text("Ingresa la cantidad:")
    return C_CANTIDAD

async def compra_cantidad(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    try:
        cantidad = float(update.message.text.strip())
        if cantidad <= 0:
            raise ValueError
    except ValueError:
        await update.message.reply_text("❌ Cantidad inválida. Ingresa un número mayor a 0:")
        return C_CANTIDAD
    context.user_data['cantidad'] = cantidad
    reply_keyboard = [["kilos", "toneladas", "cajas"]]
    await update.message.reply_text(
        "Selecciona la unidad:",
        reply_markup=ReplyKeyboardMarkup(reply_keyboard, one_time_keyboard=True)
    )
    return C_UNIDAD

async def compra_unidad(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    unidad = update.message.text.strip().lower()
    context.user_data['unidad'] = unidad
    await update.message.reply_text("Ingresa el precio unitario:", reply_markup=ReplyKeyboardRemove())
    return C_PRECIO

async def compra_precio(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    try:
        precio = float(update.message.text.strip())
        if precio <= 0:
            raise ValueError
    except ValueError:
        await update.message.reply_text("❌ Precio inválido. Ingresa un número mayor a 0:")
        return C_PRECIO
    context.user_data['precio'] = precio
    await update.message.reply_text("Ingresa el nombre del proveedor (o escribe 'omitir'):")
    return C_CLIENTE

async def compra_cliente(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    proveedor = update.message.text.strip()
    context.user_data['cliente'] = "" if proveedor.lower() == "omitir" else proveedor
    await update.message.reply_text("Ingresa alguna nota adicional (o escribe 'omitir'):")
    return C_NOTA

async def compra_nota(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    nota = update.message.text.strip()
    context.user_data['nota'] = "" if nota.lower() == "omitir" else nota
    resumen = (
        f"Por favor, confirma la siguiente información:\n\n"
        f"Tipo: Compra\n"
        f"Producto: {context.user_data['producto']}\n"
        f"Cantidad: {context.user_data['cantidad']} {context.user_data['unidad']}\n"
        f"Precio: ${context.user_data['precio']:.2f}\n"
        f"Proveedor: {context.user_data['cliente'] or 'No especificado'}\n"
        f"Nota: {context.user_data['nota'] or 'Ninguna'}\n\n"
        "¿Confirmas la transacción?"
    )
    keyboard = [
        [InlineKeyboardButton("Confirmar", callback_data="confirmar_compra"),
         InlineKeyboardButton("Cancelar", callback_data="cancelar_compra")]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    await update.message.reply_text(resumen, reply_markup=reply_markup)
    return C_CONFIRM

async def compra_confirmar(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    await query.answer()
    try:
        user_id = str(query.from_user.id)
        registrado_por = query.from_user.username or user_id
        transaccion_id = agregar_transaccion(
            registrado_por,
            "compra",
            context.user_data['producto'],
            context.user_data['cantidad'],
            context.user_data['unidad'],
            context.user_data['precio'],
            context.user_data['cliente'],
            context.user_data['nota']
        )
    except ValueError as ve:
        await query.edit_message_text(text=f"❌ Error registrando la compra: {ve}")
        context.user_data.clear()
        return ConversationHandler.END
    respuesta = (
        f"✅ Compra registrada:\n"
        f"Producto: {context.user_data['producto']}\n"
        f"Cantidad: {context.user_data['cantidad']} {context.user_data['unidad']}\n"
        f"Precio: ${context.user_data['precio']:.2f}\n"
        f"ID: {transaccion_id}"
    )
    await query.edit_message_text(respuesta)
    await enviar_informe_inventario(query, context.user_data['producto'])
    context.user_data.clear()
    return ConversationHandler.END

async def compra_cancel(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    await update.message.reply_text("❌ Operación cancelada", reply_markup=ReplyKeyboardRemove())
    context.user_data.clear()
    return ConversationHandler.END

# -----------------------
# Flujo de conversación para MODIFICAR
# (Se mantiene igual que en el código base)
# -----------------------
M_ID, M_PRODUCTO, M_CANTIDAD, M_PRECIO, M_CLIENTE, M_NOTA, M_UNIDAD, M_CONFIRM = range(8)

async def start_modificar(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    await update.message.reply_text("🚀 Iniciando modificación.\n\nIngresa el ID de la transacción a modificar:")
    return M_ID

async def modificar_id(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    trans_id = update.message.text.strip()
    context.user_data['transaccion_id'] = trans_id
    await update.message.reply_text("Ingresa el nuevo nombre del producto (o escribe 'omitir' para dejar sin cambios):")
    return M_PRODUCTO

async def modificar_producto(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    producto = update.message.text.strip()
    context.user_data['producto'] = None if producto.lower() == "omitir" else producto
    await update.message.reply_text("Ingresa la nueva cantidad (o 'omitir'):")
    return M_CANTIDAD

async def modificar_cantidad(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    text = update.message.text.strip()
    if text.lower() == "omitir":
        context.user_data['cantidad'] = None
    else:
        try:
            cantidad = float(text)
            context.user_data['cantidad'] = cantidad
        except ValueError:
            await update.message.reply_text("❌ Cantidad inválida. Ingresa un número o 'omitir':")
            return M_CANTIDAD
    await update.message.reply_text("Ingresa el nuevo precio unitario (o 'omitir'):")
    return M_PRECIO

async def modificar_precio(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    text = update.message.text.strip()
    if text.lower() == "omitir":
        context.user_data['precio'] = None
    else:
        try:
            precio = float(text)
            context.user_data['precio'] = precio
        except ValueError:
            await update.message.reply_text("❌ Precio inválido. Ingresa un número o 'omitir':")
            return M_PRECIO
    await update.message.reply_text("Ingresa el nuevo cliente/proveedor (o 'omitir'):")
    return M_CLIENTE

async def modificar_cliente(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    cliente = update.message.text.strip()
    context.user_data['cliente'] = None if cliente.lower() == "omitir" else cliente
    await update.message.reply_text("Ingresa la nueva nota (o 'omitir'):")
    return M_NOTA

async def modificar_nota(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    nota = update.message.text.strip()
    context.user_data['nota'] = None if nota.lower() == "omitir" else nota
    reply_keyboard = [["kilos", "toneladas", "cajas"], ["omitir"]]
    await update.message.reply_text(
        "Selecciona la nueva unidad (o 'omitir'):",
        reply_markup=ReplyKeyboardMarkup(reply_keyboard, one_time_keyboard=True)
    )
    return M_UNIDAD

async def modificar_unidad(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    unidad = update.message.text.strip().lower()
    context.user_data['unidad'] = None if unidad.lower() == "omitir" else unidad
    resumen = "Por favor, confirma la siguiente modificación:\n\n"
    resumen += f"ID: {context.user_data['transaccion_id']}\n"
    if context.user_data['producto'] is not None:
        resumen += f"Nuevo producto: {context.user_data['producto']}\n"
    if context.user_data['cantidad'] is not None:
        resumen += f"Nueva cantidad: {context.user_data['cantidad']}\n"
    if context.user_data['precio'] is not None:
        resumen += f"Nuevo precio: ${context.user_data['precio']:.2f}\n"
    if context.user_data['cliente'] is not None:
        resumen += f"Nuevo cliente/proveedor: {context.user_data['cliente']}\n"
    if context.user_data['nota'] is not None:
        resumen += f"Nueva nota: {context.user_data['nota']}\n"
    if context.user_data['unidad'] is not None:
        resumen += f"Nueva unidad: {context.user_data['unidad']}\n"
    resumen += "\n¿Confirmas la modificación?"
    keyboard = [
        [InlineKeyboardButton("Confirmar", callback_data="confirmar_modificar"),
         InlineKeyboardButton("Cancelar", callback_data="cancelar_modificar")]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    await update.message.reply_text(resumen, reply_markup=reply_markup)
    return M_CONFIRM

async def modificar_confirmar(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    await query.answer()
    if query.data == "confirmar_modificar":
        user_id = str(query.from_user.id)
        nuevos_datos = {}
        if context.user_data.get('producto') is not None:
            nuevos_datos['producto'] = context.user_data['producto']
        if context.user_data.get('cantidad') is not None:
            nuevos_datos['cantidad'] = context.user_data['cantidad']
        if context.user_data.get('precio') is not None:
            nuevos_datos['precio'] = context.user_data['precio']
        if context.user_data.get('cliente') is not None:
            nuevos_datos['cliente'] = context.user_data['cliente']
        if context.user_data.get('nota') is not None:
            nuevos_datos['notas'] = context.user_data['nota']
        if context.user_data.get('unidad') is not None:
            nuevos_datos['unidad'] = context.user_data['unidad']
        try:
            modificar_transaccion(context.user_data['transaccion_id'], user_id, nuevos_datos)
            await query.edit_message_text(text="✅ Transacción modificada exitosamente.")
        except Exception as e:
            logger.error(f"Error modificando transacción: {str(e)}", exc_info=True)
            await query.edit_message_text(text=f"❌ Error: {str(e)}")
    else:
        await query.edit_message_text(text="❌ Modificación cancelada.")
    context.user_data.clear()
    return ConversationHandler.END

async def modificar_cancel(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    await update.message.reply_text("❌ Operación cancelada", reply_markup=ReplyKeyboardRemove())
    context.user_data.clear()
    return ConversationHandler.END

# -----------------------
# Flujo de conversación para ELIMINAR
# -----------------------
CONFIRMAR_ELIMINAR = 100

async def eliminar_inicio(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    try:
        user_id = str(update.message.from_user.id)
        args = context.args
        if args:
            eliminar_transaccion(args[0], user_id)
            await update.message.reply_text(f"✅ Transacción {args[0]} eliminada")
            return ConversationHandler.END
        else:
            ultimo = obtener_ultimo_transaccion(user_id)
            if not ultimo:
                await update.message.reply_text("ℹ️ No hay transacciones recientes")
                return ConversationHandler.END
            context.user_data['transaccion_a_eliminar'] = ultimo['id']
            keyboard = [
                [InlineKeyboardButton("Confirmar", callback_data="confirmar_eliminar"),
                 InlineKeyboardButton("Cancelar", callback_data="cancelar_eliminar")]
            ]
            reply_markup = InlineKeyboardMarkup(keyboard)
            await update.message.reply_text(
                f"⚠️ Confirmar eliminación:\n\n"
                f"ID: {ultimo['id']}\n"
                f"Tipo: {ultimo['tipo'].capitalize()}\n"
                f"Producto: {ultimo['producto']}\n"
                f"Cantidad: {ultimo['cantidad']} {ultimo['unidad']}\n"
                f"Precio: ${ultimo['precio']:.2f}",
                reply_markup=reply_markup
            )
            return CONFIRMAR_ELIMINAR
    except Exception as e:
        logger.error(f"Error en eliminación: {str(e)}", exc_info=True)
        await update.message.reply_text("❌ Error iniciando el proceso de eliminación")
        return ConversationHandler.END

async def handle_eliminar_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    await query.answer()
    user_id = str(query.from_user.id)
    data = query.data
    transaccion_id = context.user_data.get('transaccion_a_eliminar')
    try:
        if data == "confirmar_eliminar":
            eliminar_transaccion(transaccion_id, user_id)
            await query.edit_message_text(text=f"✅ Transacción {transaccion_id} eliminada")
        else:
            await query.edit_message_text(text="❌ Acción cancelada")
    except Exception as e:
        await query.edit_message_text(text=f"❌ Error: {str(e)}")
    context.user_data.clear()
    return ConversationHandler.END

# -----------------------
# Funciones adicionales
# -----------------------
async def enviar_informe_inventario(update: Update, producto: str) -> None:
    try:
        inventario_df = obtener_inventario_df()
        df_producto = inventario_df[inventario_df['producto'].str.lower() == producto.lower()]
        if df_producto.empty:
            informe = f"📦 No hay información de inventario para el producto '{producto}'."
        else:
            informe = f"📦 Inventario actual para {producto}:\n" + df_producto.to_string(index=False)
        await update.message.reply_text(informe)
    except Exception as e:
        logger.error(f"Error al enviar informe de inventario: {str(e)}", exc_info=True)
        await update.message.reply_text(f"❌ Error al enviar informe de inventario: {str(e)}")

async def historial(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    try:
        user_id = str(update.message.from_user.id)
        historial_str = obtener_historial_text(user_id)
        await update.message.reply_text(f"📜 Historial reciente:\n\n{historial_str}")
    except Exception as e:
        logger.error(f"Error en historial: {str(e)}", exc_info=True)
        await update.message.reply_text("❌ Error obteniendo historial")

async def exportar_historial(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    try:
        user_id = str(update.message.from_user.id)
        df = obtener_historial_df(user_id)
        if df.empty:
            await update.message.reply_text("No hay registros en el historial para exportar.")
            return
        with tempfile.NamedTemporaryFile(suffix=".xlsx", delete=False) as tmp:
            temp_filename = tmp.name
        df.to_excel(temp_filename, index=False)
        await update.message.reply_document(document=open(temp_filename, "rb"), filename="historial.xlsx")
        os.remove(temp_filename)
    except Exception as e:
        await update.message.reply_text(f"Error al exportar el historial: {str(e)}")

async def filtrar_historial(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    try:
        args = context.args
        if len(args) < 2:
            await update.message.reply_text("Uso: /filtrar_historial <dia|mes> <valor>\nEjemplo: /filtrar_historial dia 2025-03-08")
            return
        filtro_tipo = args[0].lower()
        valor = args[1]
        user_id = str(update.message.from_user.id)
        df = obtener_historial_df(user_id)
        if df.empty:
            await update.message.reply_text("No hay registros en el historial.")
            return
        df['fecha'] = pd.to_datetime(df['fecha'], format="%d/%m/%Y %I:%M%p")
        if filtro_tipo == 'dia':
            df_filtrado = df[df['fecha'].dt.strftime('%Y-%m-%d') == valor]
        elif filtro_tipo == 'mes':
            df_filtrado = df[df['fecha'].dt.strftime('%Y-%m') == valor]
        else:
            await update.message.reply_text("Tipo de filtro inválido. Usa 'dia' o 'mes'.")
            return
        if df_filtrado.empty:
            await update.message.reply_text("No se encontraron registros para el filtro especificado.")
            return
        with tempfile.NamedTemporaryFile(suffix=".xlsx", delete=False) as tmp:
            temp_filename = tmp.name
        df_filtrado.to_excel(temp_filename, index=False)
        await update.message.reply_document(document=open(temp_filename, "rb"), filename=f"historial_{filtro_tipo}_{valor}.xlsx")
        os.remove(temp_filename)
    except Exception as e:
        await update.message.reply_text(f"Error al filtrar el historial: {str(e)}")

async def ultimo_pedido(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    try:
        user_id = str(update.message.from_user.id)
        ultimo = obtener_ultimo_transaccion(user_id)
        if ultimo:
            mensaje = (
                f"📌 Último Pedido:\n"
                f"Tipo: {ultimo['tipo'].capitalize()}\n"
                f"Producto: {ultimo['producto']}\n"
                f"Cantidad: {ultimo['cantidad']} {ultimo['unidad']}\n"
                f"Precio: ${ultimo['precio']:.2f}\n"
                f"{'Cliente' if ultimo['tipo']=='venta' else 'Proveedor'}: {ultimo['cliente']}\n"
                f"Notas: {ultimo['notas']}\n"
                f"Fecha: {ultimo['fecha']}\n"
                f"ID: {ultimo['id']}"
            )
        else:
            mensaje = "ℹ️ No hay transacciones recientes."
        await update.message.reply_text(mensaje)
    except Exception as e:
        logger.error(f"Error en último pedido: {str(e)}", exc_info=True)
        await update.message.reply_text("❌ Error obteniendo el último pedido")

async def corte(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    try:
        user_id = str(update.message.from_user.id)
        df = obtener_historial_df(user_id)
        if df.empty:
            await update.message.reply_text("No hay transacciones registradas para el día de hoy.")
            return
        df['fecha'] = pd.to_datetime(df['fecha'], format="%d/%m/%Y %I:%M%p")
        hoy = date.today().strftime("%Y-%m-%d")
        df_hoy = df[df['fecha'].dt.strftime('%Y-%m-%d') == hoy]
        if df_hoy.empty:
            await update.message.reply_text("No hay transacciones registradas para el día de hoy.")
            return
        with tempfile.NamedTemporaryFile(suffix=".xlsx", delete=False) as tmp:
            temp_filename = tmp.name
        df_hoy.to_excel(temp_filename, index=False)
        await update.message.reply_document(document=open(temp_filename, "rb"), filename=f"corte_{hoy}.xlsx")
        os.remove(temp_filename)
    except Exception as e:
        logger.error(f"Error en corte: {str(e)}", exc_info=True)
        await update.message.reply_text(f"❌ Error generando el informe de corte: {str(e)}")

async def auto_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        user_id = str(update.message.from_user.id)
        mensaje = update.message.text
        registrado_por = update.message.from_user.username or user_id
        acciones = analizar_mensaje(user_id, registrado_por, mensaje)
        if not acciones:
            await update.message.reply_text("❌ No se detectaron acciones en el mensaje.")
            return
        respuestas_procesadas = []
        for accion in acciones:
            if "error" in accion:
                respuestas_procesadas.append(f"❌ Error: {accion['error']}")
                continue
            cliente = accion.get('cliente', '') or ''
            nota = accion.get('nota', '') or ''
            unidad = accion.get('unidad', 'unidades')
            transaccion_id = accion.get('transaccion_id', '')
            tipo = accion.get('tipo', '').lower()
            producto = accion.get('producto', '')
            try:
                cantidad = float(accion['cantidad'])
                precio = float(accion['precio'])
            except Exception as conv_err:
                logger.error(f"Error al convertir cantidad/precio: {conv_err}")
                respuestas_procesadas.append("❌ Error en datos numéricos")
                continue
            if not transaccion_id:
                transaccion_id = str(uuid.uuid4())
            if ContextManager.is_action_id_registered(user_id, transaccion_id):
                respuestas_procesadas.append(f"⚠️ Acción duplicada: {transaccion_id}")
                continue
            try:
                if tipo in ["venta", "compra"]:
                    agregar_transaccion(
                        registrado_por,
                        tipo,
                        producto,
                        cantidad,
                        unidad,
                        precio,
                        cliente,
                        nota,
                        None,
                        transaccion_id
                    )
                    ContextManager.add_action_id(user_id, transaccion_id)
                    respuesta = (
                        f"✅ {'Venta' if tipo == 'venta' else 'Compra'} registrada:\n"
                        f"• Producto: {producto}\n"
                        f"• Cantidad: {cantidad} {unidad}\n"
                        f"• Precio unitario: ${precio:.2f}\n"
                        f"• ID: {transaccion_id}"
                    )
                    respuestas_procesadas.append(respuesta)
                    await enviar_informe_inventario(update, producto)
                else:
                    respuestas_procesadas.append(f"⚠️ Acción no soportada: {tipo}")
            except ValueError as ve:
                respuestas_procesadas.append(f"❌ Error: {str(ve)}")
                await enviar_informe_inventario(update, producto)
        await update.message.reply_text("\n".join(respuestas_procesadas))
    except Exception as e:
        logger.error(f"❌ Error en auto_handler: {str(e)}", exc_info=True)
        await update.message.reply_text("❌ Error procesando tu mensaje")

# -----------------------
# Función principal
# -----------------------
def main() -> None:
    app = ApplicationBuilder().token(TOKEN).build()

    # ConversationHandler para eliminar
    eliminar_conv = ConversationHandler(
        entry_points=[CommandHandler('eliminar', eliminar_inicio)],
        states={
            100: [CallbackQueryHandler(handle_eliminar_callback, pattern="^(confirmar_eliminar|cancelar_eliminar)$")]
        },
        fallbacks=[]
    )

    # ConversationHandler para venta
    venta_conv = ConversationHandler(
        entry_points=[CommandHandler("venta", start_venta)],
        states={
            V_PRODUCTO: [MessageHandler(filters.TEXT & ~filters.COMMAND, venta_producto)],
            V_CANTIDAD: [MessageHandler(filters.TEXT & ~filters.COMMAND, venta_cantidad)],
            V_UNIDAD: [MessageHandler(filters.TEXT & ~filters.COMMAND, venta_unidad)],
            V_PRECIO: [MessageHandler(filters.TEXT & ~filters.COMMAND, venta_precio)],
            V_CLIENTE: [MessageHandler(filters.TEXT & ~filters.COMMAND, venta_cliente)],
            V_NOTA: [MessageHandler(filters.TEXT & ~filters.COMMAND, venta_nota)],
            V_CONFIRM: [CallbackQueryHandler(venta_confirmar, pattern="^(confirmar_venta|cancelar_venta)$")]
        },
        fallbacks=[CommandHandler("cancel", venta_cancel)]
    )

    # ConversationHandler para compra
    compra_conv = ConversationHandler(
        entry_points=[CommandHandler("compra", start_compra)],
        states={
            C_PRODUCTO: [MessageHandler(filters.TEXT & ~filters.COMMAND, compra_producto)],
            C_CANTIDAD: [MessageHandler(filters.TEXT & ~filters.COMMAND, compra_cantidad)],
            C_UNIDAD: [MessageHandler(filters.TEXT & ~filters.COMMAND, compra_unidad)],
            C_PRECIO: [MessageHandler(filters.TEXT & ~filters.COMMAND, compra_precio)],
            C_CLIENTE: [MessageHandler(filters.TEXT & ~filters.COMMAND, compra_cliente)],
            C_NOTA: [MessageHandler(filters.TEXT & ~filters.COMMAND, compra_nota)],
            C_CONFIRM: [CallbackQueryHandler(compra_confirmar, pattern="^(confirmar_compra|cancelar_compra)$")]
        },
        fallbacks=[CommandHandler("cancel", compra_cancel)]
    )

    # ConversationHandler para modificar
    modificar_conv = ConversationHandler(
        entry_points=[CommandHandler("modificar", start_modificar)],
        states={
            M_ID: [MessageHandler(filters.TEXT & ~filters.COMMAND, modificar_id)],
            M_PRODUCTO: [MessageHandler(filters.TEXT & ~filters.COMMAND, modificar_producto)],
            M_CANTIDAD: [MessageHandler(filters.TEXT & ~filters.COMMAND, modificar_cantidad)],
            M_PRECIO: [MessageHandler(filters.TEXT & ~filters.COMMAND, modificar_precio)],
            M_CLIENTE: [MessageHandler(filters.TEXT & ~filters.COMMAND, modificar_cliente)],
            M_NOTA: [MessageHandler(filters.TEXT & ~filters.COMMAND, modificar_nota)],
            M_UNIDAD: [MessageHandler(filters.TEXT & ~filters.COMMAND, modificar_unidad)],
            M_CONFIRM: [CallbackQueryHandler(modificar_confirmar, pattern="^(confirmar_modificar|cancelar_modificar)$")]
        },
        fallbacks=[CommandHandler("cancel", modificar_cancel)]
    )

    # Registro de otros CommandHandlers
    handlers = [
        CommandHandler("start", start),
        CommandHandler("help", help_command),
        CommandHandler("ganancias", ganancias),
        CommandHandler("historial", historial),
        CommandHandler("inventario", inventario),
        CommandHandler("exportar_historial", exportar_historial),
        CommandHandler("filtrar_historial", filtrar_historial),
        CommandHandler("ultimo_pedido", ultimo_pedido),
        CommandHandler("corte", corte),
        MessageHandler(filters.TEXT & ~filters.COMMAND, auto_handler)
    ]

    # Registro de CallbackQueryHandler para refrescar inventario
    app.add_handler(CallbackQueryHandler(refrescar_inventario, pattern="^refrescar_inventario$"))

    for conv in [eliminar_conv, venta_conv, compra_conv, modificar_conv]:
        app.add_handler(conv)
    for handler in handlers:
        app.add_handler(handler)

    app.run_polling()

if __name__ == "__main__":
    main()
