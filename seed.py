from app import app
from models import db, Lotizacion

with app.app_context():
    db.create_all()

    if not Lotizacion.query.first():
        l1 = Lotizacion(nombre="Costa del Sol")
        l2 = Lotizacion(nombre="Zarumilla")
        db.session.add_all([l1, l2])
        db.session.commit()
        print("✅ Lotizaciones iniciales creadas.")
    else:
        print("ℹ️ Ya existen lotizaciones.")
