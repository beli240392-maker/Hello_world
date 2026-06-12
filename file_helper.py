import os
import re

BASE_DIR = os.path.join("static", "documentos")

def sanitizar_nombre(nombre):
    return re.sub(r'[\\/*?:"<>|]', "", nombre).strip()

def get_ruta_empresa(tipo="otros"):
    ruta = os.path.join(BASE_DIR, "empresa", tipo)
    os.makedirs(ruta, exist_ok=True)
    return ruta

def get_ruta_lote(lotizacion_nombre, manzana, lote_numero):
    ruta = os.path.join(
        BASE_DIR, "lotizaciones",
        sanitizar_nombre(lotizacion_nombre),
        f"Manzana_{manzana}",
        f"Lt_{lote_numero}"
    )
    os.makedirs(ruta, exist_ok=True)
    return ruta

def get_ruta_lotizacion_general(lotizacion_nombre):
    ruta = os.path.join(
        BASE_DIR, "lotizaciones",
        sanitizar_nombre(lotizacion_nombre),
        "Generales"
    )
    os.makedirs(ruta, exist_ok=True)
    return ruta

def subir_archivo(file_storage, nombre_archivo, ruta_carpeta):
    nombre_limpio = sanitizar_nombre(nombre_archivo)
    ruta_completa = os.path.join(ruta_carpeta, nombre_limpio)

    base, ext = os.path.splitext(nombre_limpio)
    contador = 1
    while os.path.exists(ruta_completa):
        nombre_limpio = f"{base}_{contador}{ext}"
        ruta_completa = os.path.join(ruta_carpeta, nombre_limpio)
        contador += 1

    file_storage.save(ruta_completa)
    ruta_relativa = ruta_completa.replace("\\", "/")
    return nombre_limpio, ruta_relativa

def eliminar_archivo(ruta_relativa):
    if os.path.exists(ruta_relativa):
        os.remove(ruta_relativa)