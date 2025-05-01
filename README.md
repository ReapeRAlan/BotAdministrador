# 🤖 Gestor Comercial Inteligente con Telegram

## 📌 Descripción

El Gestor Comercial Inteligente es una herramienta digital basada en Telegram que simplifica y optimiza la gestión de transacciones comerciales mediante una interfaz interactiva y comandos específicos. La aplicación facilita el registro detallado de ventas, compras, modificaciones y eliminaciones de transacciones utilizando comandos predefinidos o mensajes de texto naturales procesados con inteligencia artificial. Emplea bases de datos SQLite robustas para almacenar información de forma segura y permite llevar a cabo análisis detallados y reportes precisos sobre el estado del inventario, movimientos recientes y balances financieros. Esta solución está especialmente diseñada para pequeños y medianos comerciantes que buscan agilidad y precisión en la gestión diaria de sus operaciones comerciales.

---

## 🚀 Características Principales

- **Registro interactivo y ágil** de ventas y compras mediante comandos y formularios guiados.
- **Modificación y eliminación** segura y confiable de transacciones con confirmaciones interactivas.
- **Visualización rápida del historial** de transacciones recientes y capacidad de **exportación en formato Excel**.
- **Cálculo automático y en tiempo real** de ganancias, pérdidas y balances financieros.
- **Inventario siempre actualizado** con conversión automática de unidades y alertas en caso de bajo stock.
- **Análisis inteligente de mensajes** mediante inteligencia artificial, usando integración avanzada con Ollama para interpretación de lenguaje natural y automatización de tareas.

---

## 🛠️ Tecnologías Utilizadas

- **Python** (lenguaje principal para lógica de negocio y gestión del bot)
- **Telegram Bot API (python-telegram-bot)** (integración y comunicación con Telegram)
- **SQLite** (almacenamiento local robusto y eficiente)
- **Pandas** (análisis y manipulación avanzada de datos)
- **Requests** (manejo eficiente de peticiones HTTP para integraciones externas)
- **JSON Parsing** (manejo y análisis de respuestas estructuradas)

---

## 📂 Estructura del Proyecto

```
.
├── config.py                  # Configuración general y parámetros clave
├── database.py                # Funciones y lógica para operaciones de base de datos
├── ollama_integration.py      # Integración avanzada de inteligencia artificial
├── main.py                    # Archivo principal del bot
├── data
│   └── transactions.db        # Base de datos SQLite con transacciones
└── requirements.txt           # Lista de dependencias necesarias
```

---

## 📥 Instalación

Sigue estos pasos para instalar y configurar la aplicación:

1. **Clona el repositorio desde GitHub**:

```bash
git clone https://github.com/tuusuario/gestor-comercial-inteligente.git
cd gestor-comercial-inteligente
```

2. **Instala todas las dependencias necesarias** utilizando pip:

```bash
pip install -r requirements.txt
```

3. **Configura la aplicación** modificando el archivo `config.py`:
   - Introduce tu TOKEN único de Telegram para autenticar el bot.
   - Establece la ruta a la base de datos SQLite.

---

## 🖥️ Ejecución

Para ejecutar el bot, utiliza el siguiente comando:

```bash
python main.py
```

Una vez ejecutado, el bot estará disponible en Telegram para gestionar tus transacciones.

---

## 📖 Comandos Disponibles

Aquí tienes un listado detallado de los comandos disponibles para interactuar con el bot:

- `/venta`: Registrar una nueva venta con asistente guiado.
- `/compra`: Registrar una nueva compra con asistente interactivo.
- `/modificar`: Modificar detalles de una transacción existente.
- `/eliminar`: Eliminar una transacción con confirmación de seguridad.
- `/ganancias`: Mostrar reporte financiero actualizado con ventas, compras y balance neto.
- `/historial`: Visualizar transacciones recientes.
- `/inventario`: Consultar inventario actual con unidades convertidas automáticamente.
- `/exportar_historial`: Descargar el historial completo en formato Excel.
- `/filtrar_historial`: Filtrar transacciones por día o mes específicos.
- `/ultimo_pedido`: Revisar detalles del último registro realizado.
- `/corte`: Generar y descargar un reporte diario de transacciones.

---

## 🔄 Uso del Sistema Inteligente

Además del uso de comandos, puedes interactuar con el bot enviando mensajes en lenguaje natural. El sistema interpreta automáticamente la información proporcionada y realiza la operación correspondiente. Por ejemplo, puedes escribir:

```
Vendí 150 kg de maíz a $5.20/kg a Juan
```

El bot automáticamente registrará esta transacción utilizando inteligencia artificial avanzada.

---

## 📌 Beneficios Clave

- **Ahorro de tiempo**: automatización y reducción de tareas repetitivas.
- **Precisión mejorada**: reducción de errores humanos mediante validación inteligente.
- **Mejores decisiones**: acceso instantáneo a reportes detallados y actualizados.
- **Facilidad de uso**: interfaz intuitiva y accesible desde cualquier dispositivo con Telegram.

---

## 📃 Licencia

Este proyecto se distribuye bajo la licencia MIT. Puedes consultar todos los detalles en el archivo [LICENSE](LICENSE).
