from app import Lotizacion

lotizaciones = Lotizacion.query.all()
print("📋 Lista de lotizaciones:")
for l in lotizaciones:
    print(f"ID: {l.id}, Nombre: {l.nombre}")
