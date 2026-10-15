import os
import uuid
from utils import lotizacion_required, admin_required, superadmin_required 
from flask import Flask, render_template, request, redirect, url_for, flash, jsonify, session,current_app
from werkzeug.utils import secure_filename
from models import db, Cliente, Lote, Compra, Pago, Cuota, Separacion, Historial, Lotizacion, Voucher, TransferenciaTitular
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

        lotes = (
            Lote.query
            .filter_by(lotizacion_id=lotizacion.id)
            .order_by(cast(Lote.numero, Integer))
            .all()
        )

    # Identificar los lotes vendidos mediante una compra al crédito.
    lotes_credito = set()

    if lotizacion:
        lotes_credito = {
            fila[0]
            for fila in (
                db.session.query(Compra.lote_id)
                .join(Lote, Compra.lote_id == Lote.id)
                .filter(
                    Lote.lotizacion_id == lotizacion.id,
                    Lote.estado == "vendido",
                    Compra.forma_pago == "credito",
                    Compra.anulada.is_(False)
                )
                .distinct()
                .all()
            )
        }

    manzanas = {}

    for lote in lotes:
        manzanas.setdefault(lote.manzana, []).append(lote)

    total_disponibles = (
        Lote.query.filter_by(
            estado="disponible",
            lotizacion_id=lotizacion.id
        ).count()
        if lotizacion else 0
    )

    total_separados = (
        Lote.query.filter_by(
            estado="separado",
            lotizacion_id=lotizacion.id
        ).count()
        if lotizacion else 0
    )

    total_vendidos = (
        Lote.query.filter_by(
            estado="vendido",
            lotizacion_id=lotizacion.id
        ).count()
        if lotizacion else 0
    )

    return render_template(
        "lotes_disponibles.html",
        manzanas=manzanas,
        total_disponibles=total_disponibles,
        total_separados=total_separados,
        total_vendidos=total_vendidos,
        lotizacion=lotizacion,
        lotes_credito=lotes_credito
    )

@app.route("/editar_cliente/<int:cliente_id>", methods=["GET", "POST"])
@login_required
@lotizacion_required
def editar_cliente(cliente_id):
    cliente = (
        _clientes_del_proyecto(session["lotizacion_id"])
        .filter(Cliente.id == cliente_id)
        .first_or_404()
    )

    lotizacion = Lotizacion.query.get(session["lotizacion_id"])

    if request.method == "POST":
        cliente.nombre = request.form["nombre"].strip().lower()
        cliente.apellidos = request.form["apellidos"].strip().lower()
        cliente.dni = request.form["dni"].strip()
        cliente.telefono = request.form["telefono"].strip()
        cliente.estado_civil = request.form.get(
            "estado_civil", "No registrado"
        ).strip().lower()
        cliente.ocupacion = request.form.get(
            "ocupacion", "No registrada"
        ).strip().lower()
        cliente.ciudad = request.form["ciudad"].strip().lower()
        cliente.provincia = request.form.get(
            "provincia", ""
        ).strip().lower()
        cliente.departamento = request.form.get(
            "departamento", ""
        ).strip().lower()
        cliente.direccion = request.form["direccion"].strip().lower()
        cliente.correo = request.form.get("correo", "").strip().lower()

        db.session.commit()
        flash("✅ Cliente actualizado correctamente.", "success")
        return redirect(url_for("ver_cliente", cliente_id=cliente.id))

    return render_template(
        "editar_cliente.html",
        cliente=cliente,
        lotizacion=lotizacion
    )

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

        transferencias_titular = (
        TransferenciaTitular.query
        .filter_by(lote_id=lote.id)
        .order_by(
            TransferenciaTitular.fecha.desc(),
            TransferenciaTitular.id.desc()
        )
        .all()
    )

    return render_template(
        "detalle_lote.html",
        lote=lote,
        compra=compra,
        separacion=separacion,
        lotizacion=lotizacion,
        error_linderos=error_linderos,
        transferencias_titular=transferencias_titular,
        formulario_titular=None
    )

@app.route("/estado_pagos")
@login_required
@lotizacion_required
def estado_pagos():
    lotizacion_id = session["lotizacion_id"]
    lotizacion = Lotizacion.query.get(lotizacion_id)

    clientes = _clientes_del_proyecto(lotizacion_id).all()
    resumen = []

    for cliente in clientes:
        compras = (
            Compra.query
            .join(Lote)
            .filter(
                Compra.cliente_id == cliente.id,
                Lote.lotizacion_id == lotizacion_id
            )
            .all()
        )

        total_precio = sum(c.precio for c in compras)
        total_pagado = 0

        for compra in compras:
            total_pagado += compra.inicial
            total_pagado += sum(
                cuota.monto
                for cuota in compra.cuotas
                if cuota.pagada
            )

        resumen.append({
            "cliente": cliente,
            "total_precio": total_precio,
            "total_pagado": total_pagado,
            "saldo": total_precio - total_pagado
        })

    return render_template(
        "estado_pagos.html",
        resumen=resumen,
        lotizacion=lotizacion
    )
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
# ---------------- VALIDACIÓN DEL LOTE AL REGISTRAR ----------------

def _lotes_libres_registro(lotizacion_id):
    return Lote.query.filter(
        Lote.lotizacion_id == lotizacion_id,
        Lote.estado == "disponible",
        ~db.session.query(Compra.id).filter(
            Compra.lote_id == Lote.id
        ).exists(),
        ~db.session.query(Separacion.id).filter(
            Separacion.lote_id == Lote.id,
            Separacion.activa.is_(True)
        ).exists()
    ).order_by(Lote.manzana, Lote.id).all()


def _validar_lote_registro(lotizacion_id, lote_id, sep_id=None):
    try:
        lote_id = int(lote_id)
    except (TypeError, ValueError):
        raise ValueError(
            "Selecciona un lote disponible de este proyecto."
        )

    lote = (
        Lote.query
        .filter_by(
            id=lote_id,
            lotizacion_id=lotizacion_id
        )
        .populate_existing()
        .with_for_update()
        .first()
    )

    if lote is None:
        raise ValueError(
            "El lote no existe o no pertenece al proyecto activo."
        )

    if Compra.query.filter_by(lote_id=lote.id).first():
        raise ValueError(
            "Este lote ya tiene una compra registrada. "
            "Selecciona otro lote."
        )

    activas = Separacion.query.filter_by(
        lote_id=lote.id,
        activa=True
    ).all()

    separacion = None

    if sep_id:
        try:
            sep_id = int(sep_id)
        except (TypeError, ValueError):
            raise ValueError(
                "La separación recibida no es válida."
            )

        if (
            lote.estado != "separado"
            or len(activas) != 1
            or activas[0].id != sep_id
        ):
            raise ValueError(
                "La separación ya no está activa "
                "o no corresponde al lote elegido."
            )

        separacion = (
            Separacion.query
            .filter_by(
                id=sep_id,
                lote_id=lote.id,
                activa=True
            )
            .populate_existing()
            .with_for_update()
            .first()
        )

        if separacion is None:
            raise ValueError(
                "La separación ya no está activa."
            )

    elif lote.estado != "disponible" or activas:
        raise ValueError(
            "Este lote ya no está disponible. "
            "Selecciona otro lote."
        )

    return lote, separacion


def _guardar_archivo_registro(
    archivo,
    carpeta,
    archivos_creados
):
    if not archivo or not archivo.filename:
        return None

    nombre = (
        uuid.uuid4().hex
        + "_"
        + (secure_filename(archivo.filename) or "archivo")
    )

    destino = os.path.join(app.static_folder, carpeta)
    os.makedirs(destino, exist_ok=True)

    ruta = os.path.join(destino, nombre)
    archivos_creados.append(ruta)
    archivo.save(ruta)

    return f"{carpeta}/{nombre}"


def _error_registro_lote(
    tipo,
    lotizacion,
    mensaje,
    cliente=None,
    separacion=None
):
    db.session.rollback()

    # Un cliente nuevo cuyo guardado falló todavía no está registrado.
    if cliente is not None:
        cliente = (
            db.session.get(Cliente, cliente.id)
            if cliente.id else None
        )

    lotes = _lotes_libres_registro(lotizacion.id)

    sep_valida = bool(
        separacion
        and separacion.activa
        and separacion.lote.lotizacion_id == lotizacion.id
        and separacion.lote.estado == "separado"
        and not Compra.query.filter_by(
            lote_id=separacion.lote_id
        ).first()
    )

    if sep_valida:
        lotes.append(separacion.lote)
    else:
        separacion = None

    sep_id = separacion.id if separacion else None

    if request.headers.get("X-Registro-Lote") == "1":
        return jsonify(
            error=mensaje,
            lotes=[
                {
                    "id": lote.id,
                    "manzana": lote.manzana,
                    "numero": lote.numero,
                    "estado": lote.estado
                }
                for lote in lotes
            ],
            sep_id=sep_id
        ), 400

    flash(mensaje, "warning")

    elegido = next(
        (
            lote
            for lote in lotes
            if str(lote.id) == request.form.get("lote")
        ),
        None
    )

    return render_template(
        f"registrar_{tipo}.html",
        lotizacion=lotizacion,
        lotes=lotes,
        lote=elegido,
        cliente=cliente,
        separacion=separacion,
        sep_id=sep_id
    ), 400


def _retirar_archivos_registro(archivos_creados):
    # Solo retira archivos nuevos de una operación que no se guardó.
    for ruta in archivos_creados:
        try:
            if os.path.isfile(ruta):
                os.remove(ruta)
        except OSError:
            current_app.logger.exception(
                "No se pudo retirar un archivo sin confirmar."
            )


