from app import app, db

with app.app_context():
    meta = db.metadata
    for table in reversed(meta.sorted_tables):
        db.session.execute(table.delete())
    db.session.commit()
    print("🧹 Todos los registros borrados, tablas vacías.")
