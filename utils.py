from functools import wraps
from flask import abort, flash, redirect, url_for,session
from flask_login import current_user

def admin_required(f):
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if not current_user.is_authenticated:
            flash("⚠️ Debes iniciar sesión para acceder.", "warning")
            return redirect(url_for("login"))

        if current_user.rol != "admin":
            flash("❌ No tienes permisos para acceder a esta función.", "danger")
            return redirect(url_for("lotes_disponibles"))  # o donde quieras redirigir
        return f(*args, **kwargs)
    return decorated_function


def lotizacion_required(f):
    @wraps(f)
    def decorated_function(*args, **kwargs):
        # Verificamos si el usuario está autenticado
        if not current_user.is_authenticated:
            flash("Debes iniciar sesión primero.", "warning")
            return redirect(url_for("login"))

        # Verificamos si seleccionó una lotización
        if "lotizacion_id" not in session or not session["lotizacion_id"]:
            flash("Debes seleccionar una lotización para continuar.", "warning")
            return redirect(url_for("seleccionar_lotizacion"))

        return f(*args, **kwargs)
    return decorated_function


