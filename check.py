from app import app
from models import db, Usuario, Lotizacion

with app.app_context():
    print("=== Usuarios ===")
    for u in Usuario.query.all():
        print(f"ID: {u.id}, Usuario: {u.username}, Rol: {u.rol}")

    print("\n=== Lotizaciones ===")
    for l in Lotizacion.query.all():
        print(f"ID: {l.id}, Nombre: {l.nombre}")
