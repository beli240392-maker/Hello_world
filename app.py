import os
import uuid
from utils import lotizacion_required, admin_required
from flask import Flask, render_template, request, redirect, url_for, flash, jsonify, session
from werkzeug.utils import secure_filename
from models import db, Cliente, Lote, Compra, Pago, Cuota, Separacion, Historial, Lotizacion, Voucher
from datetime import datetime, timedelta, date
from flask_login import LoginManager, login_user, logout_user, login_required, current_user
from flask_login import LoginManager
from models import Usuario
from werkzeug.security import check_password_hash, generate_password_hash
from flask import Flask
from flask_login import logout_user, login_required
from functools import wraps
from flask import session, redirect, url_for, flash
import re
from sqlalchemy import cast, Integer
from flask_login import current_user
import pytz
import os
from dotenv import load_dotenv
from sqlalchemy import func
from sqlalchemy import or_


load_dotenv()  # Cargar variables del archivo .env

app = Flask(__name__)
app.config["SECRET_KEY"] = os.getenv("SECRET_KEY")
app.config["SQLALCHEMY_DATABASE_URI"] = os.getenv("DATABASE_URL")
db.init_app(app)




app.config["UPLOAD_FOLDER"] = os.path.join("static", "bouchers")
os.makedirs(app.config["UPLOAD_FOLDER"], exist_ok=True)



# Login manager
login_manager = LoginManager()
login_manager.init_app(app)
login_manager.login_view = "login"

@login_manager.user_loader
def load_user(user_id):
    return Usuario.query.get(int(user_id))



app.config["SQLALCHEMY_DATABASE_URI"] = "sqlite:///lotes.db"
app.config["SQLALCHEMY_TRACK_MODIFICATIONS"] = False


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
    lotizaciones = Lotizacion.query.all()  # 👈 Traer todas las lotizaciones

    if request.method == "POST":
        usuario = Usuario.query.filter_by(username=request.form["usuario"]).first()
        if usuario and usuario.check_password(request.form["password"]):
            login_user(usuario)
            # Guardar la lotización elegida en la sesión
            session["lotizacion_id"] = request.form.get("lotizacion_id")
            return redirect(url_for("home"))
        else:
            flash("Usuario o contraseña incorrectos", "danger")

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
@admin_required
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
        cliente.direccion = request.form["direccion"].strip().lower()

        db.session.commit()
        flash("✅ Cliente actualizado correctamente.", "success")
        return redirect(url_for("ver_cliente", cliente_id=cliente.id))

    return render_template("editar_cliente.html", cliente=cliente, lotizacion=lotizacion)

# ------------------- API para obtener lotes por manzana -------------------
@app.route("/get_lotes/<manzana>")
def get_lotes(manzana):
    lotizacion_id = session.get("lotizacion_id")
    lotes = Lote.query.filter_by(manzana=manzana, estado="disponible", lotizacion_id=lotizacion_id).all()
    data = []
    for l in lotes:
        data.append({
            "id": int(l.id) if l.id else 0,
            "numero": str(l.numero) if l.numero else ""
        })
    return jsonify(data)

@app.route("/detalle_lote/<int:lote_id>")
def detalle_lote(lote_id):
    lote = Lote.query.get_or_404(lote_id)
    compra = None
    separacion = None
    if lote.estado == "vendido":
        compra = Compra.query.filter_by(lote_id=lote.id).first()
    elif lote.estado == "separado":
        separacion = Separacion.query.filter_by(lote_id=lote.id, activa=True).first()

    lotizacion = None
    if "lotizacion_id" in session:
        lotizacion = Lotizacion.query.get(session["lotizacion_id"])

    return render_template("detalle_lote.html", lote=lote, compra=compra, separacion=separacion, lotizacion=lotizacion)

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
patron_manzana = re.compile(r"^[A-Z]{1}['\"]?$")

@app.route("/agregar_lotes", methods=["GET", "POST"])
@login_required
@admin_required
@lotizacion_required
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
                flash("Formato de manzana inválido. Usa solo una letra mayúscula y opcionalmente un apóstrofe o comilla.", "danger")
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

# ------------------- REGISTRAR AREA DE LOTE -------------------

