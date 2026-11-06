import secrets

from flask import current_app, jsonify, request, session, url_for
from flask_login import current_user, login_required
from models import Usuario, db


# Cambiar esta versión al publicar nuevas novedades.
NOVEDADES_VERSION = "20261105_02"

NOVEDADES_CONTENIDO = (
        {
        "titulo": "Estado de créditos en Reportes",
        "texto": (
            "Consulta qué lotes al crédito tienen cuotas pendientes "
            "y cuáles están pagados, con su saldo y acceso al detalle."
        ),
        "icono": "📊",
        "roles": ("vendedor", "user", "admin", "superadmin"),
        "selectores": (
            'a[href="/reportes"]',
        ),
        "ayuda": (
            "Entra a Reportes y selecciona «Estado de créditos». "
            "Usa Todos, Con deuda, Pagados o Por revisar para "
            "consultar los lotes del proyecto activo."
        )
    },
    {
        "titulo": "Corrección de fechas y vencimientos",
        "texto": (
            "Desde el detalle de una compra o separación puedes "
            "corregir su fecha. En compras al crédito se actualizan "
            "los vencimientos, conservando los pagos y vouchers."
        ),
        "icono": "📅",
        "roles": ("superadmin",),
        "selectores": (
            'a[href^="/corregir_fecha/"]',
            '[data-novedad="lotes"]'
        ),
        "ayuda": (
            "Abre Lotes, entra al detalle de una operación y busca "
            "«Corregir fecha». Revisa las fechas propuestas antes "
            "de guardar."
        )
    },
    {
        "titulo": "Filtros y descarga del plano",
        "texto": (
            "En Lotes puedes filtrar por manzana, estado y área. "
            "Desde la vista Plano puedes descargar el plano original, "
            "completo o con la selección actual."
        ),
        "icono": "🗺️",
        "roles": ("vendedor", "user", "admin", "superadmin"),
        "selectores": (
            '[data-novedad="lotes"]',
        ),
        "ayuda": (
            "Entra a Lotes y selecciona la vista Plano para usar "
            "los filtros y la descarga."
        )
    },
    {
        "titulo": "Clientes con operaciones anteriores",
        "texto": (
            "Al escribir el DNI en una compra o separación también "
            "se pueden recuperar los datos de clientes que conservan "
            "historial en el proyecto activo."
        ),
        "icono": "👤",
        "roles": ("vendedor", "user", "admin", "superadmin"),
        "selectores": (
            '[data-novedad="compra"]',
            '[data-novedad="separacion"]'
        ),
        "ayuda": (
            "Al registrar una compra o separación, escribe el DNI "
            "y revisa los datos recuperados antes de guardar. "
            "La búsqueda respeta el proyecto activo."
        )
    },
    {
        "titulo": "Documentos de proyectos antiguos",
        "texto": (
            "La sección Documentos permite organizar archivos de "
            "proyectos antiguos, además de los proyectos de lotes "
            "que ya administras."
        ),
        "icono": "📁",
        "roles": ("admin", "superadmin"),
        "selectores": (
            '[data-novedad="documentos"]',
        ),
        "ayuda": (
            "Entra a Documentos para consultar los proyectos "
            "y sus archivos."
        )
    }
)


def _novedades_del_usuario(usuario):
    if not usuario.is_authenticated:
        return []

    return [
        {
            clave: valor
            for clave, valor in novedad.items()
            if clave != "roles"
        }
        for novedad in NOVEDADES_CONTENIDO
        if usuario.rol in novedad["roles"]
    ]


def registrar_novedades(app):

    @app.context_processor
    def contexto_guia_novedades():
        elementos = _novedades_del_usuario(current_user)

        if not elementos:
            return {"guia_novedades": None}

        csrf = session.setdefault(
            "csrf_novedades",
            secrets.token_urlsafe(32)
        )

        pendiente = (
            current_user.novedades_version_vista
            != NOVEDADES_VERSION
        )

        return {
            "guia_novedades": {
                "version": NOVEDADES_VERSION,
                "usuario_id": current_user.id,
                "pendiente": pendiente,
                "automatico": (
                    pendiente and request.endpoint == "home"
                ),
                "csrf": csrf,
                "url_guardar": url_for("marcar_novedades_vistas"),
                "elementos": elementos
            }
        }

    @app.route("/novedades/vistas", methods=["POST"])
    @login_required
    def marcar_novedades_vistas():
        datos = request.get_json(silent=True)

        if not isinstance(datos, dict):
            return jsonify(
                error="La solicitud no es válida."
            ), 400

        recibido = datos.get("csrf")
        esperado = session.get("csrf_novedades")

        if (
            not isinstance(recibido, str)
            or not recibido.isascii()
            or not esperado
            or not secrets.compare_digest(recibido, esperado)
        ):
            return jsonify(
                error="La sesión cambió. Recarga la página."
            ), 400

        if datos.get("version") != NOVEDADES_VERSION:
            return jsonify(
                error=(
                    "Hay una actualización nueva. "
                    "Recarga la página."
                )
            ), 409

        if not _novedades_del_usuario(current_user):
            return jsonify(
                error="No tienes novedades disponibles."
            ), 403

        try:
            usuario = db.session.get(Usuario, current_user.id)

            usuario.novedades_version_vista = NOVEDADES_VERSION

            db.session.commit()

            return jsonify(ok=True)

        except Exception:
            db.session.rollback()

            current_app.logger.exception(
                "Error al guardar las novedades vistas"
            )

            return jsonify(
                error=(
                    "No pudimos confirmar el guardado. "
                    "Puedes intentarlo nuevamente o revisar "
                    "las novedades más tarde."
                )
            ), 500