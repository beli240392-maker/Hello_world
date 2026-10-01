import os
import uuid
from utils import lotizacion_required, admin_required, superadmin_required 
from flask import Flask, render_template, request, redirect, url_for, flash, jsonify, session,current_app
from werkzeug.utils import secure_filename
from models import db, Cliente, Lote, Compra, Pago, Cuota, Separacion, Historial, Lotizacion, Voucher
from datetime import datetime, timedelta, date
from flask_login import LoginManager, login_user, logout_user, login_required, current_user
from flask_login import LoginManager
from models import Usuario
from werkzeug.security import check_password_hash, generate_password_hash
from flask import Flask
from flask_login import logout_user, login_required
from flask import send_file, session
from functools import wraps
from flask import session, redirect, url_for, flash
import re
from sqlalchemy import cast, Integer
from flask_login import current_user
import pytz
import os
import io
import openpyxl
from dotenv import load_dotenv
from sqlalchemy import func
from sqlalchemy import or_

import json
import hashlib
import secrets
from decimal import Decimal, InvalidOperation
from itsdangerous import URLSafeTimedSerializer, BadSignature, SignatureExpired


load_dotenv()  # Cargar variables del archivo .env

app = Flask(__name__)
app.config["SECRET_KEY"] = os.getenv("SECRET_KEY")
app.config["SQLALCHEMY_DATABASE_URI"] = os.getenv("DATABASE_URL")
db.init_app(app)


@app.template_filter('from_json')
def from_json_filter(value):
    import json
    try:
        return json.loads(value) if value else []
    except:
        return []




app.config["UPLOAD_FOLDER"] = os.path.join("static", "bouchers")
os.makedirs(app.config["UPLOAD_FOLDER"], exist_ok=True)

CARPETAS_DOCUMENTOS = ["escrituras", "otros_docs"]
for carpeta in CARPETAS_DOCUMENTOS:
    os.makedirs(os.path.join("static", carpeta), exist_ok=True)



# Login manager
login_manager = LoginManager()
login_manager.init_app(app)
login_manager.login_view = "login"

@login_manager.user_loader
def load_user(user_id):
    u = Usuario.query.get(int(user_id))
    if u and not getattr(u, 'activo', True):
        return None  # fuerza logout si está desactivado
    return u


def lotizaciones_permitidas_para(usuario):
    """Devuelve únicamente las lotizaciones que el usuario puede utilizar."""
    if usuario.rol == "superadmin":
        return Lotizacion.query.order_by(Lotizacion.nombre.asc()).all()
    return sorted(usuario.lotizaciones, key=lambda lot: (lot.nombre or "").lower())


def usuario_puede_acceder_lotizacion(usuario, lotizacion_id):
    if not usuario or not usuario.is_authenticated:
        return False
    try:
        lotizacion_id = int(lotizacion_id)
    except (TypeError, ValueError):
        return False

    if usuario.rol == "superadmin":
        return Lotizacion.query.get(lotizacion_id) is not None

    return usuario.puede_acceder_lotizacion(lotizacion_id)


def bloquear_si_no_es_lotizacion_activa(lotizacion_id):
    """Bloquea IDs directos de registros que pertenecen a otra lotización."""
    if current_user.rol == "superadmin":
        return None

    activa = session.get("lotizacion_id")
    try:
        permitido = (
            activa
            and int(activa) == int(lotizacion_id)
            and usuario_puede_acceder_lotizacion(current_user, lotizacion_id)
        )
    except (TypeError, ValueError):
        permitido = False

    if permitido:
        return None

    flash("No tienes acceso a ese registro en la lotización activa.", "danger")
    return redirect(url_for("home"))



# ------------------- FUNCIONES AUXILIARES -------------------
def guardar_boucher(file):
    if not file or not file.filename:
        return None

    filename = secure_filename(file.filename)
    path = os.path.join(app.config["UPLOAD_FOLDER"], filename)
    file.save(path)

    # Guardar en BD la ruta relativa
    return f"bouchers/{filename}".replace("\\", "/")

def generar_cuotas_para_compra(compra):
    if compra.forma_pago == "credito" and compra.cuotas_total > 0 and not compra.cuotas:
        fecha_base = datetime.utcnow()
        for i in range(1, compra.cuotas_total + 1):
            vencimiento = fecha_base + timedelta(days=30 * i)
            cuota = Cuota(
                compra_id=compra.id,
                numero=i,
                monto=compra.cuota_monto,
                fecha_vencimiento=vencimiento
            )
            db.session.add(cuota)
        db.session.commit()


# ------------------- HOME -------------------
@app.route("/login", methods=["GET", "POST"])
def login():
    # Se mantiene la lista para no romper el login.html actual.
    # El permiso real se valida en backend antes de iniciar sesión.
    lotizaciones = Lotizacion.query.order_by(Lotizacion.nombre.asc()).all()

    if request.method == "POST":
        usuario = Usuario.query.filter_by(username=request.form["usuario"]).first()

        if not usuario or not usuario.check_password(request.form["password"]):
            flash("Usuario o contraseña incorrectos", "danger")
            return render_template("login.html", lotizaciones=lotizaciones)

        lotizacion_id = request.form.get("lotizacion_id")
        lot = Lotizacion.query.get(lotizacion_id) if lotizacion_id else None

        if not lot:
            flash("Debes seleccionar una lotización válida.", "warning")
            return render_template("login.html", lotizaciones=lotizaciones)

        if usuario.rol != "superadmin" and not usuario.puede_acceder_lotizacion(lot.id):
            lotizaciones_permitidas = lotizaciones_permitidas_para(usuario)

            flash(
                f"No tienes acceso a '{lot.nombre}'. Selecciona una de las lotizaciones que tienes permitidas.",
                "danger"
            )

            return render_template(
                "login.html",
                lotizaciones=lotizaciones_permitidas
            )

        login_user(usuario)
        # ✅ Guardar último inicio de sesión correcto
        usuario.ultimo_login = hora_local_peru()
        db.session.commit()
        session["lotizacion_id"] = lot.id
        session["lotizacion_nombre"] = lot.nombre
        return redirect(url_for("home"))

    return render_template("login.html", lotizaciones=lotizaciones)


@app.route("/")
@login_required
def home():
    total_disponibles = total_vendidos = total_separados = 0
    lotizacion = None
    if "lotizacion_id" in session:
        lotizacion = Lotizacion.query.get(session["lotizacion_id"])
        total_disponibles = Lote.query.filter_by(estado="disponible", lotizacion_id=lotizacion.id).count()
        total_vendidos = Lote.query.filter_by(estado="vendido", lotizacion_id=lotizacion.id).count()
        total_separados = Lote.query.filter_by(estado="separado", lotizacion_id=lotizacion.id).count()

    return render_template("index.html",
                           total_disponibles=total_disponibles,
                           total_vendidos=total_vendidos,
                           total_separados=total_separados,
                           lotizacion=lotizacion)

# ------------------- DEFINIR FECHA  -------------------

def hora_local_peru():
    tz = pytz.timezone("America/Lima")
    return datetime.now(tz)

lima = pytz.timezone("America/Lima")

# ------------------- LOTES DISPONIBLES -------------------
@app.route("/lotes_disponibles")
@lotizacion_required
@login_required
def lotes_disponibles():
    lotizacion = None
    lotes = []
    if "lotizacion_id" in session:
        lotizacion = Lotizacion.query.get(session["lotizacion_id"])
        lotes = (Lote.query.filter_by(lotizacion_id=lotizacion.id).order_by(cast(Lote.numero, Integer))   # ✅ orden numérico correcto
                 .all()
)

    manzanas = {}
    for lote in lotes:
        manzanas.setdefault(lote.manzana, []).append(lote)

    total_disponibles = Lote.query.filter_by(estado="disponible", lotizacion_id=lotizacion.id).count() if lotizacion else 0
    total_separados = Lote.query.filter_by(estado="separado", lotizacion_id=lotizacion.id).count() if lotizacion else 0
    total_vendidos = Lote.query.filter_by(estado="vendido", lotizacion_id=lotizacion.id).count() if lotizacion else 0

    return render_template("lotes_disponibles.html",
                           manzanas=manzanas,
                           total_disponibles=total_disponibles,
                           total_separados=total_separados,
                           total_vendidos=total_vendidos,
                           lotizacion=lotizacion)

@app.route("/editar_cliente/<int:cliente_id>", methods=["GET", "POST"])
def editar_cliente(cliente_id):
    cliente = Cliente.query.get_or_404(cliente_id)
    lotizacion = None
    if "lotizacion_id" in session:
        lotizacion = Lotizacion.query.get(session["lotizacion_id"])

    if request.method == "POST":
        cliente.nombre = request.form["nombre"].strip().lower()
        cliente.apellidos = request.form["apellidos"].strip().lower()
        cliente.dni = request.form["dni"].strip()
        cliente.telefono = request.form["telefono"].strip()
        cliente.estado_civil = request.form.get("estado_civil", "No registrado").strip().lower()
        cliente.ocupacion = request.form.get("ocupacion", "No registrada").strip().lower()
        cliente.ciudad = request.form["ciudad"].strip().lower()
        cliente.provincia = request.form.get("provincia", "").strip().lower()
        cliente.departamento = request.form.get("departamento", "").strip().lower()
        cliente.direccion = request.form["direccion"].strip().lower()
        cliente.correo = request.form.get("correo", "").strip().lower()

        db.session.commit()
        flash("✅ Cliente actualizado correctamente.", "success")
        return redirect(url_for("ver_cliente", cliente_id=cliente.id))

    return render_template("editar_cliente.html", cliente=cliente, lotizacion=lotizacion)

# ------------------- API para obtener lotes por manzana -------------------
@app.route("/get_lotes/<int:lotizacion_id>")
@login_required
def get_lotes(lotizacion_id):
    if not usuario_puede_acceder_lotizacion(current_user, lotizacion_id):
        return jsonify({"error": "No tienes acceso a esa lotización."}), 403
    lotes = Lote.query.filter_by(lotizacion_id=lotizacion_id, estado="disponible").all()

    data = [
        {
            "id": l.id,
            # cambia el apóstrofe simple ' por el carácter tipográfico ’
            "manzana": l.manzana.replace("'", "´") if l.manzana else "",  
            "numero": l.numero
        }
        for l in lotes
    ]
    return jsonify(data)

@app.route("/detalle_lote/<int:lote_id>", methods=["GET", "POST"])
@login_required
@lotizacion_required
def detalle_lote(lote_id):
    lote = Lote.query.get_or_404(lote_id)

    bloqueo = bloquear_si_no_es_lotizacion_activa(lote.lotizacion_id)
    if bloqueo:
        return bloqueo

    error_linderos = None

    # Guardar los linderos y el perímetro del formulario
    if request.method == "POST":
        campos = (
            "lindero_frente",
            "lindero_derecha",
            "lindero_izquierda",
            "lindero_fondo",
        )

        datos = {
            campo: request.form.get(campo, "").strip()
            for campo in campos
        }

        perimetro_texto = (
            request.form.get("perimetro", "")
            .strip()
            .replace(",", ".")
        )

        perimetro = None

        if perimetro_texto:
            try:
                perimetro = float(perimetro_texto)

                if not (0 < perimetro < float("inf")):
                    raise ValueError()

            except ValueError:
                error_linderos = (
                    "El perímetro debe ser un número mayor que cero."
                )

        if error_linderos is None:
            try:
                for campo, valor in datos.items():
                    setattr(lote, campo, valor or None)

                lote.perimetro = perimetro

                db.session.commit()

                flash(
                    "Linderos y perímetro guardados correctamente.",
                    "success"
                )

                return redirect(
                    url_for("detalle_lote", lote_id=lote.id)
                    + "#linderos"
                )

            except Exception:
                db.session.rollback()

                current_app.logger.exception(
                    "Error al guardar linderos del lote %s",
                    lote_id
                )

                error_linderos = (
                    "No se pudieron guardar los datos. "
                    "Inténtalo nuevamente."
                )

    # Consultar la compra o separación del lote
    compra = None
    separacion = None

    if lote.estado == "vendido":
        compra = Compra.query.filter_by(
            lote_id=lote.id
        ).first()

    elif lote.estado == "separado":
        separacion = Separacion.query.filter_by(
            lote_id=lote.id,
            activa=True
        ).first()

    # Consultar la lotización activa
    lotizacion = None

    if "lotizacion_id" in session:
        lotizacion = Lotizacion.query.get(
            session["lotizacion_id"]
        )

    return render_template(
        "detalle_lote.html",
        lote=lote,
        compra=compra,
        separacion=separacion,
        lotizacion=lotizacion,
        error_linderos=error_linderos
    )

@app.route("/estado_pagos")
def estado_pagos():
    clientes = Cliente.query.all()
    resumen = []
    for cliente in clientes:
        compras = Compra.query.filter_by(cliente_id=cliente.id).all()
        total_precio = sum([c.precio for c in compras])
        total_pagado = 0
        for c in compras:
            total_pagado += c.inicial
            total_pagado += sum([cuota.monto for cuota in c.cuotas if cuota.pagada])
        saldo = total_precio - total_pagado
        resumen.append({
            "cliente": cliente,
            "total_precio": total_precio,
            "total_pagado": total_pagado,
            "saldo": saldo
        })

    lotizacion = None
    if "lotizacion_id" in session:
        lotizacion = Lotizacion.query.get(session["lotizacion_id"])

    return render_template("estado_pagos.html", resumen=resumen, lotizacion=lotizacion)

# ------------------- AGREGAR LOTES -------------------

# Solo acepta manzanas de la A -Z y manzanas con apostrofe o comillas
patron_manzana = re.compile(r"^[A-ZÑ]{1}[´]?$")

@app.route("/agregar_lotes", methods=["GET", "POST"])
@login_required
@admin_required
@lotizacion_required
@superadmin_required
def agregar_lotes():
    # Verifica lotización activa
    lotizacion_id = session.get("lotizacion_id")
    if not lotizacion_id:
        flash("Debes seleccionar una lotización activa antes de agregar lotes.", "danger")
        return redirect(url_for("seleccionar_lotizacion"))

    if request.method == "POST":
        # ===== PASO 1: Mostrar tabla =====
        if (
            "manzana" in request.form
            and "total_lotes" in request.form
            and not request.form.getlist("areas[]")  # 👈 ahora detectamos por las áreas
        ):
            manzana = request.form.get("manzana", "").strip().upper()

            if not patron_manzana.match(manzana):
                flash("Formato de manzana inválido. Usa solo una letra mayúscula y opcionalmente un apostrofe")
                return redirect(url_for("agregar_lotes"))

            try:
                total_lotes = int(request.form.get("total_lotes", "0"))
            except ValueError:
                total_lotes = 0

            if not manzana or total_lotes <= 0:
                flash("Ingresa una manzana y una cantidad de lotes válida.", "danger")
                return redirect(url_for("agregar_lotes"))

            # ✅ buscar último lote existente en esa manzana
            ultimo = (
                db.session.query(db.func.max(Lote.numero.cast(db.Integer)))
                .filter_by(lotizacion_id=lotizacion_id, manzana=manzana)
                .scalar()
            ) or 0

            numeros_auto = [str(ultimo + i) for i in range(1, total_lotes + 1)]

            return render_template(
                "agregar_lotes.html",
                manzana=manzana,
                total_lotes=total_lotes,
                numeros_auto=numeros_auto
            )

        # ===== PASO 2: Guardar lotes =====
        manzana = request.form.get("manzana", "").strip().upper()

        if not patron_manzana.match(manzana):
            flash("Formato de manzana inválido.", "danger")
            return redirect(url_for("agregar_lotes"))

        numeros = request.form.getlist("numeros[]")  # 👈 ahora vienen generados automáticamente
        areas   = request.form.getlist("areas[]")

        guardados = 0
        duplicados = 0
        incompletos = 0

        for numero, area in zip(numeros, areas):
            numero = (numero or "").strip()
            area = (area or "").strip()

            if not numero or not area:
                incompletos += 1
                continue

            # validar duplicado
            existe = Lote.query.filter_by(
                lotizacion_id=lotizacion_id,
                manzana=manzana,
                numero=numero
            ).first()

            if existe:
                duplicados += 1
                continue

            try:
                nuevo = Lote(
                    manzana=manzana,
                    numero=numero,
                    area=float(area),
                    estado="disponible",
                    lotizacion_id=lotizacion_id
                )
                db.session.add(nuevo)
                guardados += 1
            except Exception as e:
                current_app.logger.exception(e)
                incompletos += 1

        db.session.commit()

        msg = f"✅ {guardados} lotes agregados en la manzana {manzana}."
        if duplicados:
            msg += f" ⚠️ {duplicados} no se agregaron porque ya existían."
        if incompletos:
            msg += f" ⚠️ {incompletos} filas estaban incompletas o inválidas."
        flash(msg, "info")

        return redirect(url_for("home"))

    # GET
    return render_template("agregar_lotes_inicio.html")


