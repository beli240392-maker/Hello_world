from flask_sqlalchemy import SQLAlchemy
from datetime import datetime,timedelta
from flask_login import UserMixin
from werkzeug.security import generate_password_hash, check_password_hash
import pytz


db = SQLAlchemy()



# ---------------- CLIENTE ----------------
class Cliente(db.Model):
    __tablename__ = "clientes"

    id = db.Column(db.Integer, primary_key=True)
    nombre = db.Column(db.String(100), nullable=False)
    apellidos = db.Column(db.String(100), nullable=False)
    dni = db.Column(db.String(20), unique=True, nullable=False)
    telefono = db.Column(db.String(20))
    estado_civil = db.Column(db.String(50), default="No registrada")
    ocupacion = db.Column(db.String(100), default="No registrada")
    ciudad = db.Column(db.String(50))
    direccion = db.Column(db.String(200))
    dni_frontal = db.Column(db.String(200), nullable=True)
    dni_reverso = db.Column(db.String(200), nullable=True)

    # Relación con compras y separaciones
    compras = db.relationship("Compra", backref="cliente", lazy=True, cascade="all, delete-orphan")
    separaciones = db.relationship("Separacion", backref="cliente", lazy=True, cascade="all, delete-orphan")
    historial = db.relationship("Historial", backref="cliente", lazy=True, cascade="all, delete-orphan")


# ---------------- LOTE ----------------
class Lote(db.Model):
    __tablename__ = "lotes"

    id = db.Column(db.Integer, primary_key=True)
    manzana = db.Column(db.String(10), nullable=False)
    numero  = db.Column(db.String(10), nullable=False)
    area    = db.Column(db.Float, nullable=False)
    estado  = db.Column(db.String(20), nullable=False, default="disponible")

    # (tus relaciones existentes)
    compra        = db.relationship("Compra", backref="lote", uselist=False)
    separaciones  = db.relationship("Separacion", backref="lote", lazy=True, cascade="all, delete-orphan")
    historial     = db.relationship("Historial", backref="lote", lazy=True, cascade="all, delete-orphan")

    lotizacion_id = db.Column(db.Integer, db.ForeignKey("lotizaciones.id"), nullable=False)

    # ✅ NUEVO: el par (lotizacion_id, manzana, numero) debe ser único
    __table_args__ = (
        db.UniqueConstraint("lotizacion_id", "manzana", "numero",
                            name="uq_lote_lotizacion_manzana_numero"),
    )

# ---------------- COMPRA ----------------
def hora_local_peru():
    return datetime.utcnow() - timedelta(hours=5)

def hora_lima():
    return datetime.now(pytz.timezone("America/Lima"))

class Compra(db.Model):
    __tablename__ = "compras"

    
    id = db.Column(db.Integer, primary_key=True)
    cliente_id = db.Column(db.Integer, db.ForeignKey("clientes.id"), nullable=False)
    lote_id = db.Column(db.Integer, db.ForeignKey("lotes.id"), nullable=False)
    usuario_id = db.Column(db.Integer, db.ForeignKey("usuarios.id"))  # 👈 Nuevo campo

    forma_pago = db.Column(db.String(20), nullable=False)  # contado o credito
    precio = db.Column(db.Float, nullable=False)
    inicial = db.Column(db.Float, default=0)
    cuotas_total = db.Column(db.Integer, default=0)
    cuota_monto = db.Column(db.Float, default=0)
    fecha_compra = db.Column(db.DateTime, default=hora_local_peru)
    boucher_inicial = db.Column(db.String(200))
    anulada = db.Column(db.Boolean, default=False)
    interes = db.Column(db.Float, default=0)  # porcentaje de interés aplicado al crédito
    acta_entrega = db.Column(db.String(200), nullable=True)   # ruta archivo PDF/JPG
    entregado = db.Column(db.Boolean, default=False)          # flag rápido

    # Relación con cuotas y pagos
    cuotas = db.relationship("Cuota", backref="compra", lazy=True, cascade="all, delete-orphan")
    pagos = db.relationship("Pago", backref="compra", lazy=True, cascade="all, delete-orphan")
    usuario = db.relationship("Usuario", backref="compras") 


