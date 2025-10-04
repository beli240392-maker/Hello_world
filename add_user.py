from app import app, db, Usuario

with app.app_context():
    username = input("Ingrese nombre de usuario: ")
    password = input("Ingrese contraseña: ")
    rol = input("Ingrese rol (ejemplo: admin, vendedor): ")

    nuevo = Usuario(username=username, rol=rol)
    nuevo.set_password(password)

    db.session.add(nuevo)
    db.session.commit()

    print(f"✅ Usuario '{username}' creado con éxito (ID: {nuevo.id}, Rol: {rol})")