@app.route("/eliminar_lote/<int:lote_id>", methods=["POST"])
@login_required
@lotizacion_required
@superadmin_required
def eliminar_lote(lote_id):
    lote = Lote.query.get_or_404(lote_id)

    bloqueo = bloquear_si_no_es_lotizacion_activa(lote.lotizacion_id)
    if bloqueo:
        return bloqueo

    # Solo lotes disponibles
    if lote.estado != "disponible":
        flash("⚠️ Solo se pueden eliminar lotes disponibles.", "warning")
        return redirect(url_for("lotes_disponibles"))

    # Verificar que nunca haya tenido movimientos
    tiene_compra = Compra.query.filter_by(lote_id=lote.id).first()
    tiene_separacion = Separacion.query.filter_by(lote_id=lote.id).first()
    tiene_historial = Historial.query.filter_by(lote_id=lote.id).first()
    tiene_voucher = Voucher.query.filter_by(lote_id=lote.id).first()

    if tiene_compra or tiene_separacion or tiene_historial or tiene_voucher:
        flash("⚠️ Este lote tiene movimientos y no puede eliminarse.", "warning")
        return redirect(url_for("lotes_disponibles"))

    db.session.delete(lote)
    db.session.commit()

    flash("✅ Lote eliminado correctamente.", "success")
    return redirect(url_for("lotes_disponibles"))

# ------------------- REGISTRAR AREA DE LOTE -------------------

@app.route("/editar_area/<int:lote_id>", methods=["GET", "POST"])
@login_required
@lotizacion_required
def editar_area(lote_id):
    lote = Lote.query.get_or_404(lote_id)

    bloqueo = bloquear_si_no_es_lotizacion_activa(lote.lotizacion_id)
    if bloqueo:
        return bloqueo

    if request.method == "POST":
        nueva_area = request.form.get("area")
        if nueva_area:
            lote.area = float(nueva_area)
            db.session.commit()
            flash("✅ Área del lote actualizada correctamente", "success")
            return redirect(url_for("lotes_disponibles"))  # tu lista principal de lotes

    return render_template("editar_area.html", lote=lote)


# ------------------- REGISTRAR COMPRA -------------------



@app.route("/agregar_comentario/<int:compra_id>", methods=["POST"])
@login_required
def agregar_comentario(compra_id):
    compra = Compra.query.get_or_404(compra_id)

    bloqueo = bloquear_si_no_es_lotizacion_activa(compra.lote.lotizacion_id)
    if bloqueo:
        return bloqueo
    comentario = request.form.get("comentario", "").strip()

    if comentario:
        compra.comentario = comentario
        db.session.commit()
        flash("✅ Comentario guardado correctamente.", "success")
    else:
        flash("⚠️ El comentario está vacío o no se pudo guardar.", "warning")

    # Redirigir de vuelta a la vista del cliente correcto
    return redirect(url_for("ver_cliente", cliente_id=compra.cliente_id))



# ------------------- REGISTRAR SEPARACION -------------------
@app.route("/registrar_compra", methods=["GET", "POST"]) 
@lotizacion_required
@login_required
def registrar_compra():
    lotizacion = None
    if "lotizacion_id" in session:
        lotizacion = Lotizacion.query.get(session["lotizacion_id"])

    lotes = []
    if lotizacion:
        from urllib.parse import unquote
        manzana_param = request.args.get("manzana") or request.form.get("manzana")

        if manzana_param:
            # ✅ Normalizamos la manzana para evitar errores con apóstrofes o espacios
            manzana_param = unquote(manzana_param).strip().replace("&#39;", "'").replace("&apos;", "'")

            # 🔍 Filtramos de forma más robusta
            lotes = (
                Lote.query
                .filter(
                    Lote.lotizacion_id == lotizacion.id,
                    Lote.estado == "disponible",
                    db.func.replace(Lote.manzana, "'", "'") == manzana_param  # acepta distintos tipos de comilla
                )
                .all()
            )
        else:
            # Muestra todos los disponibles si no hay parámetro
            lotes = Lote.query.filter_by(lotizacion_id=lotizacion.id, estado="disponible").all()

    sep_id = request.args.get("sep_id")
    cliente_id_param = request.args.get("cliente_id")
    lote_id_param = request.args.get("lote_id")

    lote = None
    cliente = None
    separacion = None

    # Si viene desde Detalle del Lote
    if lote_id_param and not sep_id and lotizacion:
        lote = (
            Lote.query
            .filter_by(
                id=int(lote_id_param),
                lotizacion_id=lotizacion.id,
                estado="disponible"
            )
            .first()
        )

        if not lote:
            flash("⚠️ El lote no está disponible o no pertenece a la lotización activa.", "warning")
            return redirect(url_for("lotes_disponibles"))

        if lote not in lotes:
            lotes.append(lote)

    # ✅ Si viene lote_id desde "Detalle del Lote", cargar ese lote disponible
    if lote_id_param and not sep_id and lotizacion:
        lote = (
            Lote.query
            .filter_by(
                id=int(lote_id_param),
                lotizacion_id=lotizacion.id,
                estado="disponible"
            )
            .first()
        )

        if not lote:
            flash("⚠️ El lote no está disponible o no pertenece a la lotización activa.", "warning")
            return redirect(url_for("lotes_disponibles"))

        if lote not in lotes:
            lotes.append(lote)

    # ✅ Si viene cliente_id, cargar el cliente existente
    if cliente_id_param:
        cliente = Cliente.query.get(int(cliente_id_param))
    
    if sep_id:
        separacion = Separacion.query.get(sep_id)
        if separacion:
            lote = separacion.lote
            cliente = separacion.cliente
            # ✅ Si el lote está separado o vendido, lo agregamos manualmente para que aparezca en el combo
            if lote not in lotes:
                lotes.append(lote)

    if request.method == "POST":
        # 👇 Normalizamos a minúsculas y quitamos espacios
        nombre = request.form["nombre"].strip().lower()
        apellidos = request.form.get("apellidos", "").strip().lower()
        dni = request.form["dni"].strip()
        correo = request.form.get("correo", "").strip().lower()
        telefono = request.form.get("telefono", "").strip()
        direccion = request.form.get("direccion", "").strip().lower()
        ciudad = request.form.get("ciudad", "").strip().lower()
        provincia = request.form.get("provincia", "").strip().lower()
        departamento = request.form.get("departamento", "").strip().lower()
        estado_civil = request.form.get("estado_civil", "").strip().lower()
        ocupacion = request.form.get("ocupacion", "").strip().lower()

        precio = float(request.form.get("precio", 0))
        forma_pago = request.form["forma_pago"]
        inicial = float(request.form.get("inicial", 0)) if forma_pago == "credito" else 0
        interes = float(request.form.get("interes") or 0) if forma_pago == "credito" else 0

        try:
            cuotas_total = (
                int(request.form.get("cuotas") or 0)
                if forma_pago == "credito" else 0
            )
        except (ValueError, TypeError):
            cuotas_total = None

        # Validar antes de guardar clientes, compras o cambiar el lote.
        if forma_pago == "credito" and (
            cuotas_total is None
            or cuotas_total < 0
            or (precio > inicial and cuotas_total == 0)
        ):
            flash(
                "Si es una compra al credito , cuotas no puede ser cero. "
                "Debes indicar al menos 1 cuota.",
                "warning"
            )

            return render_template(
                "registrar_compra.html",
                lotes=lotes,
                lote=lote,
                cliente=cliente,
                sep_id=sep_id,
                separacion=separacion,
                lotizacion=lotizacion
            ), 400
       
        # Cliente
        # ✅ Si viene cliente_id en el formulario (hidden), usar ese cliente
        cliente_id_form = request.form.get("cliente_id")
        if cliente_id_form:
            cliente = Cliente.query.get(int(cliente_id_form))
            # Actualizar datos del cliente si es necesario
            if cliente:
                cliente.nombre = nombre
                cliente.apellidos = apellidos
                cliente.telefono = telefono
                cliente.direccion = direccion
                cliente.ciudad = ciudad
                cliente.provincia = provincia
                cliente.departamento = departamento
                cliente.estado_civil = estado_civil
                cliente.ocupacion = ocupacion
                if correo:
                    cliente.correo = correo
                db.session.commit()
        else:
            # Buscar por DNI si no viene cliente_id
            cliente = Cliente.query.filter_by(dni=dni).first()
        
        if not cliente:
            cliente = Cliente(
                nombre=nombre,
                apellidos=apellidos,
                dni=dni,
                telefono=telefono,
                direccion=direccion,
                ciudad=ciudad,
                provincia=provincia,
                departamento=departamento,
                estado_civil=estado_civil,  
                ocupacion=ocupacion,
                correo=correo if correo else None 
            )
            db.session.add(cliente)
            db.session.commit()  # ✅ Commit para obtener el ID
        else:
            cliente.nombre = nombre
            cliente.apellidos = apellidos
            cliente.telefono = telefono
            cliente.direccion = direccion
            cliente.ciudad = ciudad
            cliente.provincia = provincia
            cliente.departamento = departamento
            cliente.estado_civil = estado_civil
            cliente.ocupacion = ocupacion
            if correo:  # 👈 NUEVO: Solo actualiza si hay correo
                cliente.correo = correo
            db.session.commit()  # ✅ Commit para actualizar datos

        # ✅ Guardar fotos de DNI (solo si se suben nuevas)
        dni_frontal_file = request.files.get("dni_frontal")
        dni_reverso_file = request.files.get("dni_reverso")

        if dni_frontal_file and dni_frontal_file.filename:
            from werkzeug.utils import secure_filename
            filename = secure_filename(dni_frontal_file.filename)
            save_path = os.path.join("static", "dni", filename)
            os.makedirs(os.path.dirname(save_path), exist_ok=True)
            dni_frontal_file.save(save_path)
            cliente.dni_frontal = f"dni/{filename}".replace("\\", "/")

        if dni_reverso_file and dni_reverso_file.filename:
            from werkzeug.utils import secure_filename
            filename = secure_filename(dni_reverso_file.filename)
            save_path = os.path.join("static", "dni", filename)
            os.makedirs(os.path.dirname(save_path), exist_ok=True)
            dni_reverso_file.save(save_path)
            cliente.dni_reverso = f"dni/{filename}".replace("\\", "/")

        if dni_frontal_file and dni_frontal_file.filename or dni_reverso_file and dni_reverso_file.filename:
            db.session.commit()  # ✅ Commit para guardar las fotos solo si se subieron

        # Lote
        # Lote
        lote_id = request.form.get("lote")

        if not lote_id and lote:
            lote_id = lote.id

        lote = Lote.query.get(lote_id)

        # ✅ Evitar convertir dos veces una separación
        sep_id_form = request.form.get("sep_id")

        if sep_id_form:
            compra_existente = Compra.query.filter_by(lote_id=lote.id).first()

            if compra_existente:
                flash(
                    "⚠️ Esta separación ya fue convertida en compra anteriormente.",
                    "warning"
                )
                return redirect(
                    url_for(
                        "ver_cliente",
                        cliente_id=compra_existente.cliente_id
                    )
                )

        lote.estado = "vendido"

        # ✅ Guardar boucher inicial
        boucher_file = request.files.get("boucher_inicial")
        boucher_path = None
        if boucher_file and boucher_file.filename:
            from werkzeug.utils import secure_filename
            boucher_path = guardar_boucher(boucher_file)

        # Calcular precio total con interés
        monto_total = precio
        if forma_pago == "credito" and interes > 0:
            saldo_a_financiar = precio - inicial
            interes_monto = saldo_a_financiar * (interes / 100)
            monto_total = precio + interes_monto
        
        # ✅ Fecha de compra (si no se ingresa, usa la actual)
        fecha_compra_str = request.form.get("fecha_compra")
        if fecha_compra_str:
            try:
                fecha_compra = datetime.strptime(fecha_compra_str, "%Y-%m-%d")
            except ValueError:
                fecha_compra = datetime.utcnow()
        else:
            fecha_compra = datetime.utcnow()

        # Crear compra
        compra = Compra(
            cliente_id=cliente.id,  # ✅ Ahora cliente.id existe
            lote_id=lote.id,
            forma_pago=forma_pago,
            precio=monto_total,
            inicial=inicial,
            cuotas_total=cuotas_total,
            cuota_monto=(monto_total - inicial) / cuotas_total if forma_pago == "credito" and cuotas_total > 0 else 0,
            boucher_inicial=boucher_path,
            interes=interes,
            usuario_id=current_user.id,
            fecha_compra=fecha_compra
        )
        db.session.add(compra)
        db.session.flush()  # ✅ flush para obtener compra.id antes del commit final

        # ✅ Si es al contado, marcar como cancelado inmediatamente
        if forma_pago == "contado":
            compra.cancelado = True
            compra.fecha_cancelacion = fecha_compra

       # Generar cuotas
        if forma_pago == "credito" and cuotas_total > 0:
            for i in range(1, cuotas_total + 1):
                # Calcula directamente desde fecha_compra
                fecha_vencimiento = fecha_compra + timedelta(days=30 * i)
                cuota = Cuota(
                    compra_id=compra.id,
                    numero=i,
                    monto=compra.cuota_monto,
                    fecha_vencimiento=fecha_vencimiento
                )
                db.session.add(cuota)

        # Si venía de separación → marcar inactiva
        sep_id = request.form.get("sep_id")
        if sep_id:
            separacion = Separacion.query.get(sep_id)
            if separacion:
                separacion.activa = False
                historial = Historial(
                    cliente_id=cliente.id,
                    lote_id=lote.id,
                    tipo="Separación convertida",
                    detalle=f"Separación de S/ {separacion.monto} convertida en compra"
                )
                db.session.add(historial)

        db.session.commit()  # ✅ Commit final
        flash("Compra registrada correctamente.", "success")
        return redirect(url_for("ver_cliente", cliente_id=cliente.id))

    return render_template(
        "registrar_compra.html",
        lotes=lotes,
        lote=lote,
        cliente=cliente,
        sep_id=sep_id,
        separacion=separacion,
        lotizacion=lotizacion
    )

@app.route("/registrar_separacion", methods=["GET", "POST"])
@lotizacion_required
@login_required
def registrar_separacion():
    lotizacion = None
    if "lotizacion_id" in session:
        lotizacion = Lotizacion.query.get(session["lotizacion_id"])

    lotes = []
    lote = None

    if lotizacion:
        lotes = Lote.query.filter_by(
            estado="disponible",
            lotizacion_id=lotizacion.id
        ).all()

        # ✅ Si viene lote_id desde "Detalle del Lote", cargar ese lote disponible
        lote_id_param = request.args.get("lote_id", type=int)

        if lote_id_param:
            lote = (
                Lote.query
                .filter_by(
                    id=lote_id_param,
                    lotizacion_id=lotizacion.id,
                    estado="disponible"
                )
                .first()
            )

            if not lote:
                flash(
                    "⚠️ El lote no está disponible o no pertenece a la lotización activa.",
                    "warning"
                )
                return redirect(url_for("lotes_disponibles"))

    if request.method == "POST":
        nombre = request.form["nombre"].strip().lower()
        apellidos = request.form.get("apellidos").strip().lower()
        dni = request.form["dni"].strip()
        telefono = request.form.get("telefono").strip()
        direccion = request.form.get("direccion").strip().lower()
        ciudad = request.form.get("ciudad").strip().lower()
        provincia = request.form.get("provincia", "").strip().lower()
        departamento = request.form.get("departamento", "").strip().lower()
        correo = request.form.get("correo", "").strip().lower()

        # 🔹 Nuevos campos
        estado_civil = request.form.get("estado_civil").strip().lower()
        ocupacion = request.form.get("ocupacion").strip().lower()

        monto = float(request.form.get("monto", 0))
        lote_id = request.form["lote"]

        # ✅ Buscar cliente existente o crearlo
        cliente = Cliente.query.filter_by(dni=dni).first()
        if not cliente:
            cliente = Cliente(
                nombre=nombre,
                apellidos=apellidos,
                dni=dni,
                telefono=telefono,
                direccion=direccion,
                ciudad=ciudad,
                provincia=provincia,
                departamento=departamento,
                estado_civil=estado_civil,
                ocupacion=ocupacion,
                correo=correo if correo else None
            )
            db.session.add(cliente)
            db.session.commit()
        else:
            # 🔹 Si ya existe, actualizar sus datos
            cliente.estado_civil = estado_civil or cliente.estado_civil
            cliente.ocupacion = ocupacion or cliente.ocupacion
            cliente.telefono = telefono or cliente.telefono
            cliente.direccion = direccion or cliente.direccion
            cliente.ciudad = ciudad or cliente.ciudad
            cliente.provincia = provincia or cliente.provincia
            cliente.departamento = departamento or cliente.departamento
            cliente.correo = correo or cliente.correo

        # ✅ Subida de fotos de DNI
        dni_frontal_file = request.files.get("dni_frontal")
        dni_reverso_file = request.files.get("dni_reverso")

        if dni_frontal_file and dni_frontal_file.filename:
            from werkzeug.utils import secure_filename
            filename = secure_filename(dni_frontal_file.filename)
            save_path = os.path.join("static", "dni", filename)
            os.makedirs(os.path.dirname(save_path), exist_ok=True)
            dni_frontal_file.save(save_path)
            cliente.dni_frontal = f"dni/{filename}".replace("\\", "/")

        if dni_reverso_file and dni_reverso_file.filename:
            from werkzeug.utils import secure_filename
            filename = secure_filename(dni_reverso_file.filename)
            save_path = os.path.join("static", "dni", filename)
            os.makedirs(os.path.dirname(save_path), exist_ok=True)
            dni_reverso_file.save(save_path)
            cliente.dni_reverso = f"dni/{filename}".replace("\\", "/")

        db.session.commit()

        # ✅ Subida de boucher
        boucher_file = request.files.get("boucher")
        boucher_path = None
        if boucher_file and boucher_file.filename:
            boucher_path = guardar_boucher(boucher_file)

        # ✅ Crear la separación con cliente_id correcto
        separacion = Separacion(
            cliente_id=cliente.id,
            lote_id=lote_id,
            monto=monto,
            fecha=hora_local_peru(),
            boucher=boucher_path,
            activa=True,
            usuario_id=current_user.id
        )
        db.session.add(separacion)

        # Cambiar estado del lote
        lote = Lote.query.get(lote_id)
        lote.estado = "separado"

        db.session.commit()
        flash("Separación registrada correctamente.", "success")
        return redirect(url_for("ver_cliente", cliente_id=cliente.id))

    return render_template(
        "registrar_separacion.html",
        lotes=lotes,
        lote=lote,
        lotizacion=lotizacion
    )