@app.route("/registrar_compra", methods=["GET", "POST"])
@lotizacion_required
@login_required
def registrar_compra():
    lotizacion = db.session.get(
        Lotizacion,
        session["lotizacion_id"]
    )

    if lotizacion is None:
        flash("Selecciona un proyecto válido.", "warning")
        return redirect(url_for("seleccionar_lotizacion"))

    cliente = None
    separacion = None
    lote = None

    sep_id = (
        request.form.get("sep_id")
        if request.method == "POST"
        else request.args.get("sep_id")
    )

    archivos_creados = []

    if request.method == "POST":
        try:
            # Primero comprobar y bloquear el lote.
            lote, separacion = _validar_lote_registro(
                lotizacion.id,
                request.form.get("lote"),
                sep_id
            )

            dni = request.form.get("dni", "").strip()
            nombre = request.form.get(
                "nombre", ""
            ).strip().lower()
            apellidos = request.form.get(
                "apellidos", ""
            ).strip().lower()

            if not dni or not nombre or not apellidos:
                raise ValueError(
                    "Completa el DNI, nombre y apellidos del cliente."
                )

            cliente_id = request.form.get(
                "cliente_id", ""
            ).strip()

            if separacion:
                cliente = separacion.cliente

                if (
                    cliente.dni != dni
                    or (
                        cliente_id
                        and cliente_id != str(cliente.id)
                    )
                ):
                    raise ValueError(
                        "Los datos del cliente no corresponden "
                        "a esta separación."
                    )

            elif cliente_id:
                try:
                    cliente = db.session.get(
                        Cliente,
                        int(cliente_id)
                    )
                except ValueError:
                    raise ValueError(
                        "El cliente seleccionado no es válido."
                    )

                if cliente is None or cliente.dni != dni:
                    cliente = None
                    raise ValueError(
                        "El DNI no coincide con el cliente seleccionado. "
                        "Consulta nuevamente."
                    )

            else:
                cliente = Cliente.query.filter_by(
                    dni=dni
                ).first()

            forma_pago = request.form.get("forma_pago", "")

            if forma_pago not in ("contado", "credito"):
                raise ValueError(
                    "Selecciona una forma de pago válida."
                )

            try:
                precio = float(request.form.get("precio", ""))

                inicial = (
                    float(request.form.get("inicial") or 0)
                    if forma_pago == "credito"
                    else 0
                )

                interes = (
                    float(request.form.get("interes") or 0)
                    if forma_pago == "credito"
                    else 0
                )
            except (TypeError, ValueError):
                raise ValueError(
                    "Revisa el precio, la inicial y el interés."
                )

            if (
                any(
                    not (0 <= valor < float("inf"))
                    for valor in (precio, inicial, interes)
                )
                or inicial > precio
            ):
                raise ValueError(
                    "Los montos deben ser válidos y la inicial "
                    "no puede superar el precio."
                )

            try:
                cuotas_total = (
                    int(request.form.get("cuotas") or 0)
                    if forma_pago == "credito"
                    else 0
                )
            except (TypeError, ValueError):
                raise ValueError(
                    "La cantidad de cuotas debe ser un número entero."
                )

            if (
                cuotas_total < 0
                or (
                    forma_pago == "credito"
                    and precio > inicial
                    and cuotas_total == 0
                )
            ):
                raise ValueError(
                    "Si queda saldo por pagar, "
                    "debes indicar al menos 1 cuota."
                )

            fecha_texto = request.form.get("fecha_compra", "").strip()
            ahora_peru = hora_local_peru().replace(tzinfo=None)

            try:
                if fecha_texto:
                    fecha_compra = datetime.strptime(fecha_texto, "%Y-%m-%d")
                else:
                    fecha_compra = ahora_peru
            except ValueError:
                raise ValueError("La fecha de compra no es válida.")

            if fecha_compra.date() > ahora_peru.date():
                raise ValueError(
                    "La fecha de venta no puede ser posterior a hoy. "
                    "Revisa la fecha ingresada."
                )
            monto_total = precio

            if forma_pago == "credito" and interes > 0:
                monto_total = (
                    precio
                    + (precio - inicial) * (interes / 100)
                )

            if not (0 <= monto_total < float("inf")):
                raise ValueError(
                    "Revisa el precio y el interés; "
                    "el total no es válido."
                )

            if cliente is None:
                cliente = Cliente(
                    dni=dni,
                    nombre=nombre,
                    apellidos=apellidos
                )
                db.session.add(cliente)

            cliente.nombre = nombre
            cliente.apellidos = apellidos

            for campo in (
                "telefono",
                "direccion",
                "ciudad",
                "provincia",
                "departamento",
                "estado_civil",
                "ocupacion"
            ):
                valor = request.form.get(campo, "").strip()

                if campo in (
                    "direccion",
                    "ciudad",
                    "provincia",
                    "departamento",
                    "estado_civil",
                    "ocupacion"
                ):
                    valor = valor.lower()

                setattr(cliente, campo, valor)

            correo = request.form.get(
                "correo", ""
            ).strip().lower()

            if correo:
                cliente.correo = correo

            db.session.flush()

            for campo in ("dni_frontal", "dni_reverso"):
                ruta = _guardar_archivo_registro(
                    request.files.get(campo),
                    "dni",
                    archivos_creados
                )

                if ruta:
                    setattr(cliente, campo, ruta)

            boucher = _guardar_archivo_registro(
                request.files.get("boucher_inicial"),
                "bouchers",
                archivos_creados
            )

            compra = Compra(
                cliente_id=cliente.id,
                lote_id=lote.id,
                forma_pago=forma_pago,
                precio=monto_total,
                inicial=inicial,
                cuotas_total=cuotas_total,
                cuota_monto=(
                    (monto_total - inicial) / cuotas_total
                    if forma_pago == "credito" and cuotas_total > 0
                    else 0
                ),
                boucher_inicial=boucher,
                interes=interes,
                usuario_id=current_user.id,
                fecha_compra=fecha_compra
            )

            if forma_pago == "contado":
                compra.cancelado = True
                compra.fecha_cancelacion = fecha_compra

            db.session.add(compra)
            db.session.flush()

            for numero in range(1, cuotas_total + 1):
                db.session.add(
                    Cuota(
                        compra_id=compra.id,
                        numero=numero,
                        monto=compra.cuota_monto,
                        fecha_vencimiento=(
                            fecha_compra
                            + timedelta(days=30 * numero)
                        )
                    )
                )

            if separacion:
                separacion.activa = False

                db.session.add(
                    Historial(
                        cliente_id=cliente.id,
                        lote_id=lote.id,
                        tipo="Separación convertida",
                        detalle=(
                            f"Separación de S/ {separacion.monto} "
                            "convertida en compra"
                        )
                    )
                )

            lote.estado = "vendido"
            cliente_guardado = cliente.id

            # Guardar cliente, compra, cuotas y estado juntos.
            db.session.commit()

        except (ValueError, TypeError) as error:
            db.session.rollback()
            _retirar_archivos_registro(archivos_creados)

            return _error_registro_lote(
                "compra",
                lotizacion,
                str(error),
                cliente,
                separacion
            )

        except Exception:
            db.session.rollback()
            _retirar_archivos_registro(archivos_creados)

            current_app.logger.exception(
                "Error al registrar una compra."
            )

            return _error_registro_lote(
                "compra",
                lotizacion,
                "No se pudo guardar la compra. "
                "Revisa los datos e inténtalo nuevamente."
            )

        destino = url_for(
            "ver_cliente",
            cliente_id=cliente_guardado
        )

        flash("Compra registrada correctamente.", "success")

        if request.headers.get("X-Registro-Lote") == "1":
            return jsonify(url=destino)

        return redirect(destino)

    lotes = _lotes_libres_registro(lotizacion.id)

    if sep_id:
        try:
            separacion = (
                Separacion.query
                .join(Lote)
                .filter(
                    Separacion.id == int(sep_id),
                    Separacion.activa.is_(True),
                    Lote.lotizacion_id == lotizacion.id,
                    Lote.estado == "separado"
                )
                .first()
            )
        except ValueError:
            separacion = None

        if (
            separacion is None
            or Compra.query.filter_by(
                lote_id=separacion.lote_id
            ).first()
        ):
            flash(
                "La separación ya no está activa "
                "o no pertenece al proyecto.",
                "warning"
            )
            return redirect(url_for("lotes_disponibles"))

        lote = separacion.lote
        cliente = separacion.cliente
        lotes.append(lote)

    else:
        lote_param = request.args.get("lote_id", type=int)

        if lote_param:
            lote = next(
                (
                    disponible
                    for disponible in lotes
                    if disponible.id == lote_param
                ),
                None
            )

            if lote is None:
                flash(
                    "El lote ya no está disponible en este proyecto.",
                    "warning"
                )
                return redirect(url_for("lotes_disponibles"))

        cliente_param = request.args.get(
            "cliente_id",
            type=int
        )

        if cliente_param:
            cliente = db.session.get(
                Cliente,
                cliente_param
            )

        manzana = (
            request.args.get("manzana", "")
            .strip()
            .replace("&#39;", "'")
            .replace("&apos;", "'")
        )

        if manzana and not lote:
            lotes = [
                disponible
                for disponible in lotes
                if disponible.manzana == manzana
            ]

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
    lotizacion = db.session.get(
        Lotizacion,
        session["lotizacion_id"]
    )

    if lotizacion is None:
        flash("Selecciona un proyecto válido.", "warning")
        return redirect(url_for("seleccionar_lotizacion"))

    cliente = None
    archivos_creados = []

    if request.method == "POST":
        try:
            lote, _ = _validar_lote_registro(
                lotizacion.id,
                request.form.get("lote")
            )

            dni = request.form.get("dni", "").strip()
            nombre = request.form.get(
                "nombre", ""
            ).strip().lower()
            apellidos = request.form.get(
                "apellidos", ""
            ).strip().lower()

            if not dni or not nombre or not apellidos:
                raise ValueError(
                    "Completa el DNI, nombre y apellidos del cliente."
                )

            try:
                monto = float(request.form.get("monto", ""))
            except (TypeError, ValueError):
                raise ValueError(
                    "El monto de separación no es válido."
                )

            if not (0 <= monto < float("inf")):
                raise ValueError(
                    "El monto de separación debe ser válido "
                    "y no negativo."
                )

            cliente = Cliente.query.filter_by(dni=dni).first()

            if cliente is None:
                cliente = Cliente(
                    dni=dni,
                    nombre=nombre,
                    apellidos=apellidos
                )
                db.session.add(cliente)

            for campo in (
                "estado_civil",
                "ocupacion",
                "telefono",
                "direccion",
                "ciudad",
                "provincia",
                "departamento",
                "correo"
            ):
                valor = request.form.get(campo, "").strip()

                if campo != "telefono":
                    valor = valor.lower()

                if valor:
                    setattr(cliente, campo, valor)

            db.session.flush()

            for campo in ("dni_frontal", "dni_reverso"):
                ruta = _guardar_archivo_registro(
                    request.files.get(campo),
                    "dni",
                    archivos_creados
                )

                if ruta:
                    setattr(cliente, campo, ruta)

            boucher = _guardar_archivo_registro(
                request.files.get("boucher"),
                "bouchers",
                archivos_creados
            )

            db.session.add(
                Separacion(
                    cliente_id=cliente.id,
                    lote_id=lote.id,
                    monto=monto,
                    fecha=hora_local_peru().replace(tzinfo=None),
                    boucher=boucher,
                    activa=True,
                    usuario_id=current_user.id
                )
            )

            lote.estado = "separado"
            cliente_guardado = cliente.id

            db.session.commit()

        except (ValueError, TypeError) as error:
            db.session.rollback()
            _retirar_archivos_registro(archivos_creados)

            return _error_registro_lote(
                "separacion",
                lotizacion,
                str(error),
                cliente
            )

        except Exception:
            db.session.rollback()
            _retirar_archivos_registro(archivos_creados)

            current_app.logger.exception(
                "Error al registrar una separación."
            )

            return _error_registro_lote(
                "separacion",
                lotizacion,
                "No se pudo guardar la separación. "
                "Revisa los datos e inténtalo nuevamente."
            )

        destino = url_for(
            "ver_cliente",
            cliente_id=cliente_guardado
        )

        flash(
            "Separación registrada correctamente.",
            "success"
        )

        if request.headers.get("X-Registro-Lote") == "1":
            return jsonify(url=destino)

        return redirect(destino)

    lotes = _lotes_libres_registro(lotizacion.id)

    lote_param = request.args.get("lote_id", type=int)

    lote = next(
        (
            disponible
            for disponible in lotes
            if disponible.id == lote_param
        ),
        None
    )

    if lote_param and lote is None:
        flash(
            "El lote ya no está disponible en este proyecto.",
            "warning"
        )
        return redirect(url_for("lotes_disponibles"))

    return render_template(
        "registrar_separacion.html",
        lotes=lotes,
        lote=lote,
        cliente=None,
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
    transferencias_titular = []
    vouchers_separacion = {}

    lotizacion = None

    if "lotizacion_id" in session:
        lotizacion = Lotizacion.query.get(
            session["lotizacion_id"]
        )

    lotizacion_id = lotizacion.id if lotizacion else None

    cliente_id = request.args.get("cliente_id")

    if cliente_id:
        cliente = (
            _clientes_del_proyecto(lotizacion_id)
            .filter(Cliente.id == int(cliente_id))
            .first_or_404()
        )

    elif request.method == "POST":
        criterio = request.form.get("criterio")

        if criterio and lotizacion_id:
            cliente = (
                _clientes_del_proyecto(lotizacion_id)
                .filter(
                    (Cliente.dni == criterio)
                    | Cliente.apellidos.ilike(f"%{criterio}%")
                )
                .first()
            )

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

        transferencias_titular = (
            TransferenciaTitular.query
            .join(
                Lote,
                Lote.id == TransferenciaTitular.lote_id
            )
            .filter(
                Lote.lotizacion_id == lotizacion_id,
                (
                    TransferenciaTitular.cliente_anterior_id == cliente.id
                )
                | (
                    TransferenciaTitular.cliente_nuevo_id == cliente.id
                )
            )
            .order_by(
                TransferenciaTitular.fecha.desc(),
                TransferenciaTitular.id.desc()
            )
            .all()
        )

        # Conservar el voucher de la separación original,
        # aunque la compra haya cambiado de titular.
        for compra in compras_contado + compras_credito:
            separacion_convertida = _separacion_original_compra(
                compra
            )

            if separacion_convertida and separacion_convertida.boucher:
                vouchers_separacion[compra.id] = separacion_convertida

    return render_template(
        "ver_cliente.html",
        cliente=cliente,
        separaciones=separaciones,
        compras_contado=compras_contado,
        compras_credito=compras_credito,
        historial=historial,
        transferencias_titular=transferencias_titular,
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
@admin_required
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
        term = f"%{query.lower()}%"

        clientes = (
            _clientes_del_proyecto(
                session.get("lotizacion_id")
            )
            .filter(
                or_(
                    db.func.lower(Cliente.dni).like(term),
                    db.func.lower(Cliente.apellidos).like(term)
                )
            )
            .all()
        )

    return render_template(
        "buscar_cliente.html",
        query=query,
        clientes=clientes
    )

@app.route("/buscar_cliente_json")
@login_required
@lotizacion_required
def buscar_cliente_json():
    q = request.args.get("q", "").strip()

    if len(q) < 2:
        return jsonify([])

    term = f"%{q.lower()}%"

    clientes = (
        _clientes_del_proyecto(session["lotizacion_id"])
        .filter(
            or_(
                db.func.lower(Cliente.apellidos).like(term),
                db.func.lower(Cliente.dni).like(term)
            )
        )
        .limit(10)
        .all()
    )

    return jsonify([
        {
            "id": cliente.id,
            "nombre": cliente.nombre,
            "apellidos": cliente.apellidos,
            "dni": cliente.dni
        }
        for cliente in clientes
    ])

@app.route("/autocomplete_clientes")
@login_required
def autocomplete_clientes():
    term = request.args.get("term", "")
    lotizacion_id = session.get("lotizacion_id")

    if not term or not lotizacion_id:
        return jsonify([])

    clientes = (
        _clientes_del_proyecto(lotizacion_id)
        .filter(
            Cliente.dni.ilike(f"%{term}%")
            | Cliente.apellidos.ilike(f"%{term}%")
        )
        .all()
    )

    results = [
        {
            "id": c.id,
            "label": f"{c.apellidos} {c.nombre} - {c.dni}",
            "value": c.apellidos
        }
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

    bloqueo = bloquear_si_no_es_lotizacion_activa(
        compra.lote.lotizacion_id
    )

    if bloqueo:
        return bloqueo

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
@lotizacion_required
def eliminar_voucher_cuota(cuota_id):
    cuota = Cuota.query.get_or_404(cuota_id)

    bloqueo = bloquear_si_no_es_lotizacion_activa(
        cuota.compra.lote.lotizacion_id
    )

    if bloqueo:
        return bloqueo
    
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

def _estado_correccion_pago(compra, pago, cuotas):
    return {
        "compra": [
            compra.id,
            compra.cliente_id,
            compra.lote_id,
            compra.forma_pago,
            compra.precio,
            compra.inicial,
            compra.boucher_inicial,
            compra.anulada,
            compra.cancelado,
            (
                compra.fecha_cancelacion.isoformat()
                if compra.fecha_cancelacion else None
            )
        ],
        "pago": [
            pago.id,
            pago.compra_id,
            pago.monto,
            pago.boucher,
            pago.fecha_pago.isoformat() if pago.fecha_pago else None
        ],
        "cuotas": [
            [
                c.id,
                c.compra_id,
                c.numero,
                c.monto,
                c.pagada,
                c.pago_id,
                (
                    c.fecha_vencimiento.isoformat()
                    if c.fecha_vencimiento else None
                )
            ]
            for c in sorted(cuotas, key=lambda c: c.id)
        ]
    }


def _contexto_correccion_pagos():
    csrf = session.setdefault(
        "csrf_corregir_pago",
        secrets.token_urlsafe(32)
    )

    contexto = {
        "lotizacion": request.args.get("cp_lotizacion", ""),
        "manzana": request.args.get("cp_manzana", "").strip(),
        "numero": request.args.get("cp_numero", "").strip(),
        "compra": None,
        "pagos": [],
        "error": None,
        "csrf": csrf
    }

    if not any(
        contexto[k]
        for k in ("lotizacion", "manzana", "numero")
    ):
        return contexto

    try:
        lotizacion_id = int(contexto["lotizacion"])

        if not contexto["manzana"] or not contexto["numero"]:
            raise ValueError(
                "Selecciona el proyecto e indica la manzana y el lote."
            )

        compras = (
            Compra.query
            .join(Lote, Compra.lote_id == Lote.id)
            .filter(
                Lote.lotizacion_id == lotizacion_id,
                func.lower(Lote.manzana)
                == contexto["manzana"].lower(),
                Lote.numero == contexto["numero"],
                Compra.anulada.is_(False),
                Compra.forma_pago == "credito"
            )
            .all()
        )

        if len(compras) != 1:
            raise ValueError(
                "No se encontró una compra vigente al crédito "
                "para ese lote."
                if not compras else
                "Ese lote tiene más de una compra vigente. "
                "Revisa el caso antes de corregir."
            )

        compra = compras[0]
        contexto["compra"] = compra

        firmador = URLSafeTimedSerializer(
            app.config["SECRET_KEY"],
            salt="corregir-pago-cuotas-v1"
        )

        pagos = (
            Pago.query
            .filter_by(compra_id=compra.id)
            .order_by(Pago.id)
            .all()
        )

        for pago in pagos:
            cuotas = (
                Cuota.query
                .filter_by(pago_id=pago.id)
                .order_by(Cuota.numero, Cuota.id)
                .all()
            )

            valido = bool(cuotas) and all(
                c.compra_id == compra.id and c.pagada
                for c in cuotas
            )

            contexto["pagos"].append({
                "pago": pago,
                "cuotas": cuotas,
                "valido": valido,
                "token": (
                    firmador.dumps({
                        "usuario_id": current_user.id,
                        "csrf": csrf,
                        "compra_id": compra.id,
                        "pago_id": pago.id,
                        "estado": _estado_correccion_pago(
                            compra, pago, cuotas
                        )
                    })
                    if valido else ""
                )
            })

    except (TypeError, ValueError) as error:
        contexto["error"] = str(error)

    return contexto


@app.route(
    "/superadmin/corregir_pago_cuotas",
    methods=["POST"]
)
@login_required
@superadmin_required
def corregir_pago_cuotas():
    destino = (
        url_for("panel_superadmin") + "#correccion-pagos"
    )

    ruta_nueva = None
    archivo_creado = False

    try:
        csrf = session.get("csrf_corregir_pago", "")
        recibido = request.form.get("csrf_corregir_pago", "")

        if not csrf or not secrets.compare_digest(csrf, recibido):
            raise ValueError(
                "El formulario venció. Busca nuevamente la compra."
            )

        firmador = URLSafeTimedSerializer(
            app.config["SECRET_KEY"],
            salt="corregir-pago-cuotas-v1"
        )

        datos = firmador.loads(
            request.form.get("token_pago", ""),
            max_age=1800
        )

        if (
            not isinstance(datos, dict)
            or datos.get("usuario_id") != current_user.id
            or datos.get("csrf") != csrf
        ):
            raise ValueError(
                "El formulario no corresponde a tu sesión. "
                "Busca nuevamente la compra."
            )

        accion = request.form.get("accion", "")

        if (
            accion not in ("reemplazar", "deshacer")
            or request.form.get("confirmacion") != "si"
        ):
            raise ValueError(
                "Selecciona una acción y marca la casilla "
                "de confirmación."
            )

        contenido = None

        if accion == "reemplazar":
            from PIL import Image, UnidentifiedImageError

            archivo = request.files.get("voucher_nuevo")

            if not archivo or not archivo.filename:
                raise ValueError("Selecciona el nuevo voucher.")

            contenido = archivo.read(10 * 1024 * 1024 + 1)

            if (
                not contenido
                or len(contenido) > 10 * 1024 * 1024
            ):
                raise ValueError(
                    "El voucher debe ser una imagen de hasta 10 MB."
                )

            try:
                with Image.open(io.BytesIO(contenido)) as imagen:
                    extension = {
                        "JPEG": ".jpg",
                        "PNG": ".png",
                        "WEBP": ".webp"
                    }.get(imagen.format)

                    if (
                        not extension
                        or imagen.width * imagen.height > 30_000_000
                    ):
                        raise ValueError("Imagen no permitida.")

                    imagen.verify()

            except (
                UnidentifiedImageError,
                OSError,
                ValueError,
                Image.DecompressionBombError
            ):
                raise ValueError(
                    "Selecciona una imagen JPG, PNG o WEBP válida."
                )

        compra = (
            Compra.query
            .filter_by(id=datos["compra_id"])
            .populate_existing()
            .with_for_update()
            .first()
        )

        if (
            not compra
            or compra.anulada
            or compra.forma_pago != "credito"
        ):
            raise ValueError(
                "La compra ya no está vigente al crédito."
            )

        destino = url_for(
            "panel_superadmin",
            cp_lotizacion=compra.lote.lotizacion_id,
            cp_manzana=compra.lote.manzana,
            cp_numero=compra.lote.numero
        ) + "#correccion-pagos"

        pago = (
            Pago.query
            .filter_by(
                id=datos["pago_id"],
                compra_id=compra.id
            )
            .populate_existing()
            .with_for_update()
            .first()
        )

        if not pago:
            raise ValueError(
                "El pago ya no existe. Busca nuevamente la compra."
            )

        cuotas = (
            Cuota.query
            .filter_by(pago_id=pago.id)
            .order_by(Cuota.id)
            .populate_existing()
            .with_for_update()
            .all()
        )

        if not cuotas or any(
            c.compra_id != compra.id or not c.pagada
            for c in cuotas
        ):
            raise ValueError(
                "Las cuotas asociadas al pago no son válidas "
                "para esta corrección."
            )

        if (
            _estado_correccion_pago(compra, pago, cuotas)
            != datos["estado"]
        ):
            raise ValueError(
                "La compra o el pago cambió. "
                "Busca nuevamente y revisa los datos."
            )

        pago_id = pago.id
        compra_id = compra.id
        monto = pago.monto

        if accion == "reemplazar":
            carpeta = os.path.join(
                app.static_folder, "bouchers"
            )
            os.makedirs(carpeta, exist_ok=True)

            nombre = (
                "voucher_" + uuid.uuid4().hex + extension
            )
            ruta_nueva = os.path.join(carpeta, nombre)

            with open(ruta_nueva, "xb") as salida:
                archivo_creado = True
                salida.write(contenido)

            # Cambiar únicamente el adjunto del pago.
            pago.boucher = "bouchers/" + nombre

        else:
            # Dejar pendientes todas las cuotas de este pago.
            for cuota in cuotas:
                cuota.pagada = False
                cuota.pago_id = None

            db.session.flush()

            # Retirar únicamente el pago seleccionado.
            db.session.delete(pago)

            compra.cancelado = False
            compra.fecha_cancelacion = None

        db.session.commit()

    except (BadSignature, SignatureExpired):
        db.session.rollback()

        flash(
            "La confirmación venció o no es válida. "
            "Busca nuevamente la compra.",
            "warning"
        )
        return redirect(destino)

    except Exception as error:
        db.session.rollback()

        if archivo_creado:
            try:
                os.remove(ruta_nueva)
            except OSError:
                current_app.logger.exception(
                    "No se pudo retirar el archivo nuevo "
                    "tras el error."
                )

        if isinstance(error, ValueError):
            flash(str(error), "warning")
        else:
            current_app.logger.exception(
                "Error al corregir un pago de cuotas"
            )
            flash(
                "No se pudo completar la corrección. "
                "No se guardaron cambios.",
                "danger"
            )

        return redirect(destino)

    current_app.logger.info(
        "Corrección de cuotas: usuario=%s accion=%s "
        "compra=%s pago=%s monto=%s",
        current_user.id, accion, compra_id, pago_id, monto
    )

    flash(
        "Voucher reemplazado. El pago y las cuotas se conservan."
        if accion == "reemplazar" else
        "Pago deshecho. Sus cuotas quedaron pendientes. "
        "La inicial se conservó; registra cada cuota "
        "con su voucher correcto.",
        "success"
    )

    return redirect(destino)


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
        correccion=_contexto_correccion_pagos(),
    )
# ------------------- CONFIGURAR PLANO DE LOTIZACIÓN -------------------

@app.route(
    "/superadmin/configurar_plano/<int:lot_id>",
    methods=["POST"]
)
@login_required
@superadmin_required
def configurar_plano(lot_id):
    lotizacion = Lotizacion.query.get_or_404(lot_id)
    archivo = request.files.get("plano_imagen")

    if not archivo or not archivo.filename:
        flash("Selecciona un plano en PDF, PNG o JPG.", "warning")
        return redirect(url_for("panel_superadmin"))

    try:
        from PIL import Image, UnidentifiedImageError
        import pypdfium2 as pdfium

        nombre_original = secure_filename(archivo.filename)
        extension = os.path.splitext(nombre_original)[1].lower()

        contenido = archivo.stream.read(10 * 1024 * 1024 + 1)
        if extension == ".pdf" or archivo.mimetype == "application/pdf":
            lotizacion.plano_pdf = contenido
        else:
            lotizacion.plano_pdf = None

        if len(contenido) > 10 * 1024 * 1024:
            raise ValueError(
                "El plano no puede pesar más de 10 MB."
            )

        imagen = None

        # Si es PDF, convertir su única página a imagen
        if extension == ".pdf" or archivo.mimetype == "application/pdf":
            documento = pdfium.PdfDocument(contenido)

            if len(documento) != 1:
                raise ValueError(
                    "El PDF del plano debe tener exactamente una página."
                )

            pagina = documento[0]
            bitmap = pagina.render(scale=2.5)
            imagen = bitmap.to_pil().convert("RGBA")

            nombre_guardado = (
                os.path.splitext(nombre_original)[0] + ".png"
            )

        else:
            # Si es PNG o JPG, leer directamente la imagen
            with Image.open(io.BytesIO(contenido)) as imagen_original:
                if imagen_original.format not in ("PNG", "JPEG"):
                    raise ValueError(
                        "El plano debe estar en formato PDF, PNG o JPG."
                    )

                imagen = imagen_original.convert("RGBA")

            nombre_guardado = (
                os.path.splitext(nombre_original)[0] + ".png"
            )

        if imagen.width * imagen.height > 40000000:
            raise ValueError(
                "La imagen del plano tiene una resolución demasiado grande."
            )

        imagen.thumbnail(
            (5000, 5000),
            Image.Resampling.LANCZOS
        )

        salida = io.BytesIO()
        imagen.save(
            salida,
            format="PNG",
            optimize=True
        )

        lotizacion.plano_imagen = salida.getvalue()
        lotizacion.plano_nombre = secure_filename(nombre_guardado)

        # Al cambiar la imagen, se reinician las zonas configuradas
        # porque las coordenadas anteriores ya no coincidirían.
        lotizacion.plano_config = {}

        db.session.commit()

        flash(
            f"Plano guardado correctamente para {lotizacion.nombre}.",
            "success"
        )

    except ValueError as error:
        db.session.rollback()
        flash(str(error), "danger")

    except ImportError:
        db.session.rollback()
        flash(
            "Falta instalar pypdfium2 en este entorno de Python.",
            "danger"
        )

    except (
        UnidentifiedImageError,
        OSError,
        Image.DecompressionBombError,
    ):
        db.session.rollback()
        flash(
            "No se pudo leer el plano. "
            "Verifica que el PDF, PNG o JPG sea válido.",
            "danger"
        )

    except Exception:
        db.session.rollback()

        current_app.logger.exception(
            "Error al guardar el plano de la lotización %s",
            lot_id
        )

        flash(
            "No se pudo guardar el plano.",
            "danger"
        )

    return redirect(url_for("panel_superadmin"))

@app.route("/superadmin/plano_imagen/<int:lot_id>")
@login_required
@superadmin_required
def plano_imagen(lot_id):
    lotizacion = Lotizacion.query.get_or_404(lot_id)

    if not lotizacion.plano_imagen:
        return "Esta lotización todavía no tiene un plano.", 404

    respuesta = send_file(
        io.BytesIO(lotizacion.plano_imagen),
        mimetype="image/png",
        download_name=(
            lotizacion.plano_nombre
            or f"plano_lotizacion_{lotizacion.id}.png"
        )
    )

    respuesta.headers["Cache-Control"] = "private, no-store"
    respuesta.headers["X-Content-Type-Options"] = "nosniff"

    return respuesta



@app.route("/plano_lotizacion/<int:lot_id>/imagen")
@login_required
def ver_plano_lotizacion(lot_id):
    lotizacion = Lotizacion.query.get_or_404(lot_id)

    if current_user.rol != "superadmin":
        lotizacion_activa = session.get("lotizacion_id")

        if str(lotizacion_activa) != str(lotizacion.id):
            return "No tienes acceso a este plano.", 403

        if not usuario_puede_acceder_lotizacion(
            current_user,
            lotizacion.id
        ):
            return "No tienes permiso para ver este plano.", 403

    if not lotizacion.plano_imagen:
        return "Esta lotización todavía no tiene un plano.", 404

    respuesta = send_file(
        io.BytesIO(lotizacion.plano_imagen),
        mimetype="image/png",
        download_name=(
            lotizacion.plano_nombre
            or f"plano_lotizacion_{lotizacion.id}.png"
        )
    )

    respuesta.headers["Cache-Control"] = "private, no-store"
    respuesta.headers["X-Content-Type-Options"] = "nosniff"

    return respuesta

@app.route(
    "/superadmin/guardar_plano_config/<int:lot_id>",
    methods=["POST"]
)
@login_required
@superadmin_required
def guardar_plano_config(lot_id):
    lotizacion = Lotizacion.query.get_or_404(lot_id)

    if not lotizacion.plano_imagen:
        return jsonify(
            error="Primero debes cargar una imagen del plano."
        ), 400

    datos = request.get_json(silent=True)

    if not isinstance(datos, dict):
        return jsonify(
            error="La configuración recibida no es válida."
        ), 400

    zonas = datos.get("lotes")

    if not isinstance(zonas, dict):
        return jsonify(
            error="No se recibieron las zonas de los lotes."
        ), 400

    lotes_validos = {
        str(lote.id)
        for lote in Lote.query.filter_by(
            lotizacion_id=lotizacion.id
        ).all()
    }

    configuracion = {}

    try:
        for lote_id, puntos in zonas.items():
            lote_id = str(lote_id)

            if lote_id not in lotes_validos:
                return jsonify(
                    error=f"El lote {lote_id} no pertenece a esta lotización."
                ), 400

            # Una lista vacía permite quitar la zona del lote
            if puntos == []:
                continue

            if not isinstance(puntos, list):
                return jsonify(
                    error=f"La zona del lote {lote_id} no es válida."
                ), 400

            if len(puntos) < 3:
                return jsonify(
                    error=f"La zona del lote {lote_id} debe tener al menos 3 puntos."
                ), 400

            if len(puntos) > 80:
                return jsonify(
                    error=f"La zona del lote {lote_id} tiene demasiados puntos."
                ), 400

            puntos_validos = []

            for punto in puntos:
                if not isinstance(punto, dict):
                    return jsonify(
                        error=f"Hay un punto inválido en el lote {lote_id}."
                    ), 400

                x = float(punto.get("x"))
                y = float(punto.get("y"))

                if not (0 <= x <= 1 and 0 <= y <= 1):
                    return jsonify(
                        error=(
                            f"Las coordenadas del lote {lote_id} "
                            "deben estar entre 0 y 1."
                        )
                    ), 400

                puntos_validos.append({
                    "x": round(x, 6),
                    "y": round(y, 6)
                })

            configuracion[lote_id] = puntos_validos

    except (TypeError, ValueError):
        return jsonify(
            error="Una de las coordenadas no es válida."
        ), 400

    try:
        lotizacion.plano_config = {
            "version": 1,
            "lotes": configuracion
        }

        db.session.commit()

        return jsonify(
            ok=True,
            mensaje="Configuración del plano guardada correctamente."
        )

    except Exception:
        db.session.rollback()

        current_app.logger.exception(
            "Error al guardar la configuración del plano %s",
            lot_id
        )

        return jsonify(
            error="No se pudo guardar la configuración del plano."
        ), 500
 
 
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
from models import Documento, ProyectoArchivo

_DOC_TIPOS = {
    "legal": ("Legal", "⚖️"),
    "contrato": ("Contrato", "📝"),
    "escritura": ("Escritura", "📜"),
    "dni": ("DNI", "🪪"),
    "plano": ("Plano", "📐"),
    "memoria": ("Memoria descriptiva", "📑"),
    "acta": ("Acta", "📋"),
    "otro": ("Otro", "📄")
}

_DOC_EXTENSIONES = {
    ".pdf", ".jpg", ".jpeg", ".png", ".webp", ".gif",
    ".tif", ".tiff", ".heic", ".doc", ".docx", ".xls",
    ".xlsx", ".csv", ".ppt", ".pptx", ".odt", ".ods",
    ".txt", ".zip", ".dwg", ".dxf"
}


def _docs_token():
    if not session.get("csrf_documentos"):
        session["csrf_documentos"] = secrets.token_urlsafe(32)
    return session["csrf_documentos"]


@app.context_processor
def _docs_contexto():
    return {
        "documentos_token": _docs_token,
        "documentos_tipos": _DOC_TIPOS
    }


def _docs_verificar_token():
    recibido = request.form.get("token_documentos", "")
    esperado = session.get("csrf_documentos", "")

    if not esperado or not secrets.compare_digest(recibido, esperado):
        raise ValueError(
            "El formulario venció. Recarga la página e inténtalo nuevamente."
        )


def _docs_respuesta(destino, error=None, mensaje=None):
    if error:
        if request.headers.get("X-Documentos") == "1":
            return jsonify(error=error), 400
        flash(error, "warning")
    elif mensaje:
        flash(mensaje, "success")

    if request.headers.get("X-Documentos") == "1":
        return jsonify(url=destino)

    return redirect(destino)


def _docs_id(valor):
    if not valor:
        return None

    try:
        numero = int(valor)
        if numero <= 0:
            raise ValueError()
        return numero
    except (TypeError, ValueError):
        raise ValueError("La selección recibida no es válida.")


def _docs_cliente_vinculado(cliente_id, lote_id):
    return (
        Compra.query.filter_by(
            cliente_id=cliente_id, lote_id=lote_id
        ).first()
        or Separacion.query.filter_by(
            cliente_id=cliente_id, lote_id=lote_id
        ).first()
        or Historial.query.filter_by(
            cliente_id=cliente_id, lote_id=lote_id
        ).first()
    ) is not None


def _docs_destino(doc):
    if doc.proyecto_archivo_id:
        return url_for(
            "documentos_archivo",
            proyecto_id=doc.proyecto_archivo_id
        )

    if doc.lotizacion_id:
        return url_for(
            "documentos_lotizacion",
            lotizacion_id=doc.lotizacion_id
        )

    return url_for("documentos_empresa")


def _docs_permitido(doc):
    if current_user.rol not in ("admin", "superadmin"):
        return False

    if doc.proyecto_archivo_id:
        return (
            not doc.lotizacion_id
            and not doc.lote_id
            and not doc.cliente_id
        )

    if doc.lotizacion_id:
        return usuario_puede_acceder_lotizacion(
            current_user, doc.lotizacion_id
        )

    return doc.lote_id is None


def _docs_ruta(doc):
    from werkzeug.utils import safe_join

    ruta = (doc.ruta or "").replace("\\", "/")

    if ruta.startswith("privado_documentos/"):
        base = os.path.join(app.instance_path, "documentos")
        relativa = ruta[len("privado_documentos/"):]
    else:
        base = app.static_folder
        relativa = (
            ruta[len("static/"):]
            if ruta.startswith("static/")
            else ruta
        )

    destino = safe_join(base, relativa)

    if destino is None:
        raise ValueError("La ruta del archivo no es válida.")

    base_real = os.path.realpath(base)
    destino_real = os.path.realpath(destino)

    if os.path.commonpath([base_real, destino_real]) != base_real:
        raise ValueError("La ruta del archivo no es válida.")

    return destino_real


def _docs_guardar_archivo(archivo):
    extension = os.path.splitext(archivo.filename or "")[1].lower()

    if extension not in _DOC_EXTENSIONES:
        raise ValueError(
            "Este formato no está permitido. "
            "Usa un documento, imagen o plano."
        )

    nombre = uuid.uuid4().hex + extension
    base = os.path.join(app.instance_path, "documentos")
    os.makedirs(base, exist_ok=True)
    destino = os.path.join(base, nombre)

    try:
        total = 0

        with open(destino, "xb") as salida:
            while True:
                bloque = archivo.stream.read(1024 * 1024)

                if not bloque:
                    break

                total += len(bloque)

                if total > 20 * 1024 * 1024:
                    raise ValueError(
                        "El archivo supera el límite de 20 MB."
                    )

                salida.write(bloque)

        if total == 0:
            raise ValueError("El archivo está vacío.")

    except Exception:
        if os.path.isfile(destino):
            os.remove(destino)
        raise

    return "privado_documentos/" + nombre, destino


def _docs_pagina(
    ambito, titulo, consulta, destino,
    lotizacion=None, proyecto=None
):
    busqueda = request.args.get(
        "q",
        request.args.get(
            "nombre", request.args.get("apellido", "")
        )
    ).strip()[:200]

    tipo = request.args.get("tipo", "").strip()
    manzana = request.args.get("manzana", "").strip()
    total = consulta.count()

    if tipo:
        consulta = consulta.filter(Documento.tipo == tipo)

    if busqueda:
        coincidencia_cliente = Documento.cliente.has(or_(
            Cliente.nombre.ilike(f"%{busqueda}%"),
            Cliente.apellidos.ilike(f"%{busqueda}%"),
            Cliente.dni.ilike(f"%{busqueda}%")
        ))

        consulta = consulta.filter(or_(
            Documento.nombre.ilike(f"%{busqueda}%"),
            Documento.nombre_cliente_libre.ilike(f"%{busqueda}%"),
            coincidencia_cliente
        ))

    if lotizacion and manzana:
        consulta = consulta.filter(
            Documento.lote.has(Lote.manzana == manzana)
        )

    paginacion = consulta.order_by(
        Documento.fecha_subida.desc(),
        Documento.id.desc()
    ).paginate(
        page=max(request.args.get("page", 1, type=int) or 1, 1),
        per_page=20,
        error_out=False
    )

    lotes_json = {}

    if lotizacion:
        lotes = Lote.query.filter_by(
            lotizacion_id=lotizacion.id
        ).all()

        lotes.sort(key=lambda l: (
            l.manzana,
            (
                (0, int(l.numero))
                if str(l.numero).isdigit()
                else (1, str(l.numero))
            ),
            l.id
        ))

        for lote in lotes:
            lotes_json.setdefault(lote.manzana, []).append({
                "id": lote.id,
                "numero": lote.numero
            })

    tipos = {
        k: v for k, v in _DOC_TIPOS.items()
        if ambito != "empresa" or k in ("legal", "plano", "otro")
    }

    plantilla = (
        "documentos_lotizacion.html"
        if lotizacion
        else "documentos_empresa.html"
    )

    return render_template(
        plantilla,
        ambito=ambito,
        titulo=titulo,
        destino=destino,
        total=total,
        busqueda=busqueda,
        tipo=tipo,
        manzana_sel=manzana,
        tipos=tipos,
        docs=paginacion.items,
        paginacion=paginacion,
        proyecto_lotizacion=lotizacion,
        proyecto=proyecto,
        lotes_json=lotes_json
    )


@app.route("/documentos")
@login_required
@admin_required
def documentos():
    lotizaciones = lotizaciones_permitidas_para(current_user)
    archivos = ProyectoArchivo.query.order_by(
        ProyectoArchivo.nombre
    ).all()

    conteos_lotes = dict(
        db.session.query(
            Documento.lotizacion_id,
            func.count(Documento.id)
        ).filter(
            Documento.lotizacion_id.in_([
                l.id for l in lotizaciones
            ]),
            Documento.proyecto_archivo_id.is_(None)
        ).group_by(Documento.lotizacion_id).all()
    )

    conteos_archivos = dict(
        db.session.query(
            Documento.proyecto_archivo_id,
            func.count(Documento.id)
        ).filter(
            Documento.proyecto_archivo_id.is_not(None),
            Documento.lotizacion_id.is_(None)
        ).group_by(Documento.proyecto_archivo_id).all()
    )

    total_empresa = Documento.query.filter_by(
        lotizacion_id=None,
        lote_id=None,
        proyecto_archivo_id=None
    ).count()

    busqueda = request.args.get("q", "").strip()[:150]

    if busqueda:
        lotizaciones = [
            l for l in lotizaciones
            if busqueda.casefold() in l.nombre.casefold()
        ]
        archivos = [
            p for p in archivos
            if busqueda.casefold() in p.nombre.casefold()
        ]

    return render_template(
        "documentos.html",
        lotizaciones=lotizaciones,
        archivos=archivos,
        conteos_lotes=conteos_lotes,
        conteos_archivos=conteos_archivos,
        total_empresa=total_empresa,
        total_documentos=(
            total_empresa
            + sum(conteos_lotes.values())
            + sum(conteos_archivos.values())
        ),
        busqueda=busqueda
    )


@app.route("/documentos/empresa")
@login_required
@admin_required
def documentos_empresa():
    consulta = Documento.query.filter_by(
        lotizacion_id=None,
        lote_id=None,
        proyecto_archivo_id=None
    )

    return _docs_pagina(
        "empresa",
        "Documentos de la empresa",
        consulta,
        url_for("documentos_empresa")
    )


@app.route("/documentos/lotizacion/<int:lotizacion_id>")
@login_required
@admin_required
def documentos_lotizacion(lotizacion_id):
    if not usuario_puede_acceder_lotizacion(
        current_user, lotizacion_id
    ):
        flash("No tienes acceso a esa lotización.", "warning")
        return redirect(url_for("documentos"))

    lotizacion = Lotizacion.query.get_or_404(lotizacion_id)

    consulta = Documento.query.filter_by(
        lotizacion_id=lotizacion.id,
        proyecto_archivo_id=None
    )

    return _docs_pagina(
        "lotizacion",
        lotizacion.nombre,
        consulta,
        url_for(
            "documentos_lotizacion",
            lotizacion_id=lotizacion.id
        ),
        lotizacion=lotizacion
    )


@app.route("/documentos/archivo/<int:proyecto_id>")
@login_required
@admin_required
def documentos_archivo(proyecto_id):
    proyecto = ProyectoArchivo.query.get_or_404(proyecto_id)

    consulta = Documento.query.filter_by(
        proyecto_archivo_id=proyecto.id,
        lotizacion_id=None,
        lote_id=None,
        cliente_id=None
    )

    return _docs_pagina(
        "archivo",
        proyecto.nombre,
        consulta,
        url_for("documentos_archivo", proyecto_id=proyecto.id),
        proyecto=proyecto
    )


@app.route("/documentos/archivo/crear", methods=["POST"])
@login_required
@admin_required
def crear_proyecto_archivo():
    destino = url_for("documentos")

    try:
        _docs_verificar_token()

        nombre = request.form.get("nombre", "").strip()
        ubicacion = request.form.get("ubicacion", "").strip()
        descripcion = request.form.get("descripcion", "").strip()

        if (
            not nombre
            or len(nombre) > 150
            or len(ubicacion) > 200
            or len(descripcion) > 2000
        ):
            raise ValueError(
                "Completa el nombre (hasta 150 caracteres) "
                "y revisa la ubicación y descripción."
            )

        if ProyectoArchivo.query.filter(
            func.lower(ProyectoArchivo.nombre) == nombre.lower()
        ).first():
            raise ValueError(
                "Ya existe un proyecto antiguo con ese nombre."
            )

        proyecto = ProyectoArchivo(
            nombre=nombre,
            ubicacion=ubicacion or None,
            descripcion=descripcion or None,
            usuario_id=current_user.id
        )

        db.session.add(proyecto)
        db.session.flush()

        destino = url_for(
            "documentos_archivo", proyecto_id=proyecto.id
        )

        db.session.commit()

        return _docs_respuesta(
            destino,
            mensaje=(
                "Proyecto antiguo registrado. "
                "Ya puedes guardar sus documentos."
            )
        )

    except ValueError as error:
        db.session.rollback()
        return _docs_respuesta(
            url_for("documentos"), error=str(error)
        )

    except Exception:
        db.session.rollback()
        current_app.logger.exception(
            "Error al crear un proyecto de archivo."
        )
        return _docs_respuesta(
            url_for("documentos"),
            error=(
                "No se pudo crear el proyecto. "
                "Revisa si ya existe e inténtalo nuevamente."
            )
        )


@app.route(
    "/documentos/archivo/<int:proyecto_id>/editar",
    methods=["POST"]
)
@login_required
@admin_required
def editar_proyecto_archivo(proyecto_id):
    proyecto = ProyectoArchivo.query.get_or_404(proyecto_id)
    destino = url_for(
        "documentos_archivo", proyecto_id=proyecto.id
    )

    try:
        _docs_verificar_token()

        nombre = request.form.get("nombre", "").strip()
        ubicacion = request.form.get("ubicacion", "").strip()
        descripcion = request.form.get("descripcion", "").strip()

        if (
            not nombre
            or len(nombre) > 150
            or len(ubicacion) > 200
            or len(descripcion) > 2000
        ):
            raise ValueError(
                "Revisa el nombre, ubicación y descripción del proyecto."
            )

        if ProyectoArchivo.query.filter(
            ProyectoArchivo.id != proyecto.id,
            func.lower(ProyectoArchivo.nombre) == nombre.lower()
        ).first():
            raise ValueError(
                "Ya existe otro proyecto antiguo con ese nombre."
            )

        proyecto.nombre = nombre
        proyecto.ubicacion = ubicacion or None
        proyecto.descripcion = descripcion or None

        db.session.commit()

        return _docs_respuesta(
            destino, mensaje="Datos del proyecto actualizados."
        )

    except ValueError as error:
        db.session.rollback()
        return _docs_respuesta(destino, error=str(error))

    except Exception:
        db.session.rollback()
        current_app.logger.exception(
            "Error al editar proyecto antiguo %s", proyecto_id
        )
        return _docs_respuesta(
            destino,
            error="No se pudieron guardar los datos del proyecto."
        )


@app.route("/documentos/clientes/<int:lotizacion_id>")
@login_required
@admin_required
def documentos_buscar_clientes(lotizacion_id):
    if not usuario_puede_acceder_lotizacion(
        current_user, lotizacion_id
    ):
        return jsonify(
            error="No tienes acceso a este proyecto."
        ), 403

    try:
        lote_id = _docs_id(request.args.get("lote_id"))

        lote = Lote.query.filter_by(
            id=lote_id,
            lotizacion_id=lotizacion_id
        ).first()

        if lote is None:
            raise ValueError(
                "Selecciona primero un lote de este proyecto."
            )

    except ValueError as error:
        return jsonify(error=str(error)), 400

    q = request.args.get("q", "").strip()[:100]

    if len(q) < 2:
        return jsonify([])

    clientes = Cliente.query.filter(
        or_(
            Cliente.nombre.ilike(f"%{q}%"),
            Cliente.apellidos.ilike(f"%{q}%"),
            Cliente.dni.ilike(f"%{q}%")
        ),
        or_(
            db.session.query(Compra.id).filter(
                Compra.cliente_id == Cliente.id,
                Compra.lote_id == lote.id
            ).exists(),
            db.session.query(Separacion.id).filter(
                Separacion.cliente_id == Cliente.id,
                Separacion.lote_id == lote.id
            ).exists(),
            db.session.query(Historial.id).filter(
                Historial.cliente_id == Cliente.id,
                Historial.lote_id == lote.id
            ).exists()
        )
    ).order_by(
        Cliente.apellidos, Cliente.nombre
    ).limit(10).all()

    return jsonify([
        {
            "id": c.id,
            "nombre": c.nombre,
            "apellidos": c.apellidos,
            "dni": c.dni
        }
        for c in clientes
    ])


@app.route("/documentos/subir", methods=["POST"])
@login_required
@admin_required
def subir_documento():
    destino = url_for("documentos")
    archivo_creado = None

    try:
        _docs_verificar_token()

        lotizacion_id = _docs_id(
            request.form.get("lotizacion_id")
        )
        proyecto_id = _docs_id(
            request.form.get("proyecto_archivo_id")
        )
        lote_id = _docs_id(request.form.get("lote_id"))
        cliente_id = _docs_id(request.form.get("cliente_id"))
        nombre_libre = request.form.get(
            "nombre_cliente_libre", ""
        ).strip()

        if lotizacion_id and proyecto_id:
            raise ValueError(
                "Elige una sola sección para el documento."
            )

        if lotizacion_id:
            if not usuario_puede_acceder_lotizacion(
                current_user, lotizacion_id
            ):
                raise ValueError(
                    "No tienes acceso al proyecto elegido."
                )

            destino = url_for(
                "documentos_lotizacion",
                lotizacion_id=lotizacion_id
            )

        elif proyecto_id:
            if db.session.get(ProyectoArchivo, proyecto_id) is None:
                raise ValueError("El proyecto antiguo no existe.")

            destino = url_for(
                "documentos_archivo", proyecto_id=proyecto_id
            )

        else:
            destino = url_for("documentos_empresa")

        asociacion = request.form.get("asociacion", "general")

        if asociacion not in ("general", "lote", "cliente", "libre"):
            raise ValueError(
                "Elige una asociación válida para el documento."
            )

        if (
            (proyecto_id or not lotizacion_id)
            and (
                asociacion != "general"
                or lote_id
                or cliente_id
                or nombre_libre
            )
        ):
            raise ValueError(
                "Esta sección guarda documentos generales, "
                "sin asociarlos a lotes o clientes."
            )

        if asociacion == "general" and (
            lote_id or cliente_id or nombre_libre
        ):
            raise ValueError(
                "Revisa la asociación: un documento general "
                "no debe tener lote ni cliente."
            )

        if asociacion in ("lote", "cliente"):
            lote = Lote.query.filter_by(
                id=lote_id,
                lotizacion_id=lotizacion_id
            ).first()

            if lote is None:
                raise ValueError(
                    "El lote no pertenece al proyecto elegido."
                )

            if nombre_libre or (
                asociacion == "lote" and cliente_id
            ):
                raise ValueError(
                    "Revisa los datos de asociación del documento."
                )

            if asociacion == "cliente" and (
                not cliente_id
                or not _docs_cliente_vinculado(cliente_id, lote.id)
            ):
                raise ValueError(
                    "Selecciona un cliente que tenga una operación "
                    "o historial en este lote."
                )

        elif asociacion == "libre":
            if (
                lote_id
                or cliente_id
                or not nombre_libre
                or len(nombre_libre) > 200
            ):
                raise ValueError(
                    "Escribe un nombre libre de hasta 200 caracteres, "
                    "sin seleccionar lote o cliente."
                )

        tipo = request.form.get("tipo", "otro")

        permitidos = (
            ("legal", "plano", "otro")
            if not lotizacion_id and not proyecto_id
            else _DOC_TIPOS
        )

        if tipo not in permitidos:
            raise ValueError(
                "El tipo de documento no corresponde a esta sección."
            )

        archivo = request.files.get("archivo")

        if not archivo or not archivo.filename:
            raise ValueError(
                "Selecciona el archivo que deseas subir."
            )

        nombre = (
            request.form.get("nombre_personalizado", "").strip()
            or os.path.basename(
                archivo.filename.replace("\\", "/")
            )
        )

        if len(nombre) > 200 or any(
            c in nombre for c in ("\n", "\r", "\x00")
        ):
            raise ValueError(
                "El nombre del documento debe tener hasta 200 caracteres."
            )

        ruta, archivo_creado = _docs_guardar_archivo(archivo)

        doc = Documento(
            nombre=nombre,
            ruta=ruta,
            tipo=tipo,
            lotizacion_id=lotizacion_id,
            proyecto_archivo_id=proyecto_id,
            lote_id=lote_id,
            cliente_id=cliente_id,
            nombre_cliente_libre=nombre_libre or None,
            usuario_id=current_user.id
        )

        db.session.add(doc)
        db.session.commit()

        return _docs_respuesta(
            destino, mensaje="Documento guardado correctamente."
        )

    except ValueError as error:
        db.session.rollback()

        if archivo_creado and os.path.isfile(archivo_creado):
            os.remove(archivo_creado)

        return _docs_respuesta(destino, error=str(error))

    except Exception:
        db.session.rollback()

        if archivo_creado and os.path.isfile(archivo_creado):
            os.remove(archivo_creado)

        current_app.logger.exception(
            "Error al subir un documento."
        )

        return _docs_respuesta(
            destino,
            error=(
                "No se pudo guardar el documento. "
                "Comprueba si aparece en la lista antes de reintentar."
            )
        )


@app.route("/documentos/archivo_documento/<int:doc_id>")
@login_required
@admin_required
def abrir_documento(doc_id):
    from flask import abort

    doc = Documento.query.get_or_404(doc_id)

    if not _docs_permitido(doc):
        abort(403)

    try:
        ruta = _docs_ruta(doc)
    except ValueError:
        abort(404)

    if not os.path.isfile(ruta):
        abort(404)

    extension = os.path.splitext(ruta)[1].lower()
    nombre = secure_filename(doc.nombre) or "documento"

    if not nombre.lower().endswith(extension):
        nombre += extension

    descargar = (
        request.args.get("descargar") == "1"
        or extension not in (
            ".pdf", ".jpg", ".jpeg", ".png",
            ".webp", ".gif", ".txt"
        )
    )

    respuesta = send_file(
        ruta,
        as_attachment=descargar,
        download_name=nombre,
        conditional=True
    )

    respuesta.headers["Cache-Control"] = "private, no-store"
    respuesta.headers["X-Content-Type-Options"] = "nosniff"

    return respuesta


@app.route("/documentos/eliminar/<int:doc_id>", methods=["POST"])
@login_required
@superadmin_required
def eliminar_documento_drive(doc_id):
    doc = (
        Documento.query
        .filter_by(id=doc_id)
        .populate_existing()
        .with_for_update()
        .first_or_404()
    )

    destino = _docs_destino(doc)
    original = None
    temporal = None

    try:
        _docs_verificar_token()

        if not _docs_permitido(doc):
            raise ValueError(
                "No tienes acceso a este documento."
            )

        original = _docs_ruta(doc)

        # Conservar archivos utilizados por otros documentos.
        variantes = {
            doc.ruta,
            doc.ruta.replace("\\", "/")
        }

        if not doc.ruta.startswith("privado_documentos/"):
            relativa = (
                doc.ruta.replace("\\", "/")
                .removeprefix("static/")
            )
            variantes.update((
                relativa,
                "static/" + relativa
            ))

        compartido = Documento.query.filter(
            Documento.id != doc.id,
            Documento.ruta.in_(variantes)
        ).first()

        if not compartido and os.path.isfile(original):
            temporal = original + ".eliminar-" + uuid.uuid4().hex
            os.replace(original, temporal)

        db.session.delete(doc)
        db.session.commit()

    except Exception as error:
        db.session.rollback()

        if temporal and os.path.isfile(temporal):
            os.replace(temporal, original)

        if not isinstance(error, ValueError):
            current_app.logger.exception(
                "Error al eliminar documento %s", doc_id
            )

        return _docs_respuesta(
            destino,
            error=(
                str(error)
                if isinstance(error, ValueError)
                else "No se pudo eliminar el documento."
            )
        )

    if temporal:
        try:
            os.remove(temporal)
        except OSError:
            current_app.logger.exception(
                "No se pudo retirar el archivo eliminado %s",
                doc_id
            )

    return _docs_respuesta(
        destino, mensaje="Documento eliminado."
    )

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

def _detectar_lotes_pdf(contenido):
    """Detector vectorial probado con el plano de Morerilla; no crea lotes."""
    import math
    from collections import defaultdict
    import fitz

    def resta(a, b):
        return a[0] - b[0], a[1] - b[1]

    def cruz(a, b):
        return a[0] * b[1] - a[1] * b[0]

    def dentro(p, poligono):
        resultado = False
        for a, b in zip(poligono, poligono[1:] + poligono[:1]):
            if (a[1] > p[1]) != (b[1] > p[1]):
                limite = (b[0]-a[0])*(p[1]-a[1])/(b[1]-a[1])+a[0]
                if p[0] < limite:
                    resultado = not resultado
        return resultado

    if not contenido or len(contenido) > 10 * 1024 * 1024:
        raise ValueError("Carga un PDF de hasta 10 MB.")

    with fitz.open(stream=contenido, filetype="pdf") as documento:
        if documento.needs_pass or len(documento) != 1:
            raise ValueError(
                "El PDF debe tener una página y no tener contraseña."
            )

        pagina = documento[0]

        if pagina.rotation or pagina.cropbox != pagina.mediabox:
            raise ValueError(
                "Exporta el plano sin rotación ni recorte de página."
            )

        ancho, alto = pagina.rect.width, pagina.rect.height
        palabras = pagina.get_text("words")

        if not palabras or len(palabras) > 20000:
            raise ValueError(
                "No se puede identificar la numeración de este PDF."
            )

        segmentos = []

        for dibujo in pagina.get_drawings():
            if dibujo.get("color") != (0, 0, 0):
                continue

            cadena = []

            for elemento in dibujo["items"]:
                tipo = elemento[0]

                if tipo == "l":
                    cadena.append((
                        tuple(elemento[1]),
                        tuple(elemento[2])
                    ))

                elif tipo in ("qu", "re"):
                    figura = elemento[1]

                    vertices = (
                        [figura.ul, figura.ur, figura.lr, figura.ll]
                        if tipo == "qu"
                        else [figura.tl, figura.tr, figura.br, figura.bl]
                    )

                    vertices = [tuple(p) for p in vertices]
                    cadena.extend(
                        zip(vertices, vertices[1:] + vertices[:1])
                    )

            if dibujo.get("closePath") and cadena:
                cadena.append((cadena[-1][1], cadena[0][0]))

            segmentos.extend(
                (a, b) for a, b in cadena if math.dist(a, b) > 2
            )

        if not segmentos or len(segmentos) > 1000:
            raise ValueError(
                "Este PDF necesita otro método de detección; "
                "no se cambió nada."
            )

    # Unir intersecciones y extremos casi coincidentes del dibujo CAD.
    tolerancia = 0.4
    cortes = [[(0, a), (1, b)] for a, b in segmentos]

    for i, (a, b) in enumerate(segmentos):
        r = resta(b, a)
        largo_r = math.dist(a, b)

        for j in range(i + 1, len(segmentos)):
            c, d = segmentos[j]
            s = resta(d, c)
            largo_s = math.dist(c, d)

            for punto in (a, b):
                u = (
                    (punto[0]-c[0])*s[0] +
                    (punto[1]-c[1])*s[1]
                ) / largo_s**2

                proyeccion = (c[0]+u*s[0], c[1]+u*s[1])

                if (
                    0 <= u <= 1
                    and math.dist(proyeccion, punto) <= tolerancia
                ):
                    cortes[j].append((u, punto))

            for punto in (c, d):
                t = (
                    (punto[0]-a[0])*r[0] +
                    (punto[1]-a[1])*r[1]
                ) / largo_r**2

                proyeccion = (a[0]+t*r[0], a[1]+t*r[1])

                if (
                    0 <= t <= 1
                    and math.dist(proyeccion, punto) <= tolerancia
                ):
                    cortes[i].append((t, punto))

            denominador = cruz(r, s)

            if abs(denominador) < 0.0000001:
                continue

            t = cruz(resta(c, a), s) / denominador
            u = cruz(resta(c, a), r) / denominador

            if (
                -tolerancia/largo_r <= t <= 1+tolerancia/largo_r
                and -tolerancia/largo_s <= u <= 1+tolerancia/largo_s
            ):
                punto = (a[0]+t*r[0], a[1]+t*r[1])
                cortes[i].append((t, punto))
                cortes[j].append((u, punto))

    puntos = []
    celdas = defaultdict(list)
    grafo = defaultdict(set)

    def nodo(punto):
        clave = tuple(math.floor(v / tolerancia) for v in punto)

        for x in range(clave[0]-1, clave[0]+2):
            for y in range(clave[1]-1, clave[1]+2):
                for n in celdas[(x, y)]:
                    if math.dist(puntos[n], punto) <= tolerancia:
                        return n

        n = len(puntos)
        puntos.append(punto)
        celdas[clave].append(n)
        return n

    for linea in cortes:
        nodos = [nodo(p) for _, p in sorted(linea)]

        for a, b in zip(nodos, nodos[1:]):
            if a != b:
                grafo[a].add(b)
                grafo[b].add(a)

    grupos = {}

    for n in grafo:
        if n in grupos:
            continue

        pendientes = [n]
        grupos[n] = n

        while pendientes:
            actual = pendientes.pop()

            for vecino in grafo[actual]:
                if vecino not in grupos:
                    grupos[vecino] = n
                    pendientes.append(vecino)

    orden = {
        n: sorted(
            vecinos,
            key=lambda v: math.atan2(
                puntos[v][1]-puntos[n][1],
                puntos[v][0]-puntos[n][0]
            )
        )
        for n, vecinos in grafo.items()
    }

    visitados = set()
    figuras = defaultdict(list)

    for a in grafo:
        for b in grafo[a]:
            inicio = (a, b)

            if inicio in visitados:
                continue

            arista = inicio
            recorrido = []

            while arista not in visitados:
                visitados.add(arista)
                u, v = arista
                recorrido.append(u)

                vecinos = orden[v]
                siguiente = vecinos[
                    (vecinos.index(u)-1) % len(vecinos)
                ]

                arista = (v, siguiente)

            if (
                arista != inicio
                or len(recorrido) != len(set(recorrido))
            ):
                continue

            poligono = [puntos[n] for n in recorrido]

            area = sum(
                cruz(x, y)
                for x, y in zip(
                    poligono,
                    poligono[1:] + poligono[:1]
                )
            ) / 2

            if not 1000 < area < 100000:
                continue

            texto = [
                w[4] for w in palabras
                if dentro(
                    ((w[0]+w[2])/2, (w[1]+w[3])/2),
                    poligono
                )
            ]

            figuras[grupos[a]].append((poligono, texto))

    resultado = {}

    for grupo in figuras.values():
        texto_grupo = [
            w for _, texto in grupo for w in texto
        ]

        letras = {
            w for w in texto_grupo
            if len(w) == 1 and w in "ABCDEFGHIJKLMNOPQRSTUVWXYZ"
        }

        if len(letras) != 1 or "MANZANA" in texto_grupo:
            continue

        manzana = next(iter(letras))

        for poligono, texto in grupo:
            usos_especiales = {
                "PARQUE", "EDUCACION", "EDUCACIÓN",
                "OTROS", "RECREACIÓN"
            }

            if usos_especiales.intersection(texto):
                continue

            numeros = [
                w for w in texto
                if w.isascii() and w.isdigit()
            ]

            if len(numeros) != 1:
                continue

            clave = (manzana, str(int(numeros[0])))

            if clave in resultado:
                raise ValueError(
                    "Se detectó una numeración repetida. "
                    "No se guardó nada."
                )

            if not all(
                0 <= x <= ancho and 0 <= y <= alto
                for x, y in poligono
            ):
                raise ValueError(
                    "Se detectaron coordenadas fuera de la página."
                )

            resultado[clave] = [
                {
                    "x": round(x/ancho, 6),
                    "y": round(y/alto, 6)
                }
                for x, y in poligono
            ]

    if not resultado:
        raise ValueError(
            "No se identificaron lotes con seguridad. "
            "No se cambió nada."
        )

    return resultado


@app.template_global()
def token_plano_auto():
    if not session.get("plano_auto_csrf"):
        session["plano_auto_csrf"] = secrets.token_urlsafe(32)

    return session["plano_auto_csrf"]


@app.route(
    "/superadmin/procesar_plano/<int:lot_id>",
    methods=["POST"]
)
@login_required
@superadmin_required
def procesar_plano_automatico(lot_id):
    esperado = session.get("plano_auto_csrf", "")
    recibido = request.form.get("plano_auto_csrf", "")

    if not esperado or not secrets.compare_digest(
        esperado.encode(),
        recibido.encode()
    ):
        return "Recarga el panel antes de procesar el plano.", 403

    lot = Lotizacion.query.get_or_404(lot_id)

    try:
        if not lot.plano_pdf or not lot.plano_imagen:
            raise ValueError(
                "Primero vuelve a cargar el PDF original del plano."
            )

        original = bytes(lot.plano_pdf)
        detectados = _detectar_lotes_pdf(original)

        # Releer al guardar para no sobrescribir un PDF recién cambiado.
        lot = (
            Lotizacion.query
            .filter_by(id=lot_id)
            .populate_existing()
            .with_for_update()
            .first()
        )

        if lot is None or bytes(lot.plano_pdf or b"") != original:
            raise ValueError(
                "El plano cambió durante el proceso. "
                "Inténtalo nuevamente."
            )

        zonas = {}
        faltantes = []
        claves = set()

        for lote in Lote.query.filter_by(
            lotizacion_id=lot_id
        ).all():
            numero = str(lote.numero).strip()

            if numero.isascii() and numero.isdigit():
                numero = str(int(numero))

            clave = (
                str(lote.manzana).strip().upper(),
                numero
            )

            if clave in claves:
                raise ValueError(
                    "Hay lotes con numeración equivalente, como 1 y 01."
                )

            claves.add(clave)

            if clave in detectados:
                zonas[str(lote.id)] = detectados[clave]
            else:
                faltantes.append(f"{clave[0]}-{numero}")

        if not zonas:
            raise ValueError(
                "Ningún lote registrado coincide con el PDF. "
                "No se cambió nada."
            )

        lot.plano_config = {
            "version": 1,
            "lotes": zonas,
            "origen": "pdf_automatico",
            "pdf_sha256": hashlib.sha256(original).hexdigest()
        }

        db.session.commit()

        flash(
            f"Plano procesado: {len(zonas)} lotes asociados "
            "automáticamente. Se reemplazaron las zonas anteriores; "
            "no se modificaron ventas ni lotes.",
            "success"
        )

        if faltantes:
            detalle = ", ".join(faltantes[:20])

            if len(faltantes) > 20:
                detalle += ", …"

            flash(
                f"Sin coincidencia en el PDF "
                f"({len(faltantes)}): {detalle}.",
                "warning"
            )

    except ValueError as error:
        db.session.rollback()
        flash(str(error), "warning")

    except ImportError:
        db.session.rollback()
        flash(
            "Falta instalar PyMuPDF en el entorno activo.",
            "danger"
        )

    except Exception:
        db.session.rollback()

        current_app.logger.exception(
            "Error al procesar el plano %s",
            lot_id
        )

        flash(
            "No se pudo procesar el PDF. "
            "Las zonas anteriores se conservaron.",
            "danger"
        )

    return redirect(url_for("panel_superadmin"))

# ------------------- CAMBIO DE TITULAR -------------------

def _copia_para_transferencia(registro):
    """Guardar los datos tal como estaban al realizar la transferencia."""
    datos = {}

    for columna in registro.__table__.columns:
        valor = getattr(registro, columna.key)

        if isinstance(valor, (datetime, date)):
            valor = valor.isoformat()

        datos[columna.key] = valor

    return datos


def _separacion_original_compra(compra):
    """Conservar el origen de la separación aunque cambie el titular."""
    primera = (
        TransferenciaTitular.query
        .filter_by(compra_id=compra.id)
        .order_by(
            TransferenciaTitular.fecha,
            TransferenciaTitular.id
        )
        .first()
    )

    if primera:
        datos = (
            primera.datos_compra or {}
        ).get("separacion_convertida")

        if datos:
            return Separacion.query.filter_by(
                id=datos["id"],
                lote_id=compra.lote_id,
                cliente_id=datos["cliente_id"],
                activa=False
            ).first()

        return None

    evento = (
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

    if not evento or evento.tipo != "Separación convertida":
        return None

    return (
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


def _pagina_cambio_titular(compra, formulario):
    lote = compra.lote

    transferencias = (
        TransferenciaTitular.query
        .filter_by(lote_id=lote.id)
        .order_by(
            TransferenciaTitular.fecha.desc(),
            TransferenciaTitular.id.desc()
        )
        .all()
    )

    return render_template(
        "detalle_lote.html",
        lote=lote,
        compra=compra,
        separacion=None,
        lotizacion=db.session.get(
            Lotizacion,
            lote.lotizacion_id
        ),
        error_linderos=None,
        transferencias_titular=transferencias,
        formulario_titular=formulario
    )

@app.route(
    "/cambiar_titular/<int:compra_id>",
    methods=["GET", "POST"]
)
@login_required
@lotizacion_required
@admin_required
def cambiar_titular(compra_id):
    compra = Compra.query.get_or_404(compra_id)
    lote = compra.lote

    if str(session.get("lotizacion_id")) != str(lote.lotizacion_id):
        flash(
            "Selecciona primero el proyecto de esta compra.",
            "warning"
        )
        return redirect(url_for("lotes_disponibles"))

    if compra.anulada or lote.estado != "vendido":
        flash(
            "Solo puedes cambiar el titular de una compra vigente.",
            "warning"
        )
        return redirect(
            url_for("detalle_lote", lote_id=lote.id)
        )

    if not session.get("csrf_cambio_titular"):
        session["csrf_cambio_titular"] = secrets.token_urlsafe(32)

    firmador = URLSafeTimedSerializer(
        app.config["SECRET_KEY"],
        salt="cambio-titular-v1"
    )

    base = {
        "compra_id": compra.id,
        "anterior_id": compra.cliente_id,
        "lote_id": lote.id,
        "usuario_id": current_user.id,
        "csrf": session["csrf_cambio_titular"]
    }

    formulario = {
        "dni": request.form.get("dni", "").strip(),
        "motivo": request.form.get("motivo", "").strip(),
        "error": None,
        "nuevo": None,
        "crear": False,
        "es_nuevo": False,
        "datos_cliente": {
            campo: request.form.get(campo, "")
            for campo in (
                "nombre", "apellidos", "telefono", "correo",
                "estado_civil", "ocupacion", "ciudad",
                "provincia", "departamento", "direccion"
            )
        },
        "token": firmador.dumps({
            **base,
            "paso": "consultar"
        })
    }

    if request.method == "GET":
        return _pagina_cambio_titular(compra, formulario)

    archivos_creados = []
    datos = None

    try:
        datos = firmador.loads(
            request.form.get("token_titular", ""),
            max_age=1800
        )

        if (
            not isinstance(datos, dict)
            or any(datos.get(k) != v for k, v in base.items())
        ):
            raise ValueError(
                "La compra o la sesión cambió. "
                "Consulta nuevamente al titular."
            )

        accion = request.form.get("accion", "")

        # CONSULTAR EL DNI
        if accion == "consultar" and datos.get("paso") == "consultar":
            dni = formulario["dni"]
            motivo = formulario["motivo"]

            if not dni or len(dni) > 20:
                raise ValueError(
                    "Escribe el DNI del nuevo titular."
                )

            if not motivo or len(motivo) > 2000:
                raise ValueError(
                    "Escribe un motivo de hasta 2000 caracteres."
                )

            nuevo = Cliente.query.filter_by(dni=dni).first()

            # Mostrar el registro cuando la persona no existe.
            if nuevo is None:
                formulario["crear"] = True
                formulario["token"] = firmador.dumps({
                    **base,
                    "paso": "registrar",
                    "dni": dni,
                    "motivo": motivo
                })

                return _pagina_cambio_titular(compra, formulario)

            if nuevo.id == compra.cliente_id:
                raise ValueError(
                    "Ese cliente ya es el titular de esta compra."
                )

            formulario["nuevo"] = nuevo
            formulario["token"] = firmador.dumps({
                **base,
                "paso": "confirmar",
                "nuevo_id": nuevo.id,
                "identidad": [
                    nuevo.nombre,
                    nuevo.apellidos,
                    nuevo.dni
                ],
                "motivo": motivo
            })

            return _pagina_cambio_titular(compra, formulario)

        # REVISAR LOS DATOS DE LA PERSONA NUEVA
        if (
            accion == "revisar_nuevo"
            and datos.get("paso") == "registrar"
        ):
            formulario["dni"] = datos["dni"]
            formulario["motivo"] = datos["motivo"]

            if Cliente.query.filter_by(dni=datos["dni"]).first():
                raise ValueError(
                    "Ese DNI ya está registrado. "
                    "Consulta nuevamente para usar su ficha."
                )

            cliente_nuevo = _datos_nuevo_titular(request.form)
            cliente_nuevo["dni"] = datos["dni"]

            formulario["nuevo"] = cliente_nuevo
            formulario["es_nuevo"] = True
            formulario["token"] = firmador.dumps({
                **base,
                "paso": "confirmar",
                "crear": True,
                "cliente": cliente_nuevo,
                "motivo": datos["motivo"]
            })

            return _pagina_cambio_titular(compra, formulario)

        # CONFIRMAR EL CAMBIO
        if (
            accion != "confirmar"
            or datos.get("paso") != "confirmar"
        ):
            raise ValueError(
                "Consulta primero al nuevo titular antes de confirmar."
            )

        if request.form.get("confirmacion") != "si":
            raise ValueError(
                "Marca la casilla para confirmar el cambio de titular."
            )

        lote = (
            Lote.query
            .filter_by(id=datos["lote_id"])
            .populate_existing()
            .with_for_update()
            .first()
        )

        compra = (
            Compra.query
            .filter_by(id=compra_id)
            .populate_existing()
            .with_for_update()
            .first()
        )

        if (
            lote is None
            or compra is None
            or compra.lote_id != datos["lote_id"]
            or compra.cliente_id != datos["anterior_id"]
            or compra.anulada
            or lote.estado != "vendido"
            or str(session.get("lotizacion_id")) != str(lote.lotizacion_id)
        ):
            raise ValueError(
                "La operación cambió. "
                "Recarga la página y revisa su estado."
            )

        # Crear al cliente nuevo dentro de la misma transacción.
        if datos.get("crear"):
            cliente_nuevo = _datos_nuevo_titular(datos["cliente"])
            cliente_nuevo["dni"] = datos["cliente"]["dni"]

            formulario["dni"] = cliente_nuevo["dni"]
            formulario["motivo"] = datos["motivo"]

            if Cliente.query.filter_by(
                dni=cliente_nuevo["dni"]
            ).first():
                raise ValueError(
                    "Ese DNI ya está registrado. "
                    "Consulta nuevamente para usar su ficha."
                )

            anterior = (
                Cliente.query
                .filter_by(id=compra.cliente_id)
                .populate_existing()
                .with_for_update()
                .first()
            )

            if anterior is None:
                raise ValueError(
                    "El titular anterior ya no existe."
                )

            nuevo = Cliente(**cliente_nuevo)
            db.session.add(nuevo)
            db.session.flush()

            nuevo.dni_frontal = _guardar_dni_titular(
                request.files.get("dni_frontal"),
                archivos_creados
            )

            nuevo.dni_reverso = _guardar_dni_titular(
                request.files.get("dni_reverso"),
                archivos_creados
            )

        # Usar la ficha cuando el cliente ya está registrado.
        else:
            ids_clientes = sorted([
                compra.cliente_id,
                datos["nuevo_id"]
            ])

            clientes = (
                Cliente.query
                .filter(Cliente.id.in_(ids_clientes))
                .order_by(Cliente.id)
                .populate_existing()
                .with_for_update()
                .all()
            )

            por_id = {c.id: c for c in clientes}
            anterior = por_id.get(compra.cliente_id)
            nuevo = por_id.get(datos["nuevo_id"])

            if (
                anterior is None
                or nuevo is None
                or anterior.id == nuevo.id
            ):
                raise ValueError(
                    "Los titulares no son válidos. Consulta nuevamente."
                )

            if [
                nuevo.nombre,
                nuevo.apellidos,
                nuevo.dni
            ] != datos["identidad"]:
                raise ValueError(
                    "Los datos del nuevo cliente cambiaron. "
                    "Consulta nuevamente su DNI."
                )

        fecha = hora_local_peru()

        # Conservar la información anterior de la operación.
        copia = _copia_para_transferencia(compra)

        copia["pagos"] = [
            _copia_para_transferencia(p)
            for p in compra.pagos
        ]

        copia["cuotas"] = [
            _copia_para_transferencia(c)
            for c in compra.cuotas
        ]

        copia["usuario_transferencia"] = current_user.username
        copia["lotizacion_id"] = lote.lotizacion_id
        copia["manzana"] = lote.manzana
        copia["numero"] = lote.numero

        separacion = _separacion_original_compra(compra)

        copia["separacion_convertida"] = (
            _copia_para_transferencia(separacion)
            if separacion else None
        )

        transferencia = TransferenciaTitular(
            compra_id=compra.id,
            lote_id=lote.id,
            cliente_anterior_id=anterior.id,
            cliente_nuevo_id=nuevo.id,
            usuario_id=current_user.id,
            fecha=fecha,
            motivo=datos["motivo"],
            datos_cliente_anterior=_copia_para_transferencia(anterior),
            datos_cliente_nuevo=_copia_para_transferencia(nuevo),
            datos_compra=copia
        )

        db.session.add(transferencia)

        db.session.add(Historial(
            cliente_id=anterior.id,
            lote_id=lote.id,
            tipo="Titularidad transferida",
            fecha=fecha,
            detalle=(
                f"Compra #{compra.id} transferida a "
                f"{nuevo.nombre} {nuevo.apellidos}, "
                f"DNI {nuevo.dni}."
            )[:200]
        ))

        db.session.add(Historial(
            cliente_id=nuevo.id,
            lote_id=lote.id,
            tipo="Titularidad recibida",
            fecha=fecha,
            detalle=(
                f"Compra #{compra.id} recibida de "
                f"{anterior.nombre} {anterior.apellidos}, "
                f"DNI {anterior.dni}."
            )[:200]
        ))

        compra.cliente_id = nuevo.id

        # Confirmar cliente, transferencia e historial juntos.
        db.session.commit()

        flash(
            "Titular cambiado. Se conservaron la compra, "
            "los pagos y las cuotas.",
            "success"
        )

        return redirect(
            url_for("detalle_lote", lote_id=lote.id)
            + "#historial-titulares"
        )

    except (BadSignature, SignatureExpired):
        db.session.rollback()
        formulario["error"] = (
            "El formulario venció o no es válido. "
            "Consulta nuevamente al cliente."
        )

    except ValueError as error:
        db.session.rollback()
        formulario["error"] = str(error)

    except Exception:
        db.session.rollback()

        current_app.logger.exception(
            "Error al cambiar titular de la compra %s",
            compra_id
        )

        formulario["error"] = (
            "No se pudo guardar el cambio. "
            "No se modificó la compra."
        )

    # Retirar las fotos creadas si la operación falló.
    for ruta in archivos_creados:
        try:
            if os.path.isfile(ruta):
                os.remove(ruta)
        except OSError:
            current_app.logger.exception(
                "No se pudo retirar una foto de DNI sin confirmar."
            )

    compra = db.session.get(Compra, compra_id)

    if compra is None:
        flash("La compra ya no existe.", "warning")
        return redirect(url_for("lotes_disponibles"))

    formulario["token"] = firmador.dumps({
        **base,
        "anterior_id": compra.cliente_id,
        "paso": "consultar"
    })

    # Mantener los datos cuando el usuario debe corregir algo.
    if (
        isinstance(datos, dict)
        and all(datos.get(k) == v for k, v in base.items())
        and compra.cliente_id == base["anterior_id"]
    ):
        if datos.get("paso") == "registrar":
            formulario["dni"] = datos["dni"]
            formulario["motivo"] = datos["motivo"]

            if not Cliente.query.filter_by(dni=datos["dni"]).first():
                formulario["crear"] = True
                formulario["token"] = firmador.dumps(datos)

        elif datos.get("crear") and datos.get("paso") == "confirmar":
            if not Cliente.query.filter_by(
                dni=datos["cliente"]["dni"]
            ).first():
                formulario["dni"] = datos["cliente"]["dni"]
                formulario["motivo"] = datos["motivo"]
                formulario["nuevo"] = datos["cliente"]
                formulario["es_nuevo"] = True
                formulario["token"] = firmador.dumps(datos)

    return _pagina_cambio_titular(compra, formulario)



def _clientes_del_proyecto(lotizacion_id):
    compras = (
        db.session.query(
            Compra.cliente_id.label("cliente_id")
        )
        .join(Lote, Lote.id == Compra.lote_id)
        .filter(Lote.lotizacion_id == lotizacion_id)
    )

    separaciones = (
        db.session.query(
            Separacion.cliente_id.label("cliente_id")
        )
        .join(Lote, Lote.id == Separacion.lote_id)
        .filter(Lote.lotizacion_id == lotizacion_id)
    )

    anteriores = (
        db.session.query(
            TransferenciaTitular.cliente_anterior_id.label("cliente_id")
        )
        .join(Lote, Lote.id == TransferenciaTitular.lote_id)
        .filter(Lote.lotizacion_id == lotizacion_id)
    )

    nuevos = (
        db.session.query(
            TransferenciaTitular.cliente_nuevo_id.label("cliente_id")
        )
        .join(Lote, Lote.id == TransferenciaTitular.lote_id)
        .filter(Lote.lotizacion_id == lotizacion_id)
    )

    historicos = (
        db.session.query(
            Historial.cliente_id.label("cliente_id")
        )
        .join(Lote, Lote.id == Historial.lote_id)
        .filter(Lote.lotizacion_id == lotizacion_id)
    )

    ids = compras.union(
        separaciones,
        anteriores,
        nuevos,
        historicos
    ).subquery()

    return Cliente.query.join(
        ids,
        ids.c.cliente_id == Cliente.id
    )

def _datos_nuevo_titular(origen):
    limites = {
        "nombre": 100,
        "apellidos": 100,
        "telefono": 20,
        "correo": 150,
        "estado_civil": 50,
        "ocupacion": 100,
        "ciudad": 50,
        "provincia": 100,
        "departamento": 100,
        "direccion": 200
    }

    datos = {}

    for campo, limite in limites.items():
        valor = str(origen.get(campo) or "").strip()

        if len(valor) > limite:
            raise ValueError(
                f"El campo {campo} admite hasta {limite} caracteres."
            )

        datos[campo] = valor

    if not datos["nombre"] or not datos["apellidos"]:
        raise ValueError(
            "Completa los nombres y apellidos del nuevo titular."
        )

    datos["correo"] = datos["correo"].lower()

    return datos


def _guardar_dni_titular(archivo, archivos_creados):
    if not archivo or not archivo.filename:
        return None

    from io import BytesIO
    from PIL import Image, ImageOps, UnidentifiedImageError

    contenido = archivo.read(8 * 1024 * 1024 + 1)

    if len(contenido) > 8 * 1024 * 1024:
        raise ValueError(
            "Cada foto del DNI debe pesar como máximo 8 MB."
        )

    try:
        with Image.open(BytesIO(contenido)) as imagen:
            if imagen.format not in ("JPEG", "PNG", "WEBP"):
                raise ValueError(
                    "Las fotos del DNI deben ser JPG, PNG o WEBP."
                )

            if imagen.width * imagen.height > 30000000:
                raise ValueError(
                    "La foto del DNI es demasiado grande."
                )

            imagen = ImageOps.exif_transpose(imagen).convert("RGB")

            nombre = f"titular_{uuid.uuid4().hex}.jpg"
            carpeta = os.path.join(app.static_folder, "dni")

            os.makedirs(carpeta, exist_ok=True)

            ruta = os.path.join(carpeta, nombre)
            archivos_creados.append(ruta)

            imagen.save(ruta, "JPEG", quality=95)

            return f"dni/{nombre}"

    except (UnidentifiedImageError, Image.DecompressionBombError):
        raise ValueError(
            "No se pudo leer una de las fotos del DNI."
        )
# ------------------- MAIN -------------------
if __name__ == "__main__":
    app.run(debug=True)