# TheAdmingestor_bot/database.py
import sqlite3
import uuid
import pandas as pd
from datetime import datetime
from config import DATABASE_PATH

# Diccionario para normalizar variantes de unidades a nombres canónicos
UNIT_NORMALIZATION = {
    "kg": "kilos",
    "k": "kilos",
    "kilo": "kilos",
    "kilogramo": "kilos",
    "kilogramos": "kilos",
    "ton": "toneladas",
    "t": "toneladas",
    "tonelada": "toneladas",
    "toneladas": "toneladas",
    "caja": "cajas",
    "cajas": "cajas"
}

def normalize_unit(unit: str) -> str:
    unit_lower = unit.strip().lower()
    return UNIT_NORMALIZATION.get(unit_lower, unit_lower)

# Factores de conversión para unidades de peso, usando las unidades canónicas
# Se agregan las conversiones entre kilos, toneladas y cajas
CONVERSION_FACTORS = {
    ("kilos", "kilos"): 1,
    ("toneladas", "toneladas"): 1,
    ("cajas", "cajas"): 1,
    
    ("kilos", "toneladas"): 0.001,        # 1 kg = 0.001 toneladas
    ("toneladas", "kilos"): 1000,          # 1 tonelada = 1000 kg

    ("cajas", "kilos"): 50,                # 1 caja = 50 kg
    ("kilos", "cajas"): 1/50,              # 1 kg = 0.02 cajas

    ("cajas", "toneladas"): 50/1000,       # 1 caja = 50 kg = 0.05 toneladas
    ("toneladas", "cajas"): 1 / (50/1000)  # 1 tonelada = 20 cajas
}

def connect_db():
    """Inicializa la base de datos creando las tablas de transacciones e inventario."""
    with sqlite3.connect(DATABASE_PATH) as conn:
        cursor = conn.cursor()
        # Tabla de transacciones
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS transacciones (
                id TEXT PRIMARY KEY,
                usuario TEXT NOT NULL,
                tipo TEXT NOT NULL,
                producto TEXT NOT NULL,
                cantidad REAL NOT NULL,
                unidad TEXT NOT NULL,
                precio REAL NOT NULL,
                cliente TEXT,
                notas TEXT,
                fecha TEXT DEFAULT CURRENT_TIMESTAMP
            )
        """)
        # Tabla de inventario global
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS inventario (
                producto TEXT PRIMARY KEY,
                cantidad REAL NOT NULL,
                unidad TEXT NOT NULL
            )
        """)
        conn.commit()

def convert_units(transaction_unit: str, inventory_unit: str, cantidad: float, precio: float):
    # Normaliza las unidades a su forma canónica
    trans_unit = normalize_unit(transaction_unit)
    invent_unit = normalize_unit(inventory_unit)
    key = (trans_unit, invent_unit)
    factor = CONVERSION_FACTORS.get(key, 1)
    nueva_cantidad = cantidad * factor
    # Si el precio es por unidad de transacción, el precio por unidad de inventario se ajusta:
    nuevo_precio = precio / factor if factor != 0 else precio
    return nueva_cantidad, nuevo_precio

def actualizar_inventario(producto: str, transaction_unit: str, cantidad: float, tipo: str):
    """
    Actualiza la tabla de inventario según el tipo de transacción:
      - Para 'compra': se incrementa el stock.
      - Para 'venta': se decrementa el stock (verificando que haya suficiente).
    Realiza conversión de unidades si el producto ya existe con otra unidad en el inventario.
    """
    with sqlite3.connect(DATABASE_PATH) as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT cantidad, unidad FROM inventario WHERE producto = ?", (producto,))
        row = cursor.fetchone()
        if row:
            inventario_cantidad, inventario_unidad = row
            # Convertir la cantidad de la transacción a la unidad del inventario.
            cantidad_convertida, _ = convert_units(transaction_unit, inventario_unidad, cantidad, 0)
            if tipo == "compra":
                nueva_cantidad = inventario_cantidad + cantidad_convertida
            elif tipo == "venta":
                nueva_cantidad = inventario_cantidad - cantidad_convertida
                if nueva_cantidad < 0:
                    raise ValueError(f"Inventario insuficiente para {producto}. Disponible: {inventario_cantidad} {inventario_unidad}")
            else:
                nueva_cantidad = inventario_cantidad  # En otros casos, no se actualiza.
            cursor.execute("UPDATE inventario SET cantidad = ? WHERE producto = ?", (nueva_cantidad, producto))
        else:
            # Si el producto no existe: se crea solo si es una compra.
            if tipo == "compra":
                cursor.execute("INSERT INTO inventario (producto, cantidad, unidad) VALUES (?, ?, ?)", (producto, cantidad, transaction_unit))
            elif tipo == "venta":
                raise ValueError(f"No existe inventario registrado para {producto}")
        conn.commit()