# ------------------- VER CLIENTE -------------------
@app.route("/ver_cliente", methods=["GET", "POST"])
@lotizacion_required
@login_required
def ver_cliente():
    cliente = None
    separaciones = []
    compras_contado = []
    compras_credito = []
    historial = []

    # Vouchers de separaciones que fueron convertidas en compra
    vouchers_separacion = {}

    # Obtener lotización activa desde la sesión
    lotizacion = None
    if "lotizacion_id" in session:
        lotizacion = Lotizacion.query.get(session["lotizacion_id"])

    lotizacion_id = lotizacion.id if lotizacion else None

    # Buscar cliente
    cliente_id = request.args.get("cliente_id")

    if cliente_id:
        cliente = Cliente.query.get(int(cliente_id))

    elif request.method == "POST":
        criterio = request.form.get("criterio")

        if criterio and lotizacion_id:
            cliente = (
                Cliente.query
                .join(Separacion, isouter=True)
                .join(Compra, isouter=True)
                .join(Lote, isouter=True)
                .filter(
                    (
                        (Cliente.dni == criterio)
                        | (Cliente.apellidos.ilike(f"%{criterio}%"))
                    ),
                    Lote.lotizacion_id == lotizacion_id
                )
                .distinct()
                .all()
            )

    if isinstance(cliente, list):
        cliente = cliente[0] if cliente else None

    # Si se encontró cliente, traer sus datos SOLO
    # de la lotización activa
    if cliente and lotizacion_id:

        separaciones = (
            Separacion.query
            .join(Lote)
            .filter(
                Separacion.cliente_id == cliente.id,
                Separacion.activa == True,
                Lote.lotizacion_id == lotizacion_id
            )
            .all()
        )

        compras_contado = (
            Compra.query
            .join(Lote)
            .filter(
                Compra.cliente_id == cliente.id,
                Compra.forma_pago == "contado",
                Lote.lotizacion_id == lotizacion_id
            )
            .all()
        )

        compras_credito = (
            Compra.query
            .join(Lote)
            .filter(
                Compra.cliente_id == cliente.id,
                Compra.forma_pago == "credito",
                Lote.lotizacion_id == lotizacion_id
            )
            .all()
        )

        historial = (
            Historial.query
            .join(Lote)
            .filter(
                Historial.cliente_id == cliente.id,
                Lote.lotizacion_id == lotizacion_id
            )
            .all()
        )

        # =====================================================
        # VOUCHER DE SEPARACIÓN CONVERTIDA EN COMPRA
        # =====================================================
        for compra in compras_contado + compras_credito:

            # Revisamos el último evento importante de ese
            # cliente y lote.
            ultimo_evento = (
                Historial.query
                .filter(
                    Historial.cliente_id == compra.cliente_id,
                    Historial.lote_id == compra.lote_id,
                    Historial.tipo.in_([
                        "Separación convertida",
                        "Compra liberada"
                    ])
                )
                .order_by(
                    Historial.fecha.desc(),
                    Historial.id.desc()
                )
                .first()
            )

            # Solo si realmente provino de una separación
            if (
                ultimo_evento
                and ultimo_evento.tipo == "Separación convertida"
            ):

                separacion_convertida = (
                    Separacion.query
                    .filter(
                        Separacion.cliente_id == compra.cliente_id,
                        Separacion.lote_id == compra.lote_id,
                        Separacion.activa == False,
                        Separacion.boucher.isnot(None)
                    )
                    .order_by(
                        Separacion.fecha.desc(),
                        Separacion.id.desc()
                    )
                    .first()
                )

                if (
                    separacion_convertida
                    and separacion_convertida.boucher
                ):
                    vouchers_separacion[compra.id] = (
                        separacion_convertida
                    )

    return render_template(
        "ver_cliente.html",
        cliente=cliente,
        separaciones=separaciones,
        compras_contado=compras_contado,
        compras_credito=compras_credito,
        historial=historial,
        vouchers_separacion=vouchers_separacion,
        now=datetime.now(lima),
        lotizacion=lotizacion,
        pytz=pytz
    )

# ------------------- DETALLE CUOTAS -------------------
@app.route("/detalle_cuotas/<int:compra_id>")
@login_required
@lotizacion_required
def detalle_cuotas(compra_id):
    compra = Compra.query.get_or_404(compra_id)

    bloqueo = bloquear_si_no_es_lotizacion_activa(compra.lote.lotizacion_id)
    if bloqueo:
        return bloqueo
    now = datetime.utcnow()

    lotizacion = None
    if "lotizacion_id" in session:
        lotizacion = Lotizacion.query.get(session["lotizacion_id"])

    return render_template("detalle_cuotas.html", compra=compra, now=now,
                           cliente_id=compra.cliente.id, lotizacion=lotizacion, pytz=pytz)

# ------------------- PAGAR CUOTA -------------------
@app.route("/pagar_cuota", methods=["POST"])
@login_required
@lotizacion_required
def pagar_cuota():
    cuota_id = request.form["cuota_id"]
    cuota = Cuota.query.get_or_404(cuota_id)
    compra = cuota.compra

    bloqueo = bloquear_si_no_es_lotizacion_activa(compra.lote.lotizacion_id)
    if bloqueo:
        return bloqueo
    
    # ✅ NUEVA VALIDACIÓN: Verificar que no haya cuotas anteriores sin pagar
    cuotas_anteriores_pendientes = Cuota.query.filter(
        Cuota.compra_id == cuota.compra_id,
        Cuota.numero < cuota.numero,
        Cuota.pagada == False
    ).first()
    
    # Si hay cuotas pendientes anteriores y no se autorizó el pago forzado
    if cuotas_anteriores_pendientes and not request.form.get('forzar_pago'):
        flash(
            f"❌ No puedes pagar la cuota #{cuota.numero}. "
            f"Primero debes pagar la cuota #{cuotas_anteriores_pendientes.numero}.", 
            "danger"
        )
        return redirect(url_for("detalle_cuotas", compra_id=cuota.compra_id))
    
    # Si ya está pagada
    if cuota.pagada:
        flash("⚠️ Esta cuota ya ha sido pagada.", "warning")
        return redirect(url_for("detalle_cuotas", compra_id=cuota.compra_id))
    
    # Guardar boucher
    boucher_file = request.files.get("boucher_cuota")
    boucher_path = guardar_boucher(boucher_file)
    
    # Registrar pago
    pago = Pago(compra_id=cuota.compra_id, monto=cuota.monto, boucher=boucher_path)
    db.session.add(pago)
    db.session.flush()  # ✅ CRÍTICO: Hacer flush para obtener pago.id
    
    # Marcar cuota como pagada
    cuota.pagada = True
    cuota.pago_id = pago.id  # ✅ Ahora pago.id ya tiene valor
    
    # Verificar si se completó el pago total
    if compra.verificar_cancelacion():
        db.session.commit()
        flash("🎉 ¡Felicidades! Cuota pagada y compra TOTALMENTE CANCELADA.", "success")
    else:
        db.session.commit()
        flash("✅ Cuota pagada correctamente.", "success")
    
    return redirect(url_for("detalle_cuotas", compra_id=cuota.compra_id))

@app.route("/pagar_todas_cuotas/<int:compra_id>", methods=["GET", "POST"])
@login_required
@lotizacion_required
def pagar_todas_cuotas(compra_id):
    compra = Compra.query.get_or_404(compra_id)

    bloqueo = bloquear_si_no_es_lotizacion_activa(compra.lote.lotizacion_id)
    if bloqueo:
        return bloqueo
    
    # Obtener solo las cuotas pendientes
    cuotas_pendientes = Cuota.query.filter(
        Cuota.compra_id == compra_id,
        Cuota.pagada == False
    ).order_by(Cuota.numero).all()
    
    # Si no hay cuotas pendientes
    if not cuotas_pendientes:
        flash("⚠️ No hay cuotas pendientes para pagar.", "warning")
        return redirect(url_for("detalle_cuotas", compra_id=compra_id))
    
    if request.method == "POST":
        # Validar que se subió un boucher
        boucher_file = request.files.get("boucher_todas")
        if not boucher_file or not boucher_file.filename:
            flash("❌ Debes subir un voucher para proceder.", "danger")
            return redirect(url_for("pagar_todas_cuotas", compra_id=compra_id))
        
        # Guardar boucher una sola vez
        boucher_path = guardar_boucher(boucher_file)
        
        try:
            # Calcular monto total SIN descuento
            monto_total_cuotas = sum([c.monto for c in cuotas_pendientes])
            
            # ✨ NUEVO: Calcular descuento de interés proporcional
            cuotas_pagadas = len([c for c in compra.cuotas if c.pagada])
            cuotas_totales = compra.cuotas_total
            cuotas_faltantes = len(cuotas_pendientes)
            
            saldo_financiar = compra.precio - compra.inicial
            interes_total = saldo_financiar * (compra.interes / 100)
            interes_proporcional = interes_total * (cuotas_faltantes / compra.cuotas_total)
            # Monto final CON descuento de interés
            monto_final = monto_total_cuotas - interes_proporcional
            
            pago = Pago(
                compra_id=compra_id,
                monto=monto_final,  # ✨ Usa el monto con descuento
                boucher=boucher_path
            )
            db.session.add(pago)
            db.session.flush()  # Obtener pago.id
            
            # Marcar todas las cuotas pendientes como pagadas
            for cuota in cuotas_pendientes:
                cuota.pagada = True
                cuota.pago_id = pago.id
            
            # Verificar si se completó el pago total
            if compra.verificar_cancelacion():
                db.session.commit()
                flash(f"🎉 ¡Felicidades! Todas las cuotas pagadas y compra TOTALMENTE CANCELADA. Descuento por pago anticipado: S/ {interes_proporcional:.2f}", "success")
            else:
                db.session.commit()
                flash(f"✅ {len(cuotas_pendientes)} cuota(s) pagada(s) correctamente. Descuento aplicado: S/ {interes_proporcional:.2f}", "success")
            
            return redirect(url_for("detalle_cuotas", compra_id=compra_id))
        
        except Exception as e:
            db.session.rollback()
            current_app.logger.error(f"Error al pagar todas las cuotas: {e}")
            flash("❌ Error al procesar el pago", "danger")
            return redirect(url_for("pagar_todas_cuotas", compra_id=compra_id))
    
    # GET - Mostrar resumen de cuotas a pagar
    monto_total_sin_descuento = sum([c.monto for c in cuotas_pendientes])
    
    # ✨ NUEVO: Calcular descuento de interés
    cuotas_faltantes = len(cuotas_pendientes)
    saldo_financiar = compra.precio - compra.inicial
    interes_total = saldo_financiar * (compra.interes / 100)
    interes_proporcional = interes_total * (cuotas_faltantes / compra.cuotas_total)

    monto_total_con_descuento = monto_total_sin_descuento - interes_proporcional
    
    numeros_cuotas = [c.numero for c in cuotas_pendientes]
    
    return render_template(
        "pagar_todas_cuotas.html",
        compra=compra,
        cuotas_pendientes=cuotas_pendientes,
        monto_total=monto_total_sin_descuento,
        monto_final=monto_total_con_descuento,
        interes_proporcional=interes_proporcional,
        numeros_cuotas=numeros_cuotas
    )

# ------------------- LIBERAR LOTE -------------------
@app.route("/liberar_lote/<int:id>/<string:tipo>", methods=["POST"])
@login_required
@lotizacion_required
@admin_required
def liberar_lote(id, tipo):
    lote = Lote.query.get_or_404(id)

    bloqueo = bloquear_si_no_es_lotizacion_activa(lote.lotizacion_id)
    if bloqueo:
        return bloqueo
    cliente_id = None  # para redireccionar luego

    if tipo == "separacion":
        sep = Separacion.query.filter_by(lote_id=lote.id, activa=True).first()
        if sep:
            sep.activa = False
            lote.estado = "disponible"
            cliente_id = sep.cliente_id

            historial = Historial(
                cliente_id=sep.cliente_id,
                lote_id=sep.lote_id,
                tipo="Separación liberada",
                detalle=f"Separación de S/ {sep.monto:.2f} liberada",
                fecha=hora_local_peru()
            )
            db.session.add(historial)

    elif tipo == "compra":
        compra = Compra.query.filter_by(lote_id=lote.id).first()
        if compra:
            cliente_id = compra.cliente_id

            # Si era a crédito → eliminar cuotas
            if compra.forma_pago == "credito":
                Cuota.query.filter_by(compra_id=compra.id).delete()

            db.session.delete(compra)
            lote.estado = "disponible"

            historial = Historial(
                cliente_id=compra.cliente_id,
                lote_id=compra.lote_id,
                tipo="Compra liberada",
                detalle=f"Compra de S/ {compra.precio:.2f} liberada",
                fecha=hora_local_peru()
            )
            db.session.add(historial)

    db.session.commit()
    flash("Lote liberado correctamente.", "success")
    return redirect(url_for("ver_cliente", cliente_id=cliente_id))



@app.route("/liberar_separacion/<int:sep_id>", methods=["POST"])
@login_required
@lotizacion_required
def liberar_separacion(sep_id):
    sep = Separacion.query.get_or_404(sep_id)

    bloqueo = bloquear_si_no_es_lotizacion_activa(sep.lote.lotizacion_id)
    if bloqueo:
        return bloqueo
    lote = sep.lote
    lote.estado = "disponible"
    historial = Historial(cliente_id=sep.cliente_id, lote_id=sep.lote_id, tipo="Separación liberada",
                          detalle=f"Separación de S/ {sep.monto:.2f} liberada", fecha=hora_local_peru())
    db.session.add(historial)
    db.session.delete(sep)
    db.session.commit()
    flash("Separación liberada correctamente.", "success")
    return redirect(url_for("ver_cliente", cliente_id=sep.cliente_id))



@app.route("/convertir_separacion/<int:sep_id>", methods=["POST"])
@login_required
@lotizacion_required
def convertir_separacion(sep_id):
    sep = Separacion.query.get_or_404(sep_id)

    bloqueo = bloquear_si_no_es_lotizacion_activa(
        sep.lote.lotizacion_id
    )
    if bloqueo:
        return bloqueo

    if not sep.activa:
        flash(
            "La separación ya no está activa.",
            "danger"
        )
        return redirect(
            url_for(
                "ver_cliente",
                cliente_id=sep.cliente_id
            )
        )

    # Ya no crea la compra directamente.
    # Envía al formulario normal de compra.
    return redirect(
        url_for(
            "registrar_compra",
            sep_id=sep.id
        )
    )

# ------------------- LOGIN LOTIZACION -------------------
@app.route("/seleccionar_lotizacion", methods=["GET", "POST"])
@login_required
def seleccionar_lotizacion():
    lotizaciones = lotizaciones_permitidas_para(current_user)

    if request.method == "POST":
        lotizacion_id = request.form.get("lotizacion_id")

        if not lotizacion_id:
            flash("Debes seleccionar una lotización.", "warning")
            return redirect(url_for("seleccionar_lotizacion"))

        try:
            lotizacion_id = int(lotizacion_id)
        except ValueError:
            flash("ID de lotización inválido.", "danger")
            return redirect(url_for("seleccionar_lotizacion"))

        lot = Lotizacion.query.get(lotizacion_id)
        if not lot:
            flash("La lotización seleccionada no existe.", "danger")
            return redirect(url_for("seleccionar_lotizacion"))

        if not usuario_puede_acceder_lotizacion(current_user, lot.id):
            flash("No tienes acceso a esa lotización.", "danger")
            return redirect(url_for("seleccionar_lotizacion"))

        session["lotizacion_id"] = lot.id
        session["lotizacion_nombre"] = lot.nombre

        flash(f"Lotización activa: {lot.nombre}", "success")
        next_url = request.args.get("next") or url_for("home")
        return redirect(next_url)

    return render_template("seleccionar_lotizacion.html", lotizaciones=lotizaciones)