@app.route("/editar_area/<int:lote_id>", methods=["GET", "POST"])
@login_required
@lotizacion_required
def editar_area(lote_id):
    lote = Lote.query.get_or_404(lote_id)

    if request.method == "POST":
        nueva_area = request.form.get("area")
        if nueva_area:
            lote.area = float(nueva_area)
            db.session.commit()
            flash("✅ Área del lote actualizada correctamente", "success")
            return redirect(url_for("lotes_disponibles"))  # tu lista principal de lotes

    return render_template("editar_area.html", lote=lote)


# ------------------- REGISTRAR COMPRA -------------------
@app.route("/registrar_compra", methods=["GET", "POST"])
@lotizacion_required
@login_required
def registrar_compra():
    lotizacion = None
    if "lotizacion_id" in session:
        lotizacion = Lotizacion.query.get(session["lotizacion_id"])

    lotes = []
    if lotizacion:
        # ✅ Solo mostrar disponibles por defecto
        lotes = Lote.query.filter_by(lotizacion_id=lotizacion.id, estado="disponible").all()

    sep_id = request.args.get("sep_id")
    lote = None
    cliente = None
    separacion = None

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
        telefono = request.form.get("telefono", "").strip()
        direccion = request.form.get("direccion", "").strip().lower()
        ciudad = request.form.get("ciudad", "").strip().lower()
        estado_civil = request.form.get("estado_civil", "").strip().lower()  # ✅ agregado
        ocupacion = request.form.get("ocupacion", "").strip().lower()        # ✅ agregado

        precio = float(request.form.get("precio", 0))
        forma_pago = request.form["forma_pago"]
        inicial = float(request.form.get("inicial", 0)) if forma_pago == "credito" else 0
        cuotas_total = int(request.form.get("cuotas", 0)) if forma_pago == "credito" else 0
        interes = float(request.form.get("interes", 0)) if forma_pago == "credito" else 0

        # Cliente
        cliente = Cliente.query.filter_by(dni=dni).first()
        if not cliente:
            cliente = Cliente(
                nombre=nombre,
                apellidos=apellidos,
                dni=dni,
                telefono=telefono,
                direccion=direccion,
                ciudad=ciudad,
                estado_civil=estado_civil,  
                ocupacion=ocupacion        #
            )
            db.session.add(cliente)
            db.session.commit()
        else:
            
            cliente.nombre = nombre
            cliente.apellidos = apellidos
            cliente.telefono = telefono
            cliente.direccion = direccion
            cliente.ciudad = ciudad
            cliente.estado_civil = estado_civil
            cliente.ocupacion = ocupacion
            db.session.commit()

        # ✅ Guardar fotos de DNI
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

        # Lote
        lote_id = request.form.get("lote")
        if not lote_id and lote:
            lote_id = lote.id
        lote = Lote.query.get(lote_id)
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
            monto_total = precio * (1 + interes / 100)

        # Crear compra
        compra = Compra(
            cliente_id=cliente.id,
            lote_id=lote.id,
            forma_pago=forma_pago,
            precio=monto_total,
            inicial=inicial,
            cuotas_total=cuotas_total,
            cuota_monto=(monto_total - inicial) / cuotas_total if forma_pago == "credito" and cuotas_total > 0 else 0,
            boucher_inicial=boucher_path,  # ✅ Ahora sí correcto
            interes=interes,
            usuario_id=current_user.id
        )
        db.session.add(compra)
        db.session.commit()

        # Generar cuotas
        if forma_pago == "credito" and cuotas_total > 0:
            fecha_vencimiento = datetime.utcnow()
            for i in range(1, cuotas_total + 1):
                fecha_vencimiento += timedelta(days=30)
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

        db.session.commit()
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

