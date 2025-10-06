from app import app
from models import db, Usuario

with app.app_context():
    username = input("Ingrese el nombre de usuario: ").strip()
    password = input("Ingrese la contraseña: ").strip()
    rol = input("Ingrese el rol (admin / vendedor): ").strip().lower()

    if Usuario.query.filter_by(username=username).first():
        print("⚠️ El usuario ya existe.")
    else:
        nuevo = Usuario(username=username, rol=rol)
        nuevo.set_password(password)
        db.session.add(nuevo)
        db.session.commit()
        print(f"✅ Usuario '{username}' creado con rol '{rol}'.")