@app.route("/subir_acta/<int:compra_id>", methods=["POST"])
@login_required
@lotizacion_required
def subir_acta(compra_id):
    compra = Compra.query.get_or_404(compra_id)

    bloqueo = bloquear_si_no_es_lotizacion_activa(compra.lote.lotizacion_id)
    if bloqueo:
        return bloqueo

    # Archivo subido
    acta_file = request.files.get("acta_file")
    if acta_file and acta_file.filename:
        from werkzeug.utils import secure_filename
        filename = secure_filename(acta_file.filename)
        save_path = os.path.join("static", "actas", filename)
        os.makedirs(os.path.dirname(save_path), exist_ok=True)
        acta_file.save(save_path)

        compra.acta_entrega = f"actas/{filename}"

    # Marcamos como entregado
    compra.entregado = True

    db.session.commit()
    flash("✅ Acta de entrega subida y marcada como entregado.", "success")
    return redirect(url_for("ver_cliente", cliente_id=compra.cliente_id))

# ------------------- REPORTES -------------------
@app.route("/reportes", methods=["GET"])
@login_required
@lotizacion_required
def reportes():
    lotizacion_id = session.get("lotizacion_id")

    # Obtener fecha desde query params, si no → hoy
    # Obtener fecha desde query params, si no → hoy en hora de Perú
    fecha_str = request.args.get("fecha")
    try:
        if fecha_str:
            fecha = datetime.strptime(fecha_str, "%Y-%m-%d").date()
        else:
            fecha = hora_local_peru().date()  # ✅ Fecha actual de Perú
    except ValueError:
        fecha = hora_local_peru().date()

    # Convertir fecha a datetime SIN zona horaria (para mantener compatibilidad)
    fecha_datetime = datetime.combine(fecha, datetime.min.time())

    # === Consultas ===
    separaciones = (
        Separacion.query.filter_by(activa=True)
        .join(Lote)
        .filter(Lote.lotizacion_id == lotizacion_id)
        .all()
    )

    compras_contado = (
        Compra.query.filter_by(forma_pago="contado")
        .filter(db.func.date(Compra.fecha_compra) == fecha)
        .join(Lote)
        .filter(Lote.lotizacion_id == lotizacion_id)
        .all()
    )

    compras_credito = (
        Compra.query.filter_by(forma_pago="credito")
        .filter(db.func.date(Compra.fecha_compra) == fecha)
        .join(Lote)
        .filter(Lote.lotizacion_id == lotizacion_id)
        .all()
    )

    cuotas_vencidas = (
        Cuota.query.filter(
            Cuota.fecha_vencimiento < fecha_datetime,
            Cuota.pagada == False
        )
        .join(Compra)
        .join(Lote)
        .filter(Lote.lotizacion_id == lotizacion_id)
        .all()
    )

    fecha_limite = fecha_datetime + timedelta(days=7)
    cuotas_por_vencer = (
        Cuota.query.filter(
            Cuota.fecha_vencimiento >= fecha_datetime,
            Cuota.fecha_vencimiento <= fecha_limite,
            Cuota.pagada == False
        )
        .join(Compra)
        .join(Lote)
        .filter(Lote.lotizacion_id == lotizacion_id)
        .all()
    )

    return render_template(
        "reportes.html",
        fecha=fecha,
        fecha_datetime=fecha_datetime,  # ✅ Pasar también como datetime
        separaciones=separaciones,
        compras_contado=compras_contado,
        compras_credito=compras_credito,
        cuotas_vencidas=cuotas_vencidas,
        cuotas_por_vencer=cuotas_por_vencer
    )

@app.route("/buscar_cliente", methods=["GET"])
@login_required
@lotizacion_required
def buscar_cliente():
    query = request.args.get("q", "").strip()
    clientes = []

    if query:
        lotizacion_id = session.get("lotizacion_id")
        term = f"%{query.lower()}%"

        # Subquery de separaciones
        cli_ids_sep = (
            db.session.query(Separacion.cliente_id.label("cliente_id"))
            .join(Lote, Lote.id == Separacion.lote_id)
            .filter(Lote.lotizacion_id == lotizacion_id)
        )

        # Subquery de compras
        cli_ids_com = (
            db.session.query(Compra.cliente_id.label("cliente_id"))
            .join(Lote, Lote.id == Compra.lote_id)
            .filter(Lote.lotizacion_id == lotizacion_id)
        )

        # Unimos ambos subqueries
        activos_subq = cli_ids_sep.union(cli_ids_com).subquery()

        # Ahora sí filtramos clientes
        clientes = (
            Cliente.query
            .join(activos_subq, activos_subq.c.cliente_id == Cliente.id)
            .filter(
            or_(
                db.func.lower(Cliente.dni).like(term),
                db.func.lower(Cliente.apellidos).like(f"%{query.lower()}%")
            )
    )
    .distinct()
    .all()
)
    return render_template("buscar_cliente.html", query=query, clientes=clientes)

@app.route("/buscar_cliente_json")
@login_required
def buscar_cliente_json():
    q = request.args.get("q", "").strip()
    if len(q) < 2:
        return jsonify([])
    
    term = f"%{q.lower()}%"
    clientes = Cliente.query.filter(
        or_(
            db.func.lower(Cliente.apellidos).like(term),
            db.func.lower(Cliente.dni).like(term)
        )
    ).limit(10).all()
    
    return jsonify([{
        "id": c.id,
        "nombre": c.nombre,
        "apellidos": c.apellidos,
        "dni": c.dni
    } for c in clientes])

@app.route("/autocomplete_clientes")
@login_required
def autocomplete_clientes():
    term = request.args.get("term", "")
    lotizacion_id = session.get("lotizacion_id")

    if not term or not lotizacion_id:
        return jsonify([])

    clientes = (
        Cliente.query
        .join(Separacion, isouter=True)
        .join(Compra, isouter=True)
        .join(Lote, isouter=True)
        .filter(
            ((Cliente.dni.ilike(f"%{term}%")) | (Cliente.apellidos.ilike(f"%{term}%"))),
            Lote.lotizacion_id == lotizacion_id
        )
        .all()
    )

    results = [
        {"id": c.id, "label": f"{c.apellidos} {c.nombre} - {c.dni}", "value": c.apellidos}
        for c in clientes
    ]

    return jsonify(results)


@app.route("/get_cliente_por_dni")
@login_required
def get_cliente_por_dni():
    dni = request.args.get("dni", "").strip()
    lotizacion_id = session.get("lotizacion_id")
    
    if not dni:
        return jsonify(None)
    
    # Buscar cliente que tenga compra o separación en la lotización actual
    cliente = (
        Cliente.query
        .filter_by(dni=dni)
        .join(Compra, isouter=True)
        .join(Separacion, isouter=True)
        .join(Lote, or_(
            Lote.id == Compra.lote_id,
            Lote.id == Separacion.lote_id
        ), isouter=True)
        .filter(Lote.lotizacion_id == lotizacion_id)
        .first()
    )

    if not cliente:
        return jsonify(None)
    
    return jsonify({
        "id": cliente.id,
        "nombre": cliente.nombre,
        "apellidos": cliente.apellidos,
        "dni": cliente.dni,
        "correo": cliente.correo or "",
        "estado_civil": cliente.estado_civil or "",
        "ocupacion": cliente.ocupacion or "",
        "telefono": cliente.telefono or "",
        "direccion": cliente.direccion or "",
        "ciudad": cliente.ciudad or "",
        "provincia": cliente.provincia or "",
        "departamento": cliente.departamento or "",
        "dni_frontal": cliente.dni_frontal or "",
        "dni_reverso": cliente.dni_reverso or ""
    })

@app.route("/exportar_ventas", methods=["GET"])
@login_required
@lotizacion_required
def exportar_ventas():
    """Exporta las ventas a Excel con TODA la información del cliente"""
    
    import openpyxl
    from openpyxl.styles import Font, PatternFill, Border, Side, Alignment
    from openpyxl.utils import get_column_letter
    from io import BytesIO
    
    lotizacion_id = session.get("lotizacion_id")

    if not lotizacion_id:
        flash("No hay una lotización activa seleccionada.", "warning")
        return redirect(url_for("home"))

    # Obtener parámetro de fecha si existe, sino traer TODAS las compras
    fecha_str = request.args.get("fecha")
    
    if fecha_str:
        try:
            fecha = datetime.strptime(fecha_str, "%Y-%m-%d").date()
            # Filtrar por fecha específica
            compras = (
                Compra.query
                .filter(db.func.date(Compra.fecha_compra) == fecha)
                .join(Lote)
                .filter(Lote.lotizacion_id == lotizacion_id)
                .order_by(Compra.fecha_compra.desc())
                .all()
            )
            titulo_fecha = f" - {fecha.strftime('%d/%m/%Y')}"
        except:
            # Si la fecha es inválida, traer todas
            compras = (
                Compra.query
                .join(Lote)
                .filter(Lote.lotizacion_id == lotizacion_id)
                .order_by(Compra.fecha_compra.desc())
                .all()
            )
            titulo_fecha = " - Todas"
    else:
        # Traer TODAS las compras de la lotización
        compras = (
            Compra.query
            .join(Lote)
            .filter(Lote.lotizacion_id == lotizacion_id)
            .order_by(Compra.fecha_compra.desc())
            .all()
        )
        titulo_fecha = " - Todas"

    # Crear un libro de Excel
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "Ventas"

    # Estilos
    header_fill = PatternFill(start_color="1a5f3a", end_color="1a5f3a", fill_type="solid")
    header_font = Font(bold=True, color="FFFFFF", size=10)
    border = Border(
        left=Side(style='thin'),
        right=Side(style='thin'),
        top=Side(style='thin'),
        bottom=Side(style='thin')
    )
    
    # Título en la primera fila
    title_cell = ws.cell(row=1, column=1, value=f"📊 REPORTE DE VENTAS{titulo_fecha}")
    title_cell.font = Font(bold=True, size=14, color="1a5f3a")
    ws.merge_cells("A1:Z1")
    
    # Fila vacía
    ws.row_dimensions[2].height = 5

    # ✨ TODOS LOS ENCABEZADOS DEL CLIENTE + COMPRA
    headers = [
        # Información del Cliente
        "Nombres", "Apellidos", "DNI", "Teléfono", "Correo", 
        "Dirección", "Ciudad", "Estado Civil", "Ocupación",
        
        # Información de la Compra
        "Lote", "Forma Pago", "Precio Total", "Inicial", 
        "Cuota Monto", "Cuotas Total", "Cuotas Pagadas",
        
        # Fechas y Vendedor
        "Fecha Compra", "Vendedor"
    ]

    for col, header in enumerate(headers, 1):
        cell = ws.cell(row=3, column=col, value=header)
        cell.fill = header_fill
        cell.font = header_font
        cell.border = border
        cell.alignment = Alignment(horizontal='center', vertical='center', wrap_text=True)

    # Datos de ventas
    for row_idx, compra in enumerate(compras, 4):
        col = 1
        
        # ✨ INFORMACIÓN DEL CLIENTE
        ws.cell(row=row_idx, column=col, value=compra.cliente.nombre or "").border = border
        col += 1
        ws.cell(row=row_idx, column=col, value=compra.cliente.apellidos or "").border = border
        col += 1
        ws.cell(row=row_idx, column=col, value=compra.cliente.dni or "").border = border
        col += 1
        ws.cell(row=row_idx, column=col, value=compra.cliente.telefono or "").border = border
        col += 1
        ws.cell(row=row_idx, column=col, value=compra.cliente.correo or "").border = border
        col += 1
        ws.cell(row=row_idx, column=col, value=compra.cliente.direccion or "").border = border
        col += 1
        ws.cell(row=row_idx, column=col, value=compra.cliente.ciudad or "").border = border
        col += 1
        ws.cell(row=row_idx, column=col, value=compra.cliente.estado_civil or "").border = border
        col += 1
        ws.cell(row=row_idx, column=col, value=compra.cliente.ocupacion or "").border = border
        col += 1
        
        # ✨ INFORMACIÓN DE LA COMPRA
        ws.cell(row=row_idx, column=col, value=f"Mz {compra.lote.manzana} - Lt {compra.lote.numero}").border = border
        col += 1
        ws.cell(row=row_idx, column=col, value=compra.forma_pago.upper()).border = border
        col += 1
        ws.cell(row=row_idx, column=col, value=compra.precio).border = border
        col += 1
        ws.cell(row=row_idx, column=col, value=compra.inicial if compra.forma_pago == "credito" else compra.precio).border = border
        col += 1
        ws.cell(row=row_idx, column=col, value=compra.cuota_monto if compra.forma_pago == "credito" else 0).border = border
        col += 1
        ws.cell(row=row_idx, column=col, value=compra.cuotas_total if compra.forma_pago == "credito" else 0).border = border
        col += 1
        
        # Contar cuotas pagadas
        cuotas_pagadas = len([c for c in compra.cuotas if c.pagada]) if compra.cuotas else 0
        ws.cell(row=row_idx, column=col, value=cuotas_pagadas).border = border
        col += 1
        
        # ✨ FECHAS Y VENDEDOR
        ws.cell(row=row_idx, column=col, value=compra.fecha_compra.strftime('%d/%m/%Y %H:%M')).border = border
        col += 1
        ws.cell(row=row_idx, column=col, value=compra.usuario.username if compra.usuario else "").border = border

    # Añadir fila de totales
    if compras:
        total_row = len(compras) + 4
        
        total_precio = sum([c.precio for c in compras])
        total_inicial = sum([c.inicial if c.forma_pago == "credito" else c.precio for c in compras])
        total_cuota_monto = sum([c.cuota_monto if c.forma_pago == "credito" else 0 for c in compras])
        
        ws.cell(row=total_row, column=1, value="TOTAL").font = Font(bold=True, size=11)
        ws.cell(row=total_row, column=12, value=total_precio).font = Font(bold=True, size=11)
        ws.cell(row=total_row, column=13, value=total_inicial).font = Font(bold=True, size=11)
        ws.cell(row=total_row, column=14, value=total_cuota_monto).font = Font(bold=True, size=11)
        
        for col in range(1, len(headers) + 1):
            ws.cell(row=total_row, column=col).border = border
            ws.cell(row=total_row, column=col).fill = PatternFill(start_color="e8f0ff", end_color="e8f0ff", fill_type="solid")

    # Ajustar ancho de columnas
    for col in range(1, len(headers) + 1):
        max_length = 0
        column_letter = get_column_letter(col)
        for cell in ws[column_letter]:
            try:
                if len(str(cell.value)) > max_length:
                    max_length = len(str(cell.value))
            except:
                pass
        adjusted_width = min(max_length + 2, 50)
        ws.column_dimensions[column_letter].width = adjusted_width

    # Guardar en memoria
    excel_buffer = BytesIO()
    wb.save(excel_buffer)
    excel_buffer.seek(0)

    # Descargar
    from flask import send_file
    
    if fecha_str:
        nombre_archivo = f'ventas_{fecha_str}.xlsx'
    else:
        nombre_archivo = f'ventas_todas_{datetime.now().strftime("%d%m%Y")}.xlsx'
    
    return send_file(
        excel_buffer,
        mimetype='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
        as_attachment=True,
        download_name=nombre_archivo
    )

@app.route("/vouchers", methods=["GET", "POST"])
@login_required
def vouchers():
    from models import Voucher, Lote
    codigo_buscar = request.args.get("codigo", "").strip()
    
    if request.method == "POST":
        codigo = request.form["codigo"].strip()
        banco = request.form.get("banco")
        nombres = request.form.get("nombres")
        apellidos = request.form.get("apellidos")
        monto = float(request.form.get("monto") or 0)
        lote_id = request.form.get("lote_id")
        tipo_pago = request.form.get("tipo_pago")
        numero_cuota = request.form.get("numero_cuota")

        # Verificar duplicado de código
        existe = Voucher.query.filter_by(codigo=codigo).first()
        if existe:
            flash("⚠️ El código ya está registrado.", "danger")
            return redirect(url_for("vouchers"))
        
        # ✅ VALIDACIÓN: Verificar si ya existe un pago inicial para este lote
        if tipo_pago == "inicial" and lote_id:
            inicial_existente = Voucher.query.filter_by(
                lote_id=lote_id,
                tipo_pago="inicial"
            ).first()
            
            if inicial_existente:
                lote = Lote.query.get(lote_id)
                flash(f"⚠️ El lote Mz {lote.manzana} - Lt {lote.numero} ya tiene un pago inicial registrado (voucher {inicial_existente.codigo}).", "danger")
                return redirect(url_for("vouchers"))
        
        # ✅ VALIDACIÓN: Verificar que no se repita el número de cuota para este lote
        if tipo_pago == "cuota" and lote_id and numero_cuota:
            cuota_existente = Voucher.query.filter_by(
                lote_id=lote_id,
                tipo_pago="cuota",
                numero_cuota=int(numero_cuota)
            ).first()
            
            if cuota_existente:
                lote = Lote.query.get(lote_id)
                flash(f"⚠️ La cuota #{numero_cuota} del lote Mz {lote.manzana} - Lt {lote.numero} ya está registrada (voucher {cuota_existente.codigo}).", "danger")
                return redirect(url_for("vouchers"))
        
        # Si todo está bien, registrar el voucher
        v = Voucher(
            codigo=codigo,
            banco=banco,
            nombres=nombres,
            apellidos=apellidos,
            monto=monto,
            lote_id=lote_id,
            fecha_registro=datetime.now(lima),
            usuario_id=current_user.id,
            tipo_pago=tipo_pago,
            numero_cuota=int(numero_cuota) if numero_cuota else None
        )
        db.session.add(v)
        db.session.commit()
        flash("✅ Voucher registrado correctamente.", "success")
        
        return redirect(url_for("vouchers"))

    # Búsqueda
    if codigo_buscar:
        vouchers_list = Voucher.query.filter_by(codigo=codigo_buscar).all()
    else:
        vouchers_list = Voucher.query.order_by(Voucher.fecha_registro.desc()).all()

    # Eliminar lotes duplicados
    todos_los_lotes = Lote.query.order_by(Lote.manzana.asc(), Lote.numero.asc()).all()
    lotes_unicos = {}
    for lote in todos_los_lotes:
        clave = f"{lote.manzana}-{lote.numero}"
        if clave not in lotes_unicos:
            lotes_unicos[clave] = lote
    lotes = list(lotes_unicos.values())

    return render_template(
        "vouchers.html",
        vouchers=vouchers_list,
        codigo_buscar=codigo_buscar,
        lotes=lotes,
        pytz=pytz
    )