# ------------------- REGISTRAR SEPARACION -------------------
@app.route("/registrar_separacion", methods=["GET", "POST"])
@lotizacion_required
@login_required
def registrar_separacion():
    lotizacion = None
    if "lotizacion_id" in session:
        lotizacion = Lotizacion.query.get(session["lotizacion_id"])

    lotes = []
    if lotizacion:
        lotes = Lote.query.filter_by(estado="disponible", lotizacion_id=lotizacion.id).all()

    if request.method == "POST":
        nombre = request.form["nombre"].strip().lower()
        apellidos = request.form.get("apellidos").strip().lower()
        dni = request.form["dni"].strip()
        telefono = request.form.get("telefono").strip()
        direccion = request.form.get("direccion").strip().lower()
        ciudad = request.form.get("ciudad").strip().lower()

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
                estado_civil=estado_civil,   # 🔹 agregado
                ocupacion=ocupacion          # 🔹 agregado
            )
            db.session.add(cliente)
            db.session.commit()  # 👈 ahora cliente.id está disponible
        else:
            # 🔹 Si ya existe, actualizar sus datos
            cliente.estado_civil = estado_civil or cliente.estado_civil
            cliente.ocupacion = ocupacion or cliente.ocupacion
            cliente.telefono = telefono or cliente.telefono
            cliente.direccion = direccion or cliente.direccion
            cliente.ciudad = ciudad or cliente.ciudad

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
            cliente_id=cliente.id,   # 👈 ahora nunca será None
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

    return render_template("registrar_separacion.html", lotes=lotes, lotizacion=lotizacion)



# ------------------- VER CLIENTE -------------------
# ------------------- VER CLIENTE -------------------
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
                    ((Cliente.dni == criterio) | (Cliente.apellidos.ilike(f"%{criterio}%"))),
                    Lote.lotizacion_id == lotizacion_id
                )
                .distinct()
                .all()
            )
    if isinstance(cliente, list):
        cliente = cliente[0] if cliente else None

    # Si se encontró cliente, traer sus datos SOLO de la lotización activa
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

    return render_template(
        "ver_cliente.html",
        cliente=cliente,
        separaciones=separaciones,
        compras_contado=compras_contado,
        compras_credito=compras_credito,
        historial=historial,
        now=datetime.now(lima),
        lotizacion=lotizacion,pytz=pytz
    )


# ------------------- DETALLE CUOTAS -------------------
@app.route("/detalle_cuotas/<int:compra_id>")
def detalle_cuotas(compra_id):
    compra = Compra.query.get_or_404(compra_id)
    now = datetime.utcnow()

    lotizacion = None
    if "lotizacion_id" in session:
        lotizacion = Lotizacion.query.get(session["lotizacion_id"])

    return render_template("detalle_cuotas.html", compra=compra, now=now,
                           cliente_id=compra.cliente.id, lotizacion=lotizacion)

# ------------------- PAGAR CUOTA -------------------
@app.route("/pagar_cuota", methods=["POST"])
def pagar_cuota():
    cuota_id = request.form["cuota_id"]
    cuota = Cuota.query.get_or_404(cuota_id)
    boucher_file = request.files.get("boucher_cuota")
    boucher_path = guardar_boucher(boucher_file)
    pago = Pago(compra_id=cuota.compra_id, monto=cuota.monto, boucher=boucher_path)
    db.session.add(pago)
    db.session.commit()
    cuota.pagada = True
    cuota.pago_id = pago.id
    db.session.commit()
    flash("✅ Cuota pagada correctamente.", "success")
    return redirect(url_for("detalle_cuotas", compra_id=cuota.compra_id))

# ------------------- LIBERAR LOTE -------------------
@app.route("/liberar_lote/<int:id>/<string:tipo>", methods=["POST"])
@login_required
@lotizacion_required
@admin_required
def liberar_lote(id, tipo):
    lote = Lote.query.get_or_404(id)
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
def liberar_separacion(sep_id):
    sep = Separacion.query.get_or_404(sep_id)
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
def convertir_separacion(sep_id):
    sep = Separacion.query.get_or_404(sep_id)
    if not sep.activa:
        flash("La separación ya no está activa.", "danger")
        return redirect(url_for("ver_cliente"))
    cliente = sep.cliente
    lote = sep.lote
    compra = Compra(cliente_id=cliente.id, lote_id=lote.id, forma_pago="contado",
                    precio=sep.monto, inicial=sep.monto, cuotas_total=0,
                    cuota_monto=0, fecha_compra=datetime.utcnow())
    db.session.add(compra)
    sep.activa = False
    lote.estado = "vendido"
    historial = Historial(cliente_id=cliente.id, lote_id=lote.id, tipo="Separación convertida",
                          detalle=f"Separación de S/ {sep.monto:.2f} convertida en compra", fecha=datetime.utcnow())
    db.session.add(historial)
    db.session.commit()
    flash("Separación convertida en compra.", "success")
    return redirect(url_for("ver_cliente", cliente_id=cliente.id))

