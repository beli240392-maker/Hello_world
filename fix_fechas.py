from app import db, Compra
from datetime import timedelta

# Recorremos todas las compras con fecha en 28/09/2025
compras_erroneas = Compra.query.filter(
    Compra.fecha_compra >= "2025-09-28 00:00:00",
    Compra.fecha_compra < "2025-09-29 00:00:00"
).all()

print(f"Se encontraron {len(compras_erroneas)} compras con fecha 28/09/2025")

for compra in compras_erroneas:
    print(f"Corrigiendo compra ID {compra.id} - fecha original {compra.fecha_compra}")
    # Restamos 5 horas (UTC → Perú)
    compra.fecha_compra = compra.fecha_compra - timedelta(hours=5)

# Guardar cambios en BD
db.session.commit()
print("✅ Fechas corregidas exitosamente.")