# ------------------- SUBIR DOCUMENTOS -------------------
# ------------------- SUBIR DOCUMENTOS -------------------
@app.route("/subir_documentos/<int:compra_id>", methods=["POST"])
@login_required
@lotizacion_required
def subir_documentos(compra_id):
    compra = Compra.query.get_or_404(compra_id)

    bloqueo = bloquear_si_no_es_lotizacion_activa(compra.lote.lotizacion_id)
    if bloqueo:
        return bloqueo

    tipo_documento = request.form.get("tipo_documento")
    archivo = request.files.get("archivo")

    if not archivo or not archivo.filename:
        flash("⚠️ No se seleccionó ningún archivo.", "warning")
        return redirect(url_for("ver_cliente", cliente_id=compra.cliente_id))

    from werkzeug.utils import secure_filename
    import json

    # Definir carpeta según el tipo de documento
    carpetas = {
        "escritura": "escrituras",
        "otro": "otros_docs"
    }

    carpeta = carpetas.get(tipo_documento, "otros_docs")
    docs_folder = os.path.join("static", carpeta)
    os.makedirs(docs_folder, exist_ok=True)

    # Guardar archivo con nombre único
    filename = secure_filename(archivo.filename)
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    unique_filename = f"{timestamp}_{filename}"
    save_path = os.path.join(docs_folder, unique_filename)
    archivo.save(save_path)

    ruta_relativa = f"{carpeta}/{unique_filename}".replace("\\", "/")

    # 🧩 Control de documentos según tipo
    if tipo_documento == "escritura":
        # ✅ Si ya hay una escritura, no permitir otra
        if compra.escritura:
            flash("⚠️ Ya existe una escritura registrada. Elimina la anterior si deseas reemplazarla.", "warning")
            # Borrar el archivo recién subido (porque no se usará)
            try:
                os.remove(save_path)
            except:
                pass
            return redirect(url_for("ver_cliente", cliente_id=compra.cliente_id))

        # ✅ Guardar nueva escritura
        compra.escritura = ruta_relativa
        flash("✅ Escritura subida correctamente.", "success")

    elif tipo_documento == "otro":
        # ✅ Permitir varios documentos tipo 'otro'
        nombre_custom = request.form.get("nombre_documento", "Documento")

        otros = []
        if compra.otros_documentos:
            try:
                otros = json.loads(compra.otros_documentos)
            except:
                otros = []

        otros.append({
            "nombre": nombre_custom,
            "ruta": ruta_relativa,
            "fecha": timestamp
        })

        compra.otros_documentos = json.dumps(otros)
        flash(f"✅ {nombre_custom} subido correctamente.", "success")

    db.session.commit()
    return redirect(url_for("ver_cliente", cliente_id=compra.cliente_id))


# ------------------- ELIMINAR DOCUMENTO -------------------
@app.route("/eliminar_documento/<int:compra_id>/<tipo>", methods=["POST"])
@login_required
@admin_required
@lotizacion_required
def eliminar_documento(compra_id, tipo):
    compra = Compra.query.get_or_404(compra_id)
    
    import json
    
    if tipo == "escritura":
        # Eliminar archivo físico de static/escrituras/
        if compra.escritura:
            try:
                os.remove(os.path.join("static", compra.escritura))
            except:
                pass
        compra.escritura = None
        flash("✅ Escritura eliminada.", "success")
        
    elif tipo.startswith("otro_"):
        # Eliminar documento específico de la lista en static/otros_docs/
        index = int(tipo.split("_")[1])
        if compra.otros_documentos:
            try:
                otros = json.loads(compra.otros_documentos)
                if 0 <= index < len(otros):
                    doc = otros.pop(index)
                    # Eliminar archivo físico
                    try:
                        os.remove(os.path.join("static", doc["ruta"]))
                    except:
                        pass
                    compra.otros_documentos = json.dumps(otros) if otros else None
                    flash(f"✅ {doc['nombre']} eliminado.", "success")
            except:
                pass
    
    db.session.commit()
    return redirect(url_for("ver_cliente", cliente_id=compra.cliente_id))


@app.route("/editar_voucher/<int:id>", methods=["GET", "POST"])
@login_required
def editar_voucher(id):
    from models import Voucher, Lote
    voucher = Voucher.query.get_or_404(id)
    
    if request.method == "POST":
        voucher.codigo = request.form["codigo"]
        voucher.banco = request.form["banco"]
        voucher.nombres = request.form["nombres"]
        voucher.apellidos = request.form["apellidos"]
        voucher.monto = float(request.form["monto"])
        lote_id = request.form.get("lote_id") or None
        tipo_pago = request.form.get("tipo_pago")
        numero_cuota = request.form.get("numero_cuota")
        
        # ✅ VALIDACIÓN: Si cambió el tipo a "inicial", verificar que no exista otro
        if tipo_pago == "inicial" and lote_id:
            inicial_existente = Voucher.query.filter(
                Voucher.lote_id == lote_id,
                Voucher.tipo_pago == "inicial",
                Voucher.id != voucher.id
            ).first()
            
            if inicial_existente:
                lote = Lote.query.get(lote_id)
                flash(f"⚠️ El lote Mz {lote.manzana} - Lt {lote.numero} ya tiene un pago inicial (voucher {inicial_existente.codigo}).", "danger")
                return redirect(url_for("editar_voucher", id=id))
        
        # ✅ VALIDACIÓN: Si es cuota, verificar que no se repita el número
        if tipo_pago == "cuota" and lote_id and numero_cuota:
            cuota_existente = Voucher.query.filter(
                Voucher.lote_id == lote_id,
                Voucher.tipo_pago == "cuota",
                Voucher.numero_cuota == int(numero_cuota),
                Voucher.id != voucher.id
            ).first()
            
            if cuota_existente:
                lote = Lote.query.get(lote_id)
                flash(f"⚠️ La cuota #{numero_cuota} del lote ya existe (voucher {cuota_existente.codigo}).", "danger")
                return redirect(url_for("editar_voucher", id=id))
        
        # Actualizar campos
        voucher.lote_id = lote_id
        voucher.tipo_pago = tipo_pago
        
        if tipo_pago == "cuota":
            voucher.numero_cuota = int(numero_cuota) if numero_cuota else None
        else:
            voucher.numero_cuota = None
        
        db.session.commit()
        flash("✅ Voucher actualizado correctamente.", "success")
        return redirect(url_for("vouchers"))

    # ✅ CARGAR TODOS LOS LOTES (sin filtrar por unicidad)
    # Esto mostrará todos los lotes de todas las lotizaciones
    lotes = Lote.query.order_by(Lote.manzana.asc(), Lote.numero.asc()).all()

    return render_template("editar_voucher.html", voucher=voucher, lotes=lotes)


@app.route("/eliminar_voucher_cuota/<int:cuota_id>", methods=["POST"])
@login_required
@admin_required
def eliminar_voucher_cuota(cuota_id):
    cuota = Cuota.query.get_or_404(cuota_id)
    
    if not cuota.pago_id:
        flash("⚠️ Esta cuota no tiene voucher registrado.", "warning")
        return redirect(url_for("detalle_cuotas", compra_id=cuota.compra_id))
    
    # Obtener el pago asociado
    pago = Pago.query.get(cuota.pago_id)
    
    if pago and pago.boucher:
        # Eliminar archivo físico del voucher
        boucher_path = os.path.join("static", pago.boucher)
        try:
            if os.path.exists(boucher_path):
                os.remove(boucher_path)
        except Exception as e:
            print(f"Error al eliminar archivo: {e}")
    
    # Eliminar el registro del pago
    if pago:
        db.session.delete(pago)
    
    # Desmarcar la cuota como pagada
    cuota.pagada = False
    cuota.pago_id = None
    
    db.session.commit()
    flash("✅ Voucher eliminado correctamente. Puedes subir uno nuevo.", "success")
    
    return redirect(url_for("detalle_cuotas", compra_id=cuota.compra_id))


@app.route("/logout")
@login_required
def logout():
    logout_user()
    session.pop("lotizacion_id", None)  # 👈 borra la lotización activa
    session.pop("lotizacion_nombre", None)
    flash("Sesión cerrada correctamente.", "info")
    return redirect(url_for("login"))


@app.route("/superadmin")
@login_required
@superadmin_required
def panel_superadmin():
    """Dashboard principal del superadmin."""
    # Estadísticas generales
    total_usuarios   = Usuario.query.count()
    total_lotizaciones = Lotizacion.query.count()
    total_clientes   = Cliente.query.count()
    total_lotes      = Lote.query.count()
    total_compras    = Compra.query.count()
    total_separaciones = Separacion.query.filter_by(activa=True).count()
 
    # Actividad por vendedor: compras registradas
    from sqlalchemy import func
    ventas_por_usuario = (
        db.session.query(Usuario.username, func.count(Compra.id).label("ventas"))
        .outerjoin(Compra, Compra.usuario_id == Usuario.id)
        .group_by(Usuario.id, Usuario.username)
        .order_by(func.count(Compra.id).desc())
        .all()
    )
 
    separaciones_por_usuario = (
        db.session.query(Usuario.username, func.count(Separacion.id).label("separaciones"))
        .outerjoin(Separacion, Separacion.usuario_id == Usuario.id)
        .group_by(Usuario.id, Usuario.username)
        .order_by(func.count(Separacion.id).desc())
        .all()
    )
 
    # Últimas 10 compras registradas (cualquier lotización)
    ultimas_compras = (
        Compra.query
        .order_by(Compra.fecha_compra.desc())
        .limit(10)
        .all()
    )
 
    # Usuarios del sistema
    usuarios = Usuario.query.order_by(Usuario.rol).all()
    lotizaciones = Lotizacion.query.all()
 
    return render_template(
        "panel_superadmin.html",
        total_usuarios=total_usuarios,
        total_lotizaciones=total_lotizaciones,
        total_clientes=total_clientes,
        total_lotes=total_lotes,
        total_compras=total_compras,
        total_separaciones=total_separaciones,
        ventas_por_usuario=ventas_por_usuario,
        separaciones_por_usuario=separaciones_por_usuario,
        ultimas_compras=ultimas_compras,
        usuarios=usuarios,
        lotizaciones=lotizaciones,
    )
 
 
# ------------------- CREAR USUARIO (solo superadmin) -------------------
@app.route("/superadmin/crear_usuario", methods=["GET", "POST"])
@login_required
@superadmin_required
def crear_usuario():
    if request.method == "POST":
        username = request.form.get("username", "").strip()
        password = request.form.get("password", "").strip()
        rol      = request.form.get("rol", "vendedor")
 
        if not username or not password:
            flash("Usuario y contraseña son obligatorios.", "danger")
            return redirect(url_for("crear_usuario"))
 
        if Usuario.query.filter_by(username=username).first():
            flash("Ya existe un usuario con ese nombre.", "warning")
            return redirect(url_for("crear_usuario"))
 
        nuevo = Usuario(username=username, rol=rol)
        nuevo.set_password(password)
        db.session.add(nuevo)
        db.session.flush()

        # Si el formulario ya envía lotizacion_ids, los toma.
        # Si no, el superadmin los asigna después en la pantalla de permisos.
        if rol != "superadmin":
            lotizacion_ids = request.form.getlist("lotizacion_ids")
            if lotizacion_ids:
                nuevo.lotizaciones = Lotizacion.query.filter(
                    Lotizacion.id.in_(lotizacion_ids)
                ).all()

        db.session.commit()
        flash(f"✅ Usuario '{username}' creado con rol '{rol}'.", "success")

        if rol != "superadmin":
            flash("Ahora asigna las lotizaciones permitidas para este usuario.", "info")
            return redirect(url_for("permisos_lotizaciones"))

        return redirect(url_for("panel_superadmin"))
 
    return render_template("crear_usuario.html", desde_superadmin=True)
 
 

# ------------------- PERMISOS DE LOTIZACIONES POR USUARIO -------------------
@app.route("/superadmin/permisos_lotizaciones", methods=["GET", "POST"])
@login_required
@superadmin_required
def permisos_lotizaciones():
    if request.method == "POST":
        usuario_id = request.form.get("usuario_id")
        usuario = Usuario.query.get_or_404(usuario_id)

        if usuario.rol == "superadmin":
            flash("El superadmin ya tiene acceso a todas las lotizaciones.", "info")
            return redirect(url_for("permisos_lotizaciones"))

        ids = request.form.getlist("lotizacion_ids")
        seleccionadas = (
            Lotizacion.query
            .filter(Lotizacion.id.in_(ids))
            .order_by(Lotizacion.nombre.asc())
            .all()
            if ids else []
        )

        usuario.lotizaciones = seleccionadas
        db.session.commit()
        flash(f"✅ Permisos actualizados para '{usuario.username}'.", "success")
        return redirect(url_for("panel_superadmin"))

    usuarios = Usuario.query.order_by(Usuario.rol.asc(), Usuario.username.asc()).all()
    lotizaciones = Lotizacion.query.order_by(Lotizacion.nombre.asc()).all()

    return render_template(
        "permisos_lotizaciones.html",
        usuarios=usuarios,
        lotizaciones=lotizaciones
    )


# ------------------- DESACTIVAR / ACTIVAR USUARIO -------------------
@app.route("/superadmin/toggle_usuario/<int:usuario_id>", methods=["POST"])
@login_required
@superadmin_required
def toggle_usuario(usuario_id):
    usuario = Usuario.query.get_or_404(usuario_id)
 
    if usuario.id == current_user.id:
        flash("No puedes desactivarte a ti mismo.", "warning")
        return redirect(url_for("panel_superadmin"))
 
    usuario.activo = not getattr(usuario, "activo", True)
    db.session.commit()
    estado = "activado" if usuario.activo else "desactivado"
    flash(f"✅ Usuario '{usuario.username}' {estado}.", "success")
    return redirect(url_for("panel_superadmin"))
 
 
# ------------------- CAMBIAR ROL DE USUARIO -------------------
@app.route("/superadmin/cambiar_rol/<int:usuario_id>", methods=["POST"])
@login_required
@superadmin_required
def cambiar_rol(usuario_id):
    usuario = Usuario.query.get_or_404(usuario_id)
 
    if usuario.id == current_user.id:
        flash("No puedes cambiar tu propio rol.", "warning")
        return redirect(url_for("panel_superadmin"))
 
    nuevo_rol = request.form.get("rol")
    if nuevo_rol not in ("vendedor", "admin", "superadmin"):
        flash("Rol inválido.", "danger")
        return redirect(url_for("panel_superadmin"))
 
    usuario.rol = nuevo_rol
    db.session.commit()
    flash(f"✅ Rol de '{usuario.username}' cambiado a '{nuevo_rol}'.", "success")
    return redirect(url_for("panel_superadmin"))
 
 
# ------------------- CREAR LOTIZACIÓN (solo superadmin) -------------------
# ------------------- CREAR LOTIZACIÓN -------------------

@app.route("/superadmin/crear_lotizacion", methods=["GET", "POST"])
@login_required
@superadmin_required
def crear_lotizacion():
    if request.method == "POST":
        nombre = request.form.get("nombre", "").strip()
        distrito = request.form.get("distrito", "").strip()
        provincia = request.form.get("provincia", "").strip()
        departamento = request.form.get("departamento", "").strip()

        valores = (
            nombre,
            distrito,
            provincia,
            departamento,
        )

        if not all(valores):
            flash(
                "Completa el nombre, distrito, provincia y departamento.",
                "danger"
            )

            return render_template(
                "crear_lotizacion.html",
                desde_superadmin=True
            )

        if any(len(valor) > 100 for valor in valores):
            flash(
                "Cada campo admite como máximo 100 caracteres.",
                "danger"
            )

            return render_template(
                "crear_lotizacion.html",
                desde_superadmin=True
            )

        if Lotizacion.query.filter_by(nombre=nombre).first():
            flash(
                "Ya existe una lotización con ese nombre.",
                "warning"
            )

            return render_template(
                "crear_lotizacion.html",
                desde_superadmin=True
            )

        try:
            nueva = Lotizacion(
                nombre=nombre,
                distrito=distrito,
                provincia=provincia,
                departamento=departamento
            )

            db.session.add(nueva)
            db.session.commit()

        except Exception:
            db.session.rollback()

            current_app.logger.exception(
                "Error al crear la lotización"
            )

            flash(
                "No se pudo crear la lotización. "
                "Revisa los datos e inténtalo nuevamente.",
                "danger"
            )

            return render_template(
                "crear_lotizacion.html",
                desde_superadmin=True
            )

        flash(
            f"✅ Lotización '{nombre}' creada con su ubicación.",
            "success"
        )

        return redirect(url_for("panel_superadmin"))

    return render_template(
        "crear_lotizacion.html",
        desde_superadmin=True
    )
 
