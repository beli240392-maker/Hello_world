from app import app
from models import db, Usuario, Lotizacion

with app.app_context():
    db.create_all()

    # Crear usuario admin si no existe
    if not Usuario.query.filter_by(username="admin").first():
        admin = Usuario(username="flor", rol="admin")
        admin.set_password("admin123")
        db.session.add(admin)
        print("✅ Usuario admin creado: flor / admin123")

    # Crear lotizaciones si no existen
    if not Lotizacion.query.first():
        l1 = Lotizacion(nombre="Costa del Sol")
        l2 = Lotizacion(nombre="Zarumilla")
        db.session.add_all([l1, l2])
        print("✅ Lotizaciones iniciales creadas.")

    db.session.commit()