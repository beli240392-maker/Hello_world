from app import Usuario

usuarios = Usuario.query.all()
print("📋 Lista de usuarios:")
for u in usuarios:
    print(f"ID: {u.id}, Usuario: {u.username}")