# ------------------- ELIMINAR LOTIZACIÓN (solo superadmin) -------------------
@app.route("/superadmin/eliminar_lotizacion/<int:lot_id>", methods=["POST"])
@login_required
@superadmin_required
def eliminar_lotizacion(lot_id):
    lot = Lotizacion.query.get_or_404(lot_id)
    lotes_activos = Lote.query.filter_by(lotizacion_id=lot_id).count()
 
    if lotes_activos > 0:
        flash(f"⚠️ No se puede eliminar '{lot.nombre}': tiene {lotes_activos} lotes asociados.", "danger")
        return redirect(url_for("panel_superadmin"))
 
    db.session.delete(lot)
    db.session.commit()
    flash(f"✅ Lotización '{lot.nombre}' eliminada.", "success")
    return redirect(url_for("panel_superadmin"))
 
# ============================================================
# GESTOR DE DOCUMENTOS
# ============================================================
from file_helper import subir_archivo, eliminar_archivo, get_ruta_empresa, get_ruta_lote, get_ruta_lotizacion_general
from models import Documento

@app.route("/documentos")
@login_required
def documentos():
    if current_user.rol not in ("admin", "superadmin"):
        flash("No tienes permisos para acceder.", "danger")
        return redirect(url_for("home"))
    lotizaciones = lotizaciones_permitidas_para(current_user)
    return render_template("documentos.html", lotizaciones=lotizaciones)


@app.route("/documentos/empresa")
@login_required
def documentos_empresa():
    if current_user.rol not in ("admin", "superadmin"):
        flash("No tienes permisos para acceder.", "danger")
        return redirect(url_for("home"))
    
    tipo = request.args.get("tipo", "")
    nombre = request.args.get("nombre", "")
    
    docs = Documento.query.filter_by(lotizacion_id=None, lote_id=None)
    if tipo:
        docs = docs.filter_by(tipo=tipo)
    if nombre:
        docs = docs.filter(Documento.nombre.ilike(f"%{nombre}%"))
    docs = docs.order_by(Documento.fecha_subida.desc()).all()
    
    return render_template("documentos_empresa.html", docs=docs, tipo=tipo, nombre=nombre)


@app.route("/documentos/lotizacion/<int:lotizacion_id>")
@login_required
def documentos_lotizacion(lotizacion_id):
    if current_user.rol not in ("admin", "superadmin"):
        flash("No tienes permisos para acceder.", "danger")
        return redirect(url_for("home"))

    if not usuario_puede_acceder_lotizacion(current_user, lotizacion_id):
        flash("No tienes acceso a esa lotización.", "danger")
        return redirect(url_for("documentos"))
    lotizacion = Lotizacion.query.get_or_404(lotizacion_id)
    manzana = request.args.get("manzana", "")
    apellido = request.args.get("apellido", "")

    manzanas = db.session.query(Lote.manzana).filter_by(
        lotizacion_id=lotizacion_id
    ).distinct().order_by(Lote.manzana).all()
    manzanas = [m[0] for m in manzanas]

    docs = Documento.query.filter_by(lotizacion_id=lotizacion_id)

    if manzana:
        lotes_manzana = Lote.query.filter_by(lotizacion_id=lotizacion_id, manzana=manzana).all()
        lote_ids = [l.id for l in lotes_manzana]
        docs = docs.filter(Documento.lote_id.in_(lote_ids))

    if apellido:
        clientes = Cliente.query.filter(Cliente.apellidos.ilike(f"%{apellido}%")).all()
        cliente_ids = [c.id for c in clientes]
        docs = docs.filter(
            or_(
                Documento.cliente_id.in_(cliente_ids),
                Documento.nombre.ilike(f"%{apellido}%")
            )
        )

    docs = docs.order_by(Documento.fecha_subida.desc()).all()

    return render_template(
        "documentos_lotizacion.html",
        lotizacion=lotizacion,
        docs=docs,
        manzanas=manzanas,
        manzana_sel=manzana,
        apellido=apellido
    )

@app.route("/documentos/subir", methods=["POST"])
@login_required
def subir_documento():
    if current_user.rol not in ("admin", "superadmin"):
        flash("No tienes permisos.", "danger")
        return redirect(url_for("documentos"))

    archivo = request.files.get("archivo")
    tipo = request.form.get("tipo", "otro")
    lotizacion_id = request.form.get("lotizacion_id") or None
    lote_id = request.form.get("lote_id") or None
    cliente_id = request.form.get("cliente_id") or None

    if lotizacion_id and not usuario_puede_acceder_lotizacion(current_user, lotizacion_id):
        flash("No tienes acceso a esa lotización.", "danger")
        return redirect(url_for("documentos"))
    nombre_cliente_libre = request.form.get("nombre_cliente_libre", "").strip()
    nombre_personalizado = request.form.get("nombre_personalizado", "").strip()

    if not archivo or archivo.filename == "":
        flash("Debes seleccionar un archivo.", "danger")
        return redirect(request.referrer)

    if not lotizacion_id and not nombre_personalizado:
        flash("Debes ingresar un nombre para el documento.", "danger")
        return redirect(request.referrer)

    if lotizacion_id and not lote_id and not cliente_id and not nombre_cliente_libre:
        flash("Debes seleccionar un lote, un cliente o ingresar un nombre.", "danger")
        return redirect(request.referrer)

    try:
        import datetime as dt
        fecha_str = dt.datetime.now().strftime("%d%m%Y")
        ext = os.path.splitext(archivo.filename)[1]

        # Nombre del cliente: de BD o libre
        nombre_cli = None
        if cliente_id:
            cliente = Cliente.query.get(cliente_id)
            nombre_cli = cliente.apellidos if cliente else None
        elif nombre_cliente_libre:
            nombre_cli = nombre_cliente_libre

        if lotizacion_id:
            lotizacion = Lotizacion.query.get(lotizacion_id)
            if lote_id:
                lote = Lote.query.get(lote_id)
                ruta_carpeta = get_ruta_lote(lotizacion.nombre, lote.manzana, lote.numero)
                if nombre_cli:
                    nombre_base = f"{nombre_cli}_Mz{lote.manzana}_Lt{lote.numero}_{tipo}_{fecha_str}{ext}"
                else:
                    nombre_base = f"Mz{lote.manzana}_Lt{lote.numero}_{tipo}_{fecha_str}{ext}"
            else:
                ruta_carpeta = get_ruta_lotizacion_general(lotizacion.nombre)
                if nombre_cli:
                    nombre_base = f"{nombre_cli}_{tipo}_{fecha_str}{ext}"
                else:
                    nombre_base = f"{lotizacion.nombre}_{tipo}_{fecha_str}{ext}"
        else:
            ruta_carpeta = get_ruta_empresa(tipo)
            if nombre_personalizado:
                nombre_base = f"{nombre_personalizado}{ext}"
            else:
                nombre_base = f"{tipo}_{fecha_str}{ext}"

        nombre_base = nombre_base.replace(" ", "_")
        nombre_guardado, ruta_relativa = subir_archivo(archivo, nombre_base, ruta_carpeta)

        doc = Documento(
            nombre=nombre_guardado,
            ruta=ruta_relativa,
            tipo=tipo,
            lotizacion_id=int(lotizacion_id) if lotizacion_id else None,
            lote_id=int(lote_id) if lote_id else None,
            cliente_id=int(cliente_id) if cliente_id else None,
            usuario_id=current_user.id
        )
        db.session.add(doc)
        db.session.commit()
        flash("✅ Documento subido correctamente.", "success")

    except Exception as e:
        flash(f"❌ Error al subir: {str(e)}", "danger")

    return redirect(request.referrer)


@app.route("/documentos/eliminar/<int:doc_id>", methods=["POST"])
@login_required
def eliminar_documento_drive(doc_id):
    if current_user.rol not in ("admin", "superadmin"):
        flash("No tienes permisos.", "danger")
        return redirect(url_for("documentos"))

    doc = Documento.query.get_or_404(doc_id)

    if doc.lotizacion_id and not usuario_puede_acceder_lotizacion(current_user, doc.lotizacion_id):
        flash("No tienes acceso a ese documento.", "danger")
        return redirect(url_for("documentos"))
    try:
        eliminar_archivo(doc.ruta)
        db.session.delete(doc)
        db.session.commit()
        flash("✅ Documento eliminado.", "success")
    except Exception as e:
        flash(f"❌ Error al eliminar: {str(e)}", "danger")

    return redirect(request.referrer)



# ------------------- EDITAR UBICACIÓN DE LOTIZACIÓN -------------------

@app.route(
    "/superadmin/editar_ubicacion/<int:lot_id>",
    methods=["POST"]
)
@login_required
@superadmin_required
def editar_ubicacion_lotizacion(lot_id):
    lotizacion = Lotizacion.query.get_or_404(lot_id)

    distrito = request.form.get("distrito", "").strip()
    provincia = request.form.get("provincia", "").strip()
    departamento = request.form.get("departamento", "").strip()

    valores = (distrito, provincia, departamento)

    if not all(valores):
        flash(
            "Completa el distrito, la provincia y el departamento.",
            "danger"
        )
        return redirect(url_for("panel_superadmin"))

    if any(len(valor) > 100 for valor in valores):
        flash(
            "Cada campo admite como máximo 100 caracteres.",
            "danger"
        )
        return redirect(url_for("panel_superadmin"))

    try:
        lotizacion.distrito = distrito
        lotizacion.provincia = provincia
        lotizacion.departamento = departamento

        db.session.commit()

        flash(
            f"Ubicación de '{lotizacion.nombre}' actualizada.",
            "success"
        )

    except Exception:
        db.session.rollback()

        current_app.logger.exception(
            "Error al actualizar la ubicación de la lotización %s",
            lot_id
        )

        flash(
            "No se pudo guardar la ubicación. Inténtalo nuevamente.",
            "danger"
        )

    return redirect(url_for("panel_superadmin"))




_LM_CAMPOS = ('lindero_frente', 'lindero_derecha', 'lindero_izquierda', 'lindero_fondo')


def _lm_numero(valor):
    texto = str(valor).strip()
    return str(int(texto)) if texto.isdigit() else texto


def _lm_huella(lote):
    datos = [lote.lotizacion_id, lote.manzana, lote.numero, str(lote.area)]
    datos += [getattr(lote, campo) for campo in _LM_CAMPOS]
    datos.append(str(lote.perimetro))
    return hashlib.sha256(json.dumps(datos, ensure_ascii=False).encode()).hexdigest()


def _lm_extraer(texto):
    texto = texto.replace('\r\n', '\n').replace('\r', '\n').replace('\xa0', ' ')
    cabecera = r'^[ \t]*MANZANA[ \t]+(.+?)[ \t]*[-–—][ \t]*LOTE[ \t]+(\d+)([^\n]*)$'
    bloques = list(re.finditer(cabecera, texto, re.I | re.M))
    if not bloques or len(bloques) > 200:
        raise ValueError('Pega entre 1 y 200 lotes con encabezados como MANZANA A - LOTE 01.')
    patron = r'^[ \t]*(?:por[ \t]+(?:el|la)[ \t]+)?(frente|derecha(?:[ \t]+entrando)?|izquierda(?:[ \t]+entrando)?|fondo|per[ií]metro|[aá]rea(?:[ \t]+total|[ \t]+y[ \t]+per[ií]metro)?)[ \t]*(?::|[-–]|(?=$))[ \t]*'
    filas = []
    for i, bloque in enumerate(bloques):
        fin = bloques[i + 1].start() if i + 1 < len(bloques) else len(texto)
        cuerpo = texto[bloque.end():fin]
        marcas = list(re.finditer(patron, cuerpo, re.I | re.M))
        dato = {}
        error = ''
        for j, marca in enumerate(marcas):
            etiqueta = marca.group(1).lower()
            if etiqueta.startswith(('área', 'area')):
                if 'per' in etiqueta:
                    continue
                campo = 'area_memoria'
            elif etiqueta.startswith('frente'):
                campo = 'lindero_frente'
            elif etiqueta.startswith('derecha'):
                campo = 'lindero_derecha'
            elif etiqueta.startswith('izquierda'):
                campo = 'lindero_izquierda'
            elif etiqueta.startswith('fondo'):
                campo = 'lindero_fondo'
            else:
                campo = 'perimetro'
            limite = marcas[j + 1].start() if j + 1 < len(marcas) else len(cuerpo)
            if campo in dato:
                error = 'Hay campos repetidos.'
            dato[campo] = cuerpo[marca.end():limite].strip()
        fila = {'manzana': bloque.group(1).strip().upper(), 'numero': _lm_numero(bloque.group(2)),
                'dato': dato, 'estado': error, 'listo': False}
        if bloque.group(3).strip():
            fila['estado'] = 'Revisar: ' + bloque.group(3).strip()
        if any(not dato.get(c) for c in (*_LM_CAMPOS, 'perimetro')):
            fila['estado'] = 'Faltan linderos o perímetro.'
        for campo in ('perimetro', 'area_memoria'):
            if campo not in dato:
                continue
            numero = re.fullmatch(r'(\d+(?:[.,]\d+)?)[ \t]*(?:ml\.?|m\.?|metros?\.?|m²|m2)?\.?', dato[campo], re.I)
            if not numero or Decimal(numero.group(1).replace(',', '.')) <= 0:
                fila['estado'] = 'Medida inválida: ' + campo
            else:
                dato[campo] = numero.group(1).replace(',', '.')
        filas.append(fila)
    claves = [(f['manzana'], f['numero']) for f in filas]
    for fila in filas:
        if claves.count((fila['manzana'], fila['numero'])) > 1:
            fila['estado'] = 'Lote repetido en el texto.'
    return filas


@app.route('/superadmin/linderos_manzana', methods=['GET', 'POST'])
@login_required
@superadmin_required
def linderos_manzana():
    firmador = URLSafeTimedSerializer(app.config['SECRET_KEY'], salt='linderos-manzana-v1')
    if request.method == 'GET':
        proyecto_id = request.args.get('lotizacion_id', type=int)
        proyecto = Lotizacion.query.get(proyecto_id) if proyecto_id else None
        if not proyecto:
            return jsonify(error='Selecciona un proyecto válido.'), 400
        manzanas = db.session.query(Lote.manzana).filter_by(lotizacion_id=proyecto.id).distinct().order_by(Lote.manzana).all()
        csrf = session.setdefault('lm_csrf', secrets.token_hex(32))
        return jsonify(manzanas=[m[0] for m in manzanas], csrf=csrf)

    datos = request.get_json(silent=True)
    if not isinstance(datos, dict):
        return jsonify(error='Solicitud inválida.'), 400
    if not session.get('lm_csrf') or datos.get('csrf') != session['lm_csrf']:
        return jsonify(error='Recarga el panel y selecciona el proyecto nuevamente.'), 403

    try:
        if datos.get('accion') == 'revisar':
            texto = datos.get('texto', '')
            if not isinstance(texto, str) or len(texto) > 120000:
                raise ValueError('El texto supera el tamaño permitido.')
            proyecto_id = int(datos.get('lotizacion_id') or 0)
            manzana = str(datos.get('manzana', '')).strip()
            lotes = Lote.query.filter_by(lotizacion_id=proyecto_id, manzana=manzana).all()
            if not lotes:
                raise ValueError('Esa manzana no tiene lotes en el proyecto seleccionado.')
            indice = {}
            for lote in lotes:
                indice.setdefault(_lm_numero(lote.numero), []).append(lote)
            filas = _lm_extraer(texto)
            guardar = []
            for fila in filas:
                if fila['estado']:
                    continue
                candidatos = indice.get(fila['numero'], [])
                if fila['manzana'] != manzana.upper():
                    fila['estado'] = 'Pertenece a otra manzana.'
                elif len(candidatos) != 1:
                    fila['estado'] = 'Lote inexistente o número ambiguo.'
                else:
                    lote = candidatos[0]
                    if any(getattr(lote, c) for c in _LM_CAMPOS) or lote.perimetro is not None:
                        fila['estado'] = 'Ya tiene datos; editar individualmente.'
                    elif 'area_memoria' in fila['dato'] and abs(Decimal(str(lote.area)) - Decimal(fila['dato']['area_memoria'])) > Decimal('0.01'):
                        fila['estado'] = 'El área difiere de la registrada.'
                    else:
                        fila['listo'] = True
                        fila['estado'] = 'Listo para guardar'
                        guardar.append({'id': lote.id, 'huella': _lm_huella(lote), 'dato': fila['dato']})
            token = firmador.dumps({'usuario': current_user.id, 'proyecto': proyecto_id, 'manzana': manzana, 'lotes': guardar}) if guardar else None
            return jsonify(filas=filas, token=token, listos=len(guardar))

        if datos.get('accion') == 'guardar':
            token = datos.get('token')
            if not isinstance(token, str) or len(token) > 500000:
                raise ValueError('Primero revisa el texto.')
            revision = firmador.loads(token, max_age=1800)
            if revision['usuario'] != current_user.id:
                raise ValueError('La revisión pertenece a otra sesión de usuario.')
            ids = [f['id'] for f in revision['lotes']]
            lotes = Lote.query.filter(Lote.id.in_(ids), Lote.lotizacion_id == revision['proyecto'], Lote.manzana == revision['manzana']).with_for_update().all()
            indice = {l.id: l for l in lotes}
            for fila in revision['lotes']:
                lote = indice.get(fila['id'])
                if not lote or _lm_huella(lote) != fila['huella']:
                    raise ValueError('Un lote cambió después de revisar. Vuelve a pulsar Separar y revisar.')
            for fila in revision['lotes']:
                lote = indice[fila['id']]
                for campo in _LM_CAMPOS:
                    setattr(lote, campo, fila['dato'][campo])
                lote.perimetro = float(fila['dato']['perimetro'])
            db.session.commit()
            return jsonify(mensaje=f'Se guardaron los linderos de {len(ids)} lotes.')
        raise ValueError('Acción inválida.')
    except (BadSignature, SignatureExpired):
        db.session.rollback()
        return jsonify(error='La revisión venció o no es válida. Revisa el texto nuevamente.'), 400
    except (ValueError, TypeError, InvalidOperation) as exc:
        db.session.rollback()
        return jsonify(error=str(exc)), 400
    except Exception:
        db.session.rollback()
        current_app.logger.exception('Error en carga de linderos por manzana')
        return jsonify(error='No se pudo completar la operación. Revisa el registro de errores del servidor.'), 500