def agregar_transaccion(usuario: str, tipo: str, producto: str, cantidad: float, unidad: str, 
                       precio: float, cliente: str, notas: str, fecha: str = None, 
                       transaccion_id: str = ""):
    """Registra una nueva transacción con manejo de valores nulos."""
    # Convertir campos opcionales a strings vacíos si son None
    cliente = (cliente or "").strip()
    notas = (notas or "").strip()
    producto = producto.strip()
    unidad = unidad.strip().lower()
    
    if not transaccion_id:
        transaccion_id = str(uuid.uuid4())
        
    if not fecha:
        fecha = datetime.now().strftime("%d/%m/%Y %I:%M%p")

    with sqlite3.connect(DATABASE_PATH) as conn:
        cursor = conn.cursor()
        cursor.execute("""
            INSERT INTO transacciones (id, usuario, tipo, producto, cantidad, unidad, precio, cliente, notas, fecha)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (transaccion_id, usuario, tipo, producto, cantidad, unidad, precio, cliente, notas, fecha))
        conn.commit()
        
    if tipo in ["compra", "venta"]:
        actualizar_inventario(producto, unidad, cantidad, tipo)
        
    return transaccion_id
def modificar_transaccion(transaccion_id: str, usuario: str, nuevos_datos: dict):
    """
    Modifica una transacción existente:
      - Primero, revierte el efecto en el inventario de la transacción original (si es compra o venta).
      - Luego, actualiza la transacción con los nuevos datos.
      - Finalmente, aplica el nuevo efecto en el inventario.
    
    Se permiten modificar: tipo, producto, cantidad, unidad, precio, cliente y notas.
    Solo el usuario que registró la transacción puede modificarla.
    """
    with sqlite3.connect(DATABASE_PATH) as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT usuario, tipo, producto, cantidad, unidad, precio, cliente, notas, fecha FROM transacciones WHERE id = ?", (transaccion_id,))
        row = cursor.fetchone()
        if not row:
            raise ValueError("Transacción no encontrada")
        old_trans = {
            "usuario": row[0],
            "tipo": row[1],
            "producto": row[2],
            "cantidad": row[3],
            "unidad": row[4],
            "precio": row[5],
            "cliente": row[6],
            "notas": row[7],
            "fecha": row[8]
        }
        if old_trans["usuario"] != usuario:
            raise ValueError("No tienes permiso para modificar esta transacción")
        # Revertir el efecto en inventario de la transacción original si es compra o venta.
        if old_trans["tipo"] in ["compra", "venta"]:
            revertir_tipo = "venta" if old_trans["tipo"] == "compra" else "compra"
            actualizar_inventario(old_trans["producto"], old_trans["unidad"], old_trans["cantidad"], revertir_tipo)
        # Actualizar los datos según los nuevos valores.
        for campo in ["tipo", "producto", "cantidad", "unidad", "precio", "cliente", "notas"]:
            if campo in nuevos_datos:
                old_trans[campo] = nuevos_datos[campo]
        cursor.execute("""
            UPDATE transacciones 
            SET tipo = ?, producto = ?, cantidad = ?, unidad = ?, precio = ?, cliente = ?, notas = ?
            WHERE id = ?
        """, (old_trans["tipo"], old_trans["producto"], old_trans["cantidad"], old_trans["unidad"], old_trans["precio"], old_trans["cliente"], old_trans["notas"], transaccion_id))
        conn.commit()
    if old_trans["tipo"] in ["compra", "venta"]:
        actualizar_inventario(old_trans["producto"], old_trans["unidad"], old_trans["cantidad"], old_trans["tipo"])

def eliminar_transaccion(transaccion_id: str, usuario: str):
    """
    Elimina una transacción y revierte su efecto en el inventario (para compra o venta).
    Solo el usuario que registró la transacción puede eliminarla.
    """
    with sqlite3.connect(DATABASE_PATH) as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT usuario, tipo, producto, cantidad, unidad FROM transacciones WHERE id = ?", (transaccion_id,))
        row = cursor.fetchone()
        if not row:
            raise ValueError("Transacción no encontrada")
        trans = {
            "usuario": row[0],
            "tipo": row[1],
            "producto": row[2],
            "cantidad": row[3],
            "unidad": row[4]
        }
        if trans["usuario"] != usuario:
            raise ValueError("No tienes permiso para eliminar esta transacción")
        if trans["tipo"] in ["compra", "venta"]:
            revertir_tipo = "venta" if trans["tipo"] == "compra" else "compra"
            actualizar_inventario(trans["producto"], trans["unidad"], trans["cantidad"], revertir_tipo)
        cursor.execute("DELETE FROM transacciones WHERE id = ?", (transaccion_id,))
        conn.commit()

def obtener_historial_df(user_id: str) -> pd.DataFrame:
    """
    Devuelve un DataFrame con todas las transacciones del usuario,
    ordenadas de la más reciente a la más antigua.
    """
    with sqlite3.connect(DATABASE_PATH) as conn:
        query = """
            SELECT id, usuario, tipo, producto, cantidad, unidad, precio, cliente, notas, fecha
            FROM transacciones
            WHERE usuario = ?
            ORDER BY fecha DESC
        """
        df = pd.read_sql_query(query, conn, params=(user_id,))
    return df

def obtener_inventario_df() -> pd.DataFrame:
    """
    Devuelve un DataFrame con el inventario global, ordenado alfabéticamente por producto.
    """
    with sqlite3.connect(DATABASE_PATH) as conn:
        query = """
            SELECT producto, cantidad, unidad
            FROM inventario
            ORDER BY producto
        """
        df = pd.read_sql_query(query, conn)
    return df
