from app import app, db, Lotizacion

with app.app_context():
    nombre = input("Ingrese nombre de la lotización: ")

    nueva = Lotizacion(nombre=nombre)

    db.session.add(nueva)
    db.session.commit()

    print(f"✅ Lotización '{nombre}' creada con éxito (ID: {nueva.id})")