# CONFIGURACIÓN Y GENERACIÓN DE ACTAS

@app.template_global()
def token_config_acta():
    import secrets

    if not session.get("acta_csrf"):
        session["acta_csrf"] = secrets.token_urlsafe(32)

    return session["acta_csrf"]

def _validar_margenes_acta(valores):
    import math

    valores = valores or {}

    if not isinstance(valores, dict):
        raise ValueError("La configuración de márgenes no es válida.")

    resultado = {}

    for campo, defecto in (
        ("superior", 32),
        ("inferior", 32),
        ("izquierdo", 32),
        ("derecho", 15),
    ):
        try:
            valor = float(
                str(valores.get(campo, defecto)).replace(",", ".")
            )
        except (ValueError, TypeError):
            raise ValueError(
                f"El margen {campo} debe ser un número en milímetros."
            )

        if not math.isfinite(valor) or not 0 <= valor <= 80:
            raise ValueError(
                f"El margen {campo} debe estar entre 0 y 80 mm."
            )

        resultado[campo] = valor

    if resultado["izquierdo"] + resultado["derecho"] > 105:
        raise ValueError(
            "Los márgenes laterales no deben sumar más de 105 mm."
        )

    if resultado["superior"] + resultado["inferior"] > 150:
        raise ValueError(
            "Los márgenes superior e inferior no deben sumar más de 150 mm."
        )

    return resultado


def _validar_membrete_acta(contenido):
    import io

    from pypdf import PdfReader, PdfWriter, Transformation
    from pypdf.errors import PyPdfError
    from pypdf.generic import NameObject, RectangleObject

    contenido = bytes(contenido)

    if not contenido or len(contenido) > 5 * 1024 * 1024:
        raise ValueError("El membrete debe ser un PDF de máximo 5 MB.")

    if not contenido.lstrip().startswith(b"%PDF-"):
        raise ValueError("El archivo del membrete no es un PDF válido.")

    try:
        lector = PdfReader(io.BytesIO(contenido))

        if lector.is_encrypted:
            raise ValueError(
                "El membrete debe ser un PDF sin contraseña."
            )

        if len(lector.pages) != 1:
            raise ValueError(
                "El membrete debe tener exactamente una página."
            )

        pagina = lector.pages[0]

        if pagina.rotation:
            pagina.transfer_rotation_to_content()

        caja = pagina.cropbox
        ancho = float(caja.width)
        alto = float(caja.height)

        if (
            abs(ancho - 595.28) > 3
            or abs(alto - 841.89) > 3
            or float(pagina.get("/UserUnit", 1)) != 1
        ):
            raise ValueError(
                "El membrete debe estar en tamaño A4 vertical."
            )

        pagina.add_transformation(
            Transformation().translate(
                -float(caja.left),
                -float(caja.bottom),
            )
        )

        for clave in (
            "/MediaBox", "/CropBox", "/TrimBox",
            "/BleedBox", "/ArtBox",
        ):
            pagina[NameObject(clave)] = RectangleObject(
                (0, 0, ancho, alto)
            )

        escritor = PdfWriter()
        escritor.add_page(
            pagina,
            excluded_keys=["/Annots", "/AA", "/B"],
        )

        salida = io.BytesIO()
        escritor.write(salida)

        resultado = salida.getvalue()

        if len(resultado) > 5 * 1024 * 1024:
            raise ValueError(
                "El PDF procesado supera 5 MB. Reduce su tamaño."
            )

        return resultado

    except ValueError:
        raise

    except (
        PyPdfError, OSError, TypeError,
        KeyError, IndexError, OverflowError,
    ) as error:
        raise ValueError(
            "No se pudo leer el membrete. "
            "Exporta nuevamente el archivo como PDF."
        ) from error


@app.route("/superadmin/membrete_acta/<int:lot_id>")
@login_required
@superadmin_required
def membrete_acta(lot_id):
    import io

    lot = Lotizacion.query.get_or_404(lot_id)

    if not lot.acta_membrete:
        return "No hay un membrete guardado para este proyecto.", 404

    respuesta = send_file(
        io.BytesIO(lot.acta_membrete),
        mimetype="application/pdf",
        as_attachment=True,
        download_name=f"membrete_proyecto_{lot.id}.pdf",
    )

    respuesta.headers["Cache-Control"] = "private, no-store"
    respuesta.headers["X-Content-Type-Options"] = "nosniff"

    return respuesta


@app.route(
    "/superadmin/configurar_acta/<int:lot_id>",
    methods=["POST"]
)

@app.route(
    "/superadmin/configurar_acta/<int:lot_id>",
    methods=["POST"]
)
@login_required
@superadmin_required
def configurar_acta(lot_id):
    import io
    import re
    import secrets

    recibido = request.form.get("acta_csrf", "")
    esperado = session.get("acta_csrf", "")

    if not esperado or not secrets.compare_digest(
        recibido.encode(),
        esperado.encode()
    ):
        return "Recarga el panel e inténtalo nuevamente.", 403

    lot = Lotizacion.query.get_or_404(lot_id)

    limites = {
        "acta_empresa": 200,
        "acta_ruc": 11,
        "acta_domicilio": 250,
        "acta_representante": 200,
        "acta_cargo": 100,
        "acta_dni_representante": 8,
    }

    datos = {
        campo: request.form.get(campo, "").strip()
        for campo in limites
    }

    try:
        from PIL import Image, UnidentifiedImageError

        if not all(datos.values()):
            raise ValueError(
                "Completa todos los datos de la empresa. "
                "El logo y el membrete PDF son opcionales."
            )

        if any(
            len(datos[campo]) > limite
            for campo, limite in limites.items()
        ):
            raise ValueError(
                "Uno de los campos supera el tamaño permitido."
            )

        if not re.fullmatch(r"[0-9]{11}", datos["acta_ruc"]):
            raise ValueError("El RUC debe tener 11 dígitos.")

        if not re.fullmatch(
            r"[0-9]{8}",
            datos["acta_dni_representante"]
        ):
            raise ValueError(
                "El DNI del representante debe tener 8 dígitos."
            )

        archivo = request.files.get("acta_logo")
        quitar_logo = request.form.get("quitar_logo") == "1"
        nuevo_logo = None

        if archivo and archivo.filename:
            if quitar_logo:
                raise ValueError(
                    'Desmarca "Quitar logo" si quieres subir uno nuevo.'
                )

            contenido = archivo.stream.read(
                5 * 1024 * 1024 + 1
            )

            if len(contenido) > 5 * 1024 * 1024:
                raise ValueError(
                    "El logo debe pesar como máximo 5 MB."
                )

            try:
                with Image.open(io.BytesIO(contenido)) as imagen:
                    if imagen.format not in ("PNG", "JPEG"):
                        raise ValueError(
                            "El logo debe ser una imagen PNG o JPG."
                        )

                    if imagen.width * imagen.height > 12000000:
                        raise ValueError(
                            "El logo es demasiado grande. "
                            "Usa una imagen menor a 12 megapíxeles."
                        )

                    imagen = imagen.convert("RGBA")
                    imagen.thumbnail((1000, 1000))

                    salida = io.BytesIO()
                    imagen.save(salida, format="PNG")
                    nuevo_logo = salida.getvalue()

            except (
                UnidentifiedImageError,
                OSError,
                Image.DecompressionBombError,
            ):
                raise ValueError(
                    "No se pudo leer el logo. Selecciona otro PNG o JPG."
                )

        archivo_membrete = request.files.get("acta_membrete")
        quitar_membrete = request.form.get("quitar_membrete") == "1"
        nuevo_membrete = None

        if archivo_membrete and archivo_membrete.filename:
            if quitar_membrete:
                raise ValueError(
                    'Desmarca "Quitar membrete" si quieres subir uno nuevo.'
                )

            contenido_pdf = archivo_membrete.stream.read(
                5 * 1024 * 1024 + 1
            )

            nuevo_membrete = _validar_membrete_acta(contenido_pdf)

        margenes = _validar_margenes_acta({
            campo: request.form.get(
                f"acta_margen_{campo}",
                str(defecto),
            )
            for campo, defecto in (
                ("superior", 32),
                ("inferior", 32),
                ("izquierdo", 32),
                ("derecho", 15),
            )
        })

        for campo, valor in datos.items():
            setattr(lot, campo, valor)

        if nuevo_logo is not None:
            lot.acta_logo = nuevo_logo
        elif quitar_logo:
            lot.acta_logo = None

        if nuevo_membrete is not None:
            lot.acta_membrete = nuevo_membrete
        elif quitar_membrete:
            lot.acta_membrete = None

        lot.acta_margenes = margenes

        db.session.commit()

        flash(
            f"Configuración del acta guardada para {lot.nombre}.",
            "success"
        )

    except ValueError as error:
        db.session.rollback()
        flash(str(error), "danger")

    except ImportError:
        db.session.rollback()
        flash(
            "Falta instalar pypdf o Pillow en este entorno de Python.",
            "danger"
        )

    except Exception:
        db.session.rollback()

        current_app.logger.exception(
            "Error al configurar el acta del proyecto %s",
            lot_id
        )

        flash(
            "No se pudo guardar la configuración del acta.",
            "danger"
        )

    return redirect(url_for("panel_superadmin"))


@app.route("/superadmin/logo_acta/<int:lot_id>")
@login_required
@superadmin_required
def logo_acta(lot_id):
    lot = Lotizacion.query.get_or_404(lot_id)

    if not lot.acta_logo:
        return "", 404

    respuesta = send_file(
        io.BytesIO(lot.acta_logo),
        mimetype="image/png"
    )

    respuesta.headers["Cache-Control"] = "private, no-store"

    return respuesta