# ---------------- CUOTA ----------------
class Cuota(db.Model):
    __tablename__ = "cuotas"

    id = db.Column(db.Integer, primary_key=True)
    compra_id = db.Column(db.Integer, db.ForeignKey("compras.id"), nullable=False)

    numero = db.Column(db.Integer, nullable=False)
    monto = db.Column(db.Float, nullable=False)
    fecha_vencimiento = db.Column(db.DateTime, nullable=False)

    pagada = db.Column(db.Boolean, default=False)
    pago_id = db.Column(db.Integer, db.ForeignKey("pagos.id"), nullable=True)


# ---------------- PAGO ----------------
class Pago(db.Model):
    __tablename__ = "pagos"

    id = db.Column(db.Integer, primary_key=True)
    compra_id = db.Column(db.Integer, db.ForeignKey("compras.id"), nullable=False)

    monto = db.Column(db.Float, nullable=False)
    fecha_pago = db.Column(db.DateTime, default=datetime.utcnow)
    boucher = db.Column(db.String(200))

    # Relación inversa con cuotas (1 cuota → 1 pago)
    cuota = db.relationship("Cuota", backref="pago", uselist=False)


# ---------------- SEPARACIÓN ----------------
class Separacion(db.Model):
    __tablename__ = "separaciones"

    id = db.Column(db.Integer, primary_key=True)
    cliente_id = db.Column(db.Integer, db.ForeignKey("clientes.id"), nullable=False)
    lote_id = db.Column(db.Integer, db.ForeignKey("lotes.id"), nullable=False)
    usuario_id = db.Column(db.Integer, db.ForeignKey("usuarios.id"))  # 👈 Nuevo campo
    usuario = db.relationship("Usuario")  # Para acceder al objeto User

    monto = db.Column(db.Float, nullable=False)
    fecha = db.Column(db.DateTime, default=datetime.utcnow)
    boucher = db.Column(db.String(200))
    activa = db.Column(db.Boolean, default=True)


# ---------------- HISTORIAL ----------------
class Historial(db.Model):
    __tablename__ = "historial"

    id = db.Column(db.Integer, primary_key=True)
    cliente_id = db.Column(db.Integer, db.ForeignKey("clientes.id"), nullable=False)
    lote_id = db.Column(db.Integer, db.ForeignKey("lotes.id"), nullable=False)

    tipo = db.Column(db.String(50), nullable=False)
    detalle = db.Column(db.String(200))
    fecha = db.Column(db.DateTime, default=lambda: datetime.now(pytz.timezone("America/Lima")))

class Lotizacion(db.Model):
    __tablename__ = "lotizaciones"
    id = db.Column(db.Integer, primary_key=True)
    nombre = db.Column(db.String(100), nullable=False, unique=True)

    lotes = db.relationship("Lote", backref="lotizacion", lazy=True)

class Voucher(db.Model):
    __tablename__ = "vouchers"

    id = db.Column(db.Integer, primary_key=True)
    codigo = db.Column(db.String(50), unique=True, nullable=False)
    banco = db.Column(db.String(100), nullable=False)
    nombres = db.Column(db.String(100), nullable=False)
    apellidos = db.Column(db.String(100), nullable=False)
    monto = db.Column(db.Float, nullable=False)
    proyecto = db.Column(db.String(100))
    fecha_registro = db.Column(db.DateTime, default=lambda: datetime.now(pytz.timezone("America/Lima")))


    # Relación con usuario que registró el voucher
    usuario_id = db.Column(db.Integer, db.ForeignKey("usuarios.id"), nullable=True)
    usuario = db.relationship("Usuario", backref="vouchers")


class Usuario(UserMixin, db.Model):
    __tablename__ = "usuarios"
    id = db.Column(db.Integer, primary_key=True)
    username = db.Column(db.String(50), unique=True, nullable=False)
    password_hash = db.Column(db.String(255), nullable=False)
    rol = db.Column(db.String(20), nullable=False, default="user")

    def set_password(self, password):
        """Encripta y guarda la contraseña"""
        self.password_hash = generate_password_hash(password)

    def check_password(self, password):
        """Valida la contraseña ingresada contra la encriptada"""
        return check_password_hash(self.password_hash, password)