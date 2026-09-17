from functools import wraps
from flask import abort, flash, redirect, url_for,session
from flask_login import current_user

def admin_required(f):
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if not current_user.is_authenticated:
            flash("⚠️ Debes iniciar sesión para acceder.", "warning")
            return redirect(url_for("login"))

        if current_user.rol not in ("admin", "superadmin"):
            flash("❌ No tienes permisos para acceder a esta función.", "danger")
            return redirect(url_for("lotes_disponibles"))
        return f(*args, **kwargs)
    return decorated_function


def lotizacion_required(f):
    @wraps(f)
    def decorated_function(*args, **kwargs):

        if not current_user.is_authenticated:
            flash("Debes iniciar sesión primero.", "warning")
            return redirect(url_for("login"))

        lotizacion_id = session.get("lotizacion_id")

        if not lotizacion_id:
            flash(
                "Debes seleccionar una lotización para continuar.",
                "warning"
            )
            return redirect(url_for("seleccionar_lotizacion"))

        # Verificar que el usuario realmente tenga permiso
        if current_user.rol != "superadmin":

            if not current_user.puede_acceder_lotizacion(lotizacion_id):

                session.pop("lotizacion_id", None)
                session.pop("lotizacion_nombre", None)

                flash(
                    "No tienes acceso a esa lotización.",
                    "danger"
                )

                return redirect(
                    url_for("seleccionar_lotizacion")
                )

        return f(*args, **kwargs)

    return decorated_function

def superadmin_required(f):
    """Solo permite acceso al superadmin."""
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if not current_user.is_authenticated:
            return redirect(url_for("login"))
        if current_user.rol != "superadmin":
            flash("Acceso restringido. Solo el superadministrador puede entrar aquí.", "danger")
            return redirect(url_for("home"))
        return f(*args, **kwargs)
    return decorated_function