def _crear_pdf_acta(
    datos, logo, fecha, membrete_pdf=None, margenes=None
):
    import io
    import re

    from decimal import Decimal, InvalidOperation
    from xml.sax.saxutils import escape

    from reportlab.lib import colors
    from reportlab.lib.enums import TA_CENTER, TA_LEFT
    from reportlab.lib.pagesizes import letter
    from reportlab.lib.units import mm
    from reportlab.lib.styles import ParagraphStyle
    from reportlab.lib.utils import ImageReader
    from reportlab.platypus import (
        SimpleDocTemplate, Paragraph, Spacer,
        Table, TableStyle, KeepTogether,
    )
    from reportlab.platypus.doctemplate import LayoutError

    def texto(valor):
        return (
            escape(str(valor))
            .replace("\r\n", "\n")
            .replace("\n", "<br/>")
        )

    def iniciales_direccion(valor):
        cadena = str(valor or "").strip()

        conectores = {
            "de", "del", "la", "las", "los",
            "el", "y", "e", "en", "al",
        }

        abreviaturas = {
            "av": "Av", "av.": "Av.",
            "jr": "Jr", "jr.": "Jr.",
            "urb": "Urb", "urb.": "Urb.",
            "mz": "Mz", "mz.": "Mz.",
            "lt": "Lt", "lt.": "Lt.",
            "dpto": "Dpto", "dpto.": "Dpto.",
            "int": "Int", "int.": "Int.",
            "aa.hh.": "AA.HH.", "aa.hh": "AA.HH",
            "a.h.": "A.H.", "a.h": "A.H",
            "s/n": "S/N", "km": "Km", "km.": "Km.",
        }

        prefijos = {
            "av", "jr", "urb", "avenida", "jirón", "jiron",
            "calle", "pasaje", "urbanización", "urbanizacion",
            "carretera", "aa.hh", "a.h",
        }

        romanos = {
            "ii", "iii", "iv", "vi", "vii",
            "viii", "ix", "xi", "xii",
        }

        inicio = True
        fin_anterior = 0

        def convertir(coincidencia):
            nonlocal inicio, fin_anterior

            separacion = cadena[fin_anterior:coincidencia.start()]

            if any(signo in separacion for signo in (",", ";", "\n")):
                inicio = True

            palabra = coincidencia.group(0)
            clave = palabra.lower()

            if clave in abreviaturas:
                resultado = abreviaturas[clave]
            elif any(caracter.isdigit() for caracter in palabra):
                resultado = palabra.upper()
            elif clave in conectores and not inicio:
                resultado = clave
            elif len(palabra.rstrip(".")) == 1 or clave in romanos:
                resultado = palabra.upper()
            else:
                resultado = palabra.capitalize()

            inicio = clave.rstrip(".") in prefijos
            fin_anterior = coincidencia.end()

            return resultado

        return re.sub(
            r"[^\W_]+(?:[./][^\W_]+)*\.?",
            convertir,
            cadena,
        )

    def medida(valor, etiqueta):
        try:
            numero = Decimal(str(valor))

            if not numero.is_finite() or numero <= 0:
                raise InvalidOperation()

        except (InvalidOperation, ValueError):
            raise ValueError(
                f"{etiqueta} debe ser un número mayor que cero."
            )

        entero, _, decimales = format(numero, "f").partition(".")

        return entero + "." + decimales.rstrip("0").ljust(2, "0")

    d = {
        campo: texto(valor)
        for campo, valor in datos.items()
    }

    for campo in (
        "direccion_cliente",
        "distrito_cliente",
        "provincia_cliente",
        "departamento_cliente",
    ):
        d[campo] = texto(iniciales_direccion(datos[campo]))

    d["area"] = medida(datos["area"], "El área")
    d["perimetro"] = medida(datos["perimetro"], "El perímetro")

    meses = (
        "enero", "febrero", "marzo", "abril",
        "mayo", "junio", "julio", "agosto",
        "septiembre", "octubre", "noviembre", "diciembre",
    )

    fecha_texto = (
        f"{fecha.day} de {meses[fecha.month - 1]} de {fecha.year}"
    )

    plantilla = None

    if membrete_pdf:
        from pypdf import PdfReader, PdfWriter

        margenes = _validar_margenes_acta(margenes)
        plantilla = _validar_membrete_acta(membrete_pdf)

        lector_plantilla = PdfReader(io.BytesIO(plantilla))
        pagina_base = lector_plantilla.pages[0]

        ancho = float(pagina_base.mediabox.width)
        alto = float(pagina_base.mediabox.height)
    else:
        ancho, alto = letter

    margen = 58

    dorado = colors.HexColor("#A38043")
    oscuro = colors.HexColor("#262D32")
    gris = colors.HexColor("#656D75")

    imagen = (
        ImageReader(io.BytesIO(logo))
        if logo and not plantilla else None
    )

    if not plantilla:
        nombre_completo = " ".join(str(datos["empresa"]).split())
        descriptor = ""
        nombre_cabecera = nombre_completo

        for prefijo in (
            "CONSTRUCTORA E INMOBILIARIA",
            "CONSTRUCTORA Y INMOBILIARIA",
            "INMOBILIARIA Y CONSTRUCTORA",
            "INMOBILIARIA",
            "CONSTRUCTORA",
        ):
            if nombre_completo.upper().startswith(prefijo + " "):
                descriptor = nombre_completo[:len(prefijo)].upper()
                nombre_cabecera = nombre_completo[len(prefijo):].strip()
                break

        razon_social = ""

        coincidencia = re.search(
            r"\s+(S\.?A\.?C\.?|S\.?R\.?L\.?|S\.?A\.?|E\.?I\.?R\.?L\.?)$",
            nombre_cabecera,
            re.IGNORECASE,
        )

        if coincidencia:
            razon_social = coincidencia.group(1).upper()
            nombre_cabecera = nombre_cabecera[:coincidencia.start()].strip()

        x_empresa = margen + 90 if imagen else margen
        ancho_empresa = ancho - margen - x_empresa
        alineacion = TA_LEFT if imagen else TA_CENTER

        estilo_descriptor = ParagraphStyle(
            "descriptor",
            fontName="Helvetica",
            fontSize=7.5,
            leading=10,
            textColor=oscuro,
            alignment=alineacion,
        )

        estilo_contacto = ParagraphStyle(
            "contacto",
            fontName="Helvetica",
            fontSize=8.2,
            leading=10.5,
            textColor=gris,
            alignment=alineacion,
        )

        descripcion = (
            Paragraph(texto(descriptor), estilo_descriptor)
            if descriptor else None
        )

        ruc_cabecera = Paragraph(
            f'<b>RUC {d["ruc"]}</b>',
            estilo_contacto,
        )

        domicilio_cabecera = Paragraph(
            d["domicilio_empresa"],
            estilo_contacto,
        )

        for letra in (32, 30, 28, 26, 23, 20, 17, 14, 11):
            estilo_empresa = ParagraphStyle(
                "empresa",
                fontName="Times-Bold",
                fontSize=letra,
                leading=letra + 2,
                textColor=oscuro,
                alignment=alineacion,
            )

            nombre_html = texto(nombre_cabecera.upper())

            if razon_social:
                nombre_html += (
                    f' <font size="{max(9, letra * 0.48):.1f}">'
                    f'{texto(razon_social)}</font>'
                )

            empresa = Paragraph(nombre_html, estilo_empresa)

            partes_cabecera = []

            if descripcion:
                partes_cabecera.append((descripcion, 4))

            partes_cabecera.extend([
                (empresa, 4),
                (ruc_cabecera, 2),
                (domicilio_cabecera, 0),
            ])

            alto_cabecera = sum(
                parrafo.wrap(ancho_empresa, alto)[1] + espacio
                for parrafo, espacio in partes_cabecera
            )

            if alto_cabecera <= 86:
                break
        else:
            raise ValueError(
                "El nombre o el domicilio de la empresa es demasiado "
                "extenso para el membrete."
            )

    def dibujar_logo(canvas, x, y, caja_ancho, caja_alto):
        original_ancho, original_alto = imagen.getSize()

        escala = min(
            caja_ancho / original_ancho,
            caja_alto / original_alto,
        )

        w = original_ancho * escala
        h = original_alto * escala

        canvas.drawImage(
            imagen,
            x + (caja_ancho - w) / 2,
            y + (caja_alto - h) / 2,
            width=w,
            height=h,
            mask="auto",
        )

    for tamano, interlineado in (
        (10.5, 15),
        (10.2, 14.5),
        (9.8, 13.8),
    ):
        memoria = io.BytesIO()
        paginas = [0]

        cuerpo = ParagraphStyle(
            "cuerpo",
            fontName="Helvetica",
            fontSize=tamano,
            leading=interlineado,
            textColor=oscuro,
            alignment=TA_LEFT,
            spaceAfter=11,
        )

        titulo = ParagraphStyle(
            "titulo",
            fontName="Helvetica-Bold",
            fontSize=14,
            leading=18,
            alignment=TA_CENTER,
            textColor=oscuro,
            spaceAfter=5,
        )

        subtitulo = ParagraphStyle(
            "proyecto",
            fontName="Helvetica-Bold",
            fontSize=8.3,
            leading=11,
            textColor=(
                colors.HexColor("#20395E")
                if plantilla else dorado
            ),
            alignment=TA_CENTER,
            spaceAfter=14,
        )

        lindero = ParagraphStyle(
            "lindero",
            parent=cuerpo,
            leftIndent=12,
            spaceAfter=3,
        )

        perimetro = ParagraphStyle(
            "perimetro",
            parent=lindero,
            spaceAfter=12,
        )

        fecha_estilo = ParagraphStyle(
            "fecha",
            parent=cuerpo,
            alignment=TA_CENTER,
            spaceAfter=0,
        )

        firma_estilo = ParagraphStyle(
            "firma",
            fontName="Helvetica-Bold",
            fontSize=9.4,
            leading=12.5,
            alignment=TA_CENTER,
            textColor=oscuro,
        )

        dni_estilo = ParagraphStyle(
            "dni",
            parent=firma_estilo,
            fontName="Helvetica-Bold",
            fontSize=9,
            textColor=gris,
        )

        def membrete(canvas, documento):
            paginas[0] = documento.page

            if plantilla:
                return

            canvas.saveState()

            canvas.setFillColor(colors.HexColor("#F1E8D3"))

            curva = canvas.beginPath()
            curva.moveTo(0, 0)
            curva.lineTo(0, 164)
            curva.curveTo(
                ancho * 0.245, 50,
                ancho * 0.572, 15,
                ancho, 84,
            )
            curva.lineTo(ancho, 0)
            curva.close()

            canvas.drawPath(curva, fill=1, stroke=0)

            canvas.setFillColor(dorado)
            canvas.rect(0, 10, 15, 60, fill=1, stroke=0)
            canvas.rect(
                ancho - 15, 28, 15, 42,
                fill=1, stroke=0,
            )

            if imagen:
                canvas.saveState()
                canvas.setFillAlpha(0.045)

                dibujar_logo(
                    canvas,
                    ancho / 2 - 130,
                    260,
                    260,
                    260,
                )

                canvas.restoreState()

                dibujar_logo(
                    canvas,
                    margen,
                    alto - 111,
                    72,
                    82,
                )

            y_cabecera = alto - 27

            for parrafo, espacio in partes_cabecera:
                _, altura = parrafo.wrap(ancho_empresa, alto)
                y_cabecera -= altura
                parrafo.drawOn(canvas, x_empresa, y_cabecera)
                y_cabecera -= espacio

            canvas.setStrokeColor(dorado)
            canvas.setLineWidth(0.7)

            canvas.line(
                margen,
                alto - 120,
                ancho - margen,
                alto - 120,
            )

            canvas.setFillColor(dorado)
            canvas.rect(
                margen,
                alto - 121,
                62,
                2.2,
                fill=1,
                stroke=0,
            )

            canvas.setFont("Helvetica-Bold", 6.8)
            canvas.setFillColor(gris)

            canvas.drawString(
                margen,
                22,
                "ACTA DE ENTREGA DE LOTE DE TERRENO",
            )

            canvas.drawRightString(
                ancho - margen,
                22,
                str(documento.page),
            )

            canvas.restoreState()

        contenido = [
            Paragraph(
                "ACTA DE ENTREGA LOTE DE TERRENO",
                titulo,
            ),
            Paragraph(d["proyecto"], subtitulo),
        ]

        contenido.append(
            Paragraph(
                f'Por el presente documento: La empresa '
                f'<b>{d["empresa"]}</b>, '
                f'con <b>RUC&nbsp;Nº&nbsp;{d["ruc"]}</b>, '
                f'con domicilio legal en {d["domicilio_empresa"]}, '
                f'representada por su {d["cargo"]} '
                f'<b>{d["representante"]}</b>, '
                f'con <b>DNI&nbsp;Nº&nbsp;{d["dni_representante"]}</b>, '
                f'hace entrega a <b>{d["cliente"]}</b>, '
                f'con <b>DNI&nbsp;Nº&nbsp;{d["dni"]}</b>, '
                f'con domicilio en {d["direccion_cliente"]}, '
                f'distrito de {d["distrito_cliente"]}, '
                f'provincia de {d["provincia_cliente"]} '
                f'y departamento de {d["departamento_cliente"]}.',
                cuerpo,
            )
        )

        contenido.append(
            Paragraph(
                f'El lote de terreno urbano Nº <b>{d["numero"]}</b> '
                f'de la manzana <b>{d["manzana"]}</b>, '
                f'con un área de <b>{d["area"]} m²</b>; '
                f'cuyos linderos y medida perimétrica son:',
                cuerpo,
            )
        )

        lista_linderos = []

        for campo, nombre in (
            ("frente", "Frente"),
            ("derecha", "Derecha entrando"),
            ("izquierda", "Izquierda entrando"),
            ("fondo", "Fondo"),
        ):
            lista_linderos.append(
                Paragraph(
                    f'<b>{nombre}:</b> {d[campo]}',
                    lindero,
                )
            )

        lista_linderos.append(
            Paragraph(
                f'<b>Perímetro: {d["perimetro"]} ml.</b>',
                perimetro,
            )
        )

        contenido.append(KeepTogether(lista_linderos))

        contenido.append(
            Paragraph(
                f'De la Lotización <b>“{d["proyecto"]}”</b>, '
                f'ubicada en el distrito de {d["distrito_proyecto"]}, '
                f'provincia de {d["provincia_proyecto"]}, '
                f'departamento de {d["departamento_proyecto"]}.',
                cuerpo,
            )
        )

        contenido.append(
            Paragraph(
                f'La entrega de este terreno por parte de '
                f'<b>{d["empresa"]}</b> se efectúa en conformidad '
                f'con el Art. Nº 1583 del Código Civil. '
                f'De esto, <b>EL COMPRADOR</b> adquiere la calidad prevista '
                f'por el Art. Nº 905 del Código Civil, '
                f'y en virtud de ello <b>{d["cliente"]}</b> '
                f'y <b>{d["empresa"]}</b> aceptan los alcances '
                f'que se realicen sobre el predio; '
                f'la exclusividad y responsabilidad corresponden '
                f'al <b>COMPRADOR</b> sobre el área de su propiedad.',
                cuerpo,
            )
        )

        contenido.append(
            Paragraph(
                f'El comprador, <b>{d["cliente"]}</b>, declara '
                f'que recibe el terreno a su satisfacción, '
                f'acepta y da conformidad con todo lo expuesto '
                f'en el <b>ACTA DE ENTREGA</b>.',
                cuerpo,
            )
        )

        bloque_firma = Table(
            [
                [Paragraph(d["cliente"], firma_estilo)],
                [Paragraph(f'DNI Nº {d["dni"]}', dni_estilo)],
            ],
            colWidths=[300],
            hAlign="CENTER",
        )

        bloque_firma.setStyle(TableStyle([
            ("LINEABOVE", (0, 0), (0, 0), 0.6, gris),
            ("TOPPADDING", (0, 0), (0, 0), 7),
            ("TOPPADDING", (0, 1), (0, 1), 1),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 2),
        ]))

        contenido.append(
            KeepTogether([
                Spacer(1, 7),
                Paragraph(
                    f'{d["distrito_proyecto"]}, {fecha_texto}',
                    fecha_estilo,
                ),
                Spacer(1, 46),
                bloque_firma,
            ])
        )

        documento = SimpleDocTemplate(
            memoria,
            pagesize=(ancho, alto),
            leftMargin=(
                margenes["izquierdo"] * mm
                if plantilla else margen - 6
            ),
            rightMargin=(
                margenes["derecho"] * mm
                if plantilla else margen - 6
            ),
            topMargin=(
                margenes["superior"] * mm
                if plantilla else 136
            ),
            bottomMargin=(
                margenes["inferior"] * mm
                if plantilla else 106
            ),
            title="Acta de entrega de lote de terreno",
            author=str(datos["empresa"]),
        )

        try:
            documento.build(
                contenido,
                onFirstPage=membrete,
                onLaterPages=membrete,
            )
        except LayoutError:
            continue

        if paginas[0] == 1:
            memoria.seek(0)

            if not plantilla:
                return memoria

            capa_texto = PdfReader(memoria)
            pagina_base.merge_page(capa_texto.pages[0])

            escritor = PdfWriter()
            escritor.add_page(pagina_base)
            escritor.add_metadata({
                "/Title": "Acta de entrega de lote de terreno",
                "/Author": str(datos["empresa"]),
            })

            salida = io.BytesIO()
            escritor.write(salida)
            salida.seek(0)

            return salida

    raise ValueError(
        "El texto es demasiado extenso para una hoja. "
        "Revisa la información del lote y la empresa."
    )

@app.route("/generar_acta_entrega/<int:compra_id>")
@login_required
@lotizacion_required
def generar_acta_entrega(compra_id):
    compra = Compra.query.get_or_404(compra_id)
    lote = compra.lote

    bloqueo = bloquear_si_no_es_lotizacion_activa(
        lote.lotizacion_id
    )

    if bloqueo:
        return bloqueo

    if compra.anulada or lote.estado != "vendido":
        flash(
            "El acta requiere una compra vigente de un lote vendido.",
            "warning"
        )
        return redirect(
            url_for("detalle_lote", lote_id=lote.id)
        )

    proyecto = lote.lotizacion
    cliente = compra.cliente

    if not proyecto or not cliente:
        flash(
            "No se encontró el proyecto o el cliente de la compra.",
            "danger"
        )
        return redirect(
            url_for("detalle_lote", lote_id=lote.id)
        )

    campos = {
        "empresa": (
            proyecto.acta_empresa,
            "empresa del acta"
        ),
        "ruc": (
            proyecto.acta_ruc,
            "RUC de la empresa"
        ),
        "domicilio_empresa": (
            proyecto.acta_domicilio,
            "domicilio de la empresa"
        ),
        "representante": (
            proyecto.acta_representante,
            "representante"
        ),
        "cargo": (
            proyecto.acta_cargo,
            "cargo del representante"
        ),
        "dni_representante": (
            proyecto.acta_dni_representante,
            "DNI del representante"
        ),
        "nombre": (
            cliente.nombre,
            "nombre del cliente"
        ),
        "apellidos": (
            cliente.apellidos,
            "apellidos del cliente"
        ),
        "dni": (
            cliente.dni,
            "DNI del cliente"
        ),
        "direccion_cliente": (
            cliente.direccion,
            "dirección del cliente"
        ),
        "distrito_cliente": (
            cliente.ciudad,
            "distrito del cliente"
        ),
        "provincia_cliente": (
            cliente.provincia,
            "provincia del cliente"
        ),
        "departamento_cliente": (
            cliente.departamento,
            "departamento del cliente"
        ),
        "proyecto": (
            proyecto.nombre,
            "nombre del proyecto"
        ),
        "distrito_proyecto": (
            getattr(proyecto, "distrito", None),
            "distrito del proyecto"
        ),
        "provincia_proyecto": (
            getattr(proyecto, "provincia", None),
            "provincia del proyecto"
        ),
        "departamento_proyecto": (
            getattr(proyecto, "departamento", None),
            "departamento del proyecto"
        ),
        "manzana": (
            lote.manzana,
            "manzana"
        ),
        "numero": (
            lote.numero,
            "número de lote"
        ),
        "area": (
            lote.area,
            "área del lote"
        ),
        "frente": (
            lote.lindero_frente,
            "lindero frente"
        ),
        "derecha": (
            lote.lindero_derecha,
            "lindero derecha"
        ),
        "izquierda": (
            lote.lindero_izquierda,
            "lindero izquierda"
        ),
        "fondo": (
            lote.lindero_fondo,
            "lindero fondo"
        ),
        "perimetro": (
            lote.perimetro,
            "perímetro"
        ),
    }

    faltantes = [
        etiqueta
        for valor, etiqueta in campos.values()
        if valor is None or not str(valor).strip()
    ]

    if faltantes:
        flash(
            "Falta completar: " + ", ".join(faltantes) + ".",
            "warning"
        )
        return redirect(
            url_for("detalle_lote", lote_id=lote.id)
        )

    datos = {
        campo: str(valor).strip()
        for campo, (valor, _) in campos.items()
    }

    datos["cliente"] = (
        datos.pop("nombre") + " " + datos.pop("apellidos")
    ).upper()

    try:
        fecha = datetime.now(
            pytz.timezone("America/Lima")
        ).date()

        archivo = _crear_pdf_acta(
            datos,
            proyecto.acta_logo,
            fecha,
            membrete_pdf=proyecto.acta_membrete,
            margenes=proyecto.acta_margenes,
        )

        nombre = secure_filename(
            f"acta_Mz_{lote.manzana}_Lote_{lote.numero}"
            f"_compra_{compra.id}.pdf"
        )

        respuesta = send_file(
            archivo,
            mimetype="application/pdf",
            as_attachment=True,
            download_name=nombre
        )

        respuesta.headers["Cache-Control"] = "private, no-store"

        return respuesta

    except ValueError as error:
        flash(str(error), "warning")

    except ImportError:
        flash(
            "Falta instalar ReportLab, Pillow o pypdf "
            "en el entorno de Python del sistema.",
            "danger"
        )

    except Exception:
        current_app.logger.exception(
            "Error al generar el acta de la compra %s",
            compra_id
        )

        flash(
            "No se pudo generar el acta. "
            "Revisa el registro de errores del servidor.",
            "danger"
        )

    return redirect(
        url_for("detalle_lote", lote_id=lote.id)
    )


# ------------------- MAIN -------------------
if __name__ == "__main__":
    app.run(debug=True)