# ------------------- LOGIN LOTIZACION -------------------
@app.route("/seleccionar_lotizacion", methods=["GET", "POST"])
@login_required
def seleccionar_lotizacion():
    if request.method == "POST":
        # NO usar indexación directa para evitar KeyError
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

        # Guardar en sesión SIN tocar nada más del flujo
        session["lotizacion_id"] = lot.id
        session["lotizacion_nombre"] = lot.nombre

        flash(f"Lotización activa: {lot.nombre}", "success")
        next_url = request.args.get("next") or url_for("home")
        return redirect(next_url)

    # GET → mostrar selector
    lotizaciones = Lotizacion.query.all()
    return render_template("seleccionar_lotizacion.html", lotizaciones=lotizaciones)

@app.route("/subir_acta/<int:compra_id>", methods=["POST"])
@login_required
@lotizacion_required
def subir_acta(compra_id):
    compra = Compra.query.get_or_404(compra_id)

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
@admin_required
@lotizacion_required
def reportes():
    lotizacion_id = session.get("lotizacion_id")

    # Obtener fecha desde query params, si no → hoy
    fecha_str = request.args.get("fecha")
    try:
        fecha = datetime.strptime(fecha_str, "%Y-%m-%d").date() if fecha_str else date.today()
    except ValueError:
        fecha = date.today()

    # === Consultas ===
    separaciones = (
        Separacion.query.filter_by(activa=True)
        .join(Lote)
        .filter(Lote.lotizacion_id == lotizacion_id)
        .all()
    )

    compras_contado = (
        Compra.query.filter_by(forma_pago="contado")
        .filter(db.func.date(Compra.fecha_compra) == fecha)   # ✅ filtro por fecha
        .join(Lote)
        .filter(Lote.lotizacion_id == lotizacion_id)
        .all()
    )

    compras_credito = (
        Compra.query.filter_by(forma_pago="credito")
        .filter(db.func.date(Compra.fecha_compra) == fecha)   # ✅ filtro por fecha
        .join(Lote)
        .filter(Lote.lotizacion_id == lotizacion_id)
        .all()
    )

    cuotas_vencidas = (
        Cuota.query.filter(
            Cuota.fecha_vencimiento < fecha,
            Cuota.pagada == False
        )
        .join(Compra)
        .join(Lote)
        .filter(Lote.lotizacion_id == lotizacion_id)
        .all()
    )

    cuotas_por_vencer = (
        Cuota.query.filter(
            Cuota.fecha_vencimiento >= fecha,
            Cuota.fecha_vencimiento <= fecha + timedelta(days=7),
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


# ------------------- VERIFICAR VOUCHER REPETIDO -------------------
@app.route("/vouchers", methods=["GET", "POST"])
@login_required
def vouchers():
    codigo_buscar = request.args.get("codigo", "").strip()

    if request.method == "POST":
        codigo = request.form["codigo"].strip()
        banco = request.form.get("banco")
        nombres = request.form.get("nombres")
        apellidos = request.form.get("apellidos")
        monto = float(request.form.get("monto") or 0)

        # Verificar duplicado
        existe = Voucher.query.filter_by(codigo=codigo).first()
        if existe:
            flash("⚠️ El código ya está registrado.", "danger")
        else:
            v = Voucher(
                codigo=codigo,
                banco=banco,
                nombres=nombres,
                apellidos=apellidos,
                monto=monto,
                fecha_registro=datetime.now(lima)
            )
            db.session.add(v)
            db.session.commit()
            flash("✅ Voucher registrado correctamente.", "success")
        

        return redirect(url_for("vouchers"))

    # ✅ Si hay búsqueda → filtrar
    if codigo_buscar:
        vouchers = Voucher.query.filter_by(codigo=codigo_buscar).all()
    else:
        vouchers = Voucher.query.order_by(Voucher.fecha_registro.desc()).all()

    return render_template("vouchers.html", vouchers=vouchers, codigo_buscar=codigo_buscar,pytz=pytz)




@app.route("/logout")
@login_required
def logout():
    logout_user()
    session.pop("lotizacion_id", None)  # 👈 borra la lotización activa
    flash("Sesión cerrada correctamente.", "info")
    return redirect(url_for("login"))



# ------------------- MAIN -------------------
if __name__ == "__main__":
    app.run(debug=True)